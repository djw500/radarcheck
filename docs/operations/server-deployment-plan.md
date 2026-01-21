# RadarCheck Server Deployment Guide

This document provides a complete guide to deploying the RadarCheck backend to **Fly.io**, a container-based hosting platform. It covers how Fly.io works, expected costs, and a step-by-step deployment process.

**Verdict: Fly.io is the recommended platform** because it offers persistent storage (critical for our weather cache), handles complex Python dependencies via Docker, and costs approximately **$3-5/month** for this use case.

---

## What is Fly.io?

Fly.io is a platform for running applications in lightweight virtual machines (called "Machines") on servers distributed around the world. Think of it as a simpler alternative to AWS or Google Cloud, designed for developers who want to deploy apps without managing infrastructure.

### Key Concepts

| Concept | What It Means |
|---------|---------------|
| **Machine** | A lightweight VM that runs your Docker container. You pay per second while it's running. |
| **Volume** | Persistent disk storage attached to a Machine. Data survives restarts and deploys. |
| **flyctl** | The command-line tool to manage Fly.io apps (`brew install flyctl` on Mac). |
| **fly.toml** | Configuration file that tells Fly.io how to run your app (like a recipe). |
| **Region** | Where your Machine runs physically (e.g., `ewr` = Secaucus, NJ). |

### Why Fly.io for RadarCheck?

1. **Persistent Volumes**: Our app needs to keep the `cache/` directory across deployments. Most platforms (Heroku, Render) wipe filesystems on every deploy. Fly.io Volumes persist.

2. **Docker Support**: Our app requires compiled system libraries (`libeccodes`, `libgeos`) that normal Python hosting can't provide. With Docker, we control the full environment.

3. **Simple Pricing**: No confusing tiers. You pay for what you use.

---

## Cost Breakdown

Based on Fly.io's current pricing ([fly.io/docs/about/pricing](https://fly.io/docs/about/pricing/)):

| Resource | Specification | Monthly Cost |
|----------|---------------|--------------|
| Machine (VM) | `shared-cpu-1x`, 256 MB RAM | ~$2.00 |
| Additional RAM | +512 MB (total 768 MB) | ~$2.50 |
| Volume | 1 GB persistent storage | $0.15 |
| Outbound Data | ~2 GB/month (estimated) | ~$0.04 |
| **Total** | | **~$4-5/month** |

**How billing works:**
- Machines are billed **per second** while running
- Volumes are billed **per hour** of provisioned capacity (even when Machine is stopped)
- Automatic daily volume snapshots (5-day retention) are free for the first 10 GB/month

**Important:** Fly.io requires a credit card on file for all organizations.

---

## Architecture Overview

We run both the **Flask web server** and the **cache builder** inside a single Machine, managed by `supervisord`:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Fly.io Machine (Docker)                      │
│                                                                 │
│   ┌───────────────┐          ┌───────────────┐                 │
│   │  supervisord  │──────────│  supervisord  │                 │
│   │    (web)      │          │   (worker)    │                 │
│   │               │          │               │                 │
│   │  gunicorn     │          │ cache_builder │                 │
│   │  (Flask)      │          │  (Python)     │                 │
│   └───────┬───────┘          └───────┬───────┘                 │
│           │                          │                          │
│           └──────────┬───────────────┘                          │
│                      │                                          │
│                      ▼                                          │
│           ┌──────────────────┐                                  │
│           │   /app/cache     │ ◄── Fly Volume (persists data)   │
│           │  (Fly Volume)    │                                  │
│           └──────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
                      │
                      ▼
              Internet (Your iOS App, Browser)
```

### Why One Machine Instead of Two?

Fly Volumes can only attach to **one Machine at a time**. Running the web server and cache builder separately would require:
- Separate volumes (duplicated data)
- Complex synchronization logic
- Higher costs

Running both in one Machine is simpler and sufficient for personal use.

---

## What Happens to the Cache When You Deploy?

This is an important question. Here's the lifecycle:

| Event | What Happens to `/app/cache` |
|-------|------------------------------|
| **First Deploy** | Volume is created empty. Cache builder starts populating it. |
| **Subsequent Deploys** | Volume is **detached** from old Machine, then **reattached** to new Machine. Data is preserved. |
| **Machine Restart** | Volume remains attached. Data is preserved. |
| **Machine Stops** | Volume persists (you're still billed for it). |
| **Volume Deleted** | Data is lost permanently. |

**Key insight:** Fly.io performs "rolling deploys" for apps with volumes. The old Machine is stopped *before* the new one starts (since the volume can't be shared). This causes ~10-30 seconds of downtime during deploys.

### Volume Snapshots (Backup)

Fly.io automatically takes daily snapshots of your volume and retains them for 5 days. If something goes wrong:

```bash
# List snapshots
fly volumes snapshots list

# Restore from snapshot
fly volumes create radar_cache --snapshot-id <snapshot-id>
```

---

## Required Files

You'll need to create 4 files in your project root:

### 1. `Dockerfile`

```dockerfile
FROM python:3.11-slim

# Install system dependencies for scientific Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    libeccodes0 \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    curl \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for volume mount and logs
RUN mkdir -p /app/cache /app/logs

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Start supervisor (runs both web server and cache builder)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
```

### 2. `requirements.txt` (Updated)

```txt
# Web framework
flask
gunicorn

# Data fetching
requests
pillow
pytz

# Scientific/GRIB processing
numpy
xarray
cfgrib
scipy
cartopy
geopandas
shapely
filelock
```

### 3. `supervisord.conf`

```ini
[supervisord]
nodaemon=true
logfile=/app/logs/supervisord.log
pidfile=/tmp/supervisord.pid

[program:web]
command=gunicorn -b 0.0.0.0:5000 --workers 2 --timeout 120 app:app
directory=/app
autostart=true
autorestart=true
stdout_logfile=/app/logs/web.log
stderr_logfile=/app/logs/web_error.log

[program:cache_builder]
command=python cache_builder.py --latest-only
directory=/app
autostart=true
autorestart=true
startsecs=10
stdout_logfile=/app/logs/cache_builder.log
stderr_logfile=/app/logs/cache_builder_error.log
```

**How the cache builder loop works:** The script runs once and exits. Because `autorestart=true`, supervisord restarts it immediately. This creates an infinite loop that keeps the cache fresh.

### 4. `fly.toml`

```toml
app = "radarcheck"
primary_region = "ewr"  # Secaucus, NJ - good for US East

[build]
  dockerfile = "Dockerfile"

[mounts]
  source = "radar_cache"
  destination = "/app/cache"
  initial_size = "1gb"

[http_service]
  internal_port = 5000
  force_https = true
  auto_stop_machines = false  # Keep running for cache builder
  auto_start_machines = true
  min_machines_running = 1

[[http_service.checks]]
  grace_period = "30s"
  interval = "30s"
  method = "GET"
  path = "/health"
  timeout = "5s"
```

---

## Development Workflow

### Local Development (Without Docker)

For quick iterations, continue running directly on your Mac:

```bash
# Start Flask dev server
python app.py

# In another terminal, run cache builder once
python cache_builder.py --latest-only
```

### Local Development (With Docker)

To test the exact production environment:

```bash
# Build the Docker image (do this once, or after changing Dockerfile)
docker build -t radarcheck .

# Run with your local code mounted (changes appear instantly)
docker run -it --rm \
  -p 5000:5000 \
  -v $(pwd):/app \
  -v radar_local_cache:/app/cache \
  radarcheck
```

**What `-v $(pwd):/app` does:** Mounts your current directory into the container, so edits in VS Code are reflected immediately without rebuilding.

### Testing Before Deploy

```bash
# Run pytest inside the container
docker exec -it $(docker ps -q -f ancestor=radarcheck) pytest tests/
```

### Deploying Changes

```bash
# 1. Commit your changes
git add . && git commit -m "Your message"

# 2. Deploy to Fly.io
fly deploy

# 3. Watch the logs to verify it's working
fly logs
```

---

## Initial Setup (One-Time)

### Step 1: Install Fly CLI

```bash
brew install flyctl
```

### Step 2: Create Account & Login

```bash
fly auth signup   # Creates account (opens browser)
# or
fly auth login    # If you already have an account
```

### Step 3: Initialize the App

From your project directory:

```bash
fly launch --no-deploy
```

This creates the app on Fly.io and generates `fly.toml`. Answer the prompts:
- **App name:** `radarcheck` (or your preferred name)
- **Region:** `ewr` (Secaucus, NJ) or pick one close to you
- **Would you like to set up a Postgresql database?** No
- **Would you like to set up an Upstash Redis database?** No

### Step 4: Create the Volume

```bash
fly volumes create radar_cache --region ewr --size 1
```

### Step 5: Deploy

```bash
fly deploy
```

### Step 6: Verify

```bash
# Open in browser
fly open

# Check logs
fly logs

# SSH into the machine (for debugging)
fly ssh console
```

---

## Monitoring & Troubleshooting

### Useful Commands

| Command | What It Does |
|---------|--------------|
| `fly status` | Show app status (Machine state, health checks) |
| `fly logs` | Stream live logs from All Machines |
| `fly ssh console` | SSH into the running Machine |
| `fly volumes list` | Show all volumes and their status |
| `fly scale show` | Show current Machine configuration |
| `fly deploy --strategy immediate` | Force deploy without waiting for health checks |

### Common Issues

**Cache builder failing:**
```bash
# Check the specific logs
fly ssh console -C "cat /app/logs/cache_builder_error.log"
```

**Out of memory:**
```bash
# Scale up RAM
fly scale memory 512
```

**Volume full:**
```bash
# Extend volume (can only grow, not shrink)
fly volumes extend <volume-id> --size 2
```

---

## Rollback

If a deploy goes wrong:

```bash
# List recent deployments
fly releases

# Rollback to a previous release
fly deploy --image registry.fly.io/radarcheck:v<number>
```

---

## API Security

By default, your API is **completely public**. Anyone who discovers the URL can access it. For a personal weather app, this may be acceptable, but here are your options:

### Security Options

| Approach | Protection | Complexity | Recommendation |
|----------|------------|------------|----------------|
| **None** | Zero | None | Only if you don't care who uses it |
| **API Key** | Basic | Low | ✅ **Recommended for personal use** |
| **Rate Limiting** | Abuse prevention | Low | Good addition to API key |
| **iOS App Attestation** | Strong | Medium | Overkill for personal use |
| **User Auth (JWT)** | Full | High | Only for multi-user apps |

### The Hard Truth About Mobile API Security

**You cannot completely prevent** someone from calling your API if they reverse-engineer your iOS app. Any secret embedded in the app binary can be extracted by a determined attacker. However, an API key:
- Blocks casual discovery (someone browsing to your URL)
- Prevents automated scraping
- Lets you revoke access if the key leaks

For a personal weather app, this is sufficient.

### Implementation: API Key Authentication

**Server-side (app.py):**

```python
import os
from functools import wraps
from flask import request, jsonify

# In production: set via `fly secrets set RADARCHECK_API_KEY=...`
# In development: defaults to allowing all requests
API_KEY = os.environ.get("RADARCHECK_API_KEY")

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Skip auth in development (when no key is configured)
        if API_KEY is None:
            return f(*args, **kwargs)
        
        # Check the header
        provided_key = request.headers.get("X-API-Key")
        if provided_key != API_KEY:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated

# Apply to API routes (not to web UI routes)
@app.route("/api/locations")
@require_api_key
def api_locations():
    ...

@app.route("/frame/<location_id>/<run_id>/<int:hour>")
@require_api_key  
def get_frame(location_id, run_id, hour):
    ...
```

**Key behavior:**
- When `RADARCHECK_API_KEY` is **not set** (local dev): All requests allowed
- When `RADARCHECK_API_KEY` **is set** (production): Requests must include valid key

**iOS client-side:**

```swift
// APIClient.swift
class APIClient {
    #if DEBUG
    static let baseURL = "http://localhost:5000"
    static let apiKey: String? = nil  // Not needed locally
    #else
    static let baseURL = "https://radarcheck.fly.dev"
    static let apiKey = "your-production-key-here"
    #endif
    
    func request(_ endpoint: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "\(Self.baseURL)\(endpoint)")!)
        if let key = Self.apiKey {
            request.setValue(key, forHTTPHeaderField: "X-API-Key")
        }
        return request
    }
}
```

**Setting the production key on Fly.io:**

```bash
# Generate a random key
openssl rand -hex 32
# Example output: a1b2c3d4e5f6...

# Set it as a secret (not visible in fly.toml or logs)
fly secrets set RADARCHECK_API_KEY="a1b2c3d4e5f6..."
```

---

## Full Development Workflow

This section maps out how you'll work day-to-day when developing both the server and iOS app.

### Development Environments

| Environment | Server | iOS App | API Key Required |
|-------------|--------|---------|------------------|
| **Local + Simulator** | `python app.py` on Mac | Xcode Simulator | ❌ No |
| **Local + Physical iPhone** | `python app.py` on Mac (0.0.0.0) | Your iPhone on same WiFi | ❌ No |
| **Production** | Fly.io | TestFlight or App Store | ✅ Yes |

### Scenario 1: Server-Only Changes

You're modifying `app.py`, `cache_builder.py`, or other backend code. The iOS app is unchanged.

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Mac                                                       │
│                                                                 │
│   VS Code                    Terminal                           │
│   ┌──────────────┐          ┌──────────────┐                   │
│   │ Edit app.py  │          │ python app.py│                   │
│   │              │  ──────► │ (auto-reload)│                   │
│   └──────────────┘          └──────┬───────┘                   │
│                                    │                            │
│                                    ▼                            │
│                             localhost:5000                      │
│                                    │                            │
└────────────────────────────────────┼────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
              Browser          Simulator        iPhone
             (testing)         (iOS app)      (via WiFi)
```

**Workflow:**

1. Start the server:
   ```bash
   python app.py
   ```

2. Edit code in VS Code. Flask auto-reloads on save.

3. Test in browser: `http://localhost:5000`

4. When ready, deploy:
   ```bash
   fly deploy
   ```

5. Verify production:
   ```bash
   fly logs
   curl https://radarcheck.fly.dev/health
   ```

### Scenario 2: iOS-Only Changes

You're modifying SwiftUI views, networking code, or other iOS code. The server is unchanged.

**Against local server (fastest iteration):**

1. Ensure server is running:
   ```bash
   python app.py
   ```

2. In Xcode, ensure `DEBUG` build configuration uses `localhost`:
   ```swift
   #if DEBUG
   static let baseURL = "http://localhost:5000"
   #endif
   ```

3. Run on Simulator (⌘R). Changes to Swift code require rebuild.

4. For SwiftUI previews: Many views work without a running server if you mock the data.

**Against production server (testing real environment):**

1. Temporarily change the URL:
   ```swift
   // For testing only - revert before committing
   static let baseURL = "https://radarcheck.fly.dev"
   static let apiKey = "your-key"
   ```

2. Or set up a scheme-based configuration (cleaner).

### Scenario 3: Physical iPhone Testing

The Simulator can't test everything (background downloads, real network conditions). Here's how to test on your actual iPhone:

**Step 1: Find your Mac's local IP**
```bash
ipconfig getifaddr en0
# Example: 192.168.1.42
```

**Step 2: Update iOS code for physical device**
```swift
#if DEBUG
// Use your Mac's IP for physical device testing
static let baseURL = "http://192.168.1.42:5000"
#endif
```

**Step 3: Ensure Flask binds to all interfaces**
Your `app.py` already does this:
```python
app.run(host="0.0.0.0", port=5000, debug=True)
```

**Step 4: Connect iPhone and run from Xcode**
- Phone must be on same WiFi network as Mac
- First run requires trusting the developer certificate on iPhone

### Scenario 4: End-to-End Changes (Server + iOS)

You're adding a new API endpoint and iOS UI to consume it.

**Recommended order:**

1. **Design the API contract first** (what endpoint, what JSON shape)

2. **Implement server-side:**
   ```bash
   # Terminal 1: Run server
   python app.py
   
   # Terminal 2: Test with curl
   curl http://localhost:5000/api/new-endpoint
   ```

3. **Implement iOS-side against local server:**
   - Run Simulator pointing at localhost
   - Faster iteration (no deploy needed)

4. **Deploy server changes:**
   ```bash
   fly deploy
   ```

5. **Test iOS against production:**
   - Switch URL to production
   - Verify with physical device if needed

6. **Commit both changes:**
   ```bash
   git add .
   git commit -m "Add new-endpoint API and iOS UI"
   ```

### Quick Reference: Common Commands

| Task | Command |
|------|---------|
| Start local server | `python app.py` |
| Rebuild cache locally | `python cache_builder.py --latest-only` |
| Run iOS Simulator | ⌘R in Xcode |
| Deploy to production | `fly deploy` |
| View production logs | `fly logs` |
| SSH into production | `fly ssh console` |
| Set production secret | `fly secrets set KEY=value` |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Volume data loss | Cache lost (can rebuild) | Daily snapshots (automatic, 5-day retention) |
| Fly.io outage | App unavailable | Accept for personal use; cache on iOS as backup |
| Cost overrun | Unexpected bills | Set billing alerts in Fly.io dashboard |
| NOAA API changes | Cache builder breaks | Monitor logs; update parsing code |

---

## Summary

- **Total estimated cost:** $3-5/month
- **Deployment time:** ~2 minutes per `fly deploy`
- **Downtime during deploy:** ~10-30 seconds
- **Cache persistence:** Guaranteed via Fly Volume (survives deploys)
- **Local dev:** Continue using `python app.py` for speed; use Docker for env testing
