# Issue #62 Plan — Skill surface insights (A4 expansion/submit 比率 + B4 instructions_loaded 分布)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 | A4 集計の signal 死問題への構造対応。実機 `<missing>: 202 / expansion: 75 / submit: 0` で旧 schema を expansion 扱いにすると全 skill が rate ≈ 1.0 で peach 強調が出ない初期状態になり viz の主目的が無効化される。**schema に `legacy_count` 列を追加** + `expansion_rate` の分母から legacy を除外 (= modern data だけで rate 計算)、modern が 0 の skill は `expansion_rate=null` を返し renderer 側で「観測待ち」表示。test 4 件追加 (`test_legacy_count_separate_field` / `test_expansion_rate_excludes_legacy_from_denominator` / `test_expansion_rate_null_when_no_modern_data` / `test_renderer_displays_observation_pending_for_null_rate`) | A4 集計仕様 / Schema / 関数 signature / TDD 計画 / renderer JS / Risk 表 |
| P2 | B4 schema の sort/cap 一貫性確保。Issue 本文 literal の dict 形 (`memory_type_dist: {...}`) を保つが、**aggregator は count desc → key asc の insertion order で dict を組み立てる** ことを契約化 (Python 3.7+ dict / JSON 仕様で順序保持される)。test 2 件追加 (`test_memory_type_dist_iteration_order_is_count_desc_then_key_asc` / `test_load_reason_dist_iteration_order_is_count_desc_then_key_asc`) で pin。consumer (renderer / static export) は server-side sort 済みを信頼 | B4 集計仕様 / Schema / 関数 docstring / TDD 計画 |
| P3 | Risk 表で言及していた「raw event 不変」test を test 計画に追加。`test_aggregator_does_not_mutate_input_events` で in-place rewrite を構造的に防ぐ | TDD 計画 (`TestInstructionsLoadedBreakdown`) |
| P4 | JS renderer の defensive default typo (`: {}` → `: []`) を修正。`glob_match_top` が non-array のときは `[]` がデフォルト | renderer JS サンプル |
| Q1 | glob_match scope test 1 件追加 (`test_glob_match_top_counts_only_within_glob_match_scope`)。同じ file_path が `load_reason="glob_match"` と `="session_start"` 両方に出現したとき、glob_match 由来のみ count されることを pin。実装の逆方向 (= 全 file_path をまず count してから glob_match で filter) を防ぐ | TDD 計画 (`TestInstructionsLoadedBreakdown`) |
| Q2 | `expansion_rate` の precision 規定を spec doc / aggregator 側で固定。`round(rate, 4)` で API 表現を 4 桁小数に揃える (consumer の renderer は %, textual report は 4 桁少数で同じ raw 値を読める) | A4 関数 signature / TDD 計画 (`test_expansion_rate_rounded_to_4_decimals`) / spec doc 6.1 |
| Q3 | 既存 `tests/test_dashboard_router.py` の Surface placeholder 期待を **Phase 0** に切り出し。Phase 1 (新 test RED) と既存 test の予期せぬ break を分離して TDD の "one red at a time" 規律を保つ | Phases (Phase 0 新設) |
| (Question 反映) | Empty state 文言の統一: `"観測なし"` に統一 (3 種混在 → 1 種)。3 col grid 固定は v1 維持 (load_reason 1 値しかない実機状況では `2:1` 化の根拠が弱い、将来観測増えてから検討で申し送り) | renderer JS サンプル / 申し送り |

### 二次レビュー反映 (2nd round)

| 二次 Proposal | 内容 | 反映箇所 |
|---|---|---|
| 2-P1 | `json.dumps(..., sort_keys=True)` 混入による P2 dict 契約破壊を構造的に防ぐ。実装注意 + regression test を追加。具体的には Phase 3 末尾に「`build_dashboard_data` の serialize 経路で `sort_keys=True` が混入していないこと」を確認、+ `tests/test_skill_surface.py` に `test_dict_iteration_order_survives_json_roundtrip` を 1 件追加 (json.dumps → json.loads して memory_type_dist のキー順が aggregator 出力と一致) | Phase 3 / TDD 計画 (`TestInstructionsLoadedBreakdown`) |
| 2-P2 | `expansion_rate=None` の serialize boundary 確認。Python `None` → JSON `null` → JS `null` の round-trip test を追加 (`test_expansion_rate_null_serializes_to_json_null`)。`float("nan")` 等の誤返却を防ぐ structural guard | TDD 計画 (`TestBuildDashboardDataIncludesSurfaceFields`) |
| 2-P3 | Phase 0 の操作対象 test を **関数名レベル** で pin。`tests/test_dashboard_router.py:67-76` の `test_non_overview_pages_are_placeholders` を **削除** が最小修正 (page-placeholder 期待を持つ test はこの 1 関数のみ。loop 自体が `for page in ['surface']:` の 1 要素 list なので部分修正で残す価値も無し)。`test_template_has_four_page_sections` 等が surface section 存在を別 assert で守るため退化なし | Phase 0 |
| 2-P4 | sort 分母に legacy を入れる方針の **UX 副作用** を Risk 表に明示。retention 経過 (180 日) で legacy が自然消滅した skill が下位に動き、表示順が時間経過で変動する。trade-off (= 上位順位安定 vs 順位ドリフト) を明示し、`memory/skill_surface.md` の "fine-tune 観測指標" 節に「legacy 比率 50% 超の skill は順位下落の予告」を申し送り | Risk 表 / memory file 内容 |
| 2-Q1 | memory file の "ECMAScript 2020+" citation を softening。CLAUDE.md の "Number-shaped technical identifiers" rule に従い version 番号は確定的に断言しない。代替表現: 「ECMAScript 仕様で string key の挿入順保持が規定されている」 | memory file 内容 |

### 三次レビュー反映 (3rd round)

| 三次 Proposal | 内容 | 反映箇所 |
|---|---|---|
| 3-P1 | **Issue 本文 AC との意図的乖離を明文化**。Issue #62 本文テスト要件に「source 欠落時は expansion 扱いで既存ロジックと整合」と明記されているが、P1 反映で実機 signal 死を構造解消するため legacy 列分離方針に上書きしている。レビューを経た意図的判断であることを反映ログ / Risk 表 / 申し送り / DoD で明示 (PR description でも方針差分を明示してから merge) | 反映ログ P1 / Risk 表 / 申し送り / DoD コメント方針 |
| 3-P2 | `top_n` parameter が dict 集計 (memory_type_dist / load_reason_dist) には effect しない (= glob_match_top のみ) 設計意図を docstring + spec doc に 1 行明示。memory_type / load_reason はキー数が bounded (= hooks 仕様で固定値域) のため cap 不要 | aggregator docstring / spec doc 6.1 |
| 3-P3 | 申し送りに「hardcoded Japanese UI 文言 (`観測なし` / `観測待ち`) が renderer / tooltip / aria-label に分散している → 後続で `_UI_LABELS = {...}` 定数 or i18n layer 導入の選択肢」を 1 行追加。#60/#61 の hardcoded 慣習踏襲なので本 PR scope ではないが、最終レビューで明示しておくと累積文言が i18n 化 trigger まで埋もれない | 申し送り |
| 3-Q1 | commit message convention を Phase 列に 1 行明示。Phase 0 prep / Phase 1 RED / Phase 2-4 GREEN それぞれ独立 commit、TDD "one red at a time" 規律と git history 粒度を揃える | Phases 冒頭 |

## 🎯 Goal

Surface ページ (#57 shell — 現状 placeholder) に **skill が「呼ばれ方 / 載せられ方」の
角度から見える** 2 viz を追加し、skill 定義側の改善フィードバックループを作る。

- **A4. Slash command expansion/submit 比率**: `user_slash_command.source` を skill
  ごとに集計。`expansion_rate = expansion_count / (expansion_count + submit_count)`
  が低い skill = LLM 視点で名前が想起されにくい (= description / glob 設計が弱い)
- **B4. InstructionsLoaded 分布**: `instructions_loaded` event の `memory_type` /
  `load_reason` / `file_path` を集計。proactive に load されすぎる skill / CLAUDE.md
  を浮かび上がらせる (= `skill-creator` / `skill-slimmer` での整理対象)

両 viz は同じ Surface ページ (`<section data-page="surface">`) に追加。
panel 配置順は (1) A4 source breakdown table、(2) B4 instructions_loaded
(memory_type bar + load_reason bar + glob_match top 10) — 「skill 単位 →
session/load 単位」と焦点をズームアウト。

## 📐 機能要件 / 構造設計

### A4. Slash command expansion/submit 比率

#### 集計仕様 — source 分類 (P1 反映: legacy 分離)

```
SOURCE_EXPANSION = "expansion"
SOURCE_SUBMIT    = "submit"

for ev in events where event_type == "user_slash_command":
  skill = ev.skill
  if not skill: skip                # 空 skill 名は除外 (既存 aggregate_skills と整合)
  src = ev.get("source")
  if src == SOURCE_EXPANSION:   expansion_count[skill] += 1
  elif src == SOURCE_SUBMIT:    submit_count[skill]    += 1
  else:                         legacy_count[skill]    += 1   # 旧 schema (source 欠落) / unknown 値
```

**P1 反映 — legacy 分離の根拠**: 実機観測 (`<missing>: 202 / expansion: 75 / submit: 0`)
で旧 schema を expansion 扱いに混ぜると `expansion_rate ≈ 1.0` 偏重で peach 強調
(= 改善余地 signal) が一切出ず、本 viz の主目的「LLM が想起できない skill」を
浮かび上がらせるのが構造的に効かない。

→ **legacy を 3 列目として分離** + `expansion_rate` の分母から legacy を除外 →
modern data だけで rate 計算する設計に変更。retention 経過 (180 日) で旧 schema が
自然消滅した後に signal が出始める段差をなだらかにする効果もある。

**`record_skill.py` 慣習との整合**: dedup ロジック (`source != "submit"` を
expansion 由来とみなす) は **重複落とさない安全側** の判断であり、本 viz の
**signal を出す方向の判断** とは要件が違う。整合は dedup 側で取れていれば十分で、
集計側は別判断 (= legacy 分離) を採用する。

**source 不明値 ("expansion"/"submit" 以外、e.g. 将来追加されうる新値)**: 本 PR では
**legacy 扱い** に倒す (= 確実な expansion / submit のみカウント)。新 source 値が
出てきたら spec doc 側で別 field を追加して expand。

#### 集計仕様 — rate 算出 (P1 反映: legacy を分母から除外)

```
modern_total = expansion_count[skill] + submit_count[skill]
if modern_total > 0:
  expansion_rate[skill] = round(expansion_count[skill] / modern_total, 4)
else:
  expansion_rate[skill] = None   # observation pending (modern data 0 件)
```

- 出力対象は `expansion_count + submit_count + legacy_count > 0` の skill
  (= 何かしら observed されたもの)
- `expansion_rate = None` のとき renderer 側は「観測待ち」(modern data なし) を
  表示し、peach 強調 (rate < 0.5) からは除外する
- **Q2 反映 — precision**: `round(rate, 4)` で 4 桁小数に固定。`0.6666666...`
  のような割り切れない値の精度を抑え、JSON サイズを節約。consumer (renderer は
  `Math.round(rate * 100)`、textual report は 4 桁 raw) いずれも同じ raw 値で扱える
- **clamp しない**: 0 ≤ rate ≤ 1 は構造的に保証される (両 count 非負・分母 > 0)

#### Schema (P1 反映: legacy_count + nullable rate)

```json
{
  "slash_command_source_breakdown": [
    {"skill": "/codex-review",         "expansion_count": 12, "submit_count": 3, "legacy_count": 0, "expansion_rate": 0.8},
    {"skill": "/usage-summary",        "expansion_count": 0,  "submit_count": 5, "legacy_count": 0, "expansion_rate": 0.0},
    {"skill": "/usage-export-html",    "expansion_count": 8,  "submit_count": 0, "legacy_count": 0, "expansion_rate": 1.0},
    {"skill": "/legacy-only",          "expansion_count": 0,  "submit_count": 0, "legacy_count": 23, "expansion_rate": null}
  ]
}
```

- **sort**: `expansion_count + submit_count + legacy_count` 降順 → 同点で `skill`
  昇順 (安定 sort)。**legacy も sort 分母に入れる** = 「とにかく多く呼ばれた」順で
  上位を埋める (legacy が消滅した後も同じ順序を保てる安定性)
- **`expansion_rate` 値**: 4 桁小数 (`round(rate, 4)`) または `null` (modern 0 件)
- **top N**: 20 (Issue 本文「count 降順 top 20」)
- **empty events**: `[]`

#### 関数 signature (P1 + Q2 反映)

```python
TOP_N_SLASH_COMMAND_BREAKDOWN = 20

def aggregate_slash_command_source_breakdown(
    events: list[dict],
    top_n: int = TOP_N_SLASH_COMMAND_BREAKDOWN,
) -> list[dict]:
    """user_slash_command event を skill ごとに source 分類して expansion_rate を返す。

    P1 反映: source 値を {"expansion", "submit", legacy (= それ以外)} の 3 分類で
    集計。expansion_rate の分母は modern (= expansion + submit) のみ、legacy は
    観測値として記録するが rate 計算からは除外する。modern == 0 の skill は
    expansion_rate=None を返す (= 観測待ち)。Q2 反映: rate は 4 桁小数で丸める。

    sort: (expansion + submit + legacy) 降順 → skill 昇順、top_n 件で cap。
    """
```

### B4. InstructionsLoaded 分布

#### 集計仕様

3 つの集計を 1 payload にまとめる:

1. **`memory_type_dist`**: `memory_type` フィールド値の頻度分布 (dict)
2. **`load_reason_dist`**: `load_reason` フィールド値の頻度分布 (dict)
3. **`glob_match_top`**: `load_reason == "glob_match"` の event を `file_path` で
   groupby → count 降順 top 10 (list)

```
for ev in events where event_type == "instructions_loaded":
  mt = ev.get("memory_type", "")
  lr = ev.get("load_reason", "")
  fp = ev.get("file_path", "")
  if mt: memory_type_dist[mt] += 1
  if lr: load_reason_dist[lr] += 1
  if lr == "glob_match" and fp:
    glob_match_top_counter[fp] += 1
```

**memory_type / load_reason の値ゆれ**: 実機 `usage.jsonl` 観測では
`memory_type` が **TitleCase** ("Project" / "User") で記録されている。Issue 本文の
「user / project / skill / 等」例は概念名であり、実値は hooks が capture する
そのまま (= verbatim) を採用。集計側で lower-case 正規化はしない (実機データの
真実を歪めない)。UI 側もそのまま表示。

**空文字 / 不在の扱い**: `memory_type=""` / `load_reason=""` のレコードは
分布に含めない (skip)。集計値 0 は「観測されていないだけ」と「empty value」を
同一視できないため、空値は最初から除外する (既存 aggregate_skills と整合)。

#### file_path home 圧縮

`/Users/<user>/.claude/...` → `~/.claude/...`、`/Users/<user>/...` → `~/...` を
**集計関数内** (= ハンドラ側) で圧縮する。

- 対象: `glob_match_top` の各 `file_path` のみ。`memory_type_dist` /
  `load_reason_dist` は path を持たないので非対象
- 実装方針: `os.path.expanduser("~")` で `$HOME` を取得 → prefix 一致なら
  `"~"` + 残りに置換。同 prefix が一致しないパスは無加工のまま返す
- **集計側にする根拠**: (1) export_html (静的 HTML) も同じ表示になる、
  (2) 単一箇所のメンテで済む、(3) raw path と圧縮後 path で count が分かれる事故を
  避ける (= 集計後のキーが圧縮済みなのでキーが分かれない)

```python
def _compress_home_path(path: str) -> str:
    """`/Users/<user>/...` を `~/...` に圧縮する。一致しなければ無加工 path を返す。

    注意: `os.path.expanduser("~")` は実行環境の HOME を返す (= server を
    実行している user)。dashboard server は user ローカルで走る前提なので
    実機運用では問題ない。テストでは monkey patch せずに済むよう、引数で
    home を渡せる overload を切るのは避け、純粋に env-derived の helper にする
    (テストは _expanduser を差し替えずに固定 path で書く / Phase 1 RED 参照)。
    """
    home = os.path.expanduser("~")
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
```

#### Schema (P2 反映: dict iteration order pin)

```json
{
  "instructions_loaded_breakdown": {
    "memory_type_dist": {"User": 65, "Project": 62},
    "load_reason_dist": {"session_start": 127},
    "glob_match_top": [
      {"file_path": "~/.claude/skills/skill-creator/SKILL.md", "count": 42},
      {"file_path": "~/.claude/skills/codex-review/SKILL.md",  "count": 18}
    ]
  }
}
```

- `memory_type_dist` / `load_reason_dist` は **dict**: 観測されたキーのみ。
  empty events なら `{}`
- **P2 反映 — sort 契約**: dict は **count 降順 → key 昇順** で挿入する
  (`memory_type_dist` 例: User=65, Project=62 → 順序保持で User 先頭)。
  Python 3.7+ / JSON 仕様で insertion order が保持されるため、static export と
  live dashboard で表示順がブレない (= renderer 側 sort に依存しない)
- Issue 本文 literal の dict 形を維持したのは glob_match_top (= 別概念) を list で
  返すのと組み合わせて「dict = 全観測キーの bar、list = top N の table」という
  schema 形と意図のマッピングを保つため
- `glob_match_top` は **list**: count 降順 → file_path 昇順、最大 10 件。
  observed 0 件なら `[]`
- empty events なら全体は `{"memory_type_dist": {}, "load_reason_dist": {}, "glob_match_top": []}`

#### 関数 signature (P2 + Q1 反映)

```python
TOP_N_GLOB_MATCH = 10

def aggregate_instructions_loaded_breakdown(
    events: list[dict],
    top_n: int = TOP_N_GLOB_MATCH,
) -> dict:
    """instructions_loaded event を memory_type / load_reason / glob_match_top に集計。

    memory_type / load_reason の値はそのまま (lower-case 正規化しない)。
    glob_match_top は file_path home 圧縮済み。

    P2 反映: dict (memory_type_dist / load_reason_dist) は count 降順 →
    key 昇順 の insertion order で組み立てる。Python 3.7+ dict / JSON 仕様で
    順序保持されるため、consumer (renderer / static export) はこの順を信頼可能。

    Q1 反映: glob_match_top は load_reason="glob_match" の event だけを対象に
    file_path で count する。同じ file_path が他の load_reason で出現しても
    glob_match スコープ内の count しか積まない。

    3-P2 反映: top_n は glob_match_top にのみ適用。memory_type_dist /
    load_reason_dist は全観測キーを返す (キー数が hooks 仕様で bounded =
    `{"User", "Project", "Skill", ...}` の固定値域に収まるため cap 不要)。
    """
```

### Surface ページの DOM 追加 (2 panel)

`<section data-page="surface" class="page page-placeholder">` の **placeholder
を取り除く** (= `page-placeholder` class 削除 + 中身を panel 2 つに置換)。
Issue #57 の placeholder 文言 (`Coming soon`) は除去。

#60 で Quality page を、#61 で Quality 摩擦 panel を確立した HTML pattern を
踏襲する (`.panel` / `.panel-head` / `.help-host` / `.panel-body`)。

```html
<section data-page="surface" class="page" aria-labelledby="page-surface-title" hidden>
  <header class="header">
    <div>
      <h1 id="page-surface-title"><span class="accent">Surface</span></h1>
      <p class="lede">スキルの「呼ばれ方」と「載せられ方」を可視化します。</p>
    </div>
  </header>

  <!-- (1) A4: Slash command expansion/submit breakdown -->
  <div class="panel" id="surface-source-panel">
    <div class="panel-head c-mint">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>Slash command 起動経路 (top 20)</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-source" data-help-id="hp-source">?</button>
          <span class="help-pop" id="hp-source" role="tooltip" data-place="right">
            <span class="pop-ttl">expansion / submit 比率</span>
            <span class="pop-body"><code>user_slash_command</code> イベントの <code>source</code> を skill ごとに集計。<strong>expansion</strong> = LLM が slash command を理解して展開した経路、<strong>submit</strong> = 展開できず raw prompt として送信された経路 (旧 schema は expansion 扱い)。<code>expansion_rate</code> が低い skill は description / glob 設計が弱く LLM が「思いつかない」可能性。0.5 未満の rate を peach 色で強調。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="surface-source-sub"></span>
    </div>
    <div class="panel-body">
      <table class="source-table" id="surface-source">
        <thead>
          <tr>
            <th>Skill</th>
            <th class="num">Expansion</th>
            <th class="num">Submit</th>
            <th class="num">Legacy</th>
            <th class="num">Rate</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- (2) B4: InstructionsLoaded distribution -->
  <div class="panel" id="surface-instr-panel">
    <div class="panel-head c-peri">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>Instructions ロード分布</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-instr" data-help-id="hp-instr">?</button>
          <span class="help-pop" id="hp-instr" role="tooltip" data-place="right">
            <span class="pop-ttl">Instructions ロード分布</span>
            <span class="pop-body"><code>instructions_loaded</code> hook の <code>memory_type</code> / <code>load_reason</code> 頻度分布と、<code>load_reason="glob_match"</code> が多発した <code>file_path</code> top 10。glob_match top に頻出する skill / CLAUDE.md は description が広すぎて proactive にロードされすぎる候補 = <code>skill-slimmer</code> での整理対象。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="surface-instr-sub"></span>
    </div>
    <div class="panel-body">
      <div class="instr-grid">
        <div class="instr-col">
          <h3 class="instr-h">memory_type</h3>
          <div class="instr-bars" id="surface-instr-mt"></div>
        </div>
        <div class="instr-col">
          <h3 class="instr-h">load_reason</h3>
          <div class="instr-bars" id="surface-instr-lr"></div>
        </div>
        <div class="instr-col instr-col-wide">
          <h3 class="instr-h">glob_match top 10</h3>
          <table class="glob-table" id="surface-instr-glob">
            <thead>
              <tr>
                <th>File path</th>
                <th class="num">Count</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</section>
```

### A4 描画方式 — table (P1 反映: 4 列 + null rate 対応)

`source-table` は `perm-table` (#61) と同型のテーブル。

- 列: Skill / Expansion / Submit / Legacy / Rate (%)
- `expansion_rate` が null (= modern data 0 件) のセルは「`—` (観測待ち)」表示。
  rate-warn 強調からも除外
- rate < 0.5 (= submit 比率高 / null は除外) は `<td class="num rate-warn">` で
  peach 色強調 (#61 と同じ閾値・classname を流用)
- hover/focus で `data-tip="source"` tooltip: skill / expansion / submit /
  legacy / rate% (null のとき rate 行は「観測待ち」表示)

### B4 描画方式 — 横並びの 3 カラム

`.instr-grid` は CSS Grid で 3 カラム (memory_type bar / load_reason bar /
glob_match table)。狭い viewport では 1 カラムに stacking。

- **memory_type / load_reason 分布**: シンプルな水平 bar list (CSS で実装、SVG 不要)
  - キーごとに 1 行: `<label> <bar> <count>` の構造
  - bar 幅は `width: var(--ratio)%` で max を 100% に正規化 (max bar = 観測 max count)
  - キー sort: count 降順 → key 昇順
- **glob_match top**: テーブル (file_path / count)
  - file_path は home 圧縮済み path をそのまま。長い path は CSS `text-overflow: ellipsis`
    + `title=""` で hover 時に full path 表示
  - 0 件のときは「glob_match なし」のメッセージ行

### CSS 設計 (template.html `<style>`)

`/* compact density (Issue #61 / A3) */` ブロック直後に追加。

```css
/* slash command source breakdown (Issue #62 / A4) */
.source-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
  font-family: var(--ff-mono);
}
.source-table th {
  text-align: left; color: var(--ink-faint); font-weight: 500;
  padding: 6px 8px; border-bottom: 1px solid var(--line);
}
.source-table th.num, .source-table td.num {
  text-align: right; font-variant-numeric: tabular-nums;
}
.source-table tbody tr { border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04)); }
.source-table tbody tr:hover { background: var(--bg-panel-2); }
.source-table td { padding: 5px 8px; color: var(--ink); }
.source-table td.name { color: var(--mint); }
.source-table td.dim { color: var(--ink-faint); }
.source-table td.rate-warn { color: var(--peach); font-weight: 500; }
.source-table .empty { text-align: center; color: var(--ink-faint); padding: 24px 0; }
.data-tip[data-kind="source"] { border-left-color: var(--mint); }

/* instructions_loaded breakdown (Issue #62 / B4) */
.instr-grid {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr) minmax(280px, 2fr);
  gap: 24px; align-items: start;
}
@media (max-width: 720px) {
  .instr-grid { grid-template-columns: 1fr; }
}
.instr-col-wide { min-width: 0; } /* table の overflow 回避 */
.instr-h {
  margin: 0 0 8px; font-size: 11px; font-weight: 500;
  color: var(--ink-faint); font-family: var(--ff-mono); text-transform: uppercase;
  letter-spacing: 0.04em;
}
.instr-bars { display: flex; flex-direction: column; gap: 4px; }
.instr-row {
  display: grid; grid-template-columns: minmax(60px, max-content) 1fr 36px;
  align-items: center; gap: 8px; font-size: 12px; font-family: var(--ff-mono);
}
.instr-row .lbl { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.instr-row .bar-wrap { background: var(--bg-panel-2); height: 8px; border-radius: 2px; overflow: hidden; }
.instr-row .bar { height: 100%; background: var(--peri); border-radius: 2px; }
.instr-row .v { text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
.instr-bars .empty { color: var(--ink-faint); font-size: 12px; padding: 8px 0; }

.glob-table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--ff-mono); table-layout: fixed; }
.glob-table th {
  text-align: left; color: var(--ink-faint); font-weight: 500;
  padding: 6px 8px; border-bottom: 1px solid var(--line);
}
.glob-table th.num, .glob-table td.num {
  text-align: right; font-variant-numeric: tabular-nums; width: 56px;
}
.glob-table tbody tr { border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04)); }
.glob-table tbody tr:hover { background: var(--bg-panel-2); }
.glob-table td { padding: 5px 8px; color: var(--ink); }
.glob-table td.fp {
  color: var(--peri); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.glob-table .empty { text-align: center; color: var(--ink-faint); padding: 16px 0; }
.data-tip[data-kind="instr-bar"] { border-left-color: var(--peri); }
.data-tip[data-kind="glob"] { border-left-color: var(--peri); }
```

### JS renderers

`renderCompactDensity` の **直後** に並べる。`loadAndRender()` 末尾に
2 行 call:

```javascript
// ---- A4 slash command source breakdown (Issue #62) ----
renderSlashCommandSourceBreakdown(data.slash_command_source_breakdown);
// ---- B4 instructions_loaded breakdown (Issue #62) ----
renderInstructionsLoadedBreakdown(data.instructions_loaded_breakdown);
```

両 renderer すべて **page-scoped early-out** (`activePage !== 'surface'` で no-op)。
hashchange listener (`addEventListener('hashchange', () => loadAndRender())`) は
#58 で既に main IIFE に入っているので追加実装不要。

```javascript
function renderSlashCommandSourceBreakdown(items) {
  if (document.body.dataset.activePage !== 'surface') return;
  const tbody = document.querySelector('#surface-source tbody');
  const sub = document.getElementById('surface-source-sub');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">観測なし</td></tr>';
  } else {
    tbody.innerHTML = list.map(it => {
      const e = it.expansion_count || 0;
      const s = it.submit_count || 0;
      const lg = it.legacy_count || 0;
      // P1 反映: rate が null (= modern data 0 件) のとき "観測待ち" 表示し
      // peach 強調から除外する。
      const rate = (it.expansion_rate === null || it.expansion_rate === undefined)
        ? null : Number(it.expansion_rate);
      const rateClass = (rate !== null && rate < 0.5) ? 'num rate-warn' : 'num';
      const rateText = rate === null ? '<span class="dim">— 観測待ち</span>'
                                     : (Math.round(rate * 100) + '%');
      const al = it.skill + ': expansion ' + e + ' / submit ' + s + ' / legacy ' + lg +
        (rate === null ? ' (observation pending)' : ' (' + Math.round(rate * 100) + '%)');
      return '<tr data-tip="source" data-name="' + esc(it.skill) +
        '" data-e="' + e + '" data-s="' + s + '" data-lg="' + lg +
        '" data-rate="' + (rate === null ? '' : rate) +
        '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
        '<td class="name">' + esc(it.skill) + '</td>' +
        '<td class="num">' + fmtN(e) + '</td>' +
        '<td class="num dim">' + fmtN(s) + '</td>' +
        '<td class="num dim">' + fmtN(lg) + '</td>' +
        '<td class="' + rateClass + '">' + rateText + '</td>' +
        '</tr>';
    }).join('');
  }
  if (sub) sub.textContent = list.length + ' skill(s)';
}

function renderInstructionsLoadedBreakdown(payload) {
  if (document.body.dataset.activePage !== 'surface') return;
  const data = (payload && typeof payload === 'object') ? payload : {};
  const mt = (data.memory_type_dist && typeof data.memory_type_dist === 'object') ? data.memory_type_dist : {};
  const lr = (data.load_reason_dist && typeof data.load_reason_dist === 'object') ? data.load_reason_dist : {};
  // P4 反映: defensive default は [] (list) — typo の `: {}` を修正。
  const glob = Array.isArray(data.glob_match_top) ? data.glob_match_top : [];
  renderInstrBars('surface-instr-mt', mt, 'memory_type');
  renderInstrBars('surface-instr-lr', lr, 'load_reason');
  renderGlobTable('surface-instr-glob', glob);
  const sub = document.getElementById('surface-instr-sub');
  if (sub) {
    const total = Object.values(lr).reduce((a, b) => a + Number(b || 0), 0);
    sub.textContent = total + ' instruction load(s)';
  }
}

function renderInstrBars(rootId, dict, kind) {
  const root = document.getElementById(rootId);
  if (!root) return;
  // P2 反映: dict は server side で count desc / key asc の insertion order に
  // 整列済み。Object.entries はその順を保持する (ES2020+ + JSON parse 順序保持)
  // ので renderer 側で sort し直さない (= server の契約を信頼する)。
  const entries = Object.entries(dict || {})
    .map(([k, v]) => [k, Number(v || 0)])
    .filter(([k, v]) => k && v > 0);
  if (entries.length === 0) {
    root.innerHTML = '<div class="empty">観測なし</div>';
    return;
  }
  const max = Math.max(...entries.map(([, v]) => v));
  root.innerHTML = entries.map(([k, v]) => {
    const pct = max > 0 ? (v / max) * 100 : 0;
    const al = kind + '=' + k + ': ' + v;
    return '<div class="instr-row" data-tip="instr-bar" data-kind="' + esc(kind) +
      '" data-key="' + esc(k) + '" data-c="' + v + '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
      '<span class="lbl" title="' + esc(k) + '">' + esc(k) + '</span>' +
      '<span class="bar-wrap"><span class="bar" style="width:' + pct.toFixed(1) + '%"></span></span>' +
      '<span class="v">' + fmtN(v) + '</span>' +
      '</div>';
  }).join('');
}

function renderGlobTable(rootId, items) {
  const tbody = document.querySelector('#' + rootId + ' tbody');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    // Question 反映: empty state 文言は "観測なし" に統一。
    tbody.innerHTML = '<tr><td colspan="2" class="empty">観測なし</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(it => {
    const fp = it.file_path || '';
    const c = it.count || 0;
    const al = fp + ': ' + c;
    return '<tr data-tip="glob" data-fp="' + esc(fp) + '" data-c="' + c +
      '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
      '<td class="fp" title="' + esc(fp) + '">' + esc(fp) + '</td>' +
      '<td class="num">' + fmtN(c) + '</td>' +
      '</tr>';
  }).join('');
}
```

### tooltip 拡張 (`dtipBuild()` 分岐 3 件追加)

```javascript
if (kind === 'source') {
  const name = el.getAttribute('data-name') || '';
  const e = el.getAttribute('data-e') || '0';
  const s = el.getAttribute('data-s') || '0';
  const lg = el.getAttribute('data-lg') || '0';
  const rateRaw = el.getAttribute('data-rate') || '';
  const rateText = rateRaw === '' ? '観測待ち'
                                  : (Math.round(parseFloat(rateRaw) * 100) + '%');
  return {
    kind: 'source',
    html: '<span class="ttl">' + esc(name) + '</span>' +
          '<span class="lbl">expansion</span><span class="val">' + e + '</span>' +
          '<span class="lbl">submit</span><span class="val">' + s + '</span>' +
          '<span class="lbl">legacy</span><span class="val">' + lg + '</span>' +
          '<span class="lbl">rate</span><span class="val">' + rateText + '</span>'
  };
}
if (kind === 'instr-bar') {
  const k = el.getAttribute('data-key') || '';
  const c = el.getAttribute('data-c') || '0';
  const fld = el.getAttribute('data-kind') || '';
  return {
    kind: 'instr-bar',
    html: '<span class="ttl">' + esc(k) + '</span>' +
          '<span class="lbl">' + esc(fld) + '</span><span class="val">' + c + '</span>'
  };
}
if (kind === 'glob') {
  const fp = el.getAttribute('data-fp') || '';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'glob',
    html: '<span class="ttl">' + esc(fp) + '</span>' +
          '<span class="lbl">loads</span><span class="val">' + c + '</span>'
  };
}
```

### Page-scoped early-out + hashchange 連携

#58/#59/#60/#61 で確立済み (`body[data-active-page]` 判定 + main IIFE の hashchange
listener)。本 PR で追加実装不要。renderer 2 つに early-out を入れるだけ。

## 🧪 TDD テスト計画

### 新規 server unit tests (`tests/test_skill_surface.py`)

```python
class TestSlashCommandSourceBreakdown:
    def test_empty_events_returns_empty_list(self):
        assert aggregate_slash_command_source_breakdown([]) == []
    def test_single_expansion_only_skill(self):
        # source="expansion" 3 件 → expansion=3, submit=0, legacy=0, rate=1.0
        pass
    def test_single_submit_only_skill(self):
        # source="submit" 4 件 → expansion=0, submit=4, legacy=0, rate=0.0
        pass
    def test_mixed_expansion_and_submit_skill(self):
        # expansion 3 + submit 1 → expansion_rate = 0.75 (modern total=4)
        pass
    def test_legacy_count_separate_field(self):
        # P1 反映: source 欠落 3 件 → legacy=3, expansion=0, submit=0
        # 旧 schema は expansion 扱いに混ぜず、legacy 列に分離する
        pass
    def test_expansion_rate_excludes_legacy_from_denominator(self):
        # P1 反映: expansion=2, submit=2, legacy=10 → rate = 2 / (2+2) = 0.5
        # 分母は modern total (= expansion + submit) のみ、legacy は除外
        pass
    def test_expansion_rate_null_when_no_modern_data(self):
        # P1 反映: expansion=0, submit=0, legacy=5 → rate = None (= 観測待ち)
        pass
    def test_unknown_source_value_treated_as_legacy(self):
        # P1 反映: source="something_new" → legacy 扱い (確実な
        # expansion / submit のみカウント、未知値は legacy)
        pass
    def test_empty_skill_name_skipped(self):
        # skill="" は出力に含まれない
        pass
    def test_zero_observed_skill_not_in_output(self):
        # 全 count 0 の skill は出力対象外 (構造的に発生しないが invariant 確認)
        pass
    def test_sort_by_total_desc_then_skill_asc(self):
        # total = expansion + submit + legacy 降順 / skill 昇順
        # total=5,5,3 / skill=alpha,beta,gamma → [(5,alpha), (5,beta), (3,gamma)]
        pass
    def test_sort_includes_legacy_in_total(self):
        # P1 反映: skill A は modern=2, legacy=10 / B は modern=10, legacy=0 →
        # 両方 total=12 / 10 で B(modern=10) が先 (total 降順)。
        # この test は legacy が sort に入ること自体を pin (= retention 経過後も同順)
        pass
    def test_top_n_cap(self):
        # 25 skill が observed のとき返り値は 20 件
        pass
    def test_expansion_rate_when_no_submit(self):
        # expansion=10, submit=0, legacy=0 → rate = 1.0
        pass
    def test_expansion_rate_when_no_expansion(self):
        # expansion=0, submit=5, legacy=0 → rate = 0.0
        pass
    def test_expansion_rate_rounded_to_4_decimals(self):
        # Q2 反映: expansion=2, submit=1 (modern=3) → rate = 0.6667 (4 桁丸め)
        pass
    def test_skill_tool_events_ignored(self):
        # event_type=skill_tool は対象外 (user_slash_command のみ集計)
        pass
    def test_other_event_types_ignored(self):
        # session_start / notification 等は無視
        pass


class TestInstructionsLoadedBreakdown:
    def test_empty_events_returns_safe_defaults(self):
        out = aggregate_instructions_loaded_breakdown([])
        assert out == {"memory_type_dist": {}, "load_reason_dist": {}, "glob_match_top": []}
    def test_memory_type_distribution_counted(self):
        # mt="Project" 3 件, mt="User" 2 件 → {"Project": 3, "User": 2}
        pass
    def test_load_reason_distribution_counted(self):
        # lr="session_start" 5 件, lr="glob_match" 2 件
        pass
    def test_titlecase_passthrough_no_normalization(self):
        # "Project" と "project" は別キーとして集計される (lower-case しない)
        pass
    def test_empty_memory_type_skipped(self):
        # memory_type="" は分布に含まれない
        pass
    def test_empty_load_reason_skipped(self):
        # load_reason="" は分布に含まれない
        pass
    def test_memory_type_dist_iteration_order_is_count_desc_then_key_asc(self):
        # P2 反映: mt="A":2, "B":5, "C":5 → list(dict.keys()) == ["B","C","A"]
        # (count desc → key asc / Python 3.7+ dict insertion order を契約化)
        pass
    def test_load_reason_dist_iteration_order_is_count_desc_then_key_asc(self):
        # P2 反映: lr の sort 同上
        pass
    def test_glob_match_top_sort_count_desc_path_asc(self):
        # count=5,5,3 / path=a,b,c → [(5,a), (5,b), (3,c)]
        pass
    def test_glob_match_top_n_cap(self):
        # 12 path が observed のとき glob_match_top は 10 件
        pass
    def test_glob_match_only_for_glob_match_load_reason(self):
        # load_reason="session_start" の event は glob_match_top に入らない
        pass
    def test_glob_match_top_counts_only_within_glob_match_scope(self):
        # Q1 反映: 同じ file_path X が load_reason="glob_match" で 3 件 +
        # load_reason="session_start" で 5 件出現 → glob_match_top の X は count=3
        # (= glob_match スコープ内の count のみ積む)
        pass
    def test_glob_match_empty_file_path_skipped(self):
        # file_path="" は除外
        pass
    def test_file_path_home_compression(self):
        # /Users/<HOME>/.claude/skills/foo/SKILL.md → ~/.claude/skills/foo/SKILL.md
        # _compress_home_path を直接 test しても良いが、aggregator を通した end-to-end でも 1 件確認
        pass
    def test_file_path_outside_home_unchanged(self):
        # /etc/foo/bar/CLAUDE.md → そのまま (圧縮対象外)
        pass
    def test_aggregator_does_not_mutate_input_events(self):
        # P3 反映: aggregator に入力 events を渡した後、events[*]["file_path"] が
        # absolute (圧縮前) のまま不変であることを確認 (= in-place rewrite しない)
        pass
    def test_dict_iteration_order_survives_json_roundtrip(self):
        # 2-P1 反映: aggregator → json.dumps → json.loads した後でも
        # memory_type_dist のキー順が aggregator 出力 (= count desc / key asc) と一致する。
        # 仮に将来 json.dumps(sort_keys=True) が混入したら本 test が落ちる
        # regression guard として機能する
        pass
    def test_other_event_types_ignored(self):
        # skill_tool / notification 等は対象外
        pass


class TestCompressHomePath:
    """`_compress_home_path()` の単体テスト。aggregator の path 圧縮 helper。"""
    def test_home_prefix_compressed(self):
        # HOME=/Users/foo, path=/Users/foo/.claude/x → ~/.claude/x
        # monkey patch os.path.expanduser で安定 test
        pass
    def test_home_exact_match_compressed(self):
        # path=/Users/foo (HOME と完全一致) → 圧縮しない (sep が無いので prefix 一致しない仕様)
        # `home + os.sep` 比較なので、HOME 自体は圧縮されない
        pass
    def test_path_outside_home_unchanged(self):
        # path=/etc/foo → /etc/foo
        pass
    def test_empty_path_unchanged(self):
        # path="" → ""
        pass
    def test_home_substring_not_falsely_compressed(self):
        # HOME=/Users/foo, path=/Users/foo-extended/x → 無加工
        # (`home + os.sep` で sep を要求しているので "/Users/foo-extended" には一致しない)
        pass


class TestBuildDashboardDataIncludesSurfaceFields:
    def test_slash_command_source_breakdown_key_present(self): pass
    def test_instructions_loaded_breakdown_key_present(self): pass
    def test_empty_events_returns_safe_defaults(self):
        # 両 key が空状態の正しい型 (list / dict) で出ること
        pass
    def test_constant_TOP_N_SLASH_COMMAND_BREAKDOWN(self):
        # = 20 を pin (将来変更時に明示的に test 更新)
        pass
    def test_constant_TOP_N_GLOB_MATCH(self):
        # = 10 を pin
        pass
    def test_expansion_rate_null_serializes_to_json_null(self):
        # 2-P2 反映: serialize boundary。aggregator が返す Python None が
        # json.dumps を経て JSON null として round-trip し、Python None として
        # parse し直せること。NaN / 文字列 'null' / float('inf') 等への
        # 誤変換を防ぐ structural guard。
        # events = legacy のみの skill 1 件 → expansion_rate=None
        # roundtripped = json.loads(json.dumps(build_dashboard_data(events)))
        # assert roundtripped["slash_command_source_breakdown"][0]["expansion_rate"] is None
        pass
```

### 新規 template tests (`tests/test_surface_template.py`)

```python
class TestSurfacePagePanels:
    def test_surface_section_no_longer_placeholder(self):
        # `page-placeholder` class が surface section から外れていること
        pass
    def test_surface_section_has_source_panel(self):
        # id="surface-source-panel" / "surface-source" / "surface-source-sub"
        pass
    def test_surface_section_has_instr_panel(self):
        # id="surface-instr-panel" / "surface-instr-mt" / "surface-instr-lr" /
        # "surface-instr-glob" / "surface-instr-sub"
        pass
    def test_source_table_has_thead_columns(self):
        # P1 反映: 列順 Skill / Expansion / Submit / Legacy / Rate (5 列)
        pass
    def test_glob_table_has_thead_columns(self):
        # 列順: File path / Count
        pass
    def test_instr_grid_has_three_cols(self):
        # memory_type / load_reason / glob top の 3 column
        pass
    def test_template_has_source_renderer(self):
        # renderSlashCommandSourceBreakdown function 定義あり
        pass
    def test_template_has_instr_renderer(self):
        # renderInstructionsLoadedBreakdown / renderInstrBars / renderGlobTable
        pass
    def test_loadAndRender_invokes_surface_renderers(self):
        # 2 関数すべてが loadAndRender 末尾で呼ばれている
        pass
    def test_source_renderer_has_page_scoped_early_out(self): pass
    def test_instr_renderer_has_page_scoped_early_out(self): pass
    def test_source_renderer_handles_null_rate(self):
        # P1 反映: renderer JS が `expansion_rate === null` 分岐を持ち
        # rate-warn 強調 / "観測待ち" ラベル / colspan が壊れないことを文字列レベルで pin
        # (template 文字列内に "観測待ち" / "rate === null" 分岐表現が存在することを確認)
        pass
    def test_help_popups_present(self):
        # hp-source / hp-instr が定義されている
        pass
```

### 既存 test との整合確認

- `tests/test_dashboard_router.py:71` の `for page in ['surface']:` は
  「placeholder class を持つ section」の前提でテストしている可能性がある →
  実装前に確認し、該当 test の前提を更新 (placeholder class 期待を外す)

### テスト数見込み

- `test_skill_surface.py`: ~39 (slash 18 [P1+Q2 で 4 件追加] + instr 18
  [P2 で 2 件・Q1 で 1 件・P3 で 1 件・2-P1 で 1 件追加] + compress 5 +
  build_dashboard_data 6 [2-P2 で 1 件追加])
- `test_surface_template.py`: ~13 (P1 null rate test 1 件追加)

合計 RED phase: **~52 件追加**。Issue #61 の 47 件追加に近い規模。

## 🚦 Phases

**3-Q1 反映 — commit message convention**: 各 Phase は **独立 commit** で切る。
TDD "one red at a time" 規律と git history の粒度を揃え、レビュー時に各 phase
単独で読みやすくする。Conventional Commits 形式 + `(#62)` で issue 紐付け。

| Phase | commit message 例 |
|---|---|
| 0 | `test(dashboard): remove placeholder expectation from router test (#62 prep)` |
| 1 | `test(dashboard): add Issue #62 skill surface RED tests (~52 fail)` |
| 2 | `feat(dashboard): aggregate slash command source breakdown (#62)` |
| 3 | `feat(dashboard): aggregate instructions_loaded breakdown + path compression (#62)` |
| 4 | `feat(dashboard): surface page UI — source table + instructions distribution (#62)` |
| 6.1 | `docs(spec): add slash_command_source + instructions_loaded breakdown sections (#62)` |
| 6.2-3 | `docs: update CLAUDE.md / MEMORY.md for Issue #62` |

Round 1 + 2 + 3 で固めた反映ログ table は本 plan ファイル内に閉じる (= commit
message には書かない)。

### Phase 0: 既存 test の Surface placeholder 期待を整える (Q3 + 2-P3 反映)

Phase 1 で新 test を RED にし、Phase 4 で template 変更すると、既存
`test_dashboard_router.py::TestRouterShell::test_non_overview_pages_are_placeholders`
が壊れる (= **TDD の "one red at a time" 規律違反**)。これを先に整えてから新 test
RED に進む。

**操作対象**: `tests/test_dashboard_router.py:67-76` の
`test_non_overview_pages_are_placeholders` 関数 (= placeholder 期待を持つ唯一の test)。

**最小修正**: 当該 **test 関数を削除する** (= もう placeholder ページが存在しないため)。
loop 自体が `for page in ['surface']:` の 1 要素 list なので部分修正で残す価値は
無い。docstring も "Surface のみ placeholder" 前提だったので関数全体が陳腐化。

**退化なし確認**:
- `test_template_has_four_page_sections` (line 55-58) が surface section の存在を
  別 assert で守っている
- 他 test (`test_router_javascript_present`, `test_router_initial_apply_route_call`
  等) は placeholder class に依存していない

**手順**:
1. `tests/test_dashboard_router.py:67-76` を削除 (関数本体 + 直前の空行)
2. `pytest tests/test_dashboard_router.py` で残り test が GREEN
3. mini commit (`test(dashboard): remove placeholder expectation from router test (#62 prep)`)
4. **本 phase は 10 行未満の削除で完了**

### Phase 1: TDD RED (新 test ファイル 2 本)

1. `tests/test_skill_surface.py`: 上記 4 class
2. `tests/test_surface_template.py`: 上記 1 class
3. `pytest tests/test_skill_surface.py tests/test_surface_template.py` で全部 fail
4. Phase 0 で既存 test を整えた後なので、新 test の RED 以外に既存 test が
   壊れていないことを `pytest tests/` 全体で確認 (= 新 test の fail だけが残る状態)

### Phase 2: GREEN-A (slash_command_source_breakdown)

`dashboard/server.py` に追加:
- `TOP_N_SLASH_COMMAND_BREAKDOWN = 20` 定数
- `aggregate_slash_command_source_breakdown(events, top_n)` 関数
- `build_dashboard_data` に `slash_command_source_breakdown` 追加

`tests/test_skill_surface.py::TestSlashCommandSourceBreakdown` 全部 GREEN。

### Phase 3: GREEN-B (instructions_loaded_breakdown + path 圧縮)

`dashboard/server.py` に追加:
- `TOP_N_GLOB_MATCH = 10` 定数
- `_compress_home_path(path)` helper
- `aggregate_instructions_loaded_breakdown(events, top_n)` 関数
- `build_dashboard_data` に `instructions_loaded_breakdown` 追加

`tests/test_skill_surface.py::TestInstructionsLoadedBreakdown` /
`TestCompressHomePath` / `TestBuildDashboardDataIncludesSurfaceFields` 全部 GREEN。

**2-P1 反映 — serialize 経路の確認**: Phase 3 末尾で以下を必ず確認する:
1. `build_dashboard_data` の戻り値を JSON 化する全経路 (= SSE handler / `/api/data`
   handler / `render_static_html` の inline) で **`json.dumps(..., sort_keys=True)`
   が付いていないこと** を grep で確認 (`grep -n 'sort_keys' dashboard/server.py`)
2. `tests/test_skill_surface.py::TestInstructionsLoadedBreakdown::test_dict_iteration_order_survives_json_roundtrip`
   が GREEN であること (= aggregator 出力の dict キー順が `json.loads(json.dumps(...))`
   後も保持される regression guard)。
3. P2 反映 dict insertion order 契約を、stdlib serialize に対する暗黙依存として
   明示する。新 helper を入れる人 (e.g. JSON サイズ最適化リファクタ) が
   `sort_keys=True` を不注意で付けると破壊される旨を server.py の関数 docstring
   に 1 行 caveat として書く

### Phase 4: GREEN-C (Surface DOM + renderers + CSS + tooltip)

`dashboard/template.html` に追加:
- `<section data-page="surface">` の中身を panel 2 つに置換
  (`page-placeholder` class 除去)
- CSS: `.source-table` / `.instr-grid` / `.instr-bars` / `.glob-table` ブロック追加
- JS: `renderSlashCommandSourceBreakdown` / `renderInstructionsLoadedBreakdown` /
  `renderInstrBars` / `renderGlobTable` 4 関数追加
- JS: `loadAndRender` 末尾に 2 行 call 追加
- JS: `dtipBuild` に 3 分岐追加 (`source` / `instr-bar` / `glob`)

`tests/test_surface_template.py` 全部 GREEN。

### Phase 5: 実機 smoke + 静的 export 確認

```bash
# 全テスト
python3 -m pytest tests/

# dashboard live
python3 dashboard/server.py
# → http://localhost:<port>/#/surface を開いて確認:
#   - A4 source table が表示される (実機の slash command で expansion / submit が並ぶ)
#   - A4 表で legacy のみの skill のセルに "— 観測待ち" が表示される (P1 反映 null rate UX)
#   - B4 memory_type bar / load_reason bar / glob top が描画される
#   - empty state (実機データに glob_match が無いとき "観測なし" と表示)
#   - hover で tooltip (source / instr-bar / glob) 出る
#   - rate 0.5 未満で peach 色強調 (rate=null は強調されない)
#   - file path home 圧縮済み (~/.claude/...)
#   - dict iteration order: memory_type_dist が count 降順 (例: User=65 → Project=62 順) で表示される
#   - レスポンシブ: 720px 以下で 1 カラム積み

# 静的 export
python3 reports/export_html.py --output /tmp/surface.html
open /tmp/surface.html
# → #/surface で同じ表示になることを確認

# perf 確認 (#61 と同様、回帰してないこと)
time python3 -c "from dashboard.server import build_dashboard_data, load_events; \
  import json; \
  data = build_dashboard_data(load_events()); \
  print(len(json.dumps(data)))"
# /api/data レスポンスサイズ + 計算時間が #61 merge 時点と比べて顕著に伸びていないこと
```

### Phase 6: docs

#### 6.1 spec doc 更新 (`docs/spec/dashboard-api.md`)

`slash_command_source_breakdown` / `instructions_loaded_breakdown` セクションを
追記。schema 形と sort 規約のみ書き、aggregation の domain logic (source の
legacy 分類 / path 圧縮の責務分担) は `memory/skill_surface.md` に集約 (#61 の
2-Q2 反映を踏襲: spec doc は API 形状のみ)。

spec に明示する schema 形:
- `slash_command_source_breakdown[*].expansion_rate` の型: `float (4 桁丸め) | null`
  (P1 / Q2 反映)
- `instructions_loaded_breakdown.{memory_type_dist, load_reason_dist}` の dict は
  **count 降順 → key 昇順 の insertion order** で並ぶ (P2 反映 / Python 3.7+ /
  JSON 仕様で順序保持される前提)
- `instructions_loaded_breakdown.{memory_type_dist, load_reason_dist}` は
  **top_n cap しない** (= 全観測キーを返す)。memory_type / load_reason の値域は
  hooks 仕様で bounded のため (3-P2 反映)
- `instructions_loaded_breakdown.glob_match_top` のみ top_n=10 cap (= `TOP_N_GLOB_MATCH`)
- `instructions_loaded_breakdown.glob_match_top[*].file_path` は home 圧縮済み
  (= `~/...` 形式に変換されている可能性がある、raw absolute path ではない)

#### 6.2 CLAUDE.md 更新

「ダッシュボード複数ページ構成」のページ表に Surface ページの本実装完了を反映
(currently "Surface | スキル surface (発見性 / 想起性)" は表現済みで、文言更新は
最小)。`/api/data` schema の網羅例にも `slash_command_source_breakdown` /
`instructions_loaded_breakdown` を 1 行ずつ追加。

#### 6.3 MEMORY.md / topic memory 追加

新 topic memory `skill_surface.md` を作成して MEMORY.md にリンク追加:

```markdown
- [Skill surface (#62)](skill_surface.md) — slash source breakdown の旧 schema expansion 扱い / 値ゆれ verbatim 保持 / file_path home 圧縮 server side / glob_match top empty state pattern
```

`memory/skill_surface.md` の中身:

```markdown
---
name: Skill surface (#62) — expansion/submit ratio + instructions_loaded
description: Issue #62 で確立した Surface ページ集計の dense decisions — slash source 旧 schema expansion 扱い / memory_type 値ゆれ verbatim 保持 / file_path home 圧縮の server-side 責務 / glob_match top empty state policy
type: project
---
# Skill surface (Issue #62) — dense design decisions

## 命名規約
- `slash_command_source_breakdown` (skill ごとの list)
- `instructions_loaded_breakdown` (memory_type_dist + load_reason_dist + glob_match_top の 1 payload)
- 後続で skill_tool 経由で同等指標 (e.g. tool failure rate per skill) を出すなら
  `skill_tool_*_breakdown` の prefix で additive

## A4 source 分類 — 3 値 (expansion / submit / legacy)
- **dedup と viz は要件が違う**: record_skill.py の dedup ロジックは
  `source != "submit"` を expansion 由来とみなす (= 重複落とさない安全側)。
  本 viz は signal を出す方向の判断なので legacy を expansion に混ぜず分離する
- legacy には: source 不在 (旧 schema) / source 値 unknown ("expansion" /
  "submit" 以外) を含める (= 確実な expansion / submit のみ modern として count)
- `expansion_rate` の分母から legacy を除外、modern が 0 件のとき `null`
  (= 観測待ち) を返す。retention 経過 (180 日) で旧 schema が自然消滅すると
  signal が立ち上がる段差をなだらかにする
- 新 source 値が増えたら spec doc 側で別 field を切る (= submit 扱いに勝手に
  押し込まない)

## B4 値の verbatim 保持
- 実機 `usage.jsonl` 観測では `memory_type` は TitleCase ("Project" / "User")。
  Issue 本文の例 ("user / project / skill") は概念名であり、実値は hooks が
  capture するそのまま
- aggregator で lower-case 正規化はしない (実データの真実を歪めない)
- "Project" と "project" は別キーとして集計される — 観測されたら表示する

## B4 dict の sort 契約 (Python 3.7+ / JSON 順序保持に依存)
- `memory_type_dist` / `load_reason_dist` は dict だが、aggregator は
  **count 降順 → key 昇順 の insertion order** で組み立てる
- Python 3.7+ で dict iteration order が insertion order に固定され、
  `json.dumps` は dict の iteration order でキーを出す。**ECMAScript 仕様で
  string key の挿入順保持が規定されている** ため `JSON.parse` も同順を保つ
  (2-Q1 反映 / version 番号は softening: CLAUDE.md "Number-shaped technical
  identifiers" rule に従い citation 無しの確定的 version 断言を避ける)
- renderer 側で sort し直さない (= server の契約を信頼する) ことで static
  export と live dashboard で表示順がブレない
- **実装注意 (2-P1 反映)**: `json.dumps(..., sort_keys=True)` を server.py の
  serialize 経路に混入させると本契約が破壊される。`tests/test_skill_surface.py::
  test_dict_iteration_order_survives_json_roundtrip` が regression guard として
  動くが、リファクタ時にこの test を skip しないこと

## fine-tune 観測指標
- **legacy 比率 50% 超 skill の順位下落予告** (2-P4 反映): 表で
  `legacy / total ≥ 0.5` の skill は retention 経過 (180 日) で順位下落
  予告と読む。`rescan_transcripts.py` で legacy migration を入れた段階で
  本指標は陳腐化する
- **expansion_rate < 0.5 閾値**: peach 強調が過剰 / 不足だったら閾値再考。
  実機で「peach 強調なし」が長期続くなら閾値上げ検討、「peach 強調が頻出する」
  なら閾値下げ検討
- **glob_match_top の観測量**: skill-creator / glob 連携経路が本格的に
  使われ始めると本 viz が真価を発揮。判定指標は「`load_reason_dist["glob_match"]`
  が `["session_start"]` の 10% 以上を占める」を観測十分の目安とする
  (= absolute 件数より relative ratio で判定 / 事業化に依存しない)

## file_path home 圧縮の責務分担
- 集計関数 (`aggregate_instructions_loaded_breakdown`) 内で `_compress_home_path`
  を適用してから dict に積む
- export_html (静的) でも同じ表示になる + 単一箇所のメンテで済む
- 集計後のキーが圧縮済みなのでキーが分かれない (= raw path と圧縮 path で
  count が分割される事故を構造的に避ける)
- prefix 比較は `home + os.sep` で行う (= "/Users/foo" を "/Users/foo-extended"
  に false-match させない)

## glob_match top empty state
- 実機運用初期は load_reason="glob_match" event がほぼ観測されない (sample size 0)。
  本 PR では空 list `[]` で出して renderer 側で "glob_match なし" メッセージ表示
- skill-creator / glob 連携経路が増えてくると本 viz が真価を発揮
- 観測増えてきたら fine-tune の threshold (e.g. "1 file が 100+ load された
  ら highlight") を後続で検討
```

> Issue #61 で導入した「memory file の Why / How to apply 構造」は
> `feedback` / `project` 両方に適用済み。本 PR の `skill_surface.md` も
> 同じ温度感で書く。

## 🔥 Risk / Edge case

| Risk | 影響 | 対策 |
|---|---|---|
| `source` 旧 schema (欠落) を expansion 扱いにすると実機 `<missing>: 202 / expansion: 75 / submit: 0` で全 skill が rate ≈ 1.0 になり viz の主目的が無効化される | Issue 解釈と異なる「LLM が想起できなかった経路」の signal が消える | **P1 反映で構造的に解消**: legacy 列分離 + rate 分母から legacy を除外 + modern 0 件で `null` (= 観測待ち) 表示。retention 経過 (180 日) で旧 schema が自然消滅するに従い signal が立ち上がる段差をなだらかにする。**Issue 本文 AC「source 欠落時は expansion 扱いで既存ロジックと整合」は plan-reviewer Round 1 / P1 を経て legacy 分離方針に上書き** (3-P1 反映)。PR description で方針差分を明示してから merge する |
| `legacy_count` を sort 分母 (`expansion + submit + legacy`) に入れているため、retention (180 日) 経過で legacy が自然消滅した skill が下位に動き、表示順が時間経過で変動する (2-P4 反映 / Round 2 で挙がった trade-off) | UX 上「先週 top 3 だった skill が消えた」と誤解される | **(a) sort key 自体は modern + legacy で本 PR 維持** (= 上位順位の安定性を優先 / 半年経たないと顕在化しない)、**(b)** `memory/skill_surface.md` の "fine-tune 観測指標" 節で「legacy 比率 50% 超の skill は順位下落の予告」を申し送り、**(c)** 後続 PR で `rescan_transcripts.py` 経由 legacy migration を入れたら段差を埋められる (申し送り済み) |
| `memory_type` の値ゆれ ("Project" vs "project") を verbatim 表示すると UI 上で重複に見える | UX 上「同じ概念が 2 行」になる | hooks 側 (`record_session.py`) が capture する値が一意であれば実害なし。観測されたら hooks 側で正規化を別 PR で検討 (本 PR は触らない: 実データ歪めない原則) |
| `glob_match` event がローカル data でほぼ 0 件 → glob_match_top が空 | UI 上 "観測なし" のままで「壊れてる?」と誤解 | help-pop で「glob 連携経路で multiple ロードされた skill / CLAUDE.md を集計」と明示。empty state messaging も文言設計 |
| `_compress_home_path` の prefix 比較が緩いと "/Users/foo-extended/x" を "/Users/foo" に false-match させてしまう | 別ユーザーディレクトリの path が誤って圧縮される | `home + os.sep` 比較で sep を必須にして false-match を構造的に防ぐ。test ケース 1 件追加 (`test_home_substring_not_falsely_compressed`) |
| Surface page が長くなってスクロール多 | UX 良くない | 2 panel のみ (#61 の 3 panel より少ない)。grid layout で memory_type / load_reason / glob top を横並び 3 col にして縦長を抑える |
| `expansion_rate < 0.5` の閾値が硬い | 実機で「rate 0.6 でも実は危険」みたいな観測ずれ | 申し送りに「閾値 0.5 は v1 の暫定。実機で submit 多発 skill を見て fine-tune」と明記。`memory/skill_surface.md` の "fine-tune 観測指標" 節に同記録 |
| 旧 PR の static export (= `report.html`) が schema 不在キーで例外 | export_html が落ちる | renderer 側 `Array.isArray(items) ? items : []` / `data && typeof data === 'object'` 等の defensive guard を最初から入れる (#60/#61 と同パターン) |
| `instructions_loaded` の path が absolute pre-`~`-compression 状態で fingerprint 計算等に使われていた場合、本 PR の集計 layer 圧縮が後続処理に影響 | 別 viz / verify_session 連携が壊れる | `_compress_home_path` は **集計 dict のキーにのみ** 適用 (raw event は無加工)。test で raw event 不変を確認 (= aggregator 出力以外で path 値が verbatim 残ること、本 PR では verify_session 連携無いので影響なし) |

## 📦 変更ファイル一覧

### 新規
- `tests/test_skill_surface.py` (server unit tests, ~39 件)
- `tests/test_surface_template.py` (template structure tests, ~13 件)
- `docs/plans/issue-62-skill-surface.md` (本 plan)

### 変更
- `dashboard/server.py`
  - `TOP_N_SLASH_COMMAND_BREAKDOWN` / `TOP_N_GLOB_MATCH` 定数
  - `_compress_home_path(path)` helper
  - `aggregate_slash_command_source_breakdown(events, top_n)`
  - `aggregate_instructions_loaded_breakdown(events, top_n)`
  - `build_dashboard_data` に 2 key 追加
- `dashboard/template.html`
  - `<section data-page="surface">` 中身を panel 2 つに置換 (placeholder 除去)
  - CSS `.source-table` / `.instr-*` / `.glob-table` ブロック追加
  - JS `renderSlashCommandSourceBreakdown` / `renderInstructionsLoadedBreakdown` /
    `renderInstrBars` / `renderGlobTable` 4 関数追加
  - JS `loadAndRender` 末尾 2 行 call 追加
  - JS `dtipBuild` 3 分岐追加
- `tests/test_dashboard_router.py` (Surface placeholder 期待を外す — Phase 0 で対応)
- `docs/spec/dashboard-api.md` (schema 追記)
- `CLAUDE.md` (`/api/data` schema 例に 2 key 追加)
- `~/.claude/projects/.../memory/MEMORY.md` (topic memory link 追加)
- `~/.claude/projects/.../memory/skill_surface.md` (新規 topic memory)

## 🔮 申し送り (本 PR で deferred / 後続候補)

- **`source` 必須化 (P1 後の追従)**: 本 PR で `legacy_count` 分離は構造化済みだが、
  retention 経過後に legacy が自然消滅するのを待つだけだと数ヶ月レンジ。
  `rescan_transcripts.py` で過去 transcript から source 値を再生成して append、
  または旧 record にデフォルト source を投入する別 issue で短縮可能
- **Issue 本文 AC との方針差分 PR コメント** (3-P1 反映): Issue #62 本文テスト要件
  「source 欠落時は expansion 扱いで既存ロジックと整合」は本 PR で legacy 列分離
  方針に上書きしている。**PR description / Issue close コメント** でこの方針差分を
  明示し、レビュアー / 半年後の保守者が「テスト要件と実装方針の矛盾」を再蒸し返さない
  ようにする (= AC を満たさないわけではなく、レビューを経て更新したという解釈)
- **UI 文言の集約 / i18n layer** (3-P3 反映): 本 PR の renderer / tooltip /
  aria-label に `観測なし` / `観測待ち` / その他 Japanese 文言が hardcoded で
  分散している (#60 / #61 の慣習踏襲)。累積した文言量が増えてきたら `_UI_LABELS = {...}`
  定数化 or i18n layer (gettext / message catalog) 導入を別 PR で検討。本 PR の
  defensive 統一は scope discipline で見送り
- **3 col grid → 2:1 grid 化** (Question 反映 carry): 実機で `load_reason` が
  `session_start` 1 値しかない現状では `(memory_type / load_reason) : glob_match_top`
  の `1:1:2` レイアウトが妥当。observed が増えて load_reason のキー数が 5+ に
  なってきたら `(memory_type) : (load_reason+glob_match_top)` の 1:2 化を検討
- **expansion_rate < 0.5 閾値の fine-tune**: 実機で「peach 強調が誤検知」「目立たない」
  パターンが出てきたら閾値再考 (`memory/skill_surface.md` で trend 観測)
- **memory_type 正規化**: hooks 側で TitleCase / lowercase が混在するなら
  `record_session.py` 側で `.lower()` 等の正規化を別 PR で。本 PR は実データを
  歪めない原則に従い verbatim
- **load_reason="glob_match" 観測増 → glob_match top の閾値 / highlight**:
  observed 増えてきたら "1 file が N 件以上 load されたら highlight" 等の
  threshold を導入
- **`reports/summary.py` への移管**: 本 PR の 2 集計関数は `dashboard/server.py`
  ローカル。`reports/summary.py` が同等の textual report を出すように後続で
  移管。共有化圧力が来たら検討 (#60 / #61 教訓)
- **`reports/export_html.py` の Surface ページ反映**: static export は
  `/api/data` schema を `window.__DATA__` に inject するだけなので、本 PR の
  spec 追加で自動的に反映される (= 別 PR 不要)。Phase 5 静的 smoke で確認
