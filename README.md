# hiob-data (발판)
**DB write 게이트.** 테이블 소유권(행성별 allowlist) + beat_index 결박(P1) + PIPA 동의를 write 시점에 강제.
- 소유 예: slot=atropos, reel_metrics=metis, consent_log=janus, capi=hermes.
- 의존: hiob-contracts. 자립: governor 13-check pytest green.
