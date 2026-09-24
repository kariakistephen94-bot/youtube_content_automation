import json
from pathlib import Path

import pytest
from conftest import write

from tutorial_ai.repository.manifests import (
    find_declared_dependencies,
    normalize_package_name,
    parse_requirement,
)
from tutorial_ai.repository.scanner import scan_repository


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("fastapi", ("fastapi", None)),
        ("fastapi[standard]>=0.110", ("fastapi", ">=0.110")),
        ('uvicorn==0.30 ; python_version > "3.8"', ("uvicorn", "==0.30")),
        ("python-multipart  # needed for forms", ("python-multipart", None)),
        ("# comment", None),
        ("-r base.txt", None),
        ("", None),
        ("git+https://github.com/x/y.git", None),
    ],
)
def test_parse_requirement(line, expected):
    assert parse_requirement(line) == expected


def test_normalize_package_name():
    assert normalize_package_name("Python_Multipart") == normalize_package_name("python-multipart")
    assert normalize_package_name("FastAPI[standard]") == "fastapi"


def test_all_manifest_types(tmp_path: Path):
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\ndependencies = ["fastapi>=0.1"]\n'
        '[project.optional-dependencies]\nextra = ["pillow"]\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n',
    )
    write(tmp_path, "requirements-dev.txt", "ruff\n")
    write(
        tmp_path,
        "web/package.json",
        json.dumps({"dependencies": {"react": "^19"}, "devDependencies": {"vite": "^6"}}),
    )
    deps = {d.name: d for d in find_declared_dependencies(scan_repository(tmp_path, 200_000))}
    assert deps["fastapi"].version == ">=0.1" and not deps["fastapi"].dev
    assert "pillow" in deps
    assert deps["pytest"].dev
    assert deps["ruff"].dev
    assert deps["react"].manifest == "web/package.json"
    assert deps["vite"].dev


def test_invalid_manifest_is_skipped_not_fatal(tmp_path: Path):
    write(tmp_path, "package.json", "{not json")
    assert find_declared_dependencies(scan_repository(tmp_path, 200_000)) == []
