// ForecastFrame.swift
// RadarCheck
//
// Data model for individual forecast frames

import Foundation

/// Represents a single forecast frame (one hour of the forecast)
struct ForecastFrame: Codable, Identifiable, Hashable {
    var id: Int { forecastHour }
    
    let forecastHour: Int
    let validTime: String
    let framePath: String
    
    enum CodingKeys: String, CodingKey {
        case forecastHour = "forecast_hour"
        case validTime = "valid_time"
        case framePath = "frame_path"
    }
    
    /// Human-readable valid time
    var displayValidTime: String {
        // Parse the valid_time (e.g., "2026-01-07 04:00:00")
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        
        guard let date = formatter.date(from: validTime) else {
            return validTime
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateFormat = "h:mm a"
        displayFormatter.timeZone = TimeZone.current
        
        return displayFormatter.string(from: date)
    }
    
    /// Full date and time display
    var displayFullTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        
        guard let date = formatter.date(from: validTime) else {
            return validTime
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateFormat = "EEE, MMM d 'at' h:mm a"
        displayFormatter.timeZone = TimeZone.current
        
        return displayFormatter.string(from: date)
    }
}
