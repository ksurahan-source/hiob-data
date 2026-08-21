from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hiob_data.ownership import can_write, normalize_track
from hiob_data.ownership_ssot import (
    _minimal_yaml,
    assert_consent_log_owner_hermes,
    load_ownership_manifest,
)


def test_normalization_and_unregistered_policy() -> None:
    assert normalize_track("voice") == "voiceover"
    assert normalize_track("video") == "video"
    assert can_write("unknown", "anything", "nobody")


def test_manifest_missing_invalid_and_yaml_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hiob_data.ownership_ssot as ssot

    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(ssot, "manifest_path", lambda: missing)
    with pytest.raises(FileNotFoundError):
        load_ownership_manifest()

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("schema: test", encoding="utf-8")
    monkeypatch.setattr(ssot, "manifest_path", lambda: manifest)
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda text: [text]))
    with pytest.raises(ValueError, match="mapping"):
        load_ownership_manifest()

    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda text: {"schema": text}))
    assert load_ownership_manifest()["schema"] == "schema: test"


def test_minimal_yaml_sections_and_consent_assertions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    text = """schema: hiob.db_ownership.v1
exclusive:
  listing: janus
shared:
  ignored:
create_only:
  revision: create
tenancy_required:
  revision: workspace_id
    nested: ignored
  # ignored: value
"""
    parsed = _minimal_yaml(text)
    assert parsed["exclusive"] == {"listing": "janus"}
    assert parsed["create_only"] == {"revision": "create"}
    assert parsed["tenancy_required"] == {"revision": "workspace_id"}

    assert_consent_log_owner_hermes({"shared": {"consent_log": {"create": ["hermes"]}}})
    assert_consent_log_owner_hermes({"shared": {"consent_log": "legacy"}})

    import hiob_data.ownership_ssot as ssot

    raw = tmp_path / "manifest.yaml"
    raw.write_text("shared:\n  consent_log:\n    create: [hermes]\n", encoding="utf-8")
    monkeypatch.setattr(ssot, "manifest_path", lambda: raw)
    assert_consent_log_owner_hermes({"shared": {}})
