// Timeline View
function createTimeline() {
    const timeline = document.querySelector('.timeline');
    const timelineHeader = document.getElementById('timelineHeader');

    // Create a set of all valid times across all runs
    const allValidTimes = new Set();

    // Process the pre-loaded data
    Object.values(validTimes).forEach(runData => {
        runData.forEach(vt => {
            // Create a simplified time key (just date and hour)
            const validTime = new Date(vt.valid_time);
            const timeKey = `${validTime.getFullYear()}-${(validTime.getMonth() + 1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
            allValidTimes.add(timeKey);
        });
    });

    // Sort valid times
    const sortedTimes = Array.from(allValidTimes).sort();

    // Clear existing rows and header
    timeline.querySelectorAll('.timeline-row').forEach(row => row.remove());
    timelineHeader.innerHTML = '';
    sortedTimes.forEach(timeKey => {
        const cell = document.createElement('div');
        cell.className = 'timeline-header-cell';
        cell.textContent = timeKey.split(' ')[1]; // Just show the time part
        cell.title = timeKey;
        timelineHeader.appendChild(cell);
    });

    // Create a row for each run
    timelineData.forEach(run => {
        const row = document.createElement('div');
        row.className = 'timeline-row';

        const label = document.createElement('div');
        label.className = 'timeline-label';
        label.textContent = new Date(run.init_time).toLocaleString();
        row.appendChild(label);

        const cells = document.createElement('div');
        cells.className = 'timeline-cells';

        sortedTimes.forEach(timeKey => {
            const cell = document.createElement('div');
            cell.className = 'timeline-cell';
            cell.dataset.timeKey = timeKey;
            cell.dataset.runId = run.run_id;

            // Check if this run has data for this time
            const runValidTimes = validTimes[run.run_id] || [];
            const hasData = runValidTimes.some(vt => {
                const validTime = new Date(vt.valid_time);
                const vtTimeKey = `${validTime.getFullYear()}-${(validTime.getMonth() + 1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
                return vtTimeKey === timeKey;
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
        timelineImage.src = `/frame/${locationId}/${runId}/${hour}${apiKeyParam}`;
        timelineImage.style.display = 'block';
    }
}
