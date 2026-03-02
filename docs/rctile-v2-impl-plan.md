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

### Unit tests (steps 2, 3)
- BucketMapping: correctness of nearest-point search, no NaN, snap thresholds
- rctile v2: round-trip write/read, elision, multi-run merge

### Integration tests (step 4)
- Full pipeline: synthetic GRIB → mapping → accumulate → finalize → query
- Verify values match expected (known GRIB input → known tile output)

### E2E test (after step 5)
- Start workers, let them process real GRIB data
- Query API, verify responses have all models with reasonable values
- Compare t2m values at known location with raw GRIB values
- Verify sparse variables have elided chunks (check file sizes)

### Smoke test checklist
- [ ] `cargo test -p radarcheck-core` passes
- [ ] `cargo test -p radarcheck-worker` passes
- [ ] Workers start, process jobs, produce v2 rctile files
- [ ] `ls -la cache/tiles/ne/*/` shows model-level files (not per-run dirs)
- [ ] API returns data for all models at test lat/lon
- [ ] GFS returns data (0.25° grid, no NaN)
- [ ] HRRR returns data (mapping fills all cells)
- [ ] Sparse variable file sizes << t2m file sizes
- [ ] Total disk usage < 500 MB for 5 retained runs

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
