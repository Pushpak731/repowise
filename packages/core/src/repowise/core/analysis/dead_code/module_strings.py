"""A Python module named by a module-path string is used.

Python loads code by dotted name as often as by ``import``: an entry-point
table (``"repowise.cli.main:cli"`` in ``pyproject.toml``), a Celery or Django
setting, a lazy command table that imports ``"uninstall_cmd:uninstall_command"``
only when the command runs, a package ``__getattr__`` mapping names to
``"result_panels"``. None of these leaves an import edge, so the module reads
as unreachable and the attribute after the colon as an unused export.

Two spellings count:

* a dotted name of two or more segments that is the tail of the module's own
  dotted path (``cli.commands.uninstall_cmd``), written in any indexed file;
* a bare module name, only inside a file that imports by name at runtime
  (an ``import_module`` caller), and only for a module in that file's
  directory or below it, which is where such a loader looks.

``module:attr`` additionally uses ``attr`` of that module. Each match drops the
finding: a string that resolves to the module is how the module is loaded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from ...ingestion.languages.registry import REGISTRY
from .models import DeadCodeFindingData, DeadCodeKind

#: A quoted module path, optionally followed by ``:attr``.
_MODULE_STRING_RE = re.compile(
    rb"""['"]([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?::([A-Za-z_]\w*))?['"]"""
)


def _module_parts(path: str) -> tuple[str, ...]:
    """The dotted-path segments of a Python file (a package for ``__init__``)."""
    parts = PurePosixPath(path).with_suffix("").parts
    return parts[:-1] if parts and parts[-1] == "__init__" else parts


class _ModuleIndex:
    """The candidate modules, keyed by every dotted tail and by bare name."""

    def __init__(self, paths: Iterable[str]) -> None:
        self.by_tail: dict[str, set[str]] = {}
        self.by_name: dict[str, set[str]] = {}
        for path in paths:
            parts = _module_parts(path)
            if not parts:
                continue
            for start in range(len(parts) - 1):
                self.by_tail.setdefault(".".join(parts[start:]), set()).add(path)
            self.by_name.setdefault(parts[-1], set()).add(path)

    def resolve(self, module: str, loader_dir: str | None) -> set[str]:
        """The candidate files *module* names, as written in a file in *loader_dir*."""
        found = set(self.by_tail.get(module, ()))
        if loader_dir is not None and "." not in module:
            prefix = f"{loader_dir}/" if loader_dir else ""
            found.update(p for p in self.by_name.get(module, ()) if p.startswith(prefix))
        return found


def _directory(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _is_python(path: str) -> bool:
    return REGISTRY.from_extension(PurePosixPath(path).suffix) == "python"


def drop_named_modules(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    runtime_importers: Iterable[str],
) -> list[DeadCodeFindingData]:
    """Drop Python file and export findings a module-path string names.

    *runtime_importers* are the files that import by name at runtime, the
    only place a bare module name is read as one. Returns a new list.
    """
    candidates = [
        f
        for f in findings
        if f.kind in (DeadCodeKind.UNREACHABLE_FILE, DeadCodeKind.UNUSED_EXPORT)
        and _is_python(f.file_path)
    ]
    if not candidates or not source_map:
        return findings
    index = _ModuleIndex({f.file_path for f in candidates})
    loaders = {path: _directory(path) for path in runtime_importers}
    named, attrs = _named_by_strings(source_map, index, loaders)
    return [
        f
        for f in findings
        if not (
            (f.kind is DeadCodeKind.UNREACHABLE_FILE and f.file_path in named)
            or (f.kind is DeadCodeKind.UNUSED_EXPORT and (f.file_path, f.symbol_name) in attrs)
        )
    ]


def _named_by_strings(
    source_map: dict[str, bytes], index: _ModuleIndex, loaders: dict[str, str]
) -> tuple[set[str], set[tuple[str, str]]]:
    """Modules a string names, and the ``(module, attr)`` pairs ``module:attr`` names."""
    named: set[str] = set()
    attrs: set[tuple[str, str]] = set()
    for path, blob in source_map.items():
        loader_dir = loaders.get(path)
        for match in _MODULE_STRING_RE.finditer(blob):
            module = match.group(1).decode("ascii")
            for target in index.resolve(module, loader_dir) - {path}:
                named.add(target)
                if match.group(2):
                    attrs.add((target, match.group(2).decode("ascii")))
    return named, attrs
