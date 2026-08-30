# Architecture: Why This Design, and What's Verified vs. Not

## Why both Accumulo AND Elasticsearch — the actual thesis

Elasticsearch is fast and flexible but has no concept of per-cell,
per-query authorization enforcement. Accumulo is exactly the opposite:
built specifically so a single row can hold cells at genuinely
different classification levels, with every scan re-checking the
caller's authorizations against each cell's label. These aren't
redundant — they solve different problems:

- **Accumulo is the system of record.** It holds the FULL fused
  picture — sensor telemetry, automated enrichment, analyst
  attribution, HUMINT corroboration — all layered onto the same
  entity, with each cell's real classification enforced on every read.
- **Elasticsearch is the hunt layer.** It holds a deliberately narrow,
  pre-cleared **projection** — exactly and only the cells labeled `U` —
  because ES itself performs no authorization check at query time.
  Anyone who can reach Kibana can see everything in that index; the
  security boundary has to be enforced *before* data reaches ES, not
  by ES itself.

This is proven, not just asserted: `docs/incidents.md` documents a
constructed test case showing why the projection uses exact-match
rather than authorization-satisfaction, and
`tests/test_security.py::test_projection_never_leaks_classified_values`
runs the full pipeline against 100 synthetic entities and asserts zero
classified values ever reach the ES-bound documents.

## Batch-first, real-time-later — an honest scoping decision

This project's ingest/fusion pipeline is currently **batch**: Spark
reads two pre-generated source feeds and fuses them on a schedule. The
target architecture (NiFi + Kafka handling live, per-event fusion as
sources report in real time) is the natural evolution, not yet built.
Batch-first was chosen deliberately: it's the smaller, provably-correct
increment, and the actual SECURITY logic (which cell gets which
visibility label, how the ES projection filters) is identical either
way — moving to real-time changes how data arrives, not how it's
protected once it does.

## BatchWriter now, RFile bulk-load later — another honest scoping decision

Production Accumulo ingestion at real scale typically uses bulk-loaded
RFiles (Accumulo's native sorted file format, built offline and
imported directly into tablet servers) rather than live per-mutation
writes, since bulk-load avoids per-write RPC overhead. This project's
`fusion/spark_fusion_job.py` uses the live BatchWriter API instead —
simpler to reason about correctness for a project this size, and,
again, the actual visibility-label logic doesn't change between the
two approaches. RFile generation is real added complexity (correctly
sorting and formatting Accumulo's native file structure) that isn't
necessary to prove this project's core thesis.

## What's verified, what isn't — the honest split

| Component | Verified? |
|---|---|
| `security/visibility.py` (visibility expression parser/evaluator) | **Yes** — 9 tests, including safety-critical denial cases and Accumulo's real mixed-operator ambiguity rule |
| `ingestion/generator.py` (synthetic CTI fusion data) | **Yes** — 5 tests, confirms correct fusion scenario (entity overlap, correct classification distribution, determinism) |
| `security/accumulo_sim.py` (access-control simulation + ES projection) | **Yes** — 5 tests, including the constructed adversarial-cell proof and the full 100-entity end-to-end demonstration |
| `fusion/spark_fusion_job.py` (real Spark to Accumulo pipeline) | Correct code against documented APIs — **not executed**, no Spark/Accumulo cluster in dev environment |
| `fusion/es_projection_writer.py` (real ES bulk-indexer) | Correct code against documented API — **not executed**, no ES cluster in dev environment |
| `docker/docker-compose.yml` (full 10-service topology) | Valid YAML, documented images — **not executed**, no Docker/sufficient host in dev environment |

## What it would take to close this gap for real

1. Provision a host with enough resources to actually run this stack —
   realistically 16GB+ RAM, multiple cores; this is a genuinely heavier
   requirement than any prior project in this portfolio.
2. `docker compose up` from `docker/`, and work through whatever
   actually breaks — given this portfolio's own track record across
   five prior projects, expect real issues here, likely more than
   usual given how many services need to correctly discover and
   authenticate to each other (ZooKeeper coordination, Accumulo's
   HDFS/ZooKeeper dependencies, NiFi's HDFS write path).
3. Run `fusion/spark_fusion_job.py` against the real cluster, and
   confirm the resulting Accumulo table matches what the simulator
   already proved: three clearance levels tested, zero classified
   leakage into the real Elasticsearch index, not just the in-memory
   simulation.
4. Document whatever breaks and how it was fixed, the same way every
   other project in this portfolio has.
5. The real-time fusion evolution (NiFi/Kafka routing live per-event
   updates instead of batch files) and RFile bulk-load (replacing the
   BatchWriter approach for production-scale ingestion) remain the
   next real steps after that.
