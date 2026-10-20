"""Shared fixtures: a client on a mocked server, and sample answers in the shape the API sends."""

import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from familyos import FamilyOS

BASE = "http://famos.test"
API = BASE + "/api/public/v1"
TOKEN = "famos_secret_test_key"
TZ = "Europe/Istanbul"


@pytest.fixture
def mocked() -> Iterator[aioresponses]:
    with aioresponses() as m:
        yield m


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
async def client(session: aiohttp.ClientSession) -> FamilyOS:
    return FamilyOS(BASE, TOKEN, session=session)


def route(mocked: aioresponses, method: str, path: str, **kwargs: Any) -> None:
    """Answer ``method path`` (with any query) once."""
    mocked.add(re.compile(rf"^{re.escape(API + path)}(\?.*)?$"), method=method, **kwargs)


def sent(mocked: aioresponses) -> list[tuple[str, str, dict[str, Any]]]:
    """Every request made so far: method, url, and the kwargs it was made with."""
    return [(key[0], str(key[1]), call.kwargs) for key, calls in mocked.requests.items() for call in calls]


def last(mocked: aioresponses) -> tuple[str, str, dict[str, Any]]:
    return sent(mocked)[-1]


def page(items: list[dict[str, Any]], next: str | None = None) -> dict[str, Any]:
    return {"items": items, "next": next, "timeZone": TZ}


def applied(result: dict[str, Any]) -> dict[str, Any]:
    return {"status": "applied", "result": result, "timeZone": TZ}


def event(**over: Any) -> dict[str, Any]:
    return {
        "id": "cal_event_1",
        "version": 3,
        "title": "Dentist",
        "allDay": False,
        "start": "2026-10-21T09:00:00+03:00",
        "end": "2026-10-21T10:00:00+03:00",
        "date": "2026-10-21",
        "endDate": None,
        "startTime": "09:00",
        "endTime": "10:00",
        "memberIds": ["member_1"],
        "location": None,
        "notes": None,
        "recurrence": None,
        "rrule": None,
        "reminder": None,
        "tags": [],
        **over,
    }


def occurrence(**over: Any) -> dict[str, Any]:
    return {
        "eventId": "cal_event_1",
        "recurrenceId": "2026-10-28T16:00:00+03:00",
        "title": "Football",
        "allDay": False,
        "start": "2026-10-28T16:00:00+03:00",
        "end": "2026-10-28T17:00:00+03:00",
        "memberIds": [],
        "location": "Park",
        "recurring": True,
        "notes": None,
        "source": None,
        "editable": True,
        "tags": [],
        **over,
    }


def task(**over: Any) -> dict[str, Any]:
    return {
        "id": "task_1",
        "version": 1,
        "title": "Pay the school fee",
        "notes": None,
        "assigneeId": None,
        "dueDate": "2026-10-25",
        "dueTime": None,
        "due": "2026-10-25",
        "recurrence": None,
        "rrule": None,
        "done": False,
        "doneAt": None,
        "doneBy": None,
        **over,
    }


def shopping_list(**over: Any) -> dict[str, Any]:
    return {"id": "shop_list_1", "version": 1, "name": "Groceries", "isDefault": True, "openCount": 2, **over}


def shopping_item(**over: Any) -> dict[str, Any]:
    return {
        "id": "shop_item_1",
        "version": 1,
        "listId": "shop_list_1",
        "title": "Milk",
        "quantity": "2 l",
        "category": "dairy",
        "done": False,
        "doneAt": None,
        "addedBy": None,
        **over,
    }


def chore(**over: Any) -> dict[str, Any]:
    return {
        "id": "chore_1",
        "version": 2,
        "title": "Bins",
        "notes": None,
        "recurrence": {"freq": "weekly", "interval": 1, "byDay": ["TU"]},
        "startDate": "2026-10-01",
        "nextDue": "2026-10-27",
        "overdue": False,
        "rotation": "round_robin",
        "assigneeId": None,
        "assigneeIds": ["member_1", "member_2"],
        "lastDone": {"id": "chore_done_1", "memberId": "member_1", "doneAt": "2026-10-20T08:00:00+03:00"},
        "createdAt": "2026-10-01T08:00:00+03:00",
        "updatedAt": "2026-10-20T08:00:00+03:00",
        **over,
    }


def completion(**over: Any) -> dict[str, Any]:
    return {
        "id": "chore_done_1",
        "choreId": "chore_1",
        "memberId": "member_1",
        "dueDate": "2026-10-20",
        "doneAt": "2026-10-20T08:00:00+03:00",
        **over,
    }


def note(**over: Any) -> dict[str, Any]:
    return {
        "id": "board_note_1",
        "version": 1,
        "text": "Back at six",
        "paper": "note-1",
        "pinned": False,
        "endsAt": None,
        "endsOn": "2026-10-22",
        "hasPhoto": False,
        "createdBy": None,
        "createdAt": "2026-10-20T08:00:00+03:00",
        "updatedAt": "2026-10-20T08:00:00+03:00",
        "seenBy": [],
        **over,
    }


def discovery() -> dict[str, Any]:
    return {
        "api": "v1",
        "familyOS": "0.3.0",
        "timeZone": TZ,
        "language": "en",
        "caller": {"app": {"id": "app_1", "name": "Hall", "mode": "apply"}, "memberId": None},
        "extensions": [
            {"id": "calendar", "version": "0.3.0", "enabled": True},
            {"id": "board", "version": "0.3.0", "enabled": False},
        ],
        "operations": [
            {
                "name": "calendar.createEvent",
                "method": "post",
                "path": "/api/public/v1/calendar/events",
                "summary": "Add an event",
                "outcome": "apply",
            }
        ],
    }


def error(code: str, message: str = "No", **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}
