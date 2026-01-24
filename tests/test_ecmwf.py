import pytest
from unittest.mock import MagicMock, patch
import os
import sys

# We need to make sure the module can import 'herbie' even if mocked, 
# but since we are running in an environment with herbie installed, we can just patch it.
# However, if 'ecmwf' imports it inside the function, we patch 'herbie.Herbie'.

from ecmwf import fetch_grib_herbie

@patch("herbie.Herbie")
@patch("ecmwf.shutil.move")
@patch("ecmwf.os.path.exists")
@patch("ecmwf.os.makedirs")
def test_fetch_grib_herbie_success(mock_makedirs, mock_exists, mock_move, mock_herbie_cls):
    # Setup mock
    mock_herbie_instance = MagicMock()
    mock_herbie_cls.return_value = mock_herbie_instance
    
    # Mock get_localFilePath to return a fake source path
    fake_source = "/tmp/herbie_cache/file.grib2"
    mock_herbie_instance.get_localFilePath.return_value = fake_source
    mock_exists.return_value = True

    # Call function
    target_path = "/app/cache/ecmwf/test.grib2"
    result = fetch_grib_herbie(
        model_id="ecmwf_hres",
        variable_id="t2m",
        date_str="20260124",
        init_hour="00",
        forecast_hour="003",
        target_path=target_path
    )

    # Assertions
    mock_herbie_cls.assert_called_with(
        "20260124 00:00",
        model="ifs",
        product="oper",
        fxx=3,
        save_dir=os.path.dirname(target_path)
    )
    mock_herbie_instance.download.assert_called_with(":2t:", verbose=False)
    mock_move.assert_called_with(fake_source, target_path)
    assert result == target_path

def test_fetch_grib_herbie_invalid_var():
    with pytest.raises(RuntimeError, match="not mapped"):
        fetch_grib_herbie(
            "ecmwf_hres", "invalid_var", "20260124", "00", "003", "/tmp/t.grib2"
        )