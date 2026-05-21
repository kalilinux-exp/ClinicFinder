# ClinicFinder

Find free and sliding-scale health clinics near you, with crowd-sourced wait times.

Built for HackAmerica — solo project by Kalixte Petrof.

---

## The Problem

Millions of uninsured Americans struggle to find free or sliding-scale clinics nearby. Existing tools are outdated, hard to use, and never show real wait times — making an already stressful situation worse.

## The Solution

ClinicFinder uses your location to show the nearest free clinics on an interactive map. Color-coded pins show live crowd-sourced wait times. Anyone can report a wait in two taps. Think Waze, but for free healthcare.

---

## Features

**Free clinic search**
- 18,000+ federally qualified health centers (FQHCs) from HRSA federal data
- Sorted by distance, color-coded by wait time (green = short, red = long)
- GPS location or search by city, address, or ZIP

**Filters**
- Wait time (≤ 15 min, ≤ 30 min, ≤ 1 hr)
- Star rating (3+, 4+)
- Insurance accepted (27 plans across all major carriers — Medicaid, BCBS, Aetna, Oscar, Tricare, and more)
- Radius (25 / 40 / 50 / 75 km)

**Emergency care tab**
- Hospitals and urgent care centers pulled from OpenStreetMap (live, no key needed)
- 40+ major US hospitals hand-verified with accurate phone numbers and websites (Mount Sinai, MGH, Mayo Clinic, Cedars-Sinai, and more)
- Billing warning shown so users know ERs aren't free

**Community features**
- Crowd-sourced wait times — expire after 3 hours to stay fresh
- Star ratings with optional comments
- Insurance reports — users can report which private plans were accepted
- All guaranteed plans (Medicaid, Medicare, CHIP, Self-pay) shown automatically 
— required by federal law at every FQHC

**Other**
- Street View photos proxied through Flask so the API key never touches the browser
- Server-side geocoding via Nominatim (no extra key needed)
- Time-of-day wait estimate shown when no community report exists
- This only shows/has clinics from the USA. This is a Demo afterall.
---

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Add your Google Maps API key to a `.env` file:
   ```
   GOOGLE_MAPS_KEY=your_key_here
   ```

3. Run the app:
   ```
   py -3 app.py
   ```
   First run seeds ~18,000 clinics from the HRSA CSV (takes ~5 seconds). Every run after that is instant.

4. Open **http://localhost:5000**

5. Optional — seed demo insurance data so filters work out of the box:
   ```
   py -3 seed_demo.py
   ```

---

## Tech Stack

| | |
|---|---|
| Backend | Python / Flask |
| Database | SQLite |
| Map | Google Maps JavaScript API (AdvancedMarkerElement) |
| Clinic data | [HRSA Health Center Data](https://data.hrsa.gov/) — 18,000+ active FQHCs |
| Hospital data | OpenStreetMap via Overpass API + curated list |
| Geocoding | Nominatim (OpenStreetMap) |

---

## Team

Solo — Kalixte Petrof

---

## Reflection

The hardest part was the HRSA CSV using GIS convention (Y = latitude, X = longitude — the opposite of what most developers expect). The crowd-sourced wait time model was inspired by Waze: simple, real-time, community-powered. The biggest architectural decision was proxying Street View through Flask so the Maps key never appears in page source, while accepting that the Maps JavaScript API key itself has to be client-side — that's just how Google Maps works, and the right fix is referrer restrictions in Cloud Console, not hiding the key.
