import pytest
from unittest.mock import patch, MagicMock
import os
import xarray as xr
import numpy as np
from build_tiles import build_region_tiles
from config import repomap

@pytest.fixture
def mock_grib_file(tmp_path):
    # Create a dummy GRIB-like netCDF/dataset that can be opened by xarray
    # Since we can't easily create a valid GRIB2 without eccodes/cfgrib write support,
    # we'll mock the open_dataset call instead or use a checked-in grib.
    # But for now, let's mock the download and the xarray open.
    pass

@patch("build_tiles.download_all_hours_parallel")
@patch("build_tiles.build_tiles_for_variable")
@patch("build_tiles.save_tiles_npz")
def test_build_region_tiles_ecmwf(mock_save, mock_build, mock_download):
    # Setup
    mock_download.return_value = {3: "/tmp/mock_grib.grib2"}
    
    # Mock return from build_tiles_for_variable: mins, maxs, means, hours, meta
    mock_build.return_value = (
        np.array([10]), np.array([20]), np.array([15]), np.array([3]), 
        {"lon_0_360": True}
    )
    
    # Run
    # We use 'ecmwf_hres' which is configured in config.py
    build_region_tiles(
        region_id="ne",
        model_id="ecmwf_hres",
        run_id="run_20260124_00",
        variables=["t2m"],
        max_hours=3
    )
    
    # Verify
    mock_download.assert_called_once()
    assert mock_download.call_args[0][0] == "ecmwf_hres"
    assert mock_download.call_args[0][1] == "t2m"
    
    mock_build.assert_called_once()
    mock_save.assert_called_once()
    
    # Check that metadata passed to save includes correct model info
    save_call_args = mock_save.call_args
    assert save_call_args[0][3] == "ecmwf_hres" # model_id

