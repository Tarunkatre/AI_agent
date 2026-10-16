# TB → COA Mapper (prototype slice)

A working prototype of **slice (a)** from the AI Agentic Engineer take-home: a mapper
that assigns every trial-balance account to exactly one chart-of-accounts node, with
confidence scoring, human-in-the-loop escalation, audit-grade evidence records, and a
validation gate that refuses to emit an untrustworthy batch.

It is deliberately a **deterministic pipeline with one bounded LLM step**, not an agent
swarm. See `docs/adr/0001-pipeline-of-stages-not-orchestrator.md` and `CONTEXT.md` for the
vocabulary and the reasoning behind every decision.

**Submission documents**

- `docs/architecture.md` — the architecture document (platform context → decomposition →
  topology → failure modes → traceability → the built slice)
- `docs/reflection.md` — one-page reflection
- `DEFECTS.md` — defect register (seeded defects + extra findings)
- `CONTEXT.md` — domain glossary

## Requirements

- Python 3.11+ (standard library only — no third-party packages)

## Run

The mapper runs with no key at all (orphan accounts then escalate without an LLM advisory
proposal; `run_manifest.json` marks `llm.degraded`). Add a key to enrich orphans with an
advisory proposal — Anthropic, or any OpenAI-compatible provider.

```bash
python3 -m mapper run --inputs AI_Agentic_Engineer_Assignment/inputs
```

Anthropic:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m mapper run --inputs AI_Agentic_Engineer_Assignment/inputs
```

Any OpenAI-compatible provider (free, no credit card) — Groq:

```bash
export LLM_BASE_URL=https://api.groq.com/openai/v1
export LLM_API_KEY=gsk_...           # console.groq.com
export LLM_MODEL=llama-3.3-70b-versatile
python3 -m mapper run --inputs AI_Agentic_Engineer_Assignment/inputs
```

Other free providers: OpenRouter (`https://openrouter.ai/api/v1`, a `…:free` model id),
Google AI Studio (`https://generativelanguage.googleapis.com/v1beta/openai/`,
`gemini-2.5-flash`). `ANTHROPIC_API_KEY` takes precedence if both are set.

Interactive resolution (prompts for each blocked account):

```bash
python3 -m mapper run --inputs AI_Agentic_Engineer_Assignment/inputs --interactive
```

Useful flags: `--inputs`, `--overlay coa_overrides.json`, `--escalations escalations.json`,
`--output-dir output`, `--period 2024-Q4`.

Exit codes: `0` run completed (status `complete` **or** `blocked`), `1` validation gate
failed (nothing written), `2` configuration/input error.

## What it does

1. Loads the base COA and merges `coa_overrides.json` (the *only* place human changes to
   the COA live — input files are never mutated).
2. Groups the trial balance **per unique `account_code`** (multi-currency rows collapse;
   one mapping decision per account).
3. Resolves each account through evidence tiers — exact code, exact name, fuzzy name,
   then a single schema-constrained LLM call restricted to the COA's code list.
4. Applies the failure rules:
   - code absent from the COA → `orphan_account` (blocked, unconditional)
   - target has `cf_category = TBD` → `ambiguous_mapping` (blocked)
   - LLM call fails → the reason is annotated `llm_invalid`
5. Runs the lints: `duplicate_row` (same code + currency twice), COA coverage lint, and a
   prior-period comparables lint; plus a trial-balance debit/credit check.
6. Passes the **validation gate** and writes a run folder.

## Output

Each run writes `output/<UTC-timestamp>/`:

| File | Contents |
|---|---|
| `mappings.jsonl` | one Evidence Record per mapped account (empty if the run is blocked) |
| `escalations.json` | pending escalations + carried-forward resolutions (also mirrored to `escalations.json` at the repo root as the source of truth) |
| `defects.json` | record-level defects, run-level lints, and the trial-balance balance check |
| `run_manifest.json` | input hashes, overlay, config gates, LLM model, counts |

**The batch is all-or-nothing.** While any escalation is unresolved, `mappings.jsonl` is
written empty — nothing unreviewed ever reaches a downstream statement. Resolve the
escalations (edit `escalations.json` / `coa_overrides.json`, or use `--interactive`) and
re-run; the second run is idempotent with respect to the same inputs.

## Resolving an escalation

- **Orphan** (`9999`): either assign to an existing node
  (`{"action": "assign_existing", "target_code": "1150"}`) or add the account to
  `coa_overrides.json` under `add_account`.
- **Ambiguous** (`1150`, `2170`): add a `set_field` entry to `coa_overrides.json`
  (e.g. `cf_category`), which clears the block on the next run.
- Any `assign_existing` is recorded as a `human_assign` mapping with confidence 1.0.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite is keyless: it injects a fake LLM client at the same seam the real client uses.
It encodes every design rule as an executable assertion (orphan blocks, `6310` duplicate
flagged, coverage/comparables lints fire, −4,800 imbalance detected, gate fails closed,
resolutions carry forward, COA inputs are never mutated, two runs are identical).

## Layout

```
mapper/
  cli.py engine.py runner.py validate.py   orchestration, gate, outputs
  tiers.py fuzzy.py llm.py                 evidence tiers + LLM seam
  overlay.py loaders.py lints.py models.py support
  config.py
tests/                                     executable spec
CONTEXT.md                                 domain glossary
DEFECTS.md                                 defect register (seeded + extra findings)
docs/architecture.md                       architecture document
docs/reflection.md                         one-page reflection
docs/adr/0001-pipeline-of-stages-not-orchestrator.md
output/                                    example runs (blocked → resolved)
```

## Scope and limitations

- One slice only. Statements, FX translation, and adjustment processing are out of scope;
  `DEFECTS.md` marks their defects as `downstream`.
- Under the strict "code absent from COA ⇒ orphan" rule, the `low_confidence` and
  successful-`llm_invalid` paths are unreachable in this bundle; they are kept so the
  taxonomy survives a future relaxation of code matching.
- The LLM step only ever produces an **advisory** proposal for orphans on this data; every
  emitted mapping is deterministic or human-approved.
