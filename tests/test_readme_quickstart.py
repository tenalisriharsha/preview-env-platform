"""Regression test for issue #16.

The README Quickstart told a user to run `python3 -m venv .venv` without
ever stating the project's `requires-python = ">=3.11"` constraint. On a
machine whose default `python3` is older than 3.11, the very next command
(`pip install -e .[dev]`) fails with a confusing, unrelated-looking
setuptools/editable-mode error instead of a clear version message.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relpath):
    return (REPO_ROOT / relpath).read_text()


def _required_python_version():
    pyproject = _read("pyproject.toml")
    match = re.search(r'requires-python\s*=\s*">=\s*([\d.]+)"', pyproject)
    assert match, "could not find requires-python in pyproject.toml"
    return match.group(1)


def test_quickstart_states_python_version_before_venv_command():
    readme = _read("README.md")

    quickstart_match = re.search(
        r"## Quickstart\b(.*?)(?=\n## )", readme, re.DOTALL
    )
    assert quickstart_match, "README.md has no Quickstart section"
    quickstart = quickstart_match.group(1)

    venv_match = re.search(r"python3 -m venv \.venv", quickstart)
    assert venv_match, "Quickstart no longer shows the venv creation command"

    required_version = _required_python_version()
    text_before_venv = quickstart[: venv_match.start()]

    assert required_version in text_before_venv, (
        f"Quickstart must state the Python {required_version}+ requirement "
        "before the `python3 -m venv .venv` step, so a user whose default "
        "python3 is older knows to point the venv at a newer interpreter "
        "before hitting a confusing pip editable-install error"
    )
