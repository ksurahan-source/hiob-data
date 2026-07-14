"""DataGovernor — 공유 테이블 단일 write 권위 (hiob-data, Phase 0.4).

founder "겹치는 것을 관장할 시스템" + red-team "스키마 분할이 레포분리 선결".
직접 .table().insert 대신 이 governor를 통해서만 write → 행성별 소유권 + beat_index
결박(P1)을 *write 시점에* 강제. 충돌이 코드→스키마로 새는 것을 봉쇄.

Phase 0.4 업그레이드:
- consent_log write (janus) — PIPA §17 동의 기록
- meta_ad_accounts write (hermes) — 광고계정 다중테넌트
- tenancy validation — workspace_id 검증(단일→다중 전환 준비)

actual DB 호출은 주입된 client(supabase)에 위임 — governor는 enforce + payload만.
"""
from __future__ import annotations

import os
from typing import Any, Optional
from datetime import datetime

from .ownership import (
    can_write, normalize_track, BEAT_BOUND_TRACKS, AUDIO_TRACKS,
    is_governed_table, TENANCY_TABLES,
)


def _tenancy_strict() -> bool:
    """HIOB_TENANCY_STRICT=1|true|yes → Phase 1 테넌시 강제(workspace_id 필수). 기본 off=경고만."""
    return os.environ.get("HIOB_TENANCY_STRICT", "").lower() in ("1", "true", "yes")


class OwnershipError(PermissionError):
    """행성이 권한 없는 테이블/연산을 write하려 함."""


class BindingError(ValueError):
    """beat_index 결박 위반 (P1 음소거 위험)."""


class DataGovernor:
    def __init__(self, client: Any):
        self._c = client

    # ── 권한 게이트 ──
    def _assert(self, table: str, op: str, planet: str) -> None:
        if not can_write(table, op, planet):
            raise OwnershipError(f"{planet}는 {table}.{op} 권한 없음 (소유권 위반)")

    # ── 제네릭 governed write (2026-07-05) — 전용 메서드 없는 governed 테이블도 거버넌스 가능 ──
    def write(self, table: str, op: str, planet: str, payload: dict,
              *, match: Optional[dict] = None, on_conflict: Optional[str] = None) -> Any:
        """소유권 검증 후 write. op=insert|update|upsert. `_assert`(can_write) 재사용.

        13 governed 테이블 중 7종(asset_library_item·brand·product·listing·capi_pre_sessions·
        commerce_installs·production_jobs·timeline·timeline_track·composition_snapshot·
        script_candidate)은 전용 write_* 메서드가 없어 raw write로 우회될 수밖에 없었다. 이
        제네릭 write로 전부 거버넌스 가능(호출자가 planet을 명시 선언 → can_write 게이트).
        update는 match={col:val}로 .eq 필터. 위반=OwnershipError(fail-loud).
        """
        op = (op or "").lower()
        # SEC-8(2026-07-06): generic 경로가 거버넌스 뒷문이 되지 않게 멤버십 강제. can_write는
        # 미등록 테이블에 True를 주므로(ownership.py:53), 여기서 governed 테이블만 허용 — 오타·
        # 미거버넌스 테이블이 generic write로 새는 것을 fail-loud 차단. 특화 메서드는 영향 없음.
        if not is_governed_table(table):
            raise OwnershipError(
                f"'{table}'는 거버넌스 등록 테이블 아님 — generic write() 거부. "
                f"SHARED/EXCLUSIVE_TABLES에 등록 후 사용(우회 방지)."
            )
        # B4: update는 match(=WHERE) 필수 — 없으면 무필터 update가 전체 테이블을 덮어씀. fail-loud.
        if op == "update" and not match:
            raise ValueError('update는 match 필수 (예: match={"id": run_id}) — 무필터 전체갱신 방지')
        op_key = "create" if op in ("insert", "upsert") else "update"
        self._assert(table, op_key, planet)
        # SEC-4(2026-07-06): 테넌시 민감 테이블에 workspace_id 결박(strict 모드). 기본 off=경고만.
        # op별 소스 분리(적대감사 CRITICAL-1): insert/upsert는 payload가 workspace를 실어야 하고,
        # update는 match(WHERE)가 workspace로 스코프돼야 한다. update의 workspace를 payload로 받으면
        # 무스코프 WHERE로 남의 테넌트 행을 자기 workspace로 재지정하는 탈취가 가능 → match만 신뢰.
        if table in TENANCY_TABLES:
            ws = (match or {}).get("workspace_id") if op == "update" else (payload or {}).get("workspace_id")
            self.assert_workspace_access(planet, ws, table)
        q = self._c.table(table)
        if op == "insert":
            return q.insert(payload).execute().data
        if op == "upsert":
            uq = q.upsert(payload, on_conflict=on_conflict) if on_conflict else q.upsert(payload)
            return uq.execute().data
        if op == "update":
            uq = q.update(payload)
            for col, val in (match or {}).items():
                uq = uq.eq(col, val)
            return uq.execute().data
        raise ValueError(f"알 수 없는 op: {op} (insert|update|upsert)")

    def update_where(
        self,
        table: str,
        planet: str,
        payload: dict,
        *,
        match: Optional[dict] = None,
        match_in: Optional[dict] = None,
        or_filter: Optional[str] = None,
    ) -> Any:
        """소유권 검증 후 multi-eq / multi-in / or_ update.

        match={col: val} → .eq · match_in={col: [v1,v2]} → .in_
        or_filter → PostgREST .or_(...) 문자열 (예: "motion_id.is.null,motion_status.eq.failed")
        match/match_in/or_filter 중 하나 이상 필수(무필터 전체 갱신 금지).
        """
        if not match and not match_in and not or_filter:
            raise ValueError("update_where는 match|match_in|or_filter 필수 — 무필터 전체갱신 방지")
        self._assert(table, "update", planet)
        if table in TENANCY_TABLES:
            ws = (match or {}).get("workspace_id")
            self.assert_workspace_access(planet, ws, table)
        uq = self._c.table(table).update(payload)
        for col, val in (match or {}).items():
            uq = uq.eq(col, val)
        for col, vals in (match_in or {}).items():
            uq = uq.in_(col, list(vals) if not isinstance(vals, list) else vals)
        if or_filter:
            uq = uq.or_(or_filter)
        return uq.execute().data

    def update_run(self, planet: str, run_id: str, **fields) -> Any:
        """run row update by id (athena|hermes|metis|atropos)."""
        if not run_id:
            raise ValueError("run_id 필수")
        return self.write("run", "update", planet, dict(fields), match={"id": run_id})

    def delete(
        self,
        table: str,
        planet: str,
        *,
        match: Optional[dict] = None,
        match_in: Optional[dict] = None,
        match_lt: Optional[dict] = None,
    ) -> Any:
        """Owned delete — match|match_in|match_lt required (never unscoped full-table delete).

        Permission: exclusive owner, or shared-table create/update owner (delete ≈ update).
        match_lt={col: val} → .lt(col, val) (e.g. capi_pre_sessions TTL purge by expires_at).
        Call: delete("capi_pre_sessions", "hermes", match_lt={"expires_at": iso_now}).
        """
        if not match and not match_in and not match_lt:
            raise ValueError("delete는 match|match_in|match_lt 필수 — 무필터 전체삭제 방지")
        if not is_governed_table(table):
            raise OwnershipError(
                f"'{table}'는 거버넌스 등록 테이블 아님 — delete() 거부."
            )
        # Prefer update permission; fall back to create (owners who materialize may prune).
        try:
            self._assert(table, "update", planet)
        except OwnershipError:
            self._assert(table, "create", planet)
        if table in TENANCY_TABLES:
            ws = (match or {}).get("workspace_id")
            self.assert_workspace_access(planet, ws, table)
        dq = self._c.table(table).delete()
        for col, val in (match or {}).items():
            dq = dq.eq(col, val)
        for col, vals in (match_in or {}).items():
            dq = dq.in_(col, list(vals) if not isinstance(vals, list) else vals)
        for col, val in (match_lt or {}).items():
            dq = dq.lt(col, val)
        return dq.execute().data

    # ── run ──
    def create_run(self, planet: str, fields: dict) -> dict:
        self._assert("run", "create", planet)
        return self._c.table("run").insert(fields).execute().data[0]

    def update_run_status(self, planet: str, run_id: str, status: str, **extra) -> dict:
        self._assert("run", "update", planet)
        payload = {"script_status": status, **extra}
        return self._c.table("run").update(payload).eq("id", run_id).execute().data

    # ── slot (beat 결박 앵커; atropos가 생성, 미디어 행성이 채움) ──
    def create_slot(self, planet: str, run_id: str, track: str,
                    beat_index: Optional[int], **fields) -> dict:
        """timeline 슬롯 생성 = atropos만(스캐폴드). ★ P1: beat 결박 트랙 beat_index 필수."""
        self._assert("slot", "create", planet)
        track = normalize_track(track)
        if track in BEAT_BOUND_TRACKS and beat_index is None:
            raise BindingError(f"{track} slot에 beat_index 없음 (P1 음소거 위험)")
        payload = {"run_id": run_id, "track": track, "beat_index": beat_index, **fields}
        return self._c.table("slot").insert(payload).execute().data[0]

    def fill_slot(self, planet: str, slot_id: str, artifact_id: str) -> dict:
        """슬롯에 산출 artifact 결박 = 미디어 행성(athena/orpheus/apollo)."""
        self._assert("slot", "update", planet)
        return self._c.table("slot").update({"current_artifact_id": artifact_id}).eq("id", slot_id).execute().data

    # ── hook (훅 생성 = ares 전용) ──
    def write_hook(self, planet: str, **fields) -> dict:
        """hook row insert. ares 전용. target column이 없는 구 스키마 fallback 포함."""
        self._assert("hook", "create", planet)
        payload = dict(fields)
        try:
            return self._c.table("hook").insert(payload).execute().data[0]
        except Exception as exc:
            exc_str = str(exc).lower()
            target = payload.pop("target", None)
            if target is None or ("column" not in exc_str and "does not exist" not in exc_str):
                raise
            try:
                return {**self._c.table("hook").insert(payload).execute().data[0], "target": target}
            except Exception as inner:
                raise inner

    # ── clip (타임라인 클립 생성 = atropos 전용) ──
    def create_clip(self, planet: str, track_id: str, **fields) -> dict:
        """clip row insert. atropos 전용."""
        self._assert("clip", "create", planet)
        payload = {"track_id": track_id, **fields}
        return self._c.table("clip").insert(payload).execute().data[0]

    def update_clip(self, planet: str, clip_id: str, **fields) -> Any:
        """clip row update by id. athena|orpheus|apollo|atropos.

        Phase 1 이관: 워커가 sb.table('clip').update(...).eq('id', ...) 대신 이 경로를 쓴다.
        """
        if not clip_id:
            raise ValueError("clip_id 필수")
        return self.write("clip", "update", planet, dict(fields), match={"id": clip_id})

    def create_timeline(self, planet: str, **fields) -> dict:
        """timeline insert = atropos 전용 (variant clone 등)."""
        self._assert("timeline", "create", planet)
        data = self._c.table("timeline").insert(fields).execute().data or []
        if not data:
            raise RuntimeError("timeline insert 빈 응답")
        return data[0]

    def create_timeline_track(self, planet: str, timeline_id: str, **fields) -> dict:
        """timeline_track insert = atropos 전용."""
        self._assert("timeline_track", "create", planet)
        payload = {"timeline_id": timeline_id, **fields}
        data = self._c.table("timeline_track").insert(payload).execute().data or []
        if not data:
            raise RuntimeError("timeline_track insert 빈 응답")
        return data[0]

    # ── artifact (미디어/오디오 실파일) ──
    def write_artifact(self, planet: str, run_id: str, slot_id: Optional[str], **fields) -> dict:
        """artifact insert. slot_id=None 허용 (run-level artifact, e.g. beat_plan)."""
        self._assert("artifact", "create", planet)
        base = {"run_id": run_id,
                "attributes": {**(fields.pop("attributes", {}) or {}), "owner_planet": planet},
                **fields}
        if slot_id is not None:
            base["slot_id"] = slot_id
        return self._c.table("artifact").insert(base).execute().data[0]

    # ── 오디오 결박 편의 (P1 봉쇄의 핵심) ──
    def write_audio(self, planet: str, run_id: str, track: str, beat_index: Optional[int],
                    slot_id: str, storage_key: str, *, duration_ms: Optional[int] = None, **attrs) -> dict:
        """voice/sfx → beat 결박 강제(P1) + artifact 생성 + 슬롯 채움.
        slot_id = atropos가 생성한 (track,beat_index) 슬롯. sync_clips_from_slots의 beat
        누락 버그를 write 시점에 봉쇄."""
        if track not in AUDIO_TRACKS:
            raise ValueError(f"audio track 아님: {track}")
        track_n = normalize_track(track)
        # ★ P1: beat 결박 트랙은 beat_index 필수 (소유권 검사보다 먼저 = 침묵 원천차단)
        if track_n in BEAT_BOUND_TRACKS and beat_index is None:
            raise BindingError(f"{track_n} 오디오에 beat_index 없음 (P1 음소거 위험)")
        art = self.write_artifact(planet, run_id, slot_id, storage_key=storage_key,
                                  duration_ms=duration_ms,
                                  attributes={"track": track_n, "beat_index": beat_index, **attrs})
        self.fill_slot(planet, slot_id, art["id"])
        return art

    # ── 측정 + 발행 ──
    def write_reel_metric(self, planet: str, run_id: str, workspace_id: Optional[str] = None, **fields) -> dict:
        """metis만 → reel_metrics write. 측정 데이터 저장."""
        self._assert("reel_metrics", "create", planet)
        self.assert_workspace_access(planet, workspace_id, "reel_metrics")  # SEC-4 strict 게이트
        payload = {"run_id": run_id, "workspace_id": workspace_id, **fields}
        return self._c.table("reel_metrics").insert(payload).execute().data[0]

    def upsert_reel_metrics(
        self,
        planet: str,
        rows: list[dict],
        on_conflict: str = "brand_slug,run_id,source,metric_date,utm_content",
    ) -> list[dict]:
        """metis 전용 배치 upsert — metrics_mirror가 이 경로를 써야 함(직접 upsert 금지).
        on_conflict: Supabase upsert conflict resolution column list."""
        self._assert("reel_metrics", "create", planet)
        if not rows:
            return []
        # SEC-4: strict 모드면 각 row의 workspace_id 결박 확인(배치 cross-tenant 방지).
        if _tenancy_strict():
            for r in rows:
                self.assert_workspace_access(planet, r.get("workspace_id"), "reel_metrics")
        result = self._c.table("reel_metrics").upsert(rows, on_conflict=on_conflict).execute()
        return result.data or rows

    def write_capi_event(self, planet: str, workspace_id: Optional[str],
                         pipa_consent: bool, **fields) -> dict:
        """hermes만 + PIPA 동의 검증 → capi_sent_events write.
        pipa_consent=false면 BindingError(법규위반).
        workspace_id=None: Phase 0 허용(경고만). Phase 1에서 필수화."""
        self._assert("capi_sent_events", "create", planet)
        if not pipa_consent:
            raise BindingError(f"PIPA §17 동의 없음 — CAPI 이벤트 전송 불가(법규 위반)")
        self.assert_workspace_access(planet, workspace_id, "capi_sent_events")  # SEC-4 strict 게이트
        payload = {"pipa_consent": pipa_consent, **fields}
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        return self._c.table("capi_sent_events").insert(payload).execute().data[0]

    def write_consent_log(self, planet: str, workspace_id: str,
                         user_id: str, consent_type: str, granted: bool, **fields) -> dict:
        """janus만 → consent_log write. PIPA 동의 기록.

        Args:
            planet: "janus" only
            workspace_id: 워크스페이스 ID (Phase 1.0 tenancy)
            user_id: 동의 대상자 ID
            consent_type: "overseas_transfer", "marketing", "analytics", ...
            granted: True/False (동의 여부)
        """
        self._assert("consent_log", "create", planet)
        payload = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "consent_type": consent_type,
            "granted": granted,
            "recorded_at": datetime.utcnow().isoformat(),
            **fields
        }
        return self._c.table("consent_log").insert(payload).execute().data[0]

    def write_meta_ad_account(self, planet: str, workspace_id: str,
                              account_id: str, system_user_id: str, **fields) -> dict:
        """hermes만 → meta_ad_accounts write. 광고 계정 레지스트리(다중테넌트).

        Args:
            planet: "hermes" only
            workspace_id: 테넌트 격리
            account_id: Meta 광고계정 ID
            system_user_id: Meta SystemUser ID (예: 61589513995813)
        """
        self._assert("meta_ad_accounts", "create", planet)
        payload = {
            "workspace_id": workspace_id,
            "account_id": account_id,
            "system_user_id": system_user_id,
            **fields
        }
        return self._c.table("meta_ad_accounts").insert(payload).execute().data[0]

    # ── brand_voice_chunk 거버넌스 (테넌시 격리) ──
    def write_brand_voice_chunk(self, planet: str, workspace: str,
                                source_kind: str, source_ref: str,
                                chunk_index: int, text: str, embedding: list[float],
                                **fields) -> dict:
        """janus만 → brand_voice_chunk write. 테넌시 격리(cross-tenant poisoning 방지).

        Args:
            planet: "janus" only (브랜드 콘텐츠 소유권)
            workspace: 워크스페이스 ID (필수, 비어있으면 거부)
            source_kind: 'website'|'past_script'|'approved_hook'|'manual'|'transcript'|'doc'
            source_ref: 출처 참조(e.g. URL, doc ID)
            chunk_index: 청크 순서(다중테넌트 고유성 키 일부)
            text: 청크 텍스트(최대 4000자 권장)
            embedding: 임베딩 벡터(1536차원)

        Raises:
            OwnershipError: janus가 아닌 행성이 쓰려 함
            BindingError: workspace가 없거나 비어있음 (테넌시 결박 필수)
        """
        self._assert("brand_voice_chunk", "create", planet)
        if not workspace or not isinstance(workspace, str) or workspace.strip() == "":
            raise BindingError("workspace_id 필수 — brand_voice_chunk 테넌티 격리 위반(cross-tenant 독 방지)")
        payload = {
            "workspace": workspace,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "chunk_index": chunk_index,
            "text": text[:4000],  # enforce max length
            "embedding": embedding,
            **fields
        }
        return self._c.table("brand_voice_chunk").insert(payload).execute().data[0]

    def write_brand_voice_chunks(self, planet: str, workspace: str,
                                  rows: list[dict]) -> list[dict]:
        """janus만 → brand_voice_chunk batch upsert. 테넌시 격리 강제.

        Args:
            planet: "janus" only
            workspace: 워크스페이스 ID (batch 전체에 적용, 필수)
            rows: 각 row는 {source_kind, source_ref, chunk_index, text, embedding, ...}

        Returns:
            upsert 결과 행 리스트

        Raises:
            OwnershipError: janus가 아닌 행성
            BindingError: workspace 없음 또는 비어있음
        """
        self._assert("brand_voice_chunk", "create", planet)
        if not workspace or not isinstance(workspace, str) or workspace.strip() == "":
            raise BindingError("workspace_id 필수 — brand_voice_chunk 테넌티 격리 위반(cross-tenant 독 방지)")
        if not rows:
            return []
        # 각 row에 workspace 주입 + text 길이 제한
        enriched = [
            {**r, "workspace": workspace, "text": r.get("text", "")[:4000]}
            for r in rows
        ]
        result = self._c.table("brand_voice_chunk").upsert(
            enriched, on_conflict="workspace,source_kind,source_ref,chunk_index"
        ).execute()
        return result.data or enriched

    # ── 테넌시 검증 (Phase 1.0 전환 준비) ──
    def assert_workspace_access(self, planet: str, workspace_id: Optional[str], table: str) -> None:
        """workspace_id 검증. Phase 0: None(단일테넌트) OK. Phase 1: 반드시 입력.

        SEC-4(2026-07-06): HIOB_TENANCY_STRICT=1이면 테넌시 테이블에 workspace_id 없을 시 raise
        (fail-closed·cross-tenant write 차단). 기본 off=경고만(현행 유지·byte-identical). founder가
        Phase 1 준비되면 env 하나로 전환 — 라이브 단일테넌트 write를 지금 깨지 않는다.
        """
        missing = (workspace_id is None or (isinstance(workspace_id, str) and not workspace_id.strip()))
        if missing and table in TENANCY_TABLES:
            if _tenancy_strict():
                raise BindingError(
                    f"[TENANCY_STRICT] {table}에 workspace_id 필수 — cross-tenant write 차단(fail-closed)."
                )
            import warnings
            warnings.warn(f"[Phase 1 준비] {table}에 workspace_id 필수(현재 경고만)")

