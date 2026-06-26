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
Phase 0.3 — 신규 additive 모듈(기존 코드 미수정). 검증: py_compile + 5/5 enforce 테스트(live DB 없이 FakeClient). 다음(Phase 1): 워커가 직접 write → governor 호출로 마이그레이션(team.py sync_clips_from_slots 우선 = P1 라이브 봉쇄).
