# reads the HRSA health centers CSV and seeds the clinics table on first run
# only runs once — if the table already has rows it bails out immediately
import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), 'data', 'health_centers.csv')


def seed_clinics(db):
    count = db.execute("SELECT COUNT(*) FROM clinics").fetchone()[0]
    if count > 0:
        return

    print("Seeding clinic data from HRSA CSV (first run only)...")
    rows = []
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Site Status Description', '').strip() != 'Active':
                continue
            try:
                # Y coordinate = latitude, X coordinate = longitude (GIS convention)
                lat = float(row['Geocoding Artifact Address Primary Y Coordinate'])
                lon = float(row['Geocoding Artifact Address Primary X Coordinate'])
            except (ValueError, KeyError):
                continue

            hrs = None
            try:
                hrs = int(float(row.get('Operating Hours per Week') or 0)) or None
            except (ValueError, TypeError):
                pass

            rows.append((
                row.get('BHCMIS Organization Identification Number', '').strip(),
                row.get('Site Name', '').strip(),
                row.get('Health Center Name', '').strip(),
                row.get('Site Address', '').strip(),
                row.get('Site City', '').strip(),
                row.get('Site State Abbreviation', '').strip(),
                row.get('Site Postal Code', '').strip(),
                row.get('Site Telephone Number', '').strip(),
                lat,
                lon,
                hrs,
                row.get('Health Center Type', '').strip(),
                'Active',
            ))

    db.executemany("""
        INSERT OR IGNORE INTO clinics
          (hrsa_id, name, org_name, address, city, state, zip, phone,
           latitude, longitude, hours_per_week, site_type, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    db.commit()
    print(f"Seeded {len(rows)} active clinic sites.")
