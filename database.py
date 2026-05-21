# ClinicFinder — database layer
# all DB access goes through this file — nothing talks to SQLite directly from app.py
# Flask stores one connection per request in `g` (see get_db / close_db below)
# to fully reset and re-seed: delete clinicfinder.db and restart
import math
import sqlite3
from datetime import datetime
from flask import g, current_app


# tables:
#   clinics           — HRSA FQHC data, seeded from CSV on first run, never written to at runtime
#   wait_reports      — crowd-sourced wait times, expire after 3 hours
#   clinic_reviews    — star ratings + comments, never expire
#   insurance_reports — community-reported private insurance plans, expire after 180 days
#   curated_hospitals — ~40 hand-verified major US hospitals with real phone/website data
#
# to add a new column: just delete clinicfinder.db and restart — it re-seeds from scratch
SCHEMA = """
CREATE TABLE IF NOT EXISTS clinics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hrsa_id        TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    org_name       TEXT,
    address        TEXT,
    city           TEXT,
    state          TEXT,
    zip            TEXT,
    phone          TEXT,
    latitude       REAL NOT NULL,
    longitude      REAL NOT NULL,
    hours_per_week INTEGER,
    site_type      TEXT,
    status         TEXT
);
CREATE INDEX IF NOT EXISTS idx_clinics_lat ON clinics(latitude);
CREATE INDEX IF NOT EXISTS idx_clinics_lon ON clinics(longitude);

CREATE TABLE IF NOT EXISTS wait_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
    -- wait_bucket values must match VALID_BUCKETS in app.py
    wait_bucket TEXT NOT NULL CHECK(wait_bucket IN ('<15','15-30','30-60','60-120','120+')),
    reported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wr_clinic_time ON wait_reports(clinic_id, reported_at);

CREATE TABLE IF NOT EXISTS clinic_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
    rating      INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reviews_clinic ON clinic_reviews(clinic_id);

CREATE TABLE IF NOT EXISTS insurance_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
    plan_name   TEXT NOT NULL,
    reported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ins_clinic ON insurance_reports(clinic_id);

CREATE TABLE IF NOT EXISTS curated_hospitals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    network   TEXT,
    type      TEXT NOT NULL DEFAULT 'Hospital',
    address   TEXT,
    city      TEXT,
    state     TEXT,
    zip       TEXT,
    phone     TEXT,
    website   TEXT,
    latitude  REAL NOT NULL,
    longitude REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ch_lat ON curated_hospitals(latitude);
CREATE INDEX IF NOT EXISTS idx_ch_lon ON curated_hospitals(longitude);
"""

# hand-verified hospitals — accurate names, phones, and websites.
# these show up first in Emergency Care and override any Overpass duplicates.
#
# to add a hospital: paste a new tuple into the right city section below —
#   (name, network, type, address, city, state, zip, phone, website, lat, lon)
# then delete clinicfinder.db and restart. type must be 'Hospital' or 'Urgent Care'.
# to find lat/lon: right-click the spot in Google Maps and click "What's here?"
CURATED_HOSPITALS = [
    # ── New York City — Mount Sinai ──────────────────────────────────────────
    ('Mount Sinai Hospital', 'Mount Sinai Health System', 'Hospital',
     '1 Gustave L. Levy Pl', 'New York', 'NY', '10029',
     '(212) 241-6500', 'https://www.mountsinai.org', 40.7900, -73.9526),
    ('Mount Sinai Brooklyn', 'Mount Sinai Health System', 'Hospital',
     '2636 E 16th St', 'Brooklyn', 'NY', '11235',
     '(718) 252-3000', 'https://www.mountsinai.org', 40.6007, -73.9609),
    ('Mount Sinai West', 'Mount Sinai Health System', 'Hospital',
     '1000 10th Ave', 'New York', 'NY', '10019',
     '(212) 523-4000', 'https://www.mountsinai.org', 40.7686, -73.9916),
    ('Mount Sinai Morningside', 'Mount Sinai Health System', 'Hospital',
     '1111 Amsterdam Ave', 'New York', 'NY', '10025',
     '(212) 523-3335', 'https://www.mountsinai.org', 40.8058, -73.9613),

    # ── New York City — NewYork-Presbyterian ─────────────────────────────────
    ('NewYork-Presbyterian Columbia University Irving Medical Center', 'NewYork-Presbyterian', 'Hospital',
     '622 W 168th St', 'New York', 'NY', '10032',
     '(212) 305-2500', 'https://www.nyp.org', 40.8404, -73.9441),
    ('NewYork-Presbyterian Weill Cornell Medical Center', 'NewYork-Presbyterian', 'Hospital',
     '525 E 68th St', 'New York', 'NY', '10065',
     '(212) 746-5454', 'https://www.nyp.org', 40.7647, -73.9537),
    ('NewYork-Presbyterian Queens', 'NewYork-Presbyterian', 'Hospital',
     '56-45 Main St', 'Flushing', 'NY', '11355',
     '(718) 670-2000', 'https://www.nyp.org', 40.7269, -73.8531),

    # ── New York City — Other ────────────────────────────────────────────────
    ('NYU Langone Health', 'NYU Langone', 'Hospital',
     '550 1st Ave', 'New York', 'NY', '10016',
     '(212) 263-7300', 'https://nyulangone.org', 40.7420, -73.9740),
    ('Bellevue Hospital Center', 'NYC Health + Hospitals', 'Hospital',
     '462 1st Ave', 'New York', 'NY', '10016',
     '(212) 562-4141', 'https://www.nychealthandhospitals.org/bellevue', 40.7384, -73.9756),
    ('Lenox Hill Hospital', 'Northwell Health', 'Hospital',
     '100 E 77th St', 'New York', 'NY', '10075',
     '(212) 434-2000', 'https://lenoxhill.northwell.edu', 40.7713, -73.9556),
    ('Lincoln Medical Center', 'NYC Health + Hospitals', 'Hospital',
     '234 E 149th St', 'Bronx', 'NY', '10451',
     '(718) 579-5000', 'https://www.nychealthandhospitals.org/lincoln', 40.8206, -73.9247),
    ('Kings County Hospital Center', 'NYC Health + Hospitals', 'Hospital',
     '451 Clarkson Ave', 'Brooklyn', 'NY', '11203',
     '(718) 245-3131', 'https://www.nychealthandhospitals.org/kingscounty', 40.6568, -73.9451),

    # ── Los Angeles ──────────────────────────────────────────────────────────
    ('Cedars-Sinai Medical Center', 'Cedars-Sinai', 'Hospital',
     '8700 Beverly Blvd', 'Los Angeles', 'CA', '90048',
     '(310) 423-3277', 'https://www.cedars-sinai.org', 34.0762, -118.3799),
    ('UCLA Medical Center', 'UCLA Health', 'Hospital',
     '757 Westwood Plaza', 'Los Angeles', 'CA', '90095',
     '(310) 825-9111', 'https://www.uclahealth.org', 34.0664, -118.4463),
    ('Keck Hospital of USC', 'Keck Medicine of USC', 'Hospital',
     '1500 San Pablo St', 'Los Angeles', 'CA', '90033',
     '(323) 442-8500', 'https://www.keckmedicine.org', 34.0615, -118.2037),
    ('LAC+USC Medical Center', 'LA County + USC', 'Hospital',
     '2051 Marengo St', 'Los Angeles', 'CA', '90033',
     '(323) 409-1000', 'https://dhs.lacounty.gov/lac-usc-medical-center', 34.0588, -118.2065),

    # ── San Francisco ────────────────────────────────────────────────────────
    ('UCSF Medical Center at Parnassus', 'UCSF Health', 'Hospital',
     '505 Parnassus Ave', 'San Francisco', 'CA', '94143',
     '(415) 476-1000', 'https://www.ucsfhealth.org', 37.7632, -122.4576),
    ('Zuckerberg San Francisco General Hospital', 'San Francisco Health', 'Hospital',
     '1001 Potrero Ave', 'San Francisco', 'CA', '94110',
     '(415) 206-8000', 'https://zuckerbergsanfranciscogeneral.org', 37.7552, -122.4054),

    # ── Chicago ──────────────────────────────────────────────────────────────
    ('Northwestern Memorial Hospital', 'Northwestern Medicine', 'Hospital',
     '251 E Huron St', 'Chicago', 'IL', '60611',
     '(312) 926-2000', 'https://www.northwesternmedicine.org', 41.8959, -87.6217),
    ('Rush University Medical Center', 'Rush', 'Hospital',
     '1653 W Congress Pkwy', 'Chicago', 'IL', '60612',
     '(312) 942-5000', 'https://www.rush.edu', 41.8732, -87.6682),
    ('John H. Stroger Jr. Hospital', 'Cook County Health', 'Hospital',
     '1969 W Ogden Ave', 'Chicago', 'IL', '60612',
     '(312) 864-6000', 'https://cookcountyhealth.org', 41.8742, -87.6749),

    # ── Boston ───────────────────────────────────────────────────────────────
    ('Massachusetts General Hospital', 'Mass General Brigham', 'Hospital',
     '55 Fruit St', 'Boston', 'MA', '02114',
     '(617) 726-2000', 'https://www.massgeneral.org', 42.3633, -71.0685),
    ("Brigham and Women's Hospital", 'Mass General Brigham', 'Hospital',
     '75 Francis St', 'Boston', 'MA', '02115',
     '(617) 732-5500', 'https://www.brighamandwomens.org', 42.3356, -71.1068),
    ('Boston Medical Center', 'Boston Medical Center', 'Hospital',
     '1 Boston Medical Center Pl', 'Boston', 'MA', '02118',
     '(617) 638-8000', 'https://www.bmc.org', 42.3354, -71.0723),

    # ── Baltimore ────────────────────────────────────────────────────────────
    ('The Johns Hopkins Hospital', 'Johns Hopkins Medicine', 'Hospital',
     '1800 Orleans St', 'Baltimore', 'MD', '21287',
     '(410) 955-5000', 'https://www.hopkinsmedicine.org', 39.2965, -76.5926),

    # ── Minnesota ────────────────────────────────────────────────────────────
    ('Mayo Clinic', 'Mayo Clinic', 'Hospital',
     '200 1st St SW', 'Rochester', 'MN', '55905',
     '(507) 284-2511', 'https://www.mayoclinic.org', 44.0221, -92.4655),

    # ── Ohio ─────────────────────────────────────────────────────────────────
    ('Cleveland Clinic', 'Cleveland Clinic', 'Hospital',
     '9500 Euclid Ave', 'Cleveland', 'OH', '44195',
     '(216) 444-2200', 'https://my.clevelandclinic.org', 41.5031, -81.6208),

    # ── Texas ────────────────────────────────────────────────────────────────
    ('Houston Methodist Hospital', 'Houston Methodist', 'Hospital',
     '6565 Fannin St', 'Houston', 'TX', '77030',
     '(713) 790-3311', 'https://www.houstonmethodist.org', 29.7099, -95.3989),
    ('Parkland Memorial Hospital', 'Parkland Health', 'Hospital',
     '5200 Harry Hines Blvd', 'Dallas', 'TX', '75235',
     '(214) 590-8000', 'https://www.parklandhealth.org', 32.8069, -96.8379),

    # ── Pennsylvania ─────────────────────────────────────────────────────────
    ('Hospital of the University of Pennsylvania', 'Penn Medicine', 'Hospital',
     '3400 Spruce St', 'Philadelphia', 'PA', '19104',
     '(215) 662-4000', 'https://www.pennmedicine.org', 39.9499, -75.1937),
    ('UPMC Presbyterian', 'UPMC', 'Hospital',
     '200 Lothrop St', 'Pittsburgh', 'PA', '15213',
     '(412) 647-2345', 'https://www.upmc.com', 40.4424, -79.9634),

    # ── Washington DC ────────────────────────────────────────────────────────
    ('MedStar Georgetown University Hospital', 'MedStar Health', 'Hospital',
     '3800 Reservoir Rd NW', 'Washington', 'DC', '20007',
     '(202) 444-2000', 'https://www.medstargeorgetown.org', 38.9337, -77.0782),
    ('George Washington University Hospital', 'GW Hospital', 'Hospital',
     '900 23rd St NW', 'Washington', 'DC', '20037',
     '(202) 715-4000', 'https://www.gwhospital.com', 38.9020, -77.0472),

    # ── Florida ──────────────────────────────────────────────────────────────
    ('Jackson Memorial Hospital', 'Jackson Health System', 'Hospital',
     '1611 NW 12th Ave', 'Miami', 'FL', '33136',
     '(305) 585-1111', 'https://jacksonhealth.org', 25.7896, -80.2100),

    # ── Georgia ──────────────────────────────────────────────────────────────
    ('Grady Memorial Hospital', 'Grady Health System', 'Hospital',
     '80 Jesse Hill Jr Dr SE', 'Atlanta', 'GA', '30303',
     '(404) 616-4307', 'https://www.gradyhealth.org', 33.7480, -84.3885),
    ('Emory University Hospital', 'Emory Healthcare', 'Hospital',
     '1364 Clifton Rd NE', 'Atlanta', 'GA', '30322',
     '(404) 712-2000', 'https://www.emoryhealthcare.org', 33.7942, -84.3231),

    # ── Washington State ─────────────────────────────────────────────────────
    ('Harborview Medical Center', 'UW Medicine', 'Hospital',
     '325 9th Ave', 'Seattle', 'WA', '98104',
     '(206) 744-3000', 'https://www.uwmedicine.org/locations/harborview-medical-center', 47.6047, -122.3303),
    ('UW Medical Center - Montlake', 'UW Medicine', 'Hospital',
     '1959 NE Pacific St', 'Seattle', 'WA', '98195',
     '(206) 598-3300', 'https://www.uwmedicine.org', 47.6527, -122.3098),

    # ── Tennessee ────────────────────────────────────────────────────────────
    ('Vanderbilt University Medical Center', 'Vanderbilt Health', 'Hospital',
     '1211 Medical Center Dr', 'Nashville', 'TN', '37232',
     '(615) 322-5000', 'https://www.vumc.org', 36.1449, -86.8027),

    # ── Louisiana ────────────────────────────────────────────────────────────
    ('University Medical Center New Orleans', 'LCMC Health', 'Hospital',
     '2000 Canal St', 'New Orleans', 'LA', '70112',
     '(504) 702-3000', 'https://www.umcno.org', 29.9527, -90.0685),
]


# one DB connection per request, stored in Flask's `g` object
# don't call get_db() outside a request context (e.g. background threads won't work)
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row  # rows behave like dicts: row['column_name']
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()


# runs on every startup, safe to re-run since everything uses IF NOT EXISTS
def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        db.commit()
        from hrsa import seed_clinics
        seed_clinics(db)
        seed_curated_hospitals(db)


# haversine formula — gives accurate straight-line distance between two GPS points
# accurate to under 0.5% for distances below ~1000 km, which is more than enough here
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# two-step distance query for speed:
#   first, a cheap bounding-box filter in SQL (hits the lat/lon indexes, cuts out most rows)
#   then an exact haversine check in Python on the small set that's left
# to show more results, increase limit=40. to change the default radius, edit config.py
def get_nearby_clinics(db, lat, lon, radius_km=25, limit=40):
    deg = radius_km / 111.0  # ~111 km per degree of latitude
    rows = db.execute("""
        SELECT id, hrsa_id, name, org_name, address, city, state, zip,
               phone, latitude, longitude, hours_per_week, site_type
        FROM clinics
        WHERE latitude  BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
          AND status = 'Active'
    """, (lat - deg, lat + deg, lon - deg, lon + deg)).fetchall()

    results = []
    for r in rows:
        dist = haversine_km(lat, lon, r['latitude'], r['longitude'])
        if dist <= radius_km:
            results.append({**dict(r), 'distance_km': round(dist, 2)})

    results.sort(key=lambda x: x['distance_km'])
    return results[:limit]


# fallback when no real community report exists for a clinic
# uses time of day + day of week to guess how busy it probably is
# shown in the UI with a ~ prefix so users know it's estimated, not crowd-sourced
# to adjust the "busy hours", just change the hour ranges in the if/elif blocks below
def get_estimated_wait():
    now  = datetime.now()
    hour = now.hour
    is_weekend = now.weekday() >= 5

    if is_weekend:
        if   8 <= hour < 12:  return '60-120'  # weekend morning rush
        elif 12 <= hour < 17: return '30-60'   # weekend afternoon
        elif 17 <= hour < 20: return '15-30'   # weekend evening cool-down
        else:                 return '<15'      # very early / late night
    else:
        if   8 <= hour < 11:  return '30-60'   # weekday morning
        elif 11 <= hour < 14: return '60-120'  # lunch rush (peak)
        elif 14 <= hour < 17: return '30-60'   # weekday afternoon
        elif 17 <= hour < 20: return '15-30'   # evening wind-down
        else:                 return '<15'      # off hours


# grabs the most recent wait report within the last 3 hours, or returns None
# to change how long reports stay active, edit '-3 hours' below
def get_current_wait(db, clinic_id):
    row = db.execute("""
        SELECT wait_bucket, reported_at
        FROM wait_reports
        WHERE clinic_id = ?
          AND reported_at >= datetime('now', '-3 hours')
        ORDER BY reported_at DESC
        LIMIT 1
    """, (clinic_id,)).fetchone()
    return dict(row) if row else None


# average star rating + review count for one clinic — ratings never expire
def get_clinic_rating(db, clinic_id):
    row = db.execute("""
        SELECT ROUND(AVG(CAST(rating AS REAL)), 1) AS avg_rating,
               COUNT(*) AS review_count
        FROM clinic_reviews WHERE clinic_id = ?
    """, (clinic_id,)).fetchone()
    return {'avg_rating': row['avg_rating'], 'review_count': row['review_count'] or 0}


# bulk version of get_clinic_rating so we don't hit the DB once per clinic
def get_clinic_ratings_bulk(db, clinic_ids):
    if not clinic_ids:
        return {}
    placeholders = ','.join('?' * len(clinic_ids))
    rows = db.execute(f"""
        SELECT clinic_id,
               ROUND(AVG(CAST(rating AS REAL)), 1) AS avg_rating,
               COUNT(*) AS review_count
        FROM clinic_reviews
        WHERE clinic_id IN ({placeholders})
        GROUP BY clinic_id
    """, clinic_ids).fetchall()
    return {r['clinic_id']: {'avg_rating': r['avg_rating'], 'review_count': r['review_count']}
            for r in rows}


# community-reported private insurance plans for one clinic, from the last 180 days
# sorted by how often each plan was reported. to change expiry, edit '-180 days'
def get_insurance_plans(db, clinic_id):
    rows = db.execute("""
        SELECT plan_name,
               COUNT(*)      AS report_count,
               MAX(reported_at) AS latest_report
        FROM insurance_reports
        WHERE clinic_id = ?
          AND reported_at >= datetime('now', '-180 days')
        GROUP BY plan_name
        ORDER BY report_count DESC
        LIMIT 20
    """, (clinic_id,)).fetchall()
    return [{'plan_name': r['plan_name'], 'count': r['report_count'],
             'latest_report': r['latest_report']} for r in rows]


# seeds the curated hospital list on startup — skips if already populated
def seed_curated_hospitals(db):
    if db.execute('SELECT COUNT(*) FROM curated_hospitals').fetchone()[0] > 0:
        return  # already seeded
    db.executemany("""
        INSERT INTO curated_hospitals
          (name, network, type, address, city, state, zip, phone, website, latitude, longitude)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, CURATED_HOSPITALS)
    db.commit()


# same bounding-box + haversine approach as get_nearby_clinics
# no result cap since there are only ~40 curated entries total
def get_nearby_curated_hospitals(db, lat, lon, radius_km):
    deg = radius_km / 111.0
    rows = db.execute("""
        SELECT id, name, network, type, address, city, state, zip,
               phone, website, latitude, longitude
        FROM curated_hospitals
        WHERE latitude  BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
    """, (lat - deg, lat + deg, lon - deg, lon + deg)).fetchall()
    results = []
    for r in rows:
        dist = haversine_km(lat, lon, r['latitude'], r['longitude'])
        if dist <= radius_km:
            results.append({**dict(r), 'distance_km': round(dist, 2)})
    results.sort(key=lambda x: x['distance_km'])
    return results


# bulk version — gets insurance plans for a bunch of clinics in one query
def get_insurance_plans_bulk(db, clinic_ids):
    if not clinic_ids:
        return {}
    placeholders = ','.join('?' * len(clinic_ids))
    rows = db.execute(f"""
        SELECT clinic_id, plan_name
        FROM insurance_reports
        WHERE clinic_id IN ({placeholders})
          AND reported_at >= datetime('now', '-180 days')
        GROUP BY clinic_id, plan_name
    """, clinic_ids).fetchall()
    result = {}
    for r in rows:
        cid = r['clinic_id']
        if cid not in result:
            result[cid] = []
        result[cid].append(r['plan_name'])
    return result


# bulk version of get_current_wait — grabs the most recent report per clinic in one shot
def get_current_waits_bulk(db, clinic_ids):
    if not clinic_ids:
        return {}
    placeholders = ','.join('?' * len(clinic_ids))
    rows = db.execute(f"""
        SELECT w.clinic_id, w.wait_bucket
        FROM wait_reports w
        INNER JOIN (
            SELECT clinic_id, MAX(reported_at) AS max_time
            FROM wait_reports
            WHERE clinic_id IN ({placeholders})
              AND reported_at >= datetime('now', '-3 hours')
            GROUP BY clinic_id
        ) latest ON w.clinic_id = latest.clinic_id
                   AND w.reported_at = latest.max_time
    """, clinic_ids).fetchall()
    return {r['clinic_id']: r['wait_bucket'] for r in rows}
