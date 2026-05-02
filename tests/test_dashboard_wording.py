"""tests/test_dashboard_wording.py — Issue #89: ダッシュボード文言の統一・整理。

検証対象は `load_assembled_template()` が返す **assembled template の生文字列**
(= shell.html + concat 済 styles + concat 済 scripts の literal source)。
ランタイムの DOM ではない。

訳語表 (normative source) は docs/plans/89-dashboard-wording.md §2 を参照。
"""
# pylint: disable=line-too-long
import re

from _dashboard_template_loader import load_assembled_template


def _load() -> str:
    return load_assembled_template()


def test_no_residual_english_labels():
    """旧文言 (v3 方針で書き換え対象) が消えていること。"""
    template = _load()
    forbidden = [
        # Claude-spec 片仮名 (→ 英語形に統一: §2.2 A)
        "スキル利用ランキング",
        "サブエージェント呼び出し",
        '<span class="pop-ttl">スキル共起</span>',
        "スキル共起マトリクス",
        "プロジェクト × スキル",
        "スキルが「呼ばれているか」",

        # Empty state (§2.3 全て `no data` に統一)
        "共起データなし",
        ">データなし<",
        "subagent データなし",
        "trend データなし",
        "permission prompt なし",
        "compact なし",
        "観測なし",

        # 意味不明な日本語 (§2.4)
        "上位漏れ",
        "長尾分布",

        # 一般語日本語化 (§2.2 B)
        "実行品質と摩擦シグナルを可視化します",
        ">mtime<",
        "mtime ≤14 日 / 未使用",
        "タイミングを逃した signal",
        "上位漏れは表示しないが",

        # footer Claude-spec 用語 (§2.2 A)
        ">セッション</span>",

        # Hibernating skills 翻訳 (❶, v4)
        "Hibernating skills",

        # KPI tile s: 一般語日本語化 (❸, §2.5)。
        # ※ k: の「カードタイトル」は user follow-up でユーザー判断により全英語に戻す。
        # forbidden には残さない (total events / projects / resume rate / permission gate)。
        "s: 'unique kinds'",
        "s: 'distinct cwds'",

        # KPI helpTtl 翻訳 (Q2)
        "helpTtl: 'Permission Prompt'",

        # `<th>` 一般語日本語化 (❻, v5 反映: Compact 維持)
        '<th class="num">Count</th>',
        '<th class="num">Samples</th>',
        '<th class="num">avg</th>',
        '<th class="num">Prompts</th>',
        '<th class="num">Invocations</th>',
        '<th class="num">Rate</th>',
        "<th>Project</th>",
        "<th>Mode</th>",
        '<th class="num">👤 User</th>',

        # Sparkline stats (❼ 日本語化)
        "k: 'peak'",
        "k: 'avg/day'",
        "k: 'active'",
        "k: 'window'",

        # MODE_LABEL chip 旧 lowercase (Step 4c で大文字化)
        "'🤝 dual'",
        "'🤖 llm-only'",
        "'👤 user-only'",
        # MODE_TIP 旧 'Mixed' (Step 5 で 'Dual' に変更)
        "'🤝 Mixed'",

        # 動的構築の TERNARY EXPR
        "' invocations' : ' uses'",
    ]
    for s in forbidden:
        assert s not in template, f"{s!r} がテンプレに残存している"


def test_required_new_labels_present():
    """新ラベルが追加されていること (positive assertion)。"""
    template = _load()
    required = [
        # Claude-spec 英語 (§2.2 A)
        "Skill 利用ランキング",
        "Subagent 呼び出し",
        "Skill 同時利用マトリクス",
        '<span class="pop-ttl">Skill 同時利用</span>',
        "Project × Skill",
        "Skill が「呼ばれているか」",

        # Empty state (§2.3)
        ">no data<",

        # 意味不明な日本語の改訳 (§2.4)
        "上位 10×10 に含まれない",
        "裾の長い分布",
        "Skill 同時利用",

        # 一般語日本語化 (§2.2 B)
        "実行品質と摩擦の兆候を可視化します",
        "更新日時 14 日以内 / 未使用",
        "<th>更新日時</th>",
        "タイミングを逃した兆候",

        # footer Claude-spec (§2.2 A)
        ">sessions</span>",

        # Hibernating skills 翻訳 (❶, v4)
        "休眠スキル",

        # KPI tile s: 日本語化 (❸, §2.5)。
        # ※ k: の「カードタイトル」は user follow-up により全英語維持。
        # required 側にも total events / projects / resume rate / permission gate
        # を pin することはしない (Claude-spec 以外も英語形に統一)。
        "s: '種類'",
        "s: 'ディレクトリ単位'",

        # KPI helpTtl 翻訳 (Q2)
        "helpTtl: '承認待ち'",

        # `<th>` 日本語化 (❻, v5 反映: Compact 維持)
        '<th class="num">件数</th>',
        '<th class="num">サンプル数</th>',
        '<th class="num">平均</th>',
        '<th class="num">プロンプト数</th>',
        '<th class="num">呼び出し回数</th>',
        '<th class="num">比率</th>',
        "<th>プロジェクト</th>",
        "<th>起動モード</th>",
        '<th class="num">👤 ユーザー</th>',

        # Sparkline stats (❼)
        "k: 'ピーク'",
        "k: '1 日あたり平均'",
        "k: '稼働日数'",
        "k: '期間'",

        # MODE_LABEL chip 大文字統一 (§2.2 C / Step 4c)
        "'🤝 Dual'",
        "'🤖 LLM-only'",
        "'👤 User-only'",

        # 動的構築の TERNARY EXPR 新形
        "' 呼び出し' : ' 件'",
    ]
    for s in required:
        assert s in template, f"{s!r} がテンプレに見当たらない"


def test_invariant_keys_unchanged():
    """data-* / class / id / page key / MODE_LABEL key / MODE_TIP key が変わっていないこと。

    §1 Non-Goals 構造保証: schema フィールド・data-* attribute・MODE_LABEL/MODE_TIP key
    などは触らない。MODE_TIP のキーは Issue #94 で 'mixed' → 'dual' に整合化済。
    """
    template = _load()
    invariants = [
        'data-page="overview"', 'data-page="patterns"',
        'data-page="quality"', 'data-page="surface"',
        'data-page-link="overview"',
        'data-tip="rank"', 'data-tip="cooc"', 'data-tip="projskill"',
        'data-tip="percentile"', 'data-tip="trend"',
        'data-tip="perm-skill"', 'data-tip="perm-subagent"',
        'data-tip="histogram"', 'data-tip="worst-session"',
        'data-tip="inv"', 'data-tip="life"', 'data-tip="hib"',
        "'dual'", "'llm-only'", "'user-only'",  # MODE_LABEL / MODE_TIP key (Issue #94 で整合化)
        "'accelerating'", "'stable'", "'decelerating'", "'new'",
        "'warming_up'", "'resting'", "'idle'",
        # KPI id は runtime に DOM concat で生成されるので、JS 側の literal ('id: \'kpi-*\'')
        # を構造の不変条件として pin する。20_load_and_render.js / 25_live_diff.js の双方に出現。
        "id: 'kpi-total'", "id: 'kpi-skills'", "id: 'kpi-subs'",
        "id: 'kpi-projs'", "id: 'kpi-sess'", "id: 'kpi-resume'",
        "id: 'kpi-compact'", "id: 'kpi-perm'",
        # static DOM id (shell.html 側に literal 存在)
        'id="dataTooltip"', 'id="liveToast"', 'id="connStatus"',
    ]
    for s in invariants:
        assert s in template, f"invariant key {s!r} が消えた"

    # === Paired-negative key invariants + chip-tooltip parity ===
    # MODE_TIP は 90_data_tooltip.js 内、MODE_LABEL は 50_renderers_surface.js 内。
    # concat 後の template 全文から各 const 宣言ブロックを切り出して block-scoped に判定する。
    mode_tip_match = re.search(r"const MODE_TIP\s*=\s*\{([^}]+)\}", template)
    assert mode_tip_match, "MODE_TIP 宣言ブロックが見つからない (90_data_tooltip.js の構造変化を疑え)"
    mode_tip_block = mode_tip_match.group(1)

    mode_label_match = re.search(r"const MODE_LABEL\s*=\s*\{([^}]+)\}", template)
    assert mode_label_match, "MODE_LABEL 宣言ブロックが見つからない (50_renderers_surface.js の構造変化を疑え)"
    mode_label_block = mode_label_match.group(1)

    # MODE_TIP には 'dual' があり、旧キー 'mixed' は無い (Issue #94 で整合化)
    assert "'dual'" in mode_tip_block, "MODE_TIP の 'dual' キーが消えた (Issue #94 整合契約)"
    assert "'mixed'" not in mode_tip_block, "MODE_TIP に 'mixed' を再導入してはならない (Issue #94 で除去済)"

    # MODE_LABEL には 'dual' があり、'mixed' は無い (既存設計)
    assert "'dual'" in mode_label_block, "MODE_LABEL の 'dual' キーが消えた"
    assert "'mixed'" not in mode_label_block, "MODE_LABEL に 'mixed' を追加してはならない"

    # === Chip ↔ tooltip parity (iter2 P4: 表示文字列が両ブロックで一致する契約) ===
    # MODE_LABEL[dual] と MODE_TIP[dual] が同じ表示文字列 '🤝 Dual' を持つ (chip ↔ tooltip parity)。
    assert "'🤝 Dual'" in mode_label_block, "MODE_LABEL の 'dual' 値は '🤝 Dual'"
    assert "'🤝 Dual'" in mode_tip_block, "MODE_TIP の 'dual' 値も MODE_LABEL と同じ '🤝 Dual'"
    assert "'🤖 LLM-only'" in mode_label_block and "'🤖 LLM-only'" in mode_tip_block
    assert "'👤 User-only'" in mode_label_block and "'👤 User-only'" in mode_tip_block


# mode は dual / llm-only / user-only の 3 種。将来拡張時はここを更新。
EXPECTED_MODE_COUNT = 3


def test_mode_label_tip_key_parity():
    """MODE_LABEL (writer) と MODE_TIP (reader) の key 集合が完全一致していること (Issue #94)。

    Issue #94 で `MODE_TIP` の key を `'mixed'` → `'dual'` に整合化した。
    schema → renderer (MODE_LABEL) → tooltip (MODE_TIP) のキー一致が崩れると
    tooltip lookup が miss する (例: `'mixed'` 残存時に dual 行 hover で生 literal 表示)。
    本 test は片側のみ更新された場合の structural regression を fail-fast で検知する。

    既存の chip ↔ tooltip display parity test (test_invariant_keys_unchanged 内 L218-)
    は **value-based** (`'🤝 Dual'` 等の表示文字列の有無)、本 test は **key-based**
    (lookup キー集合の一致)。両軸が直交する defense-in-depth。
    """
    template = _load()

    mode_label_match = re.search(r"const MODE_LABEL\s*=\s*\{([^}]+)\}", template)
    assert mode_label_match, "MODE_LABEL 宣言ブロックが見つからない"
    mode_tip_match = re.search(r"const MODE_TIP\s*=\s*\{([^}]+)\}", template)
    assert mode_tip_match, "MODE_TIP 宣言ブロックが見つからない"

    # colon-anchored regex で key だけを抽出 (value 側 emoji 文字列の false positive 回避)
    label_keys = set(re.findall(r"'([a-z][a-z-]*)'\s*:", mode_label_match.group(1)))
    tip_keys = set(re.findall(r"'([a-z][a-z-]*)'\s*:", mode_tip_match.group(1)))

    # 多層 fail-fast guard: 空 set 化 (regex 完全失敗で false green) を阻止
    assert label_keys, "MODE_LABEL からキーが 1 つも抽出できない (regex 失敗を疑え)"
    assert tip_keys, "MODE_TIP からキーが 1 つも抽出できない (regex 失敗を疑え)"

    # 個数固定: block 抽出 regex の partial-match で短い set が返るリスクを閉じる
    assert len(label_keys) == EXPECTED_MODE_COUNT, (
        f"MODE_LABEL key count drifted: {sorted(label_keys)}"
    )
    assert len(tip_keys) == EXPECTED_MODE_COUNT, (
        f"MODE_TIP key count drifted: {sorted(tip_keys)}"
    )

    # 本体: 両者の key 集合一致 (片側更新の structural regression を検出)
    assert label_keys == tip_keys, (
        f"MODE_LABEL keys {sorted(label_keys)} != MODE_TIP keys {sorted(tip_keys)}"
    )


def test_period_toggle_labels_intact():
    """期間トグル (`7d` / `30d` / `90d` / `全期間`) のボタン文言が維持されていること (§2.6)。"""
    template = _load()
    for s in (">7d<", ">30d<", ">90d<", ">全期間<"):
        assert s in template, f"period toggle label {s!r} が消えた"
    assert 'aria-label="集計期間"' in template, "期間トグル aria-label が消えた"


def test_kpi_help_titles_localized():
    """`helpTtl: '...'` の値がすべて日本語化されていること (過渡期の取りこぼし検出)。

    iter2 C3 反映: 「kpis 配列の entry 数」と「helpTtl 数」が一致することを cross-ref で assert。
    """
    template = _load()
    # cross-ref は `const kpis = [ ... ];` ブロック内に scope する。25_live_diff.js にも
    # `id: 'kpi-*'` literal が出てくるが、それは live-diff の名前テーブルで helpTtl を持たない。
    kpis_block_match = re.search(r"const kpis\s*=\s*\[(.*?)\];", template, re.DOTALL)
    assert kpis_block_match, "20_load_and_render.js の `const kpis = [ ... ];` 宣言ブロックが見つからない"
    kpis_block = kpis_block_match.group(1)
    help_ttls = re.findall(r"""helpTtl:\s*['"]([^'"]+)['"]""", kpis_block)
    kpi_entries = re.findall(r"id:\s*'kpi-[a-z]+'", kpis_block)
    assert len(help_ttls) == len(kpi_entries), \
        f"helpTtl 数 {len(help_ttls)} ≠ KPI entry 数 {len(kpi_entries)}"
    for ttl in help_ttls:
        # ASCII printable のみで構成された helpTtl は無いはず ("Resume 率" 等 mix は OK)
        assert not re.fullmatch(r"[\x20-\x7E]+", ttl), f"{ttl!r} が完全 ASCII"


def test_empty_state_messages_unified():
    """全 empty state 文言が `no data` に統一されていること (v3 方針 §2.3)。"""
    template = _load()
    # word-boundary に厳密化: class 値が "empty" 単体 か "empty <修飾>" / "<修飾> empty" の形のみ。
    # `empty-row` / `empty-state-warn` 等の派生 class 名は除外する。
    # `projskill-empty` は Issue #59 の独立 class 名 (CSS 上 `.empty` と同じ属性) で、
    # plan §1 Non-Goals により class 名は維持。本 test では同 class も empty 一族として拾う。
    pattern = re.compile(r'class="(?:[^"]*\s)?(?:empty|projskill-empty)(?:\s[^"]*)?">([^<]+)<')
    matches = pattern.findall(template)
    assert matches, "empty 状態セルが 1 件もマッチしない (regex 不一致の可能性)"
    for txt in matches:
        assert txt.strip() == "no data", f"empty state {txt!r} が 'no data' でない"
