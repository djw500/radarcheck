# RadarCheck iOS App - Design Document

## Executive Summary

This document evaluates the feasibility of building a native iOS app for RadarCheck that maintains an "always ready" local cache of weather model forecasts, updated automatically over WiFi in the background.

**Verdict: Feasible, with some constraints.** iOS provides robust background downloading capabilities, and the data sizes are manageable for mobile devices.

---

## Current Data Analysis

### HRRR Model (Current Implementation)

| Metric | Per Run | Per Location (5 runs) |
|--------|---------|----------------------|
| PNG frames (24 hours) | ~2.8 MB | ~14 MB |
| GRIB files (raw data) | ~9 MB | ~45 MB |
| Metadata | ~10 KB | ~50 KB |

**Key observation:** Pre-rendered PNGs are 3x smaller than raw GRIB data. For mobile, we should download pre-rendered images from a server rather than processing GRIB files on-device.

### NAM 3km Model (Proposed Addition)

The NAM 3km (NAM CONUS Nest) provides:
- 60-hour forecasts (vs HRRR's 18-48 hours)
- Runs every 6 hours (00, 06, 12, 18 UTC)
- Slightly coarser than HRRR but longer range

| Metric | Per Run | Per Location (4 runs/day) |
|--------|---------|---------------------------|
| PNG frames (60 hours) | ~7 MB | ~28 MB |
| Combined with HRRR | - | ~42 MB |

### Total Storage Requirements

| Configuration | Daily Download | On-Device Cache |
|---------------|----------------|-----------------|
| 1 location, HRRR only | ~50 MB | ~15 MB |
| 1 location, HRRR + NAM | ~80 MB | ~45 MB |
| 3 locations, HRRR + NAM | ~240 MB | ~135 MB |

**Monthly data usage (WiFi only):** 1.5-7 GB depending on configuration

---

## iOS Background Download Capabilities

### Available Mechanisms

1. **BGAppRefreshTask** (iOS 13+)
   - System schedules based on user behavior patterns
   - ~30 seconds of execution time
   - Good for checking if new data is available

2. **BGProcessingTask** (iOS 13+)
   - For longer operations (minutes)
   - Runs when device is charging and on WiFi
   - Ideal for downloading multiple model runs

3. **URLSession Background Downloads**
   - Downloads continue even when app is suspended/terminated
   - System manages retries and connectivity
   - **Best choice for our use case**

### Constraints & Considerations

- Background downloads only proceed on WiFi (configurable)
- iOS may delay/throttle based on battery, thermal state, user patterns
- App must handle being launched in background to process completed downloads
- Push notifications can wake app to trigger downloads (requires server infrastructure)

---

## Proposed Architecture

### Option A: Thin Client (Recommended)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   iOS App       │────▶│  Backend Server  │────▶│  NOAA NOMADS    │
│                 │     │  (renders PNGs)  │     │                 │
│  - Downloads    │◀────│  - Caches data   │     │                 │
│    pre-rendered │     │  - Serves API    │     │                 │
│    images       │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

**Pros:**
- Minimal on-device processing
- Smaller downloads (PNGs vs GRIB)
- Server can pre-process during off-peak hours
- Consistent rendering across devices

**Cons:**
- Requires running a backend server
- Server costs (~$5-20/month for small scale)
- Dependency on server availability

### Option B: Fat Client (On-Device Processing)

```
┌─────────────────┐     ┌─────────────────┐
│   iOS App       │────▶│  NOAA NOMADS    │
│                 │     │                 │
│  - Downloads    │     │                 │
│    GRIB files   │     │                 │
│  - Renders maps │     │                 │
│    locally      │     │                 │
└─────────────────┘     └─────────────────┘
```

**Pros:**
- No server dependency
- Direct from NOAA (always available)

**Cons:**
- 3x larger downloads
- Complex GRIB parsing on iOS (limited library support)
- CPU/battery intensive rendering
- Would need to port matplotlib/cartopy rendering to iOS (significant effort)

### Recommendation: Option A (Thin Client)

The current Flask backend already does the heavy lifting. Deploy it as an API server and have the iOS app download pre-rendered images.

---

## Implementation Strategy

### Phase 1: Backend API (1-2 weeks)

Extend current Flask app to serve as an API:

1. **Add API endpoints:**
   - `GET /api/v1/models` - List available models (HRRR, NAM)
   - `GET /api/v1/locations` - List configured locations
   - `GET /api/v1/{model}/{location}/runs` - List available runs
   - `GET /api/v1/{model}/{location}/{run}/manifest.json` - Frame URLs + metadata
   - `GET /api/v1/{model}/{location}/{run}/frames/{hour}.png` - Individual frame

2. **Add NAM 3km support:**
   - Similar to HRRR but different NOMADS URL pattern
   - 60 forecast hours instead of 24
   - Different update schedule (every 6 hours)

3. **Deploy to cloud:**
   - Railway, Fly.io, or DigitalOcean ($5-10/month)
   - Set up cron job to refresh cache hourly

### Phase 2: iOS App MVP (3-4 weeks)

1. **Core data layer:**
   - Swift `URLSession` background download manager
   - Core Data or SQLite for metadata/run tracking
   - File system cache for downloaded PNGs

2. **Background refresh:**
   ```swift
   // Register background tasks
   BGTaskScheduler.shared.register(
       forTaskWithIdentifier: "com.radarcheck.refresh",
       using: nil
   ) { task in
       self.handleAppRefresh(task: task as! BGAppRefreshTask)
   }
   ```

3. **UI components:**
   - Location selector
   - Model run selector (with timestamps)
   - Animated forecast viewer (swipe/slider through hours)
   - Timeline comparison view
   - Settings (locations, models, WiFi-only toggle)

4. **Smart caching:**
   - Keep last N runs per location/model
   - Auto-delete old data
   - Track "freshness" and show stale data indicators

### Phase 3: Enhanced Features (2-3 weeks)

1. **Run evolution view:**
   - Compare how forecast for a specific time evolved across model runs
   - "How has tomorrow 2pm forecast changed over the last 12 hours?"

2. **Push notifications (optional):**
   - Server sends silent push when new run is available
   - App wakes and triggers background download
   - Requires Apple Push Notification setup

3. **Widgets:**
   - iOS home screen widget showing current radar snapshot
   - Uses WidgetKit, refreshes periodically

---

## Technical Implementation Details

### Background Download Manager (Swift)

```swift
class ForecastDownloadManager {
    static let shared = ForecastDownloadManager()

    private lazy var backgroundSession: URLSession = {
        let config = URLSessionConfiguration.background(
            withIdentifier: "com.radarcheck.downloads"
        )
        config.isDiscretionary = true  // Let system optimize timing
        config.allowsCellularAccess = false  // WiFi only
        config.sessionSendsLaunchEvents = true  // Wake app when done
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    func downloadLatestRuns(for location: Location) {
        // Fetch manifest, queue downloads for each frame
    }
}
```

### Data Model

```swift
struct ModelRun: Codable {
    let model: String  // "hrrr" or "nam3km"
    let location: String
    let runId: String
    let initTime: Date
    let frames: [ForecastFrame]
    let downloadedAt: Date?
}

struct ForecastFrame: Codable {
    let hour: Int
    let validTime: Date
    let localPath: String?  // nil if not yet downloaded
    let remoteURL: URL
}
```

### Storage Strategy

```
Documents/
├── forecasts/
│   ├── hrrr/
│   │   └── philly/
│   │       ├── run_20260107_00/
│   │       │   ├── manifest.json
│   │       │   ├── frame_01.png
│   │       │   └── ...
│   │       └── run_20260106_23/
│   └── nam3km/
│       └── philly/
└── metadata.sqlite  // Run index, download status
```

---

## NAM 3km Integration

### NOMADS URL Pattern

```python
# NAM 3km CONUS Nest
url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_nam_conusnest.pl?"
       f"file=nam.t{init_hour}z.conusnest.hiresf{forecast_hour}.tm00.grib2&"
       f"dir=%2Fnam.{date_str}&"
       f"var_REFC=on&"  # Composite reflectivity
       f"leftlon={lon_min}&rightlon={lon_max}&"
       f"toplat={lat_max}&bottomlat={lat_min}")
```

### Key Differences from HRRR

| Aspect | HRRR | NAM 3km |
|--------|------|---------|
| Update frequency | Hourly | Every 6 hours |
| Forecast length | 18-48 hours | 60 hours |
| Resolution | 3 km | 3 km |
| Latency | ~45 min | ~1.5 hours |
| Best for | Next 6-12 hours | 12-48 hour outlook |

---

## Effort Estimates

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Backend API + NAM support | 1-2 weeks | Current codebase |
| Server deployment | 1-2 days | Cloud account |
| iOS app MVP | 3-4 weeks | Backend API |
| Background downloads | Included in MVP | - |
| Timeline/evolution view | 1 week | MVP complete |
| Widgets | 1 week | MVP complete |
| Push notifications | 1 week | Server + Apple Developer |

**Total: 6-9 weeks** for full-featured app

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| iOS background limits | Downloads may be delayed | Use discretionary downloads; user can manually refresh |
| NOAA server downtime | Missing model runs | Cache aggressively; show stale data with warning |
| Server costs | Ongoing expense | Start with free tier; usage-based scaling |
| App Store rejection | Launch delay | Follow Apple guidelines; no private APIs |

---

## Conclusion

Building an "always ready" iOS weather app is **feasible and practical**:

1. **Data sizes are manageable:** ~50-80 MB/day per location over WiFi
2. **iOS supports this pattern:** Background URLSession downloads work well
3. **Architecture is clear:** Thin client with backend API is the right approach
4. **Incremental path:** Start with HRRR, add NAM later

### Recommended Next Steps

1. Add NAM 3km support to current Python backend
2. Deploy backend to cloud with auto-refresh cron
3. Build iOS app MVP with background downloads
4. Iterate on UI/UX based on personal use
