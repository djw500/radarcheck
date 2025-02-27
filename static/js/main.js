// Single Run View
function initSingleRunView() {
    const slider = document.getElementById('timeSlider');
    const timeDisplay = document.getElementById('timeDisplay');
    const forecastImage = document.getElementById('forecastImage');
    const loading = document.getElementById('loading');
    const playButton = document.getElementById('playButton');
    
    let isPlaying = false;
    let playInterval;
    
    // Preload images
    const images = new Array(24);
    function preloadImage(hour) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                images[hour-1] = img;
                resolve();
            };
            img.onerror = reject;
            img.src = `/frame/${locationId}/${runId}/${hour}`;
        });
        });
    }
    
    // Preload first few frames immediately
    Promise.all([1,2,3].map(preloadImage)).then(() => {
        // Then load the rest in background
        for (let hour = 4; hour <= 24; hour++) {
            preloadImage(hour);
        }
    });
    
    function updateDisplay(hour) {
        timeDisplay.textContent = `Hour +${hour}`;
        if (images[hour-1]) {
            forecastImage.src = images[hour-1].src;
        } else {
            forecastImage.src = `/frame/${locationId}/${runId}/${hour}`;
        }
    }
    
    slider.addEventListener('input', () => {
        const hour = parseInt(slider.value);
        updateDisplay(hour);
    });
    
    playButton.addEventListener('click', () => {
        if (isPlaying) {
            clearInterval(playInterval);
            playButton.textContent = 'Play';
        } else {
            playInterval = setInterval(() => {
                let hour = parseInt(slider.value);
                hour = hour >= 24 ? 1 : hour + 1;
                slider.value = hour;
                updateDisplay(hour);
            }, 500);
            playButton.textContent = 'Pause';
        }
        isPlaying = !isPlaying;
    });
}

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
            const timeKey = `${validTime.getFullYear()}-${(validTime.getMonth()+1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
            allValidTimes.add(timeKey);
        });
    });

    // Sort valid times
    const sortedTimes = Array.from(allValidTimes).sort();
    
    // Create header
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
                const vtTimeKey = `${validTime.getFullYear()}-${(validTime.getMonth()+1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
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
        const vtTimeKey = `${validTime.getFullYear()}-${(validTime.getMonth()+1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
        return vtTimeKey === timeKey;
    });
    
    if (matchingTime) {
        const hour = matchingTime.forecast_hour;
        const selectedForecast = document.getElementById('selectedForecast');
        const timelineImage = document.getElementById('timelineImage');
        
        selectedForecast.textContent = `Run: ${new Date(timelineData.find(r => r.run_id === runId).init_time).toLocaleString()}, Valid: ${timeKey}, Forecast Hour: +${hour}`;
        timelineImage.src = `/frame/${locationId}/${runId}/${hour}`;
        timelineImage.style.display = 'block';
    }
}

// Spaghetti Plot
let spaghettiChart = null;

function createSpaghettiPlot() {
    if (spaghettiChart) {
        spaghettiChart.destroy();
    }
    
    const ctx = document.getElementById('spaghettiChart').getContext('2d');
    
    // Prepare datasets
    const datasets = [];
    const colors = ['#004080', '#008000', '#800000', '#808000', '#800080'];
    
    // Create a dataset for each run
    timelineData.forEach((run, index) => {
        // Get valid times for this run
        const runValidTimes = validTimes[run.run_id] || [];
        
        // Sort by valid time
        runValidTimes.sort((a, b) => new Date(a.valid_time) - new Date(b.valid_time));
        
        // Create dataset
        datasets.push({
            label: new Date(run.init_time).toLocaleString(),
            data: runValidTimes.map(vt => ({
                x: new Date(vt.valid_time),
                y: Math.random() * 100  // Placeholder for actual precipitation data
            })),
            borderColor: colors[index % colors.length],
            backgroundColor: 'transparent',
            tension: 0.4
        });
    });
    
    spaghettiChart = new Chart(ctx, {
        type: 'line',
         {
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'hour',
                        displayFormats: {
                            hour: 'MM/dd HH:mm'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Valid Time'
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Precipitation Intensity (simulated)'
                    },
                    min: 0,
                    max: 100
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Precipitation Forecast Comparison'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            }
        }
    });
}

// View switching
function initViewSwitching() {
    const viewButtons = document.querySelectorAll('.view-selector button');
    const views = document.querySelectorAll('.view');
    
    viewButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Deactivate all buttons and views
            viewButtons.forEach(b => b.classList.remove('active'));
            views.forEach(v => v.classList.remove('active'));
            
            // Activate the clicked button and corresponding view
            button.classList.add('active');
            const viewId = button.id.replace('Btn', '');
            document.getElementById(viewId).classList.add('active');
            
            // Initialize the view if needed
            if (viewId === 'timelineView') {
                createTimeline();
            } else if (viewId === 'spaghettiView') {
                createSpaghettiPlot();
            }
        });
    });
}

// Initialize everything when the page loads
document.addEventListener('DOMContentLoaded', function() {
    initViewSwitching();
    initSingleRunView();
    
    // Pre-create the timeline structure when the page loads
    setTimeout(createTimeline, 100);
});
