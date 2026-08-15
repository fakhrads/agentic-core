"""Playbook files — MEMORY.md / USER.md / SELF.md.

The playbook is the agent's durable, human-readable operating context. Changes
go through `revise` (NOTIFY tier) so every edit has a rationale, a diff, and a
rollback path.
"""

from __future__ import annotations

import difflib
from pathlib import Path

MEMORY_FILE = "MEMORY.md"
USER_FILE = "USER.md"
SELF_FILE = "SELF.md"
FILES = (MEMORY_FILE, USER_FILE, SELF_FILE)

_HEADERS = {
    MEMORY_FILE: "# MEMORY\n\nDurable facts and operating knowledge.\n",
    USER_FILE: "# USER\n\nWhat the agent knows about its operator.\n",
    SELF_FILE: "# SELF\n\nThe agent's model of itself: role, limits, style.\n",
}


class PlaybookError(Exception):
    pass


class PlaybookStore:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def path(self, file: str) -> Path:
        self.validate(file)
        return self.dir / file

    @staticmethod
    def validate(file: str) -> None:
        if file not in FILES:
            raise PlaybookError(f"unknown playbook file: {file} (allowed: {FILES})")

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for file in FILES:
            p = self.dir / file
            if not p.exists():
                p.write_text(_HEADERS[file], encoding="utf-8")

    def read(self, file: str) -> str:
        p = self.path(file)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write(self, file: str, content: str) -> None:
        p = self.path(file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def is_untouched(file: str, content: str) -> bool:
    """True when the file still holds only its scaffold header.

    Lets callers skip files the agent has never written to, instead of feeding
    three empty section headers into every prompt.
    """
    PlaybookStore.validate(file)
    return content.strip() == _HEADERS[file].strip()


def unified_diff(old: str, new: str, file: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{file}",
        tofile=f"b/{file}",
    )
    return "".join(lines)
