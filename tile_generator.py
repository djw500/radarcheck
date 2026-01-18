from __future__ import annotations

import json
import logging
import os
from typing import Any

import mercantile
import numpy as np
import rioxarray  # noqa: F401
import xarray as xr
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
from rio_tiler.io import XarrayReader

from plotting import select_variable_from_dataset
from utils import convert_units

logger = logging.getLogger(__name__)


def grib_to_geotiff(grib_path: str, output_path: str, variable_config: dict[str, Any]) -> str:
    """Convert GRIB2 to Cloud-Optimized GeoTIFF for efficient tiling."""
    ds = xr.open_dataset(grib_path, engine="cfgrib")
    try:
        data = select_variable_from_dataset(ds, variable_config)
        conversion = variable_config.get("conversion")
        if conversion:
            data = convert_units(data, conversion)

        if not hasattr(data, "rio"):
            raise RuntimeError("rioxarray is required for GeoTIFF generation")

        profile = cog_profiles.get("deflate", {})

        cog_translate(
            data.rio.to_raster,
            output_path,
            profile,
            in_memory=True,
        )
        return output_path
    finally:
        ds.close()


def generate_tiles(
    geotiff_path: str,
    output_dir: str,
    variable_config: dict[str, Any],
    min_zoom: int = 4,
    max_zoom: int = 10,
) -> None:
    """Generate XYZ tile pyramid from GeoTIFF."""
    os.makedirs(output_dir, exist_ok=True)
    with XarrayReader(geotiff_path) as src:
        for zoom in range(min_zoom, max_zoom + 1):
            tiles = list(mercantile.tiles(*src.bounds, zooms=zoom))
            for tile in tiles:
                img, mask = src.tile(tile.x, tile.y, tile.z)
                tile_path = os.path.join(output_dir, str(zoom), str(tile.x), f"{tile.y}.png")
                os.makedirs(os.path.dirname(tile_path), exist_ok=True)
                img = np.moveaxis(img, 0, -1)
                if mask is not None:
                    img[..., 3] = mask
                img = img.astype(np.uint8)
                from PIL import Image

                Image.fromarray(img).save(tile_path)


def generate_vector_contours(grib_path: str, variable_config: dict[str, Any]) -> dict[str, Any]:
    """Generate GeoJSON contours for vector rendering."""
    import rasterio
    from rasterio import features

    ds = xr.open_dataset(grib_path, engine="cfgrib")
    try:
        data = select_variable_from_dataset(ds, variable_config)
        conversion = variable_config.get("conversion")
        if conversion:
            data = convert_units(data, conversion)

        vmin, vmax = variable_config.get("vmin", 0), variable_config.get("vmax", 1)
        levels = np.linspace(vmin, vmax, 10)

        contours = []
        for level in levels:
            mask = data.values >= level
            shapes = features.shapes(mask.astype(np.uint8), transform=data.rio.transform())
            for shape, value in shapes:
                if value == 1:
                    contours.append({
                        "type": "Feature",
                        "properties": {"value": float(level)},
                        "geometry": shape,
                    })

        return {"type": "FeatureCollection", "features": contours}
    finally:
        ds.close()


def save_geojson(payload: dict[str, Any], output_path: str) -> None:
    with open(output_path, "w") as f:
        json.dump(payload, f)
