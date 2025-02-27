import os
import logging
from io import BytesIO
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, send_file, render_template, redirect, url_for, request, abort, jsonify
import pytz

from config import repomap

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Add file handler
file_handler = RotatingFileHandler('logs/app.log', maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
logger.addHandler(file_handler)

app = Flask(__name__, static_folder='static', template_folder='templates')
logger.info('Application startup')

def get_available_locations():
    """Get list of locations with available forecast data"""
    locations = []
    for location_id, location_config in repomap["LOCATIONS"].items():
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        
        if not os.path.exists(location_cache_dir):
            continue
            
        # Look for the latest run
        latest_run_link = os.path.join(location_cache_dir, "latest")
        if os.path.islink(latest_run_link):
            latest_run = os.readlink(latest_run_link)
            run_dir = os.path.join(location_cache_dir, latest_run)
            
            if os.path.exists(run_dir):
                metadata_path = os.path.join(run_dir, "metadata.txt")
                
                if os.path.exists(metadata_path):
                    # Read metadata
                    metadata = {}
                    with open(metadata_path, "r") as f:
                        for line in f:
                            key, value = line.strip().split("=", 1)
                            metadata[key] = value
                    
                    # Check if forecast frames exist
                    has_frames = any(os.path.exists(os.path.join(run_dir, f"frame_{hour:02d}.png")) 
                                    for hour in range(1, 25))
                    
                    if has_frames:
                        locations.append({
                            "id": location_id,
                            "name": location_config["name"],
                            "init_time": metadata.get("init_time", "Unknown"),
                            "run_id": metadata.get("run_id", "Unknown")
                        })
    
    return locations

def get_location_runs(location_id):
    """Get all available runs for a location"""
    if location_id not in repomap["LOCATIONS"]:
        return []
        
    location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
    if not os.path.exists(location_cache_dir):
        return []
        
    runs = []
    for item in os.listdir(location_cache_dir):
        if item.startswith("run_") and os.path.isdir(os.path.join(location_cache_dir, item)):
            run_dir = os.path.join(location_cache_dir, item)
            metadata_path = os.path.join(run_dir, "metadata.txt")
            
            if os.path.exists(metadata_path):
                metadata = {}
                with open(metadata_path, "r") as f:
                    for line in f:
                        key, value = line.strip().split("=", 1)
                        metadata[key] = value
                
                # Check if this run has frames
                has_frames = any(os.path.exists(os.path.join(run_dir, f"frame_{hour:02d}.png")) 
                                for hour in range(1, 25))
                
                if has_frames:
                    runs.append({
                        "run_id": item,
                        "init_time": metadata.get("init_time", "Unknown"),
                        "date_str": metadata.get("date_str", ""),
                        "init_hour": metadata.get("init_hour", "")
                    })
    
    # Sort runs by init_time (newest first)
    runs.sort(key=lambda x: x["init_time"], reverse=True)
    return runs

def get_run_metadata(location_id, run_id):
    """Get metadata for a specific run"""
    if location_id not in repomap["LOCATIONS"]:
        return None
        
    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
    metadata_path = os.path.join(run_dir, "metadata.txt")
    
    if not os.path.exists(metadata_path):
        return None
        
    metadata = {}
    with open(metadata_path, "r") as f:
        for line in f:
            key, value = line.strip().split("=", 1)
            metadata[key] = value
            
    return metadata

def get_run_valid_times(location_id, run_id):
    """Get valid times for a specific run"""
    if location_id not in repomap["LOCATIONS"]:
        return []
        
    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
    valid_times_path = os.path.join(run_dir, "valid_times.txt")
    
    if not os.path.exists(valid_times_path):
        return []
        
    valid_times = []
    with open(valid_times_path, "r") as f:
        for line in f:
            parts = line.strip().split("=")
            if len(parts) >= 3:
                forecast_hour = int(parts[0])
                valid_time = parts[1]
                frame_path = parts[2]
                
                valid_times.append({
                    "forecast_hour": forecast_hour,
                    "valid_time": valid_time,
                    "frame_path": frame_path
                })
    
    # Sort by forecast hour
    valid_times.sort(key=lambda x: x["forecast_hour"])
    return valid_times

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

# --- Flask Endpoints ---

@app.route("/frame/<location_id>/<run_id>/<int:hour>")
def get_frame(location_id, run_id, hour):
    """Serve a single forecast frame for a specific location and run."""
    try:
        logger.info(f'Requesting frame for location {location_id}, run {run_id}, hour {hour}')
        
        if location_id not in repomap["LOCATIONS"]:
            logger.warning(f'Invalid location requested: {location_id}')
            return "Invalid location", 400
            
        if not 1 <= hour <= 24:
            logger.warning(f'Invalid forecast hour requested: {hour}')
            return "Invalid forecast hour", 400
            
        # Format hour as two digits
        hour_str = f"{hour:02d}"
        
        # Handle "latest" run_id
        if run_id == "latest":
            latest_link = os.path.join(repomap["CACHE_DIR"], location_id, "latest")
            if os.path.islink(latest_link):
                run_id = os.readlink(latest_link)
            else:
                logger.warning(f'No latest run available for location: {location_id}')
                return "No latest run available", 404
        
        # Check if the frame exists in cache
        run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
        frame_path = os.path.join(run_cache_dir, f"frame_{hour_str}.png")
        
        if not os.path.exists(frame_path):
            logger.warning(f'Frame not found in cache: {frame_path}')
            return "Forecast frame not available", 404
            
        return send_file(frame_path, mimetype="image/png")
    except Exception as e:
        logger.error(f'Unexpected error in get_frame: {str(e)}', exc_info=True)
        return f"Internal server error: {str(e)}", 500

@app.route("/frame/<location_id>/<int:hour>")
def get_latest_frame(location_id, hour):
    """Backward compatibility: serve a frame from the latest run."""
    return get_frame(location_id, "latest", hour)

@app.route("/location/<location_id>")
def location_view(location_id):
    """Show forecast for a specific location"""
    if location_id not in repomap["LOCATIONS"]:
        abort(404)
    
    # Get all runs for this location
    runs = get_location_runs(location_id)
    if not runs:
        return "Forecast data not available for this location", 404
    
    # Default to the latest run
    run_id = request.args.get('run', runs[0]['run_id'])
    
    # Get metadata for the selected run
    metadata = get_run_metadata(location_id, run_id)
    if not meta
        return "Selected forecast run not available", 404
    
    location_name = repomap["LOCATIONS"][location_id]["name"]
    init_time = metadata.get("init_time", "Unknown")
    
    # Get valid times for this run
    valid_times = get_run_valid_times(location_id, run_id)
    
    # Pre-fetch all valid times for all runs to avoid API calls
    all_valid_times = {}
    for run in runs:
        all_valid_times[run['run_id']] = get_run_valid_times(location_id, run['run_id'])
    
    # Get all available locations for the navigation
    locations = get_available_locations()
    
    return render_template('location.html', 
                          location_id=location_id,
                          location_name=location_name,
                          init_time=init_time,
                          run_id=run_id,
                          runs=runs,
                          locations=locations,
                          all_valid_times=all_valid_times)
            .forecast-container { 
                background: white;
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                margin-bottom: 20px;
            }
            .controls {
                margin: 20px 0;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            #timeSlider {
                flex-grow: 1;
            }
            .loading {
                display: none;
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(255,255,255,0.9);
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }
            .location-nav {
                margin-bottom: 20px;
            }
            .location-nav a, .run-selector a {
                display: inline-block;
                margin-right: 10px;
                padding: 5px 10px;
                background: #e0e0e0;
                border-radius: 3px;
                text-decoration: none;
                color: #333;
            }
            .location-nav a.active, .run-selector a.active {
                background: #004080;
                color: white;
            }
            .run-selector {
                margin-bottom: 15px;
            }
            .view-selector {
                margin: 15px 0;
            }
            .view-selector button {
                padding: 8px 15px;
                background: #e0e0e0;
                border: none;
                border-radius: 3px;
                cursor: pointer;
                margin-right: 10px;
            }
            .view-selector button.active {
                background: #004080;
                color: white;
            }
            .view {
                display: none;
            }
            .view.active {
                display: block;
            }
            .timeline-container {
                margin-top: 20px;
                overflow-x: auto;
            }
            .timeline {
                display: table;
                border-collapse: collapse;
                width: 100%;
                min-width: 800px;
            }
            .timeline-row {
                display: table-row;
            }
            .timeline-label {
                display: table-cell;
                padding: 5px 10px;
                background: #f0f0f0;
                border: 1px solid #ddd;
                width: 120px;
                font-weight: bold;
            }
            .timeline-cells {
                display: table-cell;
                white-space: nowrap;
                vertical-align: middle;
            }
            .timeline-cell {
                display: inline-block;
                width: 30px;
                height: 30px;
                border: 1px solid #ddd;
                text-align: center;
                line-height: 30px;
                font-size: 12px;
                cursor: pointer;
                position: relative;
            }
            .timeline-cell.has-data {
                background-color: #e0f0ff;
            }
            .timeline-cell.selected {
                border: 2px solid #004080;
                box-shadow: 0 0 5px rgba(0,64,128,0.5);
            }
            .timeline-header {
                display: table-row;
                font-weight: bold;
                text-align: center;
            }
            .timeline-header-cell {
                display: inline-block;
                width: 30px;
                border: 1px solid #ddd;
                background: #f0f0f0;
                text-align: center;
                padding: 5px 0;
                font-size: 12px;
                transform: rotate(-45deg);
                transform-origin: bottom left;
                height: 20px;
                position: relative;
                left: 15px;
                margin-bottom: 15px;
            }
            .timeline-header-spacer {
                display: table-cell;
                width: 120px;
            }
            .spaghetti-container {
                margin-top: 20px;
                height: 400px;
            }
            footer { margin-top: 20px; text-align: center; color: #666; }
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>HRRR Forecast Visualization</h1>
                <p>Compare multiple forecast runs to assess confidence</p>
            </header>
            
            <div class="location-nav">
                <a href="/">Home</a>
                {% for loc in locations %}
                <a href="/location/{{ loc.id }}" {% if loc.id == location_id %}class="active"{% endif %}>{{ loc.name }}</a>
                {% endfor %}
            </div>
            
            <div class="forecast-container">
                <h2>{{ location_name }}</h2>
                
                <div class="run-selector">
                    <strong>Select Model Run:</strong>
                    {% for run in runs %}
                    <a href="/location/{{ location_id }}?run={{ run.run_id }}" 
                       {% if run.run_id == run_id %}class="active"{% endif %}>
                       {{ run.init_time }}
                    </a>
                    {% endfor %}
                </div>
                
                <div class="view-selector">
                    <button id="singleViewBtn" class="active">Single Run View</button>
                    <button id="timelineViewBtn">Timeline View</button>
                    <button id="spaghettiViewBtn">Spaghetti Plot</button>
                </div>
                
                <div id="singleView" class="view active">
                    <p>Model initialized: {{ init_time }}</p>
                    <div class="controls">
                        <button id="playButton">Play</button>
                        <input type="range" id="timeSlider" min="1" max="24" value="1">
                        <span id="timeDisplay">Hour +1</span>
                    </div>
                    <div style="position: relative;">
                        <img id="forecastImage" src="/frame/{{ location_id }}/{{ run_id }}/1" alt="HRRR Forecast Plot" style="width: 100%; height: auto;">
                        <div id="loading" class="loading">Loading...</div>
                    </div>
                </div>
                
                <div id="timelineView" class="view">
                    <h3>Forecast Timeline</h3>
                    <p>Compare how forecasts have evolved across model runs</p>
                    
                    <div class="timeline-container">
                        <div class="timeline">
                            <div class="timeline-header">
                                <div class="timeline-header-spacer"></div>
                                <div class="timeline-cells" id="timelineHeader">
                                    <!-- Time headers will be inserted here by JavaScript -->
                                </div>
                            </div>
                            <!-- Timeline rows will be inserted here by JavaScript -->
                        </div>
                    </div>
                    
                    <div style="margin-top: 20px;">
                        <h4>Selected Forecast</h4>
                        <div id="selectedForecast" style="text-align: center; font-style: italic;">
                            Select a cell in the timeline to view the forecast
                        </div>
                        <div style="position: relative; margin-top: 10px;">
                            <img id="timelineImage" src="" alt="Selected Forecast" style="width: 100%; height: auto; display: none;">
                        </div>
                    </div>
                </div>
                
                <div id="spaghettiView" class="view">
                    <h3>Spaghetti Plot</h3>
                    <p>Compare precipitation forecasts across different model runs</p>
                    
                    <div class="spaghetti-container">
                        <canvas id="spaghettiChart"></canvas>
                    </div>
                    <div style="margin-top: 10px; text-align: center; font-style: italic;">
                        Note: This is a simplified visualization. Each line represents a different model run.
                    </div>
                </div>
            </div>
            
            <footer>&copy; 2025 Weather App</footer>
        </div>
        
        <script>
            // View switching
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
                });
            });
            
            // Single Run View
            const slider = document.getElementById('timeSlider');
            const timeDisplay = document.getElementById('timeDisplay');
            const forecastImage = document.getElementById('forecastImage');
            const loading = document.getElementById('loading');
            const playButton = document.getElementById('playButton');
            const locationId = "{{ location_id }}";
            const runId = "{{ run_id }}";
            
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
            
            // Timeline View
            const timelineData = {{ runs|tojson }};
            const validTimes = {};
            
            // Function to create the timeline
            function createTimeline() {
                const timeline = document.querySelector('.timeline');
                const timelineHeader = document.getElementById('timelineHeader');
                
                // Create a set of all valid times across all runs
                const allValidTimes = new Set();
                
                // Use pre-loaded valid times data
                const validTimes = {{ all_valid_times|tojson }};
            
                // Process the pre-loaded data
                Object.values(validTimes).forEach(runData => {
                    runData.forEach(vt => {
                        // Create a simplified time key (just date and hour)
                        const validTime = new Date(vt.valid_time);
                        const timeKey = `${validTime.getFullYear()}-${(validTime.getMonth()+1).toString().padStart(2, '0')}-${validTime.getDate().toString().padStart(2, '0')} ${validTime.getHours().toString().padStart(2, '0')}:00`;
                        allValidTimes.add(timeKey);
                    });
                });
            
                // Now create the timeline with the data we already have
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
            
            // Initialize timeline when the timeline view is shown
            document.getElementById('timelineViewBtn').addEventListener('click', createTimeline);
            
            // Pre-create the timeline structure when the page loads
            window.addEventListener('load', function() {
                // Create the timeline structure but don't show it yet
                setTimeout(createTimeline, 100);
            });
            
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
                const fetchPromises = timelineData.map((run, index) => {
                    return fetch(`/api/valid_times/${locationId}/${run.run_id}`)
                        .then(response => response.json())
                        .then(data => {
                            // Sort by valid time
                            data.sort((a, b) => new Date(a.valid_time) - new Date(b.valid_time));
                            
                            // Create dataset
                            datasets.push({
                                label: new Date(run.init_time).toLocaleString(),
                                data: data.map(vt => ({
                                    x: new Date(vt.valid_time),
                                    y: Math.random() * 100  // Placeholder for actual precipitation data
                                })),
                                borderColor: colors[index % colors.length],
                                backgroundColor: 'transparent',
                                tension: 0.4
                            });
                        });
                });
                
                spaghettiChart = new Chart(ctx, {
                        type: 'line',
                        data: {
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
                });
            }
            
            // Initialize spaghetti plot when the view is shown
            document.getElementById('spaghettiViewBtn').addEventListener('click', createSpaghettiPlot);
        </script>
    </body>
    </html>
    """
    
    # Get all available locations for the navigation
    locations = get_available_locations()
    
    return render_template_string(html, 
                                 location_id=location_id,
                                 location_name=location_name,
                                 init_time=init_time,
                                 run_id=run_id,
                                 runs=runs,
                                 locations=locations,
                                 all_valid_times=all_valid_times)

@app.route("/forecast")
def forecast():
    """Legacy endpoint for GIF - redirect to main page"""
    return redirect(url_for('index'))


@app.route("/api/runs/<location_id>")
def api_runs(location_id):
    """API endpoint to get all runs for a location"""
    runs = get_location_runs(location_id)
    return jsonify(runs)

@app.route("/api/valid_times/<location_id>/<run_id>")
def api_valid_times(location_id, run_id):
    """API endpoint to get valid times for a specific run"""
    valid_times = get_run_valid_times(location_id, run_id)
    return jsonify(valid_times)

@app.route("/")
def index():
    """Home page showing available locations"""
    locations = get_available_locations()
    return render_template('index.html', locations=locations)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
