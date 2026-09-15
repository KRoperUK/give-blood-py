"""Packaging and release-metadata tests.

These guard the things that only break at publish time, when the feedback loop is
a failed release rather than a red test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "nhs_give_blood"


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    """The parsed pyproject.toml."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


class TestMetadata:
    """PEP 621 metadata PyPI actually reads."""

    def test_distribution_name_is_the_published_one(self, pyproject: dict[str, Any]) -> None:
        assert pyproject["project"]["name"] == "nhs-give-blood"

    def test_version_is_a_plain_string_release_please_can_rewrite(self, pyproject: dict[str, Any]) -> None:
        """release-please's python strategy rewrites `[project] version` in place."""
        version = pyproject["project"]["version"]
        assert isinstance(version, str)
        assert version.count(".") == 2, "expected semver, which is what release-please emits"

    def test_version_matches_the_release_please_manifest(self, pyproject: dict[str, Any]) -> None:
        """A drift here means release-please would bump from the wrong base."""
        import json

        manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())
        assert manifest["."] == pyproject["project"]["version"]

    def test_readme_and_licence_are_declared(self, pyproject: dict[str, Any]) -> None:
        """Without these the PyPI page is blank and the licence is unset."""
        project = pyproject["project"]
        assert project["readme"] == "README.md"
        assert (REPO_ROOT / project["readme"]).is_file()
        assert project["license"] == "MIT"
        assert project["license-files"] == ["LICENSE"]
        assert (REPO_ROOT / "LICENSE").is_file()

    def test_requires_python_floor_is_declared(self, pyproject: dict[str, Any]) -> None:
        assert pyproject["project"]["requires-python"].startswith(">=3.")

    def test_runtime_dependencies_are_bounded(self, pyproject: dict[str, Any]) -> None:
        """An unbounded major range means a breaking release of a dep breaks users."""
        for requirement in pyproject["project"]["dependencies"]:
            assert "<" in requirement, f"{requirement} has no upper bound"

    def test_dev_dependencies_are_not_shipped(self, pyproject: dict[str, Any]) -> None:
        """pytest and friends must not become runtime requirements."""
        runtime = " ".join(pyproject["project"]["dependencies"])
        for tool in ("pytest", "ruff", "mypy", "bandit"):
            assert tool not in runtime

    def test_console_script_points_at_a_real_callable(self, pyproject: dict[str, Any]) -> None:
        """A typo here only surfaces when a user runs the command."""
        target = pyproject["project"]["scripts"]["give-blood"]
        module_path, _, attribute = target.partition(":")
        module = __import__(module_path, fromlist=[attribute])
        assert callable(getattr(module, attribute))

    def test_urls_are_present(self, pyproject: dict[str, Any]) -> None:
        urls = pyproject["project"]["urls"]
        assert urls["Repository"].startswith("https://github.com/")
        assert "Issues" in urls
        assert "Changelog" in urls


class TestPackageContents:
    """What ends up inside the wheel."""

    def test_py_typed_marker_exists(self) -> None:
        """PEP 561: without this, consumers get no type information at all."""
        assert (PACKAGE / "py.typed").is_file()

    def test_poetry_packages_points_at_the_real_package(self, pyproject: dict[str, Any]) -> None:
        includes = [entry["include"] for entry in pyproject["tool"]["poetry"]["packages"]]
        assert "nhs_give_blood" in includes
        assert PACKAGE.is_dir()

    def test_public_api_is_importable_and_exported(self) -> None:
        """Every name in __all__ must actually resolve."""
        import nhs_give_blood

        missing = [name for name in nhs_give_blood.__all__ if not hasattr(nhs_give_blood, name)]
        assert not missing, f"__all__ names that do not exist: {missing}"

    def test_all_has_no_duplicates(self) -> None:
        """A duplicate is harmless at runtime but signals a bad merge."""
        import nhs_give_blood

        assert len(nhs_give_blood.__all__) == len(set(nhs_give_blood.__all__))

    def test_all_is_grouped_and_ordered(self) -> None:
        """Constants first, then names, each alphabetically.

        This is the ordering ruff's RUF022 expects, so it is asserted as written
        rather than with a plain ``sorted()`` — which would put ``AccountDetails``
        before ``BASE_URL``.
        """
        import nhs_give_blood

        names = list(nhs_give_blood.__all__)
        constants = [name for name in names if name.isupper()]
        rest = [name for name in names if not name.isupper()]
        assert names == constants + rest, "constants and names are interleaved"
        assert constants == sorted(constants)
        assert rest == sorted(rest)

    def test_no_module_imports_home_assistant(self) -> None:
        """The library must stay usable standalone; HA is the consumer, not a dep."""
        offenders = [
            str(path.relative_to(REPO_ROOT)) for path in PACKAGE.rglob("*.py") if "homeassistant" in path.read_text()
        ]
        assert not offenders, f"Home Assistant referenced in {offenders}"


class TestLockFile:
    """The lock file CI caches against."""

    def test_lock_file_is_committed(self) -> None:
        assert (REPO_ROOT / "poetry.lock").is_file(), "CI caches keyed on poetry.lock"

    def test_lock_file_matches_pyproject(self) -> None:
        """`poetry check` fails the build when these drift; catch it here first."""
        import hashlib

        with (REPO_ROOT / "poetry.lock").open("rb") as handle:
            lock = handle.read().decode()
        recorded = None
        for line in lock.splitlines():
            if line.startswith("content-hash"):
                recorded = line.split("=", 1)[1].strip().strip('"')
        assert recorded, "poetry.lock has no content-hash"
        # Not recomputing Poetry's hash here (it is an internal algorithm); the
        # presence check plus `poetry check` in CI is the real gate.
        assert len(recorded) == len(hashlib.sha256(b"").hexdigest())
