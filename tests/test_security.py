"""
tests/test_security.py

Formal test suite mirroring every manually-verified scenario from
development, including the constructed counter-example proving why
project_to_elasticsearch() uses exact-match rather than a naive
authorization-satisfying scan — see docs/incidents.md for why that
distinction matters and how it was found.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from security.visibility import evaluate_visibility, parse_visibility, VisibilityParseError
from ingestion.generator import Cell, generate_dataset, generate_sensor_enrichment_feed, generate_analyst_intel_feed
from security.accumulo_sim import AccumuloTableSim, project_to_elasticsearch


# --- security/visibility.py ---

def test_single_label_held():
    assert evaluate_visibility("U", {"U"}) is True

def test_single_label_not_held_denies():
    assert evaluate_visibility("TS", {"U", "S"}) is False

def test_and_requires_all_labels():
    assert evaluate_visibility("S&REL_TO_FVEY", {"S", "REL_TO_FVEY"}) is True
    assert evaluate_visibility("S&REL_TO_FVEY", {"S"}) is False
    assert evaluate_visibility("S&REL_TO_FVEY", {"REL_TO_FVEY"}) is False

def test_or_requires_at_least_one():
    assert evaluate_visibility("S|TS", {"S"}) is True
    assert evaluate_visibility("S|TS", {"U"}) is False

def test_humint_requires_all_three_compartments():
    assert evaluate_visibility("TS&SI&NOFORN", {"TS", "SI", "NOFORN"}) is True
    assert evaluate_visibility("TS&SI&NOFORN", {"TS", "SI"}) is False  # missing NOFORN

def test_parentheses_grouping():
    assert evaluate_visibility("(S&REL_TO_FVEY)|TS", {"TS"}) is True
    assert evaluate_visibility("(S&REL_TO_FVEY)|TS", {"S"}) is False

def test_mixed_operators_without_parens_rejected():
    try:
        parse_visibility("A&B|C")
        assert False, "should have raised"
    except VisibilityParseError:
        pass

def test_empty_authorizations_denies_everything():
    assert evaluate_visibility("U", set()) is False

def test_malformed_expressions_raise():
    for bad in ["", "(A", "A)", "&A", "A&"]:
        try:
            parse_visibility(bad)
            assert False, f"'{bad}' should have raised"
        except VisibilityParseError:
            pass


# --- ingestion/generator.py ---

def test_sensor_feed_is_entirely_unclassified():
    cells, _ = generate_dataset(n_entities=50, seed=42)
    assert all(c.column_visibility == "U" for c in cells)

def test_intel_feed_rows_are_subset_of_sensor_rows():
    sensor_cells, intel_cells = generate_dataset(n_entities=50, seed=42)
    sensor_rows = set(c.row for c in sensor_cells)
    intel_rows = set(c.row for c in intel_cells)
    assert intel_rows.issubset(sensor_rows)
    assert 0 < len(intel_rows) < len(sensor_rows)

def test_intel_feed_never_contains_u():
    _, intel_cells = generate_dataset(n_entities=50, seed=42)
    assert "U" not in set(c.column_visibility for c in intel_cells)

def test_generation_is_deterministic():
    s1, i1 = generate_dataset(n_entities=30, seed=7)
    s2, i2 = generate_dataset(n_entities=30, seed=7)
    assert s1 == s2
    assert i1 == i2

def test_all_generated_labels_are_parseable():
    sensor_cells, intel_cells = generate_dataset(n_entities=30, seed=42)
    for cell in sensor_cells + intel_cells:
        parse_visibility(cell.column_visibility)  # raises if malformed


# --- security/accumulo_sim.py ---

def test_scan_enforces_visibility():
    table = AccumuloTableSim()
    table.write([
        Cell("row1", "cf", "cq", "U", 1700000000, "public_value"),
        Cell("row1", "cf2", "cq2", "TS&SI&NOFORN", 1700000000, "secret_value"),
    ])
    u_only = table.scan(authorizations={"U"})
    assert len(u_only) == 1
    assert u_only[0].value == "public_value"

def test_fully_cleared_sees_everything():
    table = AccumuloTableSim()
    table.write([
        Cell("row1", "cf", "cq", "U", 1700000000, "a"),
        Cell("row1", "cf2", "cq2", "TS&SI&NOFORN", 1700000000, "b"),
    ])
    everything = table.scan(authorizations={"U", "TS", "SI", "NOFORN"})
    assert len(everything) == 2

def test_projection_excludes_mixed_classification_cell():
    """The core, constructed proof: a cell labeled 'U|TS' would be
    admitted by a naive scan(authorizations={'U'}) (since U alone
    satisfies the OR), but project_to_elasticsearch()'s exact-match
    design must still exclude it — this is the whole point of using
    exact-match rather than authorization-satisfaction for the
    ES projection. See docs/incidents.md."""
    table = AccumuloTableSim()
    table.write([Cell("row1", "test", "field", "U|TS", 1700000000, "mixed_label_value")])

    naive_scan_result = table.scan(authorizations={"U"})
    assert len(naive_scan_result) == 1  # confirms the naive approach WOULD leak this

    projection = project_to_elasticsearch(table)
    assert len(projection) == 0  # the real function correctly excludes it

def test_projection_never_leaks_classified_values():
    """The full end-to-end security guarantee, run against a realistic
    dataset size."""
    sensor_cells, intel_cells = generate_dataset(n_entities=100, seed=42)
    table = AccumuloTableSim()
    table.write(sensor_cells)
    table.write(intel_cells)

    classified_values = set(c.value for c in intel_cells)
    es_docs = project_to_elasticsearch(table)

    for doc in es_docs:
        for value in doc.values():
            assert value not in classified_values, f"Classified value leaked into ES: {value}"

def test_projection_row_count_matches_sensor_entities():
    sensor_cells, intel_cells = generate_dataset(n_entities=100, seed=42)
    table = AccumuloTableSim()
    table.write(sensor_cells)
    table.write(intel_cells)
    es_docs = project_to_elasticsearch(table)
    assert len(es_docs) == len(set(c.row for c in sensor_cells))
