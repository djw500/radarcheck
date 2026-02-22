from unittest.mock import MagicMock, patch

from grib_fetcher import (
    _get_search_string,
    get_valid_forecast_hours,
    check_availability,
)


def test_get_search_string_default():
    search = _get_search_string("t2m", "hrrr")
    assert "TMP" in search and "2 m" in search


def test_get_search_string_model_specific():
    search = _get_search_string("t2m", "ecmwf_hres")
    assert search == ":2t:"


def test_get_search_string_missing_variable():
    search = _get_search_string("nonexistent_var", "hrrr")
    assert search == ""


@patch("grib_fetcher._build_herbie")
def test_check_availability_true(mock_build):
    mock_herbie = MagicMock()
    mock_herbie.inventory.return_value = MagicMock(empty=False)
    mock_build.return_value = mock_herbie

    assert check_availability("hrrr", "20260215", "12", 1) is True


@patch("grib_fetcher._build_herbie")
def test_check_availability_false_on_exception(mock_build):
    mock_build.side_effect = Exception("network error")
    assert check_availability("hrrr", "20260215", "12", 1) is False
