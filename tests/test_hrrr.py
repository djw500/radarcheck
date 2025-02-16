import os
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
import requests

# Import the functions we want to test
from app import get_latest_hrrr_run, fetch_grib, CACHE_DIR

def test_get_latest_hrrr_run():
    """Test that get_latest_hrrr_run returns valid data format"""
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # Check date string format (YYYYMMDD)
    assert len(date_str) == 8
    assert date_str.isdigit()
    
    # Check init hour format (HH)
    assert len(init_hour) == 2
    assert init_hour.isdigit()
    assert 0 <= int(init_hour) <= 23
    
    # Check init time format
    datetime.strptime(init_time, "%Y-%m-%d %H:%M")

@pytest.fixture
def mock_response():
    """Mock successful HTTP response"""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = b"mock_grib_data"
    return mock

def test_fetch_grib(mock_response):
    """Test GRIB file fetching with mocked HTTP response"""
    with patch('requests.get', return_value=mock_response):
        with patch('builtins.open', create=True) as mock_open:
            # Clear cache if exists
            test_grib = os.path.join(CACHE_DIR, "test.grib2")
            if os.path.exists(test_grib):
                os.remove(test_grib)
                
            # Test the fetch
            result = fetch_grib()
            
            # Verify the file would have been downloaded
            assert mock_open.called
            
            # Verify the result is a path string
            assert isinstance(result, str)
            assert result.endswith('.grib2')

def test_get_latest_hrrr_run_error():
    """Test error handling when no HRRR run is available"""
    with patch('requests.head') as mock_head:
        # Mock all requests to return 404
        mock_head.return_value.status_code = 404
        
        with pytest.raises(Exception) as exc_info:
            get_latest_hrrr_run()
        
        assert "Could not find a recent HRRR run" in str(exc_info.value)
