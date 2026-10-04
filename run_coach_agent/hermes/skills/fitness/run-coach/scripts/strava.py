"""
strava.py — Strava API client (standard library only).

Each athlete connects their own Strava account to the coach's Strava API
app (OAuth, scope activity:read_all). The coach then pulls their runs
AND their other workouts (strength, cross-training) automatically,
instead of waiting for .fit/.gpx files.

No public web server is needed: the authorize link redirects to
http://localhost/..., which won't load on the athlete's phone -- they
copy that address (it contains the one-time code) and send it to the
bot. Set the Strava app's "Authorization Callback Domain" to localhost.

Credentials: STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET, read from the
environment or straight from the profile's .env (cron scripts run with
a sanitised environment, so the file is the reliable source).

Strava's API Agreement: an athlete's Strava data may only be shown to
that athlete. The coach uses it in that athlete's own conversation and
never shows it to the owner or anyone else (see coach_tools).

All network calls go through _http(), which tests replace.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEAUTH_URL = "https://www.strava.com/oauth/deauthorize"
API = "https://www.strava.com/api/v3"
REDIRECT_URI = "http://localhost/strava-connected"
SCOPE = "read,activity:read_all"

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
STRENGTH_TYPES = {"WeightTraining", "Crossfit", "HighIntensityIntervalTraining", "Workout"}
MOBILITY_TYPES = {"Yoga", "Pilates"}


class StravaError(Exception):
    pass


class StravaRateLimited(StravaError):
    pass


class StravaAuthRevoked(StravaError):
    pass


def _http(method, url, data=None, headers=None):
    """Returns (status, parsed_json, response_headers). Replaced in tests."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"null")
        except ValueError:
            payload = None
        return e.code, payload, dict(e.headers or {})


def app_credentials(hermes_home):
    """(client_id, client_secret) from the environment or the profile's .env."""
    cid, secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    env = os.path.join(hermes_home, ".env") if hermes_home else None
    if (not cid or not secret) and env and os.path.exists(env):
        with open(env, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?(STRAVA_CLIENT_ID|STRAVA_CLIENT_SECRET)\s*=\s*(.*)", line)
                if m:
                    val = m.group(2).strip().strip("'\"")
                    if m.group(1) == "STRAVA_CLIENT_ID":
                        cid = cid or val
                    else:
                        secret = secret or val
    if not cid or not secret:
        raise StravaError("Strava isn't set up: add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to this profile's "
                          ".env (from strava.com/settings/api)")
    return cid, secret


def authorize_url(client_id, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "approval_prompt": "auto", "scope": SCOPE, "state": state})


def parse_redirect(text):
    """The athlete pastes the localhost address (or just the code). Returns (code, state, scope)."""
    t = (text or "").strip()
    if "code=" not in t:
        if re.fullmatch(r"[0-9a-f]{20,64}", t):
            return t, None, None
        if "error=access_denied" in t:
            raise StravaError("the athlete pressed Cancel on Strava's page; send the link again")
        raise StravaError("that doesn't look like the Strava address -- it should contain 'code='")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(t if "://" in t else "http://x/?" + t.split("?", 1)[-1]).query)
    return q.get("code", [None])[0], q.get("state", [None])[0], q.get("scope", [None])[0]


def _check(status, payload, headers, what):
    if status == 429:
        raise StravaRateLimited(f"Strava rate limit reached during {what}; it will retry on the next sync")
    if status == 401:
        raise StravaAuthRevoked(f"Strava refused access during {what} (connection revoked or expired)")
    if status >= 400:
        raise StravaError(f"Strava error {status} during {what}: {payload}")
    return payload


def exchange_code(client_id, secret, code):
    s, p, h = _http("POST", TOKEN_URL, {"client_id": client_id, "client_secret": secret, "code": code,
                                         "grant_type": "authorization_code"})
    return _check(s, p, h, "connecting")


def refresh(client_id, secret, refresh_token):
    s, p, h = _http("POST", TOKEN_URL, {"client_id": client_id, "client_secret": secret,
                                         "grant_type": "refresh_token", "refresh_token": refresh_token})
    return _check(s, p, h, "token refresh")


def deauthorize(access_token):
    s, p, h = _http("POST", DEAUTH_URL, {"access_token": access_token})
    return s < 400


def _get(token, path, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    s, p, h = _http("GET", url, headers={"Authorization": f"Bearer {token}"})
    return _check(s, p, h, f"GET {path}")


def list_activities(token, after_epoch):
    out, page = [], 1
    while True:
        batch = _get(token, "/athlete/activities", {"after": int(after_epoch), "per_page": 100, "page": page}) or []
        out.extend(batch)
        if len(batch) < 100:
            return sorted(out, key=lambda a: a.get("start_date") or "")
        page += 1


def activity_detail(token, activity_id):
    return _get(token, f"/activities/{activity_id}")


def activity_streams(token, activity_id):
    return _get(token, f"/activities/{activity_id}/streams",
                {"keys": "time,distance,heartrate,altitude,latlng,cadence", "key_by_type": "true"}) or {}


def category(activity):
    st = activity.get("sport_type") or activity.get("type") or ""
    if st in RUN_TYPES:
        return "run"
    if st in STRENGTH_TYPES:
        return "strength"
    if st in MOBILITY_TYPES:
        return "mobility"
    return "cross_training"


def _iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()


def run_summary(activity, streams):
    """A Strava run as the same dict the FIT/GPX parsers return, so the rest
    of the coach treats every run identically."""
    notes = []
    start = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    data = lambda k: (streams.get(k) or {}).get("data") or []
    t, dist, hr, alt = data("time"), data("distance"), data("heartrate"), data("altitude")
    if activity.get("manual"):
        notes.append("manual entry on Strava (no recorded data)")
    if not hr:
        notes.append("no heart rate recorded")
    if activity.get("trainer") or activity.get("sport_type") == "VirtualRun":
        notes.append("treadmill / indoor run -- distance and pace come from the device")
    points, splits = [], []
    for i in range(len(t)):
        points.append({"t": (start + timedelta(seconds=t[i])).isoformat(),
                       "dist_m": dist[i] if i < len(dist) else None,
                       "hr": hr[i] if i < len(hr) else None,
                       "alt_m": alt[i] if i < len(alt) else None})
    if t and dist:
        marker, seg_t0, seg_hr, km = 1000.0, t[0], [], 1
        for i in range(len(t)):
            if i < len(hr) and hr[i]:
                seg_hr.append(hr[i])
            if i < len(dist) and dist[i] >= marker:
                splits.append({"km": km, "time_sec": t[i] - seg_t0,
                               "avg_hr": sum(seg_hr) / len(seg_hr) if seg_hr else None,
                               "max_hr": max(seg_hr) if seg_hr else None})
                marker += 1000.0
                seg_t0, seg_hr, km = t[i], [], km + 1
    fh = sh = drift = None
    hrs = [h for h in hr if h]
    if len(hrs) >= 20:
        mid = len(hrs) // 2
        fh, sh = sum(hrs[:mid]) / mid, sum(hrs[mid:]) / (len(hrs) - mid)
        drift = sh - fh
    km_total = (activity.get("distance") or 0) / 1000.0
    moving = (activity.get("moving_time") or 0) / 60.0
    return {
        "source_format": "strava",
        "recorded_at": start.isoformat(),
        "distance_km": km_total,
        "elapsed_time_min": (activity.get("elapsed_time") or 0) / 60.0,
        "moving_time_min": moving,
        "avg_hr": activity.get("average_heartrate"),
        "max_hr": activity.get("max_heartrate"),
        "elevation_gain_m": activity.get("total_elevation_gain"),
        "elevation_loss_m": None,
        "avg_pace_sec_per_km": (moving * 60 / km_total) if km_total else None,
        "splits": splits,
        "first_half_avg_hr": fh, "second_half_avg_hr": sh, "hr_drift_delta": drift,
        "parser_notes": "; ".join(notes) if notes else None,
        "points": points,
    }


def other_summary(activity, detail=None):
    d = detail or {}
    return {
        "external_id": f"strava:{activity['id']}",
        "start_at": _iso(activity["start_date"]),
        "sport_type": activity.get("sport_type") or activity.get("type"),
        "category": category(activity),
        "name": activity.get("name"),
        "description": d.get("description"),
        "elapsed_min": round((activity.get("elapsed_time") or 0) / 60.0, 1),
        "moving_min": round((activity.get("moving_time") or 0) / 60.0, 1),
        "distance_km": round((activity.get("distance") or 0) / 1000.0, 2) or None,
        "avg_hr": activity.get("average_heartrate"),
        "max_hr": activity.get("max_heartrate"),
        "perceived_exertion": d.get("perceived_exertion"),
    }


def now_epoch():
    return int(time.time())


def epoch_of(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def days_ago_epoch(days):
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
