from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from hiob_data import BindingError, DataGovernor, OwnershipError


class _Response:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    def __init__(self, client: "_Client", table: str):
        self.client = client
        self.table = table
        self.action = "select"

    def _record(self, action: str, *args: Any, **kwargs: Any) -> "_Query":
        self.action = action
        self.client.log.append((action, self.table, args, kwargs))
        return self

    def insert(self, payload: Any) -> "_Query":
        return self._record("insert", payload)

    def upsert(self, payload: Any, **kwargs: Any) -> "_Query":
        return self._record("upsert", payload, **kwargs)

    def update(self, payload: Any) -> "_Query":
        return self._record("update", payload)

    def delete(self) -> "_Query":
        return self._record("delete")

    def eq(self, *args: Any) -> "_Query":
        self.client.log.append(("eq", self.table, args, {}))
        return self

    def in_(self, *args: Any) -> "_Query":
        self.client.log.append(("in_", self.table, args, {}))
        return self

    def lt(self, *args: Any) -> "_Query":
        self.client.log.append(("lt", self.table, args, {}))
        return self

    def or_(self, *args: Any) -> "_Query":
        self.client.log.append(("or_", self.table, args, {}))
        return self

    def execute(self) -> _Response:
        scripted = self.client.responses[(self.table, self.action)]
        value = scripted.pop(0) if scripted else [{"id": f"{self.table}-1"}]
        if isinstance(value, BaseException):
            raise value
        return _Response(value)


class _Client:
    def __init__(self):
        self.log: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []
        self.responses: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)

    def table(self, table: str) -> _Query:
        return _Query(self, table)

    def queue(self, table: str, action: str, *values: Any) -> None:
        self.responses[(table, action)].extend(values)


def test_update_helpers_and_scoped_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "true")
    client = _Client()
    governor = DataGovernor(client)

    governor.update_where(
        "consent_log",
        "hermes",
        {"granted": True},
        match={"workspace_id": "ws-1"},
        match_in={"id": ("a", "b")},
        or_filter="revoked_at.is.null",
    )
    governor.update_where(
        "clip",
        "apollo",
        {"volume": 1},
        match_in={"id": ["clip-1"]},
    )
    governor.update_run("metis", "run-1", status="done")

    assert any(entry[0] == "in_" and entry[2][1] == ["a", "b"] for entry in client.log)
    assert any(entry[0] == "or_" for entry in client.log)
    with pytest.raises(ValueError, match="run_id"):
        governor.update_run("metis", "")
    with pytest.raises(ValueError, match="update_where"):
        governor.update_where("clip", "apollo", {})


def test_delete_rejects_unknown_and_covers_all_scope_types(monkeypatch: pytest.MonkeyPatch) -> None:
    governor = DataGovernor(_Client())
    with pytest.raises(OwnershipError, match="등록 테이블"):
        governor.delete("unknown", "janus", match={"id": "1"})
    with pytest.raises(OwnershipError):
        governor.delete("ares_script_revisions", "atropos", match={"workspace_id": "ws-1"})

    client = _Client()
    governor = DataGovernor(client)
    governor.delete(
        "slot",
        "atropos",
        match={"id": "slot-1"},
        match_in={"beat_index": (1, 2)},
        match_lt={"created_at": "2026-01-01"},
    )
    assert {entry[0] for entry in client.log} >= {"delete", "eq", "in_", "lt"}

    monkeypatch.setenv("HIOB_TENANCY_STRICT", "yes")
    DataGovernor(_Client()).delete(
        "reel_metrics",
        "metis",
        match={"workspace_id": "ws-1", "id": "metric-1"},
    )


def test_run_slot_clip_and_timeline_helpers() -> None:
    client = _Client()
    governor = DataGovernor(client)
    assert governor.create_run("atropos", {"id": "run-1"})["id"] == "run-1"
    governor.update_run_status("athena", "run-1", "ready", reason="ok")
    with pytest.raises(BindingError):
        governor.create_slot("atropos", "run-1", "voice", None)
    assert governor.create_slot("atropos", "run-1", "music", None)["id"] == "slot-1"
    governor.fill_slot("apollo", "slot-1", "artifact-1")
    governor.create_clip("atropos", "track-1", start_ms=0)
    with pytest.raises(ValueError, match="clip_id"):
        governor.update_clip("apollo", "")
    governor.update_clip("apollo", "clip-1", volume=0.5)

    client.queue("timeline", "insert", [])
    with pytest.raises(RuntimeError, match="timeline insert"):
        governor.create_timeline("atropos", run_id="run-1")
    assert governor.create_timeline("atropos", run_id="run-1")["id"] == "timeline-1"

    client.queue("timeline_track", "insert", [])
    with pytest.raises(RuntimeError, match="timeline_track insert"):
        governor.create_timeline_track("atropos", "timeline-1", kind="video")
    assert governor.create_timeline_track("atropos", "timeline-1", kind="video")["id"] == "timeline_track-1"


def test_hook_legacy_fallback_and_error_paths() -> None:
    normal = DataGovernor(_Client()).write_hook("ares", text="hook")
    assert normal["id"] == "hook-1"

    client = _Client()
    client.queue("hook", "insert", RuntimeError("target column does not exist"), [{"id": "hook-2"}])
    assert DataGovernor(client).write_hook("ares", text="hook", target="swimmer") == {
        "id": "hook-2",
        "target": "swimmer",
    }

    for fields, message in [({"text": "hook"}, "column does not exist"), ({"target": "x"}, "network down")]:
        client = _Client()
        client.queue("hook", "insert", RuntimeError(message))
        with pytest.raises(RuntimeError, match=message):
            DataGovernor(client).write_hook("ares", **fields)

    client = _Client()
    client.queue(
        "hook",
        "insert",
        RuntimeError("target column does not exist"),
        RuntimeError("fallback failed"),
    )
    with pytest.raises(RuntimeError, match="fallback failed"):
        DataGovernor(client).write_hook("ares", target="x")


def test_artifact_audio_and_metrics_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    governor = DataGovernor(client)
    without_slot = governor.write_artifact("janus", "run-1", None, attributes=None, kind="research")
    assert without_slot["id"] == "artifact-1"
    governor.write_artifact("apollo", "run-1", "slot-1", attributes={"source": "test"})

    with pytest.raises(ValueError, match="audio track"):
        governor.write_audio("orpheus", "run-1", "video", 0, "slot-1", "x.mp4")
    with pytest.raises(BindingError):
        governor.write_audio("orpheus", "run-1", "sfx", None, "slot-1", "x.wav")
    assert governor.write_audio("orpheus", "run-1", "sfx", 1, "slot-1", "x.wav")["id"] == "artifact-1"

    assert governor.write_reel_metric("metis", "run-1", "ws-1", ctr=0.1)["id"] == "reel_metrics-1"
    assert governor.upsert_reel_metrics("metis", []) == []

    rows = [{"run_id": "run-1", "workspace_id": "ws-1"}]
    monkeypatch.delenv("HIOB_TENANCY_STRICT", raising=False)
    assert governor.upsert_reel_metrics("metis", rows)[0]["id"] == "reel_metrics-1"
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    client.queue("reel_metrics", "upsert", [])
    assert governor.upsert_reel_metrics("metis", rows) == rows
    assert governor.upsert_reel_metrics("metis", rows)[0]["id"] == "reel_metrics-1"
    with pytest.raises(BindingError):
        governor.upsert_reel_metrics("metis", [{"run_id": "run-2"}])


def test_capi_consent_account_and_brand_voice_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    governor = DataGovernor(client)
    with pytest.raises(BindingError, match="PIPA"):
        governor.write_capi_event("hermes", "ws-1", False, event_id="e-1")
    with pytest.warns(UserWarning):
        capi = governor.write_capi_event("hermes", None, True, event_id="e-1")
    assert "workspace_id" not in next(entry[2][0] for entry in client.log if entry[1] == "capi_sent_events")
    assert capi["id"] == "capi_sent_events-1"
    assert governor.write_capi_event("hermes", "ws-1", True, event_id="e-2")["id"] == "capi_sent_events-1"
    governor.write_consent_log("hermes", "ws-1", "user-1", "marketing", True)
    governor.write_meta_ad_account("hermes", "ws-1", "act-1", "system-1")

    for invalid in (None, 7, "  "):
        with pytest.raises(BindingError):
            governor.write_brand_voice_chunk(
                "janus", invalid, "manual", "source", 0, "text", [0.1]
            )
        with pytest.raises(BindingError):
            governor.write_brand_voice_chunks("janus", invalid, [{"text": "x"}])

    single = governor.write_brand_voice_chunk(
        "janus", "ws-1", "manual", "source", 0, "x" * 5000, [0.1], approved=True
    )
    assert single["id"] == "brand_voice_chunk-1"
    assert governor.write_brand_voice_chunks("janus", "ws-1", []) == []

    rows = [{"source_kind": "manual", "source_ref": "source", "chunk_index": 0}]
    client.queue("brand_voice_chunk", "upsert", [])
    assert governor.write_brand_voice_chunks("janus", "ws-1", rows)[0]["workspace"] == "ws-1"
    assert governor.write_brand_voice_chunks("janus", "ws-1", rows)[0]["id"] == "brand_voice_chunk-1"

    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    with pytest.raises(BindingError):
        governor.assert_workspace_access("metis", None, "reel_metrics")
    governor.assert_workspace_access("metis", None, "run")
    governor.assert_workspace_access("metis", "ws-1", "reel_metrics")
