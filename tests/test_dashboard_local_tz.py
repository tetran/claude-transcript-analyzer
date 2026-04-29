"""tests/test_dashboard_local_tz.py — Issue #65: dashboard 表示の local TZ 化。

ヘッダ「最終更新」と日別 sparkline の bucket を **client-side で local TZ に rebucket**
する設計の構造テスト + behavior テスト。

設計判断:
- server (`dashboard/server.py`) は無改修。`/api/data` の `daily_trend` field は
  backward-compat のため残るが、frontend は `hourly_heatmap.buckets` から
  local TZ で daily を再構築して使う。
- header は `formatLocalTimestamp(data.last_updated)` で `YYYY-MM-DD HH:mm <TZ>`
  形式 (TZ 短縮名は環境依存)。

テストは 2 段構え:
1. literal pin (regex / substring): JS 側に正しい関数定義 / 呼び出し痕跡があるか
2. Node 経由 round-trip: `localDailyFromHourly` の DST / 月またぎ / 年またぎ /
   空 buckets 等の behavior が正しいか (CI に node が無いので skipUnless gate)
"""
# pylint: disable=line-too-long
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from _dashboard_template_loader import load_assembled_template

_TEMPLATE_DIR = Path(__file__).parent.parent / "dashboard" / "template"
_HELPERS_JS = _TEMPLATE_DIR / "scripts" / "10_helpers.js"
_LOAD_RENDER_JS = _TEMPLATE_DIR / "scripts" / "20_load_and_render.js"


def _read_helpers() -> str:
    return _HELPERS_JS.read_text(encoding="utf-8")


def _read_load_render() -> str:
    return _LOAD_RENDER_JS.read_text(encoding="utf-8")


# ============================================================
#  TestHelpersDefined: 10_helpers.js に新ヘルパが定義されている
# ============================================================
class TestHelpersDefined:
    def test_format_local_timestamp_function_defined(self):
        """`formatLocalTimestamp(iso)` が 10_helpers.js に定義されている。

        header「最終更新」の表示用ヘルパ。`Intl.DateTimeFormat` の
        timeZoneName: 'short' で TZ 短縮名を抽出する。
        """
        body = _read_helpers()
        assert re.search(r"\bfunction\s+formatLocalTimestamp\s*\(", body), \
            "function formatLocalTimestamp(iso) が 10_helpers.js に定義されていない"

    def test_format_local_timestamp_uses_intl_date_time_format(self):
        """`Intl.DateTimeFormat` で TZ 短縮名を取り出している痕跡を pin。

        `timeZoneName: 'short'` を使うことで `JST` / `GMT+9` 等の環境依存表記を
        ブラウザに任せる。test では具体文字列を pin しない (= 環境依存仕様)。
        """
        body = _read_helpers()
        assert "Intl.DateTimeFormat" in body, \
            "Intl.DateTimeFormat で TZ 名を抽出する実装が無い"
        assert "timeZoneName" in body, \
            "timeZoneName option による TZ 短縮名抽出が無い"

    def test_local_daily_from_hourly_function_defined(self):
        """`localDailyFromHourly(buckets)` が 10_helpers.js に定義されている。

        `hourly_heatmap.buckets` (UTC hour bucket) を local TZ 日付で集約して
        sparkline 用の `[{date, count}]` を返すヘルパ。
        """
        body = _read_helpers()
        assert re.search(r"\bfunction\s+localDailyFromHourly\s*\(", body), \
            "function localDailyFromHourly(buckets) が 10_helpers.js に定義されていない"

    def test_local_daily_from_hourly_avoids_to_iso_string(self):
        """`localDailyFromHourly` は `toISOString` を使わない。

        `toISOString().slice(0,10)` は UTC 日付を返すため、local 日付集約には
        使えない。`getFullYear` / `getMonth` / `getDate` の組み合わせで手組みする。
        """
        body = _read_helpers()
        # 関数本体だけ抽出
        match = re.search(
            r"function\s+localDailyFromHourly\s*\([^)]*\)\s*\{",
            body,
        )
        assert match is not None, "localDailyFromHourly 本体が見つからない"
        start = match.end()
        # 単純な brace counting で関数本体を取り出す
        depth = 1
        i = start
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        fn_body = body[start:i - 1]
        assert "toISOString" not in fn_body, \
            "localDailyFromHourly が toISOString を使うと UTC 日付が混入する (DST 罠)"


# ============================================================
#  TestHeaderUsesLocalTimestamp: header が UTC 表記を捨てて local TZ で出る
# ============================================================
class TestHeaderUsesLocalTimestamp:
    def test_load_render_no_longer_uses_get_utc_hours(self):
        """`getUTCHours` / `getUTCMinutes` / `getUTCFullYear` / `getUTCMonth`
        / `getUTCDate` のいずれも 20_load_and_render.js から消えていること
        (header timestamp と sparkline densify の両方で使われていた)。"""
        body = _read_load_render()
        for sym in ("getUTCHours", "getUTCMinutes", "getUTCFullYear",
                    "getUTCMonth(", "getUTCDate("):
            assert sym not in body, \
                f"20_load_and_render.js に UTC 系メソッド {sym} が残っている"

    def test_load_render_no_longer_emits_utc_suffix_literal(self):
        """`' UTC'` 文字列リテラル (header timestamp の suffix) が消えていること。"""
        body = _read_load_render()
        assert "' UTC'" not in body and '" UTC"' not in body, \
            "header timestamp の ' UTC' suffix が残っている"

    def test_load_render_calls_format_local_timestamp(self):
        """`formatLocalTimestamp(data.last_updated)` (もしくは同等の呼び出し) が
        20_load_and_render.js にある。"""
        body = _read_load_render()
        assert "formatLocalTimestamp(" in body, \
            "formatLocalTimestamp(...) の呼び出しが無い"


# ============================================================
#  TestSparklineUsesHourlyHeatmap: sparkline が daily_trend を直読みしない
# ============================================================
class TestSparklineUsesHourlyHeatmap:
    def test_load_render_calls_local_daily_from_hourly(self):
        """sparkline 構築前に `localDailyFromHourly(...)` を呼んで trend を組む。"""
        body = _read_load_render()
        assert "localDailyFromHourly(" in body, \
            "localDailyFromHourly(...) の呼び出しが無い"

    def test_load_render_local_daily_from_hourly_takes_hourly_heatmap(self):
        """`localDailyFromHourly` の引数が `hourly_heatmap` を経由していること。"""
        body = _read_load_render()
        # 引数に hourly_heatmap が含まれる呼び出しを期待
        assert re.search(
            r"localDailyFromHourly\([^)]*hourly_heatmap",
            body,
        ), "localDailyFromHourly の引数が hourly_heatmap 由来でない"

    def test_load_render_does_not_iterate_daily_trend_directly(self):
        """sparkline 描画ロジックが `data.daily_trend` を直接 sort して trend に
        使っていないこと。Issue #65 後は hourly_heatmap 由来の derived array を
        使う設計。"""
        body = _read_load_render()
        # 旧: `(data.daily_trend||[]).slice().sort(...)` パターンが消えていること
        assert not re.search(
            r"data\.daily_trend\s*\|\|\s*\[\]\)\s*\.slice\(\)\s*\.sort",
            body,
        ), "sparkline が data.daily_trend を直 sort して使う旧実装のまま"


# ============================================================
#  TestServerSentinelDocstring: aggregate_daily に sentinel コメント
# ============================================================
class TestServerSentinelDocstring:
    def test_aggregate_daily_has_local_tz_sentinel_in_docstring(self):
        """`aggregate_daily` の docstring が dashboard frontend は使わない旨を
        明記している。将来 field 削除を検討するときの sentinel。"""
        server_py = (Path(__file__).parent.parent / "dashboard" / "server.py").read_text(encoding="utf-8")
        # aggregate_daily 関数定義から最初の triple-quoted block を抜き出す
        match = re.search(r"def\s+aggregate_daily\s*\([^)]*\)[^:]*:\s*\n\s*\"\"\"(.*?)\"\"\"",
                          server_py, re.DOTALL)
        assert match is not None, \
            "aggregate_daily に docstring が無い (sentinel 配置不可)"
        doc = match.group(1)
        # Issue #65 への参照と「frontend は使わない」旨のキーワード
        assert "Issue #65" in doc, \
            "aggregate_daily docstring に Issue #65 sentinel が無い"
        assert ("hourly_heatmap" in doc) or ("local TZ" in doc) or ("local-TZ" in doc), \
            "aggregate_daily docstring に hourly_heatmap rebucket / local TZ 言及が無い"


# ============================================================
#  TestAssembledTemplateContainsLocalTzCode: concat 後の HTML にも反映される
# ============================================================
class TestAssembledTemplateContainsLocalTzCode:
    def test_assembled_template_contains_format_local_timestamp(self):
        template = load_assembled_template()
        assert "formatLocalTimestamp" in template, \
            "concat 後の _HTML_TEMPLATE に formatLocalTimestamp が含まれていない"

    def test_assembled_template_contains_local_daily_from_hourly(self):
        template = load_assembled_template()
        assert "localDailyFromHourly" in template, \
            "concat 後の _HTML_TEMPLATE に localDailyFromHourly が含まれていない"


# ============================================================
#  TestLocalDailyFromHourlyNode: Node 経由の behavior round-trip
#  CI に node が無いので skipUnless gate (手元 / actions/setup-node 入れた CI で走る)
# ============================================================
_NODE = shutil.which("node")


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestLocalDailyFromHourlyNode(unittest.TestCase):
    """`localDailyFromHourly` を Node で実行して各種 fixture を behavior 検証する。

    process.env.TZ を起動時に渡すことで host TZ に依存しない (= CI でも JST 仮想実行
    できる)。本テストは手元 (macOS, node v24+) と actions/setup-node 配備された
    runner でのみ走る。CI default の Ubuntu runner には node が入っているが、
    setup-node 無しでバージョン不定なので gating は `which('node')` で十分。
    """

    @staticmethod
    def _run_node(tz: str, buckets: list[dict]) -> list[dict]:
        """Node で localDailyFromHourly(buckets) を実行し JSON 結果を返す。

        helpers.js の関数定義を読み込み、その後 fixture を JSON で埋め込んで
        eval する。`pad` 関数も helpers.js から拾う必要がある (依存)。
        """
        helpers_src = _HELPERS_JS.read_text(encoding="utf-8")
        # helpers.js は IIFE 内側で動く前提なので、Node で直接 eval すると
        # `function` 宣言がトップレベルになり問題なく動く。
        # ただし helpers.js の 1 行目は `function esc(...)` から始まるトップレベル
        # 関数宣言群なので、Node で素直に評価できる。
        script = (
            helpers_src
            + "\nconst __input = " + json.dumps(buckets) + ";\n"
            + "const __out = localDailyFromHourly(__input);\n"
            + "process.stdout.write(JSON.stringify(__out));\n"
        )
        proc = subprocess.run(
            [_NODE, "-e", script],
            env={"TZ": tz, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"node failed (returncode={proc.returncode}): stderr={proc.stderr}"
            )
        return json.loads(proc.stdout)

    def test_empty_buckets_returns_empty_list(self):
        out = self._run_node("Asia/Tokyo", [])
        self.assertEqual(out, [])

    def test_jst_23_utc_hour_falls_into_next_day(self):
        """UTC 23:00 = JST 翌日 08:00 → JST 日付では翌日 bucket。"""
        out = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-04-28T23:00:00+00:00", "count": 5}],
        )
        self.assertEqual(out, [{"date": "2026-04-29", "count": 5}])

    def test_jst_00_utc_hour_falls_into_same_day_morning(self):
        """UTC 00:00 = JST 当日 09:00 → 同 JST 日付 bucket。"""
        out = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-04-29T00:00:00+00:00", "count": 3}],
        )
        self.assertEqual(out, [{"date": "2026-04-29", "count": 3}])

    def test_z_suffix_and_offset_suffix_equivalent(self):
        """`Z` suffix と `+00:00` suffix が同じ結果を返す。"""
        out_z = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-04-29T00:00:00Z", "count": 1}],
        )
        out_offset = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-04-29T00:00:00+00:00", "count": 1}],
        )
        self.assertEqual(out_z, out_offset)

    def test_count_preserved_across_dst_start_day(self):
        """DST 開始日 (US/Eastern: 2026-03-08 02:00→03:00 で 23h 日) でも
        count が保存される (= 全 hour bucket の count 合計が一致)。"""
        # 2026-03-08 全日の UTC hour buckets (24 個) を 1 件ずつ
        buckets = [
            {"hour_utc": f"2026-03-08T{h:02d}:00:00+00:00", "count": 1}
            for h in range(24)
        ]
        out = self._run_node("America/New_York", buckets)
        total = sum(item["count"] for item in out)
        self.assertEqual(total, 24, f"DST 開始日で count が失われた: {out}")

    def test_count_preserved_across_dst_end_day(self):
        """DST 終了日 (US/Eastern: 2025-11-02 02:00→01:00 で 25h 日) でも count 保存。"""
        buckets = [
            {"hour_utc": f"2025-11-02T{h:02d}:00:00+00:00", "count": 1}
            for h in range(24)
        ]
        out = self._run_node("America/New_York", buckets)
        total = sum(item["count"] for item in out)
        self.assertEqual(total, 24, f"DST 終了日で count が失われた: {out}")

    def test_month_boundary_wrap(self):
        """UTC 1/31 23:00 → JST 2/1 08:00 に正しく wrap。"""
        out = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-01-31T23:00:00+00:00", "count": 7}],
        )
        self.assertEqual(out, [{"date": "2026-02-01", "count": 7}])

    def test_year_boundary_wrap(self):
        """UTC 12/31 23:00 → JST 翌年 1/1 08:00 に正しく wrap。"""
        out = self._run_node(
            "Asia/Tokyo",
            [{"hour_utc": "2026-12-31T23:00:00+00:00", "count": 2}],
        )
        self.assertEqual(out, [{"date": "2027-01-01", "count": 2}])

    def test_aggregation_across_multiple_hours_same_local_day(self):
        """同じ local 日付に複数 hour bucket がある場合は count を加算する。"""
        out = self._run_node(
            "Asia/Tokyo",
            [
                {"hour_utc": "2026-04-29T00:00:00+00:00", "count": 1},  # JST 4/29 09:00
                {"hour_utc": "2026-04-29T05:00:00+00:00", "count": 2},  # JST 4/29 14:00
                {"hour_utc": "2026-04-28T23:00:00+00:00", "count": 4},  # JST 4/29 08:00
            ],
        )
        self.assertEqual(out, [{"date": "2026-04-29", "count": 7}])

    def test_output_sorted_ascending_by_date(self):
        """出力は date 昇順ソート済 (sparkline 描画前提)。"""
        out = self._run_node(
            "Asia/Tokyo",
            [
                {"hour_utc": "2026-04-30T00:00:00+00:00", "count": 1},
                {"hour_utc": "2026-04-28T05:00:00+00:00", "count": 2},
                {"hour_utc": "2026-04-29T05:00:00+00:00", "count": 3},
            ],
        )
        dates = [item["date"] for item in out]
        self.assertEqual(dates, sorted(dates), f"date 昇順でない: {out}")
