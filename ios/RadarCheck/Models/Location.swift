// Location.swift
// RadarCheck
//
// Data model for weather forecast locations

import Foundation

/// Represents a geographic location with available forecast data
struct Location: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let initTime: String
    let runId: String
    
    enum CodingKeys: String, CodingKey {
        case id
        case name
        case initTime = "init_time"
        case runId = "run_id"
    }
}
