
const { describe, expect, test, beforeEach } = require('@jest/globals');

// We'll extract the logic from snow.html into functions we can test here.
// Since we can't easily import from the HTML template, I'll copy the critical logic 
// into this test file to verify its correctness.

// --- LOGIC UNDER TEST (Copied/Adapted from snow.html) ---

// Helper to interpolate value at a specific time
const interpolate = (series, targetTime) => {
    if (!series || !series.length) return null;
    
    // Sort just in case (though API should provide sorted)
    // series.sort((a,b) => new Date(a.valid_time) - new Date(b.valid_time));
    
    // Convert series times to timestamps
    const times = series.map(p => new Date(p.valid_time).getTime());
    
    // Exact match?
    const idx = times.indexOf(targetTime);
    if (idx !== -1) return series[idx].value;
    
    // Find left and right neighbors
    let leftIdx = -1;
    let rightIdx = -1;
    for (let i = 0; i < times.length; i++) {
        if (times[i] < targetTime) leftIdx = i;
        if (times[i] > targetTime) {
            rightIdx = i;
            break;
        }
    }
    
    // Out of bounds (Extrapolation check)
    if (leftIdx === -1 || rightIdx === -1) return null; 
    
    // Linear interp
    const t1 = times[leftIdx];
    const v1 = series[leftIdx].value;
    const t2 = times[rightIdx];
    const v2 = series[rightIdx].value;
    
    const fraction = (targetTime - t1) / (t2 - t1);
    return v1 + (v2 - v1) * fraction;
};

// Resampling logic
const resampleSeries = (series, startMs, endMs, stepMs = 3600000) => {
    const resampled = [];
    for (let t = startMs; t <= endMs; t += stepMs) {
        resampled.push({
            x: t,
            y: interpolate(series, t)
        });
    }
    return resampled;
};

// --- TESTS ---

describe('Snow Chart Logic', () => {

    describe('Interpolation', () => {
        const series = [
            { valid_time: '2024-01-01T12:00:00Z', value: 10 },
            { valid_time: '2024-01-01T15:00:00Z', value: 25 }, // +5 per hour
            { valid_time: '2024-01-01T18:00:00Z', value: 40 }
        ];

        test('returns exact value for existing points', () => {
            const t = new Date('2024-01-01T12:00:00Z').getTime();
            expect(interpolate(series, t)).toBe(10);
        });

        test('linearly interpolates between points', () => {
            // 13:00 is 1/3 between 12:00 and 15:00. Value should be 10 + (15/3)*1 = 15
            const t = new Date('2024-01-01T13:00:00Z').getTime();
            expect(interpolate(series, t)).toBeCloseTo(15);
            
            // 14:00 should be 20
            const t2 = new Date('2024-01-01T14:00:00Z').getTime();
            expect(interpolate(series, t2)).toBeCloseTo(20);
        });

        test('returns null for times before start (No Extrapolation)', () => {
            const t = new Date('2024-01-01T11:00:00Z').getTime();
            expect(interpolate(series, t)).toBeNull();
        });

        test('returns null for times after end (No Extrapolation)', () => {
            const t = new Date('2024-01-01T19:00:00Z').getTime();
            expect(interpolate(series, t)).toBeNull();
        });
    });

    describe('Resampling', () => {
        const series = [
            { valid_time: '2024-01-01T12:00:00Z', value: 0 },
            { valid_time: '2024-01-01T14:00:00Z', value: 10 }
        ];
        const start = new Date('2024-01-01T11:00:00Z').getTime();
        const end = new Date('2024-01-01T15:00:00Z').getTime();

        test('generates hourly points covering the window', () => {
            const res = resampleSeries(series, start, end);
            
            // Should have 11:00, 12:00, 13:00, 14:00, 15:00 (5 points)
            expect(res.length).toBe(5);
            
            // 11:00 (before start) -> null
            expect(res[0].y).toBeNull();
            
            // 12:00 (exact) -> 0
            expect(res[1].y).toBe(0);
            
            // 13:00 (midpoint) -> 5
            expect(res[2].y).toBe(5);
            
            // 14:00 (exact) -> 10
            expect(res[3].y).toBe(10);
            
            // 15:00 (after end) -> null
            expect(res[4].y).toBeNull();
        });
    });
});
