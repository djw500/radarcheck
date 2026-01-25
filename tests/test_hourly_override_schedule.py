import cache_builder as cb


def test_get_run_forecast_hours_no_override(monkeypatch):
    # Force detection to False
    monkeypatch.setattr(cb, "detect_hourly_support", lambda *a, **k: False)
    hours = cb.get_run_forecast_hours("gfs", "20260101", "12", max_hours=30)
    # Should fall back to base schedule (3-hourly)
    assert hours == list(range(3, 31, 3))


def test_get_run_forecast_hours_with_override(monkeypatch):
    # Force detection to True
    monkeypatch.setattr(cb, "detect_hourly_support", lambda *a, **k: True)
    # For max_hours 30 and override 48, expect 1..30 hourly
    hours = cb.get_run_forecast_hours("gfs", "20260101", "12", max_hours=30)
    assert hours == list(range(1, 31))

