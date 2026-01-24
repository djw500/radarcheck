import pytest
import requests

import app as app_module
import cache_builder
from utils import GribDownloadError, PlotGenerationError


def test_fetch_grib_handles_network_timeout(monkeypatch, tmp_path):
    monkeypatch.setitem(cache_builder.repomap, "CACHE_DIR", str(tmp_path))
    monkeypatch.setitem(cache_builder.repomap, "MAX_DOWNLOAD_RETRIES", 1)

    def raise_timeout(*args, **kwargs):
        raise requests.Timeout

    monkeypatch.setattr(cache_builder, "download_file", raise_timeout)

    location_config = {
        "id": "test",
        "lon_min": -80,
        "lon_max": -70,
        "lat_min": 35,
        "lat_max": 45,
    }

    with pytest.raises(GribDownloadError, match="Failed to obtain valid GRIB"):
        cache_builder.fetch_grib(
            "hrrr",
            "refc",
            "20240101",
            "00",
            "01",
            "run_20240101_00",
            location_config,
        )


def test_create_plot_handles_corrupted_grib(monkeypatch, tmp_path):
    def raise_runtime(*args, **kwargs):
        raise RuntimeError("bad grib")

    monkeypatch.setattr("plotting.xr.open_dataset", raise_runtime)

    with pytest.raises(PlotGenerationError):
        from plotting import create_plot

        create_plot(
            str(tmp_path / "bad.grib2"),
            "2024-01-15 12:00:00",
            "01",
            str(tmp_path),
            variable_config={"short_name": "refc"},
        )


def test_api_handles_missing_cache_gracefully(monkeypatch):
    monkeypatch.setitem(cache_builder.repomap, "CACHE_DIR", "/nonexistent")
    monkeypatch.setitem(app_module.repomap, "CACHE_DIR", "/nonexistent")
    monkeypatch.setattr(app_module, "API_KEY", None)
    with app_module.app.test_client() as client:
        response = client.get("/api/locations")
    assert response.status_code == 200
    assert response.get_json() == []
