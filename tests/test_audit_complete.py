from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from hiob_data.audit_writes import (
    Violation,
    _owner_hint,
    load_allowlist,
    main,
    scan_paths,
    scan_source,
    violation_key,
)


def test_owner_hints_and_violation_format() -> None:
    assert "update" in _owner_hint("run", "delete")
    assert "create" in _owner_hint("run", "upsert")
    assert "?" in _owner_hint("unknown", "update")
    violation = Violation("worker.py", 2, "run", "update", "owner", "snippet")
    assert "worker.py:2" in violation.format()


def test_multiline_scanner_non_matches_and_duplicate_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    source = "\n".join(
        [
            'db.table("unknown")',
            "  .update({})",
            'db.table("run")',
            "  .select('*')",
            'db.table("run").update({})',
        ]
    )
    assert [(item.table, item.op) for item in scan_source(source, "worker.py")] == [("run", "update")]

    import hiob_data.audit_writes as audit

    class _WriteMatch:
        def group(self, index: int) -> str:
            return "run" if index == 1 else "update"

    class _TableMatch:
        def group(self, index: int) -> str:
            return "run"

    class _OperationMatch:
        def group(self, index: int) -> str:
            return "update"

    monkeypatch.setattr(
        audit,
        "_WRITE_RE",
        type("R", (), {"finditer": lambda self, line: [_WriteMatch()] if line == "run" else []})(),
    )
    monkeypatch.setattr(
        audit,
        "_TABLE_OPEN_RE",
        type("R", (), {"search": lambda self, line: _TableMatch() if line == "run" else None})(),
    )
    monkeypatch.setattr(
        audit,
        "_OP_ONLY_RE",
        type("R", (), {"match": lambda self, line: _OperationMatch() if line == "update" else None})(),
    )
    monkeypatch.setattr(audit, "GOVERNED_TABLES", frozenset({"run"}))
    assert len(audit.scan_source("run\nupdate", "worker.py")) == 1


def test_scan_paths_load_allowlist_and_keys(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    bad = source_dir / "bad.py"
    bad.write_text('db.table("run").insert({})', encoding="utf-8")
    (source_dir / "binary.py").write_bytes(b"\xff\xfe")
    ignored = source_dir / ".venv"
    ignored.mkdir()
    (ignored / "ignored.py").write_text('db.table("run").insert({})', encoding="utf-8")

    assert len(scan_paths([str(bad)])) == 1
    assert len(scan_paths([str(source_dir)])) == 1
    assert scan_paths([str(tmp_path / "missing")]) == []

    allow = tmp_path / "allow.txt"
    assert load_allowlist(allow) == set()
    allow.write_text("\n# comment\nbad.py:1:run:insert\n", encoding="utf-8")
    assert load_allowlist(allow) == {"bad.py:1:run:insert"}

    violation = scan_paths([str(bad)])[0]
    assert violation_key(violation, root=tmp_path).startswith("src/bad.py:1")
    assert violation_key(violation, root=tmp_path / "elsewhere").startswith("bad.py:1")
    assert violation_key(violation).startswith(str(bad))


def test_main_argument_and_allowlist_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("value = 1", encoding="utf-8")
    assert main(["--unknown", str(clean)]) == 0

    bad = tmp_path / "bad.py"
    bad.write_text('db.table("run").insert({})', encoding="utf-8")
    allow = tmp_path / "allow.txt"

    allow.write_text(f"{bad}:1:run:insert\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), str(bad)]) == 0
    allow.write_text("bad.py:1\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), str(bad)]) == 0
    allow.write_text("bad.py:1:run:insert\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), "--root", str(tmp_path), str(bad)]) == 0
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_bad = nested / "bad.py"
    nested_bad.write_text('db.table("run").insert({})', encoding="utf-8")
    allow.write_text("nested\\bad.py:1:run:insert\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), "--root", str(tmp_path), str(nested_bad)]) == 0
    allow.write_text("missing.py:1\n", encoding="utf-8")
    assert main(["--allowlist", str(allow), str(bad)]) == 0
    assert main([str(bad), "--strict"]) == 1
    assert "테이블별" in capsys.readouterr().out


def test_main_defaults_to_current_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    import hiob_data.audit_writes as audit

    seen: list[list[str]] = []
    monkeypatch.setattr(audit, "scan_paths", lambda paths: seen.append(paths) or [])
    assert audit.main([]) == 0
    assert seen == [["."]]


def test_module_entrypoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("value = 1", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["audit_writes", str(clean)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("hiob_data.audit_writes", run_name="__main__")
    assert exc.value.code == 0
