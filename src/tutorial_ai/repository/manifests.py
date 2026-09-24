"""Deterministic extraction of declared dependencies from common manifest files.

Used to ground the AI's dependency list in what the repository actually declares.
Covers pyproject.toml, requirements*.txt and package.json; other ecosystems are left to the AI.
"""

import json
import re
import tomllib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from tutorial_ai.repository.scanner import ScanResult
from tutorial_ai.utils.logging import get_logger

log = get_logger("repository.manifests")

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


class DeclaredDependency(BaseModel):
    name: str
    version: str | None = None
    dev: bool = False
    manifest: str


def normalize_package_name(name: str) -> str:
    """'FastAPI[standard]' and 'fastapi' are the same package."""
    return re.sub(r"[-_.]+", "-", name.split("[", 1)[0].strip()).lower()


def parse_requirement(line: str) -> tuple[str, str | None] | None:
    """'fastapi[standard]>=0.110 ; python_version>"3.8"' -> ('fastapi', '>=0.110')."""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http://", "https://")):
        return None
    match = _REQUIREMENT_NAME.match(line)
    if not match:
        return None
    version = match[3].split(";", 1)[0].strip() or None
    return match[1], version


def find_declared_dependencies(scan: ScanResult) -> list[DeclaredDependency]:
    found: list[DeclaredDependency] = []
    for file in scan.files:
        name = PurePosixPath(file.path).name
        full = scan.root / file.path
        try:
            if name == "pyproject.toml":
                found += _from_pyproject(full, file.path)
            elif name.startswith("requirements") and name.endswith((".txt", ".in")):
                found += _from_requirements(full, file.path)
            elif name == "package.json" and "node_modules" not in file.path:
                found += _from_package_json(full, file.path)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            log.warning("Could not parse %s: %s", file.path, exc)
    return found


def _from_requirements(path: Path, rel: str) -> list[DeclaredDependency]:
    dev = any(word in PurePosixPath(rel).name.lower() for word in ("dev", "test", "lint"))
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_requirement(line)
        if parsed:
            result.append(DeclaredDependency(name=parsed[0], version=parsed[1], dev=dev, manifest=rel))
    return result


def _from_pyproject(path: Path, rel: str) -> list[DeclaredDependency]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result: list[DeclaredDependency] = []

    def add(requirements: list, dev: bool) -> None:
        for req in requirements:
            if isinstance(req, str) and (parsed := parse_requirement(req)):
                result.append(
                    DeclaredDependency(name=parsed[0], version=parsed[1], dev=dev, manifest=rel)
                )

    project = data.get("project", {})
    add(project.get("dependencies", []), dev=False)
    for requirements in project.get("optional-dependencies", {}).values():
        add(requirements, dev=False)
    for requirements in data.get("dependency-groups", {}).values():
        add(requirements, dev=True)

    poetry = data.get("tool", {}).get("poetry", {})
    for dev, table in ((False, poetry.get("dependencies", {})), (True, poetry.get("dev-dependencies", {}))):
        for name, spec in table.items():
            if name.lower() != "python":
                version = spec if isinstance(spec, str) else None
                result.append(DeclaredDependency(name=name, version=version, dev=dev, manifest=rel))
    return result


def _from_package_json(path: Path, rel: str) -> list[DeclaredDependency]:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for key, dev in (("dependencies", False), ("devDependencies", True), ("peerDependencies", False)):
        for name, version in (data.get(key) or {}).items():
            result.append(DeclaredDependency(name=name, version=str(version), dev=dev, manifest=rel))
    return result
