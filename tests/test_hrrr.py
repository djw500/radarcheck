import os
import pytest
import pytz
from datetime import datetime
from unittest.mock import patch, MagicMock
import requests

# Import the functions we want to test
from app import get_latest_hrrr_run, fetch_grib, CACHE_DIR, HRRR_URL

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
    print(f"HRRR URL: {HRRR_URL}")
    
    # Verify the HRRR file is actually available
    response = requests.head(HRRR_URL)
    assert response.status_code == 200, f"HRRR file not available at {HRRR_URL}"
    
    # Get file size in MB if available
    if 'content-length' in response.headers:
        size_mb = int(response.headers['content-length']) / (1024 * 1024)
        print(f"File size: {size_mb:.1f} MB")

@pytest.fixture
def mock_response():
    """Mock successful HTTP response"""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = b"mock_grib_data"
    return mock

def test_real_grib_download():
    """Test actual GRIB file downloading and verification"""
    # Fetch the GRIB file
    grib_path = fetch_grib()
    
    # Verify the file exists
    assert os.path.exists(grib_path), f"GRIB file not found at {grib_path}"
    
    # Check file size (should be at least 1MB for a valid GRIB2 file)
    size_mb = os.path.getsize(grib_path) / (1024 * 1024)
    print(f"\nDownloaded GRIB file: {grib_path}")
    print(f"File size: {size_mb:.1f} MB")
    
    assert size_mb > 1, f"GRIB file seems too small ({size_mb:.1f} MB)"

def test_get_latest_hrrr_run_error():
    """Test error handling when no HRRR run is available"""
    with patch('requests.head') as mock_head:
        # Mock all requests to return 404
        mock_head.return_value.status_code = 404
        
        with pytest.raises(Exception) as exc_info:
            get_latest_hrrr_run()
        
        assert "Could not find a recent HRRR run" in str(exc_info.value)
