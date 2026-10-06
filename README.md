# Ward Portal

A small self-hosted portal for working with LCR report exports.

**Current features**

- **One upload box** that recognizes the LCR report you give it (PDF):
  - *Member List* (directory): updates the ward roster **and** the birthday calendar.
  - *Birthday List*: updates the birthday calendar only. Still supported.
  - *Ministering Assignments*: imports what's currently in LCR (leaders only).
- **Birthdays:** a live iCalendar feed that Google Calendar or an iPhone subscribes to, with
  step-by-step phone guides on the dashboard. People missing from a new upload are **flagged, not
  deleted**. The dashboard lists them as "Possibly moved out" with *Keep* / *Remove birthday*
  buttons, and the flag clears if they show up again.
- **Ministering** (leaders): separate **Elders Quorum** and **Relief Society** pages (they're
  separate in LCR). Each has a drag-and-drop board of districts → groups → ministers and assigned
  people, saved as layouts (start from the current assignments, from scratch, or from a copy).
  Includes warnings, priority tags, companion history from past imports, Draft / Proposed / Approved
  status, and a printable **Changes for LCR** checklist. See [Ministering](#ministering).

Stack: FastAPI · SQLite · Jinja templates · Docker Compose · exposed via Tailscale Funnel.

## Privacy

LCR exports are *For Church Use Only*.

- Uploaded files are parsed in memory and never written to disk.
- Stored per person: **name, gender, birth date**, and any ministering tags leaders add.
  Phones, emails and addresses in the exports are discarded.
- The calendar feed is the only unauthenticated route, and it lives at an unguessable URL
  that an admin can regenerate.
- `.gitignore` blocks PDFs, CSVs, spreadsheets and `data/`. **Never commit real exports.**
  Tests use made-up names only.

## Deploy on the home server

Prereqs: Docker with the Compose plugin, Tailscale.

```bash
git clone <repo-url> ward-portal && cd ward-portal
cp .env.example .env          # set WARD_NAME; keep COOKIE_SECURE=true
docker compose up -d --build
docker compose exec portal python -m app.cli create-user ty --admin
```

The app now listens on `127.0.0.1:8000` on the server (not on the LAN).

### Make it public with Tailscale Funnel (free)

1. In the [Tailscale admin console](https://login.tailscale.com/admin/dns), enable **MagicDNS**
   and **HTTPS Certificates**. Optionally rename your tailnet to something nicer.
2. Allow Funnel for the node. Admin console → *Access controls*. The default policy usually already has a
   `funnel` nodeAttr. If it doesn't, the command below prints a link that adds it.
3. On the server:

   ```bash
   sudo tailscale funnel --bg 8000
   tailscale funnel status        # shows https://<machine>.<tailnet>.ts.net
   ```

4. Open that URL, sign in, and copy the **Calendar feed** link from the dashboard.
5. In Google Calendar (web): *Other calendars → + → From URL* → paste → *Add calendar*.

To turn public access off: `sudo tailscale funnel --https=443 off`.

### Updating

```bash
git pull && docker compose up -d --build
```

### Backups

Everything lives in `./data` (SQLite DB + session key). Back up that folder.

## Ministering

Elders Quorum and Relief Society each have their own page, imports, layouts and change list.
Any Leader can see and edit both.

Typical cycle (for either organization):

1. Upload the latest **Member List** so move-ins and move-outs are current. New people are tagged
   *New move-in*, and anyone who moved out is struck through wherever they appear.
2. Upload **Ministering Assignments** to import what's in LCR now. LCR's PDF doesn't say which
   organization it's from, so it's filed by the ministers: brothers → Elders Quorum, sisters →
   Relief Society. If they're mixed, the import asks. If a name doesn't exactly match
   the roster, a review page lets you pick who it is or skip it. Each import becomes the "Current in
   LCR" snapshot. Older imports are kept as history.
3. **New layout → Start from current assignments**, then drag names between groups. Tap a name to
   move it with a menu, set tags, or see their history. Everything autosaves.
4. Watch the **Warnings** panel: groups with fewer than 2 or more than 3 ministers, more than 6
   assigned, brothers and sisters as companions, a sister in an Elders Quorum group (or a brother in
   Relief Society), someone in two groups, moved-out people,
   tagged people with no ministers, and uneven districts. Warnings never block a change.
5. Set the status to **Proposed** for the bishop (give him the Leader role) and **Approved** once
   it's final. Approved layouts are locked.
6. Open **Changes for LCR** and work through the checklist in LCR.

The side lists ("Not assigned to anyone", "Not ministering") can show brothers, sisters or
everyone. They default to what each organization does in your ward, and each leader's choice is
remembered in their browser.

Companion history (“↺”) comes from past imports, so it appears once you've imported more than once.

## Users

Admins manage people from the **Users** page:

- **Invite someone:** creates a single-use link (Member or Admin) that expires in 7 days. Send it
  privately. The recipient picks their own username and password. Pending invites can be revoked.
- **Reset password:** creates a single-use link for that person to set a new password. Their
  existing sessions are signed out. Only the newest reset link works.
- **Change role / Remove.** Pick Member, Leader or Admin from the Role menu. You can't change or remove your own account, so there's
  always at least one admin.

Everyone can change their own password on the **Account** page, which signs out their other devices.

Roles: **Members** can upload the directory and see the calendar link. **Leaders** can also use
Ministering. **Admins** can also manage users and regenerate the calendar link.

The first admin (or a lost-access recovery) is created from the server:

```bash
docker compose exec portal python -m app.cli create-user <name> [--admin | --leader]
docker compose exec portal python -m app.cli set-password <name>
docker compose exec portal python -m app.cli list-users
```

Repeated failed logins lock that username for 15 minutes.

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; use .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
python -m app.cli create-user dev --admin
uvicorn app.main:app --reload
pytest
```

## Adding another report

Each LCR report gets its own module in `app/reports/` that handles parsing (raising
`ReportError` on bad input), storage and sync. Its tables go in `db.SCHEMA`, and its routes and
templates go in `main.py`/`templates/`. See `reports/birthdays.py` for the pattern.
