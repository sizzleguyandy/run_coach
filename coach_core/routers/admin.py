"""
Admin endpoints.

All endpoints require:  X-Admin-Key: <ADMIN_SECRET from .env>

GET    /admin/dashboard           — web-based admin UI (login with admin key in browser)
POST   /admin/broadcast           — send message to all athletes on Telegram
GET    /admin/stats               — platform stats
GET    /admin/athletes            — list all athletes with key fields
DELETE /admin/athletes/{id}       — delete athlete + all their logs/history
PATCH  /admin/athletes/{id}/vo2x  — update VO2X (also writes VO2XHistory record)
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from coach_core.database import get_db
from coach_core.models import Athlete, RunLog, VO2XHistory

router = APIRouter(prefix="/admin", tags=["admin"])

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured.")
    return token


def _check_admin_key(x_admin_key: str = Header(...)) -> None:
    secret = os.getenv("ADMIN_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET not set in .env")
    if x_admin_key != secret:
        raise HTTPException(status_code=401, detail="Invalid admin key.")


class BroadcastRequest(BaseModel):
    message: str                    # HTML-formatted text to send
    image_url: Optional[str] = None # optional photo URL (will send as photo + caption)
    parse_mode: str = "HTML"        # always HTML — consistent with system


class BroadcastResponse(BaseModel):
    total_athletes: int
    sent: int
    failed: int
    failed_ids: list[str]


@router.post("/broadcast", response_model=BroadcastResponse)
async def broadcast(
    body: BroadcastRequest,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Send a message to every athlete on the platform.

    Usage:
      POST /admin/broadcast
      Headers: X-Admin-Key: your_secret
      Body: { "message": "<b>Big news!</b>\n\nCheck this out...", "image_url": null }

    Rate limiting: 0.04s delay between sends (25 msg/sec, safely under Telegram's 30/sec limit).
    Large platforms: for > 5000 users consider running this as a background task.
    """
    # Fetch all telegram_ids
    result = await db.execute(select(Athlete.telegram_id))
    telegram_ids = [row[0] for row in result.fetchall()]

    if not telegram_ids:
        return BroadcastResponse(total_athletes=0, sent=0, failed=0, failed_ids=[])

    token = _get_bot_token()
    sent = 0
    failed = 0
    failed_ids: list[str] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for tid in telegram_ids:
            try:
                if body.image_url:
                    # Send photo with caption
                    url = TELEGRAM_API.format(token=token, method="sendPhoto")
                    payload = {
                        "chat_id": tid,
                        "photo": body.image_url,
                        "caption": body.message,
                        "parse_mode": body.parse_mode,
                    }
                else:
                    # Text only
                    url = TELEGRAM_API.format(token=token, method="sendMessage")
                    payload = {
                        "chat_id": tid,
                        "text": body.message,
                        "parse_mode": body.parse_mode,
                    }

                r = await client.post(url, json=payload)

                if r.status_code == 200 and r.json().get("ok"):
                    sent += 1
                else:
                    failed += 1
                    failed_ids.append(tid)

            except Exception:
                failed += 1
                failed_ids.append(tid)

            # Respect Telegram rate limit — 25 msg/sec
            await asyncio.sleep(0.04)

    return BroadcastResponse(
        total_athletes=len(telegram_ids),
        sent=sent,
        failed=failed,
        failed_ids=failed_ids,
    )


@router.get("/stats")
async def stats(
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """Quick platform stats — total athletes, plan type breakdown."""
    from sqlalchemy import func
    from coach_core.models import Athlete

    result = await db.execute(select(Athlete.plan_type, func.count()).group_by(Athlete.plan_type))
    rows = result.fetchall()
    breakdown = {row[0]: row[1] for row in rows}
    total = sum(breakdown.values())

    return {
        "total_athletes": total,
        "full_plan": breakdown.get("full", 0),
        "c25k": breakdown.get("c25k", 0),
    }


# ── LIST ALL ATHLETES ─────────────────────────────────────────────────────────

@router.get("/athletes")
async def list_athletes(
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """Return all athletes with key fields for the admin dashboard."""
    result = await db.execute(
        select(Athlete).order_by(Athlete.created_at.desc())
    )
    athletes = result.scalars().all()

    rows = []
    for a in athletes:
        # Count run logs
        log_count_result = await db.execute(
            select(RunLog).where(RunLog.athlete_id == a.id)
        )
        log_count = len(log_count_result.scalars().all())

        rows.append({
            "id":                 a.id,
            "name":               a.name,
            "telegram_id":        a.telegram_id,
            "plan_type":          a.plan_type,
            "vo2x":               a.vo2x,
            "race_name":          a.race_name,
            "race_distance":      a.race_distance,
            "race_date":          a.race_date.isoformat() if a.race_date else None,
            "preset_race_id":     a.preset_race_id,
            "long_run_day":       a.long_run_day,
            "quality_day":        a.quality_day,
            "training_profile":   a.training_profile,
            "c25k_week":          a.c25k_week,
            "c25k_completed":     a.c25k_completed,
            "streak_weeks":       a.streak_weeks,
            "total_badges":       a.total_badges,
            "link_code":          a.link_code,
            "run_log_count":      log_count,
            "created_at":         a.created_at.isoformat() if a.created_at else None,
        })

    return {"athletes": rows, "total": len(rows)}


# ── DELETE ATHLETE ────────────────────────────────────────────────────────────

@router.delete("/athletes/{athlete_id}")
async def delete_athlete(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Permanently delete an athlete and all their associated data.
    This resets their onboarding — they will be prompted to /start again in Telegram.
    """
    # Verify athlete exists
    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found.")

    name = athlete.name
    telegram_id = athlete.telegram_id

    # Delete cascade: run logs → VO2X history → athlete
    await db.execute(delete(RunLog).where(RunLog.athlete_id == athlete_id))
    await db.execute(delete(VO2XHistory).where(VO2XHistory.athlete_id == athlete_id))
    await db.execute(delete(Athlete).where(Athlete.id == athlete_id))
    await db.commit()

    return {
        "deleted": True,
        "athlete_id": athlete_id,
        "name": name,
        "telegram_id": telegram_id,
    }


# ── UPDATE VO2X ───────────────────────────────────────────────────────────────

class VO2XUpdateRequest(BaseModel):
    vo2x: float
    note: Optional[str] = None   # optional reason / note stored in VO2XHistory


@router.patch("/athletes/{athlete_id}/vo2x")
async def update_athlete_vo2x(
    athlete_id: int,
    body: VO2XUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Override an athlete's VO2X and record the change in VO2XHistory.
    The new VO2X takes effect immediately — their next /plan or /today
    will use updated paces.
    """
    if body.vo2x < 20 or body.vo2x > 85:
        raise HTTPException(status_code=422, detail="VO2X must be between 20 and 85.")

    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found.")

    old_vo2x = athlete.vo2x
    athlete.vo2x = body.vo2x

    # Record in history
    history_entry = VO2XHistory(
        athlete_id=athlete_id,
        vo2x=body.vo2x,
        source="admin_adjusted",
        effective_date=date.today(),
    )
    db.add(history_entry)
    await db.commit()

    return {
        "athlete_id":  athlete_id,
        "name":        athlete.name,
        "old_vo2x":    old_vo2x,
        "new_vo2x":    body.vo2x,
        "note":        body.note,
    }


# ── ADMIN DASHBOARD ───────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def admin_dashboard():
    """
    Web-based admin UI. Open in browser — enter your ADMIN_SECRET to log in.
    No credentials are stored server-side for this page; the key is held in
    sessionStorage and sent as X-Admin-Key on every API call.
    """
    return HTMLResponse(content=_DASHBOARD_HTML)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tr3d Admin</title>
<style>
  :root {
    --brand: #26B5A8;
    --brand-dark: #1a8f84;
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3248;
    --text: #e8eaf0;
    --muted: #8891aa;
    --danger: #e05555;
    --warning: #e0a030;
    --success: #3dcc7e;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }

  /* ── Login ── */
  #login-screen {
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .login-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 40px; width: 360px; text-align: center;
  }
  .login-card .logo { font-size: 2rem; font-weight: 800; color: var(--brand); margin-bottom: 4px; }
  .login-card .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 28px; }
  .login-card input {
    width: 100%; padding: 12px 14px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--text); font-size: 0.95rem; margin-bottom: 14px;
    outline: none;
  }
  .login-card input:focus { border-color: var(--brand); }
  .login-card .error { color: var(--danger); font-size: 0.82rem; margin-bottom: 10px; min-height: 18px; }

  /* ── Layout ── */
  #app { display: none; }
  .topbar {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 24px; height: 56px; display: flex; align-items: center;
    justify-content: space-between; position: sticky; top: 0; z-index: 10;
  }
  .topbar .brand { font-weight: 800; font-size: 1.1rem; color: var(--brand); }
  .topbar .right { display: flex; align-items: center; gap: 12px; }
  .main { padding: 28px 24px; max-width: 1200px; margin: 0 auto; }

  /* ── Stats cards ── */
  .stats-row { display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }
  .stat-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 22px; flex: 1; min-width: 140px;
  }
  .stat-card .val { font-size: 2rem; font-weight: 700; color: var(--brand); }
  .stat-card .lbl { font-size: 0.78rem; color: var(--muted); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.05em; }

  /* ── Section header ── */
  .section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 14px;
  }
  .section-header h2 { font-size: 1rem; font-weight: 600; }
  .search-box {
    padding: 8px 12px; border-radius: 7px; border: 1px solid var(--border);
    background: var(--surface2); color: var(--text); font-size: 0.88rem;
    width: 220px; outline: none;
  }
  .search-box:focus { border-color: var(--brand); }

  /* ── Table ── */
  .table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  thead th {
    background: var(--surface2); padding: 11px 14px; text-align: left;
    font-weight: 600; color: var(--muted); font-size: 0.76rem;
    text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap;
  }
  tbody tr { border-top: 1px solid var(--border); }
  tbody tr:hover { background: var(--surface2); }
  tbody td { padding: 11px 14px; vertical-align: middle; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 99px;
    font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  }
  .badge-full { background: #26B5A820; color: var(--brand); }
  .badge-c25k { background: #e0a03020; color: var(--warning); }
  .muted { color: var(--muted); }

  /* ── Buttons ── */
  .btn {
    padding: 7px 14px; border-radius: 7px; border: none; cursor: pointer;
    font-size: 0.82rem; font-weight: 600; transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.85; }
  .btn-brand { background: var(--brand); color: #fff; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-ghost { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
  .btn-sm { padding: 5px 10px; font-size: 0.78rem; }

  /* ── Modal ── */
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    z-index: 100; align-items: center; justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 28px; width: 400px; max-width: 90vw;
  }
  .modal h3 { font-size: 1rem; margin-bottom: 8px; }
  .modal p { color: var(--muted); font-size: 0.87rem; margin-bottom: 20px; line-height: 1.5; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
  .modal input {
    width: 100%; padding: 10px 12px; border-radius: 7px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 0.92rem;
    margin-bottom: 16px; outline: none;
  }
  .modal input:focus { border-color: var(--brand); }

  /* ── Toast ── */
  #toast {
    position: fixed; bottom: 24px; right: 24px; background: var(--surface2);
    border: 1px solid var(--border); border-radius: 8px; padding: 12px 18px;
    font-size: 0.88rem; display: none; z-index: 200; max-width: 320px;
  }
  #toast.show { display: block; }
  #toast.success { border-color: var(--success); color: var(--success); }
  #toast.error   { border-color: var(--danger);  color: var(--danger); }

  .empty { text-align: center; padding: 40px; color: var(--muted); }
  .spinner { color: var(--muted); text-align: center; padding: 40px; }
</style>
</head>
<body>

<!-- LOGIN -->
<div id="login-screen">
  <div class="login-card">
    <div class="logo">Tr3d</div>
    <div class="subtitle">Admin Dashboard</div>
    <input type="password" id="key-input" placeholder="Enter admin key" autocomplete="off">
    <div class="error" id="login-error"></div>
    <button class="btn btn-brand" style="width:100%;padding:12px" onclick="doLogin()">Sign in</button>
  </div>
</div>

<!-- APP -->
<div id="app">
  <div class="topbar">
    <span class="brand">Tr3d Admin</span>
    <div class="right">
      <span id="topbar-stats" class="muted" style="font-size:0.82rem"></span>
      <button class="btn btn-ghost btn-sm" onclick="doLogout()">Sign out</button>
    </div>
  </div>

  <div class="main">
    <!-- Stats -->
    <div class="stats-row">
      <div class="stat-card"><div class="val" id="s-total">—</div><div class="lbl">Total Athletes</div></div>
      <div class="stat-card"><div class="val" id="s-full">—</div><div class="lbl">Full Plan</div></div>
      <div class="stat-card"><div class="val" id="s-c25k">—</div><div class="lbl">C25K</div></div>
    </div>

    <!-- Athletes table -->
    <div class="section-header">
      <h2>Athletes</h2>
      <input class="search-box" id="search" placeholder="Search name or Telegram ID…" oninput="filterTable()">
    </div>
    <div class="table-wrap">
      <div id="table-container" class="spinner">Loading…</div>
    </div>
  </div>
</div>

<!-- DELETE MODAL -->
<div class="modal-overlay" id="delete-modal">
  <div class="modal">
    <h3>Delete athlete?</h3>
    <p id="delete-msg">This will permanently remove the athlete and all their run logs and VO2X history. This cannot be undone.</p>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal('delete-modal')">Cancel</button>
      <button class="btn btn-danger" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<!-- VO2X MODAL -->
<div class="modal-overlay" id="vo2x-modal">
  <div class="modal">
    <h3>Update VO2X</h3>
    <p id="vo2x-msg">Set a new VO2X value. Training paces update immediately.</p>
    <input type="number" id="vo2x-input" min="20" max="85" step="0.5" placeholder="e.g. 42.5">
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal('vo2x-modal')">Cancel</button>
      <button class="btn btn-brand" onclick="confirmVO2X()">Update</button>
    </div>
  </div>
</div>

<!-- TOAST -->
<div id="toast"></div>

<script>
let adminKey = '';
let allAthletes = [];
let pendingDeleteId = null;
let pendingVO2XId = null;

// ── Auth ──────────────────────────────────────────────────────────────────────

async function doLogin() {
  const key = document.getElementById('key-input').value.trim();
  if (!key) return;
  document.getElementById('login-error').textContent = '';

  // Test the key by hitting /stats
  try {
    const r = await api('/admin/stats', 'GET', null, key);
    if (r.ok) {
      adminKey = key;
      sessionStorage.setItem('adminKey', key);
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('app').style.display = 'block';
      loadDashboard();
    } else {
      document.getElementById('login-error').textContent = 'Invalid admin key.';
    }
  } catch {
    document.getElementById('login-error').textContent = 'Could not reach the server.';
  }
}

function doLogout() {
  sessionStorage.removeItem('adminKey');
  adminKey = '';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app').style.display = 'none';
  document.getElementById('key-input').value = '';
}

document.getElementById('key-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

// Auto-login if key saved in session
window.addEventListener('load', () => {
  const saved = sessionStorage.getItem('adminKey');
  if (saved) {
    document.getElementById('key-input').value = saved;
    doLogin();
  }
});

// ── API helper ────────────────────────────────────────────────────────────────

async function api(path, method = 'GET', body = null, key = null) {
  const headers = { 'Content-Type': 'application/json', 'X-Admin-Key': key || adminKey };
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  return fetch('/v1' + path, opts);
}

// ── Load dashboard ────────────────────────────────────────────────────────────

async function loadDashboard() {
  await Promise.all([loadStats(), loadAthletes()]);
}

async function loadStats() {
  try {
    const r = await api('/admin/stats');
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('s-total').textContent = d.total_athletes;
    document.getElementById('s-full').textContent  = d.full_plan;
    document.getElementById('s-c25k').textContent  = d.c25k;
    document.getElementById('topbar-stats').textContent =
      `${d.total_athletes} athlete${d.total_athletes !== 1 ? 's' : ''}`;
  } catch {}
}

async function loadAthletes() {
  document.getElementById('table-container').innerHTML = '<div class="spinner">Loading…</div>';
  try {
    const r = await api('/admin/athletes');
    if (!r.ok) { document.getElementById('table-container').innerHTML = '<div class="empty">Failed to load athletes.</div>'; return; }
    const d = await r.json();
    allAthletes = d.athletes || [];
    renderTable(allAthletes);
  } catch {
    document.getElementById('table-container').innerHTML = '<div class="empty">Error loading athletes.</div>';
  }
}

// ── Table ─────────────────────────────────────────────────────────────────────

function renderTable(athletes) {
  if (!athletes.length) {
    document.getElementById('table-container').innerHTML = '<div class="empty">No athletes found.</div>';
    return;
  }
  const rows = athletes.map(a => `
    <tr>
      <td><strong>${esc(a.name)}</strong></td>
      <td class="muted" style="font-size:0.8rem">${esc(a.telegram_id)}</td>
      <td><span class="badge badge-${a.plan_type}">${a.plan_type === 'c25k' ? 'C25K' : 'Full'}</span></td>
      <td>${a.vo2x != null ? a.vo2x : '<span class="muted">—</span>'}</td>
      <td>${esc(a.race_name || '—')}</td>
      <td>${a.race_date ? a.race_date.slice(0,10) : '<span class="muted">—</span>'}</td>
      <td>${a.run_log_count ?? 0}</td>
      <td>${a.streak_weeks ?? 0} wk / ${a.total_badges ?? 0} 🏅</td>
      <td>${a.created_at ? a.created_at.slice(0,10) : '<span class="muted">—</span>'}</td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="openVO2X(${a.id}, '${esc(a.name)}', ${a.vo2x})">VO2X</button>
        <button class="btn btn-danger btn-sm" style="margin-left:6px" onclick="openDelete(${a.id}, '${esc(a.name)}')">Delete</button>
      </td>
    </tr>
  `).join('');

  document.getElementById('table-container').innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Name</th><th>Telegram ID</th><th>Plan</th><th>VO2X</th>
          <th>Race</th><th>Race Date</th><th>Runs</th><th>Streak</th>
          <th>Joined</th><th>Actions</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function filterTable() {
  const q = document.getElementById('search').value.toLowerCase();
  const filtered = allAthletes.filter(a =>
    a.name.toLowerCase().includes(q) ||
    a.telegram_id.toLowerCase().includes(q) ||
    (a.race_name || '').toLowerCase().includes(q)
  );
  renderTable(filtered);
}

// ── Delete ────────────────────────────────────────────────────────────────────

function openDelete(id, name) {
  pendingDeleteId = id;
  document.getElementById('delete-msg').textContent =
    `Permanently delete "${name}" and all their run logs and VO2X history? This cannot be undone.`;
  document.getElementById('delete-modal').classList.add('open');
}

async function confirmDelete() {
  if (!pendingDeleteId) return;
  closeModal('delete-modal');
  try {
    const r = await api(`/admin/athletes/${pendingDeleteId}`, 'DELETE');
    if (r.ok) {
      toast('Athlete deleted.', 'success');
      await loadDashboard();
    } else {
      const d = await r.json();
      toast(d.detail || 'Delete failed.', 'error');
    }
  } catch { toast('Request failed.', 'error'); }
  pendingDeleteId = null;
}

// ── VO2X ──────────────────────────────────────────────────────────────────────

function openVO2X(id, name, current) {
  pendingVO2XId = id;
  document.getElementById('vo2x-msg').textContent =
    `Update VO2X for "${name}". Current value: ${current ?? 'not set'}. Range: 20–85.`;
  document.getElementById('vo2x-input').value = current ?? '';
  document.getElementById('vo2x-modal').classList.add('open');
}

async function confirmVO2X() {
  if (!pendingVO2XId) return;
  const val = parseFloat(document.getElementById('vo2x-input').value);
  if (isNaN(val) || val < 20 || val > 85) { toast('VO2X must be 20–85.', 'error'); return; }
  closeModal('vo2x-modal');
  try {
    const r = await api(`/admin/athletes/${pendingVO2XId}/vo2x`, 'PATCH', { vo2x: val });
    if (r.ok) {
      toast('VO2X updated.', 'success');
      await loadDashboard();
    } else {
      const d = await r.json();
      toast(d.detail || 'Update failed.', 'error');
    }
  } catch { toast('Request failed.', 'error'); }
  pendingVO2XId = null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `show ${type}`;
  setTimeout(() => { t.className = ''; }, 3000);
}
</script>
</body>
</html>"""
