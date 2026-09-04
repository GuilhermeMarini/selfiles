"""Packaging claims that are stated more than once, and must not disagree.

The three places this package states which Pythons it supports must agree.

`requires-python` is the promise to whoever installs it, the CI matrix is what
is actually measured, and the classifiers are what a reader believes without
running anything. Nothing keeps them together on its own, and the failure is
silent in both directions: a matrix that starts above the floor never runs the
oldest supported version, and a classifier list that outruns the matrix
advertises a version nobody tested.
"""
from __future__ import annotations

import re
from pathlib import Path

from selfiles import __version__

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _ci_matrix() -> list[tuple[int, int]]:
    line = next(ln for ln in WORKFLOW.splitlines()
                if ln.strip().startswith("python-version:"))
    return sorted((int(a), int(b)) for a, b in re.findall(r'"(\d+)\.(\d+)"', line))


def _requires_python_floor() -> tuple[int, int]:
    m = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', PYPROJECT)
    assert m, "requires-python must declare a floor"
    return int(m[1]), int(m[2])


def test_ci_starts_at_the_version_pyproject_promises():
    assert _ci_matrix()[0] == _requires_python_floor()


def test_the_ci_matrix_has_no_holes_in_it():
    """A skipped version is where a real incompatibility hides -- Python 3.13
    dropping `telnetlib` from the standard library is the example that bit the
    project this library was extracted from."""
    matrix = _ci_matrix()
    expected = [(matrix[0][0], minor)
                for minor in range(matrix[0][1], matrix[-1][1] + 1)]
    assert matrix == expected, f"gap in the CI matrix: {matrix}"


def test_the_classifiers_name_exactly_what_ci_runs():
    claimed = {tuple(int(p) for p in m) for m in re.findall(
        r'"Programming Language :: Python :: (\d+)\.(\d+)"', PYPROJECT)}
    assert claimed == set(_ci_matrix())


def test_the_two_version_literals_agree():
    """`__version__` and pyproject's `version` are typed separately, and until
    this test nothing compared them. The drift is silent and one-directional in
    practice: a release bumps `pyproject.toml`, the wheel goes to PyPI, and the
    package it installs still answers the old number when asked. A consumer
    pinning `selfiles>=X` then gets a distribution whose own code denies the
    version the index served. The project this library was extracted from
    guards the same pair in `tests/test_version.py`; this is that guard, for a
    package whose version is a literal rather than a `VERSION` file.
    """
    declared = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, re.M)
    assert declared, "pyproject.toml must declare a literal version"
    assert __version__ == declared[1], (
        f"__init__.py says {__version__}, pyproject.toml says {declared[1]}"
    )
