# Potential features

Ideas for later, from research into what leaders and clerks complain about in LCR (October 2026).
Most of the evidence came from the Church Tech Forum; links are at the bottom.

**Where this app fits:** LCR now has its own *Proposed Assignments* sandbox for ministering, so
editing assignments alone isn't the draw. The app's value is everything around it: warnings,
companion history, priority tags, the LCR check after approval, the to-do list, phone-friendly
pages, and not depending on LCR staying up on Sunday (it often fails between about 10am and 2:30pm Mountain).

Already built from this list: **Who ministers to whom** (printable, both organizations side by side).

## Ideas, best first

### 1. Age milestones on the to-do list
"Sam turns 8 next month" (baptism interview), turning 11/12 (youth classes, priesthood),
turning 18 (newly eligible to minister; they quietly join the *Not ministering* pool).

- **Needs:** nothing new; birth dates are already stored from the Member List.
- **Notes:** add to the dashboard to-do the same way today's birthdays are. Decide how far ahead
  to look (a month?) and which roles see it (bishopric-type milestones may be Leader-only).

### 2. Calling tracker
Track each calling through Called → Sustained → Set apart → Entered in LCR, plus releases.
Includes a printable sustain/release list for sacrament meeting and to-do items like "3 to set apart".

- **Why:** the most common spreadsheet workaround on the forum. One bishopric keeps six lists:
  callings to fill, people needing callings, extend/release, sustain/release, to set apart, enter in LCR.
- **Needs:** a sample *Members with Callings* PDF (made-up or redacted names) to write a parser.
  Store callings per person (low sensitivity).
- **Notes:** reuse the "re-import to check" pattern from ministering. Uploading Members with
  Callings confirms "Entered in LCR" and the set-apart flag.

### 3. Move-in welcome checklist
For each move-in: introduced in sacrament meeting, ministers assigned, calling considered.
To-do item: "2 move-ins have no ministers yet".

- **Needs:** nothing new; move-ins are already detected and tagged *New move-in*.
- **Notes:** the new Who ministers to whom data (`ministering.who_ministers`) already answers
  "do they have ministers?".

### 4. Ministering interview tracker
Which companionships have had their quarterly interview, by district, with a to-do item near the
end of each quarter and a printable interview list per district leader.

- **Why:** presidencies report interviews every quarter. LCR only records them after the fact.
- **Needs:** interview dates (entered here, or parsed from LCR's ministering interviews report;
  a sample would tell us which is practical).

### 5. Weekly email digest
The to-do list emailed to each leader, e.g. Saturday night.

- **Needs:** outgoing email (SMTP settings in `.env`), an email address per user, and a scheduler
  (a cron job running a CLI command fits the current Docker setup).

## Considered and skipped

- **Temple recommend expirations** and **mailing labels** would mean storing recommend status
  and addresses, which goes beyond the app's privacy rule (names, gender and birth dates only).
- **CSV export.** Clerks want it from LCR, but exporting member data from here works against
  the same privacy rule. Printing is the safer way to share.

## Sources

- [ministering assignments under LCR problems](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=38551)
- [Proposed Ministering Assignments "Show Changes"](https://tech6.churchofjesuschrist.org/forum/viewtopic.php?p=242769)
- [Export Custom Reports in LCR to comma delimited file](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=31261)
- [Ministering – Filter by Member option needed](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=40632)
- [How do EQ/RS presidencies enter ministering assignments?](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=31818&start=10)
- [Home Ministering Interviews](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=31279)
- [Ward Calling Tracker](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=29136)
- [record progress on Pending Setting Apart ordinances](https://tech.churchofjesuschrist.org/forum/viewtopic.php?p=171975)
- [Report for members sustained but not set apart](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=11627)
- [LCR Mailing Labels for Unassigned Ministering Assignments](https://tech.churchofjesuschrist.org/forum/viewtopic.php?t=38362)
