from pathlib import Path

from agent.cli._envfile import read_env, write_env


def test_read_env_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_env(tmp_path / "missing.env") == {}


def test_read_env_parses_keys_ignoring_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# a comment\n\nFOO=bar\nBAZ=1\n")
    assert read_env(p) == {"FOO": "bar", "BAZ": "1"}


def test_write_env_creates_file_when_missing(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    write_env(p, {"FOO": "bar"})
    assert read_env(p) == {"FOO": "bar"}


def test_write_env_preserves_comments_and_unrelated_keys(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# header comment\nFOO=old\nUNTOUCHED=keep\n")
    write_env(p, {"FOO": "new"})
    text = p.read_text()
    assert "# header comment" in text
    assert "UNTOUCHED=keep" in text
    assert read_env(p) == {"FOO": "new", "UNTOUCHED": "keep"}


def test_write_env_appends_new_keys(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n")
    write_env(p, {"NEW": "value"})
    assert read_env(p) == {"FOO": "bar", "NEW": "value"}
