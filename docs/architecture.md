# Architecture — agentic ERP-to-financial-statements

Design for an AI-native feature that ingests a trial balance (CSV/JSON), a configurable
chart of accounts, and a batch of manual adjustments, and produces four audit-grade
statements. This document designs the whole feature and then goes deep on the one slice
that was actually built: the **TB→COA mapper** (see `README.md`).

The guiding belief throughout: *"generate financial statements" is not one problem.* It is
six-plus sub-problems with different reliability requirements, and the architecture earns
its name by drawing the right line between arithmetic and judgment, not by placing an LLM
in the control loop.

Companion documents: `CONTEXT.md` (ubiquitous language), `DEFECTS.md` (defect register),
`docs/adr/0001-pipeline-of-stages-not-orchestrator.md`.

---

## 1. Platform context

This feature is one of a platform's ten core capabilities. It sits in the middle of a
chain:

```
ERP adapters → [ ingest → translate → map → adjust → eliminate → build → validate ] → consolidate → report/disclose
   (SAP, NetSuite)                          ▲ this feature                          ▲ ten features, not one
```

**Upstream seams.** ERPs export different shapes; an adapter layer normalises to the
platform's canonical TB (account_code, account_name, currency, debit, credit, period).
The mapper assumes that canonical shape but never assumes it is *clean*.

**Downstream seams.** Statements feed consolidation (multi-entity, intercompany
elimination across entities) and disclosure/reporting. The contract handed downstream is a
mapping table plus evidence records — not prose, not a rendered PDF.

**Why design it as a pipeline, not a product monolith.** Each stage publishes a typed
artifact with a stated reliability bar; a failure in one stage is localised and its blast
radius is known. That is the difference between "revenue is wrong on the P&L" and "the
statement run is blocked because three mappings are unresolved".

---

## 2. Decomposition: the six sub-problems

| # | Sub-problem | Input → Output | Reliability bar | Nature |
|---|---|---|---|---|
| 1 | **Ingest & canonicalise** | ERP export → canonical TB | exact; lossless | deterministic |
| 2 | **Translate (FX)** | multi-currency TB + rates → functional-currency TB | exact arithmetic; a missing rate is a hard stop | deterministic |
| 3 | **Map TB→COA** | TB accounts + COA → mapping with confidence | every account resolves or blocks | mostly deterministic, judgment at the residual |
| 4 | **Apply adjustments** | unposted JEs → posted deltas | debits = credits per entry; unknown accounts block | deterministic checks, judgment in narration |
| 5 | **Eliminate intercompany** | entity TBs → consolidated | must net to zero or block | deterministic, judgment on circularity |
| 6 | **Build statements** | mapped+adjusted TB → BS/P&L/CF/SOCIE | statements must articulate and cross-foot | deterministic |
| 7 | **Validate & reconcile** | before/after artifacts → explained deltas | every delta explained or flagged | mixed; narration is the LLM's job |

The plan for the platform is to build these stages behind one validation spine, shipping
the ones with the highest reliability requirement first. This assignment's prototype is
stage 3.

---

## 3. Agent topology

**Chosen: a pipeline of specialised stages with localised judgment and a shared audit
spine.** Each stage is deterministic code with, at most, one bounded LLM sub-step at its
judgment point; a single validation/audit spine runs alongside. See ADR 0001.

```
input ─▶ [stage]──▶ artifact ─▶ [stage]──▶ artifact ─▶ ... ─▶ statements
             │                    │
             └─────── audit spine (evidence records, hashes, run manifest) ───────┘
```

**Rejected alternatives (deliberately).**

- *Single agent with tools.* Wraps deterministic arithmetic in an unbounded reasoning loop.
  Cost, latency, and — fatally — **reproducibility** are lost; an auditor cannot replay a
  statement that a sampling model produced.
- *Orchestrator with sub-agents (Mapper, Adjuster, Builder, Validator).* This is the
  assignment's "12-agent swarm for a 200-line script" anti-pattern when applied as day-one
  architecture. It recreates nondeterminism at every hop and makes the audit trail
  discretionary. Sub-agents may be introduced later as *bounded* stages behind the same
  validation gate — for example an Adjuster that must narrate rejections in plain English —
  but only where narration genuinely earns its keep.

**Where the LLM actually earns its keep.** Three places, all bounded:

1. **Mapping residual** — a TB account whose code/name evidence is insufficient needs a
   semantic judgment against the COA's *language*. One schema-constrained call.
2. **Adjustment narration** — explaining in plain English *why* an entry was rejected, to
   a finance user. Language task; deterministic detection.
3. **Reconciliation narration** — explaining a delta line-by-line between two TBs.

Everything else — totals, translation, articulation, cross-footing, tie-outs — is a
200-line script's job, and should be.

---

## 4. The deterministic/LLM line

**Rule.** The LLM is allowed to make *judgments over language and taxonomy*. It is never
allowed to perform arithmetic, enforce policy, or own control flow. If a decision has a
correct answer that a reviewer could compute, it is code.

| Decision | Deterministic | LLM | Why |
|---|---|---|---|
| FX translation, summing, totals | ✅ | | arithmetic must replay exactly |
| Statement articulation / cross-foot | ✅ | | policy + arithmetic |
| Exact code / exact name match | ✅ | | correct answer exists |
| Fuzzy name match | ✅ (score) | | a metric, not a judgment |
| Residual semantic mapping | | ✅ (1 call) | genuine taxonomy judgment |
| Whether an orphan may map | ✅ (never) | | policy: structural block |
| Rejection explanation text | | ✅ | language |
| Accept / reject / block a mapping | ✅ | | policy + gates |

**Consequence.** On the provided bundle, exact-code resolves 58 of 59 accounts; the LLM
tier is idle for mapping and fires only to *advise* on the single orphan. That is the
point, not a weakness: the architecture proves it does not reach for a model when a
dictionary lookup suffices.

---

## 5. Failure modes and responses

The failures that kill financial software in production, and how each is caught:

| Failure | Detection | Response |
|---|---|---|
| **Hallucinated account mapping** | target ∉ COA (deterministic post-condition); LLM output constrained to a COA-code enum | escalate `llm_invalid`; never emitted |
| **Debits ≠ credits after adjustments** | per-entry balance check (stage 4) | block the entry; explain in plain English |
| **Account fits no COA node** | code absent from COA (stage 3) | escalate `orphan_account`; human assigns or adds the account via overlay |
| **Ambiguous / incomplete COA node** | target has `cf_category = TBD` | escalate `ambiguous_mapping` |
| **FX gap** (missing rate) | rate lookup returns nothing (stage 2) | hard-stop the run; never interpolate silently |
| **Circular intercompany** | elimination does not net to zero / self-referential entry | quarantine the entry; surface for review |
| **Prior-period restatement** | comparables lint: prior account with no current counterpart | report as defect; never silently drop |
| **Duplicate source rows** | same account+currency twice | non-blocking `duplicate_row` defect; the amount is flagged |
| **Unbalanced TB** | debit/credit total check | run-level check; surfaced, scoped to translation |

**Precedence and the blocking rule.** Escalations *block*; defects *inform*. A single
unresolved escalation means the batch does not produce mappings — nothing unreviewed ever
reaches a statement.

---

## 6. Validation and self-correction loop

Correction happens at three layers, in order of cost:

1. **Per-stage post-conditions (cheap, automatic).** Every mapping must pass the
   COA-existence check; every adjustment must balance; every FX lookup must succeed. These
   run inline and short-circuit.
2. **Batch validation gate (before any output).** Four assertions for the mapper:
   one evidence record per unique account; every mapped record passes its tier gate and
   post-condition; the batch is **all-or-nothing** (any unresolved escalation ⇒ zero mapped
   records written); manifest hashes match the files read. Failure means **no output** and a
   named assertion — fail closed, not fail partial.
3. **Human resolution + idempotent rerun (expensive, deliberate).** A blocked account is
   resolved by editing `escalations.json` / `coa_overrides.json` (or `--interactive`). The
   next run consumes the resolution and reproduces the whole batch; same inputs + same
   resolutions ⇒ same output.

**What we deliberately did *not* build: an in-loop retry.** Re-prompting a model that
returned malformed output is theatre — nothing tells the retry what to do differently.
The honest self-correction story for arithmetic-shaped work is *human resolution* and
*idempotent replay*, not a model re-rolling its own dice. (An LLM-driven self-correction
loop belongs to the statement-builder slice, where a validator can feed a *specific,
checkable* defect back to a builder.)

---

## 7. Traceability: any cell back to source rows

The chain is built at stage 3 and never broken:

```
statement cell
   └─ statement line  (code + period + sign)
        └─ mapping Evidence Record        ← the mapper's output
             ├─ source TB rows            (raw debit/credit/currency, preserved verbatim)
             ├─ resolving tier + score    ("exact_code", confidence 1.0)
             ├─ matched-against string    (which COA field was compared)
             ├─ defects                   (duplicate_row, …)
             └─ resolution                (who approved, what they chose)
                  └─ run manifest        (SHA-256 of every input file + overlay + config)
```

An auditor's query — *"why is account 9999 on this line?"* — is answered without guessing:
the record shows it was an orphan, blocked, then human-assigned to `1150`, with the input
hashes that prove which files produced the run. Inputs are immutable; the only place a
human changes the COA is the overlay, which is itself hashed. The mapper never mutates a
source file.

---

## 8. The prototype slice (built)

**What it is.** A working, dependency-free Python implementation of stage 3, wired to the
provided messy bundle. Run instructions and output schema are in `README.md`.

**What it proves.**

- The full tiered pipeline (exact code → exact name → fuzzy → one bounded LLM call) with
  tier-determined confidence gates, no cross-tier voting.
- Structural failure handling that survives the seeded defects: orphan `9999`, ambiguous
  `1150`/`2170`, duplicate `6310`, empty header `1290`, prior-period `6905`, and the
  −4,800 imbalance.
- The all-or-nothing validation gate, human resolution, and idempotent replay.
- A keyless run path so a reviewer can clone and run without paying for a model.

**What it is not.** It does not translate FX, apply adjustments, or build statements — those
are other stages, and `DEFECTS.md` marks their defects `downstream`.

---

## 9. Two of us in three months — and where this breaks

**With three months instead of eight hours** the order of work would be:

1. **Stage 2 (FX translation) next.** It is pure and it is where D7/D8 live; the mapper is
   meaningless until amounts are in one currency.
2. **The canvas/fixture layer.** A corpus of *synthetic messy bundles* (renamed accounts,
   dropped rates, restatements) with expected outcomes, so staging is testable end-to-end —
   the single highest-leverage investment for a system whose whole job is resilience to mess.
3. **Persistent mapping memory.** Mappings, once approved, become a durable
   account-identity ledger keyed by tenant + ERP + period, so "this account was resolved
   last quarter" is a fact, not a re-derivation. This is also the cheapest accuracy win.
4. **The explanation layer** (adjustment and reconciliation narration) — bounded,
   eval-gated, human-facing.

**Where the prototype breaks at scale:**

- **Thousands of accounts / many ERPs.** The fuzzy tier is O(accounts × COA). Fine at 60;
  at 100k it needs blocking/indexing (code-prefix, tenant prior, embeddings) and the LLM
  tier needs batching and a cache. The *design* holds; the *implementation* is a prototype.
- **Multi-entity consolidation.** The mapper is single-entity. Intercompany elimination is
  a different stage with a different failure topology (cross-entity matching, FX on both
  legs, circularity). It cannot be bolted on; it needs its own stage and its own gate.
- **Idempotency of the LLM tier.** Deterministic tiers and resolutions replay exactly; the
  LLM tier is only *practically* deterministic (temperature 0, schema-constrained). At
  scale, every accepted LLM proposal must be **recorded as a resolution** the moment it is
  approved, so the deterministic replay path never depends on the model again.
- **Concurrency.** Two reviewers resolving the same escalation, or two runs racing on the
  overlay, need optimistic locking and an append-only resolution log. Out of scope for the
  prototype; non-negotiable in production.
