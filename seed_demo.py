# run this once before the demo: py -3 seed_demo.py
# fills the insurance_reports table so the insurance filter actually works
# skips automatically if reports already exist, so safe to re-run

import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = 'clinicfinder.db'

# which private plans are common in each state — based on real carrier presence
# plan names here must match the option values in index.html exactly
# swap out any list if you want different plans for a state
STATE_PLANS = {
    # Northeast
    'NY': ['Blue Cross Blue Shield', 'Fidelis Care', 'HealthFirst', 'MetroPlus',
           'EmblemHealth', 'UnitedHealthcare', 'Aetna', 'Oscar Health'],
    'NJ': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Cigna',
           'Amerigroup', 'Anthem'],
    'CT': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Anthem'],
    'MA': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Anthem', 'Cigna'],
    'PA': ['Highmark', 'Independence Blue Cross', 'Blue Cross Blue Shield',
           'Aetna', 'UnitedHealthcare'],
    'ME': ['Blue Cross Blue Shield', 'Aetna', 'Anthem'],
    'NH': ['Blue Cross Blue Shield', 'Aetna', 'Anthem'],
    'VT': ['Blue Cross Blue Shield', 'Anthem'],
    'RI': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare'],

    # Mid-Atlantic
    'MD': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'CareSource'],
    'DC': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Amerigroup'],
    'VA': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Amerigroup', 'Anthem'],
    'WV': ['Blue Cross Blue Shield', 'Aetna', 'WellCare', 'Highmark'],
    'DE': ['Blue Cross Blue Shield', 'Highmark', 'Aetna', 'Amerigroup'],

    # Southeast
    'FL': ['Blue Cross Blue Shield', 'WellCare', 'Aetna', 'UnitedHealthcare',
           'Humana', 'Ambetter'],
    'GA': ['Blue Cross Blue Shield', 'Amerigroup', 'WellCare', 'Aetna', 'Ambetter'],
    'NC': ['Blue Cross Blue Shield', 'WellCare', 'Aetna', 'UnitedHealthcare', 'Ambetter'],
    'SC': ['Blue Cross Blue Shield', 'Aetna', 'Molina Healthcare', 'Ambetter'],
    'TN': ['Blue Cross Blue Shield', 'Amerigroup', 'Aetna', 'Humana', 'UnitedHealthcare'],
    'AL': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Humana'],
    'MS': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'LA': ['Blue Cross Blue Shield', 'Aetna', 'Humana', 'Molina Healthcare', 'Ambetter'],
    'AR': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Ambetter'],
    'KY': ['Blue Cross Blue Shield', 'Anthem', 'Aetna', 'WellCare', 'Humana'],

    # Midwest
    'IL': ['Blue Cross Blue Shield', 'Molina Healthcare', 'Aetna',
           'UnitedHealthcare', 'Humana', 'Ambetter'],
    'OH': ['Blue Cross Blue Shield', 'CareSource', 'Molina Healthcare',
           'UnitedHealthcare', 'Aetna'],
    'MI': ['Blue Cross Blue Shield', 'Molina Healthcare', 'UnitedHealthcare',
           'Aetna', 'Humana'],
    'IN': ['Blue Cross Blue Shield', 'Anthem', 'Aetna', 'UnitedHealthcare', 'Ambetter'],
    'WI': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'MN': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'IA': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'MO': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'ND': ['Blue Cross Blue Shield', 'Aetna', 'Sanford Health (BCBS affiliated)'],
    'SD': ['Blue Cross Blue Shield', 'Aetna', 'Wellmark'],
    'NE': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare'],
    'KS': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Ambetter'],

    # South-Central
    'TX': ['Blue Cross Blue Shield', 'Amerigroup', 'Molina Healthcare',
           'Aetna', 'UnitedHealthcare', 'Ambetter'],
    'OK': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Ambetter'],

    # Mountain
    'CO': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare',
           'Kaiser Permanente', 'Cigna'],
    'UT': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'AZ': ['Blue Cross Blue Shield', 'Molina Healthcare', 'UnitedHealthcare',
           'Ambetter', 'Health Net'],
    'NM': ['Blue Cross Blue Shield', 'Molina Healthcare', 'UnitedHealthcare', 'Aetna'],
    'NV': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare',
           'Molina Healthcare', 'Health Net'],
    'ID': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Molina Healthcare'],
    'MT': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare'],
    'WY': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare'],

    # West Coast
    'CA': ['Blue Cross Blue Shield', 'Kaiser Permanente', 'Health Net',
           'Molina Healthcare', 'UnitedHealthcare', 'Aetna', 'Anthem', 'Oscar Health'],
    'OR': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare',
           'Molina Healthcare', 'Kaiser Permanente'],
    'WA': ['Premera Blue Cross', 'Blue Cross Blue Shield', 'Molina Healthcare',
           'UnitedHealthcare', 'Kaiser Permanente', 'Aetna'],

    # Alaska / Hawaii
    'AK': ['Blue Cross Blue Shield', 'Aetna', 'Premera Blue Cross'],
    'HI': ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Kaiser Permanente'],
}

UNIVERSAL_PLANS = ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare']  # show up nearly everywhere

DEFAULT_PLANS = ['Blue Cross Blue Shield', 'Aetna', 'UnitedHealthcare', 'Cigna', 'Humana']  # fallback if state not listed


def seed_demo():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    existing = db.execute('SELECT COUNT(*) FROM insurance_reports').fetchone()[0]
    if existing > 0:
        print(f'Already have {existing} insurance reports in the database.')
        print('To re-seed, delete clinicfinder.db and restart the server first.')
        db.close()
        return

    clinics = db.execute("""
        SELECT id, state FROM clinics
        WHERE status = 'Active' AND state IS NOT NULL
    """).fetchall()

    print(f'Seeding insurance data for {len(clinics)} clinics...')

    inserts = []
    now = datetime.now()
    random.seed(42)  # same seed = same data every time, good for demos

    for clinic in clinics:
        state = clinic['state']
        pool = STATE_PLANS.get(state, DEFAULT_PLANS)

        # weighted so most clinics get 2-3 plans, a few get 1 or 4
        n = random.choices([1, 2, 3, 4], weights=[10, 35, 40, 15])[0]
        chosen = random.sample(pool, min(n, len(pool)))

        for plan in chosen:
            days_ago = random.randint(0, 89)  # spread reports over the last 90 days
            hours_ago = random.randint(0, 23)
            reported_at = (
                now - timedelta(days=days_ago, hours=hours_ago)
            ).strftime('%Y-%m-%d %H:%M:%S')
            inserts.append((clinic['id'], plan, reported_at))

    db.executemany(
        'INSERT INTO insurance_reports (clinic_id, plan_name, reported_at) VALUES (?, ?, ?)',
        inserts
    )
    db.commit()

    from collections import Counter
    plan_counts = Counter(row[1] for row in inserts)
    print(f'\nDone — inserted {len(inserts)} reports across {len(clinics)} clinics.\n')
    print('Top plans by report count:')
    for plan, count in plan_counts.most_common(10):
        print(f'  {plan:<35} {count:>4} reports')

    db.close()


if __name__ == '__main__':
    seed_demo()
