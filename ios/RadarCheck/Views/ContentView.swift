// ContentView.swift
// RadarCheck
//
// Main content view - shows locations list or forecast

import SwiftUI

struct ContentView: View {
    @StateObject private var apiClient = APIClient.shared
    @State private var locations: [Location] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    
    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Loading locations...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = errorMessage {
                    VStack(spacing: 16) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 48))
                            .foregroundColor(.orange)
                        Text("Unable to Load")
                            .font(.title2)
                            .fontWeight(.semibold)
                        Text(error)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                        Button("Retry") {
                            Task { await loadLocations() }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .padding()
                } else if locations.isEmpty {
                    VStack(spacing: 16) {
                        Image(systemName: "cloud.rain")
                            .font(.system(size: 48))
                            .foregroundColor(.secondary)
                        Text("No Forecasts Available")
                            .font(.title2)
                            .fontWeight(.semibold)
                        Text("The server has no cached forecast data yet.")
                            .foregroundColor(.secondary)
                        Button("Refresh") {
                            Task { await loadLocations() }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .padding()
                } else {
                    List(locations) { location in
                        NavigationLink(value: location) {
                            LocationRow(location: location)
                        }
                    }
                    .refreshable {
                        await loadLocations()
                    }
                }
            }
            .navigationTitle("RadarCheck")
            .navigationDestination(for: Location.self) { location in
                ForecastView(location: location)
            }
        }
        .task {
            await loadLocations()
        }
    }
    
    private func loadLocations() async {
        isLoading = true
        errorMessage = nil
        
        do {
            locations = try await apiClient.fetchLocations()
            isLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
        }
    }
}

// MARK: - Location Row

struct LocationRow: View {
    let location: Location
    
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(location.name)
                .font(.headline)
            Text("Last updated: \(formattedTime)")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 4)
    }
    
    private var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        
        guard let date = formatter.date(from: location.initTime) else {
            return location.initTime
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateFormat = "MMM d, h:mm a"
        displayFormatter.timeZone = TimeZone.current
        
        return displayFormatter.string(from: date)
    }
}

#Preview {
    ContentView()
}
