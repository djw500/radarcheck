import json
import numpy as np
import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
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

    meta = {
        "region_id": region_id,
        "lat_min": 0.0,
        "lat_max": 1.0,
        "lon_min": 0.0,
        "lon_max": 1.0,
        "resolution_deg": res,
    }
    meta_path = run_dir / f"{var_id}.meta.json"
    with open(meta_path, "w") as handle:
        json.dump(meta, handle)
    return meta, str(run_dir / f"{var_id}.npz"), str(meta_path)


def _configure_tiles(tmp_path, monkeypatch):
    from config import repomap
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path / "tiles"))
    monkeypatch.setitem(repomap, "TILES_DB_PATH", str(tmp_path / "tiles.db"))
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


def test_api_table_multirun_success(client, tmp_path, monkeypatch):
    _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"

    from tiles_db import record_tile_run, record_tile_variable

    meta, npz_path, meta_path = _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        [0, 1],
        [10.0, 12.0],
    )
    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_20240101_00", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        [0, 1],
        None,
        npz_path,
        meta_path,
        meta,
    )

    meta, npz_path, meta_path = _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0, 1],
        [11.0, 13.0],
    )

    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_20240102_00", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0, 1],
        None,
        npz_path,
        meta_path,
        meta,
    )

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=2")
    assert response.status_code == 200
    data = response.get_json()

    assert set(data["runs"].keys()) == {"run_20240102_00", "run_20240101_00"}
    assert data["metadata"]["model_id"] == "hrrr"
    assert len(data["rows"]) == 4
    assert "run_20240102_00_t2m" in data["rows"][2]
    assert "run_20240101_00_t2m" in data["rows"][0]


def test_api_table_multirun_skips_invalid_run_id(client, tmp_path, monkeypatch):
    _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"

    from tiles_db import record_tile_run, record_tile_variable

    meta, npz_path, meta_path = _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_bad",
        "t2m",
        [0],
        [10.0],
    )
    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_bad", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        1.0,
        "hrrr",
        "run_bad",
        "t2m",
        [0],
        None,
        npz_path,
        meta_path,
        meta,
    )

    meta, npz_path, meta_path = _write_tile_run(
        tiles_dir,
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0],
        [12.0],
    )

    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_20240102_00", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        1.0,
        "hrrr",
        "run_20240102_00",
        "t2m",
        [0],
        None,
        npz_path,
        meta_path,
        meta,
    )

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr&num_runs=2")
    assert response.status_code == 200
    data = response.get_json()
    assert "run_bad" not in data["runs"]
    assert "run_20240102_00" in data["runs"]


def test_api_table_multirun_validation_errors(client, tmp_path, monkeypatch):
    _configure_tiles(tmp_path, monkeypatch)

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
    _configure_tiles(tmp_path, monkeypatch)

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 404
    assert response.get_json()["error"] == "No tile runs available"


def test_api_table_multirun_no_variables(client, tmp_path, monkeypatch):
    _configure_tiles(tmp_path, monkeypatch)
    tiles_dir = tmp_path / "tiles"
    res_dir = f"{1.0:.3f}deg".rstrip("0").rstrip(".")
    empty_run_dir = tiles_dir / "ne" / res_dir / "hrrr" / "run_20240101_00"
    empty_run_dir.mkdir(parents=True, exist_ok=True)
    from tiles_db import record_tile_run
    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_20240101_00", None)

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 404
    assert response.get_json()["error"] == "No variables present in available tile runs"


def test_api_table_multirun_missing_metadata_skips_variable(client, tmp_path, monkeypatch):
    _configure_tiles(tmp_path, monkeypatch)
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
    from tiles_db import record_tile_run, record_tile_variable
    record_tile_run(str(tmp_path / "tiles.db"), "ne", 1.0, "hrrr", "run_20240101_00", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        1.0,
        "hrrr",
        "run_20240101_00",
        "t2m",
        [0],
        None,
        str(run_dir / "t2m.npz"),
        str(run_dir / "t2m.meta.json"),
        None,
    )

    response = client.get("/api/table/multirun?lat=0.5&lon=0.5&model=hrrr")
    assert response.status_code == 200
    data = response.get_json()
    assert data["rows"] == []
