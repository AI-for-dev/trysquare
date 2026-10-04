# SPDX-License-Identifier: BSD-3-Clause
"""Where an agent runs, and what it can see from there.

Every run lives under one work directory and the agent runs as the operator, so
without a boundary an agent can read its neighbours' clones, sessions and traces, the
pinned sources and the output directory. The boundary has to come from the operating
system: an agent told not to look is an agent that was asked.

A backend is **one execution**, not a command prefix: it receives the argv and the
scope of one run, and returns what the child did. That is what lets a backend which is
not a command at all - a microVM - stand behind the same interface.

The scope is given as host paths, and a backend that confines exposes each one at the
same path inside. Nothing in the argv then needs rewriting, and nothing outside the
scope exists for the agent to find.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import interrupt

NONE = "none"


@dataclass(frozen=True)
class Scope:
    """What one run may touch: its own clone and session, and the bricks it loads."""

    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()


class Confinement(Protocol):
    name: str

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        """`interrupt.run`, inside the boundary. `kwargs` are `interrupt.run`'s."""
        ...


class Unconfined:
    """No boundary: the agent sees whatever the operator sees.

    Only ever chosen, never fallen back to, and every run it measures says so.
    """

    name = NONE

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        return interrupt.run(argv, **kwargs)


BACKENDS = {NONE: Unconfined}


def backend(settings: dict) -> Confinement:
    """The backend the config's `[isolation]` section describes.

    Raises `ValueError` naming what is wrong, so `config.load` can refuse the file before
    anything runs. Building one checks the settings only: whether the backend can run on
    this machine is asked when a launch starts.
    """
    settings = dict(settings)
    name = settings.pop("backend", NONE)
    if name not in BACKENDS:
        raise ValueError(f"backend = {name!r} is not one (known: {', '.join(BACKENDS)})")
    try:
        return BACKENDS[name](**settings)
    except TypeError:
        raise ValueError(f"the {name!r} backend takes no {', '.join(sorted(settings))}") from None
