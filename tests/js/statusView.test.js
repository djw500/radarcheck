/**
 * @jest-environment jsdom
 */

// Mock fetch
global.fetch = jest.fn();

// Mock console.error to keep output clean
console.error = jest.fn();

document.body.innerHTML = `
<div id="statusGrid"></div>
<div id="diskUsage"></div>
<div id="logsContainer"></div>
`;

// We'll load the script under test later, or mock its dependencies.
// Since we are writing the test first, we define the expected interface.

describe('Status View', () => {
    let statusView;

    beforeEach(() => {
        // Reset mocks
        fetch.mockClear();
        document.getElementById('statusGrid').innerHTML = '';
        document.getElementById('diskUsage').innerHTML = '';
        
        // Dynamic import or require if module system allows, 
        // otherwise we might need to paste code or setup JSDOM to load script.
        // For simplicity in this env, we'll assume we can require the module 
        // if we export it for testing.
    });

    test('fetches status data on init', async () => {
        const mockData = {
            cache_status: {},
            disk_usage: { total: 0 },
            timestamp: "2026-01-24T12:00:00Z"
        };
        fetch.mockResolvedValueOnce({
            ok: true,
            json: async () => mockData
        });

        // We'll implement initStatusPage in statusView.js
        const { initStatusPage } = require('../../static/js/statusView.js');
        await initStatusPage();

        expect(fetch).toHaveBeenCalledWith('/api/status/summary');
    });

    // We'll add more tests as we implement logic
});
