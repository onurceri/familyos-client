"""Errors, the transport, sessions and URLs."""

import builtins

import aiohttp
import pytest
from aioresponses import aioresponses

from familyos import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    CursorExpired,
    ErrorCode,
    FamilyOS,
    FamilyOSError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailed,
    RateLimited,
    TimeoutError,
)
from tests.conftest import API, BASE, TOKEN, error, last, page, route, task

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("status", "code", "exception"),
    [
        (400, "bad_request", BadRequestError),
        (401, "unauthenticated", AuthenticationError),
        (403, "forbidden", ForbiddenError),
        (404, "not_found", NotFoundError),
        (404, "feature_off", NotFoundError),
        (409, "conflict", ConflictError),
        (410, "gone", CursorExpired),
        (412, "precondition_failed", PreconditionFailed),
        (422, "idempotency_mismatch", BadRequestError),
        (429, "limit_reached", RateLimited),
        (500, "internal", FamilyOSError),
        (503, "unavailable", FamilyOSError),
    ],
)
async def test_error_mapping(
    client: FamilyOS, mocked: aioresponses, status: int, code: str, exception: type[FamilyOSError]
) -> None:
    route(mocked, "GET", "/lists/tasks", status=status, payload=error(code, "Because"))
    with pytest.raises(exception) as caught:
        await client.list_tasks()
    assert type(caught.value) is exception
    assert caught.value.code == ErrorCode(code)
    assert caught.value.status == status
    assert caught.value.message == "Because"
    assert str(caught.value) == f"{code}: Because"


async def test_bad_request_names_the_fields(client: FamilyOS, mocked: aioresponses) -> None:
    fields = [{"path": "items[0].title", "message": "Required"}]
    route(mocked, "POST", "/board/notes", status=400, payload=error("bad_request", "Wrong", fields=fields))
    with pytest.raises(BadRequestError) as caught:
        await client.create_note("")
    assert caught.value.fields == fields


async def test_forbidden_says_why(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "POST",
        "/board/notes",
        status=403,
        payload=error("forbidden", "No", reason="capability", capability="board.use"),
    )
    with pytest.raises(ForbiddenError) as caught:
        await client.create_note("x")
    assert caught.value.reason == "capability" and caught.value.capability == "board.use"
    assert caught.value.fields == []


async def test_precondition_failed_carries_the_current_version(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "PATCH",
        "/lists/tasks/task_1",
        status=412,
        payload=error("precondition_failed", currentVersion=7),
    )
    with pytest.raises(PreconditionFailed) as caught:
        await client.patch_task("task_1", title="x", if_match=2)
    assert caught.value.current_version == 7


async def test_rate_limited_has_retry_after(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "GET",
        "/lists/tasks",
        status=429,
        payload=error("limit_reached"),
        headers={"Retry-After": "42"},
    )
    with pytest.raises(RateLimited) as caught:
        await client.list_tasks()
    assert caught.value.retry_after == 42.0


@pytest.mark.parametrize("headers", [{}, {"Retry-After": "soon"}])
async def test_rate_limited_without_a_usable_retry_after(
    client: FamilyOS, mocked: aioresponses, headers: dict[str, str]
) -> None:
    route(mocked, "GET", "/lists/tasks", status=429, payload=error("limit_reached"), headers=headers)
    with pytest.raises(RateLimited) as caught:
        await client.list_tasks()
    assert caught.value.retry_after is None


async def test_changes_410_is_cursor_expired(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/changes", status=410, payload=error("gone", "Too old"))
    with pytest.raises(CursorExpired):
        await client.changes(1)


async def test_an_unknown_error_code_falls_back_to_the_status(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=409, payload=error("brand_new", "Hm"))
    with pytest.raises(ConflictError) as caught:
        await client.list_tasks()
    assert caught.value.code == ErrorCode.CONFLICT


async def test_an_error_that_is_not_the_api_format(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=502, body="<html>Bad gateway</html>")
    with pytest.raises(FamilyOSError) as caught:
        await client.list_tasks()
    assert caught.value.status == 502 and caught.value.code is None
    assert str(caught.value) == "FamilyOS answered 502"


async def test_an_error_with_an_unknown_status_and_empty_body(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=401, body="")
    with pytest.raises(AuthenticationError) as caught:
        await client.list_tasks()
    assert caught.value.code == ErrorCode.UNAUTHENTICATED


async def test_a_paused_app_is_a_401_with_its_reason(client: FamilyOS, mocked: aioresponses) -> None:
    route(
        mocked,
        "GET",
        "/lists/tasks",
        status=401,
        payload=error("unauthenticated", "This app is paused in FamilyOS", reason="paused"),
    )
    with pytest.raises(AuthenticationError) as caught:
        await client.list_tasks()
    assert caught.value.reason == "paused"
    assert caught.value.message == "This app is paused in FamilyOS"


async def test_a_refused_key_has_no_reason(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=401, payload=error("unauthenticated", "Unknown, expired or revoked"))
    with pytest.raises(AuthenticationError) as caught:
        await client.list_tasks()
    assert caught.value.reason is None


async def test_a_success_that_is_not_json(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", body="hello")
    with pytest.raises(FamilyOSError, match="not JSON"):
        await client.list_tasks()


async def test_a_success_with_nothing(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=200, body="")
    with pytest.raises(FamilyOSError, match="nothing"):
        await client.list_tasks()


async def test_connection_failure(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", exception=aiohttp.ClientConnectionError("refused"))
    with pytest.raises(ConnectionError) as caught:
        await client.list_tasks()
    assert caught.value.status is None and "refused" not in str(caught.value)


async def test_timeout(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", exception=builtins.TimeoutError())
    with pytest.raises(TimeoutError):
        await client.list_tasks()


async def test_the_key_is_never_in_an_error_or_repr(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", status=401, payload=error("unauthenticated", "No valid key"))
    with pytest.raises(AuthenticationError) as caught:
        await client.list_tasks()
    assert TOKEN not in str(caught.value) and TOKEN not in repr(client)
    assert last(mocked)[2]["headers"]["Authorization"] == f"Bearer {TOKEN}"


async def test_request_timeout_is_applied(session: aiohttp.ClientSession, mocked: aioresponses) -> None:
    client = FamilyOS(BASE, TOKEN, session=session, request_timeout=3)
    route(mocked, "GET", "/lists/tasks", payload=page([task()]))
    await client.list_tasks()
    assert last(mocked)[2]["timeout"].total == 3


@pytest.mark.parametrize("url", [BASE, BASE + "/", API, API + "/"])
async def test_url_forms(session: aiohttp.ClientSession, mocked: aioresponses, url: str) -> None:
    client = FamilyOS(url, TOKEN, session=session)
    route(mocked, "GET", "/", payload={"api": "v1"}, repeat=False)
    with pytest.raises(FamilyOSError):  # not a discovery document; the address is what is checked
        await client.discover()
    assert last(mocked)[1] == API + "/"


async def test_the_client_makes_and_closes_its_own_session_when_given_none(mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]))
    route(mocked, "GET", "/lists/tasks", payload=page([task()]))
    client = FamilyOS(BASE, TOKEN)
    await client.list_tasks()
    own = client._session
    assert own is not None and not own.closed
    await client.list_tasks()
    assert client._session is own
    await client.close()
    assert own.closed and client._session is None


async def test_a_given_session_is_left_open(session: aiohttp.ClientSession, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]))
    client = FamilyOS(BASE, TOKEN, session=session)
    await client.list_tasks()
    await client.close()
    assert not session.closed and client._session is session


async def test_context_manager(mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload=page([task()]))
    async with FamilyOS(BASE, TOKEN) as client:
        await client.list_tasks()
        own = client._session
    assert own is not None and own.closed


async def test_repr_names_the_address_only() -> None:
    assert repr(FamilyOS(BASE, TOKEN)) == f"FamilyOS({API!r})"


async def test_an_answer_not_shaped_as_documented(client: FamilyOS, mocked: aioresponses) -> None:
    route(mocked, "GET", "/lists/tasks", payload={"items": [{"id": "task_1"}], "next": None, "timeZone": "UTC"})
    with pytest.raises(FamilyOSError, match="does not understand"):
        await client.list_tasks()
    route(mocked, "GET", "/changes", payload={"changes": "x"})
    with pytest.raises(FamilyOSError):
        await client.changes()
    route(mocked, "GET", "/drafts/d", payload={})
    with pytest.raises(FamilyOSError):
        await client.get_draft("d")
    route(mocked, "POST", "/board/notes", status=201, payload={"status": "applied"})
    with pytest.raises(FamilyOSError):
        await client.create_note("x")
    route(mocked, "POST", "/board/notes", status=202, payload={"status": "drafted"})
    with pytest.raises(FamilyOSError):
        await client.create_note("x")
