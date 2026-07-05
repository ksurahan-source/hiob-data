"""Governor 우회 감사 — governed 테이블에 raw write하는 곳을 file:line으로 리포트.

노드맵 #4: "DataGovernor는 존재하나 런타임 강제가 없다 — app.py 등이 raw sb.table().insert()로
우회." 이 스캐너는 **report-only**(런타임 변경 0·차단 없음): governed 테이블(SHARED+EXCLUSIVE)에
직접 write(insert/update/upsert)하는 라인을 찾아 소유 규칙과 대조해 보고한다. 점진 이관의 지도.

안전: 순수 정적 분석. import guard/CI에서 `--strict`로 exit 1 가능하나 기본은 report-only.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .ownership import EXCLUSIVE_TABLES, SHARED_TABLES

GOVERNED_TABLES: frozenset[str] = frozenset(SHARED_TABLES) | frozenset(EXCLUSIVE_TABLES)

# .table("run").insert(  /  .table('clip').update(  /  .table("hook").upsert(
# B5: 테이블명 캡처를 대소문자·숫자 허용으로 넓혀 .table("Run")류 대소문자 혼용 write도 본다
# (governed 대조는 .lower()로 정규화 — DB 테이블은 소문자 관례).
_WRITE_RE = re.compile(r"""\.table\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)\s*\.\s*(insert|update|upsert)\b""")


def _owner_hint(table: str, op: str) -> str:
    if table in EXCLUSIVE_TABLES:
        return f"exclusive→{EXCLUSIVE_TABLES[table]}"
    rule = SHARED_TABLES.get(table, {})
    key = "create" if op in ("insert", "upsert") else "update"
    owners = ", ".join(sorted(rule.get(key, set()))) or "?"
    return f"shared {key}→{{{owners}}}"


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    table: str
    op: str
    owner_hint: str
    snippet: str

    def format(self) -> str:
        return f"{self.path}:{self.line}  .table('{self.table}').{self.op}()  [{self.owner_hint}]  {self.snippet}"


def scan_source(text: str, path: str = "<mem>") -> list[Violation]:
    """소스 텍스트 → governed 테이블 raw write 위반 목록. governor.py 자체는 제외."""
    if path.endswith(("governor.py", "ownership.py", "audit_writes.py")):
        return []  # governor 구현 자신은 정당한 write
    out: list[Violation] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _WRITE_RE.finditer(line):
            table, op = m.group(1), m.group(2)
            tbl = table.lower()  # B5: DB 테이블은 소문자 관례 — .table("Run")도 governed로 대조.
            if tbl not in GOVERNED_TABLES:
                continue
            out.append(Violation(path, i, tbl, op, _owner_hint(tbl, op), line.strip()[:100]))
    return out


def scan_paths(paths: list[str]) -> list[Violation]:
    """경로(파일/디렉토리) → 모든 .py 스캔. 디렉토리는 재귀."""
    out: list[Violation] = []
    for p in paths:
        pp = Path(p)
        files = [pp] if pp.is_file() else pp.rglob("*.py")
        for f in files:
            if any(seg in ("__pycache__", ".git", "node_modules", ".venv") for seg in f.parts):
                continue
            try:
                out.extend(scan_source(f.read_text(encoding="utf-8"), str(f)))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    strict = "--strict" in args
    paths = [a for a in args if not a.startswith("--")] or ["."]
    violations = scan_paths(paths)
    if not violations:
        print("✅ governor 우회 raw write 없음")
        return 0
    print(f"⚠️  governed 테이블 raw write {len(violations)}건 (governor 우회 — 점진 이관 대상):")
    for v in violations:
        print("  " + v.format())
    by_table: dict[str, int] = {}
    for v in violations:
        by_table[v.table] = by_table.get(v.table, 0) + 1
    print("  ── 테이블별:", ", ".join(f"{t}={n}" for t, n in sorted(by_table.items(), key=lambda x: -x[1])))
    return 1 if strict else 0  # 기본 report-only(비차단), --strict만 CI 실패


if __name__ == "__main__":
    raise SystemExit(main())
