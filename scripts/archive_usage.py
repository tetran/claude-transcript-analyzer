#!/usr/bin/env python3
"""scripts/archive_usage.py

Issue #30 retention + 月次アーカイブ機構の本体。

usage.jsonl から retention (default 180 日) を超えた event を月単位で
archive/YYYY-MM.jsonl.gz に gzip 圧縮して移動し、hot tier をサイズ上限のある
定常状態に保つ。Archive 後の event は raw のまま gzip で保存され、reader 側で
opt-in (`--include-archive`) で読み込み可能。

並列耐性:
- LOCK_EX を取得して実行 (record_*.py 側は LOCK_SH | LOCK_NB × 5 retry)
- LOCK 取得後に state marker を再 read で skip 判定 (race-free 二重起動回避)
- すべての .gz / hot rewrite は <name>.<pid>.tmp + os.replace で atomic
- 中断耐性: tmp 残骸が残っても次回実行で os.replace により正常上書き

UTC 統一:
- now / cutoff / 月境界判定すべて datetime.now(timezone.utc) ベース
- naive timestamp event は archive 対象外で hot tier に保留 (data loss 回避)
- future-dated event も hot tier に保留 (clock skew 防御)

環境変数:
- USAGE_JSONL: hot tier path (default ~/.claude/transcript-analyzer/usage.jsonl)
- ARCHIVE_DIR: archive ディレクトリ (default ~/.claude/transcript-analyzer/archive)
- ARCHIVE_STATE_FILE: state marker (default ~/.claude/transcript-analyzer/.archive_state.json)
- USAGE_JSONL_LOCK: lock file path (default <data_file>.lock)
- USAGE_RETENTION_DAYS: retention 日数 (default 180)
- HEALTH_ALERTS_JSONL: drop alert 記録先 (hooks/_append.py が参照)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Optional

# `_lock` を import するため hooks/ を sys.path に追加 (Issue #44)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _lock  # noqa: E402


DEFAULT_RETENTION_DAYS = 180

_DEFAULT_DATA_FILE = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"


def _resolve_default_retention_days() -> int:
    """``USAGE_RETENTION_DAYS`` env を robust に解釈して default を返す。

    codex 8th P2-B: 旧実装は argparse の default に `int(env)` を直接注入していたため、
    env が "abc" だと argparse 評価前に ValueError raise → archive_usage.py が
    lock/state 書き込み前に traceback で即死。launch_archive は env malformed を
    silent に default fallback する非対称な仕様だったため、毎セッション detached
    child が即 crash → archive 機能完全停止 (env 修正まで進まない死に螺旋) を起こす。
    """
    raw = os.environ.get("USAGE_RETENTION_DAYS")
    if raw is None:
        return DEFAULT_RETENTION_DAYS
    try:
        value = int(raw)
        if value <= 0:
            return DEFAULT_RETENTION_DAYS
        return value
    except ValueError:
        return DEFAULT_RETENTION_DAYS


class ArchiveReadError(OSError):
    """既存 archive ファイルの読み込みに失敗 (rewrite 不可 / silent 上書き禁止)。

    呼び出し側 (run_archive) はこの例外を catch してその月を archive せず、
    既存 archive ファイルは触らないまま event を hot tier に保留する。
    silent に new_entries だけで rewrite すると `rescan_transcripts.py --append`
    で hot tier に古い event が再出現したとき既存 archive の履歴を消失させる
    silent data loss 経路になるため、必ず明示的な例外で halt させる (codex P1)。
    """


# ---------------------------------------------------------------------------
# 値オブジェクト
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class YearMonth:
    """UTC 月。order=True で比較可能 (set 内で sorted 等が動く)。"""
    year: int
    month: int

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @classmethod
    def from_string(cls, s: str) -> "YearMonth":
        y, m = s.split("-")
        return cls(int(y), int(m))

    def month_end_utc(self) -> datetime:
        """月末の UTC datetime を `次月初 - 1us` で安全に計算。"""
        if self.month == 12:
            next_first = datetime(self.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_first = datetime(self.year, self.month + 1, 1, tzinfo=timezone.utc)
        return next_first - timedelta(microseconds=1)


@dataclass
class ArchivePaths:
    data_file: Path
    archive_dir: Path
    state_file: Path
    lock_file: Path


@dataclass
class ArchiveResult:
    archived_months: list[str]
    archived_event_count: int
    hot_remainder_count: int
    broken_lines_kept: int


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------


def _resolve_paths() -> ArchivePaths:
    data_file = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_DATA_FILE)))
    archive_dir = Path(
        os.environ.get("ARCHIVE_DIR", str(data_file.parent / "archive"))
    )
    state_file = Path(
        os.environ.get(
            "ARCHIVE_STATE_FILE", str(data_file.parent / ".archive_state.json")
        )
    )
    lock_env = os.environ.get("USAGE_JSONL_LOCK")
    lock_file = Path(lock_env) if lock_env else Path(str(data_file) + ".lock")
    return ArchivePaths(data_file, archive_dir, state_file, lock_file)


# ---------------------------------------------------------------------------
# Boundary calculation
# ---------------------------------------------------------------------------


def _calculate_archive_target_months(
    now: datetime,
    retention_days: int,
    available_months: set[YearMonth],
) -> set[YearMonth]:
    """カレンダー月の月末 (UTC) が `now - retention` より前にある月を返す。

    available_months との intersection で実在月のみに絞る (空 month bucket 生成回避)。
    """
    cutoff = now - timedelta(days=retention_days)
    return {ym for ym in available_months if ym.month_end_utc() < cutoff}


def _calculate_archivable_horizon(now: datetime, retention_days: int) -> YearMonth:
    """現在の archivable horizon (= eligible な最大月) を返す (codex 6th P2)。

    archive_usage の eligibility 条件 `month_end_utc < cutoff` と整合する最大月は
    `cutoff` の **前月** (cutoff = now - retention)。導出:
        - cutoff の calendar 月をそのまま採用すると、月末が cutoff より strictly
          前であることが保証されない (cutoff が当月 mid なら月末は cutoff より後)
        - 前月の月末は必ず cutoff より strictly 前 (= 必ず eligible)

    launcher が「horizon が advance してなければ skip」判定するために state に
    記録する。データ存在の有無に関わらず計算可能 (純粋関数)。
    """
    cutoff = now - timedelta(days=retention_days)
    if cutoff.month == 1:
        return YearMonth(cutoff.year - 1, 12)
    return YearMonth(cutoff.year, cutoff.month - 1)


def _event_year_month(event: dict) -> Optional[YearMonth]:
    """event timestamp から UTC YearMonth を抽出。
    naive / 不正 / 欠落の場合は None を返し、partition 側で hot tier に保留させる。
    """
    ts = event.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    dt_utc = dt.astimezone(timezone.utc)
    return YearMonth(dt_utc.year, dt_utc.month)


# ---------------------------------------------------------------------------
# 構造的 fingerprint (Issue #30 dedup 戦略)
# ---------------------------------------------------------------------------


# tier 2 secondary_key dispatch — schema 進化規約で永続化 (docs/transcript-format.md)
_TIER2_DISPATCH: dict[str, tuple[str, ...]] = {
    "notification": ("notification_type",),
    "session_start": ("source", "model"),
    "session_end": ("reason",),
    "compact_start": ("trigger",),
    "compact_end": ("trigger",),
    "instructions_loaded": ("file_path",),
    "subagent_lifecycle_start": ("subagent_type",),
    "subagent_stop": ("subagent_id",),
    # tool_use_id 欠落時 fallback (P4) — 通常は tier1 で済む
    "skill_tool": ("skill",),
    "subagent_start": ("subagent_type",),
    # user_slash_command は旧 schema (source 欠落) を吸収するため特別扱い
    "user_slash_command": ("skill", "source"),
}


def _structural_fingerprint(event: dict) -> tuple:
    """schema 進化耐性のある event fingerprint。

    - tier 1: tool_use_id がある event 系 (skill_tool / subagent_start 通常パス)
    - tier 2: secondary_key dispatch (上記 _TIER2_DISPATCH)
    - tier 3: sha1(json.dumps(sort_keys=True)) ultimate fallback
    """
    event_type = event.get("event_type", "")
    session_id = event.get("session_id", "")
    timestamp = event.get("timestamp", "")
    tool_use_id = event.get("tool_use_id")

    if tool_use_id:
        return ("t1", event_type, session_id, timestamp, tool_use_id)

    secondary_keys = _TIER2_DISPATCH.get(event_type)
    if secondary_keys is not None:
        if event_type == "user_slash_command":
            secondary_values: tuple = (
                event.get("skill", ""),
                event.get("source", "expansion"),
            )
        else:
            secondary_values = tuple(event.get(k, "") for k in secondary_keys)
        return ("t2", event_type, session_id, timestamp) + secondary_values

    serialized = json.dumps(event, sort_keys=True, ensure_ascii=False)
    return ("t3", hashlib.sha1(serialized.encode("utf-8")).hexdigest())


# ---------------------------------------------------------------------------
# Read & Partition
# ---------------------------------------------------------------------------


def _read_hot_tier(data_file: Path) -> tuple[list[tuple[dict, str]], list[str]]:
    """usage.jsonl 全行を (event, raw_line) として読み込む。
    parse error 行は broken_lines として別 list に退避 (data loss 回避)。
    """
    if not data_file.exists():
        return [], []
    parsed: list[tuple[dict, str]] = []
    broken: list[str] = []
    with data_file.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                parsed.append((event, line))
            except json.JSONDecodeError:
                broken.append(line)
    return parsed, broken


def _partition_events(
    events_with_lines: list[tuple[dict, str]],
    target_months: set[YearMonth],
) -> tuple[dict[YearMonth, list[tuple[dict, str]]], list[tuple[dict, str]]]:
    """events を target_months に該当するか否かで partition。

    naive / future-dated / 不正 timestamp event はすべて hot_remainder に入る
    (年月特定不能 = archive 対象外)。
    """
    archive_buckets: dict[YearMonth, list[tuple[dict, str]]] = {}
    hot_remainder: list[tuple[dict, str]] = []
    for event, line in events_with_lines:
        ym = _event_year_month(event)
        if ym is not None and ym in target_months:
            archive_buckets.setdefault(ym, []).append((event, line))
        else:
            hot_remainder.append((event, line))
    return archive_buckets, hot_remainder


# ---------------------------------------------------------------------------
# Merge & dedup with existing archive (immutability)
# ---------------------------------------------------------------------------


def _merge_with_existing_archive(
    month: YearMonth,
    new_entries: list[tuple[dict, str]],
    archive_dir: Path,
) -> list[tuple[dict, str]]:
    """既存 archive と new_entries を merge。

    fast path: line-level 完全等価 → skip
    slow path: 構造的 fingerprint 一致 → 既存優先 (archive immutability)
    どちらにも該当しない → merged に追加
    """
    archive_path = archive_dir / f"{month}.jsonl.gz"
    if not archive_path.exists():
        return list(new_entries)

    existing: list[tuple[dict, str]] = []
    existing_lines: set[str] = set()
    existing_fps: set[tuple] = set()

    try:
        with gzip.open(archive_path, "rt", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    # codex P2 #2: malformed 行は raw のまま保持して rewrite で書き戻す。
                    # silent discard だと既存 archive を再 rewrite するパス
                    # (`rescan_transcripts.py --append` 後の再 archive 等) で
                    # 履歴が永久消失する → archive immutability 契約違反になる。
                    # halt 案 (ArchiveReadError と同列扱い) は 1 行壊れただけで
                    # その月の archive が無限に止まる運用詰みリスクがあるため不採用。
                    # preserve は壊れたデータを増やさず、次回も rewrite で raw のまま
                    # 残るので「人手 jq で読んで直す」前提の運用と整合する。
                    sys.stderr.write(
                        f"archive_usage: preserving malformed line in {archive_path}\n"
                    )
                    existing.append((None, line))
                    existing_lines.add(line)
                    continue
                existing.append((ev, line))
                existing_lines.add(line)
                existing_fps.add(_structural_fingerprint(ev))
    except OSError as exc:
        # codex P1: silent fallback で new_entries だけ rewrite すると既存 archive の
        # 履歴を上書き消失させる。呼び出し側で明示的に halt できるよう例外を投げる。
        raise ArchiveReadError(
            f"failed to read existing archive {archive_path}: {exc}"
        ) from exc

    merged = list(existing)
    for event, line in new_entries:
        if line in existing_lines:
            continue
        fp = _structural_fingerprint(event)
        if fp in existing_fps:
            continue
        merged.append((event, line))
        existing_lines.add(line)
        existing_fps.add(fp)
    return merged


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------


def _atomic_write_gzip(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _atomic_rewrite_hot(data_file: Path, lines: list[str]) -> None:
    data_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = data_file.with_name(data_file.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp, data_file)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# State marker
# ---------------------------------------------------------------------------


def _read_state(state_file: Path) -> dict:
    """state を sanitized dict として読む。不在 / 壊れた JSON / 期待外型 → 空 dict。"""
    if not state_file.exists():
        return {}
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict = {}
    if isinstance(raw.get("last_run_at"), str):
        result["last_run_at"] = raw["last_run_at"]
    last_arch = raw.get("last_archived_month")
    if isinstance(last_arch, str):
        try:
            YearMonth.from_string(last_arch)
            result["last_archived_month"] = last_arch
        except (ValueError, AttributeError):
            pass
    return result


def _write_state(
    state_file: Path,
    last_run_at: str,
    last_archived_month: Optional[str],
    last_archivable_horizon: Optional[str] = None,
) -> None:
    state: dict = {"last_run_at": last_run_at}
    if last_archived_month is not None:
        state["last_archived_month"] = last_archived_month
    if last_archivable_horizon is not None:
        # codex 6th P2: archive_usage 実行時点の archivable horizon を記録。
        # launcher が「horizon が advance してなければ skip」判定するための gate。
        # 旧 last_run_at == this_month skip では retention boundary が月末を跨いだ
        # mid-month で立つ archive 対象を次月まで遅延させる bug があった。
        state["last_archivable_horizon"] = last_archivable_horizon
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_name(state_file.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, state_file)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------


def _read_last_archived_month(state_file: Path) -> Optional[str]:
    """state.last_archived_month を読む。

    NOTE (codex P1 / Issue #30): この値を target_months の skip フィルタとしては
    **使わない**。理由:
    1. LOCK_EX で archive job が直列化されているため、再 run でも結果は idempotent
    2. 既存 archive との dedup は _merge_with_existing_archive (line equality fast
       path → 構造的 fingerprint → 既存尊重) が担い、衝突は構造的に解消される
    3. state 経由でフィルタすると `rescan_transcripts.py --append` 等の backfill
       で過去月が hot tier に再出現したとき、永続的に archive されない bug になる
    state は launch_archive 側の skip 判定 (= spawn 不要判定) と監査ログだけに用いる。
    """
    return _read_state(state_file).get("last_archived_month")


def _archive_buckets_to_gz(
    archive_buckets: dict,
    archive_dir: Path,
    hot_remainder: list,
) -> tuple[list[str], list[YearMonth], int]:
    """archive 対象月の event を .gz に書き出す。失敗月は hot_remainder に戻す。

    codex P1: 既存 archive が読めない月は archive せず hot tier に保留。
    既存 archive ファイル自体は触らず (silent 上書き消失を防ぐ)、次回以降の
    ジョブで人手修復後に正しく merge できる状態を維持する。
    """
    archived_months: list[str] = []
    successfully_archived_yms: list[YearMonth] = []
    archived_count = 0
    for ym in sorted(archive_buckets.keys()):
        new_entries = archive_buckets[ym]
        try:
            merged = _merge_with_existing_archive(ym, new_entries, archive_dir)
        except ArchiveReadError:
            hot_remainder.extend(new_entries)
            continue
        _atomic_write_gzip(
            archive_dir / f"{ym}.jsonl.gz",
            [line for _ev, line in merged],
        )
        archived_months.append(str(ym))
        successfully_archived_yms.append(ym)
        archived_count += len(new_entries)
    return archived_months, successfully_archived_yms, archived_count


def _compute_new_last_archived(
    successfully_archived_yms: list[YearMonth],
    last_archived_str: Optional[str],
) -> Optional[str]:
    """new last_archived_month を計算する。

    state は **これまでの最大値** と「今回 archive した最大値」の max を採用する
    (backfill 経路で古い月を archive しても last_archived_month を逆行させない)。
    codex P2 #2: archive に **成功した** 月だけから max を取る。failed 月
    (ArchiveReadError) を含めると state が失敗月を飛び越して進み、launcher が
    `last_archived >= prev_month` で同月内 retry を短絡してしまう。
    """
    candidate = max(successfully_archived_yms) if successfully_archived_yms else None
    if candidate is None:
        return last_archived_str
    if not last_archived_str:
        return str(candidate)
    try:
        prev_ym = YearMonth.from_string(last_archived_str)
        return str(max(prev_ym, candidate))
    except ValueError:
        return str(candidate)


def _finalize_state(
    state_file: Path,
    now_iso: str,
    last_archived_str: Optional[str],
    horizon_str: str,
    successfully_archived_yms: list[YearMonth],
    archive_buckets_count: int,
) -> None:
    """state marker を atomic 更新する。

    codex 8th P2-A: target に失敗があれば horizon は出さない (= None を書く)。
    current 値で記録すると launcher は次セッションで `last_horizon >= current` を
    見て skip してしまい、ユーザーが .gz 修復しても次月まで retry できない。
    全成功なら horizon を記録、失敗があれば None で出して launcher の同月内 retry を許す。
    """
    new_last_str = _compute_new_last_archived(
        successfully_archived_yms, last_archived_str
    )
    all_targets_succeeded = len(successfully_archived_yms) == archive_buckets_count
    horizon_to_write = horizon_str if all_targets_succeeded else None
    _write_state(state_file, now_iso, new_last_str, horizon_to_write)


def run_archive(
    now: datetime,
    paths: ArchivePaths,
    retention_days: int,
) -> ArchiveResult:
    """archive job 本体。LOCK_EX 取得済みである前提。

    順序 (strict — R4 中間状態の重複カウント回避):
    1. hot tier 全行 read
    2. state を再 read で skip 判定 (race-free 二重起動回避)
    3. 月単位で archive 出力 (各 .gz は atomic)
    4. hot tier rewrite (1 回 atomic)
    5. state marker 更新

    archive 全月の .gz 書き込み完了 → hot rewrite の順で、中間状態で
    「hot にも archive にもある」becomes 多重カウントを構造的に防ぐ。
    """
    parsed_entries, broken_lines = _read_hot_tier(paths.data_file)

    target_months = _calculate_archive_target_months(
        now,
        retention_days,
        {
            ym
            for ym in (_event_year_month(ev) for ev, _line in parsed_entries)
            if ym is not None
        },
    )
    last_archived_str = _read_last_archived_month(paths.state_file)
    # codex 6th P2: 実行ごとに archivable horizon を計算して state に記録する。
    # launcher が「horizon が advance してなければ skip」を gate にできる。
    horizon_str = str(_calculate_archivable_horizon(now, retention_days))

    if not target_months:
        _write_state(paths.state_file, now.isoformat(), last_archived_str, horizon_str)
        return ArchiveResult(
            archived_months=[],
            archived_event_count=0,
            hot_remainder_count=len(parsed_entries),
            broken_lines_kept=len(broken_lines),
        )

    archive_buckets, hot_remainder = _partition_events(parsed_entries, target_months)
    archived_months, successfully_archived_yms, archived_count = _archive_buckets_to_gz(
        archive_buckets, paths.archive_dir, hot_remainder
    )

    _atomic_rewrite_hot(
        paths.data_file,
        [line for _ev, line in hot_remainder] + broken_lines,
    )
    _finalize_state(
        paths.state_file,
        now.isoformat(),
        last_archived_str,
        horizon_str,
        successfully_archived_yms,
        len(archive_buckets),
    )

    return ArchiveResult(
        archived_months=archived_months,
        archived_event_count=archived_count,
        hot_remainder_count=len(hot_remainder),
        broken_lines_kept=len(broken_lines),
    )


# ---------------------------------------------------------------------------
# CLI entrypoint with LOCK_EX
# ---------------------------------------------------------------------------


def _open_log(spec: str, default_log_path: Path) -> IO[str]:
    """log destination を解決して open。'-' は stderr、'auto' は default_log_path。"""
    if spec == "-":
        return sys.stderr
    if spec == "auto":
        default_log_path.parent.mkdir(parents=True, exist_ok=True)
        return open(default_log_path, "a", encoding="utf-8")
    p = Path(spec)
    p.parent.mkdir(parents=True, exist_ok=True)
    return open(p, "a", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive 180-day-old usage events into monthly .jsonl.gz files",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=_resolve_default_retention_days(),
    )
    parser.add_argument(
        "--log",
        default="-",
        help="log destination: '-' (stderr) / 'auto' (~/.claude/.../archive.log) / explicit path",
    )
    args = parser.parse_args(argv)

    paths = _resolve_paths()
    log_target = _open_log(args.log, paths.data_file.parent / "archive.log")
    close_log = log_target is not sys.stderr

    try:
        now = datetime.now(timezone.utc)

        try:
            lock_fd = _lock.open_lock_file(paths.lock_file)
            try:
                try:
                    _lock.acquire_exclusive(lock_fd, blocking=True)
                except OSError as e:
                    print(
                        f"archive_usage.py: failed to acquire exclusive lock: {e}",
                        file=log_target,
                    )
                    return 1
                try:
                    result = run_archive(now, paths, args.retention_days)
                    print(
                        f"archive_usage.py: archived={result.archived_event_count} "
                        f"months={result.archived_months} "
                        f"hot_remainder={result.hot_remainder_count} "
                        f"broken_kept={result.broken_lines_kept}",
                        file=log_target,
                    )
                    return 0
                finally:
                    _lock.release(lock_fd)
            finally:
                os.close(lock_fd)
        except (OSError, ValueError) as e:  # pragma: no cover (defensive top-level catch)
            # OSError は file/lock I/O 系 (ArchiveReadError も含む)、
            # ValueError は parse 系 (json.JSONDecodeError 等)。
            # programming error (TypeError, AttributeError 等) は propagate して可視化。
            print(f"archive_usage.py: unexpected error: {e}", file=log_target)
            return 1
    finally:
        if close_log:
            try:
                log_target.close()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
