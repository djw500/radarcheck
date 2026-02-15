from jobs import enqueue
from tile_db import (
    init_db,
    list_tile_models_db,
    list_tile_runs_db,
    list_tile_variables_db,
    record_tile_run,
    record_tile_variable,
    record_tile_hour,
    delete_tile_run,
    delete_region_tiles,
)


def test_tile_db_records_and_lists(tmp_path):
    db_path = tmp_path / "tiles.db"
    conn = init_db(str(db_path))
    try:
        job_id = enqueue(conn, "build_tile", {"region": "ne"})
        record_tile_run(conn, "ne", 0.1, "hrrr", "run_20240101_00", "2024-01-01T00:00:00Z")
        record_tile_variable(
            conn,
            "ne",
            0.1,
            "hrrr",
            "run_20240101_00",
            "t2m",
            "tiles/path/t2m.npz",
            "tiles/path/t2m.meta.json",
            [0, 1, 2],
            123,
            job_id=job_id,
        )
        conn.commit()

        models = list_tile_models_db(conn, "ne", 0.1)
        assert models == {"hrrr": ["run_20240101_00"]}

        runs = list_tile_runs_db(conn, "ne", 0.1, "hrrr")
        assert runs == ["run_20240101_00"]

        variables = list_tile_variables_db(conn, "ne", 0.1, "hrrr", "run_20240101_00")
        assert variables["t2m"]["hours"] == [0, 1, 2]
        assert variables["t2m"]["file"] == "tiles/path/t2m.npz"

        # record_tile_variable no longer auto-completes jobs (worker owns that)
        job_status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert job_status["status"] == "pending"
    finally:
        conn.close()


def test_delete_tile_run(tmp_path):
    db_path = tmp_path / "tiles_del.db"
    conn = init_db(str(db_path))
    try:
        # Setup data
        region = "ne"
        res = 0.1
        model = "hrrr"
        run = "run_1"

        record_tile_run(conn, region, res, model, run, "2024-01-01T00:00:00Z")
        record_tile_variable(conn, region, res, model, run, "var1", "p1", "m1", [1], 100)
        record_tile_hour(conn, region, res, model, run, "var1", 1, "p1")

        # Verify existence
        assert list_tile_runs_db(conn, region, res, model) == [run]
        assert conn.execute("SELECT count(*) FROM tile_variables").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tile_hours").fetchone()[0] == 1

        # Delete
        delete_tile_run(conn, region, res, model, run)

        # Verify deletion
        assert list_tile_runs_db(conn, region, res, model) == []
        assert conn.execute("SELECT count(*) FROM tile_variables").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM tile_hours").fetchone()[0] == 0

    finally:
        conn.close()


def test_delete_region_tiles(tmp_path):
    db_path = tmp_path / "tiles_region_del.db"
    conn = init_db(str(db_path))
    try:
        # Setup data for two regions
        region1 = "ne"
        region2 = "sw"
        res = 0.1
        model = "hrrr"

        # Region 1
        record_tile_run(conn, region1, res, model, "run_1", "2024-01-01T00:00:00Z")
        record_tile_variable(conn, region1, res, model, "run_1", "var1", "p1", "m1", [1], 100)

        # Region 2
        record_tile_run(conn, region2, res, model, "run_1", "2024-01-01T00:00:00Z")
        record_tile_variable(conn, region2, res, model, "run_1", "var1", "p1", "m1", [1], 100)

        # Verify existence
        assert len(list_tile_runs_db(conn, region1, res, model)) == 1
        assert len(list_tile_runs_db(conn, region2, res, model)) == 1
        assert conn.execute("SELECT count(*) FROM tile_runs").fetchone()[0] == 2

        # Delete Region 1
        delete_region_tiles(conn, region1)

        # Verify deletion of region 1 and persistence of region 2
        assert len(list_tile_runs_db(conn, region1, res, model)) == 0
        assert len(list_tile_runs_db(conn, region2, res, model)) == 1
        assert conn.execute("SELECT count(*) FROM tile_runs").fetchone()[0] == 1

    finally:
        conn.close()
