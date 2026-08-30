# Real Findings From Building This Project

Same rationale as every other project in this portfolio: an honest
account of what was actually discovered while writing and testing this
code, not a cleaned-up version of events.

## 1. The exact-match-vs-authorization-satisfaction distinction — a real, constructed security proof, not a hypothetical

While designing `project_to_elasticsearch()`, the obvious first
approach was: `table.scan(authorizations={"U"})` — reuse the same
authorization-checking scan already built and verified for Accumulo
access, just called with a `U`-only authorization set. This turns out
to be the WRONG design, and it's provable, not just theoretically
risky.

Constructed a deliberately adversarial cell labeled `"U|TS"` (an OR
expression mixing the unclassified label with Top Secret) and ran it
through both approaches:
- `scan(authorizations={"U"})` **admitted the cell** — correctly, per
  Accumulo's own OR semantics: holding `U` alone satisfies `U|TS`.
- The actual `project_to_elasticsearch()` implementation, which does
  **exact string match** on the literal label `"U"` rather than
  authorization-satisfaction, **correctly excluded it**.

This project's own data generator never actually produces a mixed
label like `"U|TS"` — every cell it creates is either purely `U` or
purely classified. So this specific bug could not have been triggered
by this project's own synthetic data. It's included anyway, as a
genuine, provable defense against a plausible future/real-world
scenario: a legacy or mislabeled source system producing a cell with a
mixed classification expression. The distinction matters specifically
*because* Elasticsearch has no per-query authorization check at all
once data lands there — unlike Accumulo, which re-evaluates
authorizations on every single scan. A projection step feeding an
unguarded system needs to be more conservative than "would a U-holder
be allowed to see this," and exact-match is the way to guarantee that.

`tests/test_security.py::test_projection_excludes_mixed_classification_cell`
encodes this exact constructed scenario as a permanent regression test.

## 2. Accumulo's real ambiguity rule (mixed & and | without parentheses) had to be implemented deliberately, not just parsed permissively

Early versions of the parser considered simply giving `&` higher
precedence than `|` (standard operator-precedence parsing, the way
most expression languages handle mixed operators) rather than
rejecting the mixed case outright. Checked this against Accumulo's own
documented behavior: real Accumulo actually **rejects** an expression
like `A&B|C` as ambiguous, requiring explicit parentheses
(`(A&B)|C` or `A&(B|C)`) instead of silently picking a precedence
convention. Implemented `parse_visibility()` to match this — raising
`VisibilityParseError` on any mixed-operator expression without
parentheses — rather than the more permissive (but non-matching)
precedence-based approach, since silently picking a different
precedence convention than the real system this project mirrors would
be a subtle, dangerous correctness gap: a security label that parses
differently in this project's simulator than it would in real
Accumulo defeats the entire point of building the simulator to
validate this logic in the first place.

## What's verified, and what genuinely isn't yet

Everything in `security/`, `ingestion/`, and `tests/` runs with zero
external dependencies beyond the Python standard library — no network,
no JVM, no real cluster. All 19 tests pass, including the full
end-to-end demonstration (100 entities, 574 total cells, three
different clearance levels tested, zero classified values ever
appearing in the Elasticsearch projection).

`fusion/spark_fusion_job.py`, `fusion/es_projection_writer.py`, and
`docker/docker-compose.yml` are **correct code and configuration
written against each system's documented API and image, but have not
been executed** — no Spark, Accumulo, Elasticsearch, Kafka, NiFi,
ZooKeeper, or Hadoop cluster exists in this project's development
environment. This is a meaningfully larger unverified surface than any
prior project in this portfolio (which could at minimum push to a
free, fast-to-provision Hugging Face Space) — running this stack for
real requires a genuinely provisioned multi-service host, not a few
minutes of CI time. See `docs/architecture.md` for exactly what
running and verifying this stack for real would involve.
