# Financial Reporting Agent Platform

Agentic system that turns an ERP trial balance, chart of accounts, and manual adjustments into audit-grade financial statements. Current focus: the TB→COA mapper prototype slice.

## Language

**Trial Balance (TB)**:
Period-end export of all accounts with debit/credit balances, possibly multi-currency and multi-row per account.
_Avoid_: ledger, journal

**Chart of Accounts (COA)**:
Hierarchical list of accounts with statement classification (BS/PL) and cash-flow category.

**Mapping**:
The decision that assigns each unique TB account_code to exactly one COA node. One decision per account, not per row.
_Avoid_: classification (ambiguous with account type), translation (that's FX)

**Evidence Tier**:
The layer that resolved a mapping, in order: exact code, exact name, fuzzy name, LLM judgment. Deterministic tiers are tried first; the LLM sees only the residual. All four tiers exist in the prototype; the LLM tier is a single prompt call behind the shared interface.

**Confidence**:
A pure function of the tier that resolved the mapping — exact code = 1.0, fuzzy = computed similarity, LLM = self-reported with fuzzy score as floor. No cross-tier voting. Gates are tier-determined, not a global threshold: exact tiers never block on confidence; fuzzy blocks below ~0.9 similarity; LLM blocks unless self-report ≥ ~0.8 and fuzzy floor ≥ ~0.6.

**Functional Currency**:
The single currency all statements are reported in. USD for this entity.

**Translation**:
Deterministic arithmetic converting a non-functional-currency TB row into functional currency using FX rates. Not a mapping concern.

**Adjustment**:
An unposted journal entry from the finance team; the system accepts, rejects, or quarantines each one.
_Avoid_: journal, JE entry (too generic)

**Orphan Account**:
A TB account_code with no matching entry in the COA. Always escalates as `orphan_account` regardless of tier evidence — no confidence value can unblock a structural orphan. The LLM may attach an advisory `llm_proposal` (target, rationale, confidence) to speed human review, but acceptance requires a Resolution.

**LLM Proposal**:
Output of the LLM tier: target code, confidence, rationale — in a single schema-constrained call restricted to the COA's code list, so nonexistent targets cannot be named. Parse failure ⇒ `llm_invalid` escalation; no retries.

**Ambiguous Account**:
A COA entry that maps from a TB account but whose own classification is incomplete (e.g. `cf_category = TBD`). An exact-code hit is not sufficient — mapping blocks as `ambiguous_mapping` until the target is fully classified.

**Escalation**:
A mapping decision blocked from the pipeline until a human resolves it. Always carries a reason: `orphan_account`, `ambiguous_mapping`, `low_confidence`, or `llm_invalid` (unparseable output or a target that fails validation — the hallucination catch). Nothing unreviewed ever reaches a statement.

**Post-condition**:
Deterministic check on every accepted mapping, any tier: the target account code must exist in the COA. A mapping failing this check is escalated as `llm_invalid`, never emitted.

**Defect**:
A non-blocking data-quality notice attached to a mapping result (e.g. `duplicate_row` when the same account_code and currency appears twice). The mapping stands; the balance is flagged for review. Defects never enter the escalation queue.

**Coverage Lint**:
A deterministic post-mapping pass that walks the COA→TB direction and reports nodes with zero TB coverage or empty subtrees (e.g. an empty header). Reported as defects, never escalations — no mapping decision is pending.

**Comparables Lint**:
A separate coverage check of the prior-period TB against the current COA, surfacing renamed or dropped accounts (e.g. a prior account with no current-period counterpart). Reported as defects.

**Defect Register**:
`DEFECTS.md` — first-class inventory of every seeded and extra defect found in the inputs, each with location, observed evidence, severity, and the handling mechanism that catches it (escalation reason, defect type, coverage/comparables lint). Doubles as a coverage test: handled-not-listed is a doc hole, listed-not-handled is a design hole.

**Resolution**:
A human's recorded answer to an escalation, persisted in `escalations.json` — the source of truth. Re-runs consume resolutions idempotently; an interactive CLI prompt is optional sugar that writes to the same file.

**Evidence Record**:
The persisted audit unit per account mapping: source code and name, covered raw TB rows, target code, resolving tier, confidence with its score component, matched-against string, defects, escalation reason, and resolution reference if human-resolved. Failed-tier breadcrumbs are not stored.

**Run Hash**:
SHA-256 of each input file, persisted with the run — proves which exact inputs produced a set of Evidence Records.

**COA Overlay**:
`coa_overrides.json` — the only place human resolutions may change the COA (add_account, set_field). Base COA files are never mutated; the mapper merges base + overlay at load, so a resolved account maps deterministically on the next run.

**Run Modes**:
One mode. The mapper always runs; if an LLM key is present (Anthropic `ANTHROPIC_API_KEY`, or any OpenAI-compatible provider via `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`) the orphan advisory proposal is produced; without one the run still completes and orphan escalations simply carry no proposal. The manifest records `llm.provider` and `llm.degraded`.

**Run Output**:
One command, `mapper run [--interactive]`, writing `output/<timestamp>/`: `mappings.jsonl` (one Evidence Record per unique account_code), `defects.json`, `escalations.json` (prior resolutions carried forward), and `run_manifest.json` (input hashes, overlay hash, config, LLM flag).

**Validation Gate**:
Four pre-emit assertions: one Evidence Record per unique TB account; every `mapped` record passes post-condition and tier gate; batch all-or-nothing (any unresolved escalation ⇒ zero mapped records written); manifest hashes match files read. Failure ⇒ no output, non-zero exit, failing assertion named. No in-loop retries — correction happens via Resolution + idempotent rerun.
