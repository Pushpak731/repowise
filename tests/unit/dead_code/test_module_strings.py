"""A Python module named by a module-path string is used.

From repowise itself: ``cli/main.py`` loads ``"uninstall_cmd:uninstall_command"``
from ``cli/commands`` through ``import_module`` only when the command runs, and
``cli/ui/__init__.py`` maps names to ``"result_panels"`` for a lazy
``__getattr__``. Both modules were reported unreachable.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from repowise.core.analysis.dead_code.module_strings import drop_named_modules
from tests.unit.dead_code._helpers import _build_graph

_MAIN = b"""from importlib import import_module

_LAZY = (
    ("uninstall", "uninstall_cmd:uninstall_command"),
)

def load(name):
    module, attr = dict(_LAZY)[name].split(":")
    return getattr(import_module(f"repowise.cli.commands.{module}"), attr)
"""


def _finding(kind: DeadCodeKind, path: str, name: str | None = None) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=kind,
        file_path=path,
        symbol_name=name,
        symbol_kind="function" if name else None,
        confidence=0.4,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
    )


def _kept(findings, source, importers=()):
    return {(f.file_path, f.symbol_name) for f in drop_named_modules(findings, source, importers)}


_CMD = "pkg/repowise/cli/commands/uninstall_cmd.py"


def test_lazy_table_entry_in_a_runtime_importer_names_module_and_attribute():
    findings = [
        _finding(DeadCodeKind.UNREACHABLE_FILE, _CMD),
        _finding(DeadCodeKind.UNUSED_EXPORT, _CMD, "uninstall_command"),
        _finding(DeadCodeKind.UNUSED_EXPORT, _CMD, "helper"),
    ]
    source = {"pkg/repowise/cli/main.py": _MAIN, _CMD: b"def uninstall_command(): ...\n"}
    kept = _kept(findings, source, {"pkg/repowise/cli/main.py"})
    assert kept == {(_CMD, "helper")}


def test_a_bare_name_outside_a_runtime_importer_is_not_read_as_a_module():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, _CMD)]
    source = {"pkg/repowise/cli/main.py": b'LABEL = "uninstall_cmd"\n'}
    assert _kept(findings, source) == {(_CMD, None)}


def test_a_bare_name_only_reaches_modules_under_the_importer():
    other = "pkg/repowise/server/uninstall_cmd.py"
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, other)]
    assert _kept(findings, {"pkg/repowise/cli/main.py": _MAIN}, {"pkg/repowise/cli/main.py"}) == {
        (other, None)
    }


def test_a_dotted_entry_point_in_pyproject_names_the_module():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, "src/tool/cli.py")]
    source = {"pyproject.toml": b'[project.scripts]\ntool = "tool.cli:main"\n'}
    assert _kept(findings, source) == set()


def test_a_single_word_string_elsewhere_names_nothing():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, "src/tool/cli.py")]
    assert _kept(findings, {"README.md": b'run "cli" to start\n'}) == {("src/tool/cli.py", None)}


def test_the_analyzer_reads_a_string_map_in_a_lazy_package():
    init = b"""from importlib import import_module

_SUBMODULES = {"build_completion_panel": "result_panels"}

def __getattr__(name):
    return getattr(import_module(f"{__name__}.{_SUBMODULES[name]}"), name)
"""
    graph = _build_graph(
        {
            "cli/ui/__init__.py": {"symbols": []},
            "cli/ui/result_panels.py": {"symbols": []},
            "cli/ui/orphan.py": {"symbols": []},
        }
    )
    source = {
        "cli/ui/__init__.py": init,
        "cli/ui/result_panels.py": b"def build_completion_panel(): ...\n",
        "cli/ui/orphan.py": b"X = 1\n",
    }
    parsed = {path: SimpleNamespace(file_info=SimpleNamespace(abs_path=path)) for path in source}
    report = DeadCodeAnalyzer(graph, parsed_files=parsed, source_map=source).analyze(
        {"detect_unused_exports": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    files = {f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE}
    assert files == {"cli/ui/orphan.py"}
