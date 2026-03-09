//! Radarcheck API server — Axum replacement for Flask app.
//!
//! Serves the forecast UI, status dashboard, and all API endpoints.
//! Uses mmap'd .rctile files for zero-copy point queries.

mod status;

use std::collections::HashMap;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};
use std::time::{Instant, SystemTime};

use axum::body::Body;
use axum::extract::{Query, State};
use axum::http::{Request, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{Html, IntoResponse, Json, Response};
use axum::routing::{get, post};
use axum::Router;
use clap::Parser;
use log::info;
use serde::Deserialize;
use tower_http::services::ServeDir;

use memmap2::Mmap;

use radarcheck_core::config;
use radarcheck_core::rctile_v2;
use radarcheck_core::tile_query;

// ── App state ────────────────────────────────────────────────────────────────

/// Cache of memory-mapped .rctile files, keyed by path with mtime invalidation.
pub struct MmapCache {
    entries: RwLock<HashMap<PathBuf, (SystemTime, Mmap)>>,
}

impl MmapCache {
    pub fn new() -> Self {
        Self {
            entries: RwLock::new(HashMap::new()),
        }
    }

    /// Query a v2 rctile file for all runs at a point. Opens and caches mmap if needed.
    pub fn query_point_v2(
        &self,
        path: &Path,
        lat: f64,
        lon: f64,
    ) -> Option<rctile_v2::PointResult> {
        let mtime = std::fs::metadata(path).ok()?.modified().ok()?;

        // Try read cache
        {
            let cache = self.entries.read().ok()?;
            if let Some((cached_mtime, mmap)) = cache.get(path) {
                if *cached_mtime == mtime {
                    return rctile_v2::query_point_v2(mmap.as_ref(), lat, lon).ok();
                }
            }
        }

        // Cache miss or stale — open under write lock
        let mut cache = self.entries.write().ok()?;

        // Double-check
        if let Some((cached_mtime, mmap)) = cache.get(path) {
            if *cached_mtime == mtime {
                return rctile_v2::query_point_v2(mmap.as_ref(), lat, lon).ok();
            }
        }

        let file = std::fs::File::open(path).ok()?;
        let mmap = unsafe { Mmap::map(&file).ok()? };
        let result = rctile_v2::query_point_v2(mmap.as_ref(), lat, lon).ok();
        cache.insert(path.to_path_buf(), (mtime, mmap));
        result
    }
}

#[derive(Clone)]
pub struct AppState {
    pub db_path: PathBuf,
    pub tiles_dir: PathBuf,
    pub api_key: Option<String>,
    pub app_root: PathBuf,
    pub cache_dir: PathBuf,
    pub mmap_cache: Arc<MmapCache>,
}

// ── CLI ──────────────────────────────────────────────────────────────────────

#[derive(Parser, Debug)]
#[command(version, about = "Radarcheck API server")]
struct Args {
    /// Port to listen on
    #[arg(short, long, default_value_t = 5001)]
    port: u16,

    /// Application root directory (for templates/static)
    #[arg(long, default_value = "/app")]
    app_root: String,

    /// Path to jobs.db
    #[arg(long, default_value = "cache/jobs.db")]
    db_path: String,

    /// Path to tiles directory
    #[arg(long, default_value = "cache/tiles")]
    tiles_dir: String,

    /// Cache directory
    #[arg(long, default_value = "cache")]
    cache_dir: String,
}

// ── Main ─────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let args = Args::parse();
    let app_root = PathBuf::from(&args.app_root);

    let state = Arc::new(AppState {
        db_path: PathBuf::from(&args.db_path),
        tiles_dir: PathBuf::from(&args.tiles_dir),
        api_key: std::env::var("RADARCHECK_API_KEY").ok(),
        app_root: app_root.clone(),
        cache_dir: PathBuf::from(&args.cache_dir),
        mmap_cache: Arc::new(MmapCache::new()),
    });

    let static_dir = app_root.join("static");

    let app = Router::new()
        // Core routes
        .route("/", get(index_page))
        .route("/health", get(health_check))
        .route("/metrics", get(metrics))
        .route("/status", get(status_page))
        .route("/writeup", get(writeup_page))
        // Forecast API
        .route("/api/timeseries/multirun", get(api_timeseries_multirun))
        .route("/api/timeseries/stitched", get(api_timeseries_stitched))
        .route("/api/qualitative", get(api_qualitative))
        // Status API
        .route("/api/status/summary", get(status::api_status_summary))
        .route("/api/status/run-grid", get(status::api_status_run_grid))
        .route("/api/status/logs", get(status::api_status_logs))
        // Job management API
        .route("/api/jobs/list", get(status::api_jobs_list))
        .route("/api/jobs/retry-failed", post(status::api_jobs_retry_failed))
        .route("/api/jobs/cancel", post(status::api_jobs_cancel))
        .route("/api/jobs/enqueue-run", post(status::api_jobs_enqueue_run))
        // Writeup API
        .route("/api/writeup", get(api_writeup_get).post(api_writeup_save))
        .route("/api/writeup/audio", get(api_writeup_audio))
        .route("/api/writeup/audio/status", get(api_writeup_audio_status))
        .route("/api/writeup/audio/generate", post(api_writeup_audio_generate))
        // Static files
        .nest_service("/static", ServeDir::new(&static_dir))
        // Middleware
        .layer(middleware::from_fn_with_state(state.clone(), auth_middleware))
        .layer(middleware::from_fn(logging_middleware))
        .with_state(state);

    let addr = format!("0.0.0.0:{}", args.port);
    info!("Radarcheck server starting on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

// ── Middleware ────────────────────────────────────────────────────────────────

async fn auth_middleware(
    State(state): State<Arc<AppState>>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let path = request.uri().path().to_string();

    // Skip auth for public endpoints
    if path == "/health" || path == "/metrics" || path.starts_with("/static/") {
        return next.run(request).await;
    }

    // If no API key configured, allow all
    let api_key = match &state.api_key {
        Some(k) => k,
        None => return next.run(request).await,
    };

    // Check header or query param
    let provided_key = request
        .headers()
        .get("x-api-key")
        .and_then(|v| v.to_str().ok())
        .map(String::from)
        .or_else(|| {
            request
                .uri()
                .query()
                .and_then(|q| {
                    // Parse query string manually for api_key
                    q.split('&')
                        .find_map(|pair| {
                            let mut parts = pair.splitn(2, '=');
                            let key = parts.next()?;
                            let val = parts.next()?;
                            if key == "api_key" {
                                Some(val.to_string())
                            } else {
                                None
                            }
                        })
                })
        });

    match provided_key {
        Some(ref k) if k == api_key => next.run(request).await,
        _ => (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "Invalid or missing API key"})),
        )
            .into_response(),
    }
}

async fn logging_middleware(request: Request<Body>, next: Next) -> Response {
    let path = request.uri().path().to_string();
    let method = request.method().clone();
    let start = Instant::now();

    let response = next.run(request).await;

    let elapsed = start.elapsed().as_secs_f64();
    let status = response.status().as_u16();
    if !path.starts_with("/static/") {
        info!("{} {} {} {:.3}s", method, path, status, elapsed);
    }

    response
}

// ── Template helpers ─────────────────────────────────────────────────────────

async fn serve_template(state: &AppState, name: &str) -> Response {
    let path = state.app_root.join("templates").join(name);
    match tokio::fs::read_to_string(&path).await {
        Ok(content) => Html(content).into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "Template not found").into_response(),
    }
}

// ── Core routes ──────────────────────────────────────────────────────────────

async fn index_page(State(state): State<Arc<AppState>>) -> Response {
    serve_template(&state, "index.html").await
}

async fn status_page(State(state): State<Arc<AppState>>) -> Response {
    serve_template(&state, "status.html").await
}

async fn writeup_page(State(state): State<Arc<AppState>>) -> Response {
    serve_template(&state, "writeup.html").await
}

async fn health_check(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let tile_runs = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || {
            let region = &config::NE_REGION;
            let res = config::get_tile_resolution(region, "hrrr");
            tile_query::list_tile_runs(&db_path, region.id, res, "hrrr").unwrap_or_default()
        }
    })
    .await
    .unwrap_or_default();

    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string();
    Json(serde_json::json!({
        "status": "ok",
        "version": env!("CARGO_PKG_VERSION"),
        "build_time": option_env!("BUILD_TIME").unwrap_or("dev"),
        "git_sha": option_env!("GIT_SHA").unwrap_or("dev"),
        "timestamp": now,
        "tile_runs": tile_runs,
    }))
}

async fn metrics() -> (StatusCode, &'static str) {
    // Prometheus metrics not ported yet (low priority)
    (StatusCode::OK, "# radarcheck_server_info 1\n")
}

// ── Forecast API ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct MultirunParams {
    lat: Option<f64>,
    lon: Option<f64>,
    model: Option<String>,
    variable: Option<String>,
    region: Option<String>,
    days: Option<f64>,
}

async fn api_timeseries_multirun(
    State(state): State<Arc<AppState>>,
    Query(params): Query<MultirunParams>,
) -> Response {
    let lat = match params.lat {
        Some(v) => v,
        None => return error_response(400, "lat and lon are required"),
    };
    let lon = match params.lon {
        Some(v) => v,
        None => return error_response(400, "lat and lon are required"),
    };

    let requested_model = params.model.unwrap_or_else(|| "all".to_string());
    let variable_id = params.variable.unwrap_or_else(|| "asnow".to_string());
    let days_back = params.days.unwrap_or(1.0);

    let region_id = match params.region {
        Some(r) => r,
        None => match config::infer_region_for_latlon(lat, lon) {
            Some(r) => r.to_string(),
            None => return error_response(400, "Point outside configured regions"),
        },
    };

    if config::get_region(&region_id).is_none() {
        return error_response(400, "Invalid region");
    }

    let var_config = config::get_variable(&variable_id);
    let is_accumulation = var_config.as_ref().map(|v| v.is_accumulation).unwrap_or(false);

    // Move heavy work to blocking thread
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        let tiles_dir = state.tiles_dir.clone();
        let region_id = region_id.clone();
        let variable_id = variable_id.clone();
        let requested_model = requested_model.clone();
        let mmap_cache = state.mmap_cache.clone();
        move || {
            multirun_blocking(
                &db_path,
                &tiles_dir,
                &region_id,
                &requested_model,
                &variable_id,
                lat,
                lon,
                days_back,
                is_accumulation,
                &mmap_cache,
            )
        }
    })
    .await;

    match result {
        Ok(Ok(runs)) => Json(serde_json::json!({
            "lat": lat,
            "lon": lon,
            "variable": variable_id,
            "region": region_id,
            "runs": runs,
        }))
        .into_response(),
        Ok(Err(e)) => error_response(500, &format!("{:#}", e)),
        Err(e) => error_response(500, &format!("Task join error: {}", e)),
    }
}

fn multirun_blocking(
    _db_path: &Path,
    tiles_dir: &Path,
    region_id: &str,
    requested_model: &str,
    variable_id: &str,
    lat: f64,
    lon: f64,
    days_back: f64,
    is_accumulation: bool,
    mmap_cache: &MmapCache,
) -> anyhow::Result<serde_json::Map<String, serde_json::Value>> {
    let cutoff_seconds = (days_back * 86400.0) as i64;
    let now_unix = chrono::Utc::now().timestamp();
    let cutoff_unix = now_unix - cutoff_seconds;

    let models_to_query: Vec<&str> = if requested_model == "all" {
        config::ALL_MODEL_IDS.to_vec()
    } else if config::get_model(requested_model).is_some() {
        vec![requested_model]
    } else {
        anyhow::bail!("Invalid model");
    };

    let mut results = serde_json::Map::new();

    for model_id in models_to_query {
        let res = config::get_tile_resolution_for_variable(region_id, model_id, variable_id);
        let res_dir = config::format_res_dir(res);

        // Collect all runs from per-run rctile files in variable directory
        let var_dir = tiles_dir
            .join(region_id)
            .join(&res_dir)
            .join(model_id)
            .join(variable_id);

        let point_runs = read_all_runs_from_dir(&var_dir, lat, lon, mmap_cache);

        // Fallback: try legacy single-file path if no per-run files found
        let point_runs = if point_runs.is_empty() {
            let legacy_path = tiles_dir
                .join(region_id)
                .join(&res_dir)
                .join(model_id)
                .join(format!("{}.rctile", variable_id));
            match mmap_cache.query_point_v2(&legacy_path, lat, lon) {
                Some(r) => r.runs,
                None => continue,
            }
        } else {
            point_runs
        };

        for run_data in &point_runs {
            if run_data.init_unix < cutoff_unix {
                continue;
            }

            let init_time = tile_query::parse_run_id_to_init_iso(&run_data.run_id)
                .unwrap_or_else(|| unix_to_iso(run_data.init_unix));

            let accum_values: Vec<f64> = if is_accumulation {
                tile_query::accumulate_timeseries(&run_data.values)
            } else {
                run_data.values.iter().map(|&v| v as f64).collect()
            };

            let mut series = Vec::new();
            let is_dswrf = variable_id == "dswrf";
            for (i, &h) in run_data.hours.iter().enumerate() {
                if i >= accum_values.len() {
                    break;
                }
                let v = accum_values[i];
                if v.is_nan() {
                    continue;
                }
                let valid_unix = run_data.init_unix + (h as i64) * 3600;
                let valid_time = unix_to_iso(valid_unix);

                if is_dswrf {
                    match radarcheck_core::solar::clearness_index_from_unix(v, lat, lon, valid_unix)
                    {
                        Some(pct) => {
                            series.push(serde_json::json!({
                                "valid_time": valid_time,
                                "forecast_hour": h,
                                "value": (pct * 10.0).round() / 10.0,
                            }));
                        }
                        None => {
                            series.push(serde_json::json!({
                                "valid_time": valid_time,
                                "forecast_hour": h,
                                "value": null,
                            }));
                        }
                    }
                } else {
                    series.push(serde_json::json!({
                        "valid_time": valid_time,
                        "forecast_hour": h,
                        "value": v,
                    }));
                }
            }

            if !series.is_empty() {
                let key = format!("{}/{}", model_id, run_data.run_id);
                results.insert(
                    key,
                    serde_json::json!({
                        "model_id": model_id,
                        "run_id": run_data.run_id,
                        "init_time": init_time,
                        "series": series,
                    }),
                );
            }
        }
    }

    Ok(results)
}

// ── Stitched endpoint ────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct StitchedParams {
    lat: Option<f64>,
    lon: Option<f64>,
    model: Option<String>,
    variable: Option<String>,
    region: Option<String>,
    days: Option<f64>,
}

async fn api_timeseries_stitched(
    State(state): State<Arc<AppState>>,
    Query(params): Query<StitchedParams>,
) -> Response {
    let lat = match params.lat {
        Some(v) => v,
        None => return error_response(400, "lat and lon are required"),
    };
    let lon = match params.lon {
        Some(v) => v,
        None => return error_response(400, "lat and lon are required"),
    };

    let model_id = params.model.unwrap_or_else(|| "hrrr".to_string());
    let variable_id = params.variable.unwrap_or_else(|| "asnow".to_string());
    let days_back = params.days.unwrap_or(2.0);

    let region_id = match params.region {
        Some(r) => r,
        None => match config::infer_region_for_latlon(lat, lon) {
            Some(r) => r.to_string(),
            None => return error_response(400, "Point outside configured regions"),
        },
    };

    if config::get_region(&region_id).is_none() {
        return error_response(400, "Invalid region");
    }

    let var_config = config::get_variable(&variable_id);
    let is_accumulation = var_config.as_ref().map(|v| v.is_accumulation).unwrap_or(false);

    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        let tiles_dir = state.tiles_dir.clone();
        let region_id = region_id.clone();
        let variable_id = variable_id.clone();
        let model_id = model_id.clone();
        let mmap_cache = state.mmap_cache.clone();
        move || {
            stitched_blocking(
                &db_path,
                &tiles_dir,
                &region_id,
                &model_id,
                &variable_id,
                lat,
                lon,
                days_back,
                is_accumulation,
                &mmap_cache,
            )
        }
    })
    .await;

    match result {
        Ok(Ok(response)) => Json(response).into_response(),
        Ok(Err(e)) => {
            let msg = format!("{:#}", e);
            if msg.contains("No data") {
                error_response(404, &msg)
            } else {
                error_response(500, &msg)
            }
        }
        Err(e) => error_response(500, &format!("Task join error: {}", e)),
    }
}

fn stitched_blocking(
    _db_path: &Path,
    tiles_dir: &Path,
    region_id: &str,
    model_id: &str,
    variable_id: &str,
    lat: f64,
    lon: f64,
    days_back: f64,
    is_accumulation: bool,
    mmap_cache: &MmapCache,
) -> anyhow::Result<serde_json::Value> {
    let cutoff_seconds = (days_back * 86400.0) as i64;
    let now_unix = chrono::Utc::now().timestamp();
    let cutoff_unix = now_unix - cutoff_seconds;

    let res = config::get_tile_resolution_for_variable(region_id, model_id, variable_id);
    let res_dir = config::format_res_dir(res);

    // Read from per-run rctile files in variable directory
    let var_dir = tiles_dir
        .join(region_id)
        .join(&res_dir)
        .join(model_id)
        .join(variable_id);

    let mut all_point_runs = read_all_runs_from_dir(&var_dir, lat, lon, mmap_cache);

    // Fallback: try legacy single-file path
    if all_point_runs.is_empty() {
        let legacy_path = tiles_dir
            .join(region_id)
            .join(&res_dir)
            .join(model_id)
            .join(format!("{}.rctile", variable_id));
        if let Some(r) = mmap_cache.query_point_v2(&legacy_path, lat, lon) {
            all_point_runs = r.runs;
        }
    }

    if all_point_runs.is_empty() {
        anyhow::bail!("No data available");
    }

    // Wrap in a PointResult-like structure for the rest of the function
    let point_result = rctile_v2::PointResult { runs: all_point_runs };

    // Collect all runs with their data
    struct RunInfo {
        init_unix: i64,
        run_id: String,
        /// valid_time_unix -> accumulated value
        point_map: std::collections::BTreeMap<i64, f64>,
    }

    let mut run_data: Vec<RunInfo> = Vec::new();

    for rd in &point_result.runs {
        if rd.init_unix < cutoff_unix {
            continue;
        }

        let accum_values: Vec<f64> = if is_accumulation {
            tile_query::accumulate_timeseries(&rd.values)
        } else {
            rd.values.iter().map(|&v| v as f64).collect()
        };

        let mut point_map = std::collections::BTreeMap::new();
        for (i, &h) in rd.hours.iter().enumerate() {
            if i >= accum_values.len() {
                break;
            }
            let v = accum_values[i];
            if v.is_nan() {
                continue;
            }
            let vt = rd.init_unix + (h as i64) * 3600;
            point_map.insert(vt, v);
        }

        if !point_map.is_empty() {
            run_data.push(RunInfo {
                init_unix: rd.init_unix,
                run_id: rd.run_id.clone(),
                point_map,
            });
        }
    }

    if run_data.is_empty() {
        anyhow::bail!("No data available");
    }

    run_data.sort_by_key(|r| r.init_unix);

    // Latest run (forecast going forward)
    let latest = &run_data[run_data.len() - 1];
    let ext_init = latest.init_unix;

    // Build baseline: chain 1-hour verified segments
    let pre_runs: Vec<&RunInfo> = run_data.iter().filter(|r| r.init_unix < ext_init).collect();
    let mut baseline = 0.0;

    for idx in 0..pre_runs.len() {
        let curr = pre_runs[idx];
        let next_init = if idx + 1 < pre_runs.len() {
            pre_runs[idx + 1].init_unix
        } else {
            ext_init
        };

        let mut accum_at_handoff = 0.0;
        for (&vt, &val) in &curr.point_map {
            if vt <= next_init {
                accum_at_handoff = val;
            } else {
                break;
            }
        }
        baseline += accum_at_handoff;
    }

    // Result: baseline + latest extended run
    let mut series = Vec::new();
    let mut event_total: f64 = 0.0;
    let is_dswrf = variable_id == "dswrf";

    for (&vt, &val) in &latest.point_map {
        let total = baseline + val;
        event_total = event_total.max(total);

        if is_dswrf {
            match radarcheck_core::solar::clearness_index_from_unix(total, lat, lon, vt) {
                Some(pct) => {
                    series.push(serde_json::json!({
                        "valid_time": unix_to_iso(vt),
                        "value": (pct * 10.0).round() / 10.0,
                        "source_run": latest.run_id,
                    }));
                }
                None => {
                    series.push(serde_json::json!({
                        "valid_time": unix_to_iso(vt),
                        "value": null,
                        "source_run": latest.run_id,
                    }));
                }
            }
        } else {
            series.push(serde_json::json!({
                "valid_time": unix_to_iso(vt),
                "value": round2(total),
                "source_run": latest.run_id,
            }));
        }
    }

    Ok(serde_json::json!({
        "lat": lat,
        "lon": lon,
        "model": model_id,
        "variable": variable_id,
        "event_total": round2(event_total),
        "baseline_accumulated": round2(baseline),
        "latest_run": latest.run_id,
        "runs_in_baseline": pre_runs.len(),
        "series": series,
    }))
}

// ── Qualitative endpoint ─────────────────────────────────────────────────────

#[derive(Deserialize)]
struct QualitativeParams {
    lat: Option<f64>,
    lon: Option<f64>,
}

async fn api_qualitative(
    State(state): State<Arc<AppState>>,
    Query(_params): Query<QualitativeParams>,
) -> Response {
    // Hardcoded to Radnor, PA — single-location deployment
    let cache_file = state.cache_dir.join("qualitative").join("40.0_-75.4.json");

    match std::fs::read_to_string(&cache_file) {
        Ok(contents) => {
            Response::builder()
                .status(200)
                .header("content-type", "application/json")
                .header("cache-control", "public, max-age=300")
                .body(Body::from(contents))
                .unwrap()
        }
        Err(_) => error_response(404, "No qualitative data available"),
    }
}

// ── Writeup endpoints ────────────────────────────────────────────────────────

async fn api_writeup_get(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let writeup_path = state.cache_dir.join("forecast_writeup.json");
    let data = match tokio::fs::read_to_string(&writeup_path).await {
        Ok(content) => serde_json::from_str::<serde_json::Value>(&content).ok(),
        Err(_) => None,
    };
    Json(serde_json::json!({ "writeup": data }))
}

#[derive(Deserialize)]
struct WriteupSaveBody {
    title: Option<String>,
    body: Option<String>,
    detail: Option<String>,
    location: Option<String>,
}

async fn api_writeup_save(
    State(state): State<Arc<AppState>>,
    Json(body): Json<WriteupSaveBody>,
) -> Response {
    let text = match &body.body {
        Some(t) if !t.trim().is_empty() => t.trim().to_string(),
        _ => return error_response(400, "body is required"),
    };

    let now = chrono::Utc::now().to_rfc3339();
    let title = body
        .title
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .unwrap_or("Forecast Writeup");

    let data = serde_json::json!({
        "title": title,
        "body": text,
        "detail": body.detail.as_deref().map(|s| s.trim()).filter(|s| !s.is_empty()),
        "location": body.location,
        "created_at": &now,
        "updated_at": &now,
    });

    let writeup_path = state.cache_dir.join("forecast_writeup.json");
    match tokio::fs::write(&writeup_path, serde_json::to_string_pretty(&data).unwrap()).await {
        Ok(_) => Json(serde_json::json!({ "ok": true, "updated_at": now })).into_response(),
        Err(e) => error_response(500, &format!("Failed to save: {}", e)),
    }
}

async fn api_writeup_audio(State(state): State<Arc<AppState>>) -> Response {
    // Look for audio file in cache
    let audio_path = state.cache_dir.join("forecast_audio.mp3");
    if !audio_path.exists() {
        return error_response(404, "No audio available");
    }
    match tokio::fs::read(&audio_path).await {
        Ok(data) => Response::builder()
            .header("content-type", "audio/mpeg")
            .body(Body::from(data))
            .unwrap(),
        Err(_) => error_response(404, "No audio available"),
    }
}

async fn api_writeup_audio_status(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let audio_path = state.cache_dir.join("forecast_audio.mp3");
    Json(serde_json::json!({
        "has_audio": audio_path.exists(),
        "generating": false,
    }))
}

async fn api_writeup_audio_generate() -> Response {
    // Audio generation requires Python TTS — return 501
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({
            "error": "Audio generation requires Python TTS (not available in Rust server)"
        })),
    )
        .into_response()
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// Read all runs from per-run rctile files in a variable directory.
/// Each file contains exactly 1 run. Returns all PointRunData collected.
fn read_all_runs_from_dir(
    var_dir: &Path,
    lat: f64,
    lon: f64,
    mmap_cache: &MmapCache,
) -> Vec<rctile_v2::PointRunData> {
    let mut runs = Vec::new();
    let entries = match std::fs::read_dir(var_dir) {
        Ok(e) => e,
        Err(_) => return runs,
    };
    for entry in entries.filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.extension() != Some(OsStr::new("rctile")) {
            continue;
        }
        let name = match path.file_name().and_then(|n| n.to_str()) {
            Some(n) if n.starts_with("run_") => n,
            _ => continue,
        };
        let _ = name;
        if let Some(pr) = mmap_cache.query_point_v2(&path, lat, lon) {
            runs.extend(pr.runs);
        }
    }
    runs
}

fn error_response(status: u16, message: &str) -> Response {
    let status_code = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status_code, Json(serde_json::json!({"error": message}))).into_response()
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn unix_to_iso(unix: i64) -> String {
    let dt = chrono::DateTime::from_timestamp(unix, 0).unwrap_or_default();
    dt.to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}
