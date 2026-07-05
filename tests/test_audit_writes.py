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
    assert "governor 우회" in out


def test_flags_mixed_case_table_name():
    # B5 회귀: .table("Run")(대문자 혼용)도 governed "run"으로 대조돼 잡힘(정규식 소문자 한정 수정).
    src = 'sb.table("Run").insert(row).execute()'
    v = scan_source(src, "app.py")
    assert len(v) == 1
    assert v[0].table == "run" and v[0].op == "insert"


def test_still_ignores_truly_ungoverned_mixed_case():
    src = 'sb.table("RandomLog").insert(x).execute()'
    assert scan_source(src, "x.py") == []
