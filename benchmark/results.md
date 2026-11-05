# Benchmark results

**Fixed 536 of 545 injected errors (98%) across 5 datasets. Broke 0 of 8,510 clean cells and lost 0 real rows. Total model cost $0.26 ($0.052 per file).**

| Dataset | Fixed | Broke (cells) | Lost rows | QA | Plans | Cost | Time |
|---|---|---|---|---|---|---|---|
| customers | 111/111 (100%) | 0 | 0 | pass | 1 | $0.057 | 44.1s |
| orders | 107/110 (97%) | 0 | 0 | pass | 1 | $0.048 | 38.5s |
| employees | 111/111 (100%) | 0 | 0 | pass | 1 | $0.056 | 48.5s |
| registrations | 105/105 (100%) | 0 | 0 | pass | 1 | $0.046 | 36.9s |
| inventory | 102/108 (94%) | 0 | 0 | pass | 1 | $0.052 | 38.1s |

## By error type

| Error type | Fixed | Share |
|---|---|---|
| whitespace | 110/110 | 100% |
| missing marker | 15/16 | 94% |
| date format | 107/107 | 100% |
| phone format | 40/40 | 100% |
| number format | 84/84 | 100% |
| email case | 41/41 | 100% |
| category typo | 84/92 | 91% |
| duplicate row | 40/40 | 100% |
| empty row | 15/15 | 100% |

Data is synthetic and seeded (`python -m benchmark.generate`), so the right value of every cell is known. Risky steps were approved automatically for the run.
## What it missed (run of 2026-11-05)

- 8 category typos, all in two columns: `supplier` in inventory (6, e.g. "Northwind Prats") and `status` in orders (2, e.g. "Cnacelled"). The planner fixed typos in the other category columns of the same files but didn't plan a fix for these two.
- 1 missing marker: "NA" wasn't in the token list the planner passed to `standardize_missing`, so it was title-cased to "Na" instead of blanked.
