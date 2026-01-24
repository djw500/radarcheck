// Timeline View
function createTimeline() {
    const timeline = document.querySelector('.timeline');
    const timelineHeader = document.getElementById('timelineHeader');

    // Create a set of all valid times across all runs
    const allValidTimes = new Set();

    // Process the pre-loaded data
    Object.values(validTimes).forEach(runData => {
        runData.forEach(vt => {
            const validTime = new Date(vt.valid_time);
            const timeKey = `${validTime.getFullYear()}-${(validTime.getMonth() + 1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
            allValidTimes.add(timeKey);
        });
    });

    // Sort valid times
    const sortedKeys = Array.from(allValidTimes).sort();

    // Build header meta with day grouping
    const header = [];
    let currentDay = null;
    let dayIndex = -1;
    sortedKeys.forEach(timeKey => {
        const [dateStr, hourStr] = timeKey.split(' ');
        if (dateStr !== currentDay) {
            currentDay = dateStr;
            dayIndex += 1;
        }
        header.push({ timeKey, dateStr, hourStr, dayIndex, isDayStart: hourStr.startsWith('00') });
    });

    // Clear existing rows and header
    timeline.querySelectorAll('.timeline-row').forEach(row => row.remove());
    timelineHeader.innerHTML = '';
    header.forEach(h => {
        const cell = document.createElement('div');
        cell.className = 'timeline-header-cell';
        cell.textContent = h.hourStr; // show hour
        cell.title = `${h.dateStr} ${h.hourStr}`;
        if (h.isDayStart) cell.classList.add('day-start');
        if (h.dayIndex % 2 === 1) cell.classList.add('day-even');
        // Also show date inline for day starts
        if (h.isDayStart) {
            const [y, m, d] = h.dateStr.split('-');
            const dateLabel = `${m}/${d}`;
            cell.setAttribute('data-date', dateLabel);
        }
        timelineHeader.appendChild(cell);
    });

    // Create a row for each run
    const latestInit = Math.max(...timelineData.map(r => new Date(r.init_time).getTime()));
    timelineData.forEach(run => {
        const row = document.createElement('div');
        row.className = 'timeline-row';

        const label = document.createElement('div');
        label.className = 'timeline-label';
        const dt = new Date(run.init_time);
        const z = dt.getUTCHours().toString().padStart(2, '0');
        const agoHrs = Math.round((latestInit - dt.getTime()) / 3600000);
        const agoLabel = agoHrs === 0 ? 'LATEST' : `${agoHrs}h ago`;
        label.textContent = `${dt.toLocaleDateString()} ${z}Z • ${agoLabel}`;
        row.appendChild(label);

        const cells = document.createElement('div');
        cells.className = 'timeline-cells';

        header.forEach(h => {
            const cell = document.createElement('div');
            cell.className = 'timeline-cell';
            cell.dataset.timeKey = h.timeKey;
            cell.dataset.runId = run.run_id;
            if (h.isDayStart) cell.classList.add('day-start');
            if (h.dayIndex % 2 === 1) cell.classList.add('day-even');

            // Check if this run has data for this time
            const runValidTimes = validTimes[run.run_id] || [];
            const hasData = runValidTimes.some(vt => {
                const validTime = new Date(vt.valid_time);
                const vtTimeKey = `${validTime.getFullYear()}-${(validTime.getMonth() + 1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
                return vtTimeKey === h.timeKey;
            });

            if (hasData) {
                cell.classList.add('has-data');
                cell.addEventListener('click', () => selectTimelineCell(cell));
            }

            cells.appendChild(cell);
        });

        row.appendChild(cells);
        timeline.appendChild(row);
    });
}

function selectTimelineCell(cell) {
    // Remove selection from all cells
    document.querySelectorAll('.timeline-cell').forEach(c => c.classList.remove('selected'));

    // Add selection to clicked cell
    cell.classList.add('selected');

    const timeKey = cell.dataset.timeKey;
    const runId = cell.dataset.runId;

    // Find the forecast hour for this valid time
    const runValidTimes = validTimes[runId] || [];
    const matchingTime = runValidTimes.find(vt => {
        const validTime = new Date(vt.valid_time);
        const vtTimeKey = `${validTime.getFullYear()}-${(validTime.getMonth() + 1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
        return vtTimeKey === timeKey;
    });

    if (matchingTime) {
        const hour = matchingTime.forecast_hour;
        const selectedForecast = document.getElementById('selectedForecast');
        const timelineImage = document.getElementById('timelineImage');

        selectedForecast.textContent = `Run: ${new Date(timelineData.find(r => r.run_id === runId).init_time).toLocaleString()}, Valid: ${timeKey}, Forecast Hour: +${hour}`;
        timelineImage.src = `/frame/${locationId}/${modelId}/${runId}/${variableId}/${hour}${apiKeyParam}`;
        timelineImage.style.display = 'block';
    }
}

if (typeof module !== 'undefined') {
    module.exports = { createTimeline, selectTimelineCell };
}
