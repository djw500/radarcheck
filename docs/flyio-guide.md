# RadarCheck Fly.io Guide (For Beginners)

## What is Fly.io?

**Fly.io is like renting a computer on the internet.** Instead of running the weather server on your Mac 24/7, Fly.io runs it for you in a data center (in New Jersey, in your case). Your computer can be off and the server keeps running.

**What `radarcheck.fly.dev` is:** This is your public web address - like having your own website. Anyone who knows this URL can access it (more on security below).

---

## How Often Does the Cache Update?

The cache updates **continuously** in a loop:

```
┌────────────────────────────────────────────────────┐
│                                                    │
│   1. cache_builder.py downloads HRRR data (~5 min) │
│               ↓                                    │
│   2. Generates 24 forecast images (~2 min)         │
│               ↓                                    │
│   3. Script ends → supervisord restarts it         │
│               ↓                                    │
│   4. Repeat from step 1                            │
│                                                    │
└────────────────────────────────────────────────────┘
```

**Effective update frequency:** Every ~7-10 minutes, it checks for new HRRR runs. NOAA publishes new runs hourly, so you'll have fresh data within ~15 minutes of NOAA releasing it.

---

## Security: Who Can Access What?

| Endpoint | Who Can Access | Why |
|----------|----------------|-----|
| `/health` | Everyone | Fly.io needs this to know the server is running |
| `/` (web UI) | Everyone | The HTML forecast viewer works in any browser |
| `/location/*` | Everyone | Same as above |
| `/api/*` | **Only with API key** | For iOS app, prevents random bots |
| `/frame/*` | **Only with API key** | Same, protects the actual images |

### So can anyone see my forecasts?

**Yes, via the web UI.** If someone goes to `https://radarcheck.fly.dev`, they can see your forecasts in a browser. This is fine - it's just weather data.

**No, via the API.** The iOS app (or anyone trying to programmatically access the data) needs your secret API key. This prevents:
- Random internet bots scraping your server
- Someone building their own app using your server
- Unexpected high usage that could cost you money

### If you want to lock down the web UI too:

I can add authentication to the web interface if you want, but for a personal weather app it's usually not necessary.

---

## Your API Key

```
f928dd04aa8dd6e6e2e81160ac8cc04348dd86f2c915097243996d67da28c0ef
```

**Where this is stored:** It's a "secret" on Fly.io - encrypted and only visible to your running server. It's NOT in your code or git repo.

**When to use it:** In your iOS app, every API request includes:
```
X-API-Key: f928dd04aa8dd6e6e2e81160ac8cc04348dd86f2c915097243996d67da28c0ef
```

---

## How Much Does This Cost?

Based on Fly.io pricing:

| Item | Monthly Cost |
|------|-------------|
| Tiny VM (shared-cpu-1x, 1GB RAM) | ~$5 |
| 1GB disk for cache | ~$0.15 |
| Bandwidth (~2GB/month) | ~$0.04 |
| **Total** | **~$5-6/month** |

Fly.io bills by the second, so if the machine is idle, you pay less.

---

## Useful Commands

First, add flyctl to your path (add this to `~/.zshrc`):
```bash
export PATH="$HOME/.fly/bin:$PATH"
```

Then you can run:

| Command | What It Does |
|---------|--------------|
| `flyctl logs` | Watch live logs (see cache building in real-time) |
| `flyctl status` | Check if the app is running |
| `flyctl ssh console` | SSH into the machine (like remote desktop, but command line) |
| `flyctl secrets list` | See what secrets are set |
| `flyctl deploy` | Deploy new code after you make changes |

---

## Quick Test

Try these in your terminal:

```bash
# Should return "Invalid API key" error
curl https://radarcheck.fly.dev/api/locations

# Should return location data (once cache is built)
curl -H "X-API-Key: f928dd04aa8dd6e6e2e81160ac8cc04348dd86f2c915097243996d67da28c0ef" \
  https://radarcheck.fly.dev/api/locations

# Always works (for monitoring)
curl https://radarcheck.fly.dev/health
```

---

## Summary

- **Your server runs 24/7** on Fly.io at https://radarcheck.fly.dev
- **Cache updates every ~10 minutes** (checks for new HRRR runs hourly)
- **Web UI is public** - anyone can view forecasts in a browser (just weather data)
- **API is protected** - requires your secret key (for iOS app)
- **Cost is ~$5/month**
