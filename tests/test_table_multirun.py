import json
import numpy as np
import pytest

import app as app_module
from app import app
from tile_db import init_db, record_tile_run, record_tile_variable


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app_module.API_KEY = None
    with app.test_client() as client:
        yield client


def _write_tile_run(base_dir, region_id, res, model_id, run_id, var_id, hours, values):
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    run_dir = base_dir / region_id / res_dir / model_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    arr = np.array(values, dtype=np.float32).reshape(len(hours), 1, 1)
    payload = {
        "hours": np.array(hours, dtype=np.int32),
        "means": arr,
        "mins": arr,
        "maxs": arr,
    }
    np.savez_compressed(run_dir / f"{var_id}.npz", **payload)

    meta_path = run_dir / f"{var_id}.meta.json"
    meta = {
        "region_id": region_id,
        "lat_min": 0.0,
        "lat_max": 1.0,
        "lon_min": 0.0,
        "lon_max": 1.0,
        "resolution_deg": res,
    }
    meta_path.write_text(json.dumps(meta))


def _configure_tiles(tmp_path, monkeypatch):
    from config import repomap
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path / "tiles"))
    monkeypatch.setitem(repomap, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setitem(
        repomap,
        "TILING_REGIONS",
        {
            "ne": {
                "name": "Northeast",
                "lat_min": 0.0,
                "lat_max": 1.0,
                "lon_min": 0.0,
                "lon_max": 1.0,
                "default_resolution_deg": 1.0,
            }
        },
    )

    return init_db(str(tmp_path / "jobs.db"))


def test_api_table_multirun_success(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"

    _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        [0, 1],
        [10.0, 12.0],
    )
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_20240101_00", "2024-01-01T00:00:00Z")
    record_tile_variable(
        conn,
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240101_00" / "t2m.npz"),
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240101_00" / "t2m.meta.json"),
        [0, 1],
        123,
    )
    _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0, 1],
        [11.0, 13.0],
    )
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_20240102_00", "2024-01-02T00:00:00Z")
    record_tile_variable(
        conn,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240102_00" / "t2m.npz"),
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240102_00" / "t2m.meta.json"),
        [0, 1],
        123,
    )
    conn.commit()
    conn.close()

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=2")
    assert response.status_code == 200
    data = response.get_json()

    assert set(data["runs"].keys()) == {"run_20240102_00", "run_20240101_00"}
    assert data["metadata"]["model_id"] == "hrrr"
    assert len(data["rows"]) == 4
    assert "run_20240102_00_t2m" in data["rows"][2]
    assert "run_20240101_00_t2m" in data["rows"][0]


def test_api_table_multirun_skips_invalid_run_id(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"

    _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_bad",
        "t2m",
        [0],
        [10.0],
    )
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_bad", None)
    record_tile_variable(
        conn,
        "ne",
        1.0,
        "hrrr",
        "run_bad",
        "t2m",
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_bad" / "t2m.npz"),
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_bad" / "t2m.meta.json"),
        [0],
        123,
    )
    _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0],
        [12.0],
    )
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_20240102_00", "2024-01-02T00:00:00Z")
    record_tile_variable(
        conn,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240102_00" / "t2m.npz"),
        str(tiles_dir / "ne" / "1deg" / "hrrr" / "run_20240102_00" / "t2m.meta.json"),
        [0],
        123,
    )
    conn.commit()
    conn.close()

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=2")
    assert response.status_code == 200
    data = response.get_json()
    assert "run_bad" not in data["runs"]
    assert "run_20240102_00" in data["runs"]


def test_api_table_multirun_validation_errors(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    conn.close()

    response = client.get("/api/table/multirun?lon=0.5&model=hrrr")
    assert response.status_code == 400
    assert response.get_json()["error"] == "lat and lon are required"

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=bad")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid model"

    response = client.get("/api/table/multirun?lat=2.0&lon=0.5&model=hrrr")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Point outside configured regions"

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&region=bad")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid region"

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=0")
    assert response.status_code == 400
    assert response.get_json()["error"] == "num_runs must be >= 1"

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=bad")
    assert response.status_code == 400
    assert response.get_json()["error"] == "num_runs must be an integer"


def test_api_table_multirun_no_runs(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    conn.close()

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 404
    assert response.get_json()["error"] == "No tile runs available"


def test_api_table_multirun_no_variables(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"
    res_dir = f"{1.0:.3f}deg".rstrip("0").rstrip(".")
    empty_run_dir = tiles_dir / "ne" / res_dir / "hrrr" / "run_20240101_00"
    empty_run_dir.mkdir(parents=True, exist_ok=True)
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_20240101_00", "2024-01-01T00:00:00Z")
    conn.commit()
    conn.close()

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 404
    assert response.get_json()["error"] == "No variables present in available tile runs"


def test_api_table_multirun_missing_metadata_skips_variable(client, tmp_path, monkeypatch):
    conn = _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"
    res_dir = f"{1.0:.3f}deg".rstrip("0").rstrip(".")
    run_dir = tiles_dir / "ne" / res_dir / "hrrr" / "run_20240101_00"
    run_dir.mkdir(parents=True, exist_ok=True)

    arr = np.array([1.0], dtype=np.float32).reshape(1, 1, 1)
    np.savez_compressed(
        run_dir / "t2m.npz",
        hours=np.array([0], dtype=np.int32),
        means=arr,
        mins=arr,
        maxs=arr,
    )
    record_tile_run(conn, "ne", 1.0, "hrrr", "run_20240101_00", "2024-01-01T00:00:00Z")
    record_tile_variable(
        conn,
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        str(run_dir / "t2m.npz"),
        str(run_dir / "t2m.meta.json"),
        [0],
        123,
    )
    conn.commit()
    conn.close()

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 200
    data = response.get_json()
    assert data["rows"] == []
