# Summer Variables: Dew Point + Solar Radiation

## Variables

### Dew Point (`dpt`)
- All 5 models
- Search: `:DPT:2 m above ground`, ECMWF override `:2d:`
- Conversion: KToF default, CToF for ECMWF via `conversion_overrides`
- Units: °F
- Resolution: 0.25° (smooth field, coarse is fine)

### Downward Shortwave Radiation (`dswrf`)
- 4 models (exclude ECMWF — uses accumulated J/m², not instantaneous W/m²)
- Search: `:DSWRF:surface` (uniform across GFS/HRRR/NAM/NBM)
- Conversion: None (native W/m²)
- Units: W/m²
- Resolution: 0.25° (smooth field)

## Implementation

Same pattern as cloud_cover. Changes across 5-6 files:

1. **Rust config** — Add `variable_resolution_override: Option<f64>` to VariableConfig, add dpt + dswrf variables, update TILE_BUILD_VARIABLE_IDS
2. **Rust worker** — Check variable resolution override before model resolution
3. **Python config** — Add dswrf (dpt already exists)
4. **Scheduler + dev-services** — Add dpt,dswrf to defaults
5. **UI** — Two buttons (Dew Pt, Solar) + VAR_CONFIG entries

## Disk Impact

~30 MB total at 0.25° resolution. Negligible.
