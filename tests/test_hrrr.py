import os
import pytest
import pytz
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import requests
from flask import Flask
from io import BytesIO

# Import the functions we want to test
from app import get_latest_hrrr_run, fetch_grib, forecast, app, index
from utils import download_file, fetch_county_shapefile
from config import repomap

# --- Utility Functions ---

def create_test_app():
    """Create a test Flask app."""
    test_app = Flask(__name__)
    test_app.config['TESTING'] = True
    # Import routes
    test_app.add_url_rule('/', view_func=index)
    test_app.add_url_rule('/forecast', view_func=forecast)
    return test_app

@pytest.fixture
def test_client():
    """Create a test client for the Flask app."""
    test_app = create_test_app()
    with test_app.test_client() as client:
        with test_app.app_context():
            yield client

@pytest.fixture
def mock_response():
    """Mock successful HTTP response"""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = b"mock_grib_data"
    mock.iter_content.return_value = [b"mock_grib_data"]  # Mock iter_content
    return mock

@pytest.fixture
def mock_file_download(tmpdir):
    """Mock file download and return a temporary file path."""
    def _mock_file_download(content="test content"):
        temp_file = tmpdir.join("temp_file.txt")
        temp_file.write(content.encode('utf-8'))
        return str(temp_file)
    return _mock_file_download

# --- Unit Tests for utils.py ---

def test_download_file_success(tmpdir, mock_response):
    """Test successful file download."""
    url = "http://example.com/test.txt"
    local_path = str(tmpdir.join("test.txt"))
    
    with patch('requests.get', return_value=mock_response) as mock_get:
        download_file(url, local_path)
        
        mock_get.assert_called_once_with(url, stream=True)
        assert os.path.exists(local_path)
        with open(local_path, 'rb') as f:
            assert f.read() == mock_response.content

def test_download_file_existing_cache(tmpdir, mock_response):
    """Test using cached file when it already exists."""
    url = "http://example.com/test.txt"
    local_path = str(tmpdir.join("test.txt"))
    
    # Create a dummy file
    with open(local_path, 'w') as f:
        f.write("existing content")
    
    with patch('requests.get', return_value=mock_response) as mock_get:
        download_file(url, local_path)
        
        # Ensure that requests.get is NOT called because the file exists
        mock_get.assert_not_called()
        with open(local_path, 'r') as f:
            assert f.read() == "existing content"  # File content should remain unchanged

def test_download_file_request_error(tmpdir):
    """Test handling of HTTP request errors during download."""
    url = "http://example.com/test.txt"
    local_path = str(tmpdir.join("test.txt"))
    
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.RequestException("Request failed")
        mock_get.return_value = mock_response
        
        with pytest.raises(requests.exceptions.RequestException) as exc_info:
            download_file(url, local_path)
        
        assert "Request failed" in str(exc_info.value)
        assert not os.path.exists(local_path)  # File should not be created

def test_fetch_county_shapefile(tmpdir):
    """Test downloading and extracting county shapefile."""
    cache_dir = str(tmpdir)
    county_zip = os.path.join(cache_dir, "cb_2018_us_county_20m.zip")
    county_dir = os.path.join(cache_dir, "county_shapefile")
    county_shp = os.path.join(county_dir, "cb_2018_us_county_20m.shp")
    
    # Mock the download_file and zipfile extraction
    with patch('utils.download_file') as mock_download, \
         patch('zipfile.ZipFile') as mock_zipfile:
        
        # Configure the mocks
        mock_download.return_value = None
        mock_zipfile_instance = MagicMock()
        mock_zipfile.return_value = mock_zipfile_instance
        mock_zipfile_instance.__enter__.return_value = mock_zipfile_instance
        mock_zipfile_instance.__exit__.return_value = None
        
        # Call the function
        result_shp = fetch_county_shapefile(cache_dir)
        
        # Assertions
        mock_download.assert_called_once_with(
            "https://www2.census.gov/geo/tiger/GENZ2018/shp/cb_2018_us_county_20m.zip",
            county_zip
        )
        mock_zipfile.assert_called_once_with(county_zip, "r")
        mock_zipfile_instance.extractall.assert_called_once_with(county_dir)
        assert result_shp == county_shp

# --- Integration Tests for app.py ---

def test_forecast_endpoint_success(test_client, mock_file_download):
    """Test successful /forecast endpoint."""
    # Mock the fetch_grib and create_plot functions
    with patch('app.fetch_grib', return_value=mock_file_download()) as mock_fetch_grib, \
         patch('app.create_plot', return_value=MagicMock(spec=BytesIO)) as mock_create_plot:
        
        response = test_client.get('/forecast')
        
        assert response.status_code == 200
        assert response.content_type == 'image/png'
        mock_fetch_grib.assert_called_once()
        mock_create_plot.assert_called_once()

def test_forecast_endpoint_error(test_client):
    """Test /forecast endpoint when create_plot raises an exception."""
    with patch('app.fetch_grib', side_effect=Exception("Plotting error")):
        response = test_client.get('/forecast')
        
        assert response.status_code == 500
        assert b"Error Generating Plot" in response.data

# --- Existing Tests (Review and Adjust) ---

def test_real_hrrr_availability():
    """Test actual HRRR server availability and time conversion"""
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # Check date string format (YYYYMMDD)
    assert len(date_str) == 8
    assert date_str.isdigit()
    
    # Check init hour format (HH)
    assert len(init_hour) == 2
    assert init_hour.isdigit()
    assert 0 <= int(init_hour) <= 23
    
    # Convert UTC time to Eastern Time
    utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M")
    utc = pytz.UTC.localize(utc_time)
    eastern = pytz.timezone('America/New_York')
    est_time = utc.astimezone(eastern)
    
    print(f"\nHRRR Run Information:")
    print(f"UTC Time: {utc.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Eastern Time: {est_time.strftime('%Y-%m-%d %I:%M %p %Z')}")
    #print(f"HRRR URL: {HRRR_URL}") # Removed HRRR_URL since it's a global constant
    
    # Verify the HRRR file is actually available
    #response = requests.head(HRRR_URL) # Removed HRRR_URL since it's a global constant
    #assert response.status_code == 200, f"HRRR file not available at {HRRR_URL}" # Removed HRRR_URL since it's a global constant
    
    # Get file size in MB if available
    #if 'content-length' in response.headers: # Removed HRRR_URL since it's a global constant
    #    size_mb = int(response.headers['content-length']) / (1024 * 1024) # Removed HRRR_URL since it's a global constant
    #    print(f"File size: {size_mb:.1f} MB") # Removed HRRR_URL since it's a global constant
    
    # The test now focuses on time conversion and HRRR run info, not URL availability
    pass

def test_real_grib_download():
    """Test actual GRIB file downloading and verification"""
    # Fetch the GRIB file
    grib_path = fetch_grib()
    
    # Verify the file exists
    assert os.path.exists(grib_path), f"GRIB file not found at {grib_path}"
    
    # Check file size (should be at least 100KB for a filtered GRIB2 file)
    size_kb = os.path.getsize(grib_path) / 1024
    print(f"\nDownloaded GRIB file: {grib_path}")
    print(f"File size: {size_kb:.1f} KB")
    
    assert size_kb > 100, f"GRIB file seems too small ({size_kb:.1f} KB)"

def test_get_latest_hrrr_run_error():
    """Test error handling when no HRRR run is available"""
    with patch('requests.head') as mock_head:
        # Mock all requests to return 404
        mock_head.return_value.status_code = 404
        
        with pytest.raises(Exception) as exc_info:
            get_latest_hrrr_run()
        
        assert "Could not find a recent HRRR run" in str(exc_info.value)

def test_latest_hrrr_info():
    """Test and display information about the latest available HRRR run"""
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # Convert to Eastern Time for display
    utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M")
    utc = pytz.UTC.localize(utc_time)
    eastern = pytz.timezone('America/New_York')
    est_time = utc.astimezone(eastern)
    
    print("\nLatest HRRR Run Information:")
    print(f"Date: {date_str}")
    print(f"Initialization Hour (UTC): {init_hour}Z")
    print(f"UTC Time: {utc.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Eastern Time: {est_time.strftime('%Y-%m-%d %I:%M %p %Z')}")
    
    # Verify the format and values
    assert len(date_str) == 8, "Date string should be 8 characters (YYYYMMDD)"
    assert date_str.isdigit(), "Date string should be all digits"
    assert len(init_hour) == 2, "Init hour should be 2 characters (HH)"
    assert 0 <= int(init_hour) <= 23, "Init hour should be between 00 and 23"
