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
