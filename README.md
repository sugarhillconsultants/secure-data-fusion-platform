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

## Status: core security logic AND the real distributed stack both verified — including surviving a genuine restart

Everything in `security/`, `ingestion/`, and `tests/` runs with zero
external dependencies, verified with 19 passing tests (see
[`docs/incidents.md`](docs/incidents.md) #1-2 for two genuine findings
from that work). Beyond that: **ZooKeeper, HDFS, and Accumulo have now
actually been run for real**, on a live Azure VM — not just written
and assumed to work, and **confirmed to survive a genuine stop/restart
cycle end to end with zero manual intervention**, not just one
continuous session. Getting there took 17 distinct, real bugs across
two sessions, each found from an actual error and fixed in sequence —
a nonexistent Docker image, missing Hadoop XML config, a Docker
volume-ownership mismatch, a bash operator-precedence race condition,
Accumulo's own internal confirmation-prompt handling, a classic
`depends_on`-doesn't-mean-ready race, and — the deepest of all —
ZooKeeper silently having no persistent volume at all (see
[`docs/incidents.md`](docs/incidents.md) #3 and #4 for the full
account). Confirmed independently: HDFS's own JMX endpoint reporting
`"State": "active"`, a direct HDFS filesystem listing showing genuine
Accumulo data files and write-ahead logs, and — after the restart
fixes — Accumulo's own crash-recovery log messages confirming its
internal consistency checks passed cleanly against the persisted data.
Elasticsearch and Kibana were also confirmed running and healthy
throughout. Kafka, NiFi,
Spark, and the actual Spark→Accumulo fusion job remain unexecuted —
see [`docs/architecture.md`](docs/architecture.md) for the precise,
current split.

## What's actually in this repo

| Path | What it does | Verified? |
|---|---|---|
| `security/visibility.py` | Accumulo-style visibility expression parser/evaluator | **Yes** — 9 tests |
| `ingestion/generator.py` | Synthetic CTI fusion data (sensor+enrichment, analyst+HUMINT) | **Yes** — 5 tests |
| `security/accumulo_sim.py` | In-memory access-control simulation + ES projection | **Yes** — 5 tests, including the full end-to-end security proof |
| `fusion/spark_fusion_job.py` | Real Spark batch fusion job, writes to Accumulo via BatchWriter | Correct code, not yet executed against the now-working cluster |
| `fusion/es_projection_writer.py` | Real Elasticsearch bulk-indexer | Correct code, not yet executed against the now-working cluster |
| `docker/docker-compose.yml` | Full 10-service topology (ZK, Kafka, NiFi, HDFS, Spark, Accumulo, ES, Kibana) | **ZooKeeper, HDFS, Accumulo, ES, Kibana: run for real, confirmed independently.** Kafka, NiFi, Spark: not yet run |
| `tests/test_security.py` | 19 tests, all passing | **Yes** |
| `docs/architecture.md` | Why Accumulo + ES, honest verified/unverified split | — |
| `docs/incidents.md` | 11+ real findings, including a full live debugging session on a real Azure VM | — |

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

## Running the full stack yourself

The trimmed core (ZooKeeper, HDFS, Accumulo, Elasticsearch, Kibana) is
**confirmed working** — the commands below are exactly what was run to
prove it, not a hopeful guess:

```bash
cd docker
cp .env.example .env   # fill in real passwords
git clone https://github.com/apache/accumulo-docker.git
cd accumulo-docker && docker build -t accumulo:2.1.2 . && cd ..
docker compose up -d zookeeper namenode datanode elasticsearch kibana accumulo
```

One manual step is still required after first boot — HDFS's own
permission model blocks Accumulo from creating its own directory
otherwise (see incident #3j):

```bash
docker compose exec namenode hdfs dfs -mkdir -p /accumulo
docker compose exec namenode hdfs dfs -chown accumulo:supergroup /accumulo
docker compose up -d accumulo   # retry after the chown
```

Kafka, NiFi, and Spark are defined in the same compose file but were
deliberately excluded from this first real test — bringing those up
too, and running the actual Spark fusion job against this now-working
cluster, is the real next step (see below).

## What I'd add next

1. **Run the actual `fusion/spark_fusion_job.py` and
   `fusion/es_projection_writer.py` against the now-working cluster** —
   the single biggest remaining gap. The infrastructure is proven; the
   application-level fusion logic running on top of it isn't yet.
2. Bring up Kafka, NiFi, and Spark (deliberately excluded from the
   first real test) and confirm they integrate correctly.
3. Start the Accumulo Monitor process (port 9995 came up on the
   network but nothing was listening, since only `manager`/`tserver`
   were launched — a known, minor, documented gap).
4. Real-time fusion via NiFi/Kafka routing live per-event updates,
   replacing the current batch-file approach.
5. RFile bulk-load, replacing BatchWriter for production-scale ingestion.
6. A Kibana dashboard definition (currently just the raw ES index — no
   actual hunt-analyst-facing visualization has been built yet).
