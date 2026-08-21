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


def _violation(path: str, line_number: int, table: str, op: str, snippet: str) -> "Violation":
    return Violation(path, line_number, table, op, _owner_hint(table, op), snippet[:100])


def _direct_violations(line: str, path: str, line_number: int) -> list["Violation"]:
    violations: list[Violation] = []
    for match in _WRITE_RE.finditer(line):
        table, op = match.group(1).lower(), match.group(2)
        if table in GOVERNED_TABLES:
            violations.append(_violation(path, line_number, table, op, line.strip()))
    return violations


def _multiline_violation(
    lines: list[str],
    index: int,
    path: str,
) -> "Violation | None":
    if index + 1 >= len(lines):
        return None
    table_match = _TABLE_OPEN_RE.search(lines[index])
    operation_match = _OP_ONLY_RE.match(lines[index + 1])
    if not table_match or not operation_match:
        return None
    table = table_match.group(1).lower()
    if table not in GOVERNED_TABLES:
        return None
    operation = operation_match.group(1)
    snippet = f"{lines[index].strip()} {lines[index + 1].strip()}"
    return _violation(path, index + 1, table, operation, snippet)


def _same_write(left: "Violation", right: "Violation") -> bool:
    return (left.line, left.table, left.op) == (right.line, right.table, right.op)


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
    for index, line in enumerate(lines):
        direct = _direct_violations(line, path, index + 1)
        out.extend(direct)
        multiline = _multiline_violation(lines, index, path)
        if multiline and not any(_same_write(item, multiline) for item in direct):
            out.append(multiline)
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


def _parse_args(args: list[str]) -> tuple[bool, str | None, str | None, list[str]]:
    strict = "--strict" in args
    allowlist_path = None
    root = None
    paths: list[str] = []
    index = 0
    value_options = {"--allowlist", "--root"}
    while index < len(args):
        argument = args[index]
        if argument in value_options and index + 1 < len(args):
            value = args[index + 1]
            if argument == "--allowlist":
                allowlist_path = value
            else:
                root = value
            index += 2
            continue
        if not argument.startswith("--"):
            paths.append(argument)
        index += 1
    return strict, allowlist_path, root, paths or ["."]


def _allowlist_keys(violation: Violation, root: Path | None) -> tuple[str, str, str]:
    full_key = violation_key(violation, root=root)
    if root is None:
        short_key = f"{Path(violation.path).name}:{violation.line}"
    else:
        short_key = f"{Path(full_key.split(':')[0]).as_posix()}:{violation.line}"
    basename_key = f"{Path(violation.path).name}:{violation.line}:{violation.table}:{violation.op}"
    return full_key, short_key, basename_key


def _is_allowed(violation: Violation, allowlist: set[str], root: Path | None) -> bool:
    keys = _allowlist_keys(violation, root)
    normalized = keys[0].replace("\\", "/")
    return any(key in allowlist for key in keys) or any(
        candidate.replace("\\", "/") == normalized for candidate in allowlist
    )


def _partition_violations(
    violations: list[Violation],
    allowlist: set[str],
    root: Path | None,
) -> tuple[list[Violation], int]:
    new_violations = [item for item in violations if not _is_allowed(item, allowlist, root)]
    return new_violations, len(violations) - len(new_violations)


def _print_report(
    violations: list[Violation],
    new_violations: list[Violation],
    *,
    strict: bool,
    allowlist_path: str | None,
    allowlist: set[str],
    allowed_hits: int,
) -> None:
    print(f"⚠️  governed 테이블 raw write {len(violations)}건 (allowlist 흡수 {allowed_hits} · 신규/미허용 {len(new_violations)}):")
    shown = new_violations if strict and allowlist else violations
    for violation in shown:
        print("  " + violation.format())
    by_table: dict[str, int] = {}
    counted = new_violations if strict and allowlist else violations
    for violation in counted:
        by_table[violation.table] = by_table.get(violation.table, 0) + 1
    if by_table:
        summary = sorted(by_table.items(), key=lambda item: -item[1])
        print("  ── 테이블별:", ", ".join(f"{table}={count}" for table, count in summary))
    if allowlist_path:
        print(f"  ── allowlist: {allowlist_path} ({len(allowlist)} entries, hits={allowed_hits})")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    strict, allowlist_path, root, paths = _parse_args(args)
    violations = scan_paths(paths)
    allow = load_allowlist(allowlist_path) if allowlist_path else set()
    root_p = Path(root) if root else None
    new_violations, allowed_hits = _partition_violations(violations, allow, root_p)

    if not violations:
        print("✅ governor 우회 raw write 없음")
        return 0
    _print_report(
        violations,
        new_violations,
        strict=strict,
        allowlist_path=allowlist_path,
        allowlist=allow,
        allowed_hits=allowed_hits,
    )
    # --strict: allowlist 있으면 신규만 실패, 없으면 전체 실패
    if not strict:
        return 0
    return 1 if new_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
