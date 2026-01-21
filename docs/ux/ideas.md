# UX Improvement Ideas for Radarcheck

## Location Input Improvements

### Current State
- Manual lat/lon input fields
- Browser geolocation button
- IP-based approximate location fallback

### Proposed Improvements

#### 1. Smart Location Autocomplete
- **Type ahead search**: As user types "Phil", show "Philadelphia, PA", "Phoenix, AZ", etc.
- **Free geocoding options**:
  - [Nominatim](https://nominatim.org/) (OpenStreetMap, free, rate-limited)
  - [Photon](https://photon.komoot.io/) (OSM-based, no rate limits)
  - [US Census Geocoder](https://geocoding.geo.census.gov/) (US-only, free, no limits)
- **Implementation**: Add `/api/geocode?q=...` proxy endpoint to avoid CORS

#### 2. Recent Locations
- Store last 5 locations in localStorage
- Show as quick-select chips below the search bar
- Include location name + coordinates
- Example: `[Philadelphia] [New York] [My Home]`

#### 3. Saved/Favorite Locations
- "Star" button to save a location permanently
- Name custom locations (e.g., "Cabin", "Office")
- Stored in localStorage with optional sync (future)

#### 4. Map Picker
- Button to open Leaflet modal map
- Click anywhere to select coordinates
- Show current selection marker
- "Use this location" button to confirm

#### 5. ZIP Code Input
- Detect 5-digit ZIP codes automatically
- Geocode via US Census API
- Example: User types "19103" → Philadelphia, PA (39.95, -75.17)

#### 6. Natural Language Input
- "my location" → trigger geolocation
- "boston" → geocode search
- "40.05, -75.39" → direct coordinates
- Parse input to determine intent

---

## Table Display Improvements

### 1. Local Time Display
- Convert UTC to user's browser timezone automatically
- Format: "Tue 3pm", "Wed 9am" (friendly, compact)
- Option to show UTC for meteorologists
- Timezone selector for viewing other locations' local time

### 2. Variable Grouping
- **Primary variables** (always visible):
  - Total Precipitation
  - Total Snow
  - Temperature
- **Secondary variables** (collapsible or tabs):
  - Wind Speed, Gusts
  - Humidity, Dew Point
  - Visibility
  - CAPE (severe weather)

### 3. Model Comparison Highlighting
- **Agreement indicator**: Green highlight when models agree (within 10%)
- **Disagreement indicator**: Yellow/red when models differ significantly
- **Confidence score**: Based on model spread (tighter = higher confidence)

### 4. Color Coding
- **Temperature**: Blue (-20°F) → White (32°F) → Orange (70°F) → Red (100°F)
- **Precipitation**: White (0) → Light Blue (0.1") → Blue (0.5") → Purple (2"+)
- **Snow**: White (0) → Light Purple (1") → Purple (6") → Deep Purple (12"+)
- **Wind**: Green (0-10) → Yellow (20) → Orange (30) → Red (50+)

### 5. Forecast Summary Cards
- Above the table, show summary boxes:
  - "Total Precip: 0.5-0.8 in (models disagree)"
  - "Max Snow: 6-10 in (high uncertainty)"
  - "Low Temp: 22°F (models agree)"
- Clickable to filter table to that variable

### 6. Time Period Toggles
- "Next 24h" | "Next 3 days" | "Next 7 days"
- Adjusts visible rows
- For GFS: show hourly for first 24h, 3-hourly after

---

## Mobile UX

### 1. Compact Table Mode
- Single variable at a time (swipe to change)
- Larger touch targets
- Horizontal scroll for time axis

### 2. Bottom Sheet for Details
- Tap a cell to see details in bottom sheet
- Show: exact value, valid time (UTC + local), model init time, uncertainty

### 3. Progressive Web App (PWA)
- Add to home screen
- Offline support for last-viewed location
- Push notifications for significant forecast changes (future)

---

## Data Freshness and Trust

### 1. Model Freshness Indicators
- Show how old each model's data is
- "HRRR (2h ago)" vs "GFS (6h ago)"
- Color: Green (<2h), Yellow (2-6h), Red (>6h)

### 2. Data Source Attribution
- Footer: "Data from NOAA NOMADS. Models: HRRR, NAM, GFS."
- Link to model documentation

### 3. Explainer Page Improvements
- Current `/explainer` is good
- Add: "Why do models disagree?" section
- Add: "Which model should I trust?" guidance

---

## Advanced Features (Future)

### 1. Alerts and Notifications
- "Notify me if snow forecast exceeds 6 inches"
- Email or push notification
- Requires user accounts (complexity)

### 2. Forecast Trend Graph
- Line chart showing how forecast has evolved
- X-axis: model run time (last 48h)
- Y-axis: forecasted value (e.g., snow total)
- See if models are trending up or down

### 3. Ensemble Visualization
- For GEFS: show spread of 31 ensemble members
- Box plot or violin plot per forecast hour
- Indicates uncertainty range

### 4. Custom Variable Combinations
- "Show me precip type: rain vs snow"
- "Show wind chill (derived from temp + wind)"
- Requires derived variable calculations

---

## Implementation Priority

### Phase 1 (MVP)
1. Location autocomplete (Nominatim)
2. Recent locations in localStorage
3. Local time display
4. Basic color coding

### Phase 2 (Polish)
1. ZIP code detection
2. Model freshness indicators
3. Compact mobile layout
4. Time period toggles

### Phase 3 (Advanced)
1. Map picker modal
2. Forecast trend graph
3. PWA support
4. Saved/favorite locations

---

## Technical Notes

### Geocoding API Options

| Service | Cost | Rate Limit | US Coverage | Notes |
|---------|------|------------|-------------|-------|
| Nominatim | Free | 1 req/sec | Good | Must cache, attribute OSM |
| Photon | Free | None | Good | Hosted by Komoot |
| US Census | Free | None | US only | Official, includes ZIP |
| Mapbox | Freemium | 100k/mo free | Excellent | Best UX, requires key |

### Timezone Detection

```javascript
// Get user's timezone
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
// e.g., "America/New_York"

// Convert UTC to local
const localTime = new Date(utcISOString).toLocaleString('en-US', {
  timeZone: tz,
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit'
});
```

### localStorage Schema

```javascript
// Recent locations
localStorage.setItem('radarcheck_recent', JSON.stringify([
  { name: 'Philadelphia, PA', lat: 39.95, lon: -75.17, timestamp: 1706000000 },
  { name: 'New York, NY', lat: 40.71, lon: -74.01, timestamp: 1705900000 },
]));

// Saved locations
localStorage.setItem('radarcheck_saved', JSON.stringify([
  { name: 'Home', lat: 40.05, lon: -75.39, icon: 'home' },
  { name: 'Office', lat: 39.95, lon: -75.17, icon: 'briefcase' },
]));

// User preferences
localStorage.setItem('radarcheck_prefs', JSON.stringify({
  timezone: 'America/New_York',  // or 'auto'
  units: 'imperial',  // or 'metric'
  defaultModel: 'hrrr',
}));
```
