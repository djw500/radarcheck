const { createTimeline } = require('../../static/js/timelineView');

test('builds timeline grid from run data', () => {
    document.body.innerHTML = `
        <div class="timeline">
            <div class="timeline-header">
                <div class="timeline-header-spacer"></div>
                <div class="timeline-cells" id="timelineHeader"></div>
            </div>
        </div>
    `;

    global.timelineData = [
        { run_id: 'run_1', init_time: '2024-01-01T00:00:00Z' }
    ];
    global.validTimes = {
        run_1: [
            { forecast_hour: 1, valid_time: '2024-01-01T01:00:00Z', frame_path: 'frame_01.png' }
        ]
    };

    createTimeline();

    const headerCells = document.querySelectorAll('.timeline-header-cell');
    const dataCells = document.querySelectorAll('.timeline-cell.has-data');

    expect(headerCells.length).toBeGreaterThan(0);
    expect(dataCells.length).toBe(1);
});
