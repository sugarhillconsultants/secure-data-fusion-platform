# Secure Data Fusion Platform

The sixth project in this portfolio, and the first to move from
cloud/LLM MLOps into distributed big-data/security-platform
engineering — directly reflecting real DoD/IC data-fusion experience
(Kafka, ZooKeeper, Hadoop, Accumulo, NiFi, Spark, attribute-based
access control) rather than starting a new, unrelated skill area.

## The actual thesis, provable in code

A single logical entity (a threat indicator) accumulates cells from
multiple sources at genuinely different classification levels —
unclassified sensor telemetry, unclassified automated enrichment,
Secret-level analyst attribution, and Top-Secret HUMINT
corroboration. **Accumulo** is the system of record, enforcing every
cell's real classification on every read. **Elasticsearch/Kibana** is
the hunt layer, holding only a deliberately narrow, pre-cleared
projection of the unclassified cells — because ES itself performs no
authorization check at query time.

This isn't asserted, it's proven: 19 tests, including a full
end-to-end run across 100 synthetic entities (574 total cells, three
different clearance levels tested against the same data) that asserts
**zero classified values ever appear in the Elasticsearch projection**.

## Status: core security logic fully verified — the distributed stack is correct but unexecuted

Everything in `security/`, `ingestion/`, and `tests/` runs with zero
external dependencies, verified with 19 passing tests (see
[`docs/incidents.md`](docs/incidents.md) for two genuine findings,
including a constructed adversarial test proving a specific design
choice matters). The real Spark/Accumulo/Elasticsearch pipeline and
the 10-service docker-compose stack are correct code and configuration
against documented APIs/images, but have **not been executed** — this
project's development environment has no Docker, no JVM, no cluster of
any kind. This is a meaningfully larger unverified surface than any
prior project in this portfolio. Full honest breakdown:
[`docs/architecture.md`](docs/architecture.md).

## What's actually in this repo

| Path | What it does | Verified? |
|---|---|---|
| `security/visibility.py` | Accumulo-style visibility expression parser/evaluator | **Yes** — 9 tests |
| `ingestion/generator.py` | Synthetic CTI fusion data (sensor+enrichment, analyst+HUMINT) | **Yes** — 5 tests |
| `security/accumulo_sim.py` | In-memory access-control simulation + ES projection | **Yes** — 5 tests, including the full end-to-end security proof |
| `fusion/spark_fusion_job.py` | Real Spark batch fusion job, writes to Accumulo via BatchWriter | Correct code, unexecuted |
| `fusion/es_projection_writer.py` | Real Elasticsearch bulk-indexer | Correct code, unexecuted |
| `docker/docker-compose.yml` | Full 10-service topology (ZK, Kafka, NiFi, HDFS, Spark, Accumulo, ES, Kibana) | Valid YAML, unexecuted |
| `tests/test_security.py` | 19 tests, all passing | **Yes** |
| `docs/architecture.md` | Why Accumulo + ES, honest verified/unverified split | — |
| `docs/incidents.md` | 2 real findings from building this project | — |

## Running the verified parts yourself

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from ingestion.generator import generate_dataset
from security.accumulo_sim import AccumuloTableSim, project_to_elasticsearch

sensor_cells, intel_cells = generate_dataset(n_entities=100, seed=42)
table = AccumuloTableSim()
table.write(sensor_cells)
table.write(intel_cells)

print('U-only analyst sees:', len(table.scan(authorizations={'U'})), 'cells')
print('Fully-cleared analyst sees:', len(table.scan(authorizations={'U','S','REL_TO_FVEY','TS','SI','NOFORN'})), 'cells')
print('ES projection contains:', len(project_to_elasticsearch(table)), 'documents, zero of them classified')
"
```

## Running the full stack (unexecuted by me — this is genuinely your first real test)

```bash
cd docker
cp .env.example .env   # fill in real passwords
docker compose up
```

Expect this to need real troubleshooting — see
[`docs/architecture.md`](docs/architecture.md)'s prioritized list of
what to check first.

## What I'd add next

1. **Actually run the docker-compose stack** and document what breaks —
   the single biggest gap, and likely the richest incident log entry
   this project will ever get, given how many services need to
   correctly discover and authenticate to each other.
2. Real-time fusion via NiFi/Kafka routing live per-event updates,
   replacing the current batch-file approach.
3. RFile bulk-load, replacing BatchWriter for production-scale ingestion.
4. A Kibana dashboard definition (currently just the raw ES index — no
   actual hunt-analyst-facing visualization has been built yet).
