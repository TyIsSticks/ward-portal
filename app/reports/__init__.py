"""One module per LCR report type. Each knows how to parse its export."""
import io

import pdfplumber

from .birthdays import ReportError

TITLES = {
    "member list": "directory",
    "birthday list": "birthdays",
    "ministering assignments": "ministering",
    "general conference": "conference",  # not from LCR: the conference page printed from the Church website
}


def detect(data: bytes) -> str:
    """Which report a PDF is: 'directory', 'birthdays', 'ministering' or 'conference'."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            first = (pdf.pages[0].extract_text() or "").lower() if pdf.pages else ""
    except Exception as exc:
        raise ReportError("That file couldn't be opened as a PDF.") from exc
    for title, kind in TITLES.items():
        if title in first[:400]:
            return kind
    raise ReportError("This doesn't look like a supported LCR report "
                      "(Member List, Birthday List or Ministering Assignments) or a general conference page.")
