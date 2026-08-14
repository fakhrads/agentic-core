# agent-core

Autonomous agent daemon. Consumer of **auth core + tools backend** (contract v1).
This repo holds only the agent; the tools/auth services live elsewhere.

## Status

Built incrementally by milestone (see the implementation spec §11). **All milestones complete (M1–M12).**

| Milestone | Scope | State |
|---|---|---|
| M1 | Skeleton + config + logging + `agent health` + `agent config show` + compose | ✅ done |
| M2 | Bus + episode + `agent tail` / `agent trace` | ✅ done |
| M3 | Minimal loop (Telegram → planner → executor) + budget | ✅ done |
| M4 | Tools client + function calling | ✅ done |
| M5 | Memory (episodic/semantic/pgvector/quarantine) | ✅ done |
| M6 | Regression harness (before any self-modification) | ✅ done |
| M7 | Playbook + curator | ✅ done |
| M8 | Goals + night shift | ✅ done |
| M9 | Reasoning memory + skills | ✅ done |
| M10 | Review + drift | ✅ done |
| M11 | Tool forge | ✅ done |
| M12 | Full TUI dashboard | ✅ done |

## Quick start (dev)

```bash
# 1. deps
docker compose up -d                 # redis + postgres(pgvector)
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                 # then fill secrets

# 2. sanity
agent config show
agent health                         # checks redis, postgres, deepseek, ollama, tools
agent up                             # serves /health + /metrics on :8099

# 3. checks
ruff check src tests
mypy
pytest
```

## Non-negotiables (from spec)

1. Every artefact (memory/skill/tool) has fitness and can be disabled — never deleted.
2. External content is quarantined before it can become long-term memory.
3. Every autonomous action has a budget and a permission tier.
4. `trace_id` flows through every layer.
5. The CLI is the primary operator interface — if state isn't visible from the CLI, it isn't done.
6. Gating uses only signals the agent cannot fabricate about itself.
