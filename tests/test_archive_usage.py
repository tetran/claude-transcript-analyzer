"""tests/test_archive_usage.py

scripts/archive_usage.py の retention + 月次アーカイブ機構 (Issue #30 Phase A2) の
コアロジック (boundary / partition / fingerprint / merge / run_archive integration)
のテスト。state marker / env / lock 系は test_archive_state.py に分離。

カバー範囲:
- UTC 月境界の partition / boundary 計算
- 構造的 fingerprint (tier1/2/3) と completeness
- archive immutability (既存 archive と新 event の merge)
- idempotent 連続実行
- .tmp 中断耐性
- 壊れた行の hot tier 保留
- naive / future-dated timestamp の hot tier 保留 (P6)
- 複数月一括 archive
"""
import gzip
import json

import pytest

from archive_test_helpers import (
    fp_event as _fp_event,
    make_event as _make_event,
    read_archive as _read_archive,
    read_hot_tier as _read_hot_tier,
    utc as _utc,
    write_hot_tier as _write_hot_tier,
)


# ---------------------------------------------------------------------------
# TestBoundaryCalculation: UTC 月境界の 7 ケース列挙
# ---------------------------------------------------------------------------


class TestBoundaryCalculation:
    def test_issue_example_2026_04_27(self, archive_module):
        """Issue #30 example: now=2026-04-27, retention=180 → archive ≤ 2025-09."""
        now = _utc(2026, 4, 27)
        targets = archive_module._calculate_archive_target_months(
            now,
            180,
            available_months={
                archive_module.YearMonth(2025, 8),
                archive_module.YearMonth(2025, 9),
                archive_module.YearMonth(2025, 10),
                archive_module.YearMonth(2025, 11),
                archive_module.YearMonth(2026, 4),
            },
        )
        assert archive_module.YearMonth(2025, 8) in targets
        assert archive_module.YearMonth(2025, 9) in targets
        assert archive_module.YearMonth(2025, 10) not in targets
        assert archive_module.YearMonth(2025, 11) not in targets
        assert archive_module.YearMonth(2026, 4) not in targets

    def test_first_of_month_midnight(self, archive_module):
        """now=2026-05-01 00:00:00Z, retention=180 → archive ≤ 2025-10."""
        now = _utc(2026, 5, 1)
        targets = archive_module._calculate_archive_target_months(
            now,
            180,
            available_months={
                archive_module.YearMonth(2025, 10),
                archive_module.YearMonth(2025, 11),
            },
        )
        assert archive_module.YearMonth(2025, 10) in targets
        assert archive_module.YearMonth(2025, 11) not in targets

    def test_last_of_month_end_of_day(self, archive_module):
        """now=2026-04-30 23:59:59Z, retention=180 → archive ≤ 2025-10."""
        now = _utc(2026, 4, 30, 23, 59, 59)
        targets = archive_module._calculate_archive_target_months(
            now,
            180,
            available_months={
                archive_module.YearMonth(2025, 10),
                archive_module.YearMonth(2025, 11),
            },
        )
        assert archive_module.YearMonth(2025, 10) in targets
        assert archive_module.YearMonth(2025, 11) not in targets

    def test_leap_year_2024_february(self, archive_module):
        """2024 閏年 2 月跨ぎ: now=2024-08-01, retention=180 → archive ≤ 2024-01."""
        now = _utc(2024, 8, 1)
        targets = archive_module._calculate_archive_target_months(
            now,
            180,
            available_months={
                archive_module.YearMonth(2024, 1),
                archive_module.YearMonth(2024, 2),
                archive_module.YearMonth(2024, 3),
            },
        )
        assert archive_module.YearMonth(2024, 1) in targets
        assert archive_module.YearMonth(2024, 2) not in targets
        assert archive_module.YearMonth(2024, 3) not in targets

    def test_leap_year_2028_february(self, archive_module):
        """2028 閏年: now=2028-08-01, retention=180 → archive ≤ 2028-01."""
        now = _utc(2028, 8, 1)
        targets = archive_module._calculate_archive_target_months(
            now,
            180,
            available_months={
                archive_module.YearMonth(2028, 1),
                archive_module.YearMonth(2028, 2),
            },
        )
        assert archive_module.YearMonth(2028, 1) in targets
        assert archive_module.YearMonth(2028, 2) not in targets

    def test_retention_one_day(self, archive_module):
        """retention=1 day: now=2026-04-01 → cutoff=2026-03-31 → archive ≤ 2026-02."""
        now = _utc(2026, 4, 1)
        targets = archive_module._calculate_archive_target_months(
            now,
            1,
            available_months={
                archive_module.YearMonth(2026, 1),
                archive_module.YearMonth(2026, 2),
                archive_module.YearMonth(2026, 3),
            },
        )
        assert archive_module.YearMonth(2026, 1) in targets
        assert archive_module.YearMonth(2026, 2) in targets
        assert archive_module.YearMonth(2026, 3) not in targets

    def test_cutoff_at_first_of_month(self, archive_module):
        """retention=31 → cutoff=2026-03-01. 2026-02 月末は cutoff より前 → archive 対象."""
        now = _utc(2026, 4, 1)
        targets = archive_module._calculate_archive_target_months(
            now,
            31,
            available_months={
                archive_module.YearMonth(2026, 2),
                archive_module.YearMonth(2026, 3),
            },
        )
        assert archive_module.YearMonth(2026, 2) in targets
        assert archive_module.YearMonth(2026, 3) not in targets


# ---------------------------------------------------------------------------
# TestPartitionEvents
# ---------------------------------------------------------------------------


class TestPartitionEvents:
    def test_events_partition_by_month(self, archive_module):
        events_with_lines = [
            (_make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t1"), "L1"),
            (_make_event("skill_tool", _utc(2025, 9, 10), tool_use_id="t2"), "L2"),
            (_make_event("skill_tool", _utc(2026, 4, 1), tool_use_id="t3"), "L3"),
        ]
        target_months = {archive_module.YearMonth(2025, 8), archive_module.YearMonth(2025, 9)}
        buckets, hot_remainder = archive_module._partition_events(events_with_lines, target_months)

        assert len(buckets[archive_module.YearMonth(2025, 8)]) == 1
        assert len(buckets[archive_module.YearMonth(2025, 9)]) == 1
        assert len(hot_remainder) == 1
        assert hot_remainder[0][1] == "L3"

    def test_naive_timestamp_kept_in_hot(self, archive_module):
        """naive timestamp は hot tier に保留 (P6)."""
        ev = {"event_type": "skill_tool", "session_id": "s", "timestamp": "2025-08-15T00:00:00"}
        target_months = {archive_module.YearMonth(2025, 8)}
        buckets, hot_remainder = archive_module._partition_events([(ev, "L_naive")], target_months)
        assert buckets == {}
        assert hot_remainder == [(ev, "L_naive")]

    def test_future_dated_timestamp_kept_in_hot(self, archive_module):
        """clock skew で future-dated な event は hot に保留 (P6)."""
        future = _utc(2099, 12, 31)
        ev = _make_event("skill_tool", future, tool_use_id="t_future")
        target_months = {archive_module.YearMonth(2025, 8)}
        buckets, hot_remainder = archive_module._partition_events([(ev, "L_f")], target_months)
        assert buckets == {}
        assert len(hot_remainder) == 1

    def test_invalid_timestamp_kept_in_hot(self, archive_module):
        """timestamp 欠落 / 不正 ISO → hot に保留."""
        target_months = {archive_module.YearMonth(2025, 8)}
        ev_no_ts = {"event_type": "x", "session_id": "s"}
        ev_bad_ts = {"event_type": "x", "session_id": "s", "timestamp": "not-a-date"}
        buckets, hot_remainder = archive_module._partition_events(
            [(ev_no_ts, "L1"), (ev_bad_ts, "L2")],
            target_months,
        )
        assert buckets == {}
        assert len(hot_remainder) == 2


# ---------------------------------------------------------------------------
# TestStructuralFingerprint
# ---------------------------------------------------------------------------


class TestStructuralFingerprint:
    def test_tier1_when_tool_use_id_present(self, archive_module):
        ev1 = _fp_event("skill_tool", tool_use_id="toolu_x", skill="/foo")
        # 新フィールド追加 (schema 進化) でも同じ tier1 fingerprint
        ev2 = _fp_event("skill_tool", tool_use_id="toolu_x", skill="/foo", permission_mode="default")
        assert archive_module._structural_fingerprint(ev1) == archive_module._structural_fingerprint(ev2)
        assert archive_module._structural_fingerprint(ev1)[0] == "t1"

    def test_tier2_dispatch_for_each_event_type(self, archive_module):
        """全 event_type が tier1 (tool_use_id) または tier2 (dispatch) で分類される (P4 completeness)."""
        all_event_types = {
            "skill_tool",
            "user_slash_command",
            "subagent_start",
            "subagent_lifecycle_start",
            "subagent_stop",
            "session_start",
            "session_end",
            "compact_start",
            "compact_end",
            "notification",
            "instructions_loaded",
        }
        for et in all_event_types:
            # tool_use_id なし event の fingerprint は tier1 か tier2 (sha1 でない)
            ev = {"event_type": et, "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"}
            fp = archive_module._structural_fingerprint(ev)
            assert fp[0] in ("t1", "t2"), f"event_type={et} fell through to tier3 sha1: {fp}"

    def test_tier2_user_slash_command_legacy_no_source(self, archive_module):
        """旧 schema (source 欠落) と新 schema (source=expansion) は同じ fingerprint."""
        ev_legacy = _fp_event("user_slash_command", skill="/foo")
        ev_explicit = _fp_event("user_slash_command", skill="/foo", source="expansion")
        assert archive_module._structural_fingerprint(ev_legacy) == archive_module._structural_fingerprint(ev_explicit)

    def test_tier2_notification_value_variants_are_distinct(self, archive_module):
        """notification_type=permission と permission_prompt は別 fingerprint (値域追加吸収)."""
        ev_a = _fp_event("notification", notification_type="permission")
        ev_b = _fp_event("notification", notification_type="permission_prompt")
        assert archive_module._structural_fingerprint(ev_a) != archive_module._structural_fingerprint(ev_b)

    def test_tier3_sha1_fallback_for_unknown_event_type(self, archive_module):
        """未知 event_type は tier3 sha1 fallback."""
        ev = {"event_type": "unknown_future_event", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"}
        fp = archive_module._structural_fingerprint(ev)
        assert fp[0] == "t3"


# ---------------------------------------------------------------------------
# TestArchiveMergeAndDedup
# ---------------------------------------------------------------------------


class TestArchiveMergeAndDedup:
    def test_fast_path_line_equality(self, archive_module, tmp_path):
        """既存 archive と完全に同じ line は merged で 1 件."""
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        existing_event = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t1")
        existing_line = json.dumps(existing_event, ensure_ascii=False)
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(existing_line + "\n")

        merged = archive_module._merge_with_existing_archive(
            archive_module.YearMonth(2025, 8),
            [(existing_event, existing_line)],
            archive_dir,
        )
        assert len(merged) == 1

    def test_schema_evolution_immutability(self, archive_module, tmp_path):
        """schema 進化 (新フィールド追加) で line 不一致 → fingerprint 一致 → 既存優先."""
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        old_event = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t1")
        old_line = json.dumps(old_event, ensure_ascii=False)
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(old_line + "\n")

        # 新 schema: permission_mode が追加された同じ event
        new_event = dict(old_event)
        new_event["permission_mode"] = "default"
        new_line = json.dumps(new_event, ensure_ascii=False)
        merged = archive_module._merge_with_existing_archive(
            archive_module.YearMonth(2025, 8),
            [(new_event, new_line)],
            archive_dir,
        )
        assert len(merged) == 1
        # immutability: 既存 (permission_mode 無し) を採用
        assert merged[0][0] == old_event

    def test_corrupted_existing_archive_raises(self, archive_module, tmp_path):
        """codex P1: 既存 archive が壊れて gzip.open が失敗したら例外送出。
        silent に new_entries だけで rewrite して既存履歴を消すパスは取らない."""
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 壊れた既存 .gz (gzip header なし)
        (archive_dir / "2025-08.jsonl.gz").write_text("not a gzip file")

        ev_new = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t_new")
        with pytest.raises(archive_module.ArchiveReadError):
            archive_module._merge_with_existing_archive(
                archive_module.YearMonth(2025, 8),
                [(ev_new, json.dumps(ev_new, ensure_ascii=False))],
                archive_dir,
            )

    def test_distinct_events_both_kept(self, archive_module, tmp_path):
        """fingerprint も line も違う event は両方残る."""
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        ev_existing = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t_old")
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(json.dumps(ev_existing, ensure_ascii=False) + "\n")

        ev_new = _make_event("skill_tool", _utc(2025, 8, 2), tool_use_id="t_new")
        new_line = json.dumps(ev_new, ensure_ascii=False)
        merged = archive_module._merge_with_existing_archive(
            archive_module.YearMonth(2025, 8),
            [(ev_new, new_line)],
            archive_dir,
        )
        assert len(merged) == 2

    def test_merge_with_no_existing_archive(self, archive_module, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        ev = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t1")
        merged = archive_module._merge_with_existing_archive(
            archive_module.YearMonth(2025, 8),
            [(ev, json.dumps(ev, ensure_ascii=False))],
            archive_dir,
        )
        assert len(merged) == 1

    def test_malformed_existing_line_preserved_in_merge(self, archive_module, tmp_path):
        """codex P2 #2: 既存 archive の malformed 行は merge 結果に raw のまま残る。

        archive immutability 契約 — silent discard すると既存履歴が rewrite で
        永久消失する。preserve することで「壊れた行は手で見えるまま残す」を担保。
        """
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        valid_event = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t_old")
        valid_line = json.dumps(valid_event, ensure_ascii=False)
        malformed_line = '{"event_type": "skill_tool", "broken'
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(valid_line + "\n")
            f.write(malformed_line + "\n")

        new_event = _make_event("skill_tool", _utc(2025, 8, 2), tool_use_id="t_new")
        new_line = json.dumps(new_event, ensure_ascii=False)
        merged = archive_module._merge_with_existing_archive(
            archive_module.YearMonth(2025, 8),
            [(new_event, new_line)],
            archive_dir,
        )
        raw_lines = [line for _ev, line in merged]
        assert valid_line in raw_lines
        assert malformed_line in raw_lines
        assert new_line in raw_lines

    def test_malformed_archive_line_survives_re_archive(self, archive_module, tmp_path):
        """end-to-end: malformed 行を持つ archive を再 archive (backfill) しても消えない。

        rescan_transcripts.py --append 等で同月の event が hot tier に再出現し
        run_archive が走るシナリオ。既存 archive を再 rewrite するパスで malformed
        行が消えると immutability 契約違反になる (codex P2 #2)。
        """
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        existing_event = _make_event("skill_tool", _utc(2025, 8, 1), tool_use_id="t_old")
        existing_line = json.dumps(existing_event, ensure_ascii=False)
        malformed_line = '{"broken json'
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(existing_line + "\n")
            f.write(malformed_line + "\n")

        backfill_event = _make_event("skill_tool", _utc(2025, 8, 5), tool_use_id="t_back")
        _write_hot_tier(data_file, [backfill_event])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        with gzip.open(archive_dir / "2025-08.jsonl.gz", "rt", encoding="utf-8") as f:
            raw_lines = [line.rstrip("\n") for line in f if line.strip()]
        assert existing_line in raw_lines
        assert malformed_line in raw_lines, (
            "malformed line must survive re-archive (immutability contract)"
        )
        assert json.dumps(backfill_event, ensure_ascii=False) in raw_lines


# ---------------------------------------------------------------------------
# TestRunArchiveIntegration: 主要シナリオ
# ---------------------------------------------------------------------------


class TestRunArchiveIntegration:
    def test_basic_archive_creates_gzip_and_rewrites_hot(self, archive_module, tmp_path):
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        assert "2025-08" in result.archived_months
        assert result.archived_event_count == 1

        archived = _read_archive(archive_dir, "2025-08")
        assert archived == [old]

        hot = _read_hot_tier(data_file)
        assert hot == [recent]

    def test_idempotent_consecutive_runs(self, archive_module, tmp_path):
        """連続実行で同じ結果に収束 (line equality / archive 状態 / hot 状態)."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        state_file = tmp_path / "state.json"
        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        first_archive_bytes = (archive_dir / "2025-08.jsonl.gz").read_bytes()
        first_hot_bytes = data_file.read_bytes()

        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        assert (archive_dir / "2025-08.jsonl.gz").read_bytes() == first_archive_bytes
        assert data_file.read_bytes() == first_hot_bytes

    def test_multi_month_batch(self, archive_module, tmp_path):
        """2 ヶ月放置相当: 1 job で 2 つの .jsonl.gz が生成される."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        events = [
            _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_aug"),
            _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_sep"),
            _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent"),
        ]
        _write_hot_tier(data_file, events)

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        assert "2025-08" in result.archived_months
        assert "2025-09" in result.archived_months
        assert (archive_dir / "2025-08.jsonl.gz").exists()
        assert (archive_dir / "2025-09.jsonl.gz").exists()
        assert _read_hot_tier(data_file) == [events[2]]

    def test_empty_hot_tier(self, archive_module, tmp_path):
        """usage.jsonl 不在で run_archive → crash しない."""
        paths = archive_module.ArchivePaths(
            data_file=tmp_path / "nonexistent.jsonl",
            archive_dir=tmp_path / "archive",
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        assert result.archived_event_count == 0
        assert result.hot_remainder_count == 0

    def test_no_archive_targets_only_state_update(self, archive_module, tmp_path):
        """全 event が retention 内 → archive 0 件 + state 更新."""
        data_file = tmp_path / "usage.jsonl"
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        assert result.archived_event_count == 0
        assert _read_hot_tier(data_file) == [recent]

    def test_corrupted_existing_archive_keeps_events_in_hot_tier(self, archive_module, tmp_path):
        """codex P1: 既存 archive が読めない月は archive せず hot tier に保留する。
        既存 archive ファイルは触らずそのまま残す (silent 上書き消失を防ぐ)."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        (archive_dir / "2025-08.jsonl.gz").write_text("corrupted not-a-gzip")

        # 2025-08 は壊れた archive がある月、2025-09 は健全に archive される月
        old_aug = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_aug")
        old_sep = _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_sep")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old_aug, old_sep, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        # 2025-08 は archive されない (壊れた既存 archive を silent 上書きしない)
        assert "2025-08" not in result.archived_months
        # 2025-09 は健全に archive される
        assert "2025-09" in result.archived_months
        # 既存の壊れた archive は触らずそのまま残る
        assert (archive_dir / "2025-08.jsonl.gz").read_text() == "corrupted not-a-gzip"
        # 2025-08 の event は hot tier に保留 (data loss 回避)
        hot = _read_hot_tier(data_file)
        assert old_aug in hot
        # 2025-09 の event と recent は archive 済み / hot remainder どちらに収まる
        assert old_sep not in hot
        assert recent in hot

    def test_broken_lines_kept_in_hot(self, archive_module, tmp_path):
        """parse error 行は hot tier に保留 (data loss 回避)."""
        data_file = tmp_path / "usage.jsonl"
        data_file.parent.mkdir(parents=True, exist_ok=True)
        ev_old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        ev_recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        with data_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(ev_old) + "\n")
            f.write("this is broken json {{{ \n")
            f.write(json.dumps(ev_recent) + "\n")

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        assert result.broken_lines_kept == 1

        # hot tier に broken 行と recent が残る
        raw_lines = data_file.read_text(encoding="utf-8").splitlines()
        assert any("broken json" in line for line in raw_lines)
        assert any("t_recent" in line for line in raw_lines)
        # archive には old が入る
        assert _read_archive(tmp_path / "archive", "2025-08") == [ev_old]


# ---------------------------------------------------------------------------
# TestTmpResume: .tmp 残骸からの復帰
# ---------------------------------------------------------------------------


class TestTmpResume:
    def test_leftover_archive_tmp_overwritten(self, archive_module, tmp_path):
        """archive 出力途中で死んで .tmp が残った状態 → 次回正常実行で上書き."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        # 残骸 .tmp ファイル
        leftover = archive_dir / "2025-08.jsonl.gz.99999.tmp"
        leftover.write_bytes(b"corrupted leftover")

        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        _write_hot_tier(data_file, [old])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        # 正常な archive が生成される
        assert _read_archive(archive_dir, "2025-08") == [old]

    def test_leftover_hot_tmp_overwritten(self, archive_module, tmp_path):
        """hot tier rewrite 中に死んで .tmp が残っても、次回正常実行で上書きされる."""
        data_file = tmp_path / "usage.jsonl"
        leftover = data_file.with_suffix(data_file.suffix + ".99999.tmp")
        data_file.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_text("bad leftover\n", encoding="utf-8")

        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=tmp_path / "state.json",
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)
        assert _read_hot_tier(data_file) == [recent]
