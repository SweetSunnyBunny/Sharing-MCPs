"""Both documented installation routes must include the same runtime needs."""

from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from setuptools import find_namespace_packages


ROOT = Path(__file__).resolve().parents[1]


def project_metadata():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def normalized_requirements(lines):
    result = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        result[canonicalize_name(requirement.name)] = (
            str(requirement.specifier),
            frozenset(requirement.extras),
            str(requirement.marker) if requirement.marker else None,
            requirement.url,
        )
    return result


def test_documented_install_routes_have_matching_runtime_dependencies():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    declared = project_metadata()["project"]["dependencies"]
    assert normalized_requirements(requirements) == normalized_requirements(declared)


def test_package_discovery_includes_server_lifecycle_and_middleware():
    includes = project_metadata()["tool"]["setuptools"]["packages"]["find"]["include"]
    packages = find_namespace_packages(where=str(ROOT), include=includes)
    assert {"api", "core", "db", "services"}.issubset(packages)
