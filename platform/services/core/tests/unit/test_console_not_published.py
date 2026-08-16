"""The console must never publish a port. This is a security regression test.

WHAT HAPPENED (2026-08-08)
──────────────────────────
The operator dashboard at /ui has no authentication. It was published with
`ports: ["127.0.0.1:8905:8905"]` in the belief that this bound loopback only.

It does not. Swarm publishes in INGRESS mode and ingress DISCARDS the host-IP
prefix, so the line became `PublishMode: ingress` and the host showed
`LISTEN *:8905` — every interface. It was not merely bound but genuinely
reachable, because /etc/pimlico-firewall.sh only applies its public-DROP policy
to ports listed in SWARM_PORTS, and 8905 was not among them.

An unauthenticated dashboard was on a public interface for about three minutes.

WHY A TEST AND NOT A COMMENT
────────────────────────────
The comment explaining the danger was already there — it was written by the same
person who then added the port. A comment cannot fail a build. This can.

`mode: host` is rejected too: it binds 0.0.0.0 on the node, so it is not a fix.
"""
from __future__ import annotations

import os
import re

import pytest

# The stack file lives OUTSIDE the image build context, so it is not in the
# image. These tests therefore skip in a bare deployed-image run — which is why
# the AUTHORITATIVE gate is the preflight in deploy.sh, which always has the
# file and refuses to build without it. These tests are the fast local echo of
# that gate; do not treat them as the control.
_CANDIDATES = (
    os.environ.get("JPD_STACK_FILE", ""),
    os.path.join(os.environ.get("JPD_PACKAGE_ROOT", "/app"),
                 "docker", "docker-stack.swarm.yml"),
    os.path.join(os.environ.get("JPD_PACKAGE_ROOT", "/app"),
                 "docker-stack.swarm.yml"),
    os.path.abspath(os.path.join(os.path.dirname(__file__),
                                 "../../../../docker/docker-stack.swarm.yml")),
    "/opt/jarvis/platform/docker/docker-stack.swarm.yml",
)


def _stack_text() -> str:
    for path in _CANDIDATES:
        if path and os.path.isfile(path):
            return open(path).read()
    pytest.skip("stack file not in this context — deploy.sh preflight is the gate")


def _uncommented(text: str) -> str:
    """Lines with comments stripped.

    Needed because the stack file *documents* the forbidden mapping in prose
    ("`127.0.0.1:8905:8905` DOES NOT WORK HERE"). Scanning raw text would flag
    the warning that exists to prevent the thing — punishing the documentation
    and training the next person to delete it.
    """
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())


def _console_block(text: str) -> str:
    """The console service block.

    Stops at the next service key (2-space) OR any top-level key (0-space).
    `console` is currently the LAST service, so without the 0-space arm the
    match ran to end-of-file and swept in `volumes:` and every trailing
    comment — which is what made an earlier version of this test fail against a
    perfectly correct file.
    """
    m = re.search(r"^  console:\n(.*?)(?=^  [a-z0-9_-]+:|^[a-z0-9_-]+:|\Z)",
                  text, re.S | re.M)
    assert m, "console service block not found in the stack file"
    return m.group(1)


def test_the_console_service_publishes_no_ports():
    block = _uncommented(_console_block(_stack_text()))
    offending = [ln for ln in block.splitlines()
                 if re.match(r"\s*ports:\s*$", ln)]
    assert not offending, (
        "console must not publish a port — /ui has NO AUTH and swarm ingress "
        "ignores a 127.0.0.1 prefix, so any ports: block here is public. "
        "Use `jpd ui --out` or `ssh <host> 'jpd ui'` instead.")


def test_no_8905_mapping_anywhere_in_the_stack():
    """Catches the mapping even if it is added under a different key."""
    text = _uncommented(_stack_text())
    for m in re.finditer(r"[\d.]*:?8905:8905", text):
        line = text[: m.start()].count("\n") + 1
        pytest.fail(f"line {line}: a published 8905 mapping is present. "
                    f"Swarm ingress makes this 0.0.0.0 regardless of any host "
                    f"IP prefix — see the comment above the console service.")


def test_core_is_still_published_so_the_test_is_not_vacuous():
    """If the stack file stopped being readable or the regex stopped matching,
    the two tests above would pass while checking nothing. Core DOES publish
    8900, so finding it proves the parse is live."""
    assert "8900" in _stack_text()


def test_the_danger_is_documented_where_the_change_would_be_made():
    """The warning sits ABOVE `console:` (YAML comments precede their key), so
    this checks the file, not the captured block."""
    text = _stack_text()
    assert "NOT PUBLISHED" in text.upper()
    assert "ingress" in text.lower(), (
        "the reason a 127.0.0.1 prefix does not work must stay next to the "
        "service, or the next person re-adds it")
