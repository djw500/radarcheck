# rctile v2 Implementation Plan

Single-agent sequential implementation. ~1100 lines of Rust across 6 steps.

## Dependency Graph

```
  [1: config]──┐
               ├──[4: worker accumulator]──┐
  [2: mapping]─┘                           │
                                           ├──[6: cleanup v1]
  [3: v2 format]──[5: server reader]───────┘
```

## Step 1: GFS Resolution Config (5 min)

**Files**: `rust_worker/crates/core/src/config.rs`

- Change GFS `tile_resolution_deg` from `0.1` to `0.25`
- Verify `format_res_dir(0.25)` → `"0.250deg"` (check for rounding issues)
- `cargo test -p radarcheck-core` — ensure config tests pass

**Why first**: Unblocks step 2 (mapping needs correct resolution) and is trivial.

---

## Step 2: BucketMapping (~300 lines, 45 min)

**Files**:
- `rust_worker/crates/core/src/bucket_mapping.rs` — **NEW**
- `rust_worker/crates/core/src/lib.rs` — add `pub mod bucket_mapping;`
- `rust_worker/crates/core/Cargo.toml` — add `smallvec = "1"`

### 2a: Data structures

```rust
use smallvec::SmallVec;

/// For each tile bucket: which GRIB flat indices map to it and their weights.
pub struct BucketMapping {
    pub entries: Vec<SmallVec<[(usize, f32); 2]>>,
    pub ny: usize,
    pub nx: usize,
}
```

### 2b: Build from regular lat/lon grid (GFS, ECMWF, NBM)

```rust
pub fn build_regular(
    grib_lats: &[f64],      // 1D, sorted
    grib_lons: &[f64],      // 1D, sorted
    region: &TilingRegion,
    resolution_deg: f64,
) -> BucketMapping
```

For each bucket center, find nearest GRIB point via binary search on 1D lat/lon arrays. O(n_cells × log(n_grib)).

### 2c: Build from projected 2D grid (HRRR, NAM)

```rust
pub fn build_projected(
    grib_lats: &Array2<f64>,  // 2D (ny_grib, nx_grib)
    grib_lons: &Array2<f64>,
    region: &TilingRegion,
    resolution_deg: f64,
) -> BucketMapping
```

Build a coarse grid hash over GRIB lat/lon for O(1) nearest-neighbor lookup. For each bucket center, probe the hash cell and its neighbors.

### 2d: Apply mapping

```rust
pub fn apply(
    &self,
    grib_values: &[f32],
    conversion: Conversion,
    snap_threshold: f32,
) -> Vec<f32>
```

Gather values from GRIB using precomputed indices, apply conversion + threshold snap.

### 2e: Tests

- Test regular grid mapping: 0.25° GRIB → 0.25° tiles (1:1), 0.25° GRIB → 0.1° tiles (many:1)
- Test projected grid mapping: synthetic Lambert-like 2D coords
- Test that every cell gets a value (no NaN)
- Test snap threshold: values below threshold → 0.0, values above → unchanged

---

## Step 3: rctile v2 Format (~400 lines, 60 min)

**Files**:
- `rust_worker/crates/core/src/rctile.rs` — rewrite (keep v1 behind `#[cfg(feature = "rctile-v1")]` temporarily)
- `rust_worker/crates/core/Cargo.toml` — add `flate2 = "1"` (for gzip)

### 3a: Header + runs table structs

```rust
pub const MAGIC_V2: [u8; 4] = *b"RCT2";
pub const HEADER_SIZE_V2: usize = 128;

#[repr(C)]
pub struct RcTileHeaderV2 { ... }  // 128 bytes, bytemuck Pod

pub struct RunEntry {
    pub run_id: [u8; 16],
    pub init_unix: i64,
    pub hours: Vec<i32>,
}
```

### 3b: Write (finalize) — build complete file from in-memory data

```rust
pub struct RunData {
    pub run_id: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    pub cell_values: Vec<Vec<f32>>,  // [cell_idx][hour_slot]
}

/// Build a complete v2 rctile file from multiple runs' data.
/// Applies zero-chunk elision: all-zero cells get chunk size 0.
pub fn write_v2(
    path: &Path,
    runs: &[RunData],
    ny: u16, nx: u16,
    lat_min: f32, lat_max: f32,
    lon_min: f32, lon_max: f32,
    resolution_deg: f32,
) -> Result<()>
```

Steps: build header → serialize runs table → for each cell gzip compress (with elision) → build index → write atomically via temp+rename.

### 3c: Read — query single point from mmap

```rust
pub struct PointResult {
    pub runs: Vec<PointRunData>,
}
pub struct PointRunData {
    pub run_id: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    pub values: Vec<f32>,
}

/// Query a point from an mmap'd v2 rctile file.
/// Returns data for all runs. Elided cells return zero-filled values.
pub fn query_point_v2(data: &[u8], lat: f64, lon: f64) -> Result<PointResult>
```

### 3d: Read existing — load all runs from existing file (for merge during finalize)

```rust
/// Load all runs' cell data from an existing v2 file.
/// Used during finalize to merge with new run.
pub fn load_all_runs(data: &[u8]) -> Result<Vec<RunData>>
```

Decompress all chunks, split by run. Memory-intensive but only called during finalize.

### 3e: Tests

- Round-trip: write 2 runs → query point → verify values match
- Elision: write run with all-zero apcp → verify chunk size = 0, query returns zeros
- Multiple runs with different hour counts
- Edge cases: single run, single hour, cell at grid boundary

---

## Step 4: Worker Accumulator + Finalize (~200 lines, 45 min)

**Files**:
- `rust_worker/crates/core/src/worker.rs` — major refactor
- `rust_worker/crates/worker/src/main.rs` — worker loop changes

### 4a: RunAccumulator struct

```rust
pub struct RunAccumulator {
    pub run_id: String,
    pub variable_id: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    pub cell_values: Vec<Vec<f32>>,  // [cell_idx][hour_slot]
    pub ny: u16,
    pub nx: u16,
    pub region: &'static TilingRegion,
    pub resolution_deg: f64,
}
```

### 4b: Process hour (accumulate instead of write)

Replace current `process_build_tile_hour()` flow:
- Current: fetch → decode → build_tile_stats → upsert_rctile (write immediately)
- New: fetch → decode → apply_mapping → snap → accumulate in RunAccumulator

### 4c: Finalize (merge + write v2)

When worker detects run change or run completion:
1. If existing v2 file: `load_all_runs()` → filter retained runs
2. Append new run from accumulator
3. `write_v2()` → atomic write
4. Update DB

### 4d: Worker loop changes

Worker main loop needs to:
- Track current `(run_id, variable_id)` pair
- When job has different run/var: finalize current accumulator, start new one
- On clean shutdown: finalize any active accumulator
- BucketMapping cached per model (built on first GRIB decode)

### 4e: DB schema update

The `tile_variables` and `tile_hours` tables currently track per-run paths. With v2:
- `tile_variables.npz_path` → points to model-level rctile (not per-run)
- `tile_runs` still tracks which runs exist (for the status dashboard)
- May need a new `tile_files` table or simplify existing ones

---

## Step 5: Server Reads v2 (~150 lines, 30 min)

**Files**:
- `rust_worker/crates/server/src/main.rs` — multirun + stitched endpoints

### 5a: Change file resolution from per-run to per-model

```rust
// BEFORE:
for run_id in all_runs {
    let rctile_path = tiles_dir/region/res/model/run_id/var.rctile;
    let (hours, values) = mmap_cache.read_timeseries(&rctile_path, lat, lon);
}

// AFTER:
let rctile_path = tiles_dir/region/res/model/var.rctile;
let point_result = mmap_cache.query_point_v2(&rctile_path, lat, lon);
for run_data in point_result.runs {
    // Already split by run with hours and values
}
```

### 5b: Update MmapCache

- `query_point_v2()` method that wraps `rctile::query_point_v2()`
- Same mtime invalidation logic
- Runs table parsed once per mmap open (small, cached)

### 5c: Update stitched endpoint

Same change pattern — one file per model, iterate runs from PointResult.

### 5d: Remove run-level DB queries for file paths

Currently queries `tile_runs` to discover run_ids, then builds per-run file paths. With v2, just need to know which models have tile files — the runs are inside the file.

---

## Step 6: Cleanup (~50 lines, 15 min)

- Gate old `rctile` v1 code behind `#[cfg(feature = "rctile-v1")]`
- Remove `nn_fill_nan()` from `tiles.rs` (mapping guarantees no NaN)
- Remove old `build_tile_stats()` scatter logic (replaced by mapping + apply)
- Remove per-run directory creation in worker
- Clean up unused DB columns/tables
- Update `dev-services.sh` if paths changed
- Delete stale v1 rctile files from cache

---

## Testing Strategy

### Key Invariant

**No model that currently returns data should stop returning data after the migration.** The main risks are GFS (resolution change 0.1° → 0.25°) and HRRR/NAM (scatter → gather mapping), so those get extra scrutiny.

### Pre-Migration Snapshot (before any code changes)

Capture current API responses as the ground truth baseline. These are saved to `tests/fixtures/v1_baseline/` and used for regression comparison.

```bash
# 3 test points: Philadelphia, Boston, rural WV
for lat_lon in "40.0,-75.4" "42.36,-71.06" "38.5,-80.5"; do
  IFS=',' read lat lon <<< "$lat_lon"
  for var in t2m apcp asnow snod; do
    curl -s "localhost:5001/api/timeseries/multirun?lat=$lat&lon=$lon&variable=$var&model=all" \
      > "tests/fixtures/v1_baseline/${var}_${lat}_${lon}.json"
  done
done
```

This gives us 12 files (4 vars × 3 locations) with every model's response. These become the regression oracle.

### Unit Tests — Step 2 (BucketMapping)

| Test | What it verifies |
|------|-----------------|
| `test_regular_grid_1to1` | 0.25° GRIB → 0.25° tiles: each bucket maps to exactly one GRIB point. Zero NaN. |
| `test_regular_grid_coarse_to_fine` | 0.25° GRIB → 0.1° tiles: multiple buckets map to same GRIB point. Every bucket filled. Zero NaN. |
| `test_projected_grid_all_filled` | Synthetic Lambert 2D coords: every bucket gets a value. Zero NaN. |
| `test_projected_grid_edge_cells` | Buckets at region boundary where Lambert cone has gaps: still filled via nearest GRIB point. |
| `test_snap_threshold` | Inject values [0.0, 0.003, 0.006, 0.01, 0.1]. Verify 0.003 → 0.0, 0.006 → 0.01 (above threshold), 0.1 → 0.1. |
| `test_snap_no_effect_on_t2m` | t2m snap threshold = 0.0. Values like 32.001°F pass through unchanged. |
| `test_mapping_deterministic` | Build mapping twice from same coords → identical result. |
| `test_apply_with_conversion` | GRIB values in Kelvin, conversion KToF. Verify output in °F. |

### Unit Tests — Step 3 (rctile v2 format)

| Test | What it verifies |
|------|-----------------|
| `test_write_read_single_run` | Write 1 run (48 hours, dense). Query 5 points. Values match exactly. |
| `test_write_read_multi_run` | Write 3 runs with different hour counts (48, 60, 110). Query → verify run split correct, hour counts match, values match. |
| `test_zero_chunk_elision` | Write run where 60% of cells are all-zero. Verify: chunk_size = 0 for those cells. Query returns zero-filled timeseries. Non-zero cells unaffected. |
| `test_merge_runs` | Write file with 2 runs. Load, add 1 new run, write again. Read back → 3 runs, all values intact. |
| `test_merge_with_expiry` | Write file with 5 runs. Merge with 1 new run, retention = 5. Verify oldest run dropped, newest 5 present. |
| `test_atomic_write` | Write v2 file. Verify no partial file exists (temp file cleaned up, final file valid). |
| `test_header_fields` | Write file, read header. All fields (ny, nx, n_cells, lat/lon bounds, n_runs, total_values_per_cell) correct. |
| `test_empty_file` | Zero runs → valid file with empty runs table, all-zero index. |

### Integration Tests — Step 4 (Worker Pipeline)

| Test | What it verifies |
|------|-----------------|
| `test_full_pipeline_synthetic` | Synthetic GRIB (known values) → BucketMapping → accumulate 3 hours → finalize → query 5 points → values match expected (within f32 precision). |
| `test_full_pipeline_gfs` | If real GFS GRIB available in cache: decode → mapping → finalize → query Philadelphia (40.0, -75.4). Verify t2m is in reasonable range (0-120°F). Verify no NaN. |
| `test_full_pipeline_hrrr` | If real HRRR GRIB available: same as above. Verify no NaN for any of 5 test points (including edge cells). |
| `test_sparse_var_smaller` | Process same GRIB for t2m and apcp. Verify apcp rctile file size < 50% of t2m file size (sparsity working). |
| `test_accumulator_run_switch` | Process hour for run A, then hour for run B (different run_id). Verify run A finalized, run B accumulating. |
| `test_finalize_merges_existing` | Process run A → finalize. Process run B → finalize. Read file → both runs present. |

### E2E Tests — Step 5 (Server)

Run with `dev-services.sh` against real GRIB data.

| Test | What it verifies |
|------|-----------------|
| `test_api_all_models` | `GET /api/timeseries/multirun?lat=40.0&lon=-75.4&variable=t2m&model=all`. Every model that had data in the v1 baseline still returns data. |
| `test_api_gfs_not_empty` | GFS specifically returns series (was 84% NaN before). |
| `test_api_sparse_vars` | `variable=apcp`, `variable=asnow`, `variable=snod` all return data for models that support them. |
| `test_api_values_regression` | Compare response at 3 test points against v1 baseline snapshots. t2m: values within ±0.5°F (f32 rounding + gather vs scatter). apcp/asnow/snod: values within ±snap_threshold (0.005 in). |
| `test_api_stitched` | `/api/timeseries/stitched` endpoint still works with v2 files. |
| `test_file_layout` | `ls cache/tiles/ne/*/` shows model-level files (`hrrr/t2m.rctile`) not per-run dirs (`hrrr/run_*/t2m.rctile`). |

### Regression Comparison Rules

When comparing v2 responses against v1 baseline:

| Variable | Tolerance | Why |
|----------|-----------|-----|
| t2m | ±0.5°F | Gather (nearest GRIB point) vs scatter (which bucket GRIB point lands in) may pick a slightly different source point. For GFS at 0.25° → 0.25° the mapping is identical; for HRRR at 0.03° the difference is sub-grid-cell. |
| apcp | ±0.005 in | Threshold snap removes sub-0.005" values. This is intentional and physically correct. |
| asnow | ±0.005 in | Same. |
| snod | ±0.01 in | Slightly larger snap threshold for snow depth. |

Values outside tolerance → test failure → investigate before proceeding.

### Smoke Test Checklist (manual, after full deployment)

- [ ] `cargo test -p radarcheck-core` — all unit tests pass
- [ ] `cargo test -p radarcheck-worker` — all integration tests pass
- [ ] Workers start, process jobs, produce v2 rctile files
- [ ] `ls -la cache/tiles/ne/*/` shows model-level files (not per-run dirs)
- [ ] API returns data for all 5 models at Philadelphia (40.0, -75.4)
- [ ] GFS returns data (0.25° grid, no NaN)
- [ ] HRRR returns data at all 3 test points (mapping fills all cells)
- [ ] Sparse variable file sizes << t2m file sizes (check with `ls -la`)
- [ ] Total disk usage < 500 MB for 5 retained runs
- [ ] `/health` endpoint returns `version` confirming new binary
- [ ] Status dashboard shows runs populating normally
- [ ] No `chunk_size = 0 WARNING` in worker logs (would indicate mapping gap)

---

## Estimated Total: ~4 hours for a single focused agent

| Step | Lines | Time | Depends on |
|------|-------|------|------------|
| 1. Config | 5 | 5 min | — |
| 2. BucketMapping | 300 | 45 min | 1 |
| 3. rctile v2 format | 400 | 60 min | — |
| 4. Worker accumulator | 200 | 45 min | 2, 3 |
| 5. Server reader | 150 | 30 min | 3 |
| 6. Cleanup | 50 | 15 min | 4, 5 |
| Integration testing | — | 40 min | all |
| **Total** | **~1100** | **~4 hrs** | |

## Parallelization Assessment

**Not worth it.** Steps 2 and 3 are theoretically parallel but:
- Share `Cargo.toml` (both add deps)
- Share type imports from `config.rs`, `grib.rs`
- Integration in step 4 requires understanding both modules intimately
- Two agents loading the full crate context costs ~10 min each
- Merge conflicts on `lib.rs`, `Cargo.toml` likely
- Estimated savings from parallelizing 2+3: ~30 min
- Estimated overhead from coordination + merge: ~20 min
- **Net savings: ~10 min.** Not worth the complexity.

One agent, sequential, with the dependency order: 1 → 2 → 3 → 4 → 5 → 6.
