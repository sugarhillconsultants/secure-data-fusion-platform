"""
security/visibility.py

Implements Accumulo's ColumnVisibility authorization model: each cell
carries a boolean expression over classification/compartment labels
(e.g. "S&REL_TO_FVEY", "TS&SI&NOFORN", "U"), and a user's access is
granted only if their set of held authorizations satisfies that
expression. This is the actual security mechanism this entire project
exists to demonstrate — get this wrong, and the whole "cell-level
security" story is just marketing language over an unenforced label.

Grammar (matches Accumulo's real ColumnVisibility syntax):
  expression := term (('&' | '|') term)*
  term       := label | '(' expression ')'
  label      := alphanumeric/underscore/hyphen string

'&' = AND (user must hold ALL of these labels)
'|' = OR  (user must hold AT LEAST ONE of these labels)
Parentheses group sub-expressions. '&' and '|' cannot be mixed at the
same grouping level without parentheses (matching Accumulo's own rule
that "A&B|C" is rejected as ambiguous — you must write "(A&B)|C" or
"A&(B|C)" explicitly).
"""

# Same Python 3.8 compatibility fix as ingestion/generator.py — see
# that file's comment and docs/incidents.md for the full explanation.
# Not currently exercised under Python 3.8 (nothing in today's Spark
# pipeline imports this module), but fixed proactively since the cost
# is zero and the alternative is hitting this same landmine again
# later.
from __future__ import annotations

import re
from dataclasses import dataclass


class VisibilityParseError(ValueError):
    pass


@dataclass
class Node:
    """A parsed visibility expression node. Either a leaf (a single
    label) or an internal node (AND/OR over child nodes)."""
    op: str  # 'LABEL', 'AND', or 'OR'
    label: str | None = None
    children: list["Node"] | None = None


_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def _tokenize(expr: str) -> list[str]:
    tokens = []
    i = 0
    current_label = ""
    for ch in expr:
        if ch in "&|()":
            if current_label:
                tokens.append(current_label)
                current_label = ""
            tokens.append(ch)
        elif ch.isspace():
            if current_label:
                tokens.append(current_label)
                current_label = ""
        else:
            current_label += ch
        i += 1
    if current_label:
        tokens.append(current_label)
    return tokens


def parse_visibility(expr: str) -> Node:
    """Parses a visibility expression string into a Node tree.
    Raises VisibilityParseError on malformed input, including the
    "mixed operators without parentheses" case Accumulo itself rejects."""
    expr = expr.strip()
    if not expr:
        raise VisibilityParseError("Empty visibility expression")

    tokens = _tokenize(expr)
    pos = [0]  # mutable position counter, shared across recursive calls

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def consume():
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def parse_term() -> Node:
        tok = peek()
        if tok is None:
            raise VisibilityParseError("Unexpected end of expression")
        if tok == "(":
            consume()
            node = parse_expression()
            if peek() != ")":
                raise VisibilityParseError(f"Expected ')' in: {expr}")
            consume()
            return node
        if tok in ("&", "|", ")"):
            raise VisibilityParseError(f"Unexpected token '{tok}' in: {expr}")
        if not _LABEL_PATTERN.match(tok):
            raise VisibilityParseError(f"Invalid label '{tok}' in: {expr}")
        consume()
        return Node(op="LABEL", label=tok)

    def parse_expression() -> Node:
        left = parse_term()
        op_seen = None
        children = [left]

        while peek() in ("&", "|"):
            op_tok = consume()
            op = "AND" if op_tok == "&" else "OR"
            if op_seen is not None and op != op_seen:
                raise VisibilityParseError(
                    f"Mixed '&' and '|' without parentheses in: {expr} "
                    f"(Accumulo requires explicit grouping, e.g. '(A&B)|C')"
                )
            op_seen = op
            children.append(parse_term())

        if len(children) == 1:
            return children[0]
        return Node(op=op_seen, children=children)

    result = parse_expression()
    if pos[0] != len(tokens):
        raise VisibilityParseError(f"Unexpected trailing tokens in: {expr}")
    return result


def evaluate_visibility(expr: str, user_authorizations: set[str]) -> bool:
    """Returns True if user_authorizations satisfies the visibility
    expression — i.e., whether this user is permitted to see a cell
    carrying this label."""
    tree = parse_visibility(expr)

    def eval_node(node: Node) -> bool:
        if node.op == "LABEL":
            return node.label in user_authorizations
        elif node.op == "AND":
            return all(eval_node(child) for child in node.children)
        elif node.op == "OR":
            return any(eval_node(child) for child in node.children)
        raise VisibilityParseError(f"Unknown node op: {node.op}")

    return eval_node(tree)
