# Percentile method — Excel `PERCENTILE.INC` 採用と labeling 規律

`subagent_metrics._percentiles()` が duration list から `(p50, p90, p99)` を返す際の **percentile 計算方式の選定理由**、**stdlib `statistics.quantiles` での実装**、**test fixture pin による回帰検出** をまとめたリファレンス。Issue #60 (subagent quality A5) で導入。

ここで言う「method」とは Hyndman-Fan の 9 種類ある quantile 補間手法のうちどれを採用するか、の話。stdlib / numpy / R / Excel で **デフォルトが揃っていない** ため、明示的に固定して labeling しないと library 切替えで silently 値が変わる。

---

## §1. 採用方針 — Excel `PERCENTILE.INC` 等価

採用は **Python `statistics.quantiles(data, n=100, method="inclusive")`**。
これは Excel `PERCENTILE.INC` と等価 (端点を含めた線形補間) で、数式は

```
i = p * (len(data) - 1)        # 0-indexed の小数 index
result = sorted_data[floor(i)] + (i - floor(i)) * (sorted_data[ceil(i)] - sorted_data[floor(i)])
```

### `numpy` default 等価とは **書かない**

`np.percentile()` のデフォルトは `method="linear"` (exclusive endpoints) で、
端点を含めない別物。同じ入力でも特に **両端 (p1 / p99) で値が変わる**。

過去 Issue #60 のプラン草稿で「numpy default 等価」と書きかけたが
plan-reviewer が指摘して阻止した経緯あり。labeling は次の 3 段で確定する:

1. **関数呼び出し**: `statistics.quantiles(data, n=100, method="inclusive")`
2. **等価先 (verified)**: Excel `PERCENTILE.INC`
3. **人間向け説明**: 線形補間、端点を含む

「R-7 / Type 7」labeling は学術 taxonomy で読者に伝わらない上、論文によって
微妙に違う method を Type 7 と呼ぶことがあるので user-facing には使わない。
help-pop / docstring は (1) → (2) → (3) の順で書く。

> 「numpy 互換」と書くと numpy ユーザがあとで `np.percentile()` に差し替えた時に
> p99 系が silently shift する。「Excel PERCENTILE.INC 等価」は誤読のリスクが小さい。

---

## §2. 実装 — `_percentiles()` helper

`subagent_metrics.py:352` に常駐:

```python
def _percentiles(durations: list[float]) -> tuple[float | None, float | None, float | None]:
    if not durations:
        return (None, None, None)            # 空 → all-None triple
    if len(durations) == 1:
        v = durations[0]
        return (v, v, v)                     # 1 件 → 退化扱い (全 percentile が data[0])
    cuts = statistics.quantiles(durations, n=100, method="inclusive")
    return (cuts[49], cuts[89], cuts[98])    # p50 / p90 / p99
```

### `len < 2` ガードが必須

`statistics.quantiles` は `len(data) < 2` で `StatisticsError` を投げる。
本プロジェクトでは新しい subagent 種で sample が 0 / 1 件のケースが日常的に
発生するため、helper 側で degenerate 経路を吸収する:

| `len(durations)` | 戻り値 |
|---|---|
| 0 | `(None, None, None)` |
| 1 | `(v, v, v)` |
| ≥ 2 | `(cuts[49], cuts[89], cuts[98])` |

dashboard 側 (`dashboard/template/scripts/40_renderers_quality.js`) は
`None` を「サンプル不足 (—)」表示にする責務を負う。

### cuts index は zero-based

`statistics.quantiles(n=100)` は **99 個の cut を返す** (n=100 は 100 個の bucket
を意味し、bucket 境界は 99 個)。p50 は `cuts[49]` (0-indexed)、p90 は `cuts[89]`、
p99 は `cuts[98]`。`cuts[50]` を取ると p51 になる off-by-one trap。

---

## §3. Test fixture pin — method 切替えで loud に壊す

`tests/test_subagent_quality.py::TestPercentileEdgeCases` で次を pin:

| fixture | 期待値 (p50, p90, p99) | 検出する drift |
|---|---|---|
| `[]` | `(None, None, None)` | 空ガード退化 |
| `[42.0]` | `(42.0, 42.0, 42.0)` | `len == 1` 退化 |
| `[1.0, 2.0]` | `(1.5, 1.9, 1.99)` | inclusive 線形補間 (両端含む) |
| `[1.0, 2.0, 3.0, 4.0, 5.0]` | `(3.0, 4.6, 4.96)` | n=5 での補間結果 |

加えて手計算可能な検証用サンプル `[1, 2, 3, 4]` で
`p50=2.5 / p90=3.7 / p99=3.97`。method を `"exclusive"` や numpy default に
切り替えると **p99 が最も loud に変わる** ので、p99 を必ず pin する。

### Drift シナリオ

- `method="inclusive"` → `method="exclusive"` の reflex 変更
- `statistics.quantiles` から numpy への migrate (互換のつもりで silently shift)
- Python の minor upgrade (実害は出ていないが pin することで早期検出可能)

将来 numpy へ移行する場合は明示的に
`np.percentile(data, [50, 90, 99], method="inclusive")` (numpy 1.22+) を使う。
default `method="linear"` を使うと parity が崩れる。

---

## 参照

- 実装: `subagent_metrics.py:352` (`_percentiles`)
- 利用側: `subagent_metrics.py:384` (`_build_metrics` で `p50_duration_ms`/`p90`/`p99` を組み立て)
- spec: `docs/spec/dashboard-api.md` (`subagent_metrics` JSON 契約)
- test: `tests/test_subagent_quality.py::TestPercentileEdgeCases`
- 経緯: Issue #60 (subagent quality A5)
