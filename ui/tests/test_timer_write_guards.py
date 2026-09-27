"""Keep a completed/cancelled timer from being resurrected by a late writer."""

import ast
from pathlib import Path


def test_timer_updates_require_ownership_of_the_current_status():
    source = Path(__file__).resolve().parents[1] / "services" / "autowake.py"
    # AST folds adjacent SQL string literals without depending on formatting.
    statements = [
        node.value for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith("UPDATE timers SET")
    ]
    assert statements, "No timer updates found; review this regression guard"
    for sql in statements:
        head, _, where = sql.partition("WHERE")
        required = "status = 'pending'" if "status = 'running'" in head else "status = 'running'"
        assert required in where, f"Timer update can overwrite a terminal row: {sql}"
