"""
fusion/generate_and_land_data.py

Generates the synthetic CTI dataset (reusing ingestion/generator.py's
already-verified logic — 19 tests, see tests/test_security.py) and
lands it in HDFS as Parquet, at the exact paths
fusion/spark_fusion_job.py expects to read from:
  hdfs:///bronze/sensor_enrichment/
  hdfs:///bronze/analyst_intel/

This is the step that stands in for NiFi's real job in this project's
target architecture (see docs/architecture.md's batch-first scoping
note) — NiFi would normally land real source data here continuously;
this script exists to populate that same landing zone with realistic
synthetic data so the fusion job has something real to read.

Run via spark-submit from spark-master:
  docker compose exec spark-master spark-submit /opt/fusion/generate_and_land_data.py

NOT YET EXECUTED — this is genuinely the first attempt at running any
PySpark job in this project, on infrastructure (spark-master/worker)
that itself has never been brought up before today.
"""

import sys
import os

from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, LongType

# generator.py has zero external dependencies beyond the Python
# standard library (confirmed by this project's own test suite), so
# it can run directly inside the Spark driver without needing any
# additional packages installed in the Spark image.
sys.path.insert(0, "/opt")
from ingestion.generator import generate_dataset

HDFS_SENSOR_PATH = "hdfs:///bronze/sensor_enrichment/"
HDFS_INTEL_PATH = "hdfs:///bronze/analyst_intel/"

CELL_SCHEMA = StructType([
    StructField("row", StringType(), False),
    StructField("column_family", StringType(), False),
    StructField("column_qualifier", StringType(), False),
    StructField("column_visibility", StringType(), False),
    StructField("timestamp", LongType(), False),
    StructField("value", StringType(), False),
])


def cells_to_rows(cells):
    """Converts the dataclass Cell objects from generator.py into plain
    tuples matching CELL_SCHEMA's column order."""
    return [(c.row, c.column_family, c.column_qualifier, c.column_visibility, c.timestamp, c.value)
            for c in cells]


def main():
    spark = SparkSession.builder.appName("generate-and-land-cti-data").getOrCreate()

    sensor_cells, intel_cells = generate_dataset(n_entities=200, seed=42)
    print(f"Generated {len(sensor_cells)} sensor/enrichment cells and {len(intel_cells)} analyst/HUMINT cells")

    sensor_df = spark.createDataFrame(cells_to_rows(sensor_cells), schema=CELL_SCHEMA)
    intel_df = spark.createDataFrame(cells_to_rows(intel_cells), schema=CELL_SCHEMA)

    sensor_df.write.mode("overwrite").parquet(HDFS_SENSOR_PATH)
    intel_df.write.mode("overwrite").parquet(HDFS_INTEL_PATH)

    print(f"Landed sensor/enrichment data at {HDFS_SENSOR_PATH} ({sensor_df.count()} rows)")
    print(f"Landed analyst/intel data at {HDFS_INTEL_PATH} ({intel_df.count()} rows)")

    spark.stop()


if __name__ == "__main__":
    main()
