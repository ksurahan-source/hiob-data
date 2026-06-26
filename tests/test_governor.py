"""DataGovernor 강제 검증 — live DB 없이 enforce 로직 증명 (FakeClient).

실제 데이터모델: atropos가 slot 생성(스캐폴드), 미디어 행성(orpheus/apollo/athena)이
artifact 쓰고 slot 채움. governor가 소유권 + beat_index 결박(P1)을 write 시점에 강제.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hiob_data import DataGovernor, OwnershipError, BindingError


class _Resp:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, log, table): self._log, self._t = log, table
    def insert(self, p): self._log.append(("insert", self._t, p)); return self
    def upsert(self, p): self._log.append(("upsert", self._t, p)); return self
    def update(self, p): self._log.append(("update", self._t, p)); return self
    def eq(self, *a): return self
    def execute(self): return _Resp([{"id": f"{self._t}-1"}])


class FakeClient:
    def __init__(self): self.log = []
    def table(self, t): return _Q(self.log, t)


def run():
    passed = 0

    # 1) ★P1: voice 오디오 beat_index 없으면 거부 (소유권보다 먼저)
    try:
        DataGovernor(FakeClient()).write_audio("orpheus", "r1", "voice", None, "slot-x", "v.mp3")
        assert False, "P1 미차단"
    except BindingError as e:
        print("✅ P1 차단:", e); passed += 1

    # 2) 소유권: 미디어 행성은 slot 생성 권한 없음(=atropos만)
    try:
        DataGovernor(FakeClient()).create_slot("orpheus", "r1", "voice", 0)
        assert False, "slot create 소유권 미차단"
    except OwnershipError as e:
        print("✅ slot create 소유권 차단(atropos만):", e); passed += 1

    # 3) 정상 흐름: atropos가 slot 생성 → orpheus가 audio 결박
    fc = FakeClient(); g = DataGovernor(fc)
    slot = g.create_slot("atropos", "r1", "voice", 2)
    art = g.write_audio("orpheus", "r1", "voice", 2, slot["id"], "v.mp3", duration_ms=2000)
    tables = [(op, t) for op, t, _ in fc.log]
    assert ("insert", "slot") in tables, tables          # atropos 생성
    assert ("insert", "artifact") in tables               # orpheus artifact
    assert ("update", "slot") in tables                   # orpheus fill
    slot_payload = next(p for op, t, p in fc.log if t == "slot" and op == "insert")
    assert slot_payload["beat_index"] == 2 and slot_payload["track"] == "voiceover"
    art_payload = next(p for op, t, p in fc.log if t == "artifact")
    assert art_payload["attributes"]["owner_planet"] == "orpheus"
    assert art_payload["attributes"]["beat_index"] == 2
    print("✅ 정상 결박: slot.beat_index=2 track=voiceover(atropos), artifact.owner=orpheus beat=2"); passed += 1

    # 4) run create 소유권: metis 거부(=atropos)
    try:
        DataGovernor(FakeClient()).create_run("metis", {"brand_slug": "viewok"})
        assert False
    except OwnershipError as e:
        print("✅ run create 소유권 차단:", e); passed += 1

    # 5) music run-level 허용(beat_index None OK)
    fc2 = FakeClient(); g2 = DataGovernor(fc2)
    g2.write_audio("orpheus", "r1", "music", None, "slot-m", "bgm.mp3")
    print("✅ music run-level 허용(beat_index None OK)"); passed += 1

    # 6) ★Phase 0.4: PIPA 동의 없으면 CAPI 이벤트 불가
    try:
        DataGovernor(FakeClient()).write_capi_event("hermes", "ws-1", pipa_consent=False,
                                                     event_id="e123", event_type="Purchase")
        assert False, "PIPA 미검증"
    except BindingError as e:
        print("✅ PIPA 동의 검증(미동의→CAPI 불가):", e); passed += 1

    # 7) PIPA 동의 있으면 CAPI 이벤트 가능
    fc3 = FakeClient(); g3 = DataGovernor(fc3)
    result = g3.write_capi_event("hermes", "ws-1", pipa_consent=True,
                                 event_id="e123", event_type="Purchase")
    tables = [(op, t) for op, t, _ in fc3.log]
    assert ("insert", "capi_sent_events") in tables
    print("✅ CAPI 이벤트 write (PIPA 동의함)"); passed += 1

    # 8) consent_log는 janus만 쓸 수 있음
    try:
        DataGovernor(FakeClient()).write_consent_log("hermes", "ws-1", "user-1",
                                                     "overseas_transfer", True)
        assert False
    except OwnershipError as e:
        print("✅ consent_log 소유권 차단(janus만):", e); passed += 1

    # 9) janus consent_log 쓰기
    fc4 = FakeClient(); g4 = DataGovernor(fc4)
    log = g4.write_consent_log("janus", "ws-1", "user-1", "overseas_transfer", True)
    tables = [(op, t) for op, t, _ in fc4.log]
    assert ("insert", "consent_log") in tables
    payload = next(p for op, t, p in fc4.log if t == "consent_log")
    assert payload["user_id"] == "user-1" and payload["granted"] is True
    print("✅ consent_log write (janus, PIPA 동의 기록)"); passed += 1

    # 10) meta_ad_accounts는 hermes만 쓸 수 있음
    try:
        DataGovernor(FakeClient()).write_meta_ad_account("janus", "ws-1",
                                                         account_id="act-123",
                                                         system_user_id="61589513995813")
        assert False
    except OwnershipError as e:
        print("✅ meta_ad_accounts 소유권 차단(hermes만):", e); passed += 1

    # 11) hermes meta_ad_accounts 쓰기
    fc5 = FakeClient(); g5 = DataGovernor(fc5)
    acc = g5.write_meta_ad_account("hermes", "ws-1", account_id="act-123",
                                   system_user_id="61589513995813")
    tables = [(op, t) for op, t, _ in fc5.log]
    assert ("insert", "meta_ad_accounts") in tables
    payload = next(p for op, t, p in fc5.log if t == "meta_ad_accounts")
    assert payload["account_id"] == "act-123" and payload["system_user_id"] == "61589513995813"
    print("✅ meta_ad_accounts write (hermes, CAPI 멀티테넌트 레지스트리)"); passed += 1

    # 12) reel_metrics는 metis만 쓸 수 있음
    try:
        DataGovernor(FakeClient()).write_reel_metric("orpheus", "r1", ws_id=None)
        assert False
    except OwnershipError as e:
        print("✅ reel_metrics 소유권 차단(metis만):", e); passed += 1

    # 13) metis reel_metrics 쓰기
    fc6 = FakeClient(); g6 = DataGovernor(fc6)
    metric = g6.write_reel_metric("metis", "r1", workspace_id="ws-1", roas=2.5, ctr=0.032)
    tables = [(op, t) for op, t, _ in fc6.log]
    assert ("insert", "reel_metrics") in tables
    payload = next(p for op, t, p in fc6.log if t == "reel_metrics")
    assert payload["run_id"] == "r1" and payload["roas"] == 2.5
    print("✅ reel_metrics write (metis, 측정 데이터)"); passed += 1

    print(f"\n✅ {passed}/13 — governor가 P1·소유권·PIPA·테넌시를 write 시점에 강제(겹침 스키마누수 봉쇄).")


def test_governor_enforcement():
    """pytest entry — run() raises on any of the 13 enforcement checks failing."""
    run()


if __name__ == "__main__":
    run()
