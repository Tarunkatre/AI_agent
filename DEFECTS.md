# Defect register

Inventory of every defect found in the assignment input bundle, with the mechanism in
this prototype that surfaces it. The PDF lists some seeded defects; the inputs README
warns there are more. Each row below was verified against the raw files.

Legend for **Handled by**: `escalation:` = a blocked mapping decision, `defect:` = a
non-blocking record notice, `coverage`/`comparables` = a run-level lint finding,
`run-check:` = a top-level check in `defects.json`, `downstream` = out of this slice
(TB→COA mapping); the item is recorded because the register is also a coverage test.

| # | Location | Evidence | Severity | Handled by |
|---|---|---|---|---|
| D1 | `trial_balance.csv` account `9999 "Suspense - Unmapped"` | debit 12,400 USD; no matching `account_code` in the COA | high | `escalation: orphan_account` |
| D2 | `chart_of_accounts.csv` `1150 "Other Current Assets"`, `2170 "Intercompany Payable"` | `cf_category = TBD` (incomplete classification) | high | `escalation: ambiguous_mapping` |
| D3 | `trial_balance.csv` account `6310 "Travel and Entertainment"` | two rows, both `USD` (245,000 and 38,500) — same code + currency | medium | `defect: duplicate_row` |
| D4 | `chart_of_accounts.csv` `1290 "Other Assets"` | `Header` node with an empty subtree (no children at all) | medium | `coverage: empty_header` |
| D5 | `chart_of_accounts.csv` `8000 "Income Tax Expense"` and `3300 "Accumulated OCI"` | subtotal nodes with children (`8100`/`8200`, `3310`) but no own TB row, typed as detail accounts rather than `Header` | low | `coverage: non_header_parent_without_row` |
| D6 | `prior_period_tb.csv` `6905 "Sundry Operating Expenses"` | present in prior period, absent from the current COA (the renamed account) | medium | `comparables: absent_from_coa` |
| D7 | `trial_balance.csv` totals | debits 71,072,200 vs credits 71,077,000 → **−4,800** | high | `run-check: tb_balance_check.imbalance` |
| D8 | `fx_rates.csv` | `GBP` has `period_average` and `opening` but **no `period_end`** rate | high | `downstream` (translation slice) |
| D9 | `manual_adjustments.json` `JE-002` | debits 28,500 vs credits 25,000 → unbalanced by +3,500 | high | `downstream` (adjustments slice) |
| D10 | `manual_adjustments.json` `JE-005` | references account `6315`, which is not in the COA | medium | `downstream` (adjustments slice) |
| D11 | `manual_adjustments.json` `JE-008` | `2170` debited 320,000 and credited 320,000 in the same entry — a circular intercompany entry that nets to zero | medium | `downstream` (adjustments slice) |
| D12 | `fx_rates.csv` / TB multi-currency rows | `1110` carries EUR and GBP amounts at face value; the bundle's stated cause of D7 is "unbalanced after FX rounding" | info | `downstream` (translation slice) |

## Notes

- **D7 root cause is not proven here.** The TB sums face values across currencies; the
  assignment attributes the residual to FX rounding, but this slice (mapping) does not
  translate or sum, so it reports the −4,800 imbalance as an observation rather than
  asserting a cause. Confirming it belongs to the translation/adjustment stages.
- **D3 and D7 interact.** `6310`'s duplicate row is one of the amounts a naive sum would
  double-count; the duplicate is flagged (`D3`) so the sum that produces `D7` is suspect.
- **D1 vs D6 are different defects.** D1 is a current-period code with no COA node; D6 is
  a prior-period account with no current COA node. Only D1 blocks a current mapping.
- **Reserved taxonomy.** Under the strict rule "a code absent from the COA is always an
  orphan", the `low_confidence` and `llm_invalid` escalation reasons cannot be reached by
  a *successful* acceptance path in this slice; `llm_invalid` still appears as an
  annotation when the advisory LLM call fails (see tests). They remain defined so the
  taxonomy is stable if code matching is later relaxed for cross-ERP bundles.
- **D5 is an extra finding, not a PDF-listed one.** The PDF lists "one node has no
  children mapped" (that is D4/`1290`). `8000` and `3300` are subtotal nodes that a
  statement builder could double-count if a TB row were ever mapped onto them; the lint
  surfaces them at low severity. They are not part of the seeded set.
