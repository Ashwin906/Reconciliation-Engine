"""
Build-breaking guard for DECISIONS.md D-02. Parses every .py file in
verify/ and fails if any of them import match/ or agent/ -- the two
modules whose logic verify/ must never share, because sharing logic
would defeat the entire purpose of an independent check (see this
module's own docstring in recompute.py).

This lives inside verify/ itself (not tests/) so it travels with the
module it protects and is impossible to accidentally skip by running
`pytest tests/` without also picking this file up -- pytest discovers
test_*.py files regardless of directory by default.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = ("match", "agent")

VERIFY_DIR = Path(__file__).parent


def _imported_top_level_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:  # None for a bare "from . import x" relative import
                modules.add(node.module.split(".")[0])
    return modules


def test_verify_package_never_imports_match_or_agent():
    violations: dict[str, set[str]] = {}
    for py_file in VERIFY_DIR.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        imported = _imported_top_level_modules(source)
        forbidden_found = imported & set(FORBIDDEN_MODULES)
        if forbidden_found:
            violations[py_file.name] = forbidden_found

    assert not violations, (
        f"verify/ must never import match/ or agent/ (DECISIONS.md D-02) -- "
        f"found forbidden imports: {violations}"
    )


def test_the_guard_itself_actually_detects_a_violation():
    """A guard that can't fail isn't proving anything. This confirms
    _imported_top_level_modules genuinely flags a forbidden import,
    rather than the test above passing merely because the detection
    logic is silently broken."""
    fake_source = "import match\nfrom agent.client import GroqClient\n"
    modules = _imported_top_level_modules(fake_source)
    assert "match" in modules
    assert "agent" in modules


def test_the_guard_does_not_flag_allowed_imports():
    """Equally important: proves the guard isn't so broad it would flag
    core/ or gen.models, which verify/ is explicitly allowed to use
    (DECISIONS.md D-02, revised)."""
    fake_source = "from core.money import Paise\nfrom gen.models import Dataset\n"
    modules = _imported_top_level_modules(fake_source)
    assert modules == {"core", "gen"}
    assert not (modules & set(FORBIDDEN_MODULES))
