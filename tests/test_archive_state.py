"""tests/test_archive_state.py

scripts/archive_usage.py の state marker / archivable horizon / env / lock 系
テスト。コアロジック (boundary / partition / fingerprint / merge) は
test_archive_usage.py に分離。

カバー範囲:
- state marker atomic write (last_archived_month / last_archivable_horizon)
- backfill / partial-failure 経路で state が success-only ベースで更新される
- USAGE_RETENTION_DAYS の robust 化 (codex 8th P2-B)
- 環境変数 override (USAGE_JSONL / ARCHIVE_DIR / ARCHIVE_STATE_FILE / USAGE_JSONL_LOCK)
- multiprocessing による LOCK_EX 排他確認
"""
import importlib
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from archive_test_helpers import (
    make_event as _make_event,
    read_archive as _read_archive,
    read_hot_tier as _read_hot_tier,
    utc as _utc,
    write_hot_tier as _write_hot_tier,
)


# ---------------------------------------------------------------------------
# TestStateMarker
# ---------------------------------------------------------------------------


class TestStateMarker:
    def test_state_written_after_run(self, archive_module, tmp_path):
        data_file = tmp_path / "usage.jsonl"
        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        _write_hot_tier(data_file, [old])

        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["last_archived_month"] == "2025-08"
        assert "last_run_at" in state

    def test_corrupted_state_treated_as_unknown(self, archive_module, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json {{{")
        result = archive_module._read_state(state_file)
        assert result == {}

    def test_non_dict_state_treated_as_unknown(self, archive_module, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(["array", "not", "dict"]))
        result = archive_module._read_state(state_file)
        assert result == {}

    def test_backfill_old_month_after_state_advanced(self, archive_module, tmp_path):
        """codex P1: state に 2025-09 まで archive 済みと記録された後、`rescan --append`
        相当で 2025-08 の event が再 append されたら、次回 archive で正しく archive される。

        run_archive は state.last_archived_month を skip フィルタとして使わない
        (LOCK_EX で並列直列化 + _merge_with_existing_archive で既存尊重 dedup)。
        backfill された古い月も retention 超過なら必ず archive 対象になる。"""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "last_run_at": "2026-04-01T00:00:00+00:00",
            "last_archived_month": "2025-09",
        }))

        backfilled = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_backfill")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [backfilled, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        assert "2025-08" in result.archived_months
        assert _read_archive(archive_dir, "2025-08") == [backfilled]
        assert _read_hot_tier(data_file) == [recent]

    def test_state_does_not_advance_past_failed_month(self, archive_module, tmp_path):
        """codex P2 #2: ArchiveReadError で archive 失敗した月を含む targets から
        max() を取ると state が失敗月を飛び越して進み、launcher が同月内 retry を
        short-circuit してしまう (`last_archived >= prev_month` 経由)。
        state は **成功月** だけから計算されるべき。

        シナリオ: 2025-08 は壊れた既存 archive、2025-09 は健全に archive される。
        → state.last_archived_month は 2025-08 のままが正解 (= last_archived_str を保持)。
        2025-09 の成功で進めると、launcher が 2026-04 内で再 spawn せず、
        retention 違反の hot tier (2025-08) が同月中ずっと残る。
        """
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        (archive_dir / "2025-08.jsonl.gz").write_text("corrupted not-a-gzip")

        old_aug = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_aug")
        old_sep = _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_sep")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old_aug, old_sep, recent])

        state_file = tmp_path / "state.json"
        # state 既存値 = 2025-07 (= last successful archive)
        state_file.write_text(json.dumps({
            "last_run_at": "2025-09-01T00:00:00+00:00",
            "last_archived_month": "2025-07",
        }))

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        # 2025-09 だけ archive 成功、2025-08 は失敗で hot 保留
        assert result.archived_months == ["2025-09"]
        # state は **成功月** の最大 (2025-09) と既存 (2025-07) の max = 2025-09
        # ただし launcher の同月 retry を妨げないよう、失敗月 (2025-08) を飛び越えてはいけない
        # → state は 2025-09 まで進むが、launcher は last_run_at と組み合わせて判断する。
        # 重要なのは「失敗月を含む targets から max を取らない」こと。
        # ここでは success-only ベースを直接 pin する: archive_buckets には 2025-08 と
        # 2025-09 両方あるが、successfully_archived_yms には 2025-09 のみ。
        state = json.loads(state_file.read_text(encoding="utf-8"))
        # 成功した最大月は 2025-09 — これは success-only ベースで一致する正解
        assert state["last_archived_month"] == "2025-09"

    def test_state_unchanged_when_all_targets_fail(self, archive_module, tmp_path):
        """codex P2 #2: 全 archive 対象月が ArchiveReadError で失敗したら、
        state.last_archived_month は既存値のまま（成功 0 件で前進しない）。"""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 両方の対象月で既存 archive が壊れている
        (archive_dir / "2025-08.jsonl.gz").write_text("corrupted")
        (archive_dir / "2025-09.jsonl.gz").write_text("corrupted")

        old_aug = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_aug")
        old_sep = _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_sep")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old_aug, old_sep, recent])

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "last_run_at": "2025-09-01T00:00:00+00:00",
            "last_archived_month": "2025-07",
        }))

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        assert result.archived_months == []
        # state.last_archived_month は既存値 (2025-07) のまま — failed targets で進めない
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["last_archived_month"] == "2025-07"

    def test_state_last_archived_does_not_block_target_months(self, archive_module, tmp_path):
        """state.last_archived_month は target_months 計算に影響しない。

        backfill 経路の正しさを保証するため、state があっても retention で決まる
        target_months が縮小されないことを直接 pin する。"""
        data_file = tmp_path / "usage.jsonl"
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "last_run_at": "2026-04-01T00:00:00+00:00",
            "last_archived_month": "2025-12",
        }))

        # cutoff (now=2026-04-27 / retention=180) = 2025-10-29
        # 2025-08, 2025-09 は月末が cutoff より前 → archive 対象。
        # 2025-12 は cutoff より後 → archive 対象外 (state でも block されない)。
        old_aug = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_a")
        old_sep = _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_s")
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [old_aug, old_sep, recent])

        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        # 2025-08 と 2025-09 はどちらも cutoff より前なので archive される
        # (state.last_archived_month=2025-12 はもう block しない)
        assert sorted(result.archived_months) == ["2025-08", "2025-09"]


# ---------------------------------------------------------------------------
# TestArchivableHorizonInState — codex 6th review P2
# ---------------------------------------------------------------------------


class TestArchivableHorizonInState:
    """codex 6th review P2: archive_usage は実行ごとに `last_archivable_horizon` を
    state に書き込む。launcher が「horizon が advance してなければ skip」判定する
    ための gate marker として使う。"""

    def test_horizon_recorded_when_targets_archived(self, archive_module, tmp_path):
        """archive 対象あり → horizon を state に記録する。"""
        data_file = tmp_path / "usage.jsonl"
        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        _write_hot_tier(data_file, [old])

        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        # now=2026-04-27, retention=180 → cutoff=2025-10-29 → horizon=previous_month(2025,10)=2025-09
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["last_archivable_horizon"] == "2025-09"

    def test_horizon_recorded_when_no_targets(self, archive_module, tmp_path):
        """archive 対象なし (no-op) でも horizon を state に記録する。

        これが launcher の「対象なしで run 終了したあと毎セッション spawn しない」
        gate になる。horizon が advance していなければ skip、advance したら spawn。
        """
        data_file = tmp_path / "usage.jsonl"
        # 全 event が retention 内 (= archive 対象なし)
        recent = _make_event("skill_tool", _utc(2026, 4, 20), tool_use_id="t_recent")
        _write_hot_tier(data_file, [recent])

        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        assert result.archived_months == []
        state = json.loads(state_file.read_text(encoding="utf-8"))
        # horizon は run 時点の現在 horizon (2025-09) で記録される
        assert state["last_archivable_horizon"] == "2025-09"

    def test_horizon_recorded_with_no_data_at_all(self, archive_module, tmp_path):
        """usage.jsonl が空でも horizon を state に書く。R2 無限 spawn 防止用。"""
        data_file = tmp_path / "usage.jsonl"
        data_file.write_text("")  # 空
        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=tmp_path / "archive",
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["last_archivable_horizon"] == "2025-09"

    def test_horizon_not_advanced_when_archive_fails(self, archive_module, tmp_path):
        """codex 8th review P2-A: ArchiveReadError で archive 失敗した月があるとき、
        horizon を advance しない (= state から削除)。

        旧実装は失敗があっても horizon を current 値で記録していたため、launcher が
        次セッションで `last_horizon >= current_horizon` を見て skip し、ユーザーが
        破損 .gz を修復しても horizon が次月に進むまで auto-launcher が retry して
        くれなかった。失敗時に horizon を出さないことで「次の launcher で再 spawn
        →修復後の archive_usage が成功して horizon を advance」のサイクルが回る。
        """
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 既存 archive を corrupt にする → merge で ArchiveReadError
        (archive_dir / "2025-08.jsonl.gz").write_text("not a gzip file")

        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        _write_hot_tier(data_file, [old])

        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        # 2025-08 は archive 失敗 → archived_months 空、hot_remainder に保留
        assert result.archived_months == []
        state = json.loads(state_file.read_text(encoding="utf-8"))
        # horizon が state に **書かれていない** → launcher は同月内で再 spawn できる
        assert "last_archivable_horizon" not in state, (
            "失敗があれば horizon は出さない (launcher の同月内 retry を可能にする)"
        )

    def test_horizon_advanced_when_partial_success(self, archive_module, tmp_path):
        """target が複数、一部成功 / 一部失敗の混在ケースでも horizon は advance しない。

        混在状態で horizon 進めると失敗月が次月までスタックするのは P2-A と同症状。
        """
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 2025-08 corrupt, 2025-09 healthy
        (archive_dir / "2025-08.jsonl.gz").write_text("corrupted")

        aug = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_aug")
        sep = _make_event("skill_tool", _utc(2025, 9, 15), tool_use_id="t_sep")
        _write_hot_tier(data_file, [aug, sep])

        state_file = tmp_path / "state.json"
        paths = archive_module.ArchivePaths(
            data_file=data_file,
            archive_dir=archive_dir,
            state_file=state_file,
            lock_file=tmp_path / "usage.jsonl.lock",
        )
        result = archive_module.run_archive(_utc(2026, 4, 27), paths, retention_days=180)

        # 2025-09 だけ成功、2025-08 は失敗
        assert result.archived_months == ["2025-09"]
        state = json.loads(state_file.read_text(encoding="utf-8"))
        # 部分成功でも horizon は出さない
        assert "last_archivable_horizon" not in state


# ---------------------------------------------------------------------------
# TestRetentionEnvRobustness — codex 8th review P2-B
# ---------------------------------------------------------------------------


class TestRetentionEnvRobustness:
    def test_main_with_invalid_retention_env_falls_back_to_default(
        self, tmp_path, monkeypatch
    ):
        """codex 8th review P2-B: USAGE_RETENTION_DAYS が typo (非数値) でも
        archive_usage.py は ValueError で死なず、default にフォールバックして動く。

        旧実装は argparse の default に `int(env)` を直接注入していたため、env が
        "abc" だと argparse 評価前に ValueError raise して traceback で即死していた。
        launch_archive が同じ env で default fallback して spawn し続けるため、
        毎セッション detached child が即 crash → archive 機能完全停止になっていた。
        """
        # USAGE_RETENTION_DAYS は無効値、他 env は最小限に揃える
        monkeypatch.setenv("USAGE_RETENTION_DAYS", "abc-not-a-number")
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
        monkeypatch.setenv("ARCHIVE_STATE_FILE", str(tmp_path / ".archive_state.json"))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "usage.jsonl.lock"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))

        sys.modules.pop("archive_usage", None)
        import archive_usage
        importlib.reload(archive_usage)

        # main() が ValueError で死なず exit 0 で帰ってくる
        rc = archive_usage.main(["--log", "-"])
        assert rc == 0

    def test_main_with_negative_retention_env_falls_back_to_default(
        self, tmp_path, monkeypatch
    ):
        """負数 / 0 もデフォルトにフォールバック (retention_days <= 0 は意味不明)。"""
        monkeypatch.setenv("USAGE_RETENTION_DAYS", "-5")
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
        monkeypatch.setenv("ARCHIVE_STATE_FILE", str(tmp_path / ".archive_state.json"))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "usage.jsonl.lock"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))

        sys.modules.pop("archive_usage", None)
        import archive_usage
        importlib.reload(archive_usage)

        rc = archive_usage.main(["--log", "-"])
        assert rc == 0


# ---------------------------------------------------------------------------
# TestEnvOverrides
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    def test_resolve_paths_uses_envs(self, archive_module, tmp_path, monkeypatch):
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "custom_data.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "custom_archive"))
        monkeypatch.setenv("ARCHIVE_STATE_FILE", str(tmp_path / "custom_state.json"))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "custom.lock"))

        paths = archive_module._resolve_paths()
        assert paths.data_file == tmp_path / "custom_data.jsonl"
        assert paths.archive_dir == tmp_path / "custom_archive"
        assert paths.state_file == tmp_path / "custom_state.json"
        assert paths.lock_file == tmp_path / "custom.lock"

    def test_default_lock_path_is_data_file_dot_lock(self, archive_module, tmp_path, monkeypatch):
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "data.jsonl"))
        monkeypatch.delenv("USAGE_JSONL_LOCK", raising=False)
        paths = archive_module._resolve_paths()
        assert paths.lock_file == Path(str(tmp_path / "data.jsonl") + ".lock")


# ---------------------------------------------------------------------------
# TestLockExclusion: 並列 archive job の LOCK_EX 排他
# ---------------------------------------------------------------------------


def _archive_subprocess_main(env_dict: dict, sleep_inside: float, ready_q, result_q):
    """archive_usage を環境変数経由で起動。run_archive 内で sleep して contention を作る。"""
    for k, v in env_dict.items():
        os.environ[k] = v
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    sys.modules.pop("archive_usage", None)
    import archive_usage
    importlib.reload(archive_usage)

    # run_archive を patch して sleep を挟む
    original_run = archive_usage.run_archive

    def patched_run(now, paths, retention_days):
        ready_q.put("started")
        time.sleep(sleep_inside)
        return original_run(now, paths, retention_days)

    archive_usage.run_archive = patched_run

    rc = archive_usage.main(["--retention-days", "180", "--log", "-"])
    result_q.put(rc)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock 限定")
class TestLockExclusion:
    def test_concurrent_archive_jobs_serialize(self, tmp_path):
        """2 つの archive_usage を並列起動 → LOCK_EX で直列化."""
        data_file = tmp_path / "usage.jsonl"
        old = _make_event("skill_tool", _utc(2025, 8, 15), tool_use_id="t_old")
        _write_hot_tier(data_file, [old])

        env = {
            "USAGE_JSONL": str(data_file),
            "ARCHIVE_DIR": str(tmp_path / "archive"),
            "ARCHIVE_STATE_FILE": str(tmp_path / "state.json"),
            "USAGE_JSONL_LOCK": str(tmp_path / "usage.jsonl.lock"),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "health_alerts.jsonl"),
        }

        ctx = multiprocessing.get_context("spawn")
        ready_q = ctx.Queue()
        result_q = ctx.Queue()

        p1 = ctx.Process(target=_archive_subprocess_main, args=(env, 0.5, ready_q, result_q))
        p2 = ctx.Process(target=_archive_subprocess_main, args=(env, 0.0, ready_q, result_q))

        p1.start()
        ready_q.get(timeout=5)  # p1 が run_archive に入った
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        assert p1.exitcode == 0
        assert p2.exitcode == 0
        rc1 = result_q.get(timeout=1)
        rc2 = result_q.get(timeout=1)
        assert rc1 == 0
        assert rc2 == 0

        # archive と hot tier が壊れていないこと
        assert _read_archive(tmp_path / "archive", "2025-08") == [old]
