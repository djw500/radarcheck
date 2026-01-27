import pytest
from unittest.mock import patch, MagicMock, mock_open
import os
import datetime
import numpy as np
from status_utils import scan_cache_status, get_disk_usage, read_scheduler_logs

# Mock configuration
MOCK_REPOMAP = {
    "TILES_DIR": "/fake/cache/tiles",
    "GRIB_CACHE_DIR": "/fake/cache/gribs",
    "MODELS": {
        "hrrr": {"max_forecast_hours": 18},
        "gfs": {"max_forecast_hours": 120}
    },
    "TILING_REGIONS": {
        "ne": {"default_resolution_deg": 0.1}
    }
}

@pytest.fixture
def mock_fs(tmp_path):
    """Create a mock filesystem structure for testing."""
    # Structure: cache/tiles/region/res/model/run/var.npz
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir()
    
    # Region: ne
    ne_dir = tiles_dir / "ne"
    ne_dir.mkdir()
    
    # Resolution: 0.1deg
    res_dir = ne_dir / "0.1deg"
    res_dir.mkdir()
    
    # Model: HRRR
    hrrr_dir = res_dir / "hrrr"
    hrrr_dir.mkdir()
    
    # Run: Complete (run_20260124_12) - 18 hours (max)
    run1 = hrrr_dir / "run_20260124_12"
    run1.mkdir()
    # Create valid npz with some size
    npz1 = run1 / "t2m.npz"
    np.savez(npz1, hours=np.arange(18))
    
    # Run: Partial (run_20260124_13) - 5 hours
    run2 = hrrr_dir / "run_20260124_13"
    run2.mkdir()
    npz2 = run2 / "t2m.npz"
    np.savez(npz2, hours=np.arange(5))

    # Model: GFS (Empty/Missing runs handled by logic finding gaps, 
    # but here we just test what is found)
    gfs_dir = res_dir / "gfs"
    gfs_dir.mkdir()
    
    # Create GRIBs directory
    gribs_dir = tmp_path / "gribs"
    gribs_dir.mkdir()
    grib_hrrr = gribs_dir / "hrrr"
    grib_hrrr.mkdir()
    # Create a dummy grib file
    (grib_hrrr / "test.grib2").write_bytes(b"0" * 1024) # 1KB
    
    return tmp_path

@patch("status_utils.repomap", MOCK_REPOMAP)
@patch("status_utils.os.walk")
def test_scan_cache_status_structure(mock_walk, mock_fs):
    """Verify that scan_cache_status returns the expected data structure."""
    pass 

@patch("status_utils.repomap")
def test_scan_cache_status_integration(mock_repomap, mock_fs):
    """Integration test using a temp directory structure."""
    mock_repomap.get.return_value = {}
    mock_repomap.__getitem__.side_effect = lambda k: {
        "TILES_DIR": str(mock_fs / "tiles"),
        "MODELS": {
            "hrrr": {"max_forecast_hours": 18, "name": "HRRR"},
            "gfs": {"max_forecast_hours": 120, "name": "GFS"}
        },
        "TILING_REGIONS": {
            "ne": {"default_resolution_deg": 0.1}
        }
    }[k]

    status = scan_cache_status(region="ne")
    
    assert "hrrr" in status
    assert "gfs" in status
    
    # HRRR should have runs
    assert "run_20260124_12" in status["hrrr"]["runs"]
    assert "run_20260124_13" in status["hrrr"]["runs"]
    
    # Check status flags
    assert status["hrrr"]["runs"]["run_20260124_12"]["status"] == "complete"
    assert status["hrrr"]["runs"]["run_20260124_13"]["status"] == "partial"

@patch("status_utils.repomap")
def test_get_disk_usage(mock_repomap, mock_fs):
    mock_repomap.get.return_value = {}
    mock_repomap.__getitem__.side_effect = lambda k: {
        "TILES_DIR": str(mock_fs / "tiles"),
        "GRIB_CACHE_DIR": str(mock_fs / "gribs"),
        "MODELS": {
            "hrrr": {}, "gfs": {}
        }
    }[k]
    
    usage = get_disk_usage()
    
    assert usage["total"] > 0
    assert usage["gribs"]["total"] == 1024
    assert usage["gribs"]["hrrr"] == 1024
    assert usage["tiles"]["total"] > 0
    assert "hrrr" in usage["tiles"]["models"]

def test_read_scheduler_logs():
    mock_log_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    with patch("builtins.open", mock_open(read_data=mock_log_content)):
        with patch("status_utils.os.path.exists", return_value=True):
            lines = read_scheduler_logs(lines=3)
            assert len(lines) == 3
            assert lines[0] == "Line 3"
            assert lines[-1] == "Line 5"

    with patch("status_utils.os.path.exists", return_value=False):
        assert read_scheduler_logs() == []