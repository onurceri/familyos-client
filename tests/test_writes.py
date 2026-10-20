"""Writing: every write method's request, the headers, drafts, replays."""

import datetime as dt
import re
from typing import Any

import pytest
from aioresponses import aioresponses

from familyos import (
    UNSET,
    Applied,
    Drafted,
    EventInput,
    FamilyOS,
    Recurrence,
    ShoppingItemInput,
    TaskInput,
)
from tests.conftest import (
    TZ,
    applied,
    chore,
    completion,
    event,
    last,
    note,
    route,
    shopping_item,
    task,
)

pytestmark = pytest.mark.asyncio


def body_of(mocked: aioresponses) -> Any:
    return last(mocked)[2]["json"]


def headers_of(mocked: aioresponses) -> dict[str, str]:
    return dict(last(mocked)[2]["headers"])


async def test_create_event(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "POST", "/calendar/events", status=201, payload=applied(event()), headers={"ETag": '"v1"'})
    result = await client.create_event(
        "Dentist",
        dt.date(2026, 10, 21),
        start_time=dt.time(9, 0),
        end_time=dt.time(9, 45),
        member_ids=["member_1"],
        location="Clinic",
        recurrence=Recurrence("weekly", by_day=["MO"], until=dt.date(2026, 12, 1)),
        reminder=15,
        tags=["health"],
    )
    assert isinstance(result, Applied)
    assert result.result.title == "Dentist" and result.etag == '"v1"' and result.time_zone == TZ
    assert not result.replayed
    assert body_of(mocked) == {
        "title": "Dentist",
        "date": "2026-10-21",
        "startTime": "09:00",
        "endTime": "09:45",
        "memberIds": ["member_1"],
        "location": "Clinic",
        "recurrence": {"freq": "weekly", "interval": 1, "byDay": ["MO"], "until": "2026-12-01"},
        "reminder": 15,
        "tags": ["health"],
    }
    assert re.fullmatch(r"[0-9a-f]{32}", headers_of(mocked)["Idempotency-Key"])


async def test_create_event_all_day_with_end_date(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "POST", "/calendar/events", status=201, payload=applied(event()))
    await client.create_event(
        "Trip",
        dt.date(2026, 10, 21),
        all_day=True,
        end_date=dt.date(2026, 10, 23),
        notes="Pack",
        idempotency_key="my-key",
    )
    assert body_of(mocked) == {
        "title": "Trip",
        "allDay": True,
        "date": "2026-10-21",
        "endDate": "2026-10-23",
        "notes": "Pack",
    }
    assert headers_of(mocked)["Idempotency-Key"] == "my-key"


async def test_each_call_gets_its_own_idempotency_key(client: FamilyOS, mocked: aioresponses) -> None:
    keys = []
    for _ in range(2):
        route(mocked, "POST", "/board/notes", status=201, payload=applied(note()))
        await client.create_note("Hi")
        keys.append(headers_of(mocked)["Idempotency-Key"])
    assert keys[0] != keys[1]


async def test_patch_event_sends_only_what_was_given(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PATCH", "/calendar/events/cal_event_1", payload=applied(event(title="Moved")))
    await client.patch_event(
        "cal_event_1",
        title="Moved",
        end_date=None,
        notes=None,
        start_time=dt.time(8, 5),
        occurrence=dt.datetime(2026, 10, 28, 16, tzinfo=dt.timezone(dt.timedelta(hours=3))),
        if_match=3,
    )
    assert body_of(mocked) == {
        "title": "Moved",
        "endDate": None,
        "notes": None,
        "startTime": "08:05",
        "occurrence": "2026-10-28T16:00:00+03:00",
    }
    assert headers_of(mocked)["If-Match"] == '"v3"'


async def test_patch_event_all_fields(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PATCH", "/calendar/events/cal_event_1", payload=applied(event()))
    await client.patch_event(
        "cal_event_1",
        all_day=False,
        date=dt.date(2026, 10, 22),
        end_time=dt.time(10),
        member_ids=["m"],
        location="Home",
        tags=["a"],
        occurrence="2026-10-28T16:00:00+03:00",
    )
    assert body_of(mocked) == {
        "allDay": False,
        "date": "2026-10-22",
        "endTime": "10:00",
        "memberIds": ["m"],
        "location": "Home",
        "tags": ["a"],
        "occurrence": "2026-10-28T16:00:00+03:00",
    }


async def test_replace_event_needs_if_match_and_sends_the_whole_event(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PUT", "/calendar/events/cal_event_1", payload=applied(event()))
    await client.replace_event(
        "cal_event_1",
        EventInput(title="Dentist", date=dt.date(2026, 10, 21), tags=["x"]),
        if_match='"v3"',
        occurrence=dt.date(2026, 10, 28),
        scope="following",
        keep_rule=True,
    )
    assert headers_of(mocked)["If-Match"] == '"v3"'
    assert body_of(mocked) == {
        "event": {
            "title": "Dentist",
            "allDay": False,
            "date": "2026-10-21",
            "endDate": None,
            "startTime": None,
            "endTime": None,
            "memberIds": [],
            "location": None,
            "notes": None,
            "recurrence": None,
            "reminder": None,
            "tags": ["x"],
        },
        "occurrence": "2026-10-28",
        "scope": "following",
        "keepRule": True,
    }


async def test_replace_event_without_tags(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PUT", "/calendar/events/cal_event_1", payload=applied(event()))
    await client.replace_event("cal_event_1", EventInput(title="A", date=dt.date(2026, 10, 21)), if_match=1)
    assert body_of(mocked)["event"]["tags"] is None


async def test_delete_event_and_one_occurrence(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "DELETE",
        "/calendar/events/cal_event_1",
        payload=applied({"id": "cal_event_1", "occurrence": None}),
    )
    whole = await client.delete_event("cal_event_1")
    assert isinstance(whole, Applied) and whole.result.occurrence is None
    assert not last(mocked)[2]["params"]

    route(
        mocked,
        "DELETE",
        "/calendar/events/cal_event_1",
        payload=applied({"id": "cal_event_1", "occurrence": "2026-10-28T16:00:00+03:00"}),
    )
    one = await client.delete_event(
        "cal_event_1", occurrence=dt.datetime(2026, 10, 28, 16, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    )
    assert isinstance(one, Applied) and isinstance(one.result.occurrence, dt.datetime)
    assert last(mocked)[2]["params"] == {"occurrence": "2026-10-28T16:00:00+03:00"}


async def test_occurrence_plus_sign_is_escaped_in_the_address() -> None:
    from yarl import URL

    url = URL("http://x/api").with_query({"occurrence": "2026-10-28T16:00:00+03:00"})
    assert "%2B03:00" in str(url)


async def test_add_shopping_items(client: FamilyOS, mocked: aioresponses) -> None:
    added = {"listId": "shop_list_1", "items": [shopping_item()], "addedIds": ["shop_item_1"]}
    route(mocked, "POST", "/lists/shopping/lists/shop_list_1/items", status=201, payload=applied(added))
    result = await client.add_shopping_items(
        "shop_list_1",
        [
            "Eggs",
            ShoppingItemInput("Milk", quantity="2 l", category="dairy"),
            ShoppingItemInput("Bread"),
            {"title": "Tea", "quantity": None},
        ],
        when_on_list="update",
    )
    assert isinstance(result, Applied) and result.result.added_ids == ["shop_item_1"]
    assert body_of(mocked) == {
        "items": [
            {"title": "Eggs"},
            {"title": "Milk", "quantity": "2 l", "category": "dairy"},
            {"title": "Bread"},
            {"title": "Tea", "quantity": None},
        ],
        "whenOnList": "update",
    }


async def test_add_shopping_items_without_options(client: FamilyOS, mocked: aioresponses) -> None:
    added = {"listId": "shop_list_1", "items": [], "addedIds": []}
    route(mocked, "POST", "/lists/shopping/lists/shop_list_1/items", status=201, payload=applied(added))
    await client.add_shopping_items("shop_list_1", ["Eggs"])
    assert body_of(mocked) == {"items": [{"title": "Eggs"}]}


async def test_shopping_item_changes(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PATCH", "/lists/shopping/items/shop_item_1", payload=applied(shopping_item(title="Oat milk")))
    await client.patch_shopping_item("shop_item_1", title="Oat milk", quantity=None, category="dairy")
    assert body_of(mocked) == {"title": "Oat milk", "quantity": None, "category": "dairy"}

    route(
        mocked,
        "POST",
        "/lists/shopping/items/shop_item_1/complete",
        payload=applied(shopping_item(done=True)),
    )
    done = await client.complete_shopping_item("shop_item_1", if_match=1)
    assert isinstance(done, Applied) and done.result.done
    assert last(mocked)[2]["json"] is None and headers_of(mocked)["If-Match"] == '"v1"'

    route(mocked, "POST", "/lists/shopping/items/shop_item_1/uncheck", payload=applied(shopping_item()))
    undone = await client.uncheck_shopping_item("shop_item_1")
    assert isinstance(undone, Applied) and not undone.result.done

    route(mocked, "DELETE", "/lists/shopping/items/shop_item_1", payload=applied({"id": "shop_item_1"}))
    gone = await client.delete_shopping_item("shop_item_1")
    assert isinstance(gone, Applied) and gone.result.id == "shop_item_1"


async def test_task_changes(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "POST", "/lists/tasks", status=201, payload=applied(task()))
    await client.add_task(
        "Pay the school fee",
        assignee_id="member_1",
        due_date=dt.date(2026, 10, 25),
        due_time=dt.time(9, 30),
        recurrence=Recurrence("monthly"),
        notes="Cash",
    )
    assert body_of(mocked) == {
        "title": "Pay the school fee",
        "assigneeId": "member_1",
        "dueDate": "2026-10-25",
        "dueTime": "09:30",
        "recurrence": {"freq": "monthly", "interval": 1},
        "notes": "Cash",
    }

    route(mocked, "PATCH", "/lists/tasks/task_1", payload=applied(task()))
    await client.patch_task(
        "task_1", title="T", assignee_id=None, due_date=None, due_time=dt.time(8), notes="Bring cash"
    )
    assert body_of(mocked) == {
        "title": "T",
        "assigneeId": None,
        "dueDate": None,
        "dueTime": "08:00",
        "notes": "Bring cash",
    }

    route(mocked, "PUT", "/lists/tasks/task_1", payload=applied(task()))
    await client.replace_task(
        "task_1",
        TaskInput(title="T", due_date=dt.date(2026, 11, 1)),
        if_match=1,
        only_this_date=dt.date(2026, 10, 25),
        keep_rule=False,
    )
    assert body_of(mocked) == {
        "task": {
            "title": "T",
            "assigneeId": None,
            "dueDate": "2026-11-01",
            "dueTime": None,
            "recurrence": None,
            "notes": None,
        },
        "onlyThisDate": "2026-10-25",
        "keepRule": False,
    }

    route(mocked, "POST", "/lists/tasks/task_1/complete", payload=applied(task(done=True)))
    await client.complete_task("task_1", due_date=dt.date(2026, 10, 25))
    assert body_of(mocked) == {"dueDate": "2026-10-25"}
    route(mocked, "POST", "/lists/tasks/task_1/complete", payload=applied(task(done=True)))
    await client.complete_task("task_1")
    assert body_of(mocked) == {}

    route(mocked, "POST", "/lists/tasks/task_1/reopen", payload=applied(task()))
    await client.reopen_task("task_1")
    route(mocked, "DELETE", "/lists/tasks/task_1", payload=applied({"id": "task_1"}))
    deleted = await client.delete_task("task_1")
    assert isinstance(deleted, Applied) and deleted.result.id == "task_1"


async def test_chore_changes(client: FamilyOS, mocked: aioresponses) -> None:
    done = {"chore": chore(), "completion": completion(), "reason": "already_done"}
    route(mocked, "POST", "/chores/chore_1/complete", payload=applied(done))
    result = await client.complete_chore("chore_1", dt.date(2026, 10, 20), done_by="member_1")
    assert isinstance(result, Applied) and result.result.reason == "already_done"
    assert result.result.completion.chore_id == "chore_1"
    assert body_of(mocked) == {"dueDate": "2026-10-20", "doneBy": "member_1"}

    route(mocked, "POST", "/chores/completions/chore_done_1/undo", payload=applied(chore()))
    undone = await client.undo_completion("chore_done_1")
    assert isinstance(undone, Applied) and undone.result.id == "chore_1"


async def test_create_note(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "POST", "/board/notes", status=201, payload=applied(note()))
    await client.create_note("Back at six", paper="note-2", pinned=True, ends_on=dt.date(2026, 10, 22))
    assert body_of(mocked) == {
        "text": "Back at six",
        "paper": "note-2",
        "pinned": True,
        "endsOn": "2026-10-22",
    }


async def test_a_change_that_waits_is_drafted(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "POST",
        "/board/notes",
        status=202,
        payload={"status": "drafted", "draftId": "draft_1", "timeZone": TZ},
    )
    result = await client.create_note("Hi")
    assert result == Drafted(draft_id="draft_1", time_zone=TZ)


async def test_a_replayed_answer_is_marked(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "POST",
        "/board/notes",
        status=201,
        payload=applied(note()),
        headers={"Idempotent-Replayed": "true"},
    )
    result = await client.create_note("Hi", idempotency_key="k")
    assert isinstance(result, Applied) and result.replayed


async def test_on_behalf_of_on_writes(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "POST", "/board/notes", status=201, payload=applied(note()))
    await client.create_note("Hi", on_behalf_of="member_leo")
    assert headers_of(mocked)["X-FamilyOS-On-Behalf-Of"] == "member_leo"


async def test_unset_is_falsy_and_readable() -> None:
    assert not UNSET
    assert repr(UNSET) == "UNSET"


async def test_patch_event_without_an_occurrence(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "PATCH", "/calendar/events/cal_event_1", payload=applied(event()))
    await client.patch_event("cal_event_1", title="x")
    assert "occurrence" not in body_of(mocked)
