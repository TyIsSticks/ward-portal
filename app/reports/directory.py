"""LCR "Member List" (directory) PDF: name, gender, age and birth date for everyone in the ward.

Long names wrap onto extra lines above and below the row, so rows are rebuilt from word
positions: a row is anchored on its Gender cell, and name fragments join the nearest row.
Phone numbers and emails are ignored.
"""
import io
import re
from dataclasses import dataclass

import pdfplumber

from .birthdays import MONTHS, ReportError

_ROW_RE = re.compile(r"^(?P<gender>[MF]) (?P<age>\d{1,3})(?: (?P<day>\d{1,2}) (?P<mon>[A-Z][a-z]{2}) (?P<year>\d{4}))?")


@dataclass(frozen=True)
class Member:
    name: str              # "Last, First Middle"
    gender: str            # "M" / "F"
    age: int | None
    birth_year: int | None
    birth_month: int | None
    birth_day: int | None


def _lines(words: list[dict], tolerance: float = 3) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def parse_page(words: list[dict]) -> list[Member]:
    """Parse one page's words (pdfplumber dicts with text/x0/top)."""
    header = {w["text"]: w for w in words if w["text"] in ("Name", "Gender", "Age")}
    if "Name" not in header or "Gender" not in header:
        return []
    gender_x = header["Gender"]["x0"] - 2
    header_top = header["Name"]["top"]

    rows, name_lines = [], []
    for line in _lines([w for w in words if w["top"] > header_top + 2]):
        if "Church Use Only" in " ".join(w["text"] for w in line):
            continue  # page footer
        name_part = " ".join(w["text"] for w in line if w["x1"] <= gender_x)
        rest = " ".join(w["text"] for w in line if w["x1"] > gender_x)
        top = line[0]["top"]
        m = _ROW_RE.match(rest)
        if m:
            rows.append({"top": top, "m": m, "name": []})
        if name_part and not name_part.startswith("Count:"):
            name_lines.append((top, name_part))

    if not rows:
        return []
    for top, text in name_lines:
        nearest = min(rows, key=lambda r: abs(r["top"] - top))
        if abs(nearest["top"] - top) < 25:
            nearest["name"].append((top, text))

    members = []
    for r in rows:
        name = " ".join(t for _, t in sorted(r["name"]))
        if "," not in name:
            continue
        m = r["m"]
        month = MONTHS.get(m["mon"].lower()) if m["mon"] else None
        members.append(Member(
            name=" ".join(name.split()), gender=m["gender"],
            age=int(m["age"]),
            birth_year=int(m["year"]) if m["year"] else None,
            birth_month=month, birth_day=int(m["day"]) if m["day"] and month else None))
    return members


def parse_pdf(data: bytes) -> list[Member]:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise ReportError("That file couldn't be opened as a PDF.") from exc
    with pdf:
        first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if "member list" not in first.lower():
            raise ReportError("This doesn't look like the LCR Member List (directory) report.")
        members, expected = [], None
        for page in pdf.pages:
            members += parse_page(page.extract_words())
            if m := re.search(r"Count:\s*(\d+)", page.extract_text() or ""):
                expected = int(m.group(1))
    if not members:
        raise ReportError("Found the directory but couldn't read any members from it.")
    if expected is not None and expected != len(members):
        raise ReportError(f"The report lists {expected} members but only {len(members)} could be read. "
                          "The PDF layout may have changed.")
    return members
