"""
ingestion/generator.py

Generates synthetic Cyber Threat Intelligence data mirroring Accumulo's
actual Key/Value model:
  Row : Column Family : Column Qualifier : Column Visibility [Timestamp] -> Value

Two source datasets, joined later by the fusion job on the shared Row
(entity ID) — this is the batch-fusion MVP; a follow-on real-time
version would have NiFi/Kafka route each source's updates as they
arrive instead of pre-generating both datasets up front.

  1. sensor_enrichment_feed: unclassified network telemetry + automated
     enrichment (GeoIP, ASN, reputation). Everything here is "U".
  2. analyst_intel_feed: classified analyst attribution and HUMINT
     corroboration for a SUBSET of the same entities. This is the
     data that must never leak into the unclassified hunt view.

The whole point: after fusion, a single Row (one indicator) has cells
at genuinely different classification levels — which is exactly what
Accumulo's cell-level visibility model exists to handle, and what a
row-level or table-level access control scheme cannot.
"""

# Confirmed necessary the hard way: this project's own type hints
# (list[Cell], tuple[list[Cell], list[Cell]], etc.) use PEP 585
# lowercase generic subscripting, which only works natively in Python
# 3.9+. The apache/spark:3.5.0 image bundles Python 3.8.10 — running
# this file there failed immediately with "TypeError: 'type' object is
# not subscriptable" the moment Python tried to actually evaluate
# `list[Cell]` as a real expression at import time. This future-import
# makes all annotations lazily-evaluated strings instead, sidestepping
# the incompatibility with zero changes to actual logic. See
# docs/incidents.md.
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Cell:
    row: str
    column_family: str
    column_qualifier: str
    column_visibility: str
    timestamp: int
    value: str


THREAT_ACTOR_CLUSTERS = ["APT-style-cluster-A", "APT-style-cluster-B", "criminal-syndicate-C"]
ASNS = ["AS14061", "AS16509", "AS8075", "AS4837"]
COUNTRIES = ["RU", "CN", "KP", "IR", "unknown"]


def _synthetic_ip(rng: random.Random) -> str:
    return f"{rng.randint(1,223)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"


def generate_sensor_enrichment_feed(n_entities: int, seed: int = 42) -> list[Cell]:
    """Unclassified telemetry + automated enrichment. This is the ONLY
    feed permitted to reach the Elasticsearch hunt layer unmodified."""
    rng = random.Random(seed)
    cells = []
    base_ts = 1700000000

    for i in range(n_entities):
        row = _synthetic_ip(rng)
        ts = base_ts + i * 60

        cells.append(Cell(row, "sensor", "connection_count", "U", ts, str(rng.randint(1, 500))))
        cells.append(Cell(row, "sensor", "first_seen", "U", ts, str(ts)))
        cells.append(Cell(row, "enrichment", "asn", "U", ts, rng.choice(ASNS)))
        cells.append(Cell(row, "enrichment", "country", "U", ts, rng.choice(COUNTRIES)))
        cells.append(Cell(row, "enrichment", "reputation_score", "U", ts, str(round(rng.random(), 2))))

    return cells


def generate_analyst_intel_feed(sensor_rows: list[str], fraction_with_intel: float = 0.3, seed: int = 43) -> list[Cell]:
    """Classified analyst attribution + HUMINT corroboration, for a
    SUBSET of the entities already seen in the sensor feed (analysts
    don't have classified intel on every observed IP — only some).
    This models the realistic fusion scenario: most entities are
    unclassified-only; a meaningful minority also carry classified
    cells layered on top of the SAME row."""
    rng = random.Random(seed)
    cells = []
    base_ts = 1700000000

    n_with_intel = max(1, int(len(sensor_rows) * fraction_with_intel))
    rows_with_intel = rng.sample(sensor_rows, n_with_intel)

    for i, row in enumerate(rows_with_intel):
        ts = base_ts + i * 60

        # Attribution: releasable to Five Eyes partners
        cells.append(Cell(
            row, "attribution", "attributed_actor", "S&REL_TO_FVEY", ts,
            rng.choice(THREAT_ACTOR_CLUSTERS),
        ))
        cells.append(Cell(
            row, "attribution", "confidence", "S&REL_TO_FVEY", ts,
            rng.choice(["low", "moderate", "high"]),
        ))

        # HUMINT corroboration: highest classification, source-derived,
        # not releasable to foreign partners at all
        if rng.random() < 0.4:  # not every attributed entity has HUMINT backing
            cells.append(Cell(
                row, "humint", "corroborating_report", "TS&SI&NOFORN", ts,
                f"Source-derived corroboration, report ref R{rng.randint(1000,9999)}",
            ))

    return cells


def generate_dataset(n_entities: int = 200, seed: int = 42) -> tuple[list[Cell], list[Cell]]:
    """Convenience entry point: generates both feeds with consistent,
    overlapping entity IDs so the fusion job has something real to join."""
    sensor_cells = generate_sensor_enrichment_feed(n_entities, seed=seed)
    sensor_rows = sorted(set(c.row for c in sensor_cells))
    intel_cells = generate_analyst_intel_feed(sensor_rows, seed=seed + 1)
    return sensor_cells, intel_cells
