# `.rctile` v2 Spec: Compressed Multi-Run Tile Format

## Problem

| Issue | Detail |
|-------|--------|
| **Disk** | rctile v1 = raw f32, pre-allocated → 65 MB/file HRRR × 5 runs × 4 vars = 1.3 GB. 1 GB volume. |
| **NaN gaps** | GFS 0.25° tiled at 0.1° → 84% empty. HRRR Lambert → 16% empty edges. |
| **File count** | One file per (model, run, var). `model=all` opens 20+ files. |

## Design

One file per **(region, model, variable)** containing all retained runs. Every bucket is filled by construction via precomputed nearest-GRIB-point mapping. Per-cell gzip chunks with offsets-only index for O(1) point queries. Sparsity exploited via threshold snapping + zero-chunk elision.

---

## 1. Bucket Grid

Regular lat/lon grid over a region:

```
ny = ceil((lat_max - lat_min) / resolution_deg)
nx = ceil((lon_max - lon_min) / resolution_deg)
cell_idx = iy * nx + ix
```

**Tile resolutions** (matching native grid where possible):

| Model | Native | Tile Res | Grid (NE region) | Cells |
|-------|--------|----------|-------------------|-------|
| GFS | 0.25° lat/lon | **0.25°** | 56 × 88 | 4,928 |
| ECMWF | 0.1° lat/lon | 0.1° | 140 × 220 | 30,800 |
| NAM | ~3km Lambert | 0.1° | 140 × 220 | 30,800 |
| NBM | ~3km Lambert | 0.1° | 140 × 220 | 30,800 |
| HRRR | ~3km Lambert | 0.03° | 467 × 734 | 342,778 |

GFS tiles at **0.25°** (not 0.1°). Eliminates 84% NaN. Server maps queries to 0.25° grid.

---

## 2. Bucket → GRIB Mapping

### The Guarantee

Every bucket gets a value. No NaN. No query-time NN search.

### How

Current approach (scatter): *for each GRIB point, find which bucket it lands in*. Leaves buckets empty when native grid is coarser than tile grid.

New approach (gather): *for each bucket, find the nearest GRIB point(s)*. Every bucket always has a source.

### Algorithm

For each bucket center `(lat_c, lon_c)`:

1. Search radius `R = native_resolution × √2` (auto-derived per model)
2. Find all GRIB points within R
3. **1 point** → use directly
4. **N > 1 equidistant** → inverse-distance-weighted average
5. **0 points** → chunk_size = 0, log warning (shouldn't happen — R guarantees coverage)

### Precomputation

The mapping depends only on `(model_id, region_id, resolution_deg)` — same for every run and every forecast hour. Built once per worker process, kept in memory.

```rust
/// For each bucket cell: which GRIB flat indices to read and their weights
struct BucketMapping {
    /// entries[cell_idx] = [(grib_flat_idx, weight), ...]
    entries: Vec<SmallVec<[(usize, f32); 2]>>,
}
```

**Build cost:**
- Regular grids (GFS, ECMWF, NBM): analytical nearest-point math. O(n_cells).
- Projected grids (HRRR, NAM): for each bucket center, scan GRIB lat/lon arrays for nearest. O(n_cells × n_grib) but n_grib ≈ n_cells, so build a spatial index (simple grid hash) to make it O(n_cells).

**Memory:** ~16 bytes/cell. HRRR = 5.5 MB, others < 1 MB.

### Applying the Mapping (replaces `build_tile_stats`)

```rust
fn build_tile_values(
    grib_values: &[f32],
    mapping: &BucketMapping,
    conversion: Conversion,
) -> Vec<f32> {
    let mut out = vec![f32::NAN; mapping.entries.len()];
    for (cell_idx, sources) in mapping.entries.iter().enumerate() {
        if sources.is_empty() { continue; }
        let mut sum = 0.0f64;
        let mut w_sum = 0.0f64;
        for &(grib_idx, w) in sources {
            let v = conversion.apply(grib_values[grib_idx]);
            if !v.is_nan() {
                sum += v as f64 * w as f64;
                w_sum += w as f64;
            }
        }
        if w_sum > 0.0 {
            out[cell_idx] = (sum / w_sum) as f32;
        }
    }
    out
}
```

No min/max/mean stats — just one value per cell per hour (the weighted average). Simpler, smaller.

---

## 3. Sparsity Optimization

### Measured Sparsity (current data)

| Variable | Zero (<0.001) | Near-zero (<0.01) | Dense? |
|----------|--------------|-------------------|--------|
| **t2m** | 0% | 0% | Yes — every cell has a real temperature |
| **apcp** | 73% | 85% | No — most cells have no precip most hours |
| **asnow** | 83% | 87% | No — snow is rare and localized |
| **snod** | 77% | 77% | No — snow depth zero outside snow regions |

3 of 4 variables are 73-87% zero. This is exploited in two layers.

### Layer 1: Threshold Snap (at build time)

Values below a physically meaningless threshold are snapped to exact 0.0 before any storage. This converts near-zero noise into true zeros that compress maximally.

```rust
/// Per-variable thresholds (in display units after conversion)
fn snap_threshold(variable_id: &str) -> f32 {
    match variable_id {
        "apcp"  => 0.005,  // 0.005 in = 0.1 mm — sub-millimeter precip is noise
        "asnow" => 0.005,  // 0.005 in — trace snowfall
        "snod"  => 0.01,   // 0.01 in = 0.25 mm — sub-mm snow depth
        _       => 0.0,    // t2m: no snapping (temperature is never "noise")
    }
}

/// Applied after unit conversion, before accumulation
fn snap_near_zero(value: f32, threshold: f32) -> f32 {
    if value.abs() < threshold { 0.0 } else { value }
}
```

**Effect**: Pushes apcp from 73% → ~85% true zeros. asnow from 83% → ~87%. snod stays ~77% (values are already discrete). More true zeros = better gzip compression and more all-zero cells for elision.

### Layer 2: Zero-Chunk Elision (at finalize time)

If a cell is all-zero across **all runs and all hours**, store no chunk data. The index convention `offsets[i] == offsets[i+1]` (chunk size = 0) signals an all-zero cell.

```
Index:  [..., 4820, 4820, 4820, 5412, ...]
                ↑     ↑
         cells 1037, 1038: all-zero, chunk size = 0
```

At query time: chunk size = 0 → return a zero-filled timeseries (no decompression needed).

```rust
// In finalize, per-cell:
let all_zero = all_runs.iter().all(|run|
    run.cell_values[cell_idx].iter().all(|&v| v == 0.0)
);
if all_zero {
    // Push same offset again (size 0)
    offsets.push(*offsets.last().unwrap());
} else {
    let raw = concat_all_run_values(cell_idx, &all_runs);
    let compressed = gzip_compress(&raw);
    chunks.push(compressed);
    offsets.push(offsets.last().unwrap() + compressed.len() as u64);
}
```

### Combined Effect on Chunk Sizes

After threshold snap, estimated fraction of **all-zero cells** (zero across all 5 runs, all hours):

| Variable | All-zero cells | Chunks stored | Chunk savings |
|----------|---------------|---------------|---------------|
| t2m | 0% | 100% | none |
| apcp | ~50% | ~50% | 50% fewer chunks |
| asnow | ~65% | ~35% | 65% fewer chunks |
| snod | ~55% | ~45% | 55% fewer chunks |

For the remaining non-zero cells, the increased true-zero density within the timeseries makes gzip much more effective — long runs of `0x00000000` bytes compress to near-nothing.

### Estimated Chunk Sizes (gzip, after snap, 5 runs)

**Dense variable (t2m):**

| Model | Values/cell | Raw | Gzip est. |
|-------|-------------|-----|-----------|
| GFS | 550 | 2,200 B | ~1,200 B |
| HRRR | 240 | 960 B | ~550 B |
| NAM | 300 | 1,200 B | ~700 B |
| NBM | 400 | 1,600 B | ~950 B |
| ECMWF | 400 | 1,600 B | ~950 B |

**Sparse variable (apcp/asnow/snod) — non-zero cells only:**

| Model | Values/cell | Raw | Gzip est. | Notes |
|-------|-------------|-----|-----------|-------|
| GFS | 550 | 2,200 B | ~400 B | ~85% of values are zero within the chunk |
| HRRR | 240 | 960 B | ~120 B | Zeros compress to near-nothing |
| NAM | 300 | 1,200 B | ~200 B | |
| NBM | 400 | 1,600 B | ~300 B | |
| ECMWF | 400 | 1,600 B | ~300 B | |

### Future Option: Sparse Value Encoding (if needed)

If disk is still tight after snap + elision, add a third layer: encode only non-zero values within each chunk before gzip.

```
Chunk payload (before gzip):
  [n_nonzero: u16] [slot0: u16, val0: f32] [slot1: u16, val1: f32] ...

All-zero cell: already elided (chunk size 0)
10% non-zero:  2 + 24×6 = 146 bytes → gzip ~100 bytes
                vs dense gzip: ~200 bytes
```

**Decision**: Implement snap + elision first. Measure actual file sizes. Add sparse encoding only if HRRR exceeds disk budget.

---

## 4. File Format

### Layout

```
┌──────────────────────────────────────────┐
│ HEADER (128 bytes)                       │
├──────────────────────────────────────────┤
│ RUNS TABLE (variable size)               │
│   run_id, init_unix, n_hours, hours[]    │
├──────────────────────────────────────────┤
│ CELL INDEX ((n_cells + 1) × 8 bytes)     │
│   u64 offsets into DATA                  │
│   chunk_i = data[off[i]..off[i+1]]      │
│   size 0 = all-zero cell (elided)        │
├──────────────────────────────────────────┤
│ DATA (variable size)                     │
│   per-cell gzip chunks                   │
│   each → f32[] for all runs' hours       │
└──────────────────────────────────────────┘
```

### Header (128 bytes)

```
Offset  Size  Type      Field
0       4     [u8;4]    magic = b"RCT2"
4       2     u16le     version = 2
6       2     u16le     ny
8       2     u16le     nx
10      4     u32le     n_cells (ny × nx)
14      4     f32le     lat_min
18      4     f32le     lat_max
22      4     f32le     lon_min (west-negative, e.g. -88.0)
26      4     f32le     lon_max
30      4     f32le     resolution_deg
34      2     u16le     n_runs
36      2     u16le     total_values_per_cell (sum of all runs' n_hours)
38      8     u64le     runs_table_offset (= 128)
46      8     u64le     index_offset
54      8     u64le     data_offset
62      66    [u8;66]   _reserved
```

No `lon_0_360` field — coordinate convention is handled at build time by the bucket mapping.

### Runs Table

At `runs_table_offset`. For each of `n_runs` runs (oldest first):

```
Offset  Size       Field
0       16         run_id ([u8;16], null-padded, "run_YYYYMMDD_HH")
16      8          init_unix (i64le)
24      2          n_hours (u16le)
26      n_hours×4  hours (i32le[], sorted ascending)
```

Entry size = `26 + n_hours × 4`. Variable per run.

### Cell Index

At `index_offset`: **(n_cells + 1)** u64le offsets, relative to start of DATA region.

```
chunk for cell i = data_region[offsets[i] .. offsets[i+1]]
chunk size = offsets[i+1] - offsets[i]
size 0 → all-zero cell (return zero-filled timeseries)
```

### Data Region

At `data_offset`: concatenated gzip chunks.

Each chunk decompresses to `total_values_per_cell × 4` bytes of f32le:

```
[run0_h0, run0_h1, ..., run0_hN,  run1_h0, run1_h1, ..., run1_hM,  ...]
 ├─── run 0 (N hours) ───┤        ├─── run 1 (M hours) ───┤
```

Reader splits values by run using runs table metadata.

---

## 5. Write Path

### Worker Startup

```
worker --model hrrr

1. Load BucketMapping for (hrrr, ne, 0.03°)
   - First job for this model: decode GRIB, extract coordinate grid
   - Compute nearest-point mapping
   - Cache in memory for all subsequent jobs

2. Enter job loop
```

### Per-Hour Job Processing

```
claim_job() → { model=hrrr, run=run_20260302_00, var=t2m, fh=6 }

1. Fetch GRIB bytes (byte-range via IDX)
2. Decode GRIB message → values[ny_grib × nx_grib]
3. Apply bucket mapping → tile_values[n_cells]
   (guaranteed: no NaN unless GRIB point itself was NaN)
4. Threshold snap: for sparse vars, snap near-zero → 0.0
5. Append to in-memory accumulator for this run
6. Mark job complete
```

### In-Memory Accumulator

```rust
struct RunAccumulator {
    run_id: String,
    init_unix: i64,
    variable_id: String,
    hours: Vec<i32>,                  // forecast hours, sorted
    cell_values: Vec<Vec<f32>>,       // cell_values[cell_idx][hour_slot]
}
```

Memory per active run:

| Model | Cells × Hours × 4B | Memory |
|-------|---------------------|--------|
| GFS | 4,928 × 110 | 2.2 MB |
| NAM | 30,800 × 60 | 7.4 MB |
| NBM | 30,800 × 80 | 9.9 MB |
| ECMWF | 30,800 × 80 | 9.9 MB |
| HRRR | 342,778 × 48 | 65.8 MB |

### Finalize (run complete or worker switching to new run)

```
finalize(accumulator, rctile_path):

1. LOCK rctile_path.lock (exclusive)

2. LOAD EXISTING RUNS (if file exists):
   - mmap existing .rctile
   - Parse header + runs table
   - For each existing run to RETAIN:
     - For each cell: decompress chunk (skip if size 0), extract that run's slice
     - Store as RunData { run_id, init_unix, hours, cell_values }
   - Drop expired runs (keep N newest, default 5)

3. MERGE:
   all_runs = retained_existing + [new_run]
   sort by init_unix ascending
   total_values_per_cell = Σ run.hours.len()

4. COMPRESS (with sparsity):
   offsets = [0u64]
   chunks = []
   for cell_idx in 0..n_cells:
     // Zero-chunk elision
     all_zero = all_runs.iter().all(|r| r.cell_values[cell_idx].iter().all(|&v| v == 0.0))
     if all_zero:
       offsets.push(*offsets.last())   // size 0
       continue

     raw_f32s = []
     for run in all_runs:
       raw_f32s.extend(&run.cell_values[cell_idx])
     compressed = gzip_compress(raw_f32s.as_bytes())
     chunks.push(compressed)
     offsets.push(offsets.last() + compressed.len())

5. WRITE (atomic):
   temp_path = rctile_path + ".tmp"
   write temp_path:
     - Header (128 B)
     - Runs table
     - Cell index (offsets)
     - Data (chunks)
   rename temp_path → rctile_path

6. UNLOCK
7. Update DB: tile_runs, tile_variables
```

### Fault Tolerance

- Worker crashes mid-run → accumulated data lost, jobs reset to pending by scheduler
- Existing .rctile is never corrupted (atomic rename)
- Re-processing a run is safe (finalize merges/replaces)

---

## 6. Read Path (Server)

### Point Query

```
GET /api/timeseries/multirun?lat=40.5&lon=-74.0&variable=t2m&model=gfs
```

```
1. RESOLVE FILE:
   path = tiles_dir/ne/0.250deg/gfs/t2m.rctile
   (one file per model — not per run)

2. MMAP (cached by path, invalidated on mtime change)

3. PARSE HEADER (128 bytes, in page cache)

4. COMPUTE CELL:
   iy = floor((40.5 - 33.0) / 0.25) = 30
   ix = floor((-74.0 - (-88.0)) / 0.25) = 56
   cell_idx = 30 * 88 + 56 = 2696

5. READ INDEX (16 bytes):
   off_start = index_offset + cell_idx * 8
   chunk_start = u64_le(mmap[off_start..])
   chunk_end   = u64_le(mmap[off_start+8..])

6. CHECK ELISION:
   if chunk_start == chunk_end:
     return zero-filled timeseries for all runs (fast path)

7. DECOMPRESS (sub-KB):
   compressed = mmap[data_offset + chunk_start .. data_offset + chunk_end]
   values: Vec<f32> = gunzip(compressed)

8. SPLIT BY RUN (from runs table):
   runs_table = parse_runs_table(mmap)
   offset = 0
   for run in runs_table:
     run_values = values[offset .. offset + run.n_hours]
     offset += run.n_hours
     emit { run.run_id, run.init_unix, run.hours, run_values }
```

### Server Code Change

```rust
// BEFORE: iterate per-run files (20+ opens for model=all)
for run_id in all_runs {
    let path = tiles/region/res/model/run_id/var.rctile;
    let (hours, vals) = mmap_cache.read_timeseries(&path, lat, lon);
}

// AFTER: one file per model (5 opens for model=all)
let path = tiles/region/res/model/var.rctile;
let all_runs = mmap_cache.query_point(&path, lat, lon);
for run in all_runs {
    // run.hours, run.values already split
}
```

### Performance

| Step | Cost |
|------|------|
| mmap lookup | ~0 (cached) |
| Cell index read | ~0 (8 bytes, page cache) |
| Elision check | ~0 (compare two u64s) |
| Chunk read (non-zero) | 1 page fault (100-1200 bytes) |
| gunzip | 1-5 μs |
| **Total per model** | **< 10 μs** |
| **Elided cell (all-zero)** | **< 0.1 μs** (no decompression) |

Current rctile v1: ~0.3 μs (no decompression, but 20+ file opens for model=all).
Current NPZ path: ~50 ms (full array decompression).

---

## 7. Run Management

### Retention

Keep **N most recent runs** per model (default 5). During finalize, runs older than the Nth-newest are dropped.

### Expiry = Rebuild

No tombstones, no separate cleanup. When a new run finalizes:
1. Load existing runs from current file
2. Keep only N-1 newest existing + new run
3. Rebuild file without expired data

Disk reclaimed immediately.

### Directory Layout Change

```
BEFORE (v1):
  cache/tiles/ne/0.100deg/gfs/run_20260301_00/t2m.rctile
  cache/tiles/ne/0.100deg/gfs/run_20260301_06/t2m.rctile
  cache/tiles/ne/0.100deg/gfs/run_20260301_12/t2m.rctile
  (one file per run per variable)

AFTER (v2):
  cache/tiles/ne/0.250deg/gfs/t2m.rctile        ← all runs inside
  cache/tiles/ne/0.250deg/gfs/t2m.meta.json
  cache/tiles/ne/0.030deg/hrrr/t2m.rctile
  cache/tiles/ne/0.030deg/hrrr/apcp.rctile
  ...
```

---

## 8. Disk Usage

### Estimated File Sizes (5 retained runs, snap + elision)

**t2m (dense — no elision, only gzip):**

| Model | Cells | Gzip/cell | Total |
|-------|-------|-----------|-------|
| GFS 0.25° | 4,928 | ~1,200 B | **5.9 MB** |
| HRRR 0.03° | 342,778 | ~550 B | **188 MB** |
| NAM 0.1° | 30,800 | ~700 B | **21.6 MB** |
| NBM 0.1° | 30,800 | ~950 B | **29.3 MB** |
| ECMWF 0.1° | 30,800 | ~950 B | **29.3 MB** |

**apcp/asnow/snod (sparse — snap + elision + gzip):**

| Model | Cells | Non-zero cells | Gzip/chunk | Total |
|-------|-------|---------------|------------|-------|
| GFS 0.25° | 4,928 | ~2,500 (50%) | ~400 B | **1 MB** |
| HRRR 0.03° | 342,778 | ~137K (40%) | ~120 B | **16 MB** |
| NAM 0.1° | 30,800 | ~14K (45%) | ~200 B | **2.8 MB** |
| NBM 0.1° | 30,800 | ~14K (45%) | ~300 B | **4.2 MB** |
| ECMWF 0.1° | 30,800 | ~14K (45%) | ~300 B | **4.2 MB** |

### Total Disk (all models, all variables)

| Model | t2m | apcp | asnow | snod | Total |
|-------|-----|------|-------|------|-------|
| GFS | 5.9 | 1.0 | — | 1.0 | **~8 MB** |
| HRRR | 188 | 16 | 16 | 16 | **~236 MB** |
| NAM | 21.6 | 2.8 | — | 2.8 | **~27 MB** |
| NBM | 29.3 | 4.2 | 4.2 | — | **~38 MB** |
| ECMWF | 29.3 | 4.2 | — | 4.2 | **~38 MB** |
| **Total** | | | | | **~347 MB** |

Plus cell index overhead: ~3 MB (HRRR) + ~1 MB (others) = **~4 MB**

### **Grand total: ~350 MB** (well within 1 GB volume)

### Comparison

| Format | HRRR (5 runs) | All models | Fits 1 GB? |
|--------|--------------|------------|------------|
| NPZ (current) | 380 MB | ~700 MB | Barely |
| rctile v1 (raw) | 1,316 MB | ~1,650 MB | No |
| **rctile v2 (snap + elision + gzip)** | **~236 MB** | **~350 MB** | **Yes, 65% headroom** |

The key insight: sparse variables (3 of 4) compress dramatically because:
1. Threshold snap pushes near-zero → true zero (more compressible bytes)
2. All-zero cells (40-65% of sparse vars) store nothing — zero bytes
3. Non-zero cells still have ~80% zero values within, which gzip handles well

---

## 9. Implementation Sequence

### Step 0: GFS tile resolution → 0.25°
- `config.rs`: change GFS `tile_resolution_deg` to 0.25
- Server routing: use 0.25° path for GFS

### Step 1: BucketMapping + gather-based tile building
- New `bucket_mapping.rs`: `BucketMapping` struct, build from GRIB coords
- Replace scatter logic in `tiles.rs` with gather via mapping
- Remove `nn_fill_nan` (no longer needed)
- Test: every cell has a value for all models

### Step 2: rctile v2 format (read/write)
- New `rctile_v2.rs` (or evolve `rctile.rs`):
  - Header v2, runs table, cell index, gzip data
  - `create_and_write()` — build complete file from in-memory data
  - `query_point()` — decompress one cell, split by run
  - Zero-chunk elision: size 0 → return zeros
- Threshold snap constants per variable
- Test: round-trip write → read, verify elision works

### Step 3: Worker accumulator + finalize
- `worker.rs`: add `RunAccumulator` state
- After each hour: apply snap, accumulate instead of immediately writing
- On run complete: call finalize → build compressed rctile v2
- Merge with existing runs, drop expired

### Step 4: Server reads v2
- `main.rs`: one file per model (not per run)
- `query_point()` returns all runs' data at once
- Handle elided cells (zero-fill fast path)
- Remove per-run iteration

### Step 5: Cleanup
- Remove rctile v1 code
- Remove per-run directory structure
- Update DB schema (tile_runs → simpler, no per-run paths)

### Step 6 (if needed): Sparse value encoding
- Only if measured file sizes exceed budget after steps 0-5
- Add sparse encoding layer between snap and gzip
- `[n_nonzero: u16, (slot: u16, val: f32) × n_nonzero]` → then gzip
- Estimated additional savings: ~30% on non-zero sparse chunks
