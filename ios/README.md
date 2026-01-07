# RadarCheck iOS App

Swift source files for the RadarCheck iOS app. This directory contains all the Swift code - you need to create the Xcode project to build it.

## Quick Setup (After Installing Xcode)

1. **Open Xcode** and create a new project:
   - File → New → Project → iOS → App
   - Product Name: `RadarCheck`
   - Team: Your Apple ID
   - Organization Identifier: `com.yourname`
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Click Next, save inside this `ios/` directory

2. **Delete the default files** Xcode created:
   - Delete `ContentView.swift` (we have our own)
   - Delete the default `RadarCheckApp.swift` (we have our own)

3. **Add the source files:**
   - Right-click on the RadarCheck folder in Xcode
   - Select "Add Files to RadarCheck..."
   - Navigate to this `RadarCheck/` folder
   - Select all `.swift` files and the `Assets.xcassets` folder
   - Make sure "Copy items if needed" is **unchecked** (files are already here)
   - Click Add

4. **Run the app:**
   - Select a simulator (e.g., iPhone 15 Pro)
   - Press ⌘R to build and run
   - Make sure the Flask server is running: `python app.py`

## Project Structure

```
RadarCheck/
├── RadarCheckApp.swift       # App entry point
├── Models/
│   ├── Location.swift        # Location data model
│   ├── ModelRun.swift        # Model run data model
│   └── ForecastFrame.swift   # Forecast frame data model
├── Views/
│   ├── ContentView.swift     # Main view with location list
│   └── ForecastView.swift    # Forecast viewer with animation
├── Services/
│   └── APIClient.swift       # Network client
└── Assets.xcassets/          # App icons and images
```

## Configuration

### Local Development
The app automatically uses `http://localhost:5001` in DEBUG builds.

### Physical iPhone Testing
If testing on a real iPhone (not simulator), update the `customBaseURL` in Settings or modify `APIClient.swift`:
```swift
// Find your Mac's IP: ipconfig getifaddr en0
// Then in APIClient.swift, change the baseURL
return "http://YOUR_MAC_IP:5001"
```

### Production
Update the `baseURL` and `apiKey` in the `#else` block of `APIClient.swift` before release builds.
