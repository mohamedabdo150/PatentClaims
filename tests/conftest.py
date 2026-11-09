from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from patent_converter.config.claim_patterns import PatternRegistry
from patent_converter.config.settings import PROJECT_ROOT, load_settings
from tests.fixtures.patent_fixtures import build_edge_cases, build_patent

TEST_API_KEY = "test-key-123"


def soffice_available() -> bool:
    """LibreOffice reachable as configured (PATENT__CONVERSION__SOFFICE_PATH) or on PATH."""
    import os
    import shutil

    configured = os.environ.get("PATENT__CONVERSION__SOFFICE_PATH")
    return bool(configured and Path(configured).exists()) or shutil.which("soffice") is not None


@pytest.fixture(scope="session")
def registry() -> PatternRegistry:
    return PatternRegistry.from_file(PROJECT_ROOT / "config" / "claim_patterns.yaml")


@pytest.fixture(scope="session")
def synthetic_profile(registry):
    return registry.get("synthetic-test")


@pytest.fixture(scope="session")
def example_profile(registry):
    return registry.get("example")


def make_settings(tmp: Path, **overrides):
    base = {
        "environment": "test",
        "storage": {"temporary_directory": str(tmp / "work"), "min_free_disk_gb": 0, "stale_job_retention_minutes": 60},
        "security": {"auth_enabled": True, "api_key_sha256": [hashlib.sha256(TEST_API_KEY.encode()).hexdigest()],
                     "rate_limit_requests_per_minute": 0},
        "default_config_profile": "synthetic-test",
        "logging": {"level": "WARNING", "json_format": True},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return load_settings(PROJECT_ROOT / "config" / "app.yaml", **base)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def docs(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("docs")
    return {
        "en": build_patent(d / "en.docx", "en"),
        "ar": build_patent(d / "ar.docx", "ar"),
        "en_auto": build_patent(d / "en_auto.docx", "en", autonumber=True),
        "ar_auto": build_patent(d / "ar_auto.docx", "ar", autonumber=True),
        "ar_missing5": build_patent(d / "ar_missing5.docx", "ar", missing={5}),
        "en_dup3": build_patent(d / "en_dup3.docx", "en", duplicate=3),
        "en_unclosed2": build_patent(d / "en_unclosed2.docx", "en", unclosed=2),
        "en_orphan": build_patent(d / "en_orphan.docx", "en", orphan_after=2),
        "ar_reversed": build_patent(d / "ar_reversed.docx", "ar", reverse_order=True),
        "en_extra": build_patent(d / "en_extra.docx", "en", extra_claims=4),
        "edge": build_edge_cases(d / "edge.docx"),
        "en_marker": build_patent(d / "en_marker.docx", "en", markers=True),
        "ar_marker": build_patent(d / "ar_marker.docx", "ar", markers=True),
    }
