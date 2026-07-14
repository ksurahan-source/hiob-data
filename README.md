# hiob-data — 공유 테이블 governor (Phase 0.3)

공유 7테이블(`run·clip·slot·artifact·hook·asset_library_item·product`)의 **단일 write 권위**. 직접 `.table().insert` 대신 `DataGovernor`로만 → 행성별 소유권 + **beat_index 결박(P1)**을 *write 시점에* 강제. red-team 지적("레포만 쪼개면 disease가 코드→스키마로 이동")의 **차단막**.

## API (grounded: storage.upload_artifact·runs.*·team.py sync_clips_from_slots)
```python
g = DataGovernor(supabase_client)
g.create_run("atropos", {...})                       # run 생성 = atropos만
g.create_slot("atropos", run_id, "voice", beat=2)    # 슬롯 스캐폴드 = atropos만
g.write_audio("orpheus", run_id, "voice", 2, slot_id, key)  # P1: beat 없으면 BindingError
```

## 강제 규칙
- **P1 봉쇄**: voice/sfx 오디오 = `beat_index` 필수(없으면 `BindingError`) → 음소거 슬라이드쇼 원천차단.
- **소유권**: `ownership.py` 맵 위반 시 `OwnershipError` (예: orpheus는 slot 생성 불가=atropos만; metis는 run 생성 불가).
- **owner_planet 태그**: 모든 artifact에 자동 기록(추적성).
- music = run-level 허용(beat_index None OK).

## 상태
Phase 0.5 — write 권위 + audit + 점진 이관. 검증: FakeClient enforce 테스트 + audit_writes.

### Phase 1 이관 완료 (2026-07-14 Ralph)
- `update_clip` / `update_run` / `update_where` / `create_timeline` / `create_timeline_track`
- Modal workers + `app.py` **governed raw write = 0** (audit_writes --strict exit 0)
- allowlist empty — 재유입 시 CI 즉시 실패

```bash
# CI gate (must be zero)
python -m hiob_data.audit_writes --strict apps/modal/workers apps/modal/app.py
```
