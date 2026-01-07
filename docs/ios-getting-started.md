# iOS Development Getting Started Guide

## What You Need

### Required (Free)
- **Mac** - iOS development requires macOS
- **Xcode** - Apple's IDE, free from the Mac App Store (~12 GB download)
  ```bash
  # Or install via command line
  xcode-select --install  # Just command line tools
  # For full Xcode, use App Store or developer.apple.com
  ```
- **iPhone Simulator** - Included with Xcode, runs on your Mac
- **Apple ID** - Free, lets you run on your personal device for testing

### Required for App Store (Paid)
- **Apple Developer Program** - $99/year
  - Required to publish to App Store
  - Required for push notifications
  - NOT required for personal use on your own devices

### Optional but Recommended
- **Physical iPhone** - For real-world testing (simulator works fine for most development)
- **SF Symbols app** - Free icon browser from Apple

## Project Structure Recommendation

Keep iOS app in this repo as a subdirectory:

```
radarcheck/
├── app.py                 # Existing Flask backend
├── cache_builder.py
├── ios/                   # NEW: iOS app
│   ├── RadarCheck.xcodeproj
│   ├── RadarCheck/
│   │   ├── App/
│   │   ├── Models/
│   │   ├── Views/
│   │   └── Services/
│   └── RadarCheckTests/
├── docs/
└── ...
```

**Why same repo:**
- Backend and app are tightly coupled
- Easier to keep API contracts in sync
- Single place for all project docs

## Creating the Xcode Project

1. **Open Xcode** → File → New → Project

2. **Choose template:**
   - iOS → App
   - Click Next

3. **Configure project:**
   - Product Name: `RadarCheck`
   - Team: Your Apple ID (or "None" for now)
   - Organization Identifier: `com.yourname` (e.g., `com.djweiss`)
   - Interface: **SwiftUI** (modern, recommended for new projects)
   - Language: **Swift**
   - ☐ Include Tests (check this)
   - Click Next

4. **Save location:**
   - Navigate to `/Users/djweiss/github/radarcheck`
   - Create folder named `ios`
   - Save inside `ios/`

## Development Workflow

### Running in Simulator

1. Open `ios/RadarCheck.xcodeproj` in Xcode
2. Select a simulator from the dropdown (e.g., "iPhone 15 Pro")
3. Click the Play button (or ⌘R)
4. App builds and launches in simulator

### Running on Your iPhone (Free)

1. Connect iPhone via USB
2. In Xcode: Select your iPhone from the device dropdown
3. First time setup:
   - Xcode prompts you to trust the device
   - On iPhone: Settings → General → VPN & Device Management → Trust your developer certificate
4. Click Play (⌘R)

**Note:** Free provisioning expires after 7 days. Just re-run from Xcode to refresh.

### Using SwiftUI Previews

SwiftUI has live previews - you see UI changes instantly without running the app:

```swift
struct ForecastView: View {
    var body: some View {
        Text("Hello, Weather!")
    }
}

#Preview {
    ForecastView()
}
```

The preview canvas shows on the right side of Xcode. Changes appear in real-time.

## Key Concepts for iOS Development

### SwiftUI Basics

SwiftUI is declarative - you describe what the UI should look like:

```swift
struct ContentView: View {
    @State private var selectedHour = 1

    var body: some View {
        VStack {
            Text("Forecast Hour: \(selectedHour)")
                .font(.headline)

            Slider(value: Binding(
                get: { Double(selectedHour) },
                set: { selectedHour = Int($0) }
            ), in: 1...24, step: 1)

            AsyncImage(url: URL(string: "http://localhost:5001/frame/philly/latest/\(selectedHour)"))
                .frame(maxWidth: .infinity)
        }
        .padding()
    }
}
```

### Networking

```swift
// Simple GET request
let url = URL(string: "http://localhost:5001/api/locations")!
let (data, _) = try await URLSession.shared.data(from: url)
let locations = try JSONDecoder().decode([Location].self, from: data)
```

### Project Organization

```
RadarCheck/
├── RadarCheckApp.swift      # App entry point
├── ContentView.swift        # Main view
├── Models/
│   ├── Location.swift       # Data models
│   ├── ModelRun.swift
│   └── ForecastFrame.swift
├── Views/
│   ├── LocationListView.swift
│   ├── ForecastView.swift
│   └── TimelineView.swift
├── Services/
│   ├── APIClient.swift      # Network requests
│   └── DownloadManager.swift # Background downloads
└── Assets.xcassets/         # Images, colors, app icon
```

## Testing During Development

### Simulator Limitations
- ✅ Network requests work
- ✅ UI testing
- ✅ Core Data / local storage
- ❌ Background downloads (limited support)
- ❌ Push notifications
- ❌ Real-world performance

### Testing Background Downloads
Background downloads need a real device. The simulator doesn't fully simulate iOS background behavior.

**Workflow:**
1. Develop UI and networking in simulator
2. Test background downloads on real iPhone
3. Use Xcode's "Simulate Background Fetch" for basic testing:
   - Debug → Simulate Background Fetch

## Connecting to Your Local Backend

### From Simulator
The simulator runs on your Mac, so `localhost` works:
```swift
let baseURL = "http://localhost:5001"
```

### From Physical iPhone
Your iPhone needs to reach your Mac over the local network:

1. Find your Mac's local IP:
   ```bash
   ipconfig getifaddr en0
   # Example: 192.168.4.104
   ```

2. Use that IP in your app:
   ```swift
   let baseURL = "http://192.168.4.104:5001"
   ```

3. Make sure Flask binds to all interfaces (it already does with `host="0.0.0.0"`):
   ```python
   app.run(host="0.0.0.0", port=5001)
   ```

### For Production
Point to your deployed backend:
```swift
#if DEBUG
let baseURL = "http://localhost:5001"
#else
let baseURL = "https://radarcheck.yourdomain.com"
#endif
```

## Learning Resources

### Official Apple Resources (Recommended)
- **[SwiftUI Tutorials](https://developer.apple.com/tutorials/swiftui)** - Interactive, takes ~4 hours
- **[Swift Language Guide](https://docs.swift.org/swift-book/)** - Reference
- **[Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)** - Design patterns

### Video Tutorials
- **Stanford CS193p** - Free Stanford course on SwiftUI (YouTube)
- **Sean Allen** (YouTube) - Practical Swift/SwiftUI tutorials
- **Paul Hudson / Hacking with Swift** - Extensive free tutorials

### For This Project Specifically
- **URLSession Background Downloads**: Search "URLSession background download tutorial"
- **AsyncImage**: Built-in SwiftUI component for loading remote images

## Quick Start Checklist

- [ ] Install Xcode from App Store
- [ ] Create Apple ID (if you don't have one)
- [ ] Open Xcode, let it install additional components
- [ ] Create new project in `radarcheck/ios/`
- [ ] Run "Hello World" in simulator
- [ ] Try the SwiftUI tutorials (first few sections)
- [ ] Build a simple view that loads an image from your Flask backend

## First Milestone Suggestion

**Goal:** Display a single forecast frame from your running Flask backend.

```swift
import SwiftUI

struct ContentView: View {
    var body: some View {
        VStack {
            Text("Philadelphia Forecast")
                .font(.title)

            AsyncImage(url: URL(string: "http://localhost:5001/frame/philly/latest/1")) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            } placeholder: {
                ProgressView()
            }
        }
        .padding()
    }
}
```

This proves the full stack works: Flask → Network → iOS display.
