"""The playbook is the agent's memory when vector retrieval isn't available
(no embedding model pulled, Ollama down, empty archive). It was written and
curated but never read back into a prompt — the agent maintained files it
could not see.
"""

from pathlib import Path

from agent.playbook.context import DEFAULT_MAX_CHARS, build_context
from agent.playbook.store import MEMORY_FILE, SELF_FILE, USER_FILE, PlaybookStore


def _store(tmp_path: Path) -> PlaybookStore:
    store = PlaybookStore(tmp_path)
    store.ensure()
    return store


def test_fresh_playbook_contributes_nothing(tmp_path: Path) -> None:
    # Three empty scaffolds would otherwise ride along on every single turn.
    assert build_context(_store(tmp_path)) == ""


def test_written_content_is_included(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(MEMORY_FILE, "# MEMORY\n\n- Deploys happen on Fridays.\n")
    assert "Deploys happen on Fridays." in build_context(store)


def test_untouched_files_are_skipped_but_written_ones_kept(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(USER_FILE, "# USER\n\n- Prefers Bahasa Indonesia.\n")
    context = build_context(store)
    assert "Prefers Bahasa Indonesia." in context
    # MEMORY/SELF are still scaffolds — their headers shouldn't appear.
    assert "Durable facts and operating knowledge." not in context


def test_identity_survives_when_memory_is_huge(tmp_path: Path) -> None:
    # SELF/USER define who the agent is; bulk facts are what should get cut.
    store = _store(tmp_path)
    store.write(SELF_FILE, "# SELF\n\n- I am Ratu, a concise assistant.\n")
    store.write(USER_FILE, "# USER\n\n- Operator is Fakhri.\n")
    store.write(MEMORY_FILE, "# MEMORY\n\n" + ("- filler fact\n" * 2000))

    context = build_context(store)
    assert "I am Ratu, a concise assistant." in context
    assert "Operator is Fakhri." in context
    assert len(context) <= DEFAULT_MAX_CHARS + 64  # separators/marker slack
    assert "…(truncated)" in context


def test_respects_explicit_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(MEMORY_FILE, "# MEMORY\n\n" + ("x" * 5000))
    assert len(build_context(store, max_chars=200)) <= 200 + 64


def test_missing_directory_yields_empty_not_an_error(tmp_path: Path) -> None:
    assert build_context(PlaybookStore(tmp_path / "does-not-exist")) == ""
