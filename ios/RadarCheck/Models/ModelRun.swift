// ModelRun.swift
// RadarCheck
//
// Data model for HRRR model runs

import Foundation

/// Represents a single HRRR model run
struct ModelRun: Codable, Identifiable, Hashable {
    var id: String { runId }
    
    let runId: String
    let initTime: String
    let dateStr: String
    let initHour: String
    
    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case initTime = "init_time"
        case dateStr = "date_str"
        case initHour = "init_hour"
    }
    
    /// Human-readable description of the run time
    var displayTime: String {
        // Parse the init_time (e.g., "2026-01-07 03:00:00")
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        
        guard let date = formatter.date(from: initTime) else {
            return initTime
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateFormat = "MMM d, h:mm a"
        displayFormatter.timeZone = TimeZone.current
        
        return displayFormatter.string(from: date)
    }
}
