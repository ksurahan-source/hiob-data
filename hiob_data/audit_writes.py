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
# Also: .from("run").insert(  (supabase-js style used in some workers/scripts)
# B5: 테이블명 캡처를 대소문자·숫자 허용으로 넓혀 .table("Run")류 대소문자 혼용 write도 본다
# (governed 대조는 .lower()로 정규화 — DB 테이블은 소문자 관례).
# B6: multi-line `.table("run")\n  .update(` — join next non-empty line when chain incomplete.
# B7: include .delete() — raw-write scanner previously missed deletes (DB audit FAIL).
_WRITE_RE = re.compile(
    r"""\.(?:table|from)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)\s*\.\s*(insert|update|upsert|delete)\b"""
)
_TABLE_OPEN_RE = re.compile(
    r"""\.(?:table|from)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)\s*$"""
)
_OP_ONLY_RE = re.compile(r"""^\s*\.\s*(insert|update|upsert|delete)\b""")


def _owner_hint(table: str, op: str) -> str:
    if table in EXCLUSIVE_TABLES:
        return f"exclusive→{EXCLUSIVE_TABLES[table]}"
    rule = SHARED_TABLES.get(table, {})
    if op == "delete":
        # delete treated as update-class ownership for shared tables
        key = "update"
    else:
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
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        for m in _WRITE_RE.finditer(line):
            table, op = m.group(1), m.group(2)
            tbl = table.lower()  # B5: DB 테이블은 소문자 관례 — .table("Run")도 governed로 대조.
            if tbl not in GOVERNED_TABLES:
                continue
            out.append(Violation(path, i, tbl, op, _owner_hint(tbl, op), line.strip()[:100]))
        # B6 multi-line chain: .table("run")  next: .update(
        open_m = _TABLE_OPEN_RE.search(line)
        if open_m and i < len(lines):
            nxt = lines[i]  # 0-index next = line i (1-indexed next is i+1)
            op_m = _OP_ONLY_RE.match(nxt)
            if op_m:
                tbl = open_m.group(1).lower()
                if tbl in GOVERNED_TABLES:
                    op = op_m.group(1)
                    snippet = f"{line.strip()} {nxt.strip()}"[:100]
                    # de-dupe if same line already matched (shouldn't for open-only)
                    if not any(v.line == i and v.table == tbl and v.op == op for v in out):
                        out.append(Violation(path, i, tbl, op, _owner_hint(tbl, op), snippet))
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


def load_allowlist(path: str | Path) -> set[str]:
    """Allowlist 파일 → 'relpath:line:table:op' 키 집합.

    형식: 한 줄에 path:line:table:op 또는 path:line (table/op 생략 시 경로+줄만 매칭).
    # 주석·빈 줄 무시. 점진 이관: 마이그레이션할 때마다 줄 삭제.
    """
    p = Path(path)
    if not p.is_file():
        return set()
    keys: set[str] = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line)
    return keys


def violation_key(v: Violation, *, root: Path | None = None) -> str:
    """Allowlist 대조 키. root 주면 path를 root-relative로."""
    path = v.path
    if root is not None:
        try:
            path = str(Path(v.path).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            path = Path(v.path).name
    return f"{path}:{v.line}:{v.table}:{v.op}"


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    strict = "--strict" in args
    allowlist_path = None
    root = None
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--strict":
            i += 1
            continue
        if a == "--allowlist" and i + 1 < len(args):
            allowlist_path = args[i + 1]
            i += 2
            continue
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        paths.append(a)
        i += 1
    if not paths:
        paths = ["."]
    violations = scan_paths(paths)
    allow = load_allowlist(allowlist_path) if allowlist_path else set()
    root_p = Path(root) if root else None

    new_violations: list[Violation] = []
    allowed_hits = 0
    for v in violations:
        key = violation_key(v, root=root_p)
        # full key or path:line prefix match
        short = f"{Path(v.path).name}:{v.line}" if root_p is None else f"{Path(key.split(':')[0]).as_posix()}:{v.line}"
        rel_key = violation_key(v, root=root_p)
        basename_key = f"{Path(v.path).name}:{v.line}:{v.table}:{v.op}"
        if rel_key in allow or key in allow or basename_key in allow or short in allow:
            allowed_hits += 1
            continue
        # also accept path:line:table:op with forward slashes normalized
        if any(k.replace("\\", "/") == rel_key.replace("\\", "/") for k in allow):
            allowed_hits += 1
            continue
        new_violations.append(v)

    if not violations:
        print("✅ governor 우회 raw write 없음")
        return 0
    print(f"⚠️  governed 테이블 raw write {len(violations)}건 (allowlist 흡수 {allowed_hits} · 신규/미허용 {len(new_violations)}):")
    show = new_violations if (strict and allow) else violations
    for v in show:
        print("  " + v.format())
    by_table: dict[str, int] = {}
    for v in (new_violations if strict and allow else violations):
        by_table[v.table] = by_table.get(v.table, 0) + 1
    if by_table:
        print("  ── 테이블별:", ", ".join(f"{t}={n}" for t, n in sorted(by_table.items(), key=lambda x: -x[1])))
    if allowlist_path:
        print(f"  ── allowlist: {allowlist_path} ({len(allow)} entries, hits={allowed_hits})")
    # --strict: allowlist 있으면 신규만 실패, 없으면 전체 실패
    if not strict:
        return 0
    return 1 if new_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
