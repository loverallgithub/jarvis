"""The render guard.

`jpd checkpoint render` writes over CHECKPOINT.md. If the target has content
but no preservation marker, everything in it is destroyed — and institutional
memory is the one artifact in this system with no backup and no way to
regenerate it. Pimlico's equivalent file is 3,897 lines of hard-won context.

These tests are pure-unit on purpose: they must not need a database, because
the failure they prevent is a data-loss bug in a command an operator runs at
the exact moment things are going badly.
"""
from __future__ import annotations

import pytest

from jarvis.runtime import checkpoints


class _Args:
    def __init__(self, output):
        self.output = str(output)


async def test_render_refuses_to_clobber_an_unmarked_file(tmp_path, capsys, monkeypatch):
    from jarvis.cli import cmd_checkpoint_render

    doc = tmp_path / "CHECKPOINT.md"
    precious = "# Hand-written\n\nThree weeks of reasoning that cannot be regenerated.\n"
    doc.write_text(precious)

    rc = await cmd_checkpoint_render(_Args(doc))

    assert rc == 1
    assert doc.read_text() == precious, "the file was modified despite the refusal"
    out = capsys.readouterr().out
    assert "refusing to overwrite" in out
    assert checkpoints.WHY_MARKER in out, "the fix must be shown, not just the refusal"


async def test_render_writes_freely_to_a_new_file(tmp_path, clean_db):
    from jarvis.cli import cmd_checkpoint_render

    doc = tmp_path / "new" / "CHECKPOINT.md"
    assert await cmd_checkpoint_render(_Args(doc)) == 0
    assert doc.exists()
    assert checkpoints.WHY_MARKER in doc.read_text()


async def test_render_preserves_a_marked_hand_written_section(tmp_path, clean_db):
    from jarvis.cli import cmd_checkpoint_render

    doc = tmp_path / "CHECKPOINT.md"
    doc.write_text(
        "# old generated header that should be replaced\n"
        f"{checkpoints.WHY_MARKER}\n"
        "## Why\n\nCommerce is built first because Pimlico built it last.\n")

    assert await cmd_checkpoint_render(_Args(doc)) == 0

    after = doc.read_text()
    assert "Commerce is built first because Pimlico built it last." in after
    assert "old generated header" not in after
    assert after.count(checkpoints.WHY_MARKER) == 1


async def test_an_empty_file_is_not_treated_as_precious(tmp_path, clean_db):
    """A zero-byte or whitespace-only file has nothing to lose."""
    from jarvis.cli import cmd_checkpoint_render

    doc = tmp_path / "CHECKPOINT.md"
    doc.write_text("   \n\n")
    assert await cmd_checkpoint_render(_Args(doc)) == 0
    assert checkpoints.WHY_MARKER in doc.read_text()
