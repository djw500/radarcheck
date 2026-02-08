from jobs import enqueue
from tile_db import (
    init_db,
    list_tile_models_db,
    list_tile_runs_db,
    list_tile_variables_db,
    record_tile_run,
    record_tile_variable,
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

        job_status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert job_status["status"] == "completed"
    finally:
        conn.close()
