"""Minimal `.env` reader/writer for `agent setup` and `agent model`.

Preserves comments and unrelated keys — only touches the keys being written.
No third-party dependency; the format is deliberately simple (`KEY=VALUE`
lines, `#` comments), matching what pydantic-settings' `env_file` loader reads.
"""

from __future__ import annotations

from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into `path`, preserving existing lines/comments/order.

    Keys not already present are appended at the end.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")
