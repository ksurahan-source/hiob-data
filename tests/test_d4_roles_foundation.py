"""D4 foundation: ownership SSOT forbids service_role; ledger migration present."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from hiob_data.ownership_ssot import load_ownership_manifest, manifest_path


def test_runtime_policy_forbids_service_role():
    raw = manifest_path().read_text(encoding="utf-8")
    assert "service_role_allowed: false" in raw
    assert "per_planet_credentials: required" in raw
    m = load_ownership_manifest()
    assert m.get("schema") == "hiob.db_ownership.v1"
    assert "hermes" in str(m.get("exclusive") or {}) or "capi_pre_sessions" in raw


def test_migration_0096_when_schema_checkout_provided():
    schema_root = os.environ.get("HIOB_SCHEMA_ROOT")
    if not schema_root:
        pytest.skip(
            "cross-repo schema check requires an explicit HIOB_SCHEMA_ROOT"
        )
    path = (
        Path(schema_root)
        / "infra"
        / "migrations"
        / "0096_planet_roles_rls_foundation.sql"
    )
    assert path.is_file(), f"migration 0096 missing at {path}"
    text = path.read_text(encoding="utf-8")
    assert "hiob_planet_" in text
    assert "NOBYPASSRLS" in text
    assert "db_ownership_ledger" in text
    assert "service_role_allowed" in text
    for planet in (
        "janus", "ares", "athena", "orpheus", "apollo", "parzifal", "karma",
        "hermes", "metis", "atropos", "artemis", "hephaestus",
    ):
        assert planet in text
