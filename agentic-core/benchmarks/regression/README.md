# Regression suite (fixed, human-written)

These tasks are **ground truth**. Rules (spec §7):

- Deterministic answers, human-written, checked by `exact` or `regex`.
- **Never** auto-modified by the agent.
- **Never** fed to the LLM as learning material — they exist only to probe the
  current behaviour and detect misevolution.
- Run by the curator weekly and on every playbook revision. Results → `regression_run`.

Each line in `suite.jsonl` is one task:

```json
{"id": "...", "prompt": "...", "expected": "...", "checker": "exact|regex"}
```

For `exact`, the solver's answer is compared verbatim (stripped). For `regex`,
`expected` is a pattern matched against the stripped answer.
