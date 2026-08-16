"""`jpd <cmd> > file` must produce the artifact, not the artifact plus logs.

Nothing ever called `structlog.configure()`, so structlog used its default
PrintLoggerFactory — which writes to STDOUT. Every `jpd` invocation therefore
interleaved log lines with real command output.

It went unnoticed until `jpd ui > dashboard.html` produced a file beginning
`2026-08-08 18:14:10 [info] discovery.steps_registered` instead of `<!doctype
html>`. The registry logs at IMPORT time — before any command body runs — so no
care inside the command could have avoided it. It had to be fixed at the stream.

Logs are diagnostics (stderr); stdout is the artifact.
"""
from __future__ import annotations

import sys

import pytest
import structlog

from jarvis.cli import _logs_to_stderr


@pytest.fixture(autouse=True)
def _restore_structlog():
    """`_logs_to_stderr` mutates GLOBAL structlog config, so leaving it set
    would leak into every later test in the session."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def test_log_output_does_not_reach_stdout(capsys):
    _logs_to_stderr()
    structlog.get_logger("t").info("some.event", detail="x")
    cap = capsys.readouterr()
    assert "some.event" not in cap.out, "a log line landed on stdout"
    assert "some.event" in cap.err


def test_stdout_stays_usable_for_real_output(capsys):
    """The other half: moving logs must not move the artifact too."""
    _logs_to_stderr()
    print("<!doctype html>")
    structlog.get_logger("t").info("noise.event")
    cap = capsys.readouterr()
    assert cap.out.startswith("<!doctype html>")
    assert "noise.event" not in cap.out


def test_the_factory_is_bound_to_stderr():
    _logs_to_stderr()
    factory = structlog.get_config()["logger_factory"]
    assert getattr(factory, "_file", None) is sys.stderr
