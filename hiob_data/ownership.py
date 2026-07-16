"""공유 테이블 write 소유권 맵 (ground-truth grep, 2026-06-27).

겹침 10테이블(Phase 0.4) = 여러 행성이 write. governor가 행성별 write 권한을 강제한다.
- 기존 7: run, clip, slot, artifact, hook, asset_library_item, product
- 신규 3(Phase 0.4): reel_metrics(metis), capi_sent_events(hermes), consent_log(hermes), meta_ad_accounts(hermes)
- Phase 0.5(brand_voice): brand_voice_chunk(janus만, 테넌시 격리 강제 via DataGovernor.write_brand_voice_chunk)
- hermes CAPI: capi_pre_sessions·capi_sent_events·commerce_installs 배타 소유
  (purge_expired_capi_sessions → DataGovernor.delete("capi_pre_sessions", "hermes", ...))
출처: apps/modal/workers .table() write op grep + hiob_platform 헬퍼(storage/runs/role_artifacts).
"""
from __future__ import annotations

# 공유 10테이블: create_owner(생성 권한) + update_owners(자기 범위 갱신 허용)
# Phase 0.4: 측정(reel_metrics, capi_sent_events), 동의(consent_log), 광고계정(meta_ad_accounts) 추가
SHARED_TABLES: dict[str, dict] = {
    "run":                {"create": {"atropos"},                       "update": {"athena", "hermes", "metis", "atropos"}},
    "clip":               {"create": {"atropos"},                       "update": {"athena", "orpheus", "apollo", "atropos"}},
    "slot":               {"create": {"atropos"},                       "update": {"athena", "orpheus", "apollo"}},
    "artifact":           {"create": {"athena", "orpheus", "apollo", "atropos", "janus"}, "update": {"athena", "orpheus", "apollo", "atropos"}},
    "hook":               {"create": {"ares"},                          "update": {"atropos"}},
    "asset_library_item": {"create": {"janus"},                         "update": {"atropos", "janus"}},
    "product":            {"create": {"janus"},                         "update": {"hermes", "janus"}},
    "reel_metrics":       {"create": {"metis"},                         "update": {"metis"}},
    "capi_sent_events":   {"create": {"hermes"},                        "update": {"hermes"}},
    # consent_log = Hermes/CAPI (OWNERSHIP.toml SSOT) — was wrongly janus here (audit conflict)
    "consent_log":        {"create": {"hermes"},                        "update": {"hermes"}},
    "meta_ad_accounts":   {"create": {"hermes"},                        "update": {"hermes"}},
}

# 배타 소유 테이블(1행성만 write) — can_write/is_governed_table이 강제(create·update·delete).
# hermes: CAPI pre-session PII + sent events + commerce installs (TTL purge 포함).
EXCLUSIVE_TABLES: dict[str, str] = {
    "timeline": "atropos", "timeline_track": "atropos", "composition_snapshot": "atropos",
    "script_candidate": "atropos", "production_jobs": "atropos", "render_jobs": "atropos",
    "agent_call": "atropos",
    "brand": "janus", "listing": "janus", "brand_voice_chunk": "janus",
    # hermes CAPI / commerce (delete 허용 — exclusive owner = any op)
    "capi_pre_sessions": "hermes",
    "capi_sent_events": "hermes",
    "commerce_installs": "hermes",
    "reel_metrics": "metis",
}

# audio 트랙 (slot.track 어휘) — voice/sfx는 beat_index 결박 필수(P1), music은 run-level 허용
AUDIO_TRACKS = {"voice", "voiceover", "sfx", "music"}
BEAT_BOUND_TRACKS = {"voice", "voiceover", "sfx"}
_TRACK_NORMALIZE = {"voice": "voiceover", "voiceover": "voiceover", "sfx": "sfx", "music": "music"}


def normalize_track(track: str) -> str:
    return _TRACK_NORMALIZE.get(track, track)


# governor가 아는 전체 테이블(공유 ∪ 배타). generic write()는 이 집합 밖 테이블을 거부한다
# (SEC-8, 2026-07-06): can_write는 미등록 테이블에 True를 주므로, generic 경로가 열린 뒷문이
# 되지 않도록 write()가 멤버십을 별도 강제.
GOVERNED_TABLES: frozenset[str] = frozenset(SHARED_TABLES) | frozenset(EXCLUSIVE_TABLES)

# workspace_id 결박이 필수인 테넌시-민감 테이블(Phase 1). HIOB_TENANCY_STRICT=1이면 강제.
TENANCY_TABLES: frozenset[str] = frozenset(
    {"consent_log", "meta_ad_accounts", "reel_metrics", "capi_sent_events", "brand_voice_chunk"}
)


def is_governed_table(table: str) -> bool:
    """governor가 소유권을 아는 테이블인가 (generic write 멤버십 게이트)."""
    return table in GOVERNED_TABLES


def can_write(table: str, op: str, planet: str) -> bool:
    """planet이 table에 op(create|update)를 할 권한이 있나."""
    planet = (planet or "").lower()
    if table in EXCLUSIVE_TABLES:
        return EXCLUSIVE_TABLES[table] == planet
    rule = SHARED_TABLES.get(table)
    if not rule:
        return True  # 미등록 테이블 = 제약 없음(governor 밖)
    return planet in rule.get(op, set())
