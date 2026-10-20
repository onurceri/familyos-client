# familyos-client

An asynchronous Python client for the public API of [FamilyOS](https://github.com/onurceri/family-os), the
self-hosted family organizer. It reads and changes the calendar, shopping lists, tasks, chores and the family board
with an app key. It is the library the Home Assistant integration uses.

- Python 3.12+, `asyncio` only.
- `aiohttp` for HTTP (you pass in your session) and `mashumaro` for the typed models. Nothing else.
- Fully typed (`py.typed`, `mypy --strict`).

## Install

```bash
pip install familyos-client
```

## Quick start

1. In FamilyOS, as an admin, open **Settings → You → Connected apps** and press **Add an app**. Choose a name, a
   role and a mode, and copy the key when it appears. It is shown once.
2. Use your FamilyOS address and the key:

```python
import asyncio
import datetime as dt

import aiohttp

from familyos import Applied, Drafted, FamilyOS


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        famos = FamilyOS("https://family.example.com", "famos_...", session=session)

        info = await famos.discover()
        print(info.family_os, info.time_zone, info.caller.app.name)

        today = dt.date.today()
        async for occurrence in famos.iter_occurrences(today, today + dt.timedelta(days=7)):
            print(occurrence.start, occurrence.title)

        answer = await famos.add_task("Pay the school fee", due_date=today + dt.timedelta(days=5))
        if isinstance(answer, Applied):
            print("added", answer.result.id)
        else:  # an app in "Changes wait for approval" mode
            print("waits for a person:", answer.draft_id)


asyncio.run(main())
```

The client never closes a session you give it. Without `session=` it makes one and closes it in
`await famos.close()` (or leave an `async with FamilyOS(...)` block).

## What you get back

- **Models are dataclasses.** Instants are timezone-aware `datetime`, whole days are `date`, clock times are `time`.
  An all-day event's `start`/`end` are `date`. Fields the library does not know are ignored, so a newer FamilyOS
  does not break an older library.
- **Lists** come as a `Page` (`items`, `next`, `time_zone`) from `list_tasks()`, `list_chores()`,
  `list_notes()`, `list_occurrences()`, `list_shopping_lists()` and `list_shopping_items()`. The `iter_*` twins
  (`async for task in famos.iter_tasks()`) follow the pages for you.
- **Changes** return `Applied[T]` (`result`, `etag`, `time_zone`, `replayed`) or `Drafted` (`draft_id`) when the
  change waits for a person to approve it. Follow a draft with `await famos.get_draft(draft_id)`.
- **Every operation** of the API has a method of the same name in snake_case: `create_event`, `patch_event`,
  `replace_event`, `delete_event`, `get_event`, `list_occurrences`, `add_shopping_items`,
  `complete_shopping_item`, `uncheck_shopping_item`, `add_task`, `complete_task`, `reopen_task`, `complete_chore`,
  `undo_completion`, `create_note`, `discover`, `get_draft`, `changes` and the rest.

### Polling cheaply: ETag

Lists and single reads send `If-None-Match` for you. An unchanged answer is `304` and you get the cached page back
with `page.not_modified == True`, which costs the server almost nothing:

```python
page = await famos.list_tasks()
...
page = await famos.list_tasks()
if not page.not_modified:
    refresh(page.items)
```

### What changed since I looked

```python
cursor = (await famos.changes()).cursor  # where "now" is; read everything you need
...
try:
    result = await famos.changes(cursor)  # the things that changed since
except CursorExpired:  # older than 30 days: read everything again
    ...
cursor = result.cursor  # ask again at once while result.more is true
```

### Writing safely

```python
version = (await famos.get_task("task_...")).version
await famos.patch_task("task_...", notes="Bring cash", if_match=version)  # 412 if it moved on
```

- `if_match=` takes the `version` you read (or an `ETag` text). A stale one raises `PreconditionFailed` with
  `current_version`. `replace_*` requires it.
- Every write sends an `Idempotency-Key` (generated per call; pass `idempotency_key=` to choose, so a retry after a
  network error does not happen twice).
- `patch_*` changes only the arguments you pass. `None` clears a field that may be empty.

### Acting for a member

```python
famos = FamilyOS(url, key, session=session, on_behalf_of="member_...")  # the default for every call
await famos.add_task("Homework", on_behalf_of="member_other")  # one call
await famos.add_task("Chore for nobody", on_behalf_of=None)  # none for this call
```

## Errors

All errors derive from `FamilyOSError` and carry the API's `code` (`ErrorCode`), `message` and HTTP `status`.

| Exception | When |
|---|---|
| `AuthenticationError` | 401, no valid key |
| `ForbiddenError` | 403; `reason` says why (`capability`, `approval`, `critical`, `on_behalf`) |
| `NotFoundError` | 404, also when the feature is switched off (`code` is `feature_off`) |
| `PreconditionFailed` | 412; `current_version` |
| `CursorExpired` | 410, from `changes(since)` |
| `RateLimited` | 429; `retry_after` seconds |
| `BadRequestError` | 400 (see `fields`) and 422 |
| `ConflictError` | 409 |
| `ConnectionError`, `TimeoutError` | FamilyOS could not be reached / did not answer in time (the library's own classes) |

## Versioning

The library follows FamilyOS's public API **v1**, which is expand-only: new operations and fields can appear,
nothing is renamed or removed. The OpenAPI document the library is checked against is in
`familyos/openapi/v1.json`; a test fails when the library and that document disagree. A breaking change to the API
would be `/api/public/v2` and a new major version of this library.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                                  # unit and contract tests, 100% branch coverage
FAMOS_URL=http://localhost:3000 FAMOS_TOKEN=famos_... uv run pytest -m integration --no-cov
```

The integration tests change data in the family they run against (they create and delete their own tasks, events
and items, and leave one note on the board). Point them at a scratch FamilyOS, with a key whose role may change the
calendar, lists and chores and whose mode is "Changes happen at once".

## License

AGPL-3.0-or-later, the same as FamilyOS.
