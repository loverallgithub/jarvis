"""Reply schemas.

A reply that does not match its schema is **rejected and re-asked**, never
stored. Pimlico's operator prompts were free text, so a half-answer and a real
answer were the same shape and nothing could tell them apart.
"""
from __future__ import annotations

import pytest

from jarvis.console import schemas
from jarvis.console.schemas import SchemaError, validate


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

def test_text_accepts_a_long_enough_answer():
    r = validate({"type": "text", "min_chars": 10}, "  this is long enough  ")
    assert r.ok and r.value == {"text": "this is long enough"}


def test_text_rejects_something_too_short_and_says_the_numbers():
    r = validate({"type": "text", "min_chars": 80}, "ok")
    assert r.ok is False
    assert "2 characters" in r.error and "80" in r.error


@pytest.mark.parametrize("blob", [
    "[Automation failed: Page.goto: Timeout 30000ms exceeded]",
    "Traceback (most recent call last):\n  File x\nValueError",
    "403 Forbidden — Cloudflare",
])
def test_text_rejects_a_pasted_error_message(blob):
    """🔴 The LinkedIn incident arriving by a different route.

    Pimlico published `"[Automation failed: Page.goto: Timeout 30000ms
    exceeded...]"` to a live LinkedIn account on six consecutive days. Sintra is
    now a HUMAN connector — so the same string can now arrive by an operator
    pasting whatever the UI showed them. The gate has to exist on this path too.
    """
    r = validate({"type": "text", "min_chars": 10}, blob + " " * 100)
    assert r.ok is False
    assert "error message, not output" in r.error


# ---------------------------------------------------------------------------
# choice
# ---------------------------------------------------------------------------

def test_choice_accepts_the_word_the_number_and_a_prefix():
    s = {"type": "choice", "options": ["approve", "reject"]}
    assert validate(s, "approve").value == {"choice": "approve"}
    assert validate(s, "REJECT").value == {"choice": "reject"}
    assert validate(s, "2").value == {"choice": "reject"}
    assert validate(s, "app").value == {"choice": "approve"}


def test_choice_refuses_an_ambiguous_prefix_rather_than_guessing():
    """This is an approval gate. A wrong guess spends money or publishes
    something outward-facing."""
    s = {"type": "choice", "options": ["approve", "approve_with_changes"]}
    r = validate(s, "app")
    assert r.ok is False
    assert "ambiguous" in r.error
    assert "approve" in r.error and "approve_with_changes" in r.error


def test_choice_lists_the_options_when_nothing_matches():
    r = validate({"type": "choice", "options": ["approve", "reject"]}, "maybe")
    assert r.ok is False
    assert "1. approve" in r.error and "2. reject" in r.error


# ---------------------------------------------------------------------------
# fields
# ---------------------------------------------------------------------------

def test_fields_parses_colon_and_equals_forms():
    s = {"type": "fields", "required": {"store_id": "str", "count": "int"}}
    r = validate(s, "store_id: ABC123\ncount = 7")
    assert r.ok and r.value == {"store_id": "ABC123", "count": 7}


def test_fields_reports_what_is_missing_with_an_example():
    s = {"type": "fields", "required": {"store_id": "str", "domain": "str"}}
    r = validate(s, "store_id: ABC123")
    assert r.ok is False
    assert "missing: domain" in r.error
    assert "store_id: <str>" in r.error


def test_fields_reports_a_bad_type_by_name():
    s = {"type": "fields", "required": {"count": "int"}}
    r = validate(s, "count: many")
    assert r.ok is False
    assert "`count` should be a int" in r.error


def test_fields_ignores_surrounding_prose():
    """Operators reply from a phone and add context. Extract, don't demand."""
    s = {"type": "fields", "required": {"store_id": "str"}}
    r = validate(s, "here you go, took a while\nstore_id: XYZ\nhope that helps")
    assert r.ok and r.value == {"store_id": "XYZ"}


# ---------------------------------------------------------------------------
# SKIP
# ---------------------------------------------------------------------------

def test_skip_is_an_explicit_recorded_decision():
    r = validate({"type": "text", "min_chars": 500}, "SKIP sintra is down today")
    assert r.ok is True
    assert r.skipped is True
    assert r.skip_reason == "sintra is down today"


def test_skip_bypasses_the_schema_entirely():
    """The whole point: you can release a block you cannot satisfy."""
    r = validate({"type": "choice", "options": ["approve", "reject"]},
                 "SKIP need to check with legal first")
    assert r.ok and r.skipped


def test_a_bare_skip_is_refused_because_the_reason_is_the_record():
    r = validate({"type": "text", "min_chars": 10}, "SKIP")
    assert r.ok is False
    assert "needs a reason" in r.error


def test_skip_separators_are_tolerated():
    for text in ("SKIP: no access", "SKIP - no access", "skip no access"):
        r = validate({"type": "text", "min_chars": 10}, text)
        assert r.skipped and r.skip_reason == "no access"


# ---------------------------------------------------------------------------
# schema errors are OUR bugs, not the operator's
# ---------------------------------------------------------------------------

def test_a_malformed_schema_raises_rather_than_rejecting_the_operator():
    for bad in ({}, {"type": "nonsense"}, {"type": "choice", "options": []},
                {"type": "fields", "required": {}}):
        with pytest.raises(SchemaError):
            validate(bad, "anything")


def test_describe_tells_the_operator_what_to_send():
    assert "80 characters" in schemas.describe({"type": "text", "min_chars": 80})
    d = schemas.describe({"type": "choice", "options": ["approve", "reject"]})
    assert "approve" in d and "reject" in d
    assert "store_id" in schemas.describe(
        {"type": "fields", "required": {"store_id": "str"}})
