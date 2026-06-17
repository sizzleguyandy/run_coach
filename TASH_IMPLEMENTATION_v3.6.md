# TASH — Implementation Instructions (v3.6)

> **For:** Tash (the agent) to read and execute in the workspace
> `C:\Users\runfr\.openclaw\workspace\`.
> **Purpose:** Apply the audited fixes to the cleaning-booking system.
> **Author:** Andrew (via audit). **Date:** 2026-06-17.
> **Golden rule:** Back up both `.db` files before running any SQL. Use `trash`, not `rm`.
> Nothing here sends a message to Imperial Cleaning — sending stays gated until Andrew says go.

---

## 0. Context — what changed and why

1. **Email ingestion moved from Gmail-polling → n8n webhook push.** n8n now POSTs the
   full email (body + Message-ID) to `/hooks/agent`. The old Gmail cron jobs are retired.
2. **Source of truth:** the booking **emails are primary**, Guesty is secondary. Match/dedup
   on the OTA confirmation code.
3. **Platforms:** Booking.com, Airbnb, VRBO (was Airbnb-only).
4. **The cleaning-date bug is fixed.** Two separate numbers that v3.5 conflated:
   - **Booking request lead = 10 days before check-in** (when we message Imperial).
   - **Actual cleaning date = 1 day before check-in** (when the cleaner comes).
   - **Reminder = 3 days before the cleaning date** (= check-in − 4 days).
5. **Timezone = SAST (UTC+2, no DST).** Host runs SA time, so cron clock-times are literal
   and SQLite `datetime('now','localtime')` already equals SAST.

Worked example:
| Guest | Check-in | Request (−10) | Cleaning (−1) | Reminder (−4) |
|---|---|---|---|---|
| Andrei Buzin | Oct 11 | Oct 1 | Oct 10 | Oct 7 |
| Leandro Peraro | Nov 1 | Oct 22 | Oct 31 | Oct 28 |

---

## 1. Back up first (do this before anything else)

```
copy bookings.db bookings.backup-2026-06-17.db
copy tasks.db    tasks.backup-2026-06-17.db
```
Confirm both backups exist before proceeding.

---

## 2. Schema migration — bookings.db

SQLite only allows `ADD COLUMN`, so this is non-destructive. Run each line:

```sql
-- Source-of-truth + reconciliation
ALTER TABLE bookings ADD COLUMN status            TEXT NOT NULL DEFAULT 'confirmed'; -- confirmed | pending | cancelled
ALTER TABLE bookings ADD COLUMN confirmation_code TEXT;   -- OTA reservation code (dedup / Guesty match key)
ALTER TABLE bookings ADD COLUMN source            TEXT;   -- 'email' | 'guesty'
ALTER TABLE bookings ADD COLUMN source_message_id TEXT;   -- Gmail Message-ID that created/updated the row

-- Escalation timers (make the 4h/24h resend rules trackable)
ALTER TABLE bookings ADD COLUMN request_sent_at   TEXT;   -- when booking request sent to Imperial
ALTER TABLE bookings ADD COLUMN reminder_sent_at  TEXT;   -- when 3-day reminder sent
ALTER TABLE bookings ADD COLUMN resend_count      INTEGER NOT NULL DEFAULT 0;

-- Money as real data (keep old 'amount' TEXT for raw display / 'TBC')
ALTER TABLE bookings ADD COLUMN amount_value      REAL;   -- numeric payout, e.g. 41330.86
ALTER TABLE bookings ADD COLUMN currency          TEXT DEFAULT 'ZAR';
```

**Platform values must normalize to exactly:** `Airbnb`, `Booking.com`, `VRBO`.

---

## 3. Idempotency — stop duplicate webhook fires creating duplicate bookings

```sql
CREATE TABLE IF NOT EXISTS processed_emails (
  message_id  TEXT PRIMARY KEY,                                  -- the <...@mail.gmail.com> id
  received_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  sender      TEXT,
  subject     TEXT,
  outcome     TEXT   -- booking_created | inquiry | cancellation | imperial_reply | ignored | forwarded
);
```

**On every `/hooks/agent` hit:**
1. Read `Message ID` from the payload.
2. `SELECT 1 FROM processed_emails WHERE message_id = ?` → **if found, STOP** (duplicate).
3. Otherwise process, then `INSERT` the id with its `outcome`.

---

## 4. Data corrections (amounts)

Authoritative figures = **"You earn"** (host payout after platform fees):
- Leandro Peraro = **R41,330.86** (already correct in DB)
- Andrei Buzin = **R49,755.68** (currently `TBC`)

```sql
UPDATE bookings
SET amount = '49755.68', amount_value = 49755.68, currency = 'ZAR',
    updated_at = datetime('now','localtime')
WHERE guest_name = 'Andrei Buzin';

UPDATE bookings
SET amount_value = 41330.86, currency = 'ZAR',
    updated_at = datetime('now','localtime')
WHERE guest_name = 'Leandro Peraro';
```

---

## 5. Wire up the audit trail (booking_audit already exists but is empty)

On **every** change to a booking's `status`, `cleaning_booked`, `confirmation_received`,
`cleaning_date`, or `amount`, also insert a row:

```sql
INSERT INTO booking_audit (booking_id, field, old_value, new_value)
VALUES (:id, :field, :old, :new);
```
Log Andrei's amount change now: field=`amount`, old=`TBC`, new=`49755.68`.

---

## 6. CLEANING_SOP.md — replace with v3.6

Save the following over `CLEANING_SOP.md`. Only the timing logic and ingestion phase changed;
templates and escalation rules are unchanged.

### Key parameters (single source — change here only)
- **Booking request lead:** check-in **− 10 days**  → Phase 1 trigger
- **Cleaning date:** check-in **− 1 day**           → goes in the message + stored in DB
- **Reminder:** cleaning date **− 3 days** (= check-in − 4)
- **Business hours:** 08:00–17:00 SAST, Mon–Fri. Queue outside hours.
- **Lockbox:** 6314 (treat as sensitive)

### PHASE 0 — Email Ingestion (replaces all Gmail polling)
```
Trigger: POST /hooks/agent from n8n. Payload: From, Subject, Message ID, Date, Content (full body).
1. Dedup: Message ID already in processed_emails? → STOP.
2. Classify by sender address + content:
     @booking.com           → Booking.com
     @airbnb.com            → Airbnb
     @vrbo.com / @homeaway  → VRBO
     +27 61 381 5761        → Imperial reply (handle per Phase 3/escalation)
     else                   → inquiry / guest message / other → forward/queue per policy
3. Booking confirmation → extract guest_name, check_in, check_out, amount (You earn),
   confirmation_code, platform → INSERT into bookings (status='confirmed', source='email',
   source_message_id=<Message ID>).
4. Conflict rule: email is primary. On disagreement with Guesty, email wins unless the
   email itself is a cancellation. Match on confirmation_code.
5. Record outcome in processed_emails.
```

### PHASE 1 — Daily DB Check  (08:00 SAST)
```sql
SELECT id, guest_name, check_in, date(check_in,'-1 day') AS cleaning_date
FROM bookings
WHERE status='confirmed' AND cleaning_booked=0
  AND date(check_in) = date('now','localtime','+10 days');
```
Matches → Phase 2. No matches → end.

### PHASE 2 — Send Booking Request (business hours only)
Send to Imperial (+27 61 381 5761). **Date = cleaning_date = check-in − 1 day.**
After sending: `UPDATE bookings SET request_sent_at=datetime('now','localtime') WHERE id=:id;`
```
Hi Imperial
I would like to book a cleaning for the following address:
Address: 9 Underberg, 39 Hely Hutchinson, Camps Bay 8005
Date: [cleaning_date = Check-in − 1 day]
Lockbox Code: 6314
Special instructions for cleaning staff:
[optional — leave blank if none]
Please can you send a WhatsApp message back to confirm that the booking is booked.
Kind regards, Tash
```

### PHASE 3 — Confirmation & DB Update
On Imperial reply containing `confirmed` OR `booked`:
```sql
UPDATE bookings
SET cleaning_booked=1, confirmation_received=1,
    cleaning_date=date(check_in,'-1 day'),
    updated_at=datetime('now','localtime')
WHERE id=:id;
```

### PHASE 4 — Pre-Booking Reminder (Main DB, 08:00 SAST)
```sql
SELECT id, guest_name, cleaning_date
FROM bookings
WHERE cleaning_booked=1 AND confirmation_received=1
  AND date(cleaning_date) = date('now','localtime','+3 days');
```
Send the reminder template (Date = cleaning_date), then set `reminder_sent_at`.

### PHASE 5 — Adhoc Confirmation (Tasks DB)
On adhoc confirm: notify Andrew, then INSERT into tasks (real columns):
`task_type='Cleaning'`, `unit_address='9 Underberg, 39 Hely Hutchinson, Camps Bay 8005'`,
`task_date=<date Andrew specified>`, `status='Upcoming'`.

### PHASE 6 — Adhoc Pre-Booking Reminder (Tasks DB, 08:00 SAST)
```sql
SELECT id, unit_address, task_date
FROM tasks
WHERE task_type='Cleaning' AND status='Upcoming'
  AND date(task_date) = date('now','localtime','+3 days');
```

### Error / escalation (unchanged)
- No reply in 4 business hours → resend once (set `resend_count=1`).
- No reply in 24 hours → alert Andrew.
- Out-of-scope Imperial reply → **DO NOT REPLY**, forward to Andrew immediately.

> NOTE: SOP text now uses the **real** task columns (`unit_address`, `task_date`,
> `status`, `task_type`) — v3.5's `Address/Date/Status` wording was wrong.

---

## 7. Cron jobs

### 7a. CREATE — "Cleaning Scheduler" @ 08:00 SAST daily (isolated session)
Runs Phases 1, 4, 6 in one pass. (Host is SAST, so schedule literally at 08:00.)
- **Phase 1 query** (above) → for each, send Phase 2 request, set `request_sent_at`.
- **Phase 4 query** (above) → for each, send reminder, set `reminder_sent_at`.
- **Phase 6 query** (above) → for each, send adhoc reminder.
- 08:00 is inside business hours, so sending is allowed.

### 7b. CREATE — "Escalation Check" @ 12:00 and 16:00 SAST daily (isolated)
```sql
-- Resend once after ~4h no confirmation
SELECT id, guest_name FROM bookings
WHERE request_sent_at IS NOT NULL AND confirmation_received=0 AND resend_count=0
  AND datetime(request_sent_at,'+4 hours') < datetime('now','localtime');
-- → resend Phase 2, then UPDATE ... SET resend_count=1

-- Alert Andrew after ~24h no confirmation
SELECT id, guest_name FROM bookings
WHERE request_sent_at IS NOT NULL AND confirmation_received=0
  AND datetime(request_sent_at,'+24 hours') < datetime('now','localtime');
```

### 7c. DELETE — legacy Gmail crons (n8n replaces them)
Remove: **Email Watch (15-min)**, **Email Check 9am**, **Email Check 1pm**, **Email Check 5pm**.
Keep: **Daily Task List (7am)**. Review **Webhook Monitor (10-min)** — likely redundant now.

> Before editing the scheduler, inspect existing jobs and preserve/merge — don't blow away
> unrelated entries.

---

## 8. n8n note (already applied — record only)

HTTP Request node body parameter (name MUST be lowercase `message`):
```
New email from {{ $json.from.text }} | Subject: {{ $json.subject }} | Message ID: {{ $json.messageId }} | Date: {{ $json.date }} | Content: {{ $json.text || $json.html }}
```
Gmail/IMAP node "Simplify" must be **OFF** so `text`/`html`/`messageId` exist.

---

## 9. MEMORY.md updates

- Bookings table: Leandro = **R41,330.86 payout** (not R50,775 — that was gross/before fees);
  Andrei = **R49,755.68 payout**.
- Cleaning rule: "**Request booked 10 days before check-in; actual cleaning 1 day before
  check-in; reminder 3 days before the cleaning date.**"
- System Setup: email ingestion is now **n8n webhook → /hooks/agent** (Gmail polling retired).
- Cron list: replace the 4 Gmail jobs with **Cleaning Scheduler (08:00)** + **Escalation Check
  (12:00/16:00)**; keep Daily Task List (07:00).

---

## 10. Verification checklist (after applying)

- [ ] `bookings.db` backed up; new columns present (`PRAGMA table_info(bookings);`).
- [ ] Andrei `amount_value=49755.68`; Leandro `amount_value=41330.86`.
- [ ] `processed_emails` table exists; a test webhook is recorded and a re-send is skipped.
- [ ] `CLEANING_SOP.md` shows v3.6 with −10 request / −1 cleaning / −3 reminder.
- [ ] Cleaning Scheduler cron at 08:00 SAST; Escalation cron at 12:00 & 16:00.
- [ ] 4 Gmail crons removed; Daily Task List still present.
- [ ] MEMORY.md updated.
- [ ] Imperial Cleaning still **NOT** messaged until Andrew gives the go-ahead.
```
