"""Test that qualitative output includes raw HRRR data."""
import json
import types


def _make_fake_model_data():
    """Minimal model_data dict matching build_model_data() output."""
    return {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {
                "t2m": [53.2, 51.8, 49.5],
                "dpt": [35.1, 34.0, 33.2],
                "cloud_cover": [30.0, 25.0, 20.0],
                "dswrf": [400.0, 200.0, 0.0],
                "apcp": [0.0, 0.0, 0.01],
                "asnow": [0.0, 0.0, 0.0],
                "snod": [0.0, 0.0, 0.0],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T14:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {"t2m": [52.0, 50.5, 48.0]},
        },
    }


def test_build_raw_hrrr_from_model_data():
    """raw_hrrr should be a list of per-hour dicts from hrrr_latest."""
    from scripts.qualitative import build_raw_hrrr

    model_data = _make_fake_model_data()
    raw = build_raw_hrrr(model_data)

    assert raw is not None
    assert raw["init"] == "2026-03-11T15:00:00+00:00"
    assert len(raw["hours"]) == 3

    assert raw["hours"][0]["hour"] == "5pm"
    assert raw["hours"][0]["t2m"] == 53.2
    assert raw["hours"][0]["cloud_cover"] == 30.0
    assert raw["hours"][0]["apcp"] == 0.0

    assert raw["hours"][2]["hour"] == "7pm"
    assert raw["hours"][2]["t2m"] == 49.5


def test_build_raw_hrrr_missing_hrrr():
    """Should return None if no hrrr_latest in model_data."""
    from scripts.qualitative import build_raw_hrrr

    raw = build_raw_hrrr({"gfs": {"init": "x", "hours": [], "data": {}}})
    assert raw is None


def test_build_raw_hrrr_stitches_synoptic():
    """Should fill nulls from hrrr_previous (synoptic run)."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {
                "t2m": [53.2, None, None],
                "dpt": [35.1, None, None],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T12:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {
                "t2m": [52.0, 50.5, 48.0],
                "dpt": [34.0, 33.0, 32.0],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # Hour 0: latest has data, use it
    assert raw["hours"][0]["t2m"] == 53.2
    assert "_stitched" not in raw["hours"][0]

    # Hours 1-2: latest is null, filled from previous
    assert raw["hours"][1]["t2m"] == 50.5
    assert raw["hours"][1]["_stitched"] is True
    assert raw["hours"][2]["t2m"] == 48.0

    assert raw["synoptic_init"] == "2026-03-11T12:00:00+00:00"


def test_build_raw_hrrr_deaccumulates_precip():
    """apcp/asnow should be per-hour increments, not cumulative totals."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [53.2, 51.8, 49.5, 48.0],
                "apcp": [0.0, 0.1, 0.3, 0.5],
                "asnow": [0.0, 0.0, 0.1, 0.3],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # Per-hour increments, not cumulative
    assert raw["hours"][0]["apcp"] == 0.0
    assert raw["hours"][1]["apcp"] == 0.1
    assert raw["hours"][2]["apcp"] == 0.2
    assert raw["hours"][3]["apcp"] == 0.2

    assert raw["hours"][2]["asnow"] == 0.1
    assert raw["hours"][3]["asnow"] == 0.2


def test_build_raw_hrrr_deaccum_resets_at_stitch_boundary():
    """De-accumulation must NOT diff across the latest→stitched boundary."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [53.2, 51.8, None, None],
                "apcp": [0.0, 0.1, None, None],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T12:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [52.0, 50.5, 48.0, 46.0],
                "apcp": [0.0, 0.2, 0.5, 0.9],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # Latest hours: de-accumulated normally
    assert raw["hours"][0]["apcp"] == 0.0
    assert raw["hours"][1]["apcp"] == 0.1

    # Stitched hours: de-accumulated within their own sequence (NOT diffed against latest)
    assert raw["hours"][2]["apcp"] == 0.5   # First stitched value: keep as-is
    assert raw["hours"][3]["apcp"] == 0.4   # 0.9 - 0.5 = 0.4


def test_build_raw_hrrr_precip_2_decimals():
    """Precip should preserve 2 decimal places, not round to 1."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm"],
            "data": {
                "t2m": [53.2, 51.8],
                "apcp": [0.0, 0.04],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # 0.04 should NOT round to 0.0
    assert raw["hours"][1]["apcp"] == 0.04


def test_build_raw_hrrr_includes_storm_vars():
    """Wind, gust, and refc should appear in raw HRRR output."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {
                "t2m": [53.2, 51.8, 49.5],
                "wind_10m": [8.0, 12.0, 15.0],
                "gust": [15.0, 22.0, 28.0],
                "refc": [0.0, 25.0, 40.0],
                "apcp": [0.0, 0.0, 0.01],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    assert raw["hours"][0]["wind_10m"] == 8.0
    assert raw["hours"][1]["gust"] == 22.0
    assert raw["hours"][2]["refc"] == 40.0
