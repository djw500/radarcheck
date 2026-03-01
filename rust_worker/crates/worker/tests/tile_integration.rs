//! Integration tests: verify full GRIB → tile stats → NPZ pipeline
//! matches Python reference output.
//!
//! For each of the 16 model/variable fixtures:
//!   1. Decode the GRIB fixture
//!   2. Build tile stats using NE region + model resolution + unit conversion
//!   3. Compare tile output arrays against Python-generated reference NPZ
//!
//! Known limitations:
//!   - ECMWF SNOD: fixture GRIB has raw `sd`, reference tiles were built from
//!     the same raw `sd` (not derived snod). So tiles should match.
//!   - NBM APCP/ASNOW: DRT3 decoder divergence propagates into tiles.
//!   - ECMWF T2M: CCSDS ~0.2% value offset propagates into tiles.
//!   - NBM Lambert projection divergence affects tile cell assignment.

use std::path::Path;

use serde::Deserialize;

use radarcheck_core::config::{Conversion, NE_REGION};
use radarcheck_core::grib;
use radarcheck_core::npz;
use radarcheck_core::tiles;

#[derive(Deserialize)]
struct TileFixtureMeta {
    #[allow(dead_code)]
    model_id: String,
    #[allow(dead_code)]
    variable_id: String,
    resolution_deg: f64,
    tile_shape: Vec<usize>,
    conversion: String,
    #[allow(dead_code)]
    src_units: Option<String>,
    #[allow(dead_code)]
    lon_0_360: bool,
    #[allow(dead_code)]
    index_lon_min: f64,
    #[allow(dead_code)]
    means_finite_count: usize,
    #[allow(dead_code)]
    means_nan_count: usize,
}

/// Map Python conversion name to Rust Conversion enum
fn parse_conversion(name: &str) -> Conversion {
    match name {
        "k_to_f" => Conversion::KToF,
        "c_to_f" => Conversion::CToF,
        "m_s_to_mph" => Conversion::MSToMph,
        "kg_m2_to_in" => Conversion::KgM2ToIn,
        "m_to_in" => Conversion::MToIn,
        "kg_m2_s_to_in_hr" => Conversion::KgM2SToInHr,
        "m_to_ft" => Conversion::MToFt,
        _ => Conversion::None,
    }
}

struct TileTestCase {
    name: &'static str,
    /// Mean absolute error tolerance for tile means (in converted units)
    mean_mae_tol: f32,
    /// Fraction of cells where NaN-agreement is required (0.0 to 1.0).
    /// With NN fill enabled, this is no longer enforced — kept for documentation.
    #[allow(dead_code)]
    nan_agreement_tol: f64,
}

fn run_tile_test(tc: &TileTestCase) {
    let fixtures = option_env!("GRIB_FIXTURES_DIR")
        .unwrap_or(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../tests/fixtures/grib_parity"));
    let base = format!("{}/{}", fixtures, tc.name);

    // 1. Load and decode GRIB
    let grib_bytes = std::fs::read(format!("{}.grib2", base))
        .unwrap_or_else(|e| panic!("Missing GRIB fixture: {} — {}", tc.name, e));
    let decoded = grib::decode_grib_message(&grib_bytes)
        .unwrap_or_else(|e| panic!("Decode failed: {} — {}", tc.name, e));

    // 2. Load tile fixture metadata
    let tile_meta_str = std::fs::read_to_string(format!("{}_tiles.json", base))
        .unwrap_or_else(|e| panic!("Missing tile fixture: {} — {}", tc.name, e));
    let tile_meta: TileFixtureMeta = serde_json::from_str(&tile_meta_str)
        .unwrap_or_else(|e| panic!("Bad tile JSON: {} — {}", tc.name, e));

    // 3. Determine conversion from fixture metadata
    let conversion = parse_conversion(&tile_meta.conversion);

    // 4. Build tiles
    let resolution = tile_meta.resolution_deg;
    let tile_stats = tiles::build_tile_stats(&decoded, &NE_REGION, resolution, conversion)
        .unwrap_or_else(|e| panic!("Tile build failed: {} — {}", tc.name, e));

    // 5. Load reference NPZ
    let ref_npz = npz::read_tile_npz(Path::new(&format!("{}_tiles.npz", base)))
        .unwrap_or_else(|e| panic!("Failed to read reference NPZ: {} — {}", tc.name, e));

    // 6. Check grid shape
    let expected_ny = tile_meta.tile_shape[1];
    let expected_nx = tile_meta.tile_shape[2];
    assert_eq!(
        tile_stats.ny, expected_ny,
        "{}: tile ny mismatch (got {} expected {})",
        tc.name, tile_stats.ny, expected_ny
    );
    assert_eq!(
        tile_stats.nx, expected_nx,
        "{}: tile nx mismatch (got {} expected {})",
        tc.name, tile_stats.nx, expected_nx
    );

    // 7. Compare tile means against reference
    let ref_means = ref_npz.means.expect("Reference NPZ missing means array");
    let ref_means_slice = ref_means.as_slice().unwrap();

    // Our tile_stats.means is 2D (ny, nx); reference is 3D (1, ny, nx)
    let rust_means_slice = tile_stats.means.as_slice().unwrap();

    assert_eq!(
        rust_means_slice.len(),
        ref_means_slice.len(),
        "{}: means array length mismatch (rust {} vs ref {})",
        tc.name,
        rust_means_slice.len(),
        ref_means_slice.len()
    );

    let (mae, max_ae, nan_agree_pct, both_finite, ref_finite_lost) =
        compare_slices(rust_means_slice, ref_means_slice);

    let rust_finite = rust_means_slice.iter().filter(|v| !v.is_nan()).count();

    eprintln!(
        "  {} TILE: MAE={:.6}, MaxAE={:.4}, NaN-agree={:.1}%, finite_cells={}, rust_finite={}, ref_lost={}",
        tc.name, mae, max_ae, nan_agree_pct * 100.0, both_finite, rust_finite, ref_finite_lost
    );

    assert!(
        mae <= tc.mean_mae_tol,
        "{}: means MAE {:.6} exceeds tolerance {:.6}",
        tc.name, mae, tc.mean_mae_tol
    );

    // NN fill changes the NaN pattern: Rust fills edge NaN cells with nearest-neighbor.
    // So instead of requiring NaN pattern agreement, we check:
    // 1. No reference-finite cells lost (ref has value, Rust doesn't)
    // 2. Rust may have MORE finite cells than reference (from NN fill) — that's OK
    assert_eq!(
        ref_finite_lost, 0,
        "{}: {} cells were finite in reference but NaN in Rust (regression!)",
        tc.name, ref_finite_lost
    );

    // Rust should have at least as many finite cells as reference
    assert!(
        rust_finite >= both_finite,
        "{}: Rust has fewer finite cells ({}) than reference overlap ({})",
        tc.name, rust_finite, both_finite
    );

    // 8. Also compare mins and maxs if available
    if let Some(ref_mins) = ref_npz.mins {
        let ref_mins_s = ref_mins.as_slice().unwrap();
        let rust_mins_s = tile_stats.mins.as_slice().unwrap();
        let (mins_mae, _, _, _, _) = compare_slices(rust_mins_s, ref_mins_s);
        assert!(
            mins_mae <= tc.mean_mae_tol * 2.0, // mins can have higher variance
            "{}: mins MAE {:.6} exceeds tolerance {:.6}",
            tc.name, mins_mae, tc.mean_mae_tol * 2.0
        );
    }
    if let Some(ref_maxs) = ref_npz.maxs {
        let ref_maxs_s = ref_maxs.as_slice().unwrap();
        let rust_maxs_s = tile_stats.maxs.as_slice().unwrap();
        let (maxs_mae, _, _, _, _) = compare_slices(rust_maxs_s, ref_maxs_s);
        assert!(
            maxs_mae <= tc.mean_mae_tol * 2.0,
            "{}: maxs MAE {:.6} exceeds tolerance {:.6}",
            tc.name, maxs_mae, tc.mean_mae_tol * 2.0
        );
    }
}

/// Compare two f32 slices (rust=a, reference=b).
/// Returns (MAE, MaxAE, NaN-agreement fraction, finite count, ref_finite_lost count).
/// ref_finite_lost = cells finite in reference but NaN in Rust (regressions).
fn compare_slices(a: &[f32], b: &[f32]) -> (f32, f32, f64, usize, usize) {
    assert_eq!(a.len(), b.len());
    let total = a.len();
    let mut sum_err = 0.0f64;
    let mut max_err = 0.0f32;
    let mut both_finite = 0usize;
    let mut nan_agree = 0usize;
    let mut ref_finite_lost = 0usize; // finite in ref, NaN in rust (bad)

    for i in 0..total {
        let a_nan = a[i].is_nan();
        let b_nan = b[i].is_nan();

        if a_nan && b_nan {
            nan_agree += 1;
            continue;
        }
        if !a_nan && !b_nan {
            nan_agree += 1;
            let err = (a[i] - b[i]).abs();
            sum_err += err as f64;
            if err > max_err {
                max_err = err;
            }
            both_finite += 1;
        } else if a_nan && !b_nan {
            // Rust lost a finite cell — regression
            ref_finite_lost += 1;
        }
        // !a_nan && b_nan: Rust gained a finite cell (NN fill) — OK
    }

    let mae = if both_finite > 0 {
        (sum_err / both_finite as f64) as f32
    } else {
        0.0
    };
    let nan_agree_pct = nan_agree as f64 / total as f64;

    (mae, max_err, nan_agree_pct, both_finite, ref_finite_lost)
}

// ── Test cases ──────────────────────────────────────────────────────────────

// HRRR (Lambert conformal, JPEG2000) — tight parity expected
#[test]
fn tile_hrrr_apcp() {
    run_tile_test(&TileTestCase {
        name: "hrrr_apcp_f1",
        mean_mae_tol: 0.01,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_hrrr_asnow() {
    run_tile_test(&TileTestCase {
        name: "hrrr_asnow_f1",
        mean_mae_tol: 0.01,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_hrrr_snod() {
    run_tile_test(&TileTestCase {
        name: "hrrr_snod_f1",
        mean_mae_tol: 0.05,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_hrrr_t2m() {
    run_tile_test(&TileTestCase {
        name: "hrrr_t2m_f1",
        mean_mae_tol: 0.1,
        nan_agreement_tol: 0.99,
    });
}

// NAM Nest (Lambert conformal, Complex Packing) — tight parity
#[test]
fn tile_nam_nest_apcp() {
    run_tile_test(&TileTestCase {
        name: "nam_nest_apcp_f3",
        mean_mae_tol: 0.01,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_nam_nest_snod() {
    run_tile_test(&TileTestCase {
        name: "nam_nest_snod_f3",
        mean_mae_tol: 0.05,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_nam_nest_t2m() {
    run_tile_test(&TileTestCase {
        name: "nam_nest_t2m_f3",
        mean_mae_tol: 0.1,
        nan_agreement_tol: 0.99,
    });
}

// GFS (regular lat/lon, Complex Packing) — tight parity
#[test]
fn tile_gfs_apcp() {
    run_tile_test(&TileTestCase {
        name: "gfs_apcp_f3",
        mean_mae_tol: 0.01,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_gfs_snod() {
    run_tile_test(&TileTestCase {
        name: "gfs_snod_f3",
        mean_mae_tol: 0.05,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_gfs_t2m() {
    run_tile_test(&TileTestCase {
        name: "gfs_t2m_f3",
        mean_mae_tol: 0.1,
        nan_agreement_tol: 0.99,
    });
}

// NBM (Lambert conformal, DRT3) — measured: perfect parity at tile level
#[test]
fn tile_nbm_apcp() {
    run_tile_test(&TileTestCase {
        name: "nbm_apcp_f1",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_nbm_asnow() {
    run_tile_test(&TileTestCase {
        name: "nbm_asnow_f1",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_nbm_t2m() {
    run_tile_test(&TileTestCase {
        name: "nbm_t2m_f1",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}

// ECMWF HRES (regular lat/lon, CCSDS) — measured: perfect parity at tile level
#[test]
fn tile_ecmwf_hres_apcp() {
    run_tile_test(&TileTestCase {
        name: "ecmwf_hres_apcp_f3",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_ecmwf_hres_t2m() {
    run_tile_test(&TileTestCase {
        name: "ecmwf_hres_t2m_f3",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}

#[test]
fn tile_ecmwf_hres_snod() {
    run_tile_test(&TileTestCase {
        name: "ecmwf_hres_snod_f3",
        mean_mae_tol: 0.001,
        nan_agreement_tol: 0.99,
    });
}
