"""Packaging tests: pyproject metadata and console entry point wiring.

These do not require setuptools to be installed — they parse the declared
metadata with the standard library and verify the entry-point module is
importable, so CI catches a broken package definition without building it.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]


def test_project_metadata_is_declared():
    project = _project()
    assert project["name"] == "plc-scada-sim"
    assert project["version"] == "1.0.0"
    assert project["requires-python"] == ">=3.10"


def test_runtime_dependencies_are_declared():
    names = {dep.split("=")[0].split(">")[0].split("<")[0]
             for dep in _project()["dependencies"]}
    assert {"numpy", "fastapi", "uvicorn", "pydantic", "websockets"} <= names


def test_console_script_points_at_importable_main():
    scripts = _project()["scripts"]
    assert scripts["plc-scada-sim"] == "scada.cli:main"
    from scada.cli import main  # noqa: F401 — must import without error


def test_package_exports_version():
    import scada
    assert scada.__version__ == "1.0.0"


def test_legacy_static_assets_are_shipped():
    static = ROOT / "scada" / "static"
    for name in ("index.html", "app.js", "style.css"):
        assert (static / name).exists()
