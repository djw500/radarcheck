import numpy as np
import pytest

from utils import compute_wind_speed, convert_units, format_forecast_hour


def test_convert_units_kelvin_to_fahrenheit():
    data = np.array([273.15, 300, 310])
    result = convert_units(data, "k_to_f")
    assert result[0] == pytest.approx(32.0)


def test_convert_units_unknown_returns_unchanged():
    data = np.array([1, 2, 3])
    result = convert_units(data, "unknown")
    np.testing.assert_array_equal(result, data)


def test_compute_wind_speed():
    u = np.array([3.0])
    v = np.array([4.0])
    result = compute_wind_speed(u, v)
    assert result[0] == pytest.approx(5.0)


def test_format_forecast_hour_uses_model_digits():
    assert format_forecast_hour(1, "hrrr") == "01"
    assert format_forecast_hour(1, "gfs") == "001"
