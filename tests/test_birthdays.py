"""Tests use made-up names only. Never put real member data in this repo."""
import sqlite3

import pytest
from icalendar import Calendar

from app import db
from app.reports.birthdays import (Birthday, ReportError, build_ics, display_name,
                                   parse_rows, sync)

HEADER = ["Birthday", "Name", "Age", "Phone Number", "Street Address"]


def test_parse_rows_finds_columns_and_skips_junk():
    tables = [
        [HEADER,
         ["3 Jan", "Doe, Jane Q", "21", "(555) 555-0100", "1 Main St\nTown UT 00000"],
         ["29 Feb", "Leap, Larry", "24", "", ""],
         ["", "No Birthday, Person", "", "", ""],
         ["31 Feb", "Bad, Date", "20", "", ""]],
        # Next page repeats the header.
        [HEADER,
         ["12 Dec", "Smith,\nJohn", "30", "", ""]],
    ]
    assert parse_rows(tables) == [
        Birthday(1, 3, "Doe, Jane Q"),
        Birthday(2, 29, "Leap, Larry"),
        Birthday(12, 12, "Smith, John"),
    ]


def test_parse_rows_handles_page_without_repeated_header():
    tables = [[HEADER, ["5 Mar", "A, B", "", "", ""]], [["6 Mar", "C, D", "", "", ""]]]
    assert len(parse_rows(tables)) == 2


def test_parse_rows_rejects_wrong_report():
    with pytest.raises(ReportError):
        parse_rows([[["Foo", "Bar"], ["1", "2"]]])


def test_display_name():
    assert display_name("Doe, Jane Quinn") == "Jane Quinn Doe"
    assert display_name("Cher") == "Cher"


def test_sync_adds_and_removes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    a, b, c = Birthday(1, 1, "A, A"), Birthday(2, 2, "B, B"), Birthday(3, 3, "C, C")

    assert sync(conn, [a, b]) == ([a, b], [])
    assert sync(conn, [b, c]) == ([c], [a])
    assert sync(conn, [b, c]) == ([], [])


def test_ics_is_yearly_and_stable():
    people = [Birthday(1, 3, "Doe, Jane"), Birthday(2, 29, "Leap, Larry")]
    first = Calendar.from_ical(build_ics(people, "Test"))
    second = Calendar.from_ical(build_ics(people, "Test"))
    events = first.walk("VEVENT")

    assert [str(e["summary"]) for e in events] == ["🎂 Jane Doe", "🎂 Larry Leap"]
    assert events[0]["rrule"]["FREQ"] == ["YEARLY"]
    assert events[1]["rrule"]["BYMONTHDAY"] == [-1]  # Feb 29 -> last day of Feb
    assert [e["uid"] for e in events] == [e["uid"] for e in second.walk("VEVENT")]
