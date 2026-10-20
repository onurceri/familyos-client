"""Small pieces of the models."""

import datetime as dt

from familyos import Recurrence
from familyos.models import format_moment, format_time, parse_moment


def test_recurrence_to_api() -> None:
    assert Recurrence("daily").to_api() == {"freq": "daily", "interval": 1}
    weekly = Recurrence("weekly", interval=2, by_day=["MO", "FR"], until=dt.date(2026, 12, 31))
    assert weekly.to_api() == {"freq": "weekly", "interval": 2, "byDay": ["MO", "FR"], "until": "2026-12-31"}


def test_moments() -> None:
    assert parse_moment(None) is None
    assert parse_moment("2026-10-21") == dt.date(2026, 10, 21)
    instant = parse_moment("2026-10-21T09:00:00+03:00")
    assert isinstance(instant, dt.datetime) and instant.utcoffset() == dt.timedelta(hours=3)
    utc = parse_moment("2026-10-21T06:00:00Z")
    assert isinstance(utc, dt.datetime) and utc.utcoffset() == dt.timedelta(0)
    assert format_moment(dt.date(2026, 10, 21)) == "2026-10-21"
    assert format_time(dt.time(9, 5)) == "09:05"


def test_fields_added_in_0_4_parse_and_default() -> None:
    from familyos import BoardNote, NoteApp, Occurrence

    from .conftest import note, occurrence

    plain = BoardNote.from_dict(note())
    assert plain.created_by_app is None
    by_app = BoardNote.from_dict(note(createdByApp={"id": "app_1", "name": "Home Assistant"}))
    assert by_app.created_by_app == NoteApp(id="app_1", name="Home Assistant")

    assert Occurrence.from_dict(occurrence()).series_recurrence_id is None
    moved = Occurrence.from_dict(occurrence(seriesRecurrenceId="2026-10-22T17:00:00+02:00"))
    assert moved.series_recurrence_id is not None
    assert moved.series_recurrence_id.isoformat() == "2026-10-22T17:00:00+02:00"
