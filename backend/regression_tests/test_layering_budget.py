"""Exercise the real CLI against tiny packages, including its exit status."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_layering.py"


def run_gate(tmp_path, sources, budget=None):
    root = tmp_path / "app"
    root.mkdir()
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(budget or {"violations": 0, "deferred_imports": 0, "cycles": 0}))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--app-root", str(root), "--fail-on", "0", "--baseline", str(baseline)],
        capture_output=True, text=True,
    )


def test_deferred_import_budget_is_enforced(tmp_path):
    result = run_gate(tmp_path, {"application/a.py": "def f():\n from app.domain import b\n", "domain/b.py": ""})
    assert result.returncode == 1, result.stderr
    assert "deferred_imports" in result.stdout


def test_cycle_budget_is_enforced(tmp_path):
    result = run_gate(tmp_path, {"domain/a.py": "import app.domain.b", "domain/b.py": "import app.domain.a"})
    assert result.returncode == 1, result.stderr


def test_illegal_dependency_is_enforced(tmp_path):
    result = run_gate(tmp_path, {"domain/a.py": "import app.api.b", "api/b.py": ""})
    assert result.returncode == 1, result.stderr


def test_parse_failure_is_tool_error(tmp_path):
    result = run_gate(tmp_path, {"domain/a.py": "def broken("})
    assert result.returncode == 2
    assert "parse errors" in result.stdout


@pytest.mark.parametrize("contents", ["not json", "{}", '{"violations": 0, "deferred_imports": -1, "cycles": 0}'])
def test_invalid_baseline_is_tool_error(tmp_path, contents):
    root = tmp_path / "app"
    root.mkdir()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(contents)
    result = subprocess.run([sys.executable, str(SCRIPT), "--app-root", str(root), "--baseline", str(baseline)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "baseline" in result.stderr.lower()


def test_budgets_do_not_offset_each_other(tmp_path):
    result = run_gate(tmp_path, {"application/a.py": "def f():\n import app.domain.b", "domain/b.py": ""}, {"violations": 20, "deferred_imports": 0, "cycles": 10})
    assert result.returncode == 1


def test_within_budget_passes(tmp_path):
    result = run_gate(tmp_path, {"application/a.py": "def f():\n import app.domain.b", "domain/b.py": ""}, {"violations": 0, "deferred_imports": 1, "cycles": 0})
    assert result.returncode == 0, result.stderr


def test_same_count_with_unapproved_deferred_dependency_fails(tmp_path):
    result = run_gate(tmp_path, {"application/a.py": "def f():\n import app.domain.b", "domain/b.py": ""}, {
        "violations": 0, "deferred_imports": 1, "cycles": 0,
        "approved_deferred_edges": ["app.application.a -> app.domain.c"],
    })
    assert result.returncode == 1
    assert "unapproved" in result.stdout


@pytest.mark.parametrize("sources", [
    {"domain/a.py": "import app.api.b", "api/b.py": ""},
    {"domain/a.py": "import app.domain.b", "domain/b.py": "import app.domain.a"},
])
def test_baseline_cannot_approve_violations_or_cycles(tmp_path, sources):
    result = run_gate(tmp_path, sources, {"violations": 10, "deferred_imports": 10, "cycles": 10})
    assert result.returncode == 1


def test_missing_baseline_is_tool_error(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    result = subprocess.run([sys.executable, str(SCRIPT), "--app-root", str(root), "--baseline", str(tmp_path / "missing.json")], capture_output=True, text=True)
    assert result.returncode == 2
    assert "baseline" in result.stderr


def test_baseline_update_does_not_accept_parse_errors(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "broken.py").write_text("def (")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("original")
    result = subprocess.run([sys.executable, str(SCRIPT), "--app-root", str(root), "--update-baseline", "--baseline", str(baseline)], capture_output=True, text=True)
    assert result.returncode == 2
    assert baseline.read_text() == "original"


@pytest.mark.parametrize("replacement", ["from app.domain import c", "from app.domain import b, c"])
def test_from_import_targets_require_approval_without_count_growth(tmp_path, replacement):
    root = tmp_path / "app"
    (root / "domain").mkdir(parents=True)
    (root / "application").mkdir()
    for name in ("__init__", "b", "c"):
        (root / f"domain/{name}.py").write_text("")
    source = root / "application/a.py"
    source.write_text("def f():\n from app.domain import b\n")
    baseline = tmp_path / "baseline.json"
    command = [sys.executable, str(SCRIPT), "--app-root", str(root), "--baseline", str(baseline)]
    approved = subprocess.run(command + ["--update-baseline"], capture_output=True, text=True)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    source.write_text("def f():\n " + replacement + "\n")
    result = subprocess.run(command, capture_output=True, text=True)
    assert "deferred_imports=1" in result.stdout
    assert result.returncode == 1, result.stdout + result.stderr
    assert "unapproved" in result.stdout


def test_from_import_child_modules_participate_in_cycle_detection(tmp_path):
    result = run_gate(tmp_path, {
        "domain/__init__.py": "",
        "domain/a.py": "from app.domain import b",
        "domain/b.py": "from app.domain import a",
    })
    assert result.returncode == 1, result.stdout + result.stderr
    assert "import_cycles=1" in result.stdout


@pytest.mark.parametrize("original,replacement,expected", [
    ("from ..domain import b", "from ..domain import c", 1),
    ("from ..domain import b", "from ..domain import b, c", 1),
    ("value = 1", "from ..domain import b", 1),
    ("from app.domain import b as first", "from app.domain import b as second", 0),
    ("from ..domain import b as first", "from ..domain import b as second", 0),
])
def test_deferred_import_fingerprint_includes_relative_targets_but_not_asname(tmp_path, original, replacement, expected):
    root = tmp_path / "app"
    (root / "application").mkdir(parents=True)
    source = root / "application/a.py"
    source.write_text("def f():\n " + original)
    baseline = tmp_path / "baseline.json"
    command = [sys.executable, str(SCRIPT), "--app-root", str(root), "--baseline", str(baseline)]
    approved = subprocess.run(command + ["--update-baseline"], capture_output=True, text=True)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    source.write_text("def f():\n " + replacement)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("original,replacement", [
    ("value = 1", "from . import b"),
    ("from . import b", "from . import c"),
    ("from . import b", "from . import b, c"),
])
def test_new_top_level_relative_imports_are_rejected_conservatively(tmp_path, original, replacement):
    root = tmp_path / "app"
    (root / "domain").mkdir(parents=True)
    source = root / "domain/a.py"
    source.write_text(original)
    baseline = tmp_path / "baseline.json"
    command = [sys.executable, str(SCRIPT), "--app-root", str(root), "--baseline", str(baseline)]
    assert subprocess.run(command + ["--update-baseline"], capture_output=True).returncode == 0
    source.write_text(replacement)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "unapproved relative" in result.stdout


@pytest.mark.parametrize('sources', [
    {'domain/a.py': 'from . import b', 'domain/b.py': 'from . import a'},
    {'domain/a.py': 'def f():\n from .b import value', 'domain/b.py': 'from .a import f\nvalue=1'},
    {'domain/__init__.py': 'from . import a', 'domain/a.py': 'from .. import domain'},
])
def test_relative_dependencies_participate_in_cycle_detection(tmp_path, sources):
    result = run_gate(tmp_path, sources)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'import_cycles=1' in result.stdout


def test_relative_dependency_cannot_hide_illegal_layer_direction(tmp_path):
    result = run_gate(tmp_path, {'domain/a.py': 'from ..api.b import value', 'api/b.py': 'value=1'})
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'violations=1' in result.stdout
