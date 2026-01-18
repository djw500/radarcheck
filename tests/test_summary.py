import json

from summary import summarize_run


def write_center_values(path, values, units):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(
            {
                "units": units,
                "values": [{"forecast_hour": i + 1, "value": value} for i, value in enumerate(values)],
            },
            f,
        )


def test_summarize_run_builds_metrics(tmp_path):
    cache_dir = tmp_path / "cache"
    location_id = "philly"
    model_id = "hrrr"
    run_id = "run_20240101_00"

    write_center_values(
        cache_dir / location_id / model_id / run_id / "asnow" / "center_values.json",
        [0.0, 1.5, 2.0],
        "in",
    )
    write_center_values(
        cache_dir / location_id / model_id / run_id / "apcp" / "center_values.json",
        [0.2, 0.4, 0.9],
        "in",
    )
    write_center_values(
        cache_dir / location_id / model_id / run_id / "gust" / "center_values.json",
        [10, 22, 18],
        "mph",
    )
    write_center_values(
        cache_dir / location_id / model_id / run_id / "t2m" / "center_values.json",
        [30, 28, 35],
        "°F",
    )

    summary = summarize_run(str(cache_dir), location_id, model_id, run_id)
    assert summary["summary"]["total_snowfall_inches"] == 2.0
    assert summary["summary"]["total_precipitation_inches"] == 0.9
    assert summary["summary"]["max_wind_gust_mph"] == 22
    assert summary["summary"]["temperature_range_f"]["min"] == 28
    assert summary["summary"]["temperature_range_f"]["max"] == 35
