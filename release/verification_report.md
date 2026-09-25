# Verification Report

Evidence baseline: `003a61deda02964c1b5f58e66eefc047c53f8896`.

## Executed checks

| Check | Result |
|---|---|
| `python -m src.evaluation.runner` | Completed and regenerated 15 JSON artifacts plus CSV evidence |
| `python -m unittest discover -s tests -v` | 30 tests passed; no resource warning |
| JSON parse check | 15 of 15 files parsed |
| Human evaluation CSV | 10 rows; every row unconfirmed and marked `draft_advisory` |
| Implementation comparison CSV | 2 comparable questions |
| Python and AI Coding source diff | Shared source and tests match; snapshot-only wrappers are intentional |
| External-platform keyword scan | No matching content in the release tree |
| Secret pattern scan | No committed API key pattern found |
| `git diff --check` | Passed |
| Release packet validator | 0 issues |

## Verified values

- Retrieval Recall@1/3/5: 100% / 100% / 100%.
- Automatic answer metrics: 100% / 100% / 100%.
- Agent metrics: all four headline metrics 100%.
- Engineering: average 0.26 ms; P95 0.39 ms; error rate 0%; test pass rate 100%.
- Corpus: 12 documents; 7 topics; 24 chunks.
- Embedding: 20 selected chunks; 512 dimensions.

## Restricted evidence

- The external LLM prompt comparison is a runnable design without credentialed output.
- The human evaluation package contains AI suggestions only and requires submitter confirmation.
