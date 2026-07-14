"""Ownership SSOT must exist and match hermes for consent_log."""
from __future__ import annotations

from hiob_data.ownership import SHARED_TABLES
from hiob_data.ownership_ssot import (
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


def test_delete_scanner_detects_delete_ops():
    from hiob_data.audit_writes import _WRITE_RE

    m = _WRITE_RE.search('.table("run").delete()')
    assert m is not None
    assert m.group(2) == "delete"
