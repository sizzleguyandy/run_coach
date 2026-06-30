# Strava Pipeline — closing the loop

`scripts/strava_sync.py` pulls an athlete's runs from Strava, maps them to the
engine's contract, POSTs them, and triggers adaptation. Run it per athlete.

## One-time: Strava app + per-athlete OAuth

1. Create a Strava API application: https://www.strava.com/settings/api
   → note the **Client ID** and **Client Secret**.
2. Each athlete authorises once (OAuth `activity:read_all`). The callback returns
   a code you exchange for a **refresh_token** (long-lived). Store per athlete:
   `client_id`, `client_secret`, `refresh_token`, and their engine `athlete_id`.

The script exchanges the refresh_token for a short-lived access token on every
run, so it's stateless — no token files to manage.

## Onboard an athlete from Strava history

```bash
python scripts/strava_sync.py \
  --athlete-id <new>  # ignored in onboard; engine assigns the id \
  --onboard --name "Andrew" --race-id cape_town --race-date 2027-05-23 --days 5 \
  --client-id $SID --client-secret $SSEC --refresh-token $SREF
# prints the new athlete_id — store it for this athlete
```

Pulls ~8 weeks, derives fitness, creates the athlete, prints the `athlete_id`.

## Daily sync + adapt

```bash
STRAVA_CLIENT_ID=$SID STRAVA_CLIENT_SECRET=$SSEC STRAVA_REFRESH_TOKEN=$SREF \
  python scripts/strava_sync.py --athlete-id <athlete_id> --days-back 14
```

Pulls recent runs → `POST /sync` (deduped) → `POST /adapt` (bounded VDOT nudge,
prints coaching notes/flags).

## Scheduling — three options

- **cron** (simplest): one line per athlete, daily.
  `0 4 * * *  STRAVA_*=... python .../strava_sync.py --athlete-id abc --days-back 14`
- **n8n** (per athlete): a Schedule node → Execute-Command node running this
  script, OR replicate the HTTP calls as n8n nodes (Strava OAuth credential,
  HTTP request, then POST to the engine). The script keeps n8n trivial.
- **systemd timer**: same idea as cron with better logging.

## Testing / manual import (no Strava creds)

`--from-file` reads raw Strava-shaped activities from JSON instead of the API —
handy for testing, backfills, or importing an export:

```bash
python scripts/strava_sync.py --athlete-id abc --from-file runs.json
```

## What the script handles

- token refresh, pagination, runs-only filtering (rides/swims dropped)
- unit conversion (metres→km, seconds→min, m/s→min/km pace)
- dedup is automatic (engine keys on Strava activity id)
- adaptation only fires once a plan week has actually completed
