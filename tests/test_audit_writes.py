"""Governor 우회 감사 스캐너 테스트 (report-only 정적 분석)."""
from hiob_data import scan_source
from hiob_data.audit_writes import GOVERNED_TABLES, main


def test_flags_raw_write_to_governed_table():
    src = 'sb.table("run").update({"x": 1}).eq("id", rid).execute()'
    v = scan_source(src, "app.py")
    assert len(v) == 1
    assert v[0].table == "run" and v[0].op == "update"
    assert "athena" in v[0].owner_hint  # shared update owners 포함


def test_flags_insert_and_upsert():
    src = '\n'.join([
        'sb.table("clip").insert(row).execute()',
        "sb.table('hook').upsert(h).execute()",
    ])
    v = scan_source(src, "w.py")
    assert {x.op for x in v} == {"insert", "upsert"}
    assert {x.table for x in v} == {"clip", "hook"}


def test_ignores_ungoverned_table():
    src = 'sb.table("some_random_log").insert(x).execute()'
    assert scan_source(src, "x.py") == []


def test_exclusive_owner_hint():
    src = 'sb.table("listing").update(x).execute()'
    v = scan_source(src, "x.py")
    assert v and "exclusive→janus" in v[0].owner_hint


def test_governor_impl_files_excluded():
    src = 'sb.table("run").insert(x)'
    assert scan_source(src, "hiob_data/governor.py") == []
    assert scan_source(src, "hiob_data/audit_writes.py") == []


def test_governed_tables_cover_shared_and_exclusive():
    assert "run" in GOVERNED_TABLES and "listing" in GOVERNED_TABLES
    assert "reel_metrics" in GOVERNED_TABLES


def test_main_report_only_exit_zero(capsys):
    # report-only 기본: 위반 있어도 exit 0(비차단). --strict만 1.
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.py")
        open(p, "w").write('sb.table("run").insert(x).execute()')
        assert main([p]) == 0          # 기본 비차단
        assert main([p, "--strict"]) == 1  # strict 차단
    out = capsys.readouterr().out
    assert "raw write" in out or "governor 우회" in out


def test_flags_mixed_case_table_name():
    # B5 회귀: .table("Run")(대문자 혼용)도 governed "run"으로 대조돼 잡힘(정규식 소문자 한정 수정).
    src = 'sb.table("Run").insert(row).execute()'
    v = scan_source(src, "app.py")
    assert len(v) == 1
    assert v[0].table == "run" and v[0].op == "insert"


def test_still_ignores_truly_ungoverned_mixed_case():
    src = 'sb.table("RandomLog").insert(x).execute()'
    assert scan_source(src, "x.py") == []


def test_allowlist_absorbs_known_violations(tmp_path):
    from hiob_data.audit_writes import main
    bad = tmp_path / "worker.py"
    bad.write_text('sb.table("run").insert(x).execute()\n', encoding="utf-8")
    allow = tmp_path / "allow.txt"
    # basename form
    allow.write_text(f"worker.py:1:run:insert\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), str(bad)]) == 0
    # unknown line must fail strict
    allow.write_text("worker.py:99:run:insert\n", encoding="utf-8")
    assert main(["--strict", "--allowlist", str(allow), str(bad)]) == 1


def test_flags_from_api_style_writes():
    """B6: .from('run').update(...) used by some supabase clients."""
    src = "sb.from('run').update({'status': 'x'}).eq('id', rid).execute()"
    v = scan_source(src, "edge.py")
    assert len(v) == 1
    assert v[0].table == "run" and v[0].op == "update"


def test_flags_multiline_table_then_op():
    """B6: chained write split across lines must not slip past the scanner."""
    src = 'sb.table("clip")\n    .update(patch)\n    .eq("id", cid)\n    .execute()\n'
    v = scan_source(src, "w.py")
    assert any(x.table == "clip" and x.op == "update" for x in v)


def test_repo_allowlist_not_emptied_while_scripts_have_hits():
    """Guard against race that zeros allowlist_raw_writes.txt while scripts still write."""
    from pathlib import Path
    from hiob_data.audit_writes import load_allowlist, scan_paths, main

    root = Path(__file__).resolve().parents[2] / "hiob"
    # When monorepo is sibling of hiob-data
    if not root.exists():
        root = Path.home() / "hiob"
    allow = Path(__file__).resolve().parents[1] / "allowlist_raw_writes.txt"
    assert allow.is_file(), "allowlist_raw_writes.txt missing"
    entries = load_allowlist(allow)
    scripts = root / "apps" / "modal" / "scripts"
    if not scripts.is_dir():
        return  # environment without monorepo checkout
    hits = scan_paths([str(scripts)])
    if hits:
        assert len(entries) > 0, "allowlist emptied while scripts still have governed raw writes"
        rc = main([
            "--strict",
            "--allowlist",
            str(allow),
            "--root",
            str(root),
            str(root / "apps" / "modal"),
        ])
        assert rc == 0, "strict audit must pass against current allowlist"
