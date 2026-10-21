# JUnit XML fixtures

Sample reports for testing the parser. Expected results are listed so tests can
assert on them.

| File | Root element | Expected |
| --- | --- | --- |
| `pytest_mixed.xml` | `<testsuites>` | 5 tests: 2 passed, 1 failed (`failure`), 1 failed (`error`), 1 skipped. Includes a parametrized name. |
| `jest_junit.xml` | `<testsuites>` | 3 tests: 2 passed, 1 failed. Has a `file` attribute; `classname` repeats the name. |
| `go_junit_report.xml` | `<testsuites>` | 3 tests: 2 passed, 1 failed. Has a `<properties>` block to ignore. |
| `gradle_single_suite.xml` | `<testsuite>` (no wrapper) | 2 tests, both passed. |
| `malformed.xml` | — | Truncated file. Parsing should fail with a clear error, not crash the worker. |
| `entity_expansion.xml` | — | Entity-expansion ("billion laughs") attack, scaled down. Must be rejected. |
| `external_entity.xml` | — | XXE attack that tries to read a local file. Must be rejected. |
