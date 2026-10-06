"""LCR "Ministering Assignments" PDF: districts -> companionships -> ministers and assigned people.

Layout per page: a district heading ("... District") with "Presidency Member: Last, First", then
companionships separated by dotted rules. Each companionship lists ministers in the left column
(with contact lines under each), a household label in the middle column, and assigned people in
the right column as "Last, First Gender DD Mon" (also followed by contact lines).
Contact details are discarded.
"""
import io
import re
from dataclasses import dataclass, field

import pdfplumber

from .birthdays import MONTHS, ReportError

LEFT_MAX_X = 150      # ministers start near x=22
RIGHT_MIN_X = 285     # assigned people start near x=296; household labels sit in between
_GAP = 12             # horizontal gap (pt) that separates columns within a line

_NAME_RE = re.compile(r"^[^\W\d][^,@\d]*, [^,@\d]+$")
_ASSIGNED_RE = re.compile(r"^(?P<name>[^\W\d][^,@\d]*, [^@\d]+?) (?P<gender>Male|Female) (?P<day>\d{1,2}) (?P<mon>[A-Z][a-z]{2})$")


@dataclass
class Assigned:
    name: str
    gender: str | None = None
    month: int | None = None
    day: int | None = None


@dataclass
class Companionship:
    ministers: list[str] = field(default_factory=list)
    assigned: list[Assigned] = field(default_factory=list)


@dataclass
class District:
    name: str
    supervisor: str = ""
    companionships: list[Companionship] = field(default_factory=list)


def _lines(words: list[dict], tolerance: float = 3) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _segments(line: list[dict]) -> list[tuple[float, str]]:
    """Split a line into (start_x, text) runs wherever there's a column-sized gap."""
    segs: list[list[dict]] = []
    for w in line:
        if segs and w["x0"] - segs[-1][-1]["x1"] < _GAP:
            segs[-1].append(w)
        else:
            segs.append([w])
    return [(s[0]["x0"], " ".join(w["text"] for w in s)) for s in segs]


def _is_district_heading(line: list[dict], next_text: str) -> bool:
    """A district name sits in the left column right above "Presidency Member: Last, First".

    That supervisor line has no phone or email; the copies that head each companionship do,
    so this doesn't depend on what the district is called.
    """
    return (line[0]["x0"] < LEFT_MAX_X and next_text.startswith("Presidency Member:")
            and not re.search(r"[\d@|]", next_text))


def parse_words(pages: list[list[dict]]) -> list[District]:
    districts: list[District] = []
    comp: Companionship | None = None
    pending: Assigned | None = None  # assigned name that wrapped before its gender/birthday

    lines = [line for words in pages for line in _lines(words)]
    texts = [" ".join(w["text"] for w in line) for line in lines]

    for i, (line, text) in enumerate(zip(lines, texts)):
            if text.startswith(". . ."):
                comp = Companionship()
                if districts:
                    districts[-1].companionships.append(comp)
                pending = None
                continue
            if "Church Use Only" in text or text.startswith("Presidency Member:") and comp is not None:
                continue
            if text.startswith("Presidency Member:"):
                if districts and not districts[-1].supervisor:
                    districts[-1].supervisor = text.split(":", 1)[1].strip()
                continue
            if _is_district_heading(line, texts[i + 1] if i + 1 < len(texts) else ""):
                districts.append(District(name=text))
                comp = None
                continue
            if comp is None:
                continue

            segs = _segments(line)
            for x, seg in segs:
                if x < LEFT_MAX_X and _NAME_RE.match(seg):
                    comp.ministers.append(seg)

            # Name, gender and birthday are separate columns on the right; read them as one.
            right = " ".join(seg for x, seg in segs if x >= RIGHT_MIN_X)
            if not right:
                continue
            candidate = f"{pending.name} {right}" if pending else right
            if m := _ASSIGNED_RE.match(candidate):
                if pending:
                    comp.assigned.remove(pending)
                comp.assigned.append(Assigned(
                    name=m["name"], gender=m["gender"][0],
                    month=MONTHS.get(m["mon"].lower()), day=int(m["day"])))
                pending = None
            elif _NAME_RE.match(right) and not pending:
                pending = Assigned(name=right)  # wrapped; gender/birthday on the next line
                comp.assigned.append(pending)
            else:
                pending = None

    for d in districts:
        d.companionships = [c for c in d.companionships if c.ministers or c.assigned]
    return [d for d in districts if d.companionships]


def parse_pdf(data: bytes) -> list[District]:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise ReportError("That file couldn't be opened as a PDF.") from exc
    with pdf:
        first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if "ministering assignments" not in first.lower():
            raise ReportError("This doesn't look like the LCR Ministering Assignments report.")
        districts = parse_words([p.extract_words() for p in pdf.pages])
    if not districts:
        raise ReportError("Found the report but couldn't read any companionships from it.")
    return districts
