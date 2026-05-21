# ClinicFinder — Flask backend
# start with: py -3 app.py  →  opens at http://127.0.0.1:5000

import requests
from flask import Flask, render_template, request, jsonify, g

from config import Config
from database import (
    get_db, close_db, init_db,
    get_nearby_clinics, get_current_wait, get_current_waits_bulk,
    get_estimated_wait, get_clinic_rating, get_clinic_ratings_bulk,
    get_insurance_plans, get_insurance_plans_bulk,
    get_nearby_curated_hospitals, haversine_km,
)

# wait bucket options — if you ever change these, update the CHECK in database.py
# and the <select id="wait-select"> in index.html too, otherwise submissions will fail
VALID_BUCKETS = {'<15', '15-30', '30-60', '60-120', '120+'}


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    @app.route('/')
    def index():
        # the Maps JS API key has to live in the HTML — Google requires it client-side
        # and there's no way around that. the real protection is setting referrer
        # restrictions in Google Cloud Console so it only works on your domain
        return render_template('index.html', maps_key=app.config['GOOGLE_MAPS_KEY'])

    # calls Street View on the server side so the key never shows up in page source.
    # the browser hits /api/streetview?lat=...&lon=... and Flask forwards to Google.
    # if Street View isn't enabled in Cloud Console, Google returns an error
    # and the JS onerror handler quietly hides the photo — nothing breaks.
    @app.route('/api/streetview')
    def streetview_proxy():
        lat = request.args.get('lat', type=float)
        lon = request.args.get('lon', type=float)
        if lat is None or lon is None:
            return '', 400
        try:
            # size controls the image dimensions — keep it consistent with .clinic-photo-img in CSS
            # fov is the field of view — lower number = more zoomed in
            sv_url = (
                f'https://maps.googleapis.com/maps/api/streetview'
                f'?size=640x160&location={lat},{lon}&fov=90'
                f'&return_error_code=true&key={app.config["GOOGLE_MAPS_KEY"]}'
            )
            resp = requests.get(sv_url, timeout=8)
            content_type = resp.headers.get('Content-Type', 'image/jpeg')
            return resp.content, resp.status_code, {'Content-Type': content_type}
        except Exception:
            return '', 502

    # pulls nearby free clinics with their wait, rating, and insurance all in one shot
    # want more results? bump limit=40 in get_nearby_clinics() inside database.py
    @app.route('/api/clinics/nearby')
    def clinics_nearby():
        try:
            lat = float(request.args['lat'])
            lon = float(request.args['lon'])
        except (KeyError, ValueError):
            return jsonify({'error': 'lat and lon are required'}), 400

        # radius defaults to SEARCH_RADIUS_KM in config.py (25 km)
        radius = float(request.args.get('radius', app.config['SEARCH_RADIUS_KM']))
        db = get_db()
        clinics = get_nearby_clinics(db, lat, lon, radius_km=radius)

        # bulk queries instead of hitting the DB once per clinic — much faster
        ids     = [c['id'] for c in clinics]
        waits   = get_current_waits_bulk(db, ids)   # reports from the last 3 hours
        ratings = get_clinic_ratings_bulk(db, ids)  # all-time average
        plans   = get_insurance_plans_bulk(db, ids) # community insurance, last 180 days
        estimate = get_estimated_wait()              # time-of-day fallback if no report exists

        for c in clinics:
            user_bucket = waits.get(c['id'])
            if user_bucket:
                c['wait_bucket'] = user_bucket
                c['is_estimate'] = False
            else:
                # no real report in the last 3 hours — fall back to the heuristic estimate
                # the UI shows this faded with a ~ to make clear it's not crowd-sourced
                c['wait_bucket'] = estimate
                c['is_estimate'] = True
            r = ratings.get(c['id'], {})
            c['avg_rating']      = r.get('avg_rating')
            c['review_count']    = r.get('review_count', 0)
            c['community_plans'] = plans.get(c['id'], [])  # read by the insurance filter in map.js

        return jsonify(clinics)

    # full detail for one clinic — called when a user clicks on a result
    @app.route('/api/clinics/<int:clinic_id>')
    def clinic_detail(clinic_id):
        db = get_db()
        row = db.execute('SELECT * FROM clinics WHERE id = ?', (clinic_id,)).fetchone()
        if not row:
            return jsonify({'error': 'not found'}), 404

        result = dict(row)
        wait = get_current_wait(db, clinic_id)
        if wait:
            result['wait_bucket']      = wait['wait_bucket']
            result['wait_reported_at'] = wait['reported_at']
            result['is_estimate']      = False
        else:
            result['wait_bucket']      = get_estimated_wait()
            result['wait_reported_at'] = None
            result['is_estimate']      = True
        rating = get_clinic_rating(db, clinic_id)
        result['avg_rating']      = rating['avg_rating']
        result['review_count']    = rating['review_count']
        result['insurance_plans'] = get_insurance_plans(db, clinic_id)
        return jsonify(result)

    # crowd-sourced wait time submission
    # reports expire after 3 hours — to change that window, edit '-3 hours' in database.py
    @app.route('/api/wait-report', methods=['POST'])
    def submit_wait_report():
        data = request.get_json(force=True, silent=True) or {}
        clinic_id   = data.get('clinic_id')
        wait_bucket = data.get('wait_bucket')

        if not clinic_id or wait_bucket not in VALID_BUCKETS:
            return jsonify({'error': 'invalid input'}), 400

        db = get_db()
        if not db.execute('SELECT 1 FROM clinics WHERE id = ?', (clinic_id,)).fetchone():
            return jsonify({'error': 'clinic not found'}), 404

        db.execute(
            'INSERT INTO wait_reports (clinic_id, wait_bucket) VALUES (?, ?)',
            (clinic_id, wait_bucket)
        )
        db.commit()
        return jsonify({'ok': True, 'wait_bucket': wait_bucket}), 201

    # star rating submission — 1 to 5, with an optional comment (capped at 500 chars)
    @app.route('/api/review', methods=['POST'])
    def submit_review():
        data = request.get_json(force=True, silent=True) or {}
        clinic_id = data.get('clinic_id')
        rating    = data.get('rating')
        comment   = (data.get('comment') or '').strip()[:500]

        if not clinic_id or not isinstance(rating, int) or isinstance(rating, bool) \
                or not (1 <= rating <= 5):
            return jsonify({'error': 'invalid input'}), 400

        db = get_db()
        if not db.execute('SELECT 1 FROM clinics WHERE id = ?', (clinic_id,)).fetchone():
            return jsonify({'error': 'clinic not found'}), 404

        db.execute(
            'INSERT INTO clinic_reviews (clinic_id, rating, comment) VALUES (?, ?, ?)',
            (clinic_id, rating, comment or None)
        )
        db.commit()
        return jsonify({'ok': True, **get_clinic_rating(db, clinic_id)}), 201

    # user reports which private insurance was accepted here
    # expires after 180 days — change that in get_insurance_plans() in database.py
    @app.route('/api/insurance-report', methods=['POST'])
    def submit_insurance_report():
        data = request.get_json(force=True, silent=True) or {}
        clinic_id = data.get('clinic_id')
        plan_name = (data.get('plan_name') or '').strip()[:100]

        if not clinic_id or not plan_name:
            return jsonify({'error': 'invalid input'}), 400

        db = get_db()
        if not db.execute('SELECT 1 FROM clinics WHERE id = ?', (clinic_id,)).fetchone():
            return jsonify({'error': 'clinic not found'}), 404

        db.execute(
            'INSERT INTO insurance_reports (clinic_id, plan_name) VALUES (?, ?)',
            (clinic_id, plan_name)
        )
        db.commit()
        return jsonify({'ok': True}), 201

    # server-side geocoding via Nominatim (OpenStreetMap, free, no key needed)
    # done here instead of client-side because the Google Maps Geocoder API key
    # has domain restrictions that block it during local development
    @app.route('/api/geocode')
    def geocode():
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'error': 'query required'}), 400
        country = request.args.get('country', '').strip()[:2].lower()
        try:
            params = {'q': query, 'format': 'json', 'limit': 1}
            if country:
                params['countrycodes'] = country  # stops ZIPs from matching the wrong country
            resp = requests.get(
                'https://nominatim.openstreetmap.org/search',
                params=params,
                headers={'User-Agent': 'ClinicFinder/1.0'},
                timeout=6,
            )
            data = resp.json()
            if data:
                parts = data[0].get('display_name', query).split(', ')
                formatted = ', '.join(parts[:2]) if len(parts) >= 2 else parts[0]
                return jsonify({
                    'lat': float(data[0]['lat']),
                    'lng': float(data[0]['lon']),
                    'formatted': formatted,
                })
        except Exception:
            pass
        return jsonify({'error': 'not found'}), 404

    # returns the latest wait report for a clinic within the last 3 hours
    @app.route('/api/wait-report/<int:clinic_id>')
    def current_wait(clinic_id):
        db = get_db()
        wait = get_current_wait(db, clinic_id)
        if wait:
            return jsonify(wait)
        return jsonify({'wait_bucket': None, 'reported_at': None})

    # merges our curated hospital list with live Overpass (OpenStreetMap) data
    # curated ones always show first and win any duplicates with OSM
    # to add a hospital: edit CURATED_HOSPITALS in database.py, delete clinicfinder.db, restart
    @app.route('/api/emergency/nearby')
    def emergency_nearby():
        try:
            lat = float(request.args['lat'])
            lon = float(request.args['lon'])
        except (KeyError, ValueError):
            return jsonify({'error': 'lat and lon are required'}), 400

        radius   = float(request.args.get('radius', app.config['SEARCH_RADIUS_KM']))
        radius_m = int(radius * 1000)

        results = []
        seen = set()  # (name.lower(), rounded lat, rounded lon) — prevents duplicates

        # step 1: curated hospitals
        db = get_db()
        for h in get_nearby_curated_hospitals(db, lat, lon, radius):
            key = (h['name'].lower(), round(h['latitude'], 3), round(h['longitude'], 3))
            seen.add(key)
            results.append({
                'id': f"c{h['id']}",  # 'c' prefix avoids collisions with OSM integer IDs
                'name': h['name'],
                'type': h['type'],
                'network': h.get('network') or '',
                'latitude': h['latitude'],
                'longitude': h['longitude'],
                'phone': h.get('phone') or '',
                'address': h.get('address') or '',
                'city': h.get('city') or '',
                'state': h.get('state') or '',
                'website': h.get('website') or '',
                'distance_km': h['distance_km'],
            })

        # step 2: Overpass API for everything else
        # to add more types (e.g. pharmacies), add more node/way lines with the amenity tag
        # first load can be slow (~5s) — non-fatal if it times out, curated data still shows
        query = (
            f'[out:json][timeout:25];'
            f'('
            f'node["amenity"="hospital"](around:{radius_m},{lat},{lon});'
            f'way["amenity"="hospital"](around:{radius_m},{lat},{lon});'
            f'node["amenity"="urgent_care"](around:{radius_m},{lat},{lon});'
            f'way["amenity"="urgent_care"](around:{radius_m},{lat},{lon});'
            f');'
            f'out body center;'
        )

        try:
            resp = requests.post(
                'https://overpass-api.de/api/interpreter',
                data=query,
                timeout=20,
            )
            data = resp.json()
        except Exception:
            data = {}

        for el in data.get('elements', []):
            tags = el.get('tags', {})
            if el['type'] == 'node':
                elat, elon = el.get('lat'), el.get('lon')
            else:
                # 'way' elements use a center object instead of direct coordinates
                center = el.get('center', {})
                elat, elon = center.get('lat'), center.get('lon')
            if elat is None or elon is None:
                continue

            name = tags.get('name') or tags.get('operator')
            if not name:
                continue

            key = (name.lower(), round(elat, 3), round(elon, 3))
            if key in seen:
                continue
            seen.add(key)

            amenity  = tags.get('amenity', '')
            er_type  = 'Hospital' if amenity == 'hospital' else 'Urgent Care'
            address  = f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip()

            results.append({
                'id': el['id'],
                'name': name,
                'type': er_type,
                'network': '',
                'latitude': elat,
                'longitude': elon,
                'phone': tags.get('phone') or tags.get('contact:phone') or '',
                'address': address,
                'city': tags.get('addr:city', ''),
                'state': tags.get('addr:state', ''),
                'website': tags.get('website') or tags.get('contact:website') or '',
                'distance_km': round(haversine_km(lat, lon, elat, elon), 2),
            })

        results.sort(key=lambda x: x['distance_km'])
        return jsonify(results[:50])

    init_db(app)
    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
