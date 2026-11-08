"""JSON-schema generation, response parsing and the structured-output repair ladder."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shahrzad.core.errors import SchemaValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_SENTINEL = re.compile(r"⟨/?\d+⟩")
_WS = re.compile(r"\s+")
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _resolve_refs(node: Any, defs: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name in stack:
                raise SchemaValidationError(f"recursive schema reference: {name}")
            return _resolve_refs(copy.deepcopy(defs[name]), defs, (*stack, name))
        return {key: _resolve_refs(value, defs, stack) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, defs, stack) for item in node]
    return node


def build_response_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Inline $defs and drop titles: some providers reject refs and oneOf."""
    raw = model_cls.model_json_schema()
    defs = raw.pop("$defs", {})
    schema = _resolve_refs(raw, defs)
    schema.pop("title", None)
    return schema


def schema_hash(schema: dict[str, Any] | type[BaseModel]) -> str:
    payload = schema if isinstance(schema, dict) else build_response_schema(schema)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text.strip()).strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """Take the first balanced JSON object; tolerate a prose preamble once."""
    cleaned = _strip_fences(text)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    start = cleaned.find("{")
    if start < 0:
        raise SchemaValidationError("response contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise SchemaValidationError(f"invalid JSON object: {exc}") from exc
                if not isinstance(value, dict):
                    raise SchemaValidationError("top-level JSON value is not an object")
                return value
    raise SchemaValidationError("unterminated JSON object")


def parse_model(model_cls: type[ModelT], text: str) -> ModelT:
    payload = extract_json_object(text)
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise SchemaValidationError(f"{model_cls.__name__} validation failed:\n{exc}") from exc


def check_ids(requested: list[str], returned: list[str]) -> None:
    if len(returned) != len(set(returned)):
        raise SchemaValidationError("response contains duplicate segment ids")
    if set(returned) != set(requested):
        missing = sorted(set(requested) - set(returned))
        extra = sorted(set(returned) - set(requested))
        raise SchemaValidationError(f"segment ids mismatch; missing={missing} extra={extra}")


def canonical_quote(text: str) -> str:
    """Canonicalize quote evidence before substring matching (SCHEMAS.md section 2)."""
    stripped = _SENTINEL.sub("", text)
    folded = unicodedata.normalize("NFKC", stripped).translate(_PERSIAN_DIGITS)
    return _WS.sub(" ", folded).strip()
