import os
from unittest.mock import MagicMock, patch

import pytest

from ecmwf import fetch_grib_herbie, herbie_run_available


@patch("ecmwf.Herbie")
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
        save_dir=os.path.dirname(target_path),
        verbose=False,
    )
    mock_herbie_instance.download.assert_called_with(":2t:", verbose=False)
    mock_move.assert_called_with(fake_source, target_path)
    assert result == target_path

def test_fetch_grib_herbie_invalid_var():
    with pytest.raises(RuntimeError, match="not mapped"):
        fetch_grib_herbie(
            "ecmwf_hres", "invalid_var", "20260124", "00", "003", "/tmp/t.grib2"
        )


@patch("ecmwf.requests.head")
@patch("ecmwf.Herbie")
def test_herbie_run_available_with_inventory(mock_herbie_cls, mock_head):
    mock_head.return_value.status_code = 200
    mock_herbie_instance = MagicMock()
    mock_herbie_instance.idx = "https://example.com/fake.index"
    mock_inventory = MagicMock()
    mock_inventory.empty = False
    mock_herbie_instance.inventory.return_value = mock_inventory
    mock_herbie_cls.return_value = mock_herbie_instance

    assert herbie_run_available(
        model_id="ecmwf_hres",
        variable_id="t2m",
        date_str="20260124",
        init_hour="00",
        forecast_hour="001",
    )

    mock_herbie_cls.assert_called_with(
        "20260124 00:00",
        model="ifs",
        product="oper",
        fxx=1,
        verbose=False,
    )
    mock_herbie_instance.inventory.assert_called_with(":2t:", verbose=False)


@patch("ecmwf.requests.head")
@patch("ecmwf.Herbie")
def test_herbie_run_available_missing_index(mock_herbie_cls, mock_head):
    mock_head.return_value.status_code = 404
    mock_herbie_instance = MagicMock()
    mock_herbie_instance.idx = "https://example.com/fake.index"
    mock_herbie_cls.return_value = mock_herbie_instance

    assert not herbie_run_available(
        model_id="ecmwf_hres",
        variable_id=None,
        date_str="20260124",
        init_hour="00",
        forecast_hour="001",
    )
