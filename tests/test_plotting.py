import numpy as np
import xarray as xr

from plotting import get_colormap, select_variable_from_dataset


def test_select_variable_from_dataset_prefers_short_name():
    ds = xr.Dataset({"refc": ("x", np.array([1, 2, 3]))})
    variable_config = {"short_name": "refc"}
    result = select_variable_from_dataset(ds, variable_config)
    assert result.name == "refc"


def test_get_colormap_returns_nws_for_reflectivity():
    config = {"colormap": "nws_reflectivity"}
    cmap = get_colormap(config)
    assert cmap.name == "radar"
