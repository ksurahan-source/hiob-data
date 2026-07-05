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
    def eq(self, *a): self._log.append(("eq", self._t, a)); return self
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


def test_generic_write_update_without_match_raises():
    # B4 회귀: update + match 없음 → 무필터 전체 UPDATE 방지, ValueError fail-loud.
    fc = FakeClient()
    with pytest.raises(ValueError):
        DataGovernor(fc).write("listing", "update", "janus", {"attributes": {"x": 1}})
    # .update가 실행되지 않았는지(전체 테이블 갱신 미발생) 확인.
    assert not any(e[0] == "update" for e in fc.log)
