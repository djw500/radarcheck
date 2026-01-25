from cache_builder import get_valid_forecast_hours


def test_gfs_forecast_hour_schedule_basic():
    # GFS should provide 3-hourly forecasts up to 240h, then 6-hourly
    hours_24 = get_valid_forecast_hours("gfs", 24)
    # Starts at 3h and increments by 3
    assert hours_24 == [3, 6, 9, 12, 15, 18, 21, 24]

    hours_246 = get_valid_forecast_hours("gfs", 246)
    # Ensure 241-245 are not included, but 246 is
    assert 241 not in hours_246 and 242 not in hours_246 and 243 not in hours_246
    assert 244 not in hours_246 and 245 not in hours_246
    assert 246 in hours_246


def test_nbm_forecast_hour_schedule_segments():
    hours_48 = get_valid_forecast_hours("nbm", 48)
    # NBM hourly 1-36, then 42 and 48 present (6-hourly starts at 42)
    assert 1 in hours_48 and 36 in hours_48
    assert 37 not in hours_48 and 39 not in hours_48
    assert 42 in hours_48 and 48 in hours_48

