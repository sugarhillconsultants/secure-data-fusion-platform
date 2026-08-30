"""
fusion/spark_fusion_job.py

The batch fusion job: reads the two source feeds (landed in HDFS by
NiFi — see docker/nifi-flow.xml), and writes the resulting cells into
Accumulo using the live BatchWriter API via Accumulo's Python client.

SCOPING NOTE, stated honestly up front: production-scale Accumulo
ingestion normally uses bulk-loaded RFiles (Accumulo's native sorted
file format, generated offline and imported directly into tablet
servers) rather than the live BatchWriter API, since bulk-load avoids
per-write RPC overhead at high volume. This demo uses BatchWriter
instead — it's simpler to reason about and verify correctness for a
project this size, and the actual SECURITY logic (which cell gets
which visibility label) is identical either way. RFile bulk-load is
documented as the real next step for production scale in
docs/architecture.md, not attempted here.

This file has NOT been executed against a real Spark/Accumulo cluster
— no such cluster exists in this project's development environment.
The actual fusion LOGIC (which rows get which cells, which visibility
labels apply) is exactly the same logic already verified in
security/accumulo_sim.py and tests/test_security.py — this file's job
is just to move that same logic from an in-memory simulation to a real
distributed pipeline. Verify this against the real docker-compose
stack (see docker/) before trusting it beyond "the logic is right."
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyspark.sql import SparkSession
import pyaccumulo  # or the current Accumulo Python client for your cluster version


HDFS_SENSOR_PATH = "hdfs:///bronze/sensor_enrichment/"
HDFS_INTEL_PATH = "hdfs:///bronze/analyst_intel/"
ACCUMULO_TABLE = "cti_fusion"
ACCUMULO_ZOOKEEPERS = os.environ.get("ACCUMULO_ZOOKEEPERS", "zookeeper:2181")
ACCUMULO_USER = os.environ.get("ACCUMULO_USER", "root")
ACCUMULO_PASSWORD = os.environ.get("ACCUMULO_PASSWORD")  # set via secret, never hardcoded


def read_source_feeds(spark: SparkSession):
    """Reads both source feeds from their HDFS bronze landing zones.
    Expects each row to already carry row/column_family/column_qualifier/
    column_visibility/timestamp/value fields, matching ingestion/generator.py's
    Cell schema — NiFi's job is to land raw source data in exactly this
    shape (see docker/nifi-flow.xml), not to make fusion decisions itself."""
    sensor_df = spark.read.parquet(HDFS_SENSOR_PATH)
    intel_df = spark.read.parquet(HDFS_INTEL_PATH)
    return sensor_df, intel_df


def fuse(sensor_df, intel_df):
    """The actual fusion step. Since both feeds already carry their
    correct per-cell visibility labels at the source (this project's
    design deliberately does NOT derive classification during fusion —
    that decision belongs to the source system, not this pipeline),
    fusion here is simply a union keyed by row. Real deployments may
    need more sophisticated conflict resolution (e.g. two sources
    disagreeing on a field); not needed for this project's scope."""
    return sensor_df.unionByName(intel_df)


def write_to_accumulo(fused_df):
    """Writes each row to Accumulo via BatchWriter, preserving the
    per-cell visibility label exactly as it arrived. This is the
    single most important line in this entire file: the visibility
    label is NEVER modified, computed, or defaulted here — it passes
    through unchanged from the source feed straight into Accumulo's
    actual enforcement mechanism."""
    conn = pyaccumulo.Accumulo(
        host=ACCUMULO_ZOOKEEPERS.split(":")[0],
        port=9099,
        user=ACCUMULO_USER,
        password=ACCUMULO_PASSWORD,
    )

    if not conn.table_exists(ACCUMULO_TABLE):
        conn.create_table(ACCUMULO_TABLE)

    writer = conn.create_batch_writer(ACCUMULO_TABLE)

    # collect() pulls all fused rows to the driver for writing — fine
    # for this project's synthetic dataset size; a real production
    # volume would instead use foreachPartition with a writer per
    # executor, or switch to RFile bulk-load entirely (see scoping
    # note above).
    for row in fused_df.collect():
        mutation = pyaccumulo.Mutation(row.row)
        mutation.put(
            cf=row.column_family,
            cq=row.column_qualifier,
            cv=row.column_visibility,  # passed through unchanged — see docstring above
            ts=row.timestamp,
            val=row.value,
        )
        writer.add_mutation(mutation)

    writer.close()
    conn.close()


def main():
    spark = SparkSession.builder.appName("cti-fusion").getOrCreate()
    sensor_df, intel_df = read_source_feeds(spark)
    fused_df = fuse(sensor_df, intel_df)
    write_to_accumulo(fused_df)
    print(f"Fusion complete: {fused_df.count()} cells written to Accumulo table '{ACCUMULO_TABLE}'")
    spark.stop()


if __name__ == "__main__":
    main()
