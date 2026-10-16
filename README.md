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
- **GroupMe bots:** a birthday bot wishes people happy birthday in the group chat, and a lesson bot
  posts next Sunday's lesson every Monday, each in a built-in voice or your own words. See
  [GroupMe bots](#groupme-bots).
- **Lessons** (leaders): plan Elders Quorum and Relief Society lessons from the latest general
  conference on a drag-and-drop board of Sundays. The talks come straight from the Church website
  (or a printed PDF), First Presidency and Twelve talks are tracked until each has a lesson, and
  stake conference, fifth Sundays and "no second hour" weeks are skipped. See [Lessons](#lessons).

**Multiple wards:** each ward has its own members, birthdays, calendar link, and Elders Quorum and
Relief Society ministering, stored in its own database file (`data/wards/ward-<id>.db`). Accounts
belong to one ward and only see that ward. Admins see every ward: they switch wards from the ward
name in the header and add wards on the **Wards** page.

**Layout:** the dashboard is a hub with a tile per area (Birthdays, Elders Quorum, Relief Society, Lessons)
and a to-do list. Uploads live behind the **Upload report** button in the header. Each person can
pick a **System / Light / Dark** theme from the account menu (top right). System follows their device.

Stack: FastAPI · SQLite · Jinja templates · Docker Compose · exposed via Tailscale Funnel.

## Privacy

LCR exports are *For Church Use Only*.

- Uploaded files are parsed in memory and never written to disk.
- Stored per person: **name, gender, birth date**, and any ministering tags leaders add.
  Phones, emails and addresses in the exports are discarded.
- The calendar feeds (birthdays, lessons, each teacher's lessons) are the only unauthenticated
  routes, and each lives at an unguessable URL that can be regenerated.
- Lesson plans store teacher names (typed, or picked from the roster) and nothing else about them.
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

Everything lives in `./data`: `portal.db` (accounts and wards), `wards/` (one database per ward)
and the session key. Back up the whole folder.

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
3. **New layout → Start from current assignments**, then drag names between groups. The tray at the
   top (Not assigned / Not ministering / Warnings) and the districts below it scroll separately,
   so you can scroll to any group and drag straight from the tray. Tap a name to
   move it with a menu, set tags, or see their history. Everything autosaves.
4. Watch the **Warnings** panel: groups with fewer than 2 or more than 3 ministers, more than 6
   assigned, brothers and sisters as companions, a sister in an Elders Quorum group (or a brother in
   Relief Society), someone in two groups, moved-out people,
   tagged people with no ministers, and uneven districts. Warnings never block a change.
5. Set the status to **Proposed** for the bishop (give him the Leader role) and **Approved** once
   it's final. Approved layouts are locked. Draft and Proposed work the same; Proposed only adds
   an "Approve" item to the dashboard's to-do list.
6. Open **Changes for LCR** and work through the checklist in LCR.
7. **Check it in LCR.** Approving a layout adds a to-do: once the changes are entered, upload a fresh
   Ministering Assignments report (there's an upload box on the Changes for LCR page). The import is
   compared with every approved layout still waiting on its check. If LCR matches, the layout is
   marked **Verified in LCR**. If not, Changes for LCR lists exactly what's still different. Use
   **Mark done anyway** when the differences are intended. Setting the status back to Draft (or
   approving again) restarts the check. Layouts approved before this feature count as verified.

**Who ministers to whom** (People → *Who ministers to whom*, or from any layout's Changes for LCR)
is an alphabetical list of everyone with their Elders Quorum and Relief Society ministers side by
side, made for ward council. Each column can show what's in LCR now or any layout. Pick a proposed
layout to send for bishopric approval: people whose ministers would change are marked **New**. It
prints cleanly (or *Save as PDF*), and *Copy link* shares the page with anyone who has a Leader account.

The side lists ("Not assigned to anyone", "Not ministering") can show brothers, sisters or
everyone. They default to what each organization does in your ward, and each leader's choice is
remembered in their browser.

Tags show as small flags on each name: **yellow** for people who need ministers (New move-in, New
member, Needs extra care, Returning, Less active; they're also in the warnings until someone is
assigned), **green** for people left out on purpose (Moving records and the Leader tags), and a plain
flag for anything else (Limited contact).

Companion history (“↺”) comes from past imports, so it appears once you've imported more than once.

## Lessons

Elders Quorum and Relief Society lessons are taught from the most recent general conference. As with
ministering, any Leader can see and edit both organizations’ plans. Every
member of the First Presidency and the Quorum of the Twelve who gave a talk needs a lesson before
the next conference.

**Conference talks** (Lessons → *Conference talks*): choose **Get talks from the Church website**.
The portal reads the conference's table of contents and each talk's page, the same public pages the
Gospel Library site uses, and ticks every talk whose speaker's calling is in the First Presidency or
the Quorum of the Twelve. Sustainings and reports are listed as *Not a lesson*. Change any tick by
hand and save.

If the website can't be reached (or changes how its pages work), upload the conference page as a
PDF instead: on churchofjesuschrist.org open *General Conference → October 2026*, choose **Print**
and save as PDF, then upload it here or on the regular **Upload report** page. A PDF has no
callings, so the ticks are filled in from names the portal already knows; check them before
planning. Loading a conference again (either way) keeps the talks already planned.

**Planning** (the **Lessons** tab): one card per Sunday from the Sunday after conference to the
end of the next conference's month. Lessons are every Sunday except:

- **Fifth Sundays:** marked automatically. Pick *Lesson* on one to teach that Sunday anyway.
- **Stake conference** and **No second hour:** pick them on the Sunday's card, with an optional
  note such as "Ward conference". These marks are **ward-wide**: they apply to Elders Quorum and
  Relief Society at once.

Drag talks from the tray onto Sundays, or tap an empty *Drop a talk here* (or *Drop a teacher
here*) to pick from a list of talks not yet planned (or that organization’s teachers, with an
option to add a new one). Drop more than one talk on the same Sunday to study them together, or
drag one back to the tray to unplan it. Tap a talk to read its summary, open it on the
Church website, or move it with a menu. ★ marks First Presidency and Twelve talks, and the tray
shows how many of those speakers are planned. **Fill in First Presidency & Twelve** puts each
missing speaker's talk on the next open Sundays in conference order, doubling up only if there
aren't enough Sundays. Everything autosaves.

**After the next conference** the plan keeps going to the end of April (or October), since the new
talks can take a little while to go up. Those Sundays are **shared** with the next conference's
plan: both plans show the same lesson on them, so nothing can be planned on top of something else.
On those Sundays you can teach talks from either conference (a talk from the other conference shows
its name, like *April 2027*), but First Presidency and Twelve talks still belong before conference:
one planned after it is flagged and doesn't count toward the total. Right after conference, until
the new talks are loaded, the Lessons tab and dashboard keep showing the previous plan.

The **Warnings** tab lists First Presidency and Twelve speakers who aren't planned, when there
are fewer open Sundays left than speakers (so lessons need combining), talks on a Sunday without
lessons, a talk planned twice, and Sundays in the next three weeks with nothing planned.

**Teachers:** each Sunday has a teacher lane. Open the tray's **Teachers** tab, choose **+ Add
teacher** (pick from the roster, brothers for Elders Quorum and sisters for Relief Society, or
type any name), then drag teachers onto Sundays. The number on each name is how many lessons
they have this conference. The **Teachers** page lists everyone with their next lesson; *Take off
the list* hides someone for a while, and removing someone who already taught keeps their name on
past lessons. Warnings add Sundays in the next three weeks without a teacher, the same teacher on
two lesson Sundays in a row, teachers who moved out or are off the list, and uneven turns.

The dashboard's **Lessons** tile shows the next lesson (talk and teacher) and how many First
Presidency and Twelve speakers are planned. Its to-do list asks for missing talks and teachers in
the next two weeks, for the new conference's talks once it's over, and for combined lessons when
there aren't enough Sundays left.

**Calendars on your phone** work like the birthday calendar: subscribe once (iPhone button, or the
Google Calendar steps for Android) and changes show up by themselves. Lessons are all-day events
on Sunday with the talks, links to read them, the teacher and any note.

- **Whole schedule** (lesson plan **⋯ → Put lessons on your phone**): one calendar per
  organization, including Sundays without a lesson. Handy for presidencies, or for trying the
  portal next to an existing spreadsheet calendar.
- **Each teacher** (Teachers → *Calendar link*): only the Sundays that teacher teaches. Copy the
  link and send it to them privately; they don't need an account.

The links are private and unguessable, like the birthday feed. *Make new links* stops an old one.

Elders Quorum and Relief Society each have their own plan. If your ward teaches the same talks in
both, choose **⋯ → EQ and RS teach the same lessons**; the shared plan starts as a copy of the
Elders Quorum plan, and switching back shows each organization's own plan again. Teachers stay
separate either way: a shared plan shows an EQ teacher and an RS teacher lane on every Sunday.

## GroupMe bots

Make a bot at [dev.groupme.com/bots](https://dev.groupme.com/bots) (pick the group it posts in, leave
the callback URL empty), then paste its **Bot ID** into the portal. *Send a test* posts a sample right
away; *Turn off* stops it (the chosen wording is kept).

- **Birthday shout-outs** (Birthdays page, ward admins): at 8 am on someone's birthday it posts a
  happy birthday message. Pick a style: *Movie trailer*, *Epic saga*, *Stadium announcer*, *Battle
  droid*, *Mix it up*, or *Write your own* (messages separated by a blank line, with `{names}` for
  the birthday person; one is picked at random). The page shows a sample of each. People flagged as
  possibly moved out are skipped, and only first and last names are used (never ages).
- **Lesson reminders** (Lessons → ⋯ → *GroupMe reminders*, its own page for leaders): every Monday at 9 am it posts the
  coming Sunday's talk, a link to read it and the teacher, or why there's no lesson (fifth Sunday,
  stake conference, no second hour, general conference, or nothing planned yet). Pick a voice:
  *Friendly* (the default), *Battle droid* (every message says "roger roger"), or *Write your own*
  opening and closing lines around the lesson details (`{org}` and `{when}` fill in). The wording is
  shuffled each week. Elders Quorum and Relief Society can each have a bot.

Times use `TZ` (default `America/Denver`). The app checks every few minutes, so a message still goes
out if the server was down at the usual time, and posts at most once a day per bot. A failed post is
retried up to three times that day; the last result shows on the settings card. Set
`GROUPME_ENABLED=false` to stop all posting (e.g. on a copy of the data).

## Wards

On an existing install, the first start after upgrading moves the existing data into the first ward
(named from `WARD_NAME`) and keeps a backup as `data/portal.pre-wards-<time>.db.bak`. The calendar
link keeps working.

To add a ward: **Wards → Add a ward**, then **Invite a ward admin** and send them the link. They sign
up, upload their own Member List and ministering reports, and invite their ward from **Users**.

```bash
docker compose exec portal python -m app.cli create-ward "Maple Creek 3rd Ward"
docker compose exec portal python -m app.cli list-wards
```

## Users

Admins manage people from the **Users** page:

- **Invite someone:** creates a single-use link (Member or Admin) that expires in 7 days. Send it
  privately. The recipient picks their own username and password. Pending invites can be revoked.
- **Reset password:** creates a single-use link for that person to set a new password. Their
  existing sessions are signed out. Only the newest reset link works.
- **Change role / Remove.** Pick Member, Leader or Admin from the Role menu. You can't change or remove your own account, so there's
  always at least one admin.

Everyone can change their own password on the **Account** page, which signs out their other devices.

Roles (all but Admin are limited to the user's own ward):

| Role | Can |
|---|---|
| Member | see birthdays, upload the Member List |
| Leader | also Elders Quorum and Relief Society ministering |
| Ward admin | also manage the ward's accounts and calendar link |
| Admin | everything, in every ward; add wards |

The first admin (or a lost-access recovery) is created from the server:

```bash
docker compose exec portal python -m app.cli create-user <name> [--admin | --ward-admin | --leader] [--ward ID]
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
