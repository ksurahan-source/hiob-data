"""DataGovernor.write() 제네릭 governed write 테스트 — 전 governed 테이블 거버넌스 가능화."""
import pytest

from hiob_data import DataGovernor, OwnershipError


class _Resp:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, log, t): self._log = log; self._t = t

    def insert(self, p): self._log.append(("insert", self._t, p)); return self
    def upsert(self, p, **kw): self._log.append(("upsert", self._t, p, kw)); return self
    def update(self, p): self._log.append(("update", self._t, p)); return self
    def delete(self): self._log.append(("delete", self._t)); return self
    def eq(self, *a): self._log.append(("eq", self._t, a)); return self
    def in_(self, *a): self._log.append(("in_", self._t, a)); return self
    def lt(self, *a): self._log.append(("lt", self._t, a)); return self
    def execute(self): return _Resp([{"id": f"{self._t}-1"}])


class FakeClient:
    def __init__(self): self.log = []
    def table(self, t): return _Q(self.log, t)


def test_generic_write_insert_governed_owner_ok():
    # asset_library_item = janus create(전용 write_* 없던 테이블) → 제네릭으로 거버넌스.
    fc = FakeClient()
    r = DataGovernor(fc).write("asset_library_item", "insert", "janus", {"kind": "image"})
    assert r == [{"id": "asset_library_item-1"}]
    assert ("insert", "asset_library_item", {"kind": "image"}) in fc.log


def test_generic_write_blocks_non_owner():
    # brand = janus 배타 소유 → athena가 쓰면 OwnershipError(fail-loud).
    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).write("brand", "update", "athena", {"profile": {}}, match={"slug": "viewok"})


def test_generic_write_update_applies_match_eq():
    fc = FakeClient()
    DataGovernor(fc).write("listing", "update", "janus", {"attributes": {"x": 1}}, match={"id": "L1"})
    ops = [e[0] for e in fc.log]
    assert "update" in ops and "eq" in ops
    assert ("eq", "listing", ("id", "L1")) in fc.log


def test_generic_write_upsert_with_on_conflict():
    fc = FakeClient()
    DataGovernor(fc).write("asset_library_item", "upsert", "janus", {"storage_key": "k"}, on_conflict="storage,storage_key")
    up = [e for e in fc.log if e[0] == "upsert"][0]
    assert up[3] == {"on_conflict": "storage,storage_key"}


def test_generic_write_unknown_op_raises():
    with pytest.raises(ValueError):
        DataGovernor(FakeClient()).write("run", "delete", "atropos", {})


def test_generic_write_exclusive_owner_enforced():
    # timeline = atropos 배타. atropos OK, 타 행성 차단.
    fc = FakeClient()
    DataGovernor(fc).write("timeline", "update", "atropos", {"duration_ms": 1000}, match={"id": "t1"})
    with pytest.raises(OwnershipError):
        DataGovernor(fc).write("timeline", "update", "janus", {"duration_ms": 1000}, match={"id": "t1"})


def test_render_jobs_updates_are_atropos_exclusive_and_scoped():
    fc = FakeClient()
    DataGovernor(fc).write(
        "render_jobs",
        "update",
        "atropos",
        {"status": "failed"},
        match={"id": "rj1", "status": "processing"},
    )
    assert ("eq", "render_jobs", ("id", "rj1")) in fc.log
    assert ("eq", "render_jobs", ("status", "processing")) in fc.log
    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).write(
            "render_jobs",
            "update",
            "janus",
            {"status": "failed"},
            match={"id": "rj1"},
        )


@pytest.mark.parametrize("table", ["ares_script_revisions", "ares_beat_plan_revisions"])
def test_revision_tables_are_insert_only_and_tenant_bound(monkeypatch, table):
    from hiob_data.governor import BindingError

    # Revision artifacts are always tenant-bound, even during the legacy
    # HIOB_TENANCY_STRICT=0 transition used by older governed tables.
    monkeypatch.delenv("HIOB_TENANCY_STRICT", raising=False)

    for invalid_workspace_id in (None, "", "   ", 123, ["ws-1"]):
        with pytest.raises(BindingError):
            DataGovernor(FakeClient()).write(
                table,
                "insert",
                "atropos",
                {"id": "rev-1", "workspace_id": invalid_workspace_id},
            )

    fc = FakeClient()
    result = DataGovernor(fc).write(
        table,
        "insert",
        "atropos",
        {"id": "rev-1", "workspace_id": "ws-1"},
    )
    assert result == [{"id": f"{table}-1"}]

    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).write(
            table,
            "update",
            "atropos",
            {"digest": "changed"},
            match={"id": "rev-1", "workspace_id": "ws-1"},
        )
    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).write(
            table,
            "upsert",
            "atropos",
            {"id": "rev-1", "workspace_id": "ws-1"},
        )
    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).delete(
            table,
            "atropos",
            match={"id": "rev-1", "workspace_id": "ws-1"},
        )


def test_generic_write_update_without_match_raises():
    # B4 회귀: update + match 없음 → 무필터 전체 UPDATE 방지, ValueError fail-loud.
    fc = FakeClient()
    with pytest.raises(ValueError):
        DataGovernor(fc).write("listing", "update", "janus", {"attributes": {"x": 1}})
    # .update가 실행되지 않았는지(전체 테이블 갱신 미발생) 확인.
    assert not any(e[0] == "update" for e in fc.log)


# ── SEC-8: 미거버넌스 테이블은 generic write로 우회 불가 ──
def test_generic_write_unmapped_table_rejected():
    # can_write는 미등록 테이블에 True를 주지만, generic write()는 멤버십을 강제해 차단.
    fc = FakeClient()
    with pytest.raises(OwnershipError):
        DataGovernor(fc).write("random_secret_table", "insert", "janus", {"x": 1})
    assert not any(e[0] == "insert" for e in fc.log)  # write 미발생


# ── SEC-4: 테넌시 strict 모드 ──
def test_tenancy_default_off_allows_missing_workspace(monkeypatch):
    # 기본(off): consent_log에 workspace_id 없어도 통과(경고만·byte-identical).
    monkeypatch.delenv("HIOB_TENANCY_STRICT", raising=False)
    fc = FakeClient()
    r = DataGovernor(fc).write("consent_log", "insert", "hermes", {"user_id": "u1", "consent_type": "marketing"})
    assert r == [{"id": "consent_log-1"}]


def test_tenancy_strict_blocks_missing_workspace(monkeypatch):
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    fc = FakeClient()
    from hiob_data.governor import BindingError
    with pytest.raises(BindingError):
        DataGovernor(fc).write("consent_log", "insert", "hermes", {"user_id": "u1"})
    assert not any(e[0] == "insert" for e in fc.log)  # cross-tenant write 차단


def test_tenancy_strict_allows_with_workspace(monkeypatch):
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    fc = FakeClient()
    r = DataGovernor(fc).write("consent_log", "insert", "hermes",
                               {"workspace_id": "ws_hiob", "user_id": "u1"})
    assert r == [{"id": "consent_log-1"}]


def test_tenancy_strict_write_capi_event(monkeypatch):
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    fc = FakeClient()
    from hiob_data.governor import BindingError
    with pytest.raises(BindingError):
        DataGovernor(fc).write_capi_event("hermes", None, True, event_id="e1")


# CRITICAL-1(적대감사): update의 workspace는 match(WHERE)에서만 신뢰 — payload로 받으면 탈취 가능.
def test_tenancy_strict_update_workspace_from_match_ok(monkeypatch):
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    fc = FakeClient()
    # WHERE가 workspace로 스코프됨 → 허용.
    DataGovernor(fc).write("consent_log", "update", "hermes", {"granted": False},
                           match={"workspace_id": "ws1", "id": "log-1"})
    assert any(e[0] == "update" for e in fc.log)


def test_tenancy_strict_update_workspace_only_in_payload_blocked(monkeypatch):
    monkeypatch.setenv("HIOB_TENANCY_STRICT", "1")
    fc = FakeClient()
    from hiob_data.governor import BindingError
    # payload엔 workspace 있으나 WHERE는 id만 → 무스코프 재지정 위험 → 차단.
    with pytest.raises(BindingError):
        DataGovernor(fc).write("consent_log", "update", "hermes",
                               {"workspace_id": "attacker_ws", "granted": True},
                               match={"id": "log-1"})
    assert not any(e[0] == "update" for e in fc.log)


# ── delete: hermes owns capi_pre_sessions (TTL purge path) ──
def test_delete_capi_pre_sessions_hermes_owner_ok():
    """purge_expired_capi_sessions → delete(table, planet, match_lt=expires_at)."""
    from hiob_data.ownership import EXCLUSIVE_TABLES, is_governed_table, can_write

    assert is_governed_table("capi_pre_sessions")
    assert EXCLUSIVE_TABLES["capi_pre_sessions"] == "hermes"
    assert can_write("capi_pre_sessions", "update", "hermes")

    fc = FakeClient()
    r = DataGovernor(fc).delete(
        "capi_pre_sessions", "hermes",
        match_lt={"expires_at": "2026-07-14T00:00:00+00:00"},
    )
    assert r == [{"id": "capi_pre_sessions-1"}]
    assert ("delete", "capi_pre_sessions") in fc.log
    assert ("lt", "capi_pre_sessions", ("expires_at", "2026-07-14T00:00:00+00:00")) in fc.log


def test_delete_capi_pre_sessions_non_owner_blocked():
    with pytest.raises(OwnershipError):
        DataGovernor(FakeClient()).delete(
            "capi_pre_sessions", "janus", match={"id": "s1"},
        )


def test_delete_requires_scope():
    with pytest.raises(ValueError):
        DataGovernor(FakeClient()).delete("capi_pre_sessions", "hermes")
