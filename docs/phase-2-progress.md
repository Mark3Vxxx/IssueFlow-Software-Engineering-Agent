# Phase 2 progress

Update this table only from verified milestone gates.

| Milestone | Tasks done | Verification | Paid spend | Status |
| --- | ---: | --- | ---: | --- |
| 2A Architectures | 8/8 | PASS | CNY 0 | Complete |
| 2B Benchmark | 0/8 | 0/20 strict × 3 | CNY 0 | Not started |
| 2C Experiments | 0/10 | 0/160 trials | CNY 0 | Not started |
| 2D Results | 0/6 | Not run | CNY 0 | Not started |

Phase 2A uses deterministic scripted model boundaries; no paid API request was made. Its evidence has three distinct layers: the phase-one verifier replays the five catalog reference patches (`5/5`), the retained infrastructure E2E runs all four architectures against one local Docker fixture (`4/4`), and the compatibility matrix runs four architectures across all five pinned cases (`20/20`) with literal case-specific decisions through RunService, Git, Docker, independent verification, SQLite, and JSON. The matrix does not read or apply reference patches and is integration evidence, not model-quality evidence. The milestone gate is `make verify-phase-1`, `make test-phase-2`, `git diff --check`, and the credential scan documented in the Task 8 report.
