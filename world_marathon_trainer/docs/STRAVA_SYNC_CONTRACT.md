# Strava → FastAPI Sync Contract

The contract every per-athlete n8n workflow builds against. As long as a workflow
sends this shape, the backend doesn't care whether there's one workflow or one per
athlete — you can change the n8n topology later without touching FastAPI.

## Flow

```
[n8n cron, per athlete]
  1. Strava OAuth (stored per workflow)
  2. GET /athlete/activities  (since last sync)
  3. filter: type == "Run"
  4. map each Strava activity -> the normalised shape below   (Set / Function node)
  5. POST  {API}/athlete/{athlete_id}/sync
```

`{athlete_id}` is the id returned by `POST /athlete` when the athlete was onboarded.
Store it in the n8n workflow (or look it up by `strava_athlete_id`).

## Endpoint

```
POST /athlete/{athlete_id}/sync
Content-Type: application/json
```

```json
{
  "activities": [
    {
      "id": "13371337",
      "start_date": "2026-06-22T05:30:00Z",
      "activity_type": "Run",
      "name": "Morning Run",
      "distance_km": 12.4,
      "moving_time_min": 64.2,
      "elapsed_time_min": 66.0,
      "total_elevation_gain_m": 88,
      "average_pace_min_per_km": 5.18,
      "average_heartrate": 148,
      "max_heartrate": 167,
      "suffer_score": 73
    }
  ],
  "recompute_weekly_km": true
}
```

Response:

```json
{ "ok": true, "created": 1, "updated": 0, "skipped": 0, "current_weekly_km": 48.2 }
```

## Field mapping (raw Strava → contract)

The n8n Function node maps Strava's raw API fields to our normalised shape:

| Contract field | Strava field | Transform |
|---|---|---|
| `id` | `id` | stringify |
| `start_date` | `start_date` | ISO 8601 (Strava already provides) |
| `activity_type` | `type` / `sport_type` | pass through (`Run`, `TrailRun`…) |
| `name` | `name` | pass through |
| `distance_km` | `distance` | **÷ 1000** (Strava is metres) |
| `moving_time_min` | `moving_time` | **÷ 60** (Strava is seconds) |
| `elapsed_time_min` | `elapsed_time` | ÷ 60 |
| `total_elevation_gain_m` | `total_elevation_gain` | pass through (metres) |
| `average_pace_min_per_km` | `average_speed` | `1000 / (speed_mps * 60)` — or omit, server derives |
| `average_heartrate` | `average_heartrate` | pass through |
| `max_heartrate` | `max_heartrate` | pass through |
| `suffer_score` | `suffer_score` | pass through (may be absent) |

**Notes**
- Only `id`, `start_date`, `distance_km`, `moving_time_min` are required. Everything
  else is optional — send what Strava gives you.
- If you omit `average_pace_min_per_km`, the server derives it from distance + time.
- **Dedup is automatic** — `id` is the primary key, so re-syncing an overlapping
  window updates existing rows rather than duplicating. Safe to over-fetch.
- Send only runs. Other activity types are accepted but ignored by the
  weekly-volume recompute.
- `recompute_weekly_km: true` makes the backend update the athlete's current
  weekly volume from the last 4 weeks of synced runs (Strava as truth).

## Why per-athlete workflows are safe here

The backend is stateless about *how* data arrives. One workflow or one-per-athlete,
the `/sync` contract is identical. If you later get Strava's higher rate limits and
consolidate to a single multi-tenant pull, only the n8n side changes — this contract
and everything below it stays the same.
