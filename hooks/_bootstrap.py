"""hooks/_bootstrap.py — entry-point leaf 用 sys.path イディオムの正典 (Issue #121)。

このリポジトリの entry-point leaf (`hooks/` `commands/` `reports/` `scripts/`
`dashboard/` 直下から plugin が直参照するスクリプト) は
`python <abs path>/<leaf>.py` で **単体起動** される (`python -m` ではない)。
そのため `analyzer` パッケージを import するには、leaf 自身が repo root を
`sys.path` に載せる必要がある。

全 leaf はこの「同一イディオム」を冒頭に置くことで統一する::

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from analyzer.<...> import ...  # noqa: E402

ルール:

- `analyzer/` パッケージ内部は **絶対 import のみ**。sys.path ハックを持たない。
- `sys.path.insert` が許されるのは entry-point leaf だけ。新しい leaf を追加
  するときは上記スニペットをそのままコピーする。
- `sys.path` 操作後の `analyzer.*` import 行には `# noqa: E402` を 1 行ずつ
  付ける (ruff の E402 は per-line 指定のため)。

このモジュール自体は import されない。各 leaf が上記イディオムを inline する
方式を採るため、ここは「正典スニペットの単一の参照点」として存在する
(`from hooks import _bootstrap` は repo root が未だ sys.path に無い単体起動
経路では解決できず、leaf ごとに依存方向が割れるため import 方式は採らない)。
"""
