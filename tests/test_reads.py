"""Reading: every read method, paging, ETag caching, acting for a member, time zones."""

import datetime as dt
from typing import Any

import pytest
from aioresponses import aioresponses

from familyos import FamilyOS, FamilyOSError
from tests.conftest import (
    API,
    TOKEN,
    TZ,
    chore,
    completion,
    discovery,
    event,
    last,
    note,
    occurrence,
    page,
    route,
    sent,
    shopping_item,
    shopping_list,
    task,
)

pytestmark = pytest.mark.asyncio


async def test_discover(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/", payload=discovery())
    found = await client.discover()
    assert found.family_os == "0.3.0"
    assert found.caller.app.name == "Hall"
    assert found.caller.member_id is None
    assert [(m.member_id, m.name) for m in found.acts_for] == [("member_1", "Leo")]
    assert found.can("calendar.createEvent")
    assert not found.can("calendar.deleteEvent")
    assert found.enabled("calendar")
    assert not found.enabled("board")
    assert not found.enabled("nothing")
    method, url, kwargs = last(mocked)
    assert (method, url) == ("GET", API + "/")
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert "X-FamilyOS-On-Behalf-Of" not in kwargs["headers"]


async def test_get_draft(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "GET",
        "/drafts/draft_1",
        payload={
            "id": "draft_1",
            "status": "applied",
            "operation": "board.createNote",
            "result": note(),
            "error": None,
            "createdAt": "2026-10-20T08:00:00+03:00",
            "decidedAt": "2026-10-20T09:00:00+03:00",
            "timeZone": TZ,
        },
    )
    draft = await client.get_draft("draft_1")
    assert draft.status == "applied"
    assert draft.result["text"] == "Back at six"
    assert draft.decided_at is not None and draft.decided_at.utcoffset() == dt.timedelta(hours=3)


async def test_changes(client: FamilyOS, mocked: aioresponses) -> None:
    body = {
        "changes": [
            {
                "seq": 813,
                "at": "2026-10-20T08:00:00+03:00",
                "extension": "calendar",
                "entity": "event",
                "id": "cal_event_1",
                "op": "upsert",
            }
        ],
        "cursor": 813,
        "more": False,
        "timeZone": TZ,
    }
    route(mocked, "GET", "/changes", payload=body)
    route(mocked, "GET", "/changes", payload={**body, "changes": []})
    now = await client.changes()
    assert now.cursor == 813 and now.changes[0].op == "upsert"
    assert "since" not in (last(mocked)[2]["params"] or {})
    await client.changes(812)
    assert last(mocked)[2]["params"] == {"since": "812"}


async def test_occurrences_and_time_zones(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "GET",
        "/calendar/occurrences",
        payload=page(
            [
                occurrence(),
                occurrence(allDay=True, recurrenceId="2026-10-29", start="2026-10-29", end="2026-10-29"),
            ]
        ),
    )
    result = await client.list_occurrences(dt.date(2026, 10, 20), dt.date(2026, 10, 31), limit=50)
    assert last(mocked)[2]["params"] == {"from": "2026-10-20", "to": "2026-10-31", "limit": "50"}
    timed, all_day = result.items
    assert isinstance(timed.start, dt.datetime) and timed.start.utcoffset() == dt.timedelta(hours=3)
    assert timed.start.astimezone(dt.UTC).hour == 13
    assert isinstance(timed.recurrence_id, dt.datetime)
    assert isinstance(all_day.start, dt.date) and not isinstance(all_day.start, dt.datetime)
    assert all_day.recurrence_id == dt.date(2026, 10, 29)
    assert result.time_zone == TZ and result.next is None and not result.not_modified


async def test_occurrence_with_a_subscription_source(client: FamilyOS, mocked: aioresponses) -> None:
    source = {"kind": "subscription", "id": "sub_1", "name": "School", "color": "blue"}
    route(mocked, "GET", "/calendar/occurrences", payload=page([occurrence(source=source, editable=False)]))
    result = await client.list_occurrences(dt.date(2026, 10, 20), dt.date(2026, 10, 31))
    assert result.items[0].source is not None and result.items[0].source.name == "School"


async def test_iter_occurrences_follows_the_cursor(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/calendar/occurrences", payload=page([occurrence(title="A")], next="c2"))
    route(mocked, "GET", "/calendar/occurrences", payload=page([occurrence(title="B")]))
    titles = [o.title async for o in client.iter_occurrences(dt.date(2026, 10, 1), dt.date(2026, 10, 31))]
    assert titles == ["A", "B"]
    calls = sent(mocked)
    assert "cursor" not in calls[0][2]["params"]
    assert calls[1][2]["params"]["cursor"] == "c2"


async def test_get_event(client: FamilyOS, mocked: aioresponses) -> None:
    body = {
        "item": event(
            recurrence={"freq": "weekly", "interval": 2, "byDay": ["MO", "WE"], "until": "2026-12-31"},
            seriesId="cal_event_0",
            recurrenceKey="2026-10-21T09:00:00+03:00",
        ),
        "timeZone": TZ,
    }
    route(mocked, "GET", "/calendar/events/cal_event_1", payload=body)
    got = await client.get_event("cal_event_1")
    assert got.start_time == dt.time(9, 0) and got.date == dt.date(2026, 10, 21)
    assert got.recurrence is not None
    assert got.recurrence.by_day == ["MO", "WE"] and got.recurrence.until == dt.date(2026, 12, 31)
    assert got.series_id == "cal_event_0"


async def test_shopping_reads(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/shopping/lists", payload=page([shopping_list()]))
    lists = await client.list_shopping_lists(limit=10, cursor="c")
    assert lists.items[0].is_default and lists.items[0].open_count == 2
    assert last(mocked)[2]["params"] == {"limit": "10", "cursor": "c"}

    route(mocked, "GET", "/lists/shopping/lists/shop_list_1/items", payload=page([shopping_item()]))
    items = await client.list_shopping_items("shop_list_1")
    assert items.items[0].quantity == "2 l" and items.items[0].category == "dairy"

    route(mocked, "GET", "/lists/shopping/lists", payload=page([shopping_list()], next="n"))
    route(mocked, "GET", "/lists/shopping/lists", payload=page([shopping_list(id="shop_list_2")]))
    ids = [x.id async for x in client.iter_shopping_lists()]
    assert ids == ["shop_list_1", "shop_list_2"]

    route(mocked, "GET", "/lists/shopping/lists/shop_list_1/items", payload=page([shopping_item()]))
    assert [x.id async for x in client.iter_shopping_items("shop_list_1")] == ["shop_item_1"]


async def test_task_reads(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task(), task(id="task_2", dueDate=None, due=None)]))
    tasks = await client.list_tasks()
    assert tasks.items[0].due == dt.date(2026, 10, 25)
    assert tasks.items[1].due is None

    timed = task(dueTime="09:30", due="2026-10-25T09:30:00+03:00", done=True, doneAt="2026-10-25T10:00:00+03:00")
    route(mocked, "GET", "/lists/tasks/task_1", payload={"item": timed, "timeZone": TZ})
    got = await client.get_task("task_1")
    assert isinstance(got.due, dt.datetime) and got.due_time == dt.time(9, 30)
    assert got.done and got.done_at is not None

    route(mocked, "GET", "/lists/tasks", payload=page([task()], next="n"))
    route(mocked, "GET", "/lists/tasks", payload=page([task(id="task_2")]))
    assert [t.id async for t in client.iter_tasks(limit=1)] == ["task_1", "task_2"]


async def test_chore_reads(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/chores", payload=page([chore(), chore(id="chore_2", lastDone=None, nextDue=None)]))
    chores = await client.list_chores()
    assert chores.items[0].rotation == "round_robin" and chores.items[0].next_due == dt.date(2026, 10, 27)
    assert chores.items[0].last_done is not None and chores.items[0].last_done.id == "chore_done_1"
    assert chores.items[1].last_done is None

    detail = chore(history=[completion(), completion(id="chore_done_0", undoneAt="2026-10-19T08:00:00+03:00")])
    route(mocked, "GET", "/chores/chore_1", payload={"item": detail, "timeZone": TZ})
    got = await client.get_chore("chore_1")
    assert len(got.history) == 2 and got.history[1].undone_at is not None

    route(mocked, "GET", "/chores", payload=page([chore()], next="n"))
    route(mocked, "GET", "/chores", payload=page([chore(id="chore_2")]))
    assert [c.id async for c in client.iter_chores()] == ["chore_1", "chore_2"]


async def test_board_reads(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/board/notes", payload=page([note()]))
    notes = await client.list_notes()
    assert notes.items[0].ends_on == dt.date(2026, 10, 22) and notes.items[0].ends_at is None

    route(mocked, "GET", "/board/notes", payload=page([note()], next="n"))
    route(mocked, "GET", "/board/notes", payload=page([note(id="board_note_2")]))
    assert [n.id async for n in client.iter_notes()] == ["board_note_1", "board_note_2"]


async def test_unknown_fields_are_ignored(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task(brandNew={"x": 1})]))
    assert (await client.list_tasks()).items[0].id == "task_1"


async def test_on_behalf_of_default_and_per_call(session: Any, mocked: aioresponses) -> None:
    client = FamilyOS("http://famos.test", TOKEN, session=session, on_behalf_of="member_leo")
    route(mocked, "GET", "/lists/tasks", payload=page([]))
    route(mocked, "GET", "/lists/tasks", payload=page([]))
    route(mocked, "GET", "/lists/tasks", payload=page([]))
    await client.list_tasks()
    assert last(mocked)[2]["headers"]["X-FamilyOS-On-Behalf-Of"] == "member_leo"
    await client.list_tasks(on_behalf_of="member_mia")
    assert last(mocked)[2]["headers"]["X-FamilyOS-On-Behalf-Of"] == "member_mia"
    await client.list_tasks(on_behalf_of=None)
    assert "X-FamilyOS-On-Behalf-Of" not in last(mocked)[2]["headers"]


# --- ETag caching -------------------------------------------------------------------------------------


async def test_etag_not_modified_returns_the_cached_page(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"abc"'})
    route(mocked, "GET", "/lists/tasks", status=304, headers={"ETag": '"abc"'})
    first = await client.list_tasks()
    assert not first.not_modified
    assert "If-None-Match" not in sent(mocked)[0][2]["headers"]
    second = await client.list_tasks()
    assert second.not_modified and second.items == first.items
    assert sent(mocked)[1][2]["headers"]["If-None-Match"] == '"abc"'


async def test_etag_changed_replaces_the_cache(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"a"'})
    route(mocked, "GET", "/lists/tasks", payload=page([task(title="New")]), headers={"ETag": '"b"'})
    route(mocked, "GET", "/lists/tasks", status=304)
    await client.list_tasks()
    changed = await client.list_tasks()
    assert not changed.not_modified and changed.items[0].title == "New"
    again = await client.list_tasks()
    assert again.not_modified and again.items[0].title == "New"
    assert last(mocked)[2]["headers"]["If-None-Match"] == '"b"'


async def test_cache_is_per_query_and_per_member(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"a"'})
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"b"'})
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"c"'})
    await client.list_tasks()
    await client.list_tasks(limit=5)
    await client.list_tasks(on_behalf_of="member_1")
    for _, _, kwargs in sent(mocked):
        assert "If-None-Match" not in kwargs["headers"]


async def test_single_reads_are_cached_too(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "GET",
        "/lists/tasks/task_1",
        payload={"item": task(), "timeZone": TZ},
        headers={"ETag": '"v1"'},
    )
    route(mocked, "GET", "/lists/tasks/task_1", status=304)
    first = await client.get_task("task_1")
    assert await client.get_task("task_1") == first
    assert last(mocked)[2]["headers"]["If-None-Match"] == '"v1"'


async def test_clear_cache(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"a"'})
    route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": '"a"'})
    await client.list_tasks()
    client.clear_cache()
    await client.list_tasks()
    assert "If-None-Match" not in last(mocked)[2]["headers"]


async def test_cache_keeps_the_most_recent_entries(
    client: FamilyOS, mocked: aioresponses, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("familyos.client._CACHE_SIZE", 1)
    for tag in ("a", "b", "c"):
        route(mocked, "GET", "/lists/tasks", payload=page([task()]), headers={"ETag": f'"{tag}"'})
    await client.list_tasks(limit=1)
    await client.list_tasks(limit=2)  # evicts limit=1
    await client.list_tasks(limit=1)
    assert "If-None-Match" not in last(mocked)[2]["headers"]


async def test_304_without_a_cache_is_an_error(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=304)
    with pytest.raises(FamilyOSError):
        await client.list_tasks()


def draft_summary(**over: Any) -> dict[str, Any]:
    return {
        "id": "draft_1",
        "status": "waiting",
        "extension": "calendar",
        "title": "Add dentist on Friday",
        "subject": None,
        "proposedAt": "2026-10-20T08:00:00+03:00",
        "proposedBy": "Leo via Home Assistant",
        "canDecide": False,
        **over,
    }


async def test_list_and_iterate_waiting_drafts(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/drafts", payload=page([draft_summary()]))
    result = await client.list_drafts(limit=5, on_behalf_of="member_leo")
    draft = result.items[0]
    assert draft.title == "Add dentist on Friday" and draft.subject is None and not draft.can_decide
    assert draft.proposed_at.utcoffset() == dt.timedelta(hours=3)
    _, _, kwargs = last(mocked)
    assert kwargs["params"] == {"status": "waiting", "limit": "5"}
    assert kwargs["headers"]["X-FamilyOS-On-Behalf-Of"] == "member_leo"

    route(mocked, "GET", "/drafts", payload=page([draft_summary()], next="n"))
    route(mocked, "GET", "/drafts", payload=page([draft_summary(id="draft_2", status="failed")]))
    assert [d.id async for d in client.iter_drafts()] == ["draft_1", "draft_2"]
