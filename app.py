import os
from datetime import datetime
from io import BytesIO

import requests
from flask import Flask, send_file, render_template_string
from PIL import Image, ImageDraw, ImageFont
import pytz

app = Flask(__name__)

# IEM endpoints (real endpoints)
WMS_BASE_URL = "https://mesonet.agron.iastate.edu/cgi-bin/wms/hrrr/refd.cgi"
METADATA_URL = "https://mesonet.agron.iastate.edu/data/gis/images/4326/hrrr/refd_1080.json"

# Bounding box around Philadelphia (approximate)
BBOX = "-75.5,39.5,-74.5,40.5"
WIDTH = 600
HEIGHT = 600

WMS_PARAMS = {
    "service": "WMS",
    "request": "GetMap",
    "version": "1.1.1",
    "layers": "refd_0000",
    "styles": "",
    "srs": "EPSG:4326",
    "bbox": BBOX,
    "width": WIDTH,
    "height": HEIGHT,
    "format": "image/png"
}

def fetch_metadata():
    response = requests.get(METADATA_URL)
    response.raise_for_status()
    return response.json()

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

def fetch_hrrr_image():
    response = requests.get(WMS_BASE_URL, params=WMS_PARAMS)
    response.raise_for_status()
    return Image.open(BytesIO(response.content))


def annotate_image(img, text):
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except IOError:
        font = ImageFont.load_default()
    text_position = (10, 10)
    # Use the font's getsize() method to determine the size of the text.
    text_width, text_height = font.getsize(text)
    # Draw a semi-opaque black rectangle behind the text for readability.
    rect_position = (text_position[0] - 2, text_position[1] - 2,
                     text_position[0] + text_width + 2, text_position[1] + text_height + 2)
    draw.rectangle(rect_position, fill="black")
    draw.text(text_position, text, fill="white", font=font)
    return img

@app.route("/latest-forecast")
def latest_forecast():
    try:
        metadata = fetch_metadata()
        utc_time_str = metadata.get("model_init_utc", "2025-02-08 00:00:00")
        annotation_text = get_local_time_text(utc_time_str)
    except Exception:
        annotation_text = "Forecast time unknown"

    try:
        img = fetch_hrrr_image()
    except Exception as e:
        return f"Error fetching image: {e}", 500

    annotated = annotate_image(img, annotation_text)
    img_io = BytesIO()
    annotated.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")

@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Philadelphia HRRR Forecast</title>
      <style>
        body { margin: 0; padding: 0; text-align: center; font-family: Arial, sans-serif; background: #f0f0f0; }
        header { background: #004080; color: white; padding: 1em; }
        img { max-width: 100%; height: auto; }
        footer { background: #004080; color: white; padding: 0.5em; position: fixed; bottom: 0; width: 100%; }
      </style>
    </head>
    <body>
      <header>
        <h1>Philadelphia HRRR Forecast</h1>
      </header>
      <main>
        <img src="/latest-forecast" alt="Latest HRRR Forecast">
      </main>
      <footer>
        &copy; 2025 Weather App
      </footer>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)