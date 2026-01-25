import types

import cache_builder as cb


def test_download_all_hours_schedules_every_expected_hour(monkeypatch):
    calls = []

    def fake_fetch_grib(model_id, variable_id, date_str, init_hour, forecast_hour, run_id, location_config=None, use_hourly=False):
        calls.append(int(forecast_hour))
        # Simulate 404 for an hour that isn't a multiple of 3/6 depending on model schedule
        # but our get_valid_forecast_hours will only pass valid ones; just return a fake path
        return f"/fake/{model_id}/{variable_id}/{run_id}/grib_{forecast_hour}.grib2"

    monkeypatch.setattr(cb, "fetch_grib", fake_fetch_grib)

    # Ensure GFS schedule is as configured (3-hourly up to 240, then 6-hourly)
    hours = cb.get_valid_forecast_hours("gfs", 30)
    # Expect [3,6,9,...,30]
    expected = list(range(3, 31, 3))
    assert hours == expected

    res = cb.download_all_hours_parallel(
        model_id="gfs",
        variable_id="t2m",
        date_str="20260101",
        init_hour="12",
        run_id="run_20260101_12",
        max_hours=30,
    )

    # Every expected hour should have been attempted
    assert sorted(calls) == expected
    assert sorted(res.keys()) == expected


def test_nbm_schedule_covers_hourly_then_six_hourly(monkeypatch):
    calls = []

    def fake_fetch_grib(model_id, variable_id, date_str, init_hour, forecast_hour, run_id, location_config=None, use_hourly=False):
        calls.append(int(forecast_hour))
        return f"/fake/{model_id}/{variable_id}/{run_id}/grib_{forecast_hour}.grib2"

    monkeypatch.setattr(cb, "fetch_grib", fake_fetch_grib)

    hours = cb.get_valid_forecast_hours("nbm", 48)
    assert 1 in hours and 36 in hours and 42 in hours and 48 in hours

    _ = cb.download_all_hours_parallel(
        model_id="nbm",
        variable_id="t2m",
        date_str="20260101",
        init_hour="12",
        run_id="run_20260101_12",
        max_hours=48,
    )

    # Ensure we attempted exactly those expected steps
    assert sorted(calls) == hours
