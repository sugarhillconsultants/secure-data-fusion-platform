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
| `docker/docker-compose.yml` — ZooKeeper, HDFS (namenode+datanode) | **Yes** — run for real on a live Azure VM; also **confirmed to survive a genuine stop/restart cycle end to end** (see `docs/incidents.md` #4), not just a single continuous session |
| `docker/docker-compose.yml` — Accumulo (init, manager, tserver) | **Yes** — run for real, confirmed independently via direct HDFS filesystem inspection showing genuine `.rf` data files and write-ahead logs; also confirmed to survive a genuine restart with zero manual intervention, after 17 total distinct real bugs found and fixed across two sessions (see `docs/incidents.md` #3 and #4) |
| `docker/docker-compose.yml` — Elasticsearch, Kibana | **Yes** — both running and healthy (`Cluster health status ... [GREEN]`) throughout the above |
| `docker/docker-compose.yml` — Kafka, NiFi, Spark, the Accumulo Monitor UI | **Not yet run** — this project's trimmed first tests deliberately excluded these; see "what's left" below |
| `fusion/spark_fusion_job.py` (Spark transform, writes fused TSV to HDFS) | Rewritten to remove a dependency on `pyaccumulo` (unverified maintenance status — see `docs/incidents.md`); correct code against documented Spark APIs, **not yet executed** |
| `fusion/AccumuloBulkWriter.java` (separate Java program, real BatchWriter API) | Written against Accumulo's current, documented `AccumuloClient` API — **not yet compiled or run**; no Java compiler exists in this project's development environment, so this has only been checked for balanced braces/parens |
| `fusion/es_projection_writer.py` (real ES bulk-indexer) | Correct code against documented API — **not yet executed against the now-working cluster** |

## What it would take to close the remaining gap

1. ~~Provision a host with enough resources~~ — **done**: a real Azure
   `Standard_D4s_v4` VM (4 vCPU, 16GB RAM).
2. ~~`docker compose up`, work through whatever breaks~~ — **done**:
   11 distinct real issues found and fixed in the first session, fully
   documented in `docs/incidents.md` #3.
3. ~~Confirm the stack survives a real restart, not just one continuous
   session~~ — **done**: a second session, after actually stopping and
   restarting the VM overnight, found and fixed 6 more distinct issues
   (`docs/incidents.md` #4), the most significant being that ZooKeeper
   had no persistent volume at all. Confirmed working: a full
   `docker compose down`/`up` cycle now succeeds with zero manual steps.
4. **Immediate next step**: compile and run `fusion/AccumuloBulkWriter.java`
   inside the `accumulo` container (see `fusion/README.md` for the exact
   commands), and run `fusion/spark_fusion_job.py` against this now-working,
   now-persistent cluster — confirming the resulting Accumulo table
   matches what the simulator already proved: three clearance levels
   tested, zero classified leakage into the real Elasticsearch index,
   not just the in-memory simulation.
5. **Still open**: bring up Kafka, NiFi, and Spark (deliberately
   excluded from the trimmed persistence tests) and confirm they
   integrate correctly with the now-proven ZooKeeper/HDFS/Accumulo core.
6. **Still open, minor**: the Accumulo Monitor web UI (port 9995) never
   came up, since the current startup command only launches `manager`
   and `tserver`. Not required to prove the core thesis, but a real,
   known gap worth closing for a fuller demo.
7. The real-time fusion evolution (NiFi/Kafka routing live per-event
   updates instead of batch files) and RFile bulk-load (replacing the
   BatchWriter approach for production-scale ingestion) remain the
   larger, longer-term next steps after that.
