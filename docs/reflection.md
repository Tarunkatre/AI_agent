# One-page reflection

## What I'd build differently with three months instead of eight hours

Three months buys **resilience engineering**, not more features. The single highest-leverage
investment is a **corpus of synthetic messy bundles** — renamed accounts, dropped FX rates,
out-of-balance entries, circular intercompany — each with an expected outcome, so every
stage is testable against the failure modes that actually occur rather than against one
hand-crafted happy path. Close behind: an **account-identity ledger**. A mapping approved
once should be a durable fact keyed by tenant + ERP + period, so resolution is a lookup, not
a re-derivation every quarter. In eight hours I could only build the mapper end-to-end; in
three months I'd spend most of the time on the fixtures and the ledger and relatively little
on new surface area.

## Where the prototype breaks at scale

The fuzzy tier is O(accounts × COA). At 60 accounts that is nothing; at 100,000 it needs
blocking (code-prefix, tenant priors, embeddings) and the LLM tier needs batching and
caching. Then multi-entity consolidation: the mapper is single-entity, and intercompany
elimination is a genuinely different problem — cross-entity matching, FX on both legs,
circularity — that cannot be bolted onto a single-entity mapper. And the subtler limit:
only the deterministic tiers and human resolutions replay exactly. The moment an LLM
proposal is approved, it must be frozen as a resolution, or "idempotent rerun" quietly stops
being true.

## How I used AI tools

I used an AI agent as a **design adversary**, not an autocomplete. I ran a structured
interview: it asked me one decision at a time, each with a recommendation, and I accepted,
rejected, or amended. That is how the whole taxonomy — evidence tiers, the four escalation
reasons, the all-or-nothing gate — got pinned before a line was written, and it caught two
things I'd have gotten wrong: I nearly defined confidence as a global threshold (the data
showed the fuzzy/LLM tiers essentially never fire on this bundle, so a "clever" threshold
would have been dead code), and mid-build the agent surfaced a genuine contradiction —
defining *orphan* by account code makes the fuzzy and LLM acceptance gates unreachable. I
chose to keep the strict, safe definition and document the dead taxonomy rather than loosen
it for tidiness.

AI also wrote most of the mapper and its tests. Where it helped: mechanical, verified work —
the pipeline, the lints, 36 unit tests that encode each design rule as an assertion. Where
it led me wrong: it can't read PDFs, so the first step was converting the brief with
`pdftotext`; and left alone it drifts toward *more* machinery (an orchestrator, retry loops,
a stub LLM mode) than the problem warrants. Every time I pushed back toward less, the design
got better. The judgment stayed mine; the typing did not.

## One thing I think is underestimated

The problem is underestimated as **arithmetic** when it is really **provenance**. Every
number on a statement has a lineage — source rows → mapping → adjustment → elimination →
cell — and the hard part is keeping that lineage intact while the data is messy, not
computing the totals. That is why auditability had to be designed in from the first line
rather than retrofitted: an evidence record and a run manifest are cheap on day one and
impossible to reconstruct later. A system that computes the right number but cannot show
*why* it is right is, for a finance user, indistinguishable from one that is wrong.
