"""Parser tests built from synthetic word positions (made-up names only)."""
import pytest

from app.reports import directory, ministering
from app.reports.birthdays import ReportError


def w(text, x0, top):
    return {"text": text, "x0": x0, "x1": x0 + 5 * len(text), "top": top}


def words(*runs):
    """runs: (text, x0, top) where text may contain spaces; splits into words like pdfplumber."""
    out = []
    for text, x0, top in runs:
        x = x0
        for part in text.split():
            out.append(w(part, x, top))
            x += 5 * len(part) + 4
    return out


DIR_HEADER = [("Name", 22, 50), ("Gender", 200, 50), ("Age", 240, 50), ("Birth Date", 270, 50),
              ("Phone Number", 350, 50), ("Email", 450, 50)]


def test_directory_rows_and_wrapped_names():
    page = words(*DIR_HEADER,
                 ("Doe, Jane", 22, 70), ("F", 200, 70), ("21", 240, 70), ("3 Jan 2005", 270, 70),
                 ("(555) 555-0100", 350, 70), ("jane@example.com", 450, 70),
                 # Long name wraps above and below its data line.
                 ("Smith, Johnathan", 22, 88), ("M", 200, 95), ("30", 240, 95), ("29 Feb 1996", 270, 95),
                 ("Michael", 22, 102),
                 ("Nobirth, Pat", 22, 120), ("M", 200, 120), ("40", 240, 120),
                 ("6 Oct 2026 For Church Use Only", 22, 700))
    members = directory.parse_page(page)
    assert [(m.name, m.gender, m.birth_month, m.birth_day, m.birth_year) for m in members] == [
        ("Doe, Jane", "F", 1, 3, 2005),
        ("Smith, Johnathan Michael", "M", 2, 29, 1996),
        ("Nobirth, Pat", "M", None, None, None),
    ]


def test_directory_page_without_header_is_ignored():
    assert directory.parse_page(words(("Something else", 22, 50))) == []


def _comp(top, ministers, assigned):
    """Words for one companionship block starting at `top`."""
    runs = [(". . . . . . . .", 22, top), ("Presidency Member: Okey, Austin 555 | a@example.com", 22, top + 10)]
    y = top + 30
    for i in range(max(len(ministers), len(assigned))):
        if i < len(ministers):
            runs.append((ministers[i], 22, y))
            runs.append(("555-555-0100", 22, y + 12))
        if i < len(assigned):
            name, gender, bday = assigned[i]
            runs += [(name.split(",")[0], 167, y), (name, 296, y), (gender, 458, y), (bday, 517, y),
                     ("555-555-0199", 296, y + 12), ("1 Main St", 296, y + 24)]
        y += 45
    return runs


def test_ministering_districts_companionships_and_people():
    page1 = words(("North District", 22, 30), ("Presidency Member: Okey, Austin", 22, 42),
                  *_comp(60, ["Doe, John", "Roe, Rick"], [("Adams, Amy", "Female", "1 Jul"),
                                                         ("Brown, Bob", "Male", "12 Dec")]),
                  ("15 For Church Use Only", 22, 760))
    # A wrapped assigned name: gender/birthday land on the next line.
    page2 = words(*_comp(30, ["Poe, Ed"], []),
                  ("Longname", 167, 75), ("Longname, Mary Anne", 296, 75),
                  ("Elizabeth", 296, 87), ("Female", 458, 87), ("2 Feb", 517, 87),
                  ("South District", 22, 300), ("Presidency Member: Fullmer, Enoch", 22, 312),
                  *_comp(330, ["Gee, Sam", "Hay, Tom", "Ivy, Lou"], [("Cole, Cy", "Male", "5 May")]))

    districts = ministering.parse_words([page1, page2])
    assert [(d.name, d.supervisor, len(d.companionships)) for d in districts] == [
        ("North District", "Okey, Austin", 2), ("South District", "Fullmer, Enoch", 1)]
    first, second = districts[0].companionships
    assert first.ministers == ["Doe, John", "Roe, Rick"]
    assert [(a.name, a.gender, a.month, a.day) for a in first.assigned] == [
        ("Adams, Amy", "F", 7, 1), ("Brown, Bob", "M", 12, 12)]
    assert second.ministers == ["Poe, Ed"]
    assert [(a.name, a.gender, a.month, a.day) for a in second.assigned] == [
        ("Longname, Mary Anne Elizabeth", "F", 2, 2)]
    assert districts[1].companionships[0].ministers == ["Gee, Sam", "Hay, Tom", "Ivy, Lou"]


def test_detect_rejects_non_pdf():
    from app.reports import detect
    with pytest.raises(ReportError):
        detect(b"not a pdf")
