// ForecastView.swift
// RadarCheck
//
// Main forecast viewing screen with frame slider

import SwiftUI

struct ForecastView: View {
    let location: Location
    
    @StateObject private var apiClient = APIClient.shared
    @State private var runs: [ModelRun] = []
    @State private var selectedRun: ModelRun?
    @State private var frames: [ForecastFrame] = []
    @State private var selectedHour: Int = 1
    @State private var isAnimating = false
    @State private var animationTimer: Timer?
    @State private var isLoading = true
    @State private var errorMessage: String?
    
    var body: some View {
        VStack(spacing: 0) {
            // Run selector
            if runs.count > 1 {
                runPicker
            }
            
            // Main content
            if isLoading {
                Spacer()
                ProgressView("Loading forecast...")
                Spacer()
            } else if let error = errorMessage {
                Spacer()
                errorView(error)
                Spacer()
            } else {
                // Forecast image
                forecastImage
                
                // Time info
                timeInfoBar
                
                // Hour slider
                hourSlider
                
                // Animation controls
                animationControls
            }
        }
        .navigationTitle(location.name)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await loadRuns()
        }
        .onDisappear {
            stopAnimation()
        }
    }
    
    // MARK: - Run Picker
    
    private var runPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(runs) { run in
                    Button {
                        selectedRun = run
                        Task { await loadFrames(for: run) }
                    } label: {
                        Text(run.displayTime)
                            .font(.caption)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(selectedRun?.id == run.id ? Color.blue : Color.gray.opacity(0.2))
                            .foregroundColor(selectedRun?.id == run.id ? .white : .primary)
                            .cornerRadius(16)
                    }
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
        }
        .background(Color(UIColor.systemBackground))
    }
    
    // MARK: - Forecast Image
    
    private var forecastImage: some View {
        GeometryReader { geometry in
            if let runId = selectedRun?.runId,
               let url = apiClient.frameURL(for: location.id, runId: runId, hour: selectedHour) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .empty:
                        ProgressView()
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    case .success(let image):
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    case .failure:
                        VStack {
                            Image(systemName: "photo")
                                .font(.system(size: 48))
                                .foregroundColor(.secondary)
                            Text("Unable to load image")
                                .foregroundColor(.secondary)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    @unknown default:
                        EmptyView()
                    }
                }
            } else {
                VStack {
                    Image(systemName: "cloud.rain")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary)
                    Text("Select a model run")
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
    
    // MARK: - Time Info
    
    private var timeInfoBar: some View {
        Group {
            if selectedHour <= frames.count, !frames.isEmpty {
                let frame = frames[selectedHour - 1]
                HStack {
                    VStack(alignment: .leading) {
                        Text("Valid Time")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(frame.displayFullTime)
                            .font(.subheadline)
                            .fontWeight(.medium)
                    }
                    Spacer()
                    VStack(alignment: .trailing) {
                        Text("Forecast Hour")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text("+\(selectedHour)h")
                            .font(.subheadline)
                            .fontWeight(.medium)
                    }
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(Color(UIColor.secondarySystemBackground))
            }
        }
    }
    
    // MARK: - Hour Slider
    
    private var hourSlider: some View {
        VStack(spacing: 4) {
            Slider(
                value: Binding(
                    get: { Double(selectedHour) },
                    set: { selectedHour = Int($0) }
                ),
                in: 1...Double(max(frames.count, 24)),
                step: 1
            )
            .padding(.horizontal)
            
            HStack {
                Text("+1h")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Text("+\(max(frames.count, 24))h")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal)
        }
        .padding(.vertical, 8)
    }
    
    // MARK: - Animation Controls
    
    private var animationControls: some View {
        HStack(spacing: 32) {
            // Step backward
            Button {
                if selectedHour > 1 {
                    selectedHour -= 1
                }
            } label: {
                Image(systemName: "backward.frame.fill")
                    .font(.title2)
            }
            .disabled(selectedHour <= 1)
            
            // Play/Pause
            Button {
                if isAnimating {
                    stopAnimation()
                } else {
                    startAnimation()
                }
            } label: {
                Image(systemName: isAnimating ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 44))
            }
            
            // Step forward
            Button {
                let maxHour = max(frames.count, 24)
                if selectedHour < maxHour {
                    selectedHour += 1
                }
            } label: {
                Image(systemName: "forward.frame.fill")
                    .font(.title2)
            }
            .disabled(selectedHour >= max(frames.count, 24))
        }
        .padding(.vertical, 16)
    }
    
    // MARK: - Error View
    
    private func errorView(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 48))
                .foregroundColor(.orange)
            Text("Error Loading Forecast")
                .font(.title3)
                .fontWeight(.semibold)
            Text(message)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task { await loadRuns() }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
    
    // MARK: - Data Loading
    
    private func loadRuns() async {
        isLoading = true
        errorMessage = nil
        
        do {
            runs = try await apiClient.fetchRuns(for: location.id)
            if let firstRun = runs.first {
                selectedRun = firstRun
                await loadFrames(for: firstRun)
            }
            isLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
        }
    }
    
    private func loadFrames(for run: ModelRun) async {
        do {
            frames = try await apiClient.fetchValidTimes(for: location.id, runId: run.runId)
            // Reset to hour 1 when switching runs
            selectedHour = 1
        } catch {
            // Keep existing frames if loading fails
            print("Failed to load frames: \(error)")
        }
    }
    
    // MARK: - Animation
    
    private func startAnimation() {
        isAnimating = true
        let maxHour = max(frames.count, 24)
        
        animationTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            Task { @MainActor in
                if selectedHour >= maxHour {
                    selectedHour = 1
                } else {
                    selectedHour += 1
                }
            }
        }
    }
    
    private func stopAnimation() {
        isAnimating = false
        animationTimer?.invalidate()
        animationTimer = nil
    }
}

#Preview {
    NavigationStack {
        ForecastView(location: Location(
            id: "philly",
            name: "Philadelphia",
            initTime: "2026-01-07 03:00:00",
            runId: "run_20260107_03"
        ))
    }
}
