"""General conference talks, for planning Elders Quorum and Relief Society lessons.

Two ways in, both giving the same list of `Talk`s:

- `fetch`: the Church website's content API (what the Gospel Library site itself uses). It's
  public but undocumented, so it may change; every failure becomes a `ReportError` that tells
  the leader to upload the PDF instead. Talk pages also give each speaker's calling, which is how
  First Presidency and Quorum of the Twelve talks are recognized.
- `parse_pdf`: the conference page printed to PDF from a browser. Each talk is a link box holding
  the speaker, the title (largest text) and a short summary. There are no callings, so
  `guess_required` falls back to names the ward has seen before.
"""
import html
import io
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser

import pdfplumber

from .birthdays import ReportError

SITE = "https://www.churchofjesuschrist.org"
API = SITE + "/study/api/v3/language-pages/type/content?lang=eng&uri="
MONTH_NAMES = {4: "April", 10: "October"}

# Sustainings and reports are read over the pulpit; they aren't lessons.
_BUSINESS = re.compile(r"^(sustaining of|church auditing department report|statistical report)", re.I)
_REQUIRED_CALLING = re.compile(r"first presidency|quorum of the twelve|president of the church", re.I)

# The First Presidency and Quorum of the Twelve as of October 2026. Only used to pre-tick
# talks from an uploaded PDF (which has no callings) before the ward has fetched any conference.
KNOWN_REQUIRED = {
    "Dallin H. Oaks", "Henry B. Eyring", "D. Todd Christofferson", "Dieter F. Uchtdorf", "David A. Bednar",
    "Quentin L. Cook", "Neil L. Andersen", "Ronald A. Rasband", "Gary E. Stevenson", "Dale G. Renlund",
    "Gerrit W. Gong", "Ulisses Soares", "Patrick Kearon", "Gérald Caussé", "Clark G. Gilbert",
}


@dataclass
class Talk:
    speaker: str
    title: str
    uri: str                  # site path, e.g. /study/general-conference/2026/10/12gong
    session: str = ""
    summary: str = ""
    calling: str = ""         # e.g. "Of the Quorum of the Twelve Apostles"; empty when unknown
    kind: str = "talk"        # "talk" or "business" (sustainings and reports)
    required: bool = False    # by a member of the First Presidency or Quorum of the Twelve

    @property
    def url(self) -> str:
        return f"{SITE}{self.uri}?lang=eng"


@dataclass
class Conference:
    year: int
    month: int
    talks: list[Talk] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{MONTH_NAMES[self.month]} {self.year}"


def _clean(text: str) -> str:
    return " ".join(html.unescape(text or "").split())  # also turns no-break spaces into spaces


def _kind(title: str) -> str:
    return "business" if _BUSINESS.match(title) else "talk"


def is_required_calling(calling: str) -> bool:
    return bool(_REQUIRED_CALLING.search(calling or ""))


def guess_required(talks: list[Talk], known: set[str]) -> None:
    """Tick talks by speakers known to be in the First Presidency or the Twelve (PDF uploads)."""
    for t in talks:
        t.required = t.speaker in known


# --- Website ----------------------------------------------------------------

def _get(uri: str, timeout: float = 15) -> dict:
    req = urllib.request.Request(API + urllib.request.quote(uri, safe="/"),
                                 headers={"User-Agent": "ward-portal (lesson planner)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


class _ListingParser(HTMLParser):
    """Talk tiles from a conference's table of contents.

    Each tile is `<li data-content-type="general-conference-talk"><a href=...>` holding
    `p.primaryMeta` (speaker), `p.title` and `p.description`. Session headings are
    `<li data-content-type="general-conference-session"><h2>…<p class="title">`.
    """

    def __init__(self):
        super().__init__()
        self.talks: list[Talk] = []
        self.session = ""
        self._li: list[str | None] = []   # content type of each open <li>
        self._item: dict | None = None
        self._field: str | None = None
        self._in_h2 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            self._li.append(a.get("data-content-type"))
        elif tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._li and self._li[-1] in ("general-conference-talk", "general-conference-business"):
            self._item = {"href": a.get("href", ""), "type": self._li[-1], "primaryMeta": "", "title": "",
                          "description": ""}
        elif tag == "p":
            cls = a.get("class", "")
            if self._item is not None and cls in ("primaryMeta", "title", "description"):
                self._field = cls
            elif self._in_h2 and cls == "title":
                self._field = "session"
                self.session = ""

    def handle_endtag(self, tag):
        if tag == "li" and self._li:
            self._li.pop()
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p":
            self._field = None
        elif tag == "a" and self._item is not None:
            it = self._item
            self._item = None
            title = _clean(it["title"])
            self.talks.append(Talk(
                speaker=_clean(it["primaryMeta"]), title=title, uri=it["href"].split("?")[0],
                session=self.session, summary=_clean(it["description"]),
                kind="business" if it["type"] == "general-conference-business" else _kind(title)))

    def handle_data(self, data):
        if self._field == "session":
            self.session = _clean(self.session + data)
        elif self._field and self._item is not None:
            self._item[self._field] += data


_ROLE = re.compile(r'<p[^>]*class="author-role"[^>]*>(.*?)</p>', re.S)


def parse_listing(body: str) -> list[Talk]:
    p = _ListingParser()
    p.feed(body)
    return [t for t in p.talks if t.speaker and t.title]


def parse_calling(body: str) -> str:
    m = _ROLE.search(body)
    return _clean(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""


def fetch(year: int, month: int) -> Conference:
    """Download a conference's talks and their speakers' callings from the Church website."""
    if month not in MONTH_NAMES:
        raise ReportError("General conference is in April and October.")
    name = f"{MONTH_NAMES[month]} {year}"
    try:
        listing = _get(f"/general-conference/{year}/{month:02d}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ReportError(f"The {name} conference isn't on the Church website yet.") from exc
        raise ReportError(f"The Church website didn't answer properly ({exc.code}). "
                          "Upload the conference PDF instead.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise ReportError("Couldn't reach the Church website. Upload the conference PDF instead.") from exc
    talks = parse_listing((listing.get("content") or {}).get("body", ""))
    if not talks:
        raise ReportError(f"Couldn't find any talks for {name} on the Church website. "
                          "Upload the conference PDF instead.")

    def calling(t: Talk) -> str:
        try:
            return parse_calling(_get(t.uri.removeprefix("/study"))["content"]["body"])
        except Exception:
            return ""  # one missing calling isn't worth failing over; the leader can tick it

    with ThreadPoolExecutor(max_workers=8) as pool:
        for t, c in zip(talks, pool.map(calling, talks)):
            t.calling = c
            t.required = is_required_calling(c)
    missing = [t for t in talks if not t.calling]
    if missing:  # fill the gaps from known names rather than leave them unticked
        guess_required(missing, KNOWN_REQUIRED | {t.speaker for t in talks if t.required})
    return Conference(year, month, talks)


# --- Printed PDF ------------------------------------------------------------

_HEADING = re.compile(r"\b(April|October)\s+(\d{4})\s+general conference", re.I)
_TALK_LINK = re.compile(r"(/study/general-conference/\d{4}/\d{2}/[^?#]+)")


def group_tiles(lines: list[dict], links: list[dict]) -> tuple[str, list[Talk]]:
    """Turn a printed conference page into talks.

    `lines`: {"page", "top", "text", "size"} in reading order. `links`: {"page", "top", "bottom", "uri"}.
    Returns the heading text and the talks. Kept apart from pdfplumber so it can be tested.
    """
    heading, session = "", ""
    tiles: dict[tuple, dict] = {}
    for ln in lines:
        link = next((l for l in links if l["page"] == ln["page"] and l["top"] - 2 <= ln["top"] <= l["bottom"]), None)
        m = _TALK_LINK.search(link["uri"]) if link else None
        if m and m.group(1).endswith("-session"):
            continue  # the session's own tile
        if not m:
            if not heading and _HEADING.search(ln["text"]):
                heading = ln["text"]
            elif ln["text"].strip().lower().endswith("session"):
                session = _clean(ln["text"])
            continue
        tile = tiles.setdefault((link["page"], link["top"]), {"uri": m.group(1), "session": session, "lines": []})
        tile["lines"].append(ln)

    talks = []
    for tile in tiles.values():
        first, rest = tile["lines"][0], tile["lines"][1:]
        if not rest:
            continue
        big = max(l["size"] for l in rest)
        title = _clean(" ".join(l["text"] for l in rest if abs(l["size"] - big) < 0.3))
        summary = _clean(" ".join(l["text"] for l in rest if abs(l["size"] - big) >= 0.3))
        talks.append(Talk(speaker=_clean(first["text"]), title=title, uri=tile["uri"], session=tile["session"],
                          summary=summary, kind=_kind(title)))
    return heading, talks


def parse_pdf(data: bytes) -> Conference:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            lines, links = [], []
            for i, page in enumerate(pdf.pages):
                for l in page.hyperlinks:
                    links.append({"page": i, "top": l["top"], "bottom": l["bottom"], "uri": l.get("uri") or ""})
                for ln in page.extract_text_lines():
                    lines.append({"page": i, "top": ln["top"], "text": ln["text"],
                                  "size": round(ln["chars"][0]["size"], 1) if ln["chars"] else 0})
    except Exception as exc:
        raise ReportError("That file couldn't be opened as a PDF.") from exc
    heading, talks = group_tiles(lines, links)
    m = _HEADING.search(heading)
    if not m:
        raise ReportError("This doesn't look like a general conference page. On the Church website, open the "
                          "conference (for example October 2026) and print it to PDF.")
    if not talks:
        raise ReportError("Couldn't find the talks in this PDF. Print the conference page again with "
                          "“Background graphics” or links kept, or fetch the talks from the website.")
    return Conference(int(m.group(2)), 4 if m.group(1).lower() == "april" else 10, talks)
