"""
fusion/spark_fusion_job.py

The batch fusion job: reads the two source feeds (landed in HDFS by
NiFi — see docker/nifi-flow.xml), fuses them, and writes the result to
HDFS as TSV. A SEPARATE program (fusion/AccumuloBulkWriter.java) then
reads that TSV and performs the actual Accumulo writes.

WHY THIS SPLIT, stated honestly: the original version of this file
called `pyaccumulo` directly from PySpark — a third-party Python
wrapper whose maintenance status and compatibility with Accumulo 2.1.2
was never actually confirmed. Checking Accumulo's own documentation
directly surfaced two real findings: (1) `AccumuloOutputFormat` — the
Hadoop-native way Spark would normally write to Accumulo — has been
DEPRECATED since Accumulo 2.0.0, moved to a different package; and
(2) making PySpark specifically call the current, non-deprecated
Accumulo Java client requires a Java/Scala interop shim (PySpark's
`saveAsNewAPIHadoopFile` needs a Converter class to translate Python
tuples into Java `Mutation` objects) — real, added complexity and
failure surface for a Python-only project to take on blind.

The cleaner, more honest design: let Spark do what it's actually good
at (distributed read/transform/join), write the result to HDFS in a
plain, Java-trivial-to-parse format, and let a small, focused Java
program — using Accumulo's REAL, CURRENT, documented client API
(`AccumuloClient` + `BatchWriter`, not the deprecated 1.x
`ZooKeeperInstance`/`Connector` pattern) — handle the actual write.
See docs/architecture.md and docs/incidents.md for the full reasoning.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyspark.sql import SparkSession

HDFS_SENSOR_PATH = "hdfs:///bronze/sensor_enrichment/"
HDFS_INTEL_PATH = "hdfs:///bronze/analyst_intel/"
HDFS_FUSED_OUTPUT_PATH = "hdfs:///accumulo-staging/fused_cells"


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


def write_fused_output(fused_df):
    """Writes the fused cells to HDFS as tab-separated values — a
    format trivial for a plain Java program to parse (BufferedReader +
    split), deliberately avoiding the need for a Parquet-reading Java
    dependency on the other side of this split. Tab-separated (not
    comma) specifically because this project's actual field values
    (free-text log/CTI content) are far more likely to contain a comma
    than a literal tab character — a real, if small, correctness
    consideration for a demo-scoped format choice."""
    fused_df.select(
        "row", "column_family", "column_qualifier", "column_visibility", "timestamp", "value"
    ).write.mode("overwrite").option("sep", "\t").csv(HDFS_FUSED_OUTPUT_PATH)


def main():
    spark = SparkSession.builder.appName("cti-fusion").getOrCreate()
    sensor_df, intel_df = read_source_feeds(spark)
    fused_df = fuse(sensor_df, intel_df)
    write_fused_output(fused_df)
    print(f"Fusion complete: {fused_df.count()} cells written to {HDFS_FUSED_OUTPUT_PATH}")
    print(f"Next step: run AccumuloBulkWriter.java against this path to load into Accumulo.")
    spark.stop()


if __name__ == "__main__":
    main()
