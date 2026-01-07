// APIClient.swift
// RadarCheck
//
// Network client for the RadarCheck backend API

import Foundation

/// Errors that can occur during API requests
enum APIError: Error, LocalizedError {
    case invalidURL
    case networkError(Error)
    case invalidResponse
    case httpError(Int)
    case decodingError(Error)
    case unauthorized
    
    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .invalidResponse:
            return "Invalid response from server"
        case .httpError(let code):
            return "HTTP error: \(code)"
        case .decodingError(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        case .unauthorized:
            return "Unauthorized - check API key"
        }
    }
}

/// Client for communicating with the RadarCheck backend
@MainActor
class APIClient: ObservableObject {
    static let shared = APIClient()
    
    // MARK: - Configuration
    
    #if DEBUG
    /// Base URL for API requests - uses localhost in debug builds
    private var baseURL: String {
        // Check if we should use a custom URL (e.g., for physical device testing)
        if let customURL = UserDefaults.standard.string(forKey: "customBaseURL"), !customURL.isEmpty {
            return customURL
        }
        return "http://localhost:5001"
    }
    
    /// API key - not needed in development
    private var apiKey: String? { nil }
    #else
    /// Base URL for API requests - production server
    private var baseURL: String {
        "https://radarcheck.fly.dev"
    }
    
    /// API key for production
    private var apiKey: String? {
        // TODO: Replace with your actual production API key
        return "YOUR_PRODUCTION_API_KEY"
    }
    #endif
    
    // MARK: - State
    
    @Published var isLoading = false
    @Published var lastError: APIError?
    
    // MARK: - Private Helpers
    
    private func createRequest(for endpoint: String) throws -> URLRequest {
        guard let url = URL(string: "\(baseURL)\(endpoint)") else {
            throw APIError.invalidURL
        }
        
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        
        if let key = apiKey {
            request.setValue(key, forHTTPHeaderField: "X-API-Key")
        }
        
        return request
    }
    
    private func performRequest<T: Decodable>(_ request: URLRequest) async throws -> T {
        isLoading = true
        defer { isLoading = false }
        
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            let apiError = APIError.networkError(error)
            lastError = apiError
            throw apiError
        }
        
        guard let httpResponse = response as? HTTPURLResponse else {
            let apiError = APIError.invalidResponse
            lastError = apiError
            throw apiError
        }
        
        if httpResponse.statusCode == 401 {
            let apiError = APIError.unauthorized
            lastError = apiError
            throw apiError
        }
        
        guard (200...299).contains(httpResponse.statusCode) else {
            let apiError = APIError.httpError(httpResponse.statusCode)
            lastError = apiError
            throw apiError
        }
        
        do {
            let decoded = try JSONDecoder().decode(T.self, from: data)
            lastError = nil
            return decoded
        } catch {
            let apiError = APIError.decodingError(error)
            lastError = apiError
            throw apiError
        }
    }
    
    // MARK: - Public API
    
    /// Fetch all available locations
    func fetchLocations() async throws -> [Location] {
        let request = try createRequest(for: "/api/locations")
        return try await performRequest(request)
    }
    
    /// Fetch all runs for a location
    func fetchRuns(for locationId: String) async throws -> [ModelRun] {
        let request = try createRequest(for: "/api/runs/\(locationId)")
        return try await performRequest(request)
    }
    
    /// Fetch valid times for a specific run
    func fetchValidTimes(for locationId: String, runId: String) async throws -> [ForecastFrame] {
        let request = try createRequest(for: "/api/valid_times/\(locationId)/\(runId)")
        return try await performRequest(request)
    }
    
    /// Get the URL for a forecast frame image
    func frameURL(for locationId: String, runId: String, hour: Int) -> URL? {
        URL(string: "\(baseURL)/frame/\(locationId)/\(runId)/\(hour)")
    }
    
    /// Get the URL for the latest frame
    func latestFrameURL(for locationId: String, hour: Int) -> URL? {
        frameURL(for: locationId, runId: "latest", hour: hour)
    }
}
