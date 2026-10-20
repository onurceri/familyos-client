"""Against a real FamilyOS: every read, and write-and-undo cycles that leave the family as they found it.

Skipped unless ``FAMOS_URL`` and ``FAMOS_TOKEN`` are set. Use a scratch family: the key needs a role that may
change the calendar, lists and chores, and the app must be in ``apply`` mode. Run it with
``pytest -m integration --no-cov``.
"""

import datetime as dt
import os
import uuid
from collections.abc import AsyncIterator

import aiohttp
import pytest

from familyos import (
    Applied,
    AuthenticationError,
    Drafted,
    EventInput,
    FamilyOS,
    NotFoundError,
    PreconditionFailed,
    Recurrence,
    TaskInput,
)

URL = os.environ.get("FAMOS_URL")
TOKEN = os.environ.get("FAMOS_TOKEN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (URL and TOKEN), reason="FAMOS_URL and FAMOS_TOKEN are not set"),
]


def made[T](answer: Applied[T] | Drafted) -> Applied[T]:
    assert isinstance(answer, Applied), "the app must be in apply mode"
    return answer


@pytest.fixture
async def live() -> AsyncIterator[FamilyOS]:
    assert URL and TOKEN
    async with aiohttp.ClientSession() as session:
        yield FamilyOS(URL, TOKEN, session=session)


async def test_every_read(live: FamilyOS) -> None:
    found = await live.discover()
    assert found.api == "v1" and found.time_zone
    today = dt.date.today()

    occurrences = [o async for o in live.iter_occurrences(today - dt.timedelta(days=20), today + dt.timedelta(days=40))]
    lists = [x async for x in live.iter_shopping_lists()]
    tasks = [x async for x in live.iter_tasks()]
    chores = [x async for x in live.iter_chores()]
    notes = [x async for x in live.iter_notes()]
    for lst in lists:
        _ = [item async for item in live.iter_shopping_items(lst.id)]
        assert (await live.list_shopping_items(lst.id)).time_zone == found.time_zone

    if found.can("calendar.getEvent") and occurrences:
        own = next((o for o in occurrences if o.source is None), None)
        if own:
            assert (await live.get_event(own.event_id)).id == own.event_id
    if tasks:
        assert (await live.get_task(tasks[0].id)).id == tasks[0].id
    if chores:
        assert (await live.get_chore(chores[0].id)).id == chores[0].id
    assert isinstance(notes, list)

    live.clear_cache()
    first = await live.list_tasks()
    again = await live.list_tasks()
    assert not first.not_modified and again.not_modified and again.items == first.items

    now = await live.changes()
    assert now.cursor >= 0 and not now.changes


async def test_task_cycle_with_changes_and_versions(live: FamilyOS) -> None:
    cursor = (await live.changes()).cursor
    created = made(
        await live.add_task("familyos-client test task", due_date=dt.date.today(), idempotency_key=uuid.uuid4().hex)
    )
    task = created.result
    assert created.etag == f'"v{task.version}"'

    # The same key again is the first answer, and nothing happens twice.
    key = uuid.uuid4().hex
    first = made(await live.add_task("familyos-client replay", idempotency_key=key))
    replay = made(await live.add_task("familyos-client replay", idempotency_key=key))
    assert replay.replayed and replay.result.id == first.result.id

    patched = made(await live.patch_task(task.id, notes="note", if_match=task.version)).result
    assert patched.notes == "note" and patched.version > task.version
    with pytest.raises(PreconditionFailed) as stale:
        await live.patch_task(task.id, notes="again", if_match=task.version)
    assert stale.value.current_version == patched.version

    assert made(await live.complete_task(task.id)).result.done
    assert not made(await live.reopen_task(task.id)).result.done
    replaced = made(
        await live.replace_task(
            task.id,
            TaskInput(title="replaced", due_date=dt.date.today()),
            if_match=(await live.get_task(task.id)).version,
        )
    )
    assert replaced.result.title == "replaced"

    changed = await live.changes(cursor)
    assert any(c.id == task.id and c.op == "upsert" for c in changed.changes)

    made(await live.delete_task(task.id))
    made(await live.delete_task(first.result.id))
    with pytest.raises(NotFoundError):
        await live.get_task(task.id)
    after = await live.changes(cursor)
    assert any(c.id == task.id and c.op == "delete" for c in after.changes)


async def test_event_cycle(live: FamilyOS) -> None:
    day = dt.date.today() + dt.timedelta(days=3)
    created = made(
        await live.create_event(
            "familyos-client test event",
            day,
            start_time=dt.time(18, 0),
            end_time=dt.time(19, 0),
            recurrence=Recurrence("weekly", by_day=["MO", "TU", "WE", "TH", "FR", "SA", "SU"]),
        )
    ).result
    assert isinstance(created.start, dt.datetime) and created.start.tzinfo is not None

    occurrences = [o async for o in live.iter_occurrences(day, day + dt.timedelta(days=2))]
    mine = [o for o in occurrences if o.event_id == created.id]
    assert len(mine) == 3 and mine[1].recurring

    # Move one date of the series, then delete another (the `+` of the offset must survive the address).
    second = mine[1].recurrence_id
    moved = made(await live.patch_event(created.id, location="Park", occurrence=second)).result
    assert moved.series_id == created.id or moved.id != created.id
    deleted = made(await live.delete_event(created.id, occurrence=mine[2].recurrence_id)).result
    assert deleted.occurrence == mine[2].recurrence_id

    current = await live.get_event(created.id)
    replaced = made(
        await live.replace_event(
            created.id,
            EventInput(title="replaced", date=day, start_time=dt.time(17, 0), end_time=dt.time(18, 0)),
            if_match=current.version,
            scope="following",
            occurrence=mine[0].recurrence_id,
        )
    ).result
    assert replaced.title == "replaced"

    made(await live.delete_event(created.id))
    with pytest.raises(NotFoundError):
        await live.get_event(created.id)
    for stray in [o async for o in live.iter_occurrences(day, day + dt.timedelta(days=2))]:
        if stray.title in {"familyos-client test event", "replaced"} and stray.source is None:
            made(await live.delete_event(stray.event_id))


async def test_shopping_cycle(live: FamilyOS) -> None:
    default = next(x for x in [x async for x in live.iter_shopping_lists()] if x.is_default)
    added = made(
        await live.add_shopping_items(default.id, ["familyos-client milk", {"title": "familyos-client tea"}])
    ).result
    assert len(added.added_ids) == 2
    milk = added.items[0]
    assert made(await live.patch_shopping_item(milk.id, quantity="2 l", category="dairy")).result.quantity == "2 l"
    assert made(await live.complete_shopping_item(milk.id)).result.done
    assert not made(await live.uncheck_shopping_item(milk.id)).result.done
    for item in added.items:
        made(await live.delete_shopping_item(item.id))
    remaining = [i.title async for i in live.iter_shopping_items(default.id)]
    assert not any(title.startswith("familyos-client") for title in remaining)


async def test_chore_cycle(live: FamilyOS) -> None:
    chores = [c async for c in live.iter_chores()]
    if not chores:
        pytest.skip("the family has no chores")
    chore = chores[0]
    due = chore.next_due or chore.start_date
    members = chore.assignee_ids or [m for m in [chore.assignee_id] if m]
    done = made(await live.complete_chore(chore.id, due, done_by=members[0] if members else None)).result
    assert done.completion.chore_id == chore.id
    if done.reason is None:
        undone = made(await live.undo_completion(done.completion.id)).result
        assert undone.id == chore.id


async def test_board_note(live: FamilyOS) -> None:
    made_note = made(
        await live.create_note("familyos-client test note", ends_on=dt.date.today(), idempotency_key=uuid.uuid4().hex)
    ).result
    assert made_note.text == "familyos-client test note"


async def test_a_wrong_key_is_refused() -> None:
    assert URL
    async with aiohttp.ClientSession() as session:
        with pytest.raises(AuthenticationError):
            await FamilyOS(URL, "famos_not_a_key", session=session).discover()
