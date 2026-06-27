"""hiob-data — 공유 테이블 governor (Phase 0.5, D-15 폴리레포).

공유 10테이블(run/clip/slot/artifact/hook/asset_library_item/product/reel_metrics/
capi_sent_events/consent_log/meta_ad_accounts)의 단일 write 권위.
직접 .table().insert 금지 → DataGovernor로만. 행성별 소유권 + beat_index 결박(P1)
강제 = 겹침이 코드→스키마로 새는 것 봉쇄(red-team 지적의 차단막).

Phase 0.4 추가:
- write_reel_metric() — metis 측정 데이터
- write_capi_event() — hermes CAPI 이벤트(PIPA 동의 검증)
- write_consent_log() — janus PIPA 동의 기록
- write_meta_ad_accounts() — hermes 광고계정 레지스트리
- assert_workspace_access() — 테넌시 검증(Phase 1 전환 준비)

Phase 0.5 추가 (brand_voice 거버넌스):
- write_brand_voice_chunk() — janus 브랜드 음성 청크(테넌티 격리 강제)
- write_brand_voice_chunks() — janus batch upsert(테넌티 격리 강제)
"""
from .governor import DataGovernor, OwnershipError, BindingError
from .ownership import can_write, SHARED_TABLES, EXCLUSIVE_TABLES, normalize_track

__all__ = [
    "DataGovernor", "OwnershipError", "BindingError",
    "can_write", "SHARED_TABLES", "EXCLUSIVE_TABLES", "normalize_track",
]
__version__ = "0.4.0"
