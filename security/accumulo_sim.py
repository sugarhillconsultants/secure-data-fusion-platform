"""
security/accumulo_sim.py

An in-memory simulation of Accumulo's core security behavior: a sorted
table of cells, where scan() enforces the caller's authorizations
against each cell's visibility expression using security/visibility.py.
This is NOT a replacement for real Accumulo (see docker/ for the real
deployment) — it exists so the actual authorization LOGIC this project
depends on can be verified with fast, deterministic, dependency-free
tests, independent of standing up a real multi-node cluster.

Also contains project_to_elasticsearch(): the function responsible for
this project's central security guarantee — it must NEVER include a
cell whose visibility expression contains anything other than exactly
"U". This is deliberately conservative (not "anything the caller could
see") because the ES/Kibana hunt layer is meant to be reachable by
analysts with NO clearance verification at query time — unlike
Accumulo, which checks authorizations on every single scan.
"""

# Same Python 3.8 compatibility fix as ingestion/generator.py — see
# that file's comment and docs/incidents.md for the full explanation.
from __future__ import annotations

from ingestion.generator import Cell
from security.visibility import evaluate_visibility


class AccumuloTableSim:
    def __init__(self):
        self._cells: list[Cell] = []

    def write(self, cells: list[Cell]):
        self._cells.extend(cells)

    def scan(self, authorizations: set[str], row: str | None = None) -> list[Cell]:
        """Returns only cells the given authorizations satisfy — the
        core behavior this whole project's security claim rests on.
        Optionally filtered to a single row."""
        results = []
        for cell in self._cells:
            if row is not None and cell.row != row:
                continue
            if evaluate_visibility(cell.column_visibility, authorizations):
                results.append(cell)
        return results

    def all_rows(self) -> set[str]:
        return set(c.row for c in self._cells)

    def __len__(self):
        return len(self._cells)


def project_to_elasticsearch(table: AccumuloTableSim) -> list[dict]:
    """The security-critical projection: builds ES-ready documents
    containing ONLY cells whose visibility is exactly 'U'. Deliberately
    does NOT use scan(authorizations={'U'}) — that would incorrectly
    admit any cell an OR-expression like 'U|SOMETHING_ELSE' satisfies
    for a U-holding user, which is not the same guarantee as "this cell
    IS the U-only unclassified data." The projection must be exact-match
    on the label, not authorization-satisfaction, since ES has no
    per-query authorization check at all once data lands there."""
    exactly_u_cells = [c for c in table._cells if c.column_visibility == "U"]

    docs_by_row: dict[str, dict] = {}
    for cell in exactly_u_cells:
        doc = docs_by_row.setdefault(cell.row, {"entity_id": cell.row})
        field_name = f"{cell.column_family}.{cell.column_qualifier}"
        doc[field_name] = cell.value

    return list(docs_by_row.values())
