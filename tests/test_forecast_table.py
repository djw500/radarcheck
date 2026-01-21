"""
Programmatic integrity tests for forecast_table.py.

These tests validate that the table generation produces
correct structure and values without requiring real cache data.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from forecast_table import (
    build_forecast_table,
    format_table_html,
    format_table_json,
    format_table_terminal,
    format_value,
    get_latest_run_id,
    get_variable_display_order,
    load_all_center_values,
    load_center_values_for_variable,
)
from config import WEATHER_VARIABLES


class TestFormatValue:
    """Test value formatting with units."""

    def test_format_none_value(self):
        """None values should return dash."""
        assert format_value(None, {"units": "dBZ"}) == "-"

    def test_format_dbz(self):
        """dBZ values should be integers."""
        assert format_value(35.4, {"units": "dBZ"}) == "35 dBZ"

    def test_format_temperature(self):
        """Temperature should have 1 decimal place."""
        assert format_value(45.67, {"units": "°F"}) == "45.7 °F"

    def test_format_precipitation(self):
        """Precipitation should have 2 decimal places."""
        assert format_value(0.123, {"units": "in"}) == "0.12 in"

    def test_format_wind_speed(self):
        """Wind speed should have 1 decimal place."""
        assert format_value(12.34, {"units": "mph"}) == "12.3 mph"

    def test_format_without_units(self):
        """Should work without include_units flag."""
        assert format_value(35.4, {"units": "dBZ"}, include_units=False) == "35"


class TestVariableDisplayOrder:
    """Test variable ordering for display."""

    def test_returns_list(self):
        """Should return a list."""
        order = get_variable_display_order()
        assert isinstance(order, list)

    def test_contains_all_variables(self):
        """Should contain all defined variables."""
        order = get_variable_display_order()
        for var_id in WEATHER_VARIABLES:
            assert var_id in order, f"Missing variable: {var_id}"

    def test_temperature_variables_grouped(self):
        """Temperature variables should be grouped together."""
        order = get_variable_display_order()
        temp_vars = [v for v in order if WEATHER_VARIABLES.get(v, {}).get("category") == "temperature"]
        if len(temp_vars) > 1:
            indices = [order.index(v) for v in temp_vars]
            # Check they are contiguous
            assert max(indices) - min(indices) + 1 == len(temp_vars)


class TestBuildForecastTable:
    """Test table construction from data."""

    def test_empty_data_returns_empty_list(self):
        """Empty data should return empty list."""
        assert build_forecast_table({}) == []
        assert build_forecast_table({"variables": {}}) == []

    def test_table_has_hour_column(self):
        """Each row should have hour field."""
        data = {
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        assert len(rows) == 1
        assert rows[0]["hour"] == 1

    def test_table_has_valid_time_column(self):
        """Each row should have valid_time field."""
        data = {
            "variables": {
                "t2m": {
                    "config": {"units": "°F"},
                    "values": {1: {"value": 45, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        assert rows[0]["valid_time"] == "2024-01-20T13:00:00"

    def test_table_rows_sorted_by_hour(self):
        """Rows should be sorted by forecast hour."""
        data = {
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {
                        3: {"value": 30, "valid_time": "2024-01-20T15:00:00"},
                        1: {"value": 35, "valid_time": "2024-01-20T13:00:00"},
                        2: {"value": 25, "valid_time": "2024-01-20T14:00:00"},
                    }
                }
            }
        }
        rows = build_forecast_table(data)
        hours = [r["hour"] for r in rows]
        assert hours == [1, 2, 3]

    def test_multiple_variables_in_rows(self):
        """Each row should have all variables."""
        data = {
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                },
                "t2m": {
                    "config": {"units": "°F"},
                    "values": {1: {"value": 45, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        assert "refc" in rows[0]
        assert "t2m" in rows[0]

    def test_missing_variable_for_hour_shows_dash(self):
        """Missing values should show as dash."""
        data = {
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {
                        1: {"value": 35, "valid_time": "2024-01-20T13:00:00"},
                        2: {"value": 30, "valid_time": "2024-01-20T14:00:00"},
                    }
                },
                "t2m": {
                    "config": {"units": "°F"},
                    "values": {1: {"value": 45, "valid_time": "2024-01-20T13:00:00"}}
                    # No value for hour 2
                }
            }
        }
        rows = build_forecast_table(data)
        assert len(rows) == 2
        assert rows[1]["t2m"] == "-"


class TestFormatTableJson:
    """Test JSON output formatting."""

    def test_json_is_valid(self):
        """Output should be valid JSON."""
        data = {
            "metadata": {"location_id": "philly", "model_id": "hrrr"},
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        output = format_table_json(data, rows)

        parsed = json.loads(output)
        assert "metadata" in parsed
        assert "columns" in parsed
        assert "rows" in parsed

    def test_json_contains_metadata(self):
        """JSON should preserve metadata."""
        data = {
            "metadata": {"location_id": "boston", "model_id": "hrrr", "run_id": "run_20240120_12"},
            "variables": {}
        }
        output = format_table_json(data, [])
        parsed = json.loads(output)

        assert parsed["metadata"]["location_id"] == "boston"
        assert parsed["metadata"]["model_id"] == "hrrr"

    def test_json_columns_match_variables(self):
        """JSON columns should list available variables."""
        data = {
            "metadata": {},
            "variables": {
                "refc": {"config": {"units": "dBZ"}, "values": {}},
                "t2m": {"config": {"units": "°F"}, "values": {}},
            }
        }
        output = format_table_json(data, [])
        parsed = json.loads(output)

        # Should have hour, valid_time, plus the variables
        assert "hour" in parsed["columns"]
        assert "valid_time" in parsed["columns"]


class TestFormatTableHtml:
    """Test HTML output formatting."""

    def test_html_has_doctype(self):
        """HTML should start with doctype."""
        output = format_table_html({"metadata": {}, "variables": {}}, [])
        assert output.startswith("<!DOCTYPE html>") or "<p>No data" in output

    def test_html_has_table_element(self):
        """HTML should contain table element when data present."""
        data = {
            "metadata": {"location_id": "philly"},
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        output = format_table_html(data, rows)

        assert "<table>" in output
        assert "</table>" in output

    def test_html_has_header_row(self):
        """HTML should have thead with headers."""
        data = {
            "metadata": {},
            "variables": {
                "t2m": {
                    "config": {"units": "°F", "display_name": "2m Temperature"},
                    "values": {1: {"value": 45, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        output = format_table_html(data, rows)

        assert "<thead>" in output
        assert "<th>Hour</th>" in output

    def test_html_no_data_message(self):
        """Empty data should show message."""
        output = format_table_html({"metadata": {}, "variables": {}}, [])
        assert "No data available" in output


class TestFormatTableTerminal:
    """Test terminal output formatting."""

    def test_terminal_has_header_info(self):
        """Terminal output should show metadata."""
        data = {
            "metadata": {"location_id": "nyc", "model_id": "hrrr", "run_id": "run_20240120_12"},
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        output = format_table_terminal(data, rows)

        assert "Location: nyc" in output
        assert "Model: hrrr" in output

    def test_terminal_has_separator_line(self):
        """Terminal should have separator between header and data."""
        data = {
            "metadata": {},
            "variables": {
                "refc": {
                    "config": {"units": "dBZ"},
                    "values": {1: {"value": 35, "valid_time": "2024-01-20T13:00:00"}}
                }
            }
        }
        rows = build_forecast_table(data)
        output = format_table_terminal(data, rows)

        assert "-+-" in output or "---" in output

    def test_terminal_no_data_message(self):
        """Empty data should show message."""
        output = format_table_terminal({"metadata": {}, "variables": {}}, [])
        assert "No data available" in output


class TestLoadCenterValuesForVariable:
    """Test loading center values from filesystem."""

    def test_load_from_new_structure(self, tmp_path):
        """Should load from new path structure."""
        # Create directory structure: cache/location/model/run/variable/center_values.json
        var_dir = tmp_path / "test-loc" / "hrrr" / "run_20240120_12" / "refc"
        var_dir.mkdir(parents=True)

        values = {
            "location_id": "test-loc",
            "variable_id": "refc",
            "values": [{"forecast_hour": 1, "value": 35.0}]
        }
        (var_dir / "center_values.json").write_text(json.dumps(values))

        result = load_center_values_for_variable(
            str(tmp_path), "test-loc", "hrrr", "run_20240120_12", "refc"
        )

        assert result is not None
        assert result["variable_id"] == "refc"
        assert len(result["values"]) == 1

    def test_returns_none_for_missing(self, tmp_path):
        """Should return None when file doesn't exist."""
        result = load_center_values_for_variable(
            str(tmp_path), "missing", "hrrr", "run_20240120_12", "refc"
        )
        assert result is None


class TestGetLatestRunId:
    """Test finding latest run from cache."""

    def test_returns_none_for_empty_cache(self, tmp_path):
        """Should return None when no runs exist."""
        result = get_latest_run_id(str(tmp_path), "philly", "hrrr")
        assert result is None

    def test_finds_latest_run_alphabetically(self, tmp_path):
        """Should return most recent run (alphabetically last)."""
        model_dir = tmp_path / "philly" / "hrrr"
        (model_dir / "run_20240119_12").mkdir(parents=True)
        (model_dir / "run_20240120_06").mkdir(parents=True)
        (model_dir / "run_20240120_12").mkdir(parents=True)

        result = get_latest_run_id(str(tmp_path), "philly", "hrrr")
        assert result == "run_20240120_12"

    def test_follows_latest_symlink(self, tmp_path):
        """Should use latest symlink when present."""
        model_dir = tmp_path / "philly" / "hrrr"
        run_dir = model_dir / "run_20240120_06"
        run_dir.mkdir(parents=True)

        latest_link = model_dir / "latest"
        latest_link.symlink_to("run_20240120_06")

        result = get_latest_run_id(str(tmp_path), "philly", "hrrr")
        assert result == "run_20240120_06"


class TestLoadAllCenterValues:
    """Test loading all variables for a location."""

    def test_returns_empty_for_missing_location(self, tmp_path):
        """Should return empty result for non-existent location."""
        result = load_all_center_values(str(tmp_path), "missing", "hrrr")
        assert result["variables"] == {}

    def test_loads_multiple_variables(self, tmp_path):
        """Should load all available variable files."""
        run_dir = tmp_path / "test-loc" / "hrrr" / "run_20240120_12"

        # Create refc data
        refc_dir = run_dir / "refc"
        refc_dir.mkdir(parents=True)
        (refc_dir / "center_values.json").write_text(json.dumps({
            "values": [{"forecast_hour": 1, "value": 35.0, "valid_time": "2024-01-20T13:00:00"}]
        }))

        # Create t2m data
        t2m_dir = run_dir / "t2m"
        t2m_dir.mkdir(parents=True)
        (t2m_dir / "center_values.json").write_text(json.dumps({
            "values": [{"forecast_hour": 1, "value": 45.0, "valid_time": "2024-01-20T13:00:00"}]
        }))

        result = load_all_center_values(
            str(tmp_path), "test-loc", "hrrr", "run_20240120_12"
        )

        assert "refc" in result["variables"]
        assert "t2m" in result["variables"]


class TestTableIntegrity:
    """Integration tests for complete table generation."""

    def test_full_pipeline_with_mock_data(self, tmp_path):
        """Test complete flow from cache to formatted output."""
        # Set up mock cache
        run_dir = tmp_path / "philly" / "hrrr" / "run_20240120_12"

        for var_id in ["refc", "t2m", "apcp"]:
            var_dir = run_dir / var_id
            var_dir.mkdir(parents=True)

            values = {
                "location_id": "philly",
                "model_id": "hrrr",
                "run_id": "run_20240120_12",
                "variable_id": var_id,
                "init_time": "2024-01-20T12:00:00",
                "values": [
                    {"forecast_hour": h, "value": float(h * 10), "valid_time": f"2024-01-20T{12+h:02d}:00:00"}
                    for h in range(1, 25)
                ]
            }
            (var_dir / "center_values.json").write_text(json.dumps(values))

        # Load and build table
        data = load_all_center_values(str(tmp_path), "philly", "hrrr", "run_20240120_12")
        rows = build_forecast_table(data)

        # Verify table structure
        assert len(rows) == 24, "Should have 24 forecast hours"
        assert all("hour" in r for r in rows), "Each row should have hour"
        assert all("valid_time" in r for r in rows), "Each row should have valid_time"
        assert all("refc" in r for r in rows), "Each row should have refc"
        assert all("t2m" in r for r in rows), "Each row should have t2m"
        assert all("apcp" in r for r in rows), "Each row should have apcp"

        # Verify all formats work
        terminal_output = format_table_terminal(data, rows)
        assert len(terminal_output) > 100, "Terminal output should have content"

        html_output = format_table_html(data, rows)
        assert "<table>" in html_output
        assert "</table>" in html_output

        json_output = format_table_json(data, rows)
        parsed = json.loads(json_output)
        assert len(parsed["rows"]) == 24

    def test_handles_sparse_data(self, tmp_path):
        """Test with variables having different hour coverage."""
        run_dir = tmp_path / "test-loc" / "hrrr" / "run_20240120_12"

        # refc has hours 1-24
        refc_dir = run_dir / "refc"
        refc_dir.mkdir(parents=True)
        (refc_dir / "center_values.json").write_text(json.dumps({
            "values": [{"forecast_hour": h, "value": float(h)} for h in range(1, 25)]
        }))

        # t2m only has hours 1-12
        t2m_dir = run_dir / "t2m"
        t2m_dir.mkdir(parents=True)
        (t2m_dir / "center_values.json").write_text(json.dumps({
            "values": [{"forecast_hour": h, "value": float(h * 10)} for h in range(1, 13)]
        }))

        data = load_all_center_values(str(tmp_path), "test-loc", "hrrr", "run_20240120_12")
        rows = build_forecast_table(data)

        # Should have all 24 hours (union of all variables)
        assert len(rows) == 24

        # t2m should be "-" for hours 13-24
        for row in rows:
            if row["hour"] > 12:
                assert row["t2m"] == "-"
