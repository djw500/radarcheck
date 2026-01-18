from config import MODELS, WEATHER_VARIABLES


def test_all_variables_have_required_fields():
    required = ["nomads_params", "display_name", "units", "colormap", "vmin", "vmax", "category"]
    for var_id, var_config in WEATHER_VARIABLES.items():
        for field in required:
            assert field in var_config, f"{var_id} missing {field}"


def test_all_models_have_required_fields():
    required = ["name", "max_forecast_hours", "nomads_url", "dir_pattern", "file_pattern"]
    for model_id, model_config in MODELS.items():
        for field in required:
            assert field in model_config, f"{model_id} missing {field}"
