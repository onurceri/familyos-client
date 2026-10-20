"""Typed models of the FamilyOS public API (v1).

Each model reads the API's JSON (camelCase) into snake_case attributes. Instants become timezone-aware
``datetime`` objects, days become ``date`` and clock times ``time``. Fields this library does not know are
ignored, so a FamilyOS that adds fields keeps working with an older library. Values that come from a fixed
list in the API (categories, statuses ...) are kept as plain ``str`` for the same reason; the ``StrEnum``
classes below name the values known today and compare equal to the strings.
"""

# No ``from __future__ import annotations``: mashumaro resolves the annotations when a class is created.

import datetime as dt
import inspect
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, TypeVar

from mashumaro import field_options
from mashumaro.mixins.dict import DataClassDictMixin

T = TypeVar("T")

Moment = dt.datetime | dt.date
"""An instant (timezone-aware ``datetime``) or, for an all-day event, a day (``date``)."""


class ErrorCode(StrEnum):
    """The `code` of an API error; the fixed list a program may act on."""

    BAD_REQUEST = "bad_request"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    LIMIT_REACHED = "limit_reached"
    FEATURE_OFF = "feature_off"
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    PRECONDITION_FAILED = "precondition_failed"
    GONE = "gone"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


class Weekday(StrEnum):
    """A day of the week in a weekly recurrence."""

    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"


class ShoppingCategory(StrEnum):
    """The aisle of a shopping item."""

    PRODUCE = "produce"
    BAKERY = "bakery"
    DAIRY = "dairy"
    MEAT = "meat"
    PANTRY = "pantry"
    FROZEN = "frozen"
    DRINKS = "drinks"
    SNACKS = "snacks"
    HOUSEHOLD = "household"
    PERSONAL = "personal"
    BABY = "baby"
    PETS = "pets"
    OTHER = "other"


class Rotation(StrEnum):
    """How a chore passes between the people it is assigned to."""

    FIXED = "fixed"
    ROUND_ROBIN = "round_robin"
    LEAST_DONE = "least_done"


class Paper(StrEnum):
    """The paper a board note is written on."""

    NOTE_1 = "note-1"
    NOTE_2 = "note-2"
    NOTE_3 = "note-3"
    NOTE_4 = "note-4"
    NOTE_5 = "note-5"


class DraftStatus(StrEnum):
    """How far a change that waits for a person has come."""

    WAITING = "waiting"
    APPLIED = "applied"
    REJECTED = "rejected"
    UNDONE = "undone"
    EXPIRED = "expired"
    FAILED = "failed"


class ChangeOp(StrEnum):
    """What happened to a thing in the change log."""

    UPSERT = "upsert"
    DELETE = "delete"


class Mode(StrEnum):
    """Whether an app's changes happen at once (`apply`) or wait for a person (`suggest`)."""

    APPLY = "apply"
    SUGGEST = "suggest"


class Outcome(StrEnum):
    """What calling an operation does for this key."""

    READ = "read"
    APPLY = "apply"
    DRAFT = "draft"


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


# The one name that does not follow camelCase from snake_case.
_SPECIAL_ALIASES = {"family_os": "familyOS"}


def parse_moment(value: str | None) -> Moment | None:
    """Read an RFC 3339 instant or a plain day. Python reads a bare day as midnight, so decide by shape."""
    if value is None:
        return None
    if len(value) == 10:
        return dt.date.fromisoformat(value)
    return dt.datetime.fromisoformat(value)


def _moment(value: str) -> Moment:
    parsed = parse_moment(value)
    assert parsed is not None
    return parsed


def format_moment(value: Moment) -> str:
    """The API's text for an instant or a day."""
    return value.isoformat()


def format_time(value: dt.time) -> str:
    """`HH:MM`, as the API takes clock times."""
    return value.strftime("%H:%M")


class Model(DataClassDictMixin):
    """Base of the response models: camelCase aliases, unknown fields ignored."""

    Config: ClassVar[type[Any]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        names: list[str] = []
        for klass in cls.__mro__:
            names.extend(inspect.get_annotations(klass))
        aliases = {n: _SPECIAL_ALIASES.get(n, _camel(n)) for n in names if "_" in n or n in _SPECIAL_ALIASES}
        cls.Config = type("Config", (), {"aliases": aliases, "serialize_by_alias": True})
        super().__init_subclass__(**kwargs)


@dataclass(frozen=True)
class Recurrence(Model):
    """How something repeats: daily, weekly (on `by_day`) or monthly, every `interval`, until a day."""

    freq: str
    interval: int = 1
    by_day: list[str] | None = None
    until: dt.date | None = None

    def to_api(self) -> dict[str, Any]:
        """The JSON the API takes."""
        body: dict[str, Any] = {"freq": str(self.freq), "interval": self.interval}
        if self.by_day is not None:
            body["byDay"] = [str(day) for day in self.by_day]
        if self.until is not None:
            body["until"] = self.until.isoformat()
        return body


@dataclass(frozen=True)
class Operation(Model):
    """One operation this key may call."""

    name: str
    method: str
    path: str
    summary: str
    outcome: str


@dataclass(frozen=True)
class CallerApp(Model):
    """The connected app a key belongs to."""

    id: str
    name: str
    mode: str


@dataclass(frozen=True)
class Caller(Model):
    """Who is calling: the app, and the member it acts for (if the request named one)."""

    app: CallerApp
    member_id: str | None


@dataclass(frozen=True)
class ActsFor(Model):
    """A member the app may act for: send `member_id` as the on-behalf-of member."""

    member_id: str
    name: str


@dataclass(frozen=True)
class ExtensionInfo(Model):
    """A FamilyOS feature and whether it is switched on."""

    id: str
    version: str
    enabled: bool


@dataclass(frozen=True)
class Discovery(Model):
    """The answer to the first call: what this server is and what this key may do."""

    api: str
    family_os: str
    time_zone: str
    language: str
    caller: Caller
    extensions: list[ExtensionInfo]
    operations: list[Operation]
    acts_for: list[ActsFor]

    def can(self, operation: str) -> bool:
        """Whether this key may call the operation (by its `operationId`, e.g. ``calendar.createEvent``)."""
        return any(op.name == operation for op in self.operations)

    def enabled(self, extension: str) -> bool:
        """Whether the feature (``calendar``, ``lists``, ``chores``, ``board`` ...) is switched on."""
        return any(ext.id == extension and ext.enabled for ext in self.extensions)


@dataclass(frozen=True)
class Event(Model):
    """A calendar event. `start` and `end` are instants, or days for an all-day event."""

    id: str
    version: int
    title: str
    all_day: bool
    start: Moment = field(metadata=field_options(deserialize=_moment))
    end: Moment = field(metadata=field_options(deserialize=_moment))
    date: dt.date
    end_date: dt.date | None
    start_time: dt.time | None
    end_time: dt.time | None
    member_ids: list[str]
    location: str | None
    notes: str | None
    recurrence: Recurrence | None
    rrule: str | None
    reminder: int | None
    tags: list[str]
    series_id: str | None = None
    recurrence_key: str | None = None


@dataclass(frozen=True)
class OccurrenceSource(Model):
    """Where a foreign occurrence comes from (a calendar subscription)."""

    kind: str
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class Occurrence(Model):
    """One date of an event, repeating events expanded."""

    event_id: str
    recurrence_id: Moment = field(metadata=field_options(deserialize=_moment))
    title: str
    all_day: bool
    start: Moment = field(metadata=field_options(deserialize=_moment))
    end: Moment = field(metadata=field_options(deserialize=_moment))
    member_ids: list[str]
    location: str | None
    recurring: bool
    notes: str | None
    source: OccurrenceSource | None
    editable: bool
    tags: list[str]
    series_id: str | None = None
    recurrence_key: str | None = None


@dataclass(frozen=True)
class EventDeleted(Model):
    """The answer to deleting an event, or one date of it (`occurrence`)."""

    id: str
    occurrence: Moment | None = field(metadata=field_options(deserialize=parse_moment))


@dataclass(frozen=True)
class Task(Model):
    """A task."""

    id: str
    version: int
    title: str
    notes: str | None
    assignee_id: str | None
    due_date: dt.date | None
    due_time: dt.time | None
    due: Moment | None = field(metadata=field_options(deserialize=parse_moment))
    recurrence: Recurrence | None
    rrule: str | None
    done: bool
    done_at: dt.datetime | None
    done_by: str | None


@dataclass(frozen=True)
class TaskDeleted(Model):
    """The answer to deleting a task."""

    id: str


@dataclass(frozen=True)
class ShoppingList(Model):
    """A shopping list. The default list has `is_default` set."""

    id: str
    version: int
    name: str
    is_default: bool
    open_count: int


@dataclass(frozen=True)
class ShoppingItem(Model):
    """A line on a shopping list."""

    id: str
    version: int
    list_id: str
    title: str
    quantity: str | None
    category: str
    done: bool
    done_at: dt.datetime | None
    added_by: str | None


@dataclass(frozen=True)
class ShoppingItemDeleted(Model):
    """The answer to deleting a shopping item."""

    id: str


@dataclass(frozen=True)
class ShoppingItemsAdded(Model):
    """The answer to adding items: all of them, and which ones are new."""

    list_id: str
    items: list[ShoppingItem]
    added_ids: list[str]


@dataclass(frozen=True)
class LastDone(Model):
    """The latest completion of a chore that still stands; its `id` takes it back."""

    id: str
    member_id: str
    done_at: dt.datetime


@dataclass(frozen=True)
class Chore(Model):
    """A recurring chore."""

    id: str
    version: int
    title: str
    notes: str | None
    recurrence: Recurrence | None
    start_date: dt.date
    next_due: dt.date | None
    overdue: bool
    rotation: str
    assignee_id: str | None
    assignee_ids: list[str]
    last_done: LastDone | None
    created_at: dt.datetime
    updated_at: dt.datetime


@dataclass(frozen=True)
class ChoreCompletion(Model):
    """One time a chore was done."""

    id: str
    chore_id: str
    member_id: str
    due_date: dt.date
    done_at: dt.datetime
    undone_at: dt.datetime | None


@dataclass(frozen=True)
class ChoreDetail(Chore):
    """A chore with its history."""

    history: list[ChoreCompletion] = field(default_factory=list)


@dataclass(frozen=True)
class ChoreCompleted(Model):
    """The answer to completing a chore; `reason` (``stale``, ``already_done``) says why nothing was made."""

    chore: Chore
    completion: ChoreCompletion
    reason: str | None = None


@dataclass(frozen=True)
class BoardNote(Model):
    """A note on the family board."""

    id: str
    version: int
    text: str
    paper: str
    pinned: bool
    ends_at: dt.datetime | None
    ends_on: dt.date | None
    has_photo: bool
    created_by: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    seen_by: list[str]


@dataclass(frozen=True)
class BoardNoteDeleted(Model):
    """The answer to taking a note off the board. `removed` is false when it was already gone."""

    removed: bool


@dataclass(frozen=True)
class Draft(Model):
    """A change that waited for a person: how it ended."""

    id: str
    status: str
    operation: str | None
    error: str | None
    created_at: dt.datetime
    decided_at: dt.datetime | None
    time_zone: str
    result: Any = None


@dataclass(frozen=True)
class DraftSummary(Model):
    """A suggestion that waits for a person to decide (or that failed and waits to be tried again)."""

    id: str
    status: str
    extension: str
    title: str
    subject: str | None
    proposed_at: dt.datetime
    proposed_by: str | None
    can_decide: bool


@dataclass(frozen=True)
class Change(Model):
    """One thing that changed: read it again (`upsert`) or forget it (`delete`)."""

    seq: int
    at: dt.datetime
    extension: str
    entity: str
    id: str
    op: str


@dataclass(frozen=True)
class Changes(Model):
    """Everything that changed since a cursor. If `more` is true, ask again at once."""

    changes: list[Change]
    cursor: int
    more: bool
    time_zone: str


@dataclass(frozen=True)
class Applied[T]:
    """A change that was made. `etag` is the new version of the thing; `replayed` is true when the server
    answered a repeated request with the first answer."""

    result: T
    time_zone: str
    etag: str | None = None
    replayed: bool = False


@dataclass(frozen=True)
class Drafted:
    """A change that waits for a person to approve it. Follow it with ``get_draft(draft_id)``."""

    draft_id: str
    time_zone: str


@dataclass(frozen=True)
class Page[T]:
    """One page of a list. Pass `next` as ``cursor`` for the following page.

    `not_modified` is true when the server answered ``304`` and these items are the cached ones from the last
    time: nothing changed since.
    """

    items: list[T]
    next: str | None
    time_zone: str
    not_modified: bool = False


# --- What a client sends ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventInput:
    """A whole event, as ``replace_event`` takes it (every field, none left out)."""

    title: str
    date: dt.date
    all_day: bool = False
    end_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    member_ids: list[str] = field(default_factory=list)
    location: str | None = None
    notes: str | None = None
    recurrence: Recurrence | None = None
    reminder: int | None = None
    tags: list[str] | None = None


@dataclass(frozen=True)
class TaskInput:
    """A whole task, as ``replace_task`` takes it (every field, none left out)."""

    title: str
    assignee_id: str | None = None
    due_date: dt.date | None = None
    due_time: dt.time | None = None
    recurrence: Recurrence | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ShoppingItemInput:
    """A line to add to a shopping list."""

    title: str
    quantity: str | None = None
    category: str | None = None


class Unset:
    """The type of ``UNSET``."""

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = Unset()
"""Marks an argument you did not give: ``patch_*`` changes only what you pass, and ``None`` means clear it."""
