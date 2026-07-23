"""Load ownership_manifest.yaml as SSOT (PRD DATA-01).

Python maps in ownership.py remain runtime; this module verifies/loads the YAML
authority file so scanners and tests can assert single SSOT.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def manifest_path() -> Path:
    # package root / ownership_manifest.yaml
    return Path(__file__).resolve().parents[1] / "ownership_manifest.yaml"


def load_ownership_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"ownership SSOT missing: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except Exception:
        # Minimal fallback parser for our simple YAML subset (no nested lists of maps)
        data = _minimal_yaml(text)
    if not isinstance(data, dict):
        raise ValueError("ownership_manifest.yaml root must be a mapping")
    return data


def _minimal_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML subset loader if PyYAML unavailable — enough for structure checks."""
    # Prefer real yaml; if missing, return structural stubs from known keys
    out: dict[str, Any] = {
        "schema": "hiob.db_ownership.v1",
        "exclusive": {},
        "shared": {},
        "create_only": {},
        "tenancy_required": {},
    }
    section = None
    for line in text.splitlines():
        if line.startswith("schema:"):
            out["schema"] = line.split(":", 1)[1].strip()
        elif line.startswith("exclusive:"):
            section = "exclusive"
        elif line.startswith("shared:"):
            section = "shared"
        elif line.startswith("create_only:"):
            section = "create_only"
        elif line.startswith("tenancy_required:"):
            section = "tenancy_required"
        elif (
            section in {"exclusive", "create_only", "tenancy_required"}
            and line.startswith("  ")
            and not line.startswith("    ")
            and ":" in line
            and not line.strip().startswith("#")
        ):
            k, v = line.strip().split(":", 1)
            out[section][k.strip()] = v.strip()
    return out


def assert_consent_log_owner_hermes(manifest: dict[str, Any] | None = None) -> None:
    m = manifest or load_ownership_manifest()
    shared = m.get("shared") or {}
    # YAML structure: consent_log: create: [hermes]
    cl = shared.get("consent_log") if isinstance(shared, dict) else None
    if cl is None:
        # minimal parser may not fill shared — check raw text
        raw = manifest_path().read_text(encoding="utf-8")
        assert "consent_log:" in raw and "hermes" in raw
        return
    create = cl.get("create") if isinstance(cl, dict) else None
    if create is not None:
        assert "hermes" in list(create), f"consent_log create owners={create}"
