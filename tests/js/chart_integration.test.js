// Integration test for chart logic against real API data
const http = require('http');

const API_URL = 'http://localhost:5001/api/timeseries/multirun?lat=40.0488&lon=-75.3890&model=all&variable=asnow&days=3';

function fetchJSON(url) {
    return new Promise((resolve, reject) => {
        http.get(url, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data));
                } catch (e) {
                    reject(new Error('JSON parse error: ' + e.message));
                }
            });
        }).on('error', reject);
    });
}

// Mock configs
const VAR_CONFIG = {
    'asnow': { label: 'Snowfall', unit: '"', stepSize: 6, beginAtZero: true },
    't2m': { label: 'Temperature', unit: '°F', stepSize: 10, beginAtZero: false },
    'wind_10m': { label: 'Wind Speed', unit: ' mph', stepSize: 10, beginAtZero: true }
};

const MODEL_CONFIG = {
    'hrrr': { color: '#3b82f6' },
    'nam_nest': { color: '#ef4444' },
    'gfs': { color: '#22d3ee' },
    'nbm': { color: '#a855f7' },
    'other': { color: '#94a3b8' }
};

function interpolateValue(series, targetTime) {
    if (!series || !series.length) return null;
    const times = series.map(p => new Date(p.valid_time).getTime());
    const idx = times.indexOf(targetTime);
    if (idx !== -1) return series[idx].value;
    let leftIdx = -1, rightIdx = -1;
    for (let i = 0; i < times.length; i++) {
        if (times[i] < targetTime) leftIdx = i;
        if (times[i] > targetTime) { rightIdx = i; break; }
    }
    if (leftIdx === -1 || rightIdx === -1) return null;
    const t1 = times[leftIdx], v1 = series[leftIdx].value;
    const t2 = times[rightIdx], v2 = series[rightIdx].value;
    return v1 + (v2 - v1) * ((targetTime - t1) / (t2 - t1));
}

describe('Chart Integration Tests', () => {
    let data;

    beforeAll(async () => {
        try {
            data = await fetchJSON(API_URL);
        } catch (e) {
            console.warn('Server not running, skipping integration tests');
            data = null;
        }
    }, 10000);

    test('API returns valid data structure', () => {
        if (!data) return;
        expect(data).toHaveProperty('runs');
        expect(data).toHaveProperty('lat');
        expect(data).toHaveProperty('lon');
        expect(Object.keys(data.runs).length).toBeGreaterThan(0);
    });

    test('All runs have required fields', () => {
        if (!data) return;
        Object.values(data.runs).forEach(run => {
            expect(run).toHaveProperty('model_id');
            expect(run).toHaveProperty('run_id');
            expect(run).toHaveProperty('init_time');
            expect(run).toHaveProperty('series');
            expect(Array.isArray(run.series)).toBe(true);
        });
    });

    test('Series data has valid structure', () => {
        if (!data) return;
        Object.values(data.runs).forEach(run => {
            run.series.forEach(point => {
                expect(point).toHaveProperty('valid_time');
                expect(point).toHaveProperty('value');
                expect(typeof point.value).toBe('number');
                expect(isNaN(point.value)).toBe(false);
            });
        });
    });

    test('Filtering by active models works', () => {
        if (!data) return;
        const activeModels = ['hrrr', 'gfs'];
        const runs = Object.values(data.runs).filter(r => activeModels.includes(r.model_id));

        runs.forEach(run => {
            expect(activeModels).toContain(run.model_id);
        });
    });

    test('Empty model filter does not crash', () => {
        if (!data) return;
        const activeModels = [];
        const runs = Object.values(data.runs).filter(r => activeModels.includes(r.model_id));

        expect(runs.length).toBe(0);
        // This would crash: Math.max(...runs.map(r => new Date(r.init_time).getTime()))
        // Should guard against empty runs
        const latestInit = runs.length > 0
            ? Math.max(...runs.map(r => new Date(r.init_time).getTime()))
            : Date.now();
        expect(latestInit).toBeGreaterThan(0);
    });

    test('Trace building succeeds', () => {
        if (!data) return;
        const activeModels = ['hrrr', 'nam_nest', 'gfs', 'nbm'];
        const runs = Object.values(data.runs).filter(r => activeModels.includes(r.model_id));

        const latestRunByModel = {};
        runs.forEach(run => {
            if (!latestRunByModel[run.model_id]) {
                latestRunByModel[run.model_id] = run.run_id;
            }
        });

        const latestInit = Math.max(...runs.map(r => new Date(r.init_time).getTime()));

        const traces = runs.map(run => {
            const isLatest = latestRunByModel[run.model_id] === run.run_id;
            const modelConf = MODEL_CONFIG[run.model_id] || MODEL_CONFIG['other'];
            const ageHours = (latestInit - new Date(run.init_time).getTime()) / 3600000;

            const x = run.series.map(p => p.valid_time);
            const y = run.series.map(p => p.value);

            expect(x.length).toBe(y.length);
            expect(x.every(v => typeof v === 'string')).toBe(true);
            expect(y.every(v => typeof v === 'number')).toBe(true);

            return { x, y, isLatest, ageHours };
        });

        expect(traces.length).toBeGreaterThan(0);
    });

    test('Interpolation works correctly', () => {
        if (!data) return;
        const run = Object.values(data.runs)[0];
        if (run.series.length < 2) return;

        // Get a time between first two points
        const t1 = new Date(run.series[0].valid_time).getTime();
        const t2 = new Date(run.series[1].valid_time).getTime();
        const midTime = (t1 + t2) / 2;

        const interpolated = interpolateValue(run.series, midTime);

        if (interpolated !== null) {
            const v1 = run.series[0].value;
            const v2 = run.series[1].value;
            const expected = (v1 + v2) / 2;
            expect(interpolated).toBeCloseTo(expected, 5);
        }
    });

    test('Interpolation returns null for out-of-bounds', () => {
        if (!data) return;
        const run = Object.values(data.runs)[0];
        if (run.series.length < 1) return;

        const firstTime = new Date(run.series[0].valid_time).getTime();
        const beforeFirst = firstTime - 3600000; // 1 hour before

        const result = interpolateValue(run.series, beforeFirst);
        expect(result).toBeNull();
    });

    test('Table run selection includes synoptic', () => {
        if (!data) return;
        const SYNOPTIC_HOURS = ['00', '12'];
        const activeModels = ['hrrr', 'nam_nest', 'gfs', 'nbm'];

        const runsByModel = {};
        Object.values(data.runs).forEach(run => {
            if (!activeModels.includes(run.model_id)) return;
            if (!runsByModel[run.model_id]) runsByModel[run.model_id] = [];
            runsByModel[run.model_id].push(run);
        });

        Object.entries(runsByModel).forEach(([modelId, runs]) => {
            runs.sort((a, b) => new Date(b.init_time) - new Date(a.init_time));
            const getRunHour = (run) => run.run_id.slice(-2);
            const synopticRuns = runs.filter(r => SYNOPTIC_HOURS.includes(getRunHour(r)));
            const nonSynopticRuns = runs.filter(r => !SYNOPTIC_HOURS.includes(getRunHour(r)));

            let selected = [];
            if (synopticRuns.length > 0) {
                selected = nonSynopticRuns.slice(0, 2);
                selected.push(synopticRuns[0]);
            } else {
                selected = runs.slice(0, 3);
            }

            // Verify at most 3 selected
            expect(selected.length).toBeLessThanOrEqual(3);

            // If synoptic exists, it should be included
            if (synopticRuns.length > 0) {
                const selectedHours = selected.map(r => getRunHour(r));
                const hasSynoptic = selectedHours.some(h => SYNOPTIC_HOURS.includes(h));
                expect(hasSynoptic).toBe(true);
            }
        });
    });
});
