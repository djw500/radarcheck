import os
import logging
from io import BytesIO
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, send_file, render_template_string, redirect, url_for, request, abort
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

app = Flask(__name__)
logger.info('Application startup')

def get_available_locations():
    """Get list of locations with available forecast data"""
    locations = []
    for location_id, location_config in repomap["LOCATIONS"].items():
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        metadata_path = os.path.join(location_cache_dir, "metadata.txt")
        
        if os.path.exists(metadata_path):
            # Read metadata
            metadata = {}
            with open(metadata_path, "r") as f:
                for line in f:
                    key, value = line.strip().split("=", 1)
                    metadata[key] = value
            
            # Check if forecast frames exist
            has_frames = any(os.path.exists(os.path.join(location_cache_dir, f"frame_{hour:02d}.png")) 
                            for hour in range(1, 25))
            
            if has_frames:
                locations.append({
                    "id": location_id,
                    "name": location_config["name"],
                    "init_time": metadata.get("init_time", "Unknown")
                })
    
    return locations

def get_location_metadata(location_id):
    """Get metadata for a specific location"""
    if location_id not in repomap["LOCATIONS"]:
        return None
        
    location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
    metadata_path = os.path.join(location_cache_dir, "metadata.txt")
    
    if not os.path.exists(metadata_path):
        return None
        
    metadata = {}
    with open(metadata_path, "r") as f:
        for line in f:
            key, value = line.strip().split("=", 1)
            metadata[key] = value
            
    return metadata

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

# --- Flask Endpoints ---

@app.route("/frame/<location_id>/<int:hour>")
def get_frame(location_id, hour):
    """Serve a single forecast frame for a specific location."""
    try:
        logger.info(f'Requesting frame for location {location_id}, hour {hour}')
        
        if location_id not in repomap["LOCATIONS"]:
            logger.warning(f'Invalid location requested: {location_id}')
            return "Invalid location", 400
            
        if not 1 <= hour <= 24:
            logger.warning(f'Invalid forecast hour requested: {hour}')
            return "Invalid forecast hour", 400
            
        # Format hour as two digits
        hour_str = f"{hour:02d}"
        
        # Check if the frame exists in cache
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        frame_path = os.path.join(location_cache_dir, f"frame_{hour_str}.png")
        
        if not os.path.exists(frame_path):
            logger.warning(f'Frame not found in cache: {frame_path}')
            return "Forecast frame not available", 404
            
        return send_file(frame_path, mimetype="image/png")
    except Exception as e:
        logger.error(f'Unexpected error in get_frame: {str(e)}', exc_info=True)
        return f"Internal server error: {str(e)}", 500

@app.route("/location/<location_id>")
def location_view(location_id):
    """Show forecast for a specific location"""
    if location_id not in repomap["LOCATIONS"]:
        abort(404)
        
    metadata = get_location_metadata(location_id)
    if not metadata:
        return "Forecast data not available for this location", 404
        
    location_name = repomap["LOCATIONS"][location_id]["name"]
    init_time = metadata.get("init_time", "Unknown")
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ location_name }} - HRRR Forecast</title>
        <style>
            body { margin: 0; padding: 20px; font-family: Arial, sans-serif; background: #f0f0f0; }
            .container { max-width: 1200px; margin: 0 auto; }
            header { background: #004080; color: white; padding: 1em; margin-bottom: 20px; border-radius: 5px; }
            .forecast-container { 
                background: white;
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
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
            .location-nav a {
                display: inline-block;
                margin-right: 10px;
                padding: 5px 10px;
                background: #e0e0e0;
                border-radius: 3px;
                text-decoration: none;
                color: #333;
            }
            .location-nav a.active {
                background: #004080;
                color: white;
            }
            footer { margin-top: 20px; text-align: center; color: #666; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>HRRR Forecast Visualization</h1>
                <p>Model initialized: {{ init_time }}</p>
            </header>
            
            <div class="location-nav">
                <a href="/">Home</a>
                {% for loc in locations %}
                <a href="/location/{{ loc.id }}" {% if loc.id == location_id %}class="active"{% endif %}>{{ loc.name }}</a>
                {% endfor %}
            </div>
            
            <div class="forecast-container">
                <h2>{{ location_name }}</h2>
                <div class="controls">
                    <button id="playButton">Play</button>
                    <input type="range" id="timeSlider" min="1" max="24" value="1">
                    <span id="timeDisplay">Hour +1</span>
                </div>
                <div style="position: relative;">
                    <img id="forecastImage" src="/frame/{{ location_id }}/1" alt="HRRR Forecast Plot" style="width: 100%; height: auto;">
                    <div id="loading" class="loading">Loading...</div>
                </div>
            </div>
            <footer>&copy; 2025 Weather App</footer>
        </div>
        
        <script>
            const slider = document.getElementById('timeSlider');
            const timeDisplay = document.getElementById('timeDisplay');
            const forecastImage = document.getElementById('forecastImage');
            const loading = document.getElementById('loading');
            const playButton = document.getElementById('playButton');
            const locationId = "{{ location_id }}";
            
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
                    img.src = `/frame/${locationId}/${hour}`;
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
                    forecastImage.src = `/frame/${locationId}/${hour}`;
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
                                 locations=locations)

@app.route("/forecast")
def forecast():
    """Legacy endpoint for GIF - redirect to main page"""
    return redirect(url_for('index'))

@app.route("/")
def index():
    """Home page showing available locations"""
    locations = get_available_locations()
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HRRR Forecast Visualization</title>
        <style>
            body { margin: 0; padding: 20px; font-family: Arial, sans-serif; background: #f0f0f0; }
            .container { max-width: 1200px; margin: 0 auto; }
            header { background: #004080; color: white; padding: 1em; margin-bottom: 20px; border-radius: 5px; }
            .locations-container { 
                background: white;
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .location-card {
                margin-bottom: 15px;
                padding: 15px;
                border: 1px solid #ddd;
                border-radius: 5px;
                background: #f9f9f9;
            }
            .location-card h3 {
                margin-top: 0;
            }
            .location-card a {
                display: inline-block;
                margin-top: 10px;
                padding: 5px 15px;
                background: #004080;
                color: white;
                text-decoration: none;
                border-radius: 3px;
            }
            footer { margin-top: 20px; text-align: center; color: #666; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>HRRR Forecast Visualization</h1>
            </header>
            
            <div class="locations-container">
                <h2>Available Locations</h2>
                
                {% if locations %}
                    {% for location in locations %}
                    <div class="location-card">
                        <h3>{{ location.name }}</h3>
                        <p>Model initialized: {{ location.init_time }}</p>
                        <a href="/location/{{ location.id }}">View Forecast</a>
                    </div>
                    {% endfor %}
                {% else %}
                    <p>No forecast data is currently available. Please check back later.</p>
                {% endif %}
            </div>
            
            <footer>&copy; 2025 Weather App</footer>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html, locations=locations)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
