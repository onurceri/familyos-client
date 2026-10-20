"""The library against FamilyOS's own description of the API (`familyos/openapi/v1.json`).

When FamilyOS adds an operation or a field, update the document and these tests say what the library lacks.
"""

import dataclasses
import inspect
import json
import re
from enum import StrEnum
from importlib import resources
from typing import Any

import pytest

import familyos
from familyos import models
from familyos.client import FamilyOS

SPEC: dict[str, Any] = json.loads(resources.files("familyos").joinpath("openapi/v1.json").read_text("utf-8"))
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]

# Operations whose method is not named after the operation.
RENAMED_OPERATIONS = {"core.listChanges": "changes"}
# Parameters whose Python name is not the snake_case of the API's.
RENAMED_PARAMETERS = {"from": "from_date", "to": "to_date"}
# Parameters the client sets itself.
AUTOMATIC_PARAMETERS = {"if-none-match"}
# Methods that are not operations.
HELPERS = {"close", "clear_cache"}


def snake(name: str) -> str:
    if name == "familyOS":
        return "family_os"
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def operations() -> list[tuple[str, str, str, dict[str, Any]]]:
    return [
        (op["operationId"], method, path, op)
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def method_name(operation_id: str) -> str:
    return RENAMED_OPERATIONS.get(operation_id) or snake(operation_id.split(".", 1)[1])


def resolve(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return resolve(SCHEMAS[schema["$ref"].rsplit("/", 1)[1]])
    return schema


def properties(schema: dict[str, Any]) -> dict[str, Any]:
    """The properties of an object schema, merging the alternatives of a union."""
    schema = resolve(schema)
    found: dict[str, Any] = dict(schema.get("properties", {}))
    for alternative in schema.get("oneOf", []) + schema.get("anyOf", []):
        found.update(properties(alternative))
    return found


def field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_the_document_is_the_public_api_v1() -> None:
    assert SPEC["info"]["version"] == "v1"
    assert SPEC["servers"][0]["url"] == "/api/public/v1"


@pytest.mark.parametrize(
    ("operation_id", "http", "path", "op"), operations(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_operation_has_a_method(operation_id: str, http: str, path: str, op: dict[str, Any]) -> None:
    name = method_name(operation_id)
    method = getattr(FamilyOS, name, None)
    assert method is not None, f"{operation_id} has no method {name}"
    assert inspect.iscoroutinefunction(method)
    signature = inspect.signature(method)

    for parameter in op.get("parameters", []):
        if parameter["name"].lower() in AUTOMATIC_PARAMETERS:
            continue
        python = RENAMED_PARAMETERS.get(parameter["name"]) or snake(parameter["name"].removeprefix("x-familyos-"))
        python = python.replace("-", "_")
        assert python in signature.parameters, f"{name} lacks {python} ({parameter['in']} {parameter['name']})"
        if parameter.get("required"):
            assert signature.parameters[python].default is inspect.Parameter.empty or parameter["in"] == "path"

    body = op.get("requestBody")
    if body:
        schema = resolve(body["content"]["application/json"]["schema"])
        for prop, sub in schema["properties"].items():
            assert snake(prop) in signature.parameters, f"{name} lacks {snake(prop)}"
            # A whole thing in one property (replace) is a model of its own.
            if prop in {"event", "task"}:
                model = {"event": models.EventInput, "task": models.TaskInput}[prop]
                assert {snake(p) for p in properties(sub)} == field_names(model)
            if prop == "items" and sub.get("type") == "array":
                inner = {snake(p) for p in properties(sub["items"])}
                assert inner == field_names(models.ShoppingItemInput)


def test_every_list_has_an_iterator() -> None:
    for operation_id, _, _, op in operations():
        ok = op["responses"].get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
        if resolve(ok).get("properties", {}).get("next") is None:
            continue
        name = method_name(operation_id)
        assert name.startswith("list_")
        iterator = getattr(FamilyOS, "iter_" + name.removeprefix("list_"), None)
        assert iterator is not None, f"{name} has no iter_ twin"


def test_the_client_has_no_method_the_document_lacks() -> None:
    known = {method_name(op_id) for op_id, *_ in operations()}
    known |= {"iter_" + n.removeprefix("list_") for n in known if n.startswith("list_")}
    public = {
        name
        for name, member in inspect.getmembers(FamilyOS)
        if not name.startswith("_") and (inspect.isfunction(member))
    }
    assert public - known - HELPERS == set()


# schema name -> model; nested objects are listed with the path to them.
MODELS: dict[str, type] = {
    "Discovery": models.Discovery,
    "Event": models.Event,
    "Occurrence": models.Occurrence,
    "Task": models.Task,
    "ShoppingList": models.ShoppingList,
    "ShoppingItem": models.ShoppingItem,
    "Chore": models.Chore,
    "ChoreDetail": models.ChoreDetail,
    "BoardNote": models.BoardNote,
    "Draft": models.Draft,
    "Changes": models.Changes,
    "EventDeleted": models.EventDeleted,
    "TaskDeleted": models.TaskDeleted,
    "ShoppingItemDeleted": models.ShoppingItemDeleted,
    "ShoppingItemsAdded": models.ShoppingItemsAdded,
    "ChoreCompleted": models.ChoreCompleted,
}


@pytest.mark.parametrize("schema_name", sorted(MODELS))
def test_a_model_has_the_fields_of_its_schema(schema_name: str) -> None:
    cls = MODELS[schema_name]
    schema = SCHEMAS[schema_name]
    assert {snake(p) for p in schema["properties"]} == field_names(cls)
    required = {snake(p) for p in schema.get("required", [])}
    must_give = {
        f.name
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    # What the API always sends the library requires; what it may leave out the library defaults.
    # (ChoreDetail.history has a default so one model can also read a chore in a list.)
    assert required - must_give <= ({"history"} if schema_name == "ChoreDetail" else set())
    assert must_give <= required


NESTED = {
    "Event.recurrence": models.Recurrence,
    "Task.recurrence": models.Recurrence,
    "Chore.recurrence": models.Recurrence,
    "Occurrence.source": models.OccurrenceSource,
    "Chore.lastDone": models.LastDone,
    "ChoreDetail.history": models.ChoreCompletion,
    "ChoreCompleted.completion": models.ChoreCompletion,
    "Changes.changes": models.Change,
    "Discovery.caller": models.Caller,
    "Discovery.extensions": models.ExtensionInfo,
    "Discovery.operations": models.Operation,
}


@pytest.mark.parametrize("path", sorted(NESTED))
def test_a_nested_model_has_the_fields_of_its_schema(path: str) -> None:
    schema_name, prop = path.split(".")
    schema = SCHEMAS[schema_name]["properties"][prop]
    if schema.get("type") == "array":
        schema = schema["items"]
    found = {snake(p) for p in properties(schema)}
    cls = NESTED[path]
    # One completion model reads both the entry of a history (with `undoneAt`) and the one just made.
    assert found <= field_names(cls) <= found | {"undone_at"}
    assert field_names(cls) - found <= ({"undone_at"} if cls is models.ChoreCompletion else set())


def test_the_app_inside_the_caller_matches() -> None:
    app = SCHEMAS["Discovery"]["properties"]["caller"]["properties"]["app"]
    assert set(app["properties"]) == field_names(models.CallerApp)


def values(schema: dict[str, Any]) -> set[str]:
    return set(schema["enum"])


def test_enums_hold_the_documented_values() -> None:
    event = SCHEMAS["Event"]["properties"]["recurrence"]["oneOf"][1]["properties"]
    discovery = SCHEMAS["Discovery"]["properties"]
    pairs: list[tuple[type[StrEnum], set[str]]] = [
        (models.ErrorCode, values(SCHEMAS["ErrorCode"])),
        (models.Weekday, values(event["byDay"]["items"])),
        (models.ShoppingCategory, values(SCHEMAS["ShoppingItem"]["properties"]["category"])),
        (models.Rotation, values(SCHEMAS["Chore"]["properties"]["rotation"])),
        (models.Paper, values(SCHEMAS["BoardNote"]["properties"]["paper"])),
        (models.DraftStatus, values(SCHEMAS["Draft"]["properties"]["status"])),
        (models.ChangeOp, values(SCHEMAS["Changes"]["properties"]["changes"]["items"]["properties"]["op"])),
        (models.Mode, values(discovery["caller"]["properties"]["app"]["properties"]["mode"])),
        (models.Outcome, values(discovery["operations"]["items"]["properties"]["outcome"])),
    ]
    for enum, documented in pairs:
        assert {member.value for member in enum} == documented, enum.__name__


def example(schema: dict[str, Any], name: str = "") -> Any:
    """A value that satisfies the schema."""
    schema = resolve(schema)
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return example(next(a for a in schema[key] if a.get("type") != "null"), name)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(k for k in kind if k != "null")
    if kind == "object":
        return {k: example(v, k) for k, v in schema["properties"].items()}
    if kind == "array":
        return [example(schema["items"], name)]
    if kind == "integer":
        return 1
    if kind == "boolean":
        return True
    if schema.get("format") == "date-time" or name.endswith("At") or name == "at":
        return "2026-10-21T09:00:00+03:00"
    if ("pattern" in schema and "\\d{4}" in schema["pattern"]) or name in {"until", "date", "endDate"}:
        return "2026-10-21"
    if name.endswith("Time"):
        return "09:00"
    return "x"


@pytest.mark.parametrize("schema_name", sorted(MODELS))
def test_a_model_reads_an_answer_built_from_its_schema(schema_name: str) -> None:
    answer = example(SCHEMAS[schema_name])
    cls: Any = MODELS[schema_name]
    assert cls.from_dict(answer) is not None


def test_the_package_exports_every_model_and_error() -> None:
    exported = set(familyos.__all__)
    for cls in (*MODELS.values(), *NESTED.values()):
        assert cls.__name__ in exported
