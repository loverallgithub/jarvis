"""Reply schemas — the difference between a parsed answer and a pasted blob.

Every human task declares what a valid reply looks like. A reply that does not
match is **rejected and re-asked**, not stored. Pimlico's equivalent was a free
text field that anything could land in, so a half-answer and a real answer were
the same shape and nothing could tell them apart.

Three schema kinds cover every task the design calls for:

    {"type": "text",   "min_chars": 80}                  free text (Sintra output)
    {"type": "choice", "options": ["approve", "reject"]}  decision cards
    {"type": "fields", "required": {"store_id": "str"}}   structured setup replies

Deliberately not JSON Schema. The full spec brings a dependency and a surface
area far beyond three shapes, and its error messages are written for developers.
The operator reading a rejection on their phone needs a sentence, not a
`$.properties.foo.minLength` path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

SKIP_PREFIX = "SKIP"


class SchemaError(ValueError):
    """The schema itself is malformed — a programming error, not an operator one."""


@dataclass(frozen=True)
class ParsedReply:
    ok: bool
    value: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None


def _detect_skip(text: str) -> Optional[str]:
    """`SKIP <reason>` is an explicit, RECORDED operator decision.

    It is not a failure and not a timeout: the operator looked at the task and
    chose to release the block. The reason is mandatory — "SKIP" alone tells a
    future reader nothing about why a step was abandoned.
    """
    stripped = text.strip()
    if not stripped.upper().startswith(SKIP_PREFIX):
        return None
    reason = stripped[len(SKIP_PREFIX):].strip(" :–-\t")
    return reason or ""


def validate(schema: dict[str, Any], text: str) -> ParsedReply:
    """Parse an operator's reply against the task's declared schema."""
    if not isinstance(schema, dict) or not schema:
        raise SchemaError("reply_schema is empty — a task must declare what a valid reply is")

    skip = _detect_skip(text)
    if skip is not None:
        if not skip:
            return ParsedReply(
                ok=False,
                error="SKIP needs a reason — reply `SKIP <why>` so the record says "
                      "why this step was abandoned.")
        return ParsedReply(ok=True, skipped=True, skip_reason=skip[:500],
                           value={"skipped": True, "reason": skip[:500]})

    kind = schema.get("type")
    if kind == "text":
        return _text(schema, text)
    if kind == "choice":
        return _choice(schema, text)
    if kind == "fields":
        return _fields(schema, text)
    raise SchemaError(f"unknown reply_schema type {kind!r}")


def _text(schema: dict, text: str) -> ParsedReply:
    body = text.strip()
    min_chars = int(schema.get("min_chars", 1))

    # The same failure markers the browser-agent and publish gates use. If a
    # human pastes an error page from a UI, that must not become the artifact.
    # This is the LinkedIn incident's shape arriving by a different route.
    lowered = body.lower()
    for marker in ("[automation failed", "traceback (most recent call last)",
                   "page.goto:", "timeout 30000ms", "call log:",
                   "403 forbidden", "502 bad gateway"):
        if marker in lowered:
            return ParsedReply(
                ok=False,
                error=f"that looks like an error message, not output "
                      f"(found {marker!r}). Paste the real result, or reply "
                      f"`SKIP <reason>`.")

    if len(body) < min_chars:
        return ParsedReply(
            ok=False,
            error=f"too short — {len(body)} characters, need at least {min_chars}. "
                  f"Paste the full output, or reply `SKIP <reason>`.")
    return ParsedReply(ok=True, value={"text": body})


def _choice(schema: dict, text: str) -> ParsedReply:
    options = [str(o) for o in (schema.get("options") or [])]
    if not options:
        raise SchemaError("choice schema has no options")

    answer = text.strip().lower()
    # Accept the option itself, or its 1-based index — a phone reply of "1" is
    # far easier than typing "approve_with_changes".
    for i, opt in enumerate(options, start=1):
        if answer == opt.lower() or answer == str(i):
            return ParsedReply(ok=True, value={"choice": opt})

    # Accept an unambiguous prefix. Ambiguity is refused rather than guessed —
    # this is an approval gate, and a wrong guess spends money.
    matches = [o for o in options if o.lower().startswith(answer)] if answer else []
    if len(matches) == 1:
        return ParsedReply(ok=True, value={"choice": matches[0]})
    if len(matches) > 1:
        return ParsedReply(
            ok=False,
            error=f"{answer!r} is ambiguous between {', '.join(matches)}. "
                  f"Reply with the full word or its number.")

    numbered = ", ".join(f"{i}. {o}" for i, o in enumerate(options, start=1))
    return ParsedReply(ok=False,
                       error=f"reply with one of: {numbered} (or `SKIP <reason>`)")


_FIELD_RE = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*[:=]\s*(.+?)\s*$")

_COERCE = {
    "str": lambda v: v,
    "int": int,
    "float": float,
    "bool": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
}


def _fields(schema: dict, text: str) -> ParsedReply:
    required: dict[str, str] = schema.get("required") or {}
    if not required:
        raise SchemaError("fields schema has no required fields")

    found: dict[str, Any] = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        if key in required:
            coerce = _COERCE.get(required[key])
            if coerce is None:
                raise SchemaError(f"unknown field type {required[key]!r} for {key!r}")
            try:
                found[key] = coerce(raw)
            except (TypeError, ValueError):
                return ParsedReply(
                    ok=False,
                    error=f"`{key}` should be a {required[key]}, got {raw!r}")

    missing = [k for k in required if k not in found]
    if missing:
        example = "\n".join(f"{k}: <{required[k]}>" for k in required)
        return ParsedReply(
            ok=False,
            error=f"missing: {', '.join(missing)}. Reply in this shape:\n{example}")
    return ParsedReply(ok=True, value=found)


def describe(schema: dict[str, Any]) -> str:
    """One line telling the operator what to send. Rendered onto every card."""
    kind = schema.get("type")
    if kind == "text":
        return (f"Reply to THIS message with the full output "
                f"(min {schema.get('min_chars', 1)} characters).")
    if kind == "choice":
        opts = schema.get("options") or []
        return "Reply with " + " / ".join(f"<b>{o}</b>" for o in opts) + "."
    if kind == "fields":
        req = schema.get("required") or {}
        lines = "\n".join(f"{k}: &lt;{v}&gt;" for k, v in req.items())
        return f"Reply with these fields, one per line:\n<pre>{lines}</pre>"
    return "Reply to this message."
