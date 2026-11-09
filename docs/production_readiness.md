# Production-readiness checklist

Status legend: ✅ done and verified here · 🟡 implemented, verified only on
synthetic data or in the sandbox · ⛔ blocked on the customer environment or a
decision.

## Functional
| Item | Status | Evidence / next step |
|---|---|---|
| Claim extraction EN/AR with configurable patterns | 🟡 | 124 automated tests + customer sample set (`docs/real_document_results.md`) |
| Verified production pattern profile | 🟡 | `marker` profile derived from and verified on 7 customer documents; widen the sample set (more Arabic documents) |
| Arabic sample consistency | ⛔ | customer: fix claim 8/9/11 number placement in `Claims - Arabic.docx` |
| Number-based pairing, exact response contract | ✅ | `tests/integration/test_api.py::test_convert_success_contract` |
| Text, formatting, RTL, tables, images, drawings, equations | 🟡 | synthetic fixtures + visual review sample `docs/visual/` |
| All-or-nothing semantics (no partial 200) | ✅ | integrity invariants + error tests |
| Unsupported content fails explicitly | ✅ | `test_unsupported_content_fails_explicitly` |
| Real-document regression suite | 🟡 | `tests/regression/test_customer_samples.py` (opt-in via `PATENT_SAMPLES_DIR`; documents stay outside the public repo) |

## Integration
| Item | Status | Evidence / next step |
|---|---|---|
| CKEditor compatibility | 🟡 | CKEditor 4.22.1 and 5 (47.4) measured. **Default filters strip sub/sup/dir/MathML**; config documented. Verify the actual OutSystems build |
| OutSystems REST consume (multipart, timeouts, deserialization of max-size response) | ⛔ | Phase 8 |
| SQL Server persistence without truncation | ⛔ | Phase 8 (schema and guidance in docs) |
| Response size limit decision (100 MB vs measured 273 MB worst case) | ⛔ | decision needed |

## Operations
| Item | Status | Evidence / next step |
|---|---|---|
| Docker image, non-root, read-only, healthcheck | ✅ | built and run hardened in the sandbox |
| Two-node deployment behind a load balancer | ⛔ | target infrastructure |
| Benchmarks on 16 vCPU / 64 GB nodes with real patents | ⛔ | `scripts/benchmark_conversion.py` |
| Prometheus metrics, JSON logs without patent content | ✅ | tests + manual inspection |
| Crash recovery / cleanup | ✅ | sweeper, timeout test, container check |

## Security
| Item | Status | Evidence |
|---|---|---|
| ZIP bomb, traversal, symlink, macro, encryption screening | ✅ | `tests/security` |
| XXE / entity / depth attacks | ✅ | `tests/security` |
| HTML / SVG sanitization | ✅ | `tests/unit/test_html_sanitizer.py` |
| No network access during conversion | ✅ | externals never fetched (test); LibreOffice runs with a minimal environment and no external links |
| Auth (hashed API keys), rate limit, concurrency limit | ✅ | API tests |
| Penetration test / dependency scanning | ⛔ | organisation process |

## Decisions applied (defaults confirmed by the product owner)
1. Tracked changes → reject (`content.tracked_changes: accept` available).
2. Floating drawings → kept in flow at the anchor, original size.
3. OLE objects → stored preview image; error if no preview; never executed.
4. Equations → MathML, with image fallback for unsupported constructs
   (`equation_mode: image` for editors that cannot allow MathML).
5. Response limit → 100 MB, explicit `OUTPUT_LIMIT_EXCEEDED`, configurable.
