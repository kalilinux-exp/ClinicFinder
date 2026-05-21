'use strict';

// all app state lives here — one place, nothing scattered across globals
// clinics and erData are cached per location so switching tabs doesn't re-fetch
// they get cleared whenever the user searches a new location or changes the radius
const STATE = {
  map: null,
  infoWindow: null,
  markers: [],
  clinics: [],
  erData: [],
  activeTab: 'clinics',
  selectedId: null,
  userLat: null,
  userLon: null,
  radiusKm: 25,
};

// pin colors and badge labels for each wait bucket
// to change a color just update the hex — it applies to map pins and badges automatically
// to add a new bucket, also update VALID_BUCKETS in app.py, the CHECK in database.py,
// and the <select id="wait-select"> options in index.html
const WAIT_COLORS = {
  '<15':    '#22c55e',  // green
  '15-30':  '#eab308',  // yellow
  '30-60':  '#f97316',  // orange
  '60-120': '#ef4444',  // red
  '120+':   '#991b1b',  // dark red
};
const WAIT_LABELS = {
  '<15':    '< 15 min',
  '15-30':  '15–30 min',
  '30-60':  '30–60 min',
  '60-120': '1–2 hrs',
  '120+':   '2+ hrs',
};
const WAIT_UNKNOWN_COLOR = '#9ca3af';  // gray — used for estimated/unknown waits
const WAIT_UNKNOWN_LABEL = 'Unknown';

// used by the wait filter to compare buckets — must stay in ascending order
const WAIT_ORDER = ['<15', '15-30', '30-60', '60-120', '120+'];

// FQHCs are legally required to accept these — they pass the insurance filter automatically
// without needing any community reports. to add one, also add a static badge in index.html
const GUARANTEED_PLANS = new Set(['Medicaid', 'Medicare', 'CHIP', 'Self-pay']);

function waitColor(bucket) { return WAIT_COLORS[bucket] ?? WAIT_UNKNOWN_COLOR; }
function waitLabel(bucket) { return WAIT_LABELS[bucket] ?? WAIT_UNKNOWN_LABEL; }

// some HRSA entries are health centers inside schools — the "site name" is the school name,
// but the actual clinic name is in org_name. this regex catches those and swaps it
const INSTITUTION_PATTERN = /\b(elementary|middle\s+school|high\s+school|grammar\s+school|junior\s+high)\b/i;

function clinicDisplayName(clinic) {
  if (INSTITUTION_PATTERN.test(clinic.name) && clinic.org_name && clinic.org_name !== clinic.name) {
    return { primary: clinic.org_name, atSite: clinic.name };
  }
  return { primary: clinic.name, atSite: null };
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return '';
  const then = new Date(isoStr.replace(' ', 'T') + 'Z');
  const diffMin = Math.round((Date.now() - then) / 60000);
  if (diffMin < 1)  return 'just now';
  if (diffMin < 60) return `${diffMin} min ago`;
  return `${Math.round(diffMin / 60)} hr ago`;
}

let selectedRating = 0;

// Filters
// to add a new filter: add a <select> in #filter-bar (index.html), wire its change event
// in initMap(), read its value in applyFilters(), write a passes___Filter() function,
// and call it inside the filter() callback in applyFilters()

// wait filter — 'le15'/'le30'/'le60' map to positions in WAIT_ORDER
// clinics with no wait data are never filtered out (index returns -1)
function passesWaitFilter(bucket, filter) {
  if (!filter || !bucket) return true;
  const idx = WAIT_ORDER.indexOf(bucket);
  if (idx === -1) return true;  // unknown/estimate — don't filter out
  const maxIdx = { le15: 0, le30: 1, le60: 2 }[filter];
  return maxIdx !== undefined && idx <= maxIdx;
}

// insurance filter — guaranteed plans pass automatically for every FQHC
// private plans check community_plans[], which is seeded from insurance_reports in the DB
function passesInsuranceFilter(clinic, plan) {
  if (!plan) return true;
  if (GUARANTEED_PLANS.has(plan)) return true;  // all FQHCs legally required to accept these
  return (clinic.community_plans || []).some(p => p === plan);
}

function applyFilters() {
  const waitF    = document.getElementById('filter-wait').value;
  const ratingF  = parseFloat(document.getElementById('filter-rating').value) || 0;
  const insF     = document.getElementById('filter-insurance').value;
  const erTypeF  = document.getElementById('filter-er-type').value;

  const anyActive = !!waitF || ratingF > 0 || !!insF || !!erTypeF;
  document.getElementById('filter-wait').classList.toggle('active', !!waitF);
  document.getElementById('filter-rating').classList.toggle('active', ratingF > 0);
  document.getElementById('filter-insurance').classList.toggle('active', !!insF);
  document.getElementById('filter-er-type').classList.toggle('active', !!erTypeF);
  document.getElementById('filter-clear').style.display = anyActive ? '' : 'none';

  if (STATE.activeTab === 'clinics') {
    if (!STATE.clinics.length) return;
    const filtered = STATE.clinics.filter(c => {
      if (!passesWaitFilter(c.wait_bucket, waitF)) return false;
      if (ratingF > 0 && c.review_count > 0 && c.avg_rating < ratingF) return false;
      if (!passesInsuranceFilter(c, insF)) return false;
      return true;
    });
    clearMarkers();
    if (filtered.length) {
      hideSidebarState();
      renderSidebarList(filtered);
      renderMapMarkers(filtered);
    } else {
      setSidebarState('No clinics match your filters. Try adjusting or clearing them.');
      document.getElementById('clinic-list').innerHTML = '';
    }
  } else {
    if (!STATE.erData.length) return;
    const filtered = STATE.erData.filter(item => {
      if (erTypeF && item.type !== erTypeF) return false;
      return true;
    });
    clearMarkers();
    if (filtered.length) {
      hideSidebarState();
      renderEmergencyList(filtered);
      renderEmergencyMarkers(filtered);
    } else {
      setSidebarState('No facilities match your filters.');
      document.getElementById('er-list').innerHTML = '';
    }
  }
}

function resetFilters() {
  document.getElementById('filter-wait').value = '';
  document.getElementById('filter-rating').value = '0';
  document.getElementById('filter-insurance').value = '';
  document.getElementById('filter-er-type').value = '';
  ['filter-wait', 'filter-rating', 'filter-insurance', 'filter-er-type'].forEach(id => {
    document.getElementById(id).classList.remove('active');
  });
  document.getElementById('filter-clear').style.display = 'none';
}

// called automatically by the Maps script tag (callback=initMap in the URL)
// wires up all event listeners, then kicks off geolocation
// DEMO_MAP_ID is needed for AdvancedMarkerElement — swap it for a real Map ID
// in Google Cloud Console if you want custom map styling

window.initMap = function () {
  STATE.map = new google.maps.Map(document.getElementById('map'), {
    center: { lat: 39.5, lng: -98.35 },  // geographic center of the contiguous US
    zoom: 4,
    mapId: 'DEMO_MAP_ID',
    zoomControl: true,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: false,
  });
  STATE.infoWindow = new google.maps.InfoWindow();

  document.getElementById('btn-locate').addEventListener('click', requestGeolocation);
  document.getElementById('detail-close').addEventListener('click', closeDetailPanel);

  document.getElementById('btn-report-wait').addEventListener('click', showWaitForm);
  document.getElementById('btn-cancel-report').addEventListener('click', hideWaitForm);
  document.getElementById('wait-form').addEventListener('submit', submitWaitReport);

  document.getElementById('btn-rate-clinic').addEventListener('click', showReviewForm);
  document.getElementById('btn-cancel-review').addEventListener('click', hideReviewForm);
  document.getElementById('review-form').addEventListener('submit', submitReview);
  initStarInput();

  document.getElementById('btn-add-insurance').addEventListener('click', showInsuranceForm);
  document.getElementById('btn-cancel-insurance').addEventListener('click', hideInsuranceForm);
  document.getElementById('insurance-form').addEventListener('submit', submitInsuranceReport);
  document.getElementById('insurance-select').addEventListener('change', (e) => {
    const custom = document.getElementById('insurance-custom');
    custom.classList.toggle('hidden', e.target.value !== 'other');
    if (e.target.value === 'other') { custom.value = ''; custom.focus(); }
  });

  document.getElementById('address-form').addEventListener('submit', handleAddressSearch);
  detectUserCountry();
  document.getElementById('radius-select').addEventListener('change', (e) => {
    STATE.radiusKm = parseInt(e.target.value);
    if (STATE.userLat !== null) {
      if (STATE.activeTab === 'clinics') loadNearbyClinics();
      else loadNearbyEmergency();
    }
  });
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  ['filter-wait', 'filter-rating', 'filter-insurance', 'filter-er-type'].forEach(id => {
    document.getElementById(id).addEventListener('change', applyFilters);
  });
  document.getElementById('filter-clear').addEventListener('click', () => { resetFilters(); applyFilters(); });

  requestGeolocation();
};

function requestGeolocation() {
  if (!navigator.geolocation) {
    showAddressSearch('Geolocation is not supported by your browser.');
    return;
  }
  setSidebarState('Detecting your location...');
  document.getElementById('address-msg').className = 'form-msg hidden';
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      STATE.userLat = pos.coords.latitude;
      STATE.userLon = pos.coords.longitude;
      STATE.clinics = [];
      STATE.erData = [];
      resetFilters();
      STATE.map.setCenter({ lat: STATE.userLat, lng: STATE.userLon });
      STATE.map.setZoom(12);
      placeUserMarker(STATE.userLat, STATE.userLon);
      if (STATE.activeTab === 'clinics') loadNearbyClinics();
      else loadNearbyEmergency();
    },
    () => {
      setSidebarState('Location access was denied. Search by city or ZIP above.');
      document.getElementById('address-input').focus();
    },
    { enableHighAccuracy: true, timeout: 30000, maximumAge: 0 }
  );
}

async function geocodeAddress(query) {
  const country = document.getElementById('country-select')?.value || 'us';
  const resp = await fetch(`/api/geocode?q=${encodeURIComponent(query)}&country=${encodeURIComponent(country)}`);
  if (!resp.ok) throw new Error('Address not found');
  const data = await resp.json();
  if (data.error) throw new Error(data.error);
  return data; // { lat, lng, formatted }
}

async function detectUserCountry() {
  try {
    const resp = await fetch('https://ipapi.co/json/');
    const data = await resp.json();
    const code = (data.country_code || '').toLowerCase();
    const sel = document.getElementById('country-select');
    if (sel && code) {
      const match = Array.from(sel.options).find(o => o.value === code);
      if (match) sel.value = code;
    }
  } catch {
    // keep default US
  }
}

function timeSince(isoStr) {
  if (!isoStr) return '';
  const days = Math.floor((Date.now() - new Date(isoStr.replace(' ', 'T') + 'Z')) / 86400000);
  if (days < 1) return 'today';
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

async function handleAddressSearch(e) {
  e.preventDefault();
  const query = document.getElementById('address-input').value.trim();
  const msgEl = document.getElementById('address-msg');
  if (!query) return;

  msgEl.textContent = 'Searching...';
  msgEl.className = 'form-msg info';

  try {
    const loc = await geocodeAddress(query);
    STATE.userLat = loc.lat;
    STATE.userLon = loc.lng;
    STATE.clinics = [];
    STATE.erData = [];
    resetFilters();
    STATE.map.setCenter({ lat: STATE.userLat, lng: STATE.userLon });
    STATE.map.setZoom(12);
    placeUserMarker(STATE.userLat, STATE.userLon);
    msgEl.className = 'form-msg hidden';
    setSidebarState(`Searching near ${loc.formatted}...`);
    if (STATE.activeTab === 'clinics') loadNearbyClinics();
    else loadNearbyEmergency();
  } catch {
    msgEl.textContent = 'Address not found. Try a different ZIP or city name.';
    msgEl.className = 'form-msg error';
  }
}

function placeUserMarker(lat, lon) {
  const dot = document.createElement('div');
  dot.style.cssText = [
    'width:16px', 'height:16px', 'border-radius:50%',
    'background:#3b82f6', 'border:3px solid white',
    'box-shadow:0 2px 8px rgba(0,0,0,.4)',
  ].join(';');
  new google.maps.marker.AdvancedMarkerElement({
    map: STATE.map,
    position: { lat, lng: lon },
    title: 'You are here',
    content: dot,
    zIndex: 9999,
  });
}

// results get cached in STATE.clinics so switching tabs doesn't re-fetch
async function loadNearbyClinics() {
  setSidebarState('Searching for clinics near you...');
  clearMarkers();
  document.getElementById('clinic-list').innerHTML = '';

  try {
    const url = `/api/clinics/nearby?lat=${STATE.userLat}&lon=${STATE.userLon}&radius=${STATE.radiusKm}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('Server error');
    const clinics = await resp.json();
    STATE.clinics = clinics;

    if (!clinics.length) {
      setSidebarState(`No clinics found within ${STATE.radiusKm} km. Try a larger radius or search a different location above.`);
      document.getElementById('address-input').focus();
      return;
    }

    hideSidebarState();
    renderSidebarList(clinics);
    renderMapMarkers(clinics);
  } catch (e) {
    setSidebarState('Could not load clinics. Check your connection and try again.');
  }
}

function clearMarkers() {
  STATE.markers.forEach(({ marker }) => { marker.map = null; });
  STATE.markers = [];
}

// to add more info to each card, edit the li.innerHTML block below
function renderSidebarList(clinics) {
  const ul = document.getElementById('clinic-list');
  ul.innerHTML = '';
  clinics.forEach(clinic => {
    const li = document.createElement('li');
    li.className = 'clinic-item';
    li.dataset.id = clinic.id;
    const distMi = (clinic.distance_km * 0.621371).toFixed(1);
    const ratingHtml = clinic.avg_rating
      ? `<span class="clinic-rating">${clinic.avg_rating}&#9733; (${clinic.review_count})</span>`
      : '';
    const { primary: dName, atSite } = clinicDisplayName(clinic);
    li.innerHTML = `
      <div class="clinic-item-top">
        <span class="clinic-item-name">${escHtml(dName)}</span>
        <span class="wait-badge ${clinic.is_estimate ? 'wait-estimated' : ''}"
              style="background:${waitColor(clinic.wait_bucket)};flex-shrink:0">
          ${clinic.is_estimate ? '~ ' : ''}${waitLabel(clinic.wait_bucket)}
        </span>
      </div>
      ${atSite ? `<div class="clinic-item-atsitename">at ${escHtml(atSite)}</div>` : ''}
      <div class="clinic-item-meta">
        <span>${distMi} mi away</span>
        <span>${escHtml(clinic.city)}, ${escHtml(clinic.state)}</span>
        ${clinic.phone ? `<span>${escHtml(clinic.phone)}</span>` : ''}
        ${ratingHtml}
      </div>`;
    li.addEventListener('click', () => selectClinic(clinic.id));
    ul.appendChild(li);
  });
}

// scale: 0.9 makes pins slightly smaller than default, looks less cluttered
function renderMapMarkers(clinics) {
  clinics.forEach(clinic => {
    const pin = new google.maps.marker.PinElement({
      background: waitColor(clinic.wait_bucket),
      borderColor: 'rgba(0,0,0,.25)',
      glyphColor: '#ffffff',
      scale: 0.9,
    });
    const marker = new google.maps.marker.AdvancedMarkerElement({
      map: STATE.map,
      position: { lat: clinic.latitude, lng: clinic.longitude },
      title: clinic.name,
      content: pin.element,
    });
    marker.addEventListener('gmp-click', () => selectClinic(clinic.id));
    STATE.markers.push({ marker, clinic });
  });
}

function updateMarkerColor(clinicId, bucket) {
  const entry = STATE.markers.find(m => m.clinic.id === clinicId);
  if (!entry) return;
  const pin = new google.maps.marker.PinElement({
    background: waitColor(bucket),
    borderColor: 'rgba(0,0,0,.25)',
    glyphColor: '#ffffff',
    scale: 0.9,
  });
  entry.marker.content = pin.element;
  entry.clinic.wait_bucket = bucket;
}

async function selectClinic(clinicId) {
  STATE.selectedId = clinicId;

  document.querySelectorAll('.clinic-item').forEach(li => {
    li.classList.toggle('selected', parseInt(li.dataset.id) === clinicId);
  });

  const selected = document.querySelector('.clinic-item.selected');
  if (selected) selected.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

  try {
    const resp = await fetch(`/api/clinics/${clinicId}`);
    const clinic = await resp.json();
    populateDetailPanel(clinic);
    document.getElementById('detail-panel').classList.remove('hidden');
    hideWaitForm();
    hideReviewForm();
    hideInsuranceForm();

    STATE.map.panTo({ lat: clinic.latitude, lng: clinic.longitude });
  } catch (e) {
    console.error('Failed to load clinic detail', e);
  }
}

function populateDetailPanel(clinic) {
  document.getElementById('er-warning').classList.add('hidden');
  document.getElementById('clinic-sections').classList.remove('hidden');
  document.getElementById('detail-website-row').style.display = 'none';

  // street view goes through Flask so the API key stays server-side
  // if Google has no photo for this address, onerror just keeps the wrapper hidden
  const photoWrap = document.getElementById('clinic-photo-wrap');
  const photoImg  = document.getElementById('clinic-photo');
  const svUrl = `/api/streetview?lat=${clinic.latitude}&lon=${clinic.longitude}`;
  photoWrap.classList.add('hidden');
  photoImg.onload  = () => photoWrap.classList.remove('hidden');
  photoImg.onerror = () => photoWrap.classList.add('hidden');
  photoImg.src = svUrl;

  const { primary: dName, atSite } = clinicDisplayName(clinic);
  document.getElementById('detail-name').textContent = dName;
  const typeParts = [clinic.site_type || 'Federally Qualified Health Center'];
  if (atSite) typeParts.push(`Located at: ${atSite}`);
  document.getElementById('detail-type').textContent = typeParts.join(' · ');
  document.getElementById('detail-address').textContent =
    [clinic.address, clinic.city, clinic.state, clinic.zip].filter(Boolean).join(', ');

  const phoneEl  = document.getElementById('detail-phone');
  const phoneRow = document.getElementById('detail-phone-row');
  if (clinic.phone) {
    const digits = clinic.phone.replace(/\D/g, '');
    phoneEl.innerHTML = `<a href="tel:+1${escHtml(digits)}" class="phone-link">${escHtml(clinic.phone)}</a>`;
    phoneRow.style.display = '';
  } else {
    phoneRow.style.display = 'none';
  }

  const hoursRow = document.getElementById('detail-hours-row');
  if (clinic.hours_per_week) {
    document.getElementById('detail-hours').textContent =
      `Approx. ${clinic.hours_per_week} hours/week of operation`;
    hoursRow.style.display = '';
  } else {
    hoursRow.style.display = 'none';
  }

  const dirLink = document.getElementById('detail-directions');
  const dest = `${clinic.latitude},${clinic.longitude}`;
  const origin = STATE.userLat ? `&origin=${STATE.userLat},${STATE.userLon}` : '';
  dirLink.href = `https://www.google.com/maps/dir/?api=1${origin}&destination=${dest}`;

  const badge = document.getElementById('detail-wait-badge');
  badge.textContent = (clinic.is_estimate ? '~ ' : '') + waitLabel(clinic.wait_bucket);
  badge.style.background = waitColor(clinic.wait_bucket);
  badge.classList.toggle('wait-estimated', !!clinic.is_estimate);

  const timeEl = document.getElementById('detail-wait-time');
  if (clinic.is_estimate) {
    timeEl.textContent = 'Estimated based on typical wait times — be first to report!';
  } else if (clinic.wait_reported_at) {
    timeEl.textContent = `Reported ${formatRelativeTime(clinic.wait_reported_at)}`;
  } else {
    timeEl.textContent = '';
  }

  renderRatingCard(clinic.avg_rating, clinic.review_count);
  renderInsurancePlans(clinic.insurance_plans || []);
}

function renderRatingCard(avgRating, reviewCount) {
  const starsEl = document.getElementById('detail-stars');
  const countEl = document.getElementById('detail-review-count');
  if (!reviewCount) {
    starsEl.innerHTML = '<span style="color:#d1d5db">&#9733;&#9733;&#9733;&#9733;&#9733;</span>';
    countEl.textContent = 'No reviews yet — be first!';
  } else {
    const full = Math.round(avgRating);
    starsEl.textContent = '★'.repeat(full) + '☆'.repeat(5 - full);
    countEl.textContent = `${avgRating} / 5  (${reviewCount} review${reviewCount !== 1 ? 's' : ''})`;
  }
}

function updateSidebarRating(clinicId, avgRating, reviewCount) {
  const li = document.querySelector(`.clinic-item[data-id="${clinicId}"]`);
  if (!li) return;
  let span = li.querySelector('.clinic-rating');
  if (!span) {
    span = document.createElement('span');
    span.className = 'clinic-rating';
    li.querySelector('.clinic-item-meta')?.appendChild(span);
  }
  span.innerHTML = `${avgRating}&#9733; (${reviewCount})`;
}

function renderInsurancePlans(communityPlans) {
  const el = document.getElementById('insurance-community');
  el.innerHTML = '';
  if (!communityPlans.length) return;

  const label = document.createElement('div');
  label.className = 'insurance-section-label';
  label.textContent = 'Also reported by community:';
  el.appendChild(label);

  const wrap = document.createElement('div');
  wrap.className = 'insurance-plans';
  communityPlans.forEach(p => {
    const badge = document.createElement('span');
    badge.className = 'plan-badge community';
    badge.textContent = p.plan_name;
    if (p.latest_report) badge.title = `Last reported: ${timeSince(p.latest_report)}`;
    wrap.appendChild(badge);
  });
  el.appendChild(wrap);

  const note = document.createElement('p');
  note.className = 'insurance-note';
  note.textContent = 'Community reports expire after 6 months. Hover a plan to see when it was last reported.';
  el.appendChild(note);
}

function addCommunityPlan(planName) {
  const el = document.getElementById('insurance-community');
  const alreadyShown = Array.from(el.querySelectorAll('.plan-badge'))
    .some(b => b.textContent === planName);
  if (alreadyShown) return;

  let wrap = el.querySelector('.insurance-plans');
  if (!wrap) {
    const label = document.createElement('div');
    label.className = 'insurance-section-label';
    label.textContent = 'Also reported by community:';
    el.appendChild(label);
    wrap = document.createElement('div');
    wrap.className = 'insurance-plans';
    el.appendChild(wrap);
  }
  const badge = document.createElement('span');
  badge.className = 'plan-badge community';
  badge.textContent = planName;
  wrap.appendChild(badge);
}

function closeDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
  STATE.selectedId = null;
  document.querySelectorAll('.clinic-item.selected').forEach(li => li.classList.remove('selected'));
  hideWaitForm();
  hideReviewForm();
  hideInsuranceForm();
}

function showWaitForm() {
  document.getElementById('wait-form').classList.remove('hidden');
  document.getElementById('btn-report-wait').style.display = 'none';
  document.getElementById('wait-form-msg').className = 'form-msg hidden';
}

function hideWaitForm() {
  document.getElementById('wait-form').classList.add('hidden');
  document.getElementById('btn-report-wait').style.display = '';
  document.getElementById('wait-select').value = '';
}

async function submitWaitReport(e) {
  e.preventDefault();
  const bucket = document.getElementById('wait-select').value;
  const msgEl  = document.getElementById('wait-form-msg');

  if (!bucket) {
    msgEl.textContent = 'Please select a wait time.';
    msgEl.className = 'form-msg error';
    return;
  }
  if (!STATE.selectedId) return;

  try {
    const resp = await fetch('/api/wait-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinic_id: STATE.selectedId, wait_bucket: bucket }),
    });
    if (!resp.ok) throw new Error();

    msgEl.textContent = 'Thanks for your report!';
    msgEl.className = 'form-msg success';

    updateMarkerColor(STATE.selectedId, bucket);
    const badge = document.getElementById('detail-wait-badge');
    badge.textContent = waitLabel(bucket);
    badge.style.background = waitColor(bucket);
    badge.classList.remove('wait-estimated');
    document.getElementById('detail-wait-time').textContent = 'Reported just now';

    const li = document.querySelector(`.clinic-item[data-id="${STATE.selectedId}"] .wait-badge`);
    if (li) {
      li.textContent = waitLabel(bucket);
      li.style.background = waitColor(bucket);
    }

    setTimeout(hideWaitForm, 1500);
  } catch {
    msgEl.textContent = 'Something went wrong. Please try again.';
    msgEl.className = 'form-msg error';
  }
}

function initStarInput() {
  document.querySelectorAll('.star-btn').forEach(btn => {
    const val = parseInt(btn.dataset.value);
    btn.addEventListener('mouseover', () => highlightStars(val));
    btn.addEventListener('mouseout',  () => highlightStars(selectedRating));
    btn.addEventListener('click',     () => { selectedRating = val; highlightStars(val); });
  });
}

function highlightStars(count) {
  document.querySelectorAll('.star-btn').forEach((btn, i) => {
    btn.classList.toggle('active', i < count);
  });
}

function showReviewForm() {
  selectedRating = 0;
  highlightStars(0);
  document.getElementById('review-comment').value = '';
  document.getElementById('review-form-msg').className = 'form-msg hidden';
  document.getElementById('review-form').classList.remove('hidden');
  document.getElementById('btn-rate-clinic').style.display = 'none';
}

function hideReviewForm() {
  document.getElementById('review-form').classList.add('hidden');
  document.getElementById('btn-rate-clinic').style.display = '';
  selectedRating = 0;
  highlightStars(0);
}

async function submitReview(e) {
  e.preventDefault();
  const msgEl   = document.getElementById('review-form-msg');
  const comment = document.getElementById('review-comment').value.trim();

  if (!selectedRating) {
    msgEl.textContent = 'Please select a star rating.';
    msgEl.className = 'form-msg error';
    return;
  }
  if (!STATE.selectedId) return;

  try {
    const resp = await fetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinic_id: STATE.selectedId, rating: selectedRating, comment: comment || null }),
    });
    if (!resp.ok) throw new Error();
    const data = await resp.json();

    msgEl.textContent = 'Thanks for your review!';
    msgEl.className = 'form-msg success';

    renderRatingCard(data.avg_rating, data.review_count);
    updateSidebarRating(STATE.selectedId, data.avg_rating, data.review_count);

    setTimeout(hideReviewForm, 1500);
  } catch {
    msgEl.textContent = 'Something went wrong. Please try again.';
    msgEl.className = 'form-msg error';
  }
}

function showInsuranceForm() {
  document.getElementById('insurance-select').value = '';
  document.getElementById('insurance-custom').value = '';
  document.getElementById('insurance-custom').classList.add('hidden');
  document.getElementById('insurance-form-msg').className = 'form-msg hidden';
  document.getElementById('insurance-form').classList.remove('hidden');
  document.getElementById('btn-add-insurance').style.display = 'none';
}

function hideInsuranceForm() {
  document.getElementById('insurance-form').classList.add('hidden');
  document.getElementById('btn-add-insurance').style.display = '';
}

async function submitInsuranceReport(e) {
  e.preventDefault();
  const msgEl  = document.getElementById('insurance-form-msg');
  const select = document.getElementById('insurance-select');
  const custom = document.getElementById('insurance-custom');

  const planName = select.value === 'other' ? custom.value.trim() : select.value;
  if (!planName) {
    msgEl.textContent = 'Please select or enter a plan name.';
    msgEl.className = 'form-msg error';
    return;
  }
  if (!STATE.selectedId) return;

  try {
    const resp = await fetch('/api/insurance-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinic_id: STATE.selectedId, plan_name: planName }),
    });
    if (!resp.ok) throw new Error();

    msgEl.textContent = 'Thanks for the info!';
    msgEl.className = 'form-msg success';

    addCommunityPlan(planName);
    setTimeout(hideInsuranceForm, 1500);
  } catch {
    msgEl.textContent = 'Something went wrong. Please try again.';
    msgEl.className = 'form-msg error';
  }
}

function setSidebarState(msg) {
  const el = document.getElementById('sidebar-state');
  el.innerHTML = `<p>${escHtml(msg)}</p>`;
  el.style.display = '';
}

function hideSidebarState() {
  document.getElementById('sidebar-state').style.display = 'none';
}

function switchTab(tab) {
  if (STATE.activeTab === tab) return;
  STATE.activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.filter-clinics-only').forEach(el => {
    el.style.display = tab === 'clinics' ? '' : 'none';
  });
  document.querySelectorAll('.filter-er-only').forEach(el => {
    el.style.display = tab === 'er' ? '' : 'none';
  });
  resetFilters();
  closeDetailPanel();
  clearMarkers();

  if (tab === 'clinics') {
    document.getElementById('clinic-list').style.display = '';
    document.getElementById('er-list').style.display = 'none';
    if (STATE.clinics.length) {
      renderSidebarList(STATE.clinics);
      renderMapMarkers(STATE.clinics);
      hideSidebarState();
    } else if (STATE.userLat !== null) {
      loadNearbyClinics();
    } else {
      setSidebarState('Click Use My Location or search above to find free clinics near you.');
    }
  } else {
    document.getElementById('clinic-list').style.display = 'none';
    document.getElementById('er-list').style.display = '';
    if (STATE.erData.length) {
      renderEmergencyList(STATE.erData);
      renderEmergencyMarkers(STATE.erData);
      hideSidebarState();
    } else if (STATE.userLat !== null) {
      loadNearbyEmergency();
    } else {
      setSidebarState('Click Use My Location or search above to find emergency care near you.');
    }
  }
}

// hospital pins = red, urgent care = purple. change the hex in renderEmergencyMarkers if needed
async function loadNearbyEmergency() {
  setSidebarState('Searching for emergency care near you...');
  clearMarkers();
  document.getElementById('er-list').innerHTML = '';

  try {
    const url = `/api/emergency/nearby?lat=${STATE.userLat}&lon=${STATE.userLon}&radius=${STATE.radiusKm}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('Server error');
    const items = await resp.json();
    STATE.erData = items;

    if (!items.length) {
      setSidebarState(`No emergency facilities found within ${STATE.radiusKm} km. Try a larger radius.`);
      return;
    }

    hideSidebarState();
    renderEmergencyList(items);
    renderEmergencyMarkers(items);
  } catch {
    setSidebarState('Could not load emergency care data. Check your connection and try again.');
  }
}

function renderEmergencyList(items) {
  const ul = document.getElementById('er-list');
  ul.innerHTML = '';
  items.forEach(item => {
    const li = document.createElement('li');
    li.className = 'clinic-item';
    li.dataset.erId = item.id;
    const distMi = (item.distance_km * 0.621371).toFixed(1);
    const color = item.type === 'Hospital' ? '#dc2626' : '#7c3aed';
    li.innerHTML = `
      <div class="clinic-item-top">
        <span class="clinic-item-name">${escHtml(item.name)}</span>
        <span class="er-type-badge" style="background:${color}">${escHtml(item.type)}</span>
      </div>
      <div class="clinic-item-meta">
        <span>${distMi} mi away</span>
        ${item.city ? `<span>${escHtml(item.city)}${item.state ? ', ' + escHtml(item.state) : ''}</span>` : ''}
        ${item.phone ? `<span>${escHtml(item.phone)}</span>` : ''}
      </div>`;
    li.addEventListener('click', () => selectEmergency(item.id));
    ul.appendChild(li);
  });
}

function renderEmergencyMarkers(items) {
  items.forEach(item => {
    const color = item.type === 'Hospital' ? '#dc2626' : '#7c3aed';
    const pin = new google.maps.marker.PinElement({
      background: color,
      borderColor: 'rgba(0,0,0,.25)',
      glyphColor: '#ffffff',
      scale: 0.9,
    });
    const marker = new google.maps.marker.AdvancedMarkerElement({
      map: STATE.map,
      position: { lat: item.latitude, lng: item.longitude },
      title: item.name,
      content: pin.element,
    });
    marker.addEventListener('gmp-click', () => selectEmergency(item.id));
    STATE.markers.push({ marker, clinic: item });
  });
}

function selectEmergency(erId) {
  STATE.selectedId = erId;
  document.querySelectorAll('.clinic-item').forEach(li => {
    li.classList.toggle('selected', li.dataset.erId == erId);
  });
  const selected = document.querySelector('.clinic-item.selected');
  if (selected) selected.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

  const item = STATE.erData.find(e => e.id === erId);
  if (!item) return;

  populateEmergencyPanel(item);
  document.getElementById('detail-panel').classList.remove('hidden');
  STATE.map.panTo({ lat: item.latitude, lng: item.longitude });
}

function populateEmergencyPanel(item) {
  document.getElementById('clinic-photo-wrap').classList.add('hidden');
  document.getElementById('er-warning').classList.remove('hidden');
  document.getElementById('clinic-sections').classList.add('hidden');

  document.getElementById('detail-name').textContent = item.name;
  document.getElementById('detail-type').textContent =
    item.network ? `${item.type} · ${item.network}` : item.type;
  document.getElementById('detail-address').textContent =
    [item.address, item.city, item.state].filter(Boolean).join(', ') || 'Address not available';

  const phoneEl  = document.getElementById('detail-phone');
  const phoneRow = document.getElementById('detail-phone-row');
  if (item.phone) {
    phoneEl.innerHTML = `<a href="tel:${escHtml(item.phone)}" class="phone-link">${escHtml(item.phone)}</a>`;
    phoneRow.style.display = '';
  } else {
    phoneRow.style.display = 'none';
  }

  document.getElementById('detail-hours-row').style.display = 'none';

  const dirLink = document.getElementById('detail-directions');
  const dest    = `${item.latitude},${item.longitude}`;
  const origin  = STATE.userLat ? `&origin=${STATE.userLat},${STATE.userLon}` : '';
  dirLink.href  = `https://www.google.com/maps/dir/?api=1${origin}&destination=${dest}`;

  const websiteRow = document.getElementById('detail-website-row');
  if (item.website) {
    document.getElementById('detail-website').href = item.website;
    websiteRow.style.display = '';
  } else {
    websiteRow.style.display = 'none';
  }
}
