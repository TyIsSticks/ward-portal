# Ward Portal

A small self-hosted portal for working with LCR report exports.

**Current features**

- **Birthdays:** upload the LCR *Birthday List* PDF. The app syncs the roster (adds move-ins, removes
  move-outs) and serves a live iCalendar feed that Google Calendar subscribes to.

Stack: FastAPI · SQLite · Jinja templates · Docker Compose · exposed via Tailscale Funnel.

## Privacy

LCR exports are *For Church Use Only*.

- Uploaded files are parsed in memory and never written to disk.
- Only **name + month/day** are stored. Phones, addresses and ages are discarded.
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

## Users

Admins manage people from the **Users** page:

- **Invite someone:** creates a single-use link (Member or Admin) that expires in 7 days. Send it
  privately. The recipient picks their own username and password. Pending invites can be revoked.
- **Reset password:** creates a single-use link for that person to set a new password. Their
  existing sessions are signed out. Only the newest reset link works.
- **Make admin / Make member / Remove.** You can't change or remove your own account, so there's
  always at least one admin.

Everyone can change their own password on the **Account** page, which signs out their other devices.

Members can upload reports and see the calendar link. Admins can also manage users and regenerate
the calendar link.

The first admin (or a lost-access recovery) is created from the server:

```bash
docker compose exec portal python -m app.cli create-user <name> [--admin]
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
