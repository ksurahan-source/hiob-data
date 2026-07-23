"""Ownership runtime maps and YAML SSOT must agree for protected boundaries."""
from __future__ import annotations

from hiob_data.ownership import (
    CREATE_ONLY_TABLES,
    EXCLUSIVE_TABLES,
    GOVERNED_TABLES,
    SHARED_TABLES,
    TENANCY_TABLES,
    can_write,
)
from hiob_data.ownership_ssot import (
    _minimal_yaml,
    assert_consent_log_owner_hermes,
    load_ownership_manifest,
    manifest_path,
)


def test_manifest_file_exists():
    assert manifest_path().is_file()


def test_load_manifest_has_schema():
    m = load_ownership_manifest()
    assert m.get("schema") == "hiob.db_ownership.v1" or "ownership" in str(m.get("schema", "")).lower() or m.get("schema")


def test_consent_log_python_and_yaml_agree_hermes():
    assert "hermes" in SHARED_TABLES["consent_log"]["create"]
    assert_consent_log_owner_hermes()


def test_manifest_exclusive_entries_match_runtime_registry():
    manifest_exclusive = load_ownership_manifest().get("exclusive") or {}

    assert {
        table: EXCLUSIVE_TABLES.get(table)
        for table in manifest_exclusive
    } == manifest_exclusive


def test_ares_script_lifecycle_exclusive_owners_match_python_and_yaml():
    """The current candidate and its append-only revisions stay Atropos-owned."""
    expected = {
        "script_candidate": "atropos",
        "ares_script_revisions": "atropos",
        "ares_beat_plan_revisions": "atropos",
    }
    manifest = load_ownership_manifest()
    manifest_exclusive = manifest.get("exclusive") or {}

    assert {table: EXCLUSIVE_TABLES.get(table) for table in expected} == expected
    assert {table: manifest_exclusive.get(table) for table in expected} == expected


def test_revision_operation_and_tenancy_policies_match_python_and_yaml():
    revision_tables = {"ares_script_revisions", "ares_beat_plan_revisions"}
    manifest = load_ownership_manifest()

    assert revision_tables <= CREATE_ONLY_TABLES
    assert revision_tables <= GOVERNED_TABLES
    assert revision_tables <= TENANCY_TABLES
    assert set(manifest.get("create_only") or {}) == revision_tables
    assert set((manifest.get("create_only") or {}).values()) == {"create"}
    assert set(manifest.get("tenancy_required") or {}) == revision_tables
    assert set((manifest.get("tenancy_required") or {}).values()) == {"workspace_id"}


def test_revision_policies_survive_minimal_yaml_fallback():
    manifest = _minimal_yaml(manifest_path().read_text(encoding="utf-8"))

    assert manifest["create_only"] == {
        "ares_script_revisions": "create",
        "ares_beat_plan_revisions": "create",
    }
    assert manifest["tenancy_required"] == {
        "ares_script_revisions": "workspace_id",
        "ares_beat_plan_revisions": "workspace_id",
    }


def test_revision_registry_allows_only_atropos_create():
    for table in ("ares_script_revisions", "ares_beat_plan_revisions"):
        assert can_write(table, "create", "atropos")
        assert not can_write(table, "update", "atropos")
        assert not can_write(table, "delete", "atropos")
        assert not can_write(table, "upsert", "atropos")
        for planet in ("ares", "star", "service_role"):
            assert not can_write(table, "create", planet)


def test_delete_scanner_detects_delete_ops():
    from hiob_data.audit_writes import _WRITE_RE

    m = _WRITE_RE.search('.table("run").delete()')
    assert m is not None
    assert m.group(2) == "delete"
