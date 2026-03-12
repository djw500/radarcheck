"""Tests for build_latest_table — unified best-available forecast."""
import datetime


def _make_model_data():
    """Model data with HRRR latest (partial), HRRR previous, GFS, NBM, GFS extended."""
    return {
        "hrrr_latest": {
            "init": "2026-03-11T23:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [60.0, 58.0, None, None],
                "gust": [15.0, 12.0, None, None],
                "apcp": [0.0, 0.1, None, None],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T22:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [59.0, 57.0, 55.0, None],
                "gust": [14.0, 11.0, 10.0, None],
                "apcp": [0.0, 0.15, 0.3, None],
            },
        },
        "gfs": {
            "init": "2026-03-11T18:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [58.0, 56.0, 54.0, 52.0],
                "gust": [13.0, 10.0, 9.0, 8.0],
                "apcp": [0.0, 0.2, 0.5, 0.9],
            },
        },
        "nbm": {
            "init": "2026-03-11T22:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "apcp": [0.0, 0.05, 0.12, 0.2],
            },
        },
        "gfs_extended": {
            "init": "2026-03-11T18:00:00+00:00",
            "hours": ["sat 6am", "sat 12pm", "sat 6pm", "sun 12am", "sun 6am", "sun 12pm"],
            "isos": [
                "2026-03-14T11:00:00", "2026-03-14T17:00:00", "2026-03-14T23:00:00",
                "2026-03-15T05:00:00", "2026-03-15T11:00:00", "2026-03-15T17:00:00",
            ],
            "data": {
                "t2m": [35.0, 45.0, 40.0, 30.0, 33.0, 44.0],
                "apcp": [0.1, 0.3, 0.5, 0.5, 0.6, 0.8],
                "gust": [20.0, 15.0, 18.0, 10.0, 12.0, 14.0],
            },
        },
    }


def test_latest_table_priority():
    """Hour 1-2 use HRRR latest, hour 3 uses HRRR previous, hour 4 uses GFS."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    assert len(hourly) == 4

    # Hours 1-2: HRRR latest has data
    assert hourly[0]["source"].startswith("HRRR")
    assert hourly[0]["t2m"] == 60.0
    assert hourly[1]["t2m"] == 58.0

    # Hour 3: HRRR latest is None, falls to previous
    assert hourly[2]["source"].startswith("HRRR")
    assert hourly[2]["t2m"] == 55.0

    # Hour 4: both HRRR runs are None, falls to GFS
    assert hourly[3]["source"].startswith("GFS")
    assert hourly[3]["t2m"] == 52.0


def test_latest_table_source_labels():
    """Source labels should be 'MODEL Xpm' format with Eastern time."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    # HRRR latest init 23:00 UTC = 7pm EDT (DST active March 11)
    assert hourly[0]["source"] == "HRRR 7pm"
    # HRRR previous init 22:00 UTC = 6pm EDT
    assert hourly[2]["source"] == "HRRR 6pm"
    # GFS init 18:00 UTC = 2pm EDT
    assert hourly[3]["source"] == "GFS 2pm"


def test_latest_table_deaccumulates_precip():
    """Precip should be per-hour increments, resetting at source boundaries."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    # Hour 1: first value from HRRR latest
    assert hourly[0]["apcp"] == 0.0
    # Hour 2: diff within HRRR latest (0.1 - 0.0)
    assert hourly[1]["apcp"] == 0.1
    # Hour 3: first value from HRRR previous (source changed — no diff)
    assert hourly[2]["apcp"] == 0.3
    # Hour 4: first value from GFS (source changed — no diff)
    assert hourly[3]["apcp"] == 0.9


def test_latest_table_nbm_precip():
    """NBM precip should appear as separate nbm_apcp column, de-accumulated."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    assert hourly[0]["nbm_apcp"] == 0.0
    assert hourly[1]["nbm_apcp"] == 0.05
    assert hourly[2]["nbm_apcp"] == 0.07  # 0.12 - 0.05
    assert hourly[3]["nbm_apcp"] == 0.08  # 0.2 - 0.12


def test_latest_table_daily_aggregation():
    """Daily rows should have min/max/avg per variable."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    daily = result["daily"]

    assert len(daily) >= 1

    # First day (Sat) has 3 points: 35, 45, 40
    sat = daily[0]
    assert sat["day"].startswith("Sat")
    assert sat["source"].startswith("GFS")
    assert sat["t2m"]["min"] == 35.0
    assert sat["t2m"]["max"] == 45.0
    assert sat["t2m"]["avg"] == 40.0  # mean

    # Precip: sum of increments (0.1, 0.2, 0.2) = 0.5
    assert sat["apcp"]["avg"] == 0.5


def test_latest_table_no_data():
    """Should return empty hourly/daily if no model data."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table({})
    assert result["hourly"] == []
    assert result["daily"] == []
