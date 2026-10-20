"""The asynchronous client of the FamilyOS public API (v1)."""

import builtins
import datetime as dt
import json
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Self, TypeVar
from urllib.parse import quote

import aiohttp

from .exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    CursorExpired,
    FamilyOSError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailed,
    RateLimited,
    TimeoutError,
)
from .models import (
    UNSET,
    Applied,
    BoardNote,
    Changes,
    Chore,
    ChoreCompleted,
    ChoreDetail,
    Discovery,
    Draft,
    Drafted,
    ErrorCode,
    Event,
    EventDeleted,
    EventInput,
    Model,
    Moment,
    Occurrence,
    Page,
    Recurrence,
    ShoppingItem,
    ShoppingItemDeleted,
    ShoppingItemInput,
    ShoppingItemsAdded,
    ShoppingList,
    Task,
    TaskDeleted,
    TaskInput,
    Unset,
    format_moment,
    format_time,
)

T = TypeVar("T")
M = TypeVar("M", bound=Model)

API_PATH = "/api/public/v1"
DEFAULT_TIMEOUT = 10.0
_CACHE_SIZE = 256

_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    410: ErrorCode.GONE,
    412: ErrorCode.PRECONDITION_FAILED,
    422: ErrorCode.IDEMPOTENCY_MISMATCH,
    429: ErrorCode.LIMIT_REACHED,
    503: ErrorCode.UNAVAILABLE,
}

OnBehalfOf = str | None | Unset
"""A member id; ``None`` for nobody; left out to use the client's default."""

IfMatch = int | str | None
"""A thing's ``version`` (or its ``ETag`` text), to refuse the change if it moved on."""


def _version_tag(value: IfMatch) -> str | None:
    if value is None:
        return None
    return f'"v{value}"' if isinstance(value, int) else value


def _json(value: Any) -> Any:
    """Turn dates, times and recurrences into what the API takes."""
    if isinstance(value, Recurrence):
        return value.to_api()
    if isinstance(value, dt.datetime):
        return format_moment(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return format_time(value)
    if isinstance(value, Mapping):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _given(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The fields that were passed (not ``UNSET``), as JSON. ``None`` stays: it clears the field."""
    return {name: _json(value) for name, value in fields.items() if value is not UNSET}


def _present(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The fields that are not ``None``, as JSON."""
    return {name: _json(value) for name, value in fields.items() if value is not None}


def _occurrence(value: Moment | str | None) -> str | None:
    return value if value is None or isinstance(value, str) else format_moment(value)


def _error(status: int, body: Any, headers: Mapping[str, str]) -> FamilyOSError:
    """Map an error answer to its exception."""
    detail: Mapping[str, Any] = {}
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        detail = body["error"]
    raw = detail.get("code")
    code: ErrorCode | None
    try:
        code = ErrorCode(raw) if raw is not None else _STATUS_CODES.get(status)
    except ValueError:
        code = _STATUS_CODES.get(status)
    message = str(detail.get("message") or f"FamilyOS answered {status}")
    extra: dict[str, Any] = {
        "code": code,
        "status": status,
        "fields": detail.get("fields"),
        "reason": detail.get("reason"),
        "capability": detail.get("capability"),
        "current_version": detail.get("currentVersion"),
    }
    if status == 401:
        return AuthenticationError(message, **extra)
    if status == 403:
        return ForbiddenError(message, **extra)
    if status == 404:
        return NotFoundError(message, **extra)
    if status == 410:
        return CursorExpired(message, **extra)
    if status == 412:
        return PreconditionFailed(message, **extra)
    if status == 429:
        try:
            retry_after: float | None = float(headers["Retry-After"])
        except (KeyError, ValueError):
            retry_after = None
        return RateLimited(message, retry_after=retry_after, **extra)
    if status == 409:
        return ConflictError(message, **extra)
    if status in (400, 422):
        return BadRequestError(message, **extra)
    return FamilyOSError(message, **extra)


class FamilyOS:
    """Talks to one FamilyOS server with one app key.

    ``url`` is the server's address (``https://family.example.com``); the API path is added. Pass the
    ``aiohttp.ClientSession`` your application owns as ``session``; without one the client makes its own and
    closes it in :meth:`close`. ``on_behalf_of`` is a member id sent as ``X-FamilyOS-On-Behalf-Of`` on every
    call unless a call says otherwise.

    Lists answer with an ``ETag`` and are cached: asking again sends ``If-None-Match`` and a ``304``
    returns the cached page with ``not_modified`` set. Changes return :class:`Applied` or, when a
    person must approve, :class:`Drafted`.
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        session: aiohttp.ClientSession | None = None,
        on_behalf_of: str | None = None,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        base = url.rstrip("/")
        self._base = base if base.endswith(API_PATH) else base + API_PATH
        self._token = token
        self._session = session
        self._own_session = False
        self.on_behalf_of = on_behalf_of
        self.request_timeout = request_timeout
        self._cache: OrderedDict[tuple[Any, ...], tuple[str, Any]] = OrderedDict()

    def __repr__(self) -> str:
        return f"FamilyOS({self._base!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the session if the client made it. A session you passed in stays open."""
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None
            self._own_session = False

    def clear_cache(self) -> None:
        """Forget the cached lists; the next reads fetch everything again."""
        self._cache.clear()

    # --- the transport ------------------------------------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> tuple[int, Mapping[str, str], Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        sent = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        member = self.on_behalf_of if isinstance(on_behalf_of, Unset) else on_behalf_of
        if member:
            sent["X-FamilyOS-On-Behalf-Of"] = member
        sent.update(headers or {})
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}
        try:
            async with self._session.request(
                method,
                self._base + path,
                params=query or None,
                json=body,
                headers=sent,
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
            ) as response:
                status = response.status
                answer = response.headers
                payload: Any = None
                if status != 304:
                    text = await response.text()
                    if text:
                        try:
                            payload = json.loads(text)
                        except ValueError:
                            if status < 400:
                                raise FamilyOSError(
                                    "FamilyOS answered something that is not JSON", status=status
                                ) from None
        except FamilyOSError:
            raise
        except builtins.TimeoutError as err:
            raise TimeoutError("FamilyOS did not answer in time") from err
        except aiohttp.ClientError as err:
            raise ConnectionError(f"Cannot reach FamilyOS: {err.__class__.__name__}") from err
        if status >= 400:
            raise _error(status, payload, answer)
        if payload is None and status != 304:
            raise FamilyOSError("FamilyOS answered with nothing", status=status)
        return status, answer, payload

    async def _read(
        self,
        path: str,
        parse: Callable[[Any, bool], T],
        *,
        params: Mapping[str, Any] | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> T:
        """A read with ETag caching: ``parse(body, not_modified)`` builds the result."""
        member = self.on_behalf_of if isinstance(on_behalf_of, Unset) else on_behalf_of
        key = (member, path, tuple(sorted((k, str(v)) for k, v in (params or {}).items() if v is not None)))
        cached = self._cache.get(key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        status, answer, payload = await self._send(
            "GET", path, params=params, headers=headers, on_behalf_of=on_behalf_of
        )
        if status == 304 and cached is not None:
            self._cache.move_to_end(key)
            return parse(cached[1], True)
        if status == 304:
            raise FamilyOSError("FamilyOS answered 304 for a request that had no ETag", status=304)
        etag = answer.get("ETag")
        if etag:
            self._cache[key] = (etag, payload)
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return parse(payload, False)

    async def _write(
        self,
        method: str,
        path: str,
        parse: Callable[[Any], T],
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[T] | Drafted:
        headers = {"Idempotency-Key": idempotency_key or uuid.uuid4().hex}
        tag = _version_tag(if_match)
        if tag is not None:
            headers["If-Match"] = tag
        status, answer, payload = await self._send(
            method, path, params=params, body=body, headers=headers, on_behalf_of=on_behalf_of
        )
        if status == 202 or (isinstance(payload, Mapping) and payload.get("status") == "drafted"):
            return Drafted(draft_id=payload["draftId"], time_zone=payload["timeZone"])
        return Applied(
            result=parse(payload["result"]),
            time_zone=payload["timeZone"],
            etag=answer.get("ETag"),
            replayed=answer.get("Idempotent-Replayed") == "true",
        )

    def _page(self, cls: type[M]) -> Callable[[Any, bool], Page[M]]:
        def parse(body: Any, not_modified: bool) -> Page[M]:
            return Page(
                items=[cls.from_dict(item) for item in body["items"]],
                next=body["next"],
                time_zone=body["timeZone"],
                not_modified=not_modified,
            )

        return parse

    async def _pages(
        self,
        path: str,
        cls: type[M],
        params: Mapping[str, Any],
        on_behalf_of: OnBehalfOf,
    ) -> AsyncIterator[M]:
        cursor = params.get("cursor")
        while True:
            page = await self._read(
                path, self._page(cls), params={**params, "cursor": cursor}, on_behalf_of=on_behalf_of
            )
            for item in page.items:
                yield item
            if page.next is None:
                return
            cursor = page.next

    # --- discovery, drafts, changes -----------------------------------------------------------------------

    async def discover(self, *, on_behalf_of: OnBehalfOf = UNSET) -> Discovery:
        """What this server is, and what this key may do now."""
        _, _, body = await self._send("GET", "/", on_behalf_of=on_behalf_of)
        return Discovery.from_dict(body)

    async def get_draft(self, draft_id: str, *, on_behalf_of: OnBehalfOf = UNSET) -> Draft:
        """How a change that waited for a person ended. Only the app (and member) that made it can read it."""
        _, _, body = await self._send("GET", f"/drafts/{_id(draft_id)}", on_behalf_of=on_behalf_of)
        return Draft.from_dict(body)

    async def changes(self, since: int | None = None, *, on_behalf_of: OnBehalfOf = UNSET) -> Changes:
        """What changed since ``since`` (a cursor). Without it, answers where "now" is, with no changes.

        Raises :class:`CursorExpired` when the cursor is older than the server keeps (30 days): read
        everything again and start from ``changes()``.
        """
        _, _, body = await self._send("GET", "/changes", params={"since": since}, on_behalf_of=on_behalf_of)
        return Changes.from_dict(body)

    # --- calendar -----------------------------------------------------------------------------------------

    async def list_occurrences(
        self,
        from_date: dt.date,
        to_date: dt.date,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[Occurrence]:
        """One page of the events between two days (inclusive), repeating events expanded."""
        return await self._read(
            "/calendar/occurrences",
            self._page(Occurrence),
            params={
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "limit": limit,
                "cursor": cursor,
            },
            on_behalf_of=on_behalf_of,
        )

    def iter_occurrences(
        self,
        from_date: dt.date,
        to_date: dt.date,
        *,
        limit: int | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> AsyncIterator[Occurrence]:
        """Every occurrence between two days, page after page."""
        params = {"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": limit}
        return self._pages("/calendar/occurrences", Occurrence, params, on_behalf_of)

    async def get_event(self, event_id: str, *, on_behalf_of: OnBehalfOf = UNSET) -> Event:
        """One event."""
        return await self._read(f"/calendar/events/{_id(event_id)}", _item(Event), on_behalf_of=on_behalf_of)

    async def create_event(
        self,
        title: str,
        date: dt.date,
        *,
        all_day: bool | None = None,
        end_date: dt.date | None = None,
        start_time: dt.time | None = None,
        end_time: dt.time | None = None,
        member_ids: Sequence[str] | None = None,
        location: str | None = None,
        notes: str | None = None,
        recurrence: Recurrence | None = None,
        reminder: int | None = None,
        tags: Sequence[str] | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Event] | Drafted:
        """Add an event. ``date`` and ``start_time`` are the family's own: no time zone to think of."""
        body = _present(
            {
                "title": title,
                "allDay": all_day,
                "date": date,
                "endDate": end_date,
                "startTime": start_time,
                "endTime": end_time,
                "memberIds": member_ids,
                "location": location,
                "notes": notes,
                "recurrence": recurrence,
                "reminder": reminder,
                "tags": tags,
            }
        )
        return await self._write(
            "POST",
            "/calendar/events",
            Event.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def patch_event(
        self,
        event_id: str,
        *,
        title: str | Unset = UNSET,
        all_day: bool | Unset = UNSET,
        date: dt.date | Unset = UNSET,
        end_date: dt.date | Unset | None = UNSET,
        start_time: dt.time | Unset = UNSET,
        end_time: dt.time | Unset | None = UNSET,
        member_ids: Sequence[str] | Unset = UNSET,
        location: str | Unset | None = UNSET,
        notes: str | Unset | None = UNSET,
        tags: Sequence[str] | Unset = UNSET,
        occurrence: Moment | str | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Event] | Drafted:
        """Change the fields you pass and keep the rest. ``None`` clears a field that may be empty.

        For a repeating event, ``occurrence`` (its ``recurrence_id``) changes only that date.
        """
        body = _given(
            {
                "title": title,
                "allDay": all_day,
                "date": date,
                "endDate": end_date,
                "startTime": start_time,
                "endTime": end_time,
                "memberIds": member_ids,
                "location": location,
                "notes": notes,
                "tags": tags,
            }
        )
        if occurrence is not None:
            body["occurrence"] = _occurrence(occurrence)
        return await self._write(
            "PATCH",
            f"/calendar/events/{_id(event_id)}",
            Event.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def replace_event(
        self,
        event_id: str,
        event: EventInput,
        *,
        if_match: IfMatch,
        occurrence: Moment | str | None = None,
        scope: str | None = None,
        keep_rule: bool | None = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Event] | Drafted:
        """Replace the whole event. ``if_match`` (the version you read) is required.

        For a repeating event, ``occurrence`` and ``scope`` (``this`` or ``following``) say which dates.
        """
        body: dict[str, Any] = {
            "event": _json(
                {
                    "title": event.title,
                    "allDay": event.all_day,
                    "date": event.date,
                    "endDate": event.end_date,
                    "startTime": event.start_time,
                    "endTime": event.end_time,
                    "memberIds": list(event.member_ids),
                    "location": event.location,
                    "notes": event.notes,
                    "recurrence": event.recurrence,
                    "reminder": event.reminder,
                    "tags": None if event.tags is None else list(event.tags),
                }
            )
        }
        body.update(_present({"occurrence": _occurrence(occurrence), "scope": scope, "keepRule": keep_rule}))
        return await self._write(
            "PUT",
            f"/calendar/events/{_id(event_id)}",
            Event.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def delete_event(
        self,
        event_id: str,
        *,
        occurrence: Moment | str | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[EventDeleted] | Drafted:
        """Delete an event, or with ``occurrence`` only that date of a repeating event."""
        return await self._write(
            "DELETE",
            f"/calendar/events/{_id(event_id)}",
            EventDeleted.from_dict,
            params={"occurrence": _occurrence(occurrence)},
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    # --- shopping lists -----------------------------------------------------------------------------------

    async def list_shopping_lists(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[ShoppingList]:
        """One page of the shopping lists."""
        return await self._read(
            "/lists/shopping/lists",
            self._page(ShoppingList),
            params={"limit": limit, "cursor": cursor},
            on_behalf_of=on_behalf_of,
        )

    def iter_shopping_lists(
        self, *, limit: int | None = None, on_behalf_of: OnBehalfOf = UNSET
    ) -> AsyncIterator[ShoppingList]:
        """Every shopping list."""
        return self._pages("/lists/shopping/lists", ShoppingList, {"limit": limit}, on_behalf_of)

    async def list_shopping_items(
        self,
        list_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[ShoppingItem]:
        """One page of the items of a shopping list."""
        return await self._read(
            f"/lists/shopping/lists/{_id(list_id)}/items",
            self._page(ShoppingItem),
            params={"limit": limit, "cursor": cursor},
            on_behalf_of=on_behalf_of,
        )

    def iter_shopping_items(
        self, list_id: str, *, limit: int | None = None, on_behalf_of: OnBehalfOf = UNSET
    ) -> AsyncIterator[ShoppingItem]:
        """Every item of a shopping list."""
        return self._pages(
            f"/lists/shopping/lists/{_id(list_id)}/items", ShoppingItem, {"limit": limit}, on_behalf_of
        )

    async def add_shopping_items(
        self,
        list_id: str,
        items: Sequence[ShoppingItemInput | str | Mapping[str, Any]],
        *,
        when_on_list: str | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ShoppingItemsAdded] | Drafted:
        """Add items to a list. An item may be just its title. ``when_on_list`` is ``keep`` or ``update``."""
        lines: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                lines.append({"title": item})
            elif isinstance(item, ShoppingItemInput):
                lines.append(
                    _present({"title": item.title, "quantity": item.quantity, "category": item.category})
                )
            else:
                lines.append(dict(item))
        body: dict[str, Any] = {"items": lines}
        if when_on_list is not None:
            body["whenOnList"] = when_on_list
        return await self._write(
            "POST",
            f"/lists/shopping/lists/{_id(list_id)}/items",
            ShoppingItemsAdded.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def patch_shopping_item(
        self,
        item_id: str,
        *,
        title: str | Unset = UNSET,
        quantity: str | Unset | None = UNSET,
        category: str | Unset = UNSET,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ShoppingItem] | Drafted:
        """Change the fields you pass of a shopping item."""
        return await self._write(
            "PATCH",
            f"/lists/shopping/items/{_id(item_id)}",
            ShoppingItem.from_dict,
            body=_given({"title": title, "quantity": quantity, "category": category}),
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def complete_shopping_item(
        self,
        item_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ShoppingItem] | Drafted:
        """Tick an item off."""
        return await self._write(
            "POST",
            f"/lists/shopping/items/{_id(item_id)}/complete",
            ShoppingItem.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def uncheck_shopping_item(
        self,
        item_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ShoppingItem] | Drafted:
        """Put a ticked item back on the list."""
        return await self._write(
            "POST",
            f"/lists/shopping/items/{_id(item_id)}/uncheck",
            ShoppingItem.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def delete_shopping_item(
        self,
        item_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ShoppingItemDeleted] | Drafted:
        """Remove an item."""
        return await self._write(
            "DELETE",
            f"/lists/shopping/items/{_id(item_id)}",
            ShoppingItemDeleted.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    # --- tasks --------------------------------------------------------------------------------------------

    async def list_tasks(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[Task]:
        """One page of the open tasks and the one-off tasks done in the last week."""
        return await self._read(
            "/lists/tasks",
            self._page(Task),
            params={"limit": limit, "cursor": cursor},
            on_behalf_of=on_behalf_of,
        )

    def iter_tasks(
        self, *, limit: int | None = None, on_behalf_of: OnBehalfOf = UNSET
    ) -> AsyncIterator[Task]:
        """Every task."""
        return self._pages("/lists/tasks", Task, {"limit": limit}, on_behalf_of)

    async def get_task(self, task_id: str, *, on_behalf_of: OnBehalfOf = UNSET) -> Task:
        """One task."""
        return await self._read(f"/lists/tasks/{_id(task_id)}", _item(Task), on_behalf_of=on_behalf_of)

    async def add_task(
        self,
        title: str,
        *,
        assignee_id: str | None = None,
        due_date: dt.date | None = None,
        due_time: dt.time | None = None,
        recurrence: Recurrence | None = None,
        notes: str | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Task] | Drafted:
        """Add a task."""
        body = _present(
            {
                "title": title,
                "assigneeId": assignee_id,
                "dueDate": due_date,
                "dueTime": due_time,
                "recurrence": recurrence,
                "notes": notes,
            }
        )
        return await self._write(
            "POST",
            "/lists/tasks",
            Task.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def patch_task(
        self,
        task_id: str,
        *,
        title: str | Unset = UNSET,
        assignee_id: str | Unset | None = UNSET,
        due_date: dt.date | Unset | None = UNSET,
        due_time: dt.time | Unset | None = UNSET,
        notes: str | Unset | None = UNSET,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Task] | Drafted:
        """Change the fields you pass and keep the rest. ``None`` clears a field that may be empty."""
        body = _given(
            {
                "title": title,
                "assigneeId": assignee_id,
                "dueDate": due_date,
                "dueTime": due_time,
                "notes": notes,
            }
        )
        return await self._write(
            "PATCH",
            f"/lists/tasks/{_id(task_id)}",
            Task.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def replace_task(
        self,
        task_id: str,
        task: TaskInput,
        *,
        if_match: IfMatch,
        only_this_date: dt.date | None = None,
        keep_rule: bool | None = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Task] | Drafted:
        """Replace the whole task. ``if_match`` (the version you read) is required."""
        body: dict[str, Any] = {
            "task": _json(
                {
                    "title": task.title,
                    "assigneeId": task.assignee_id,
                    "dueDate": task.due_date,
                    "dueTime": task.due_time,
                    "recurrence": task.recurrence,
                    "notes": task.notes,
                }
            )
        }
        body.update(_present({"onlyThisDate": only_this_date, "keepRule": keep_rule}))
        return await self._write(
            "PUT",
            f"/lists/tasks/{_id(task_id)}",
            Task.from_dict,
            body=body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def complete_task(
        self,
        task_id: str,
        *,
        due_date: dt.date | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Task] | Drafted:
        """Finish a task. For a repeating task, ``due_date`` is the date being done."""
        return await self._write(
            "POST",
            f"/lists/tasks/{_id(task_id)}/complete",
            Task.from_dict,
            body=_present({"dueDate": due_date}),
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def reopen_task(
        self,
        task_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Task] | Drafted:
        """Open a finished task again."""
        return await self._write(
            "POST",
            f"/lists/tasks/{_id(task_id)}/reopen",
            Task.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def delete_task(
        self,
        task_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[TaskDeleted] | Drafted:
        """Delete a task."""
        return await self._write(
            "DELETE",
            f"/lists/tasks/{_id(task_id)}",
            TaskDeleted.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    # --- chores -------------------------------------------------------------------------------------------

    async def list_chores(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[Chore]:
        """One page of the chores, each with ``next_due`` and ``overdue``."""
        return await self._read(
            "/chores",
            self._page(Chore),
            params={"limit": limit, "cursor": cursor},
            on_behalf_of=on_behalf_of,
        )

    def iter_chores(
        self, *, limit: int | None = None, on_behalf_of: OnBehalfOf = UNSET
    ) -> AsyncIterator[Chore]:
        """Every chore."""
        return self._pages("/chores", Chore, {"limit": limit}, on_behalf_of)

    async def get_chore(self, chore_id: str, *, on_behalf_of: OnBehalfOf = UNSET) -> ChoreDetail:
        """One chore with its history."""
        return await self._read(f"/chores/{_id(chore_id)}", _item(ChoreDetail), on_behalf_of=on_behalf_of)

    async def complete_chore(
        self,
        chore_id: str,
        due_date: dt.date,
        *,
        done_by: str | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[ChoreCompleted] | Drafted:
        """Mark a chore done for the date you saw. An app is nobody, so ``done_by`` says who did it."""
        return await self._write(
            "POST",
            f"/chores/{_id(chore_id)}/complete",
            ChoreCompleted.from_dict,
            body=_present({"dueDate": due_date, "doneBy": done_by}),
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    async def undo_completion(
        self,
        completion_id: str,
        *,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[Chore] | Drafted:
        """Take a completion back (the ``id`` of ``Chore.last_done`` or of a completion)."""
        return await self._write(
            "POST",
            f"/chores/completions/{_id(completion_id)}/undo",
            Chore.from_dict,
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )

    # --- board --------------------------------------------------------------------------------------------

    async def list_notes(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Page[BoardNote]:
        """One page of the notes on the family board."""
        return await self._read(
            "/board/notes",
            self._page(BoardNote),
            params={"limit": limit, "cursor": cursor},
            on_behalf_of=on_behalf_of,
        )

    def iter_notes(
        self, *, limit: int | None = None, on_behalf_of: OnBehalfOf = UNSET
    ) -> AsyncIterator[BoardNote]:
        """Every note on the board."""
        return self._pages("/board/notes", BoardNote, {"limit": limit}, on_behalf_of)

    async def create_note(
        self,
        text: str,
        *,
        paper: str | None = None,
        pinned: bool | None = None,
        ends_on: dt.date | None = None,
        if_match: IfMatch = None,
        idempotency_key: str | None = None,
        on_behalf_of: OnBehalfOf = UNSET,
    ) -> Applied[BoardNote] | Drafted:
        """Put a note on the board."""
        return await self._write(
            "POST",
            "/board/notes",
            BoardNote.from_dict,
            body=_present({"text": text, "paper": paper, "pinned": pinned, "endsOn": ends_on}),
            if_match=if_match,
            idempotency_key=idempotency_key,
            on_behalf_of=on_behalf_of,
        )


def _id(value: str) -> str:
    """An id for a path: ids are plain, but never let one change the path."""
    return quote(value, safe="")


def _item[M: Model](cls: type[M]) -> Callable[[Any, bool], M]:
    def parse(body: Any, _not_modified: bool) -> M:
        return cls.from_dict(body["item"])

    return parse
