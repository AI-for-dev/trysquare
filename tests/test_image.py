# SPDX-License-Identifier: BSD-3-Clause
"""The agent image stays pinned.

A tag, a version range or an unchecked download would let the image rebuild into other
tools, and a matrix measured with other tools is another experiment under the same name.
Building it needs docker and the network, so this reads the Dockerfile instead.
"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "image" / "Dockerfile"
TEXT = DOCKERFILE.read_text()


def arg(name: str) -> str:
    return re.search(rf"^ARG {name}=(\S+)$", TEXT, re.MULTILINE)[1]


def test_the_base_is_pinned_by_digest():
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", TEXT, re.MULTILINE)


def test_node_and_pi_are_exact_versions():
    for name in ("NODE_VERSION", "PI_VERSION"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", arg(name)), name


def test_node_is_checked_against_its_published_checksum():
    assert "SHASUMS256.txt" in TEXT and "sha256sum -c" in TEXT


def test_a_run_calls_nothing_but_its_provider():
    assert "PI_SKIP_VERSION_CHECK=1" in TEXT and "PI_TELEMETRY=0" in TEXT
