# Archived plans

実装が完了し、対応 Issue が CLOSED 済みの plan を保存するディレクトリ。
歴史的経緯を辿るための **read-only アーカイブ** として残している（削除しない）。

| File | 対応 Issue / リリース |
|---|---|
| `IMPLEMENTATION_PLAN.md` | v0.1 初期実装計画（`install.sh` / `install/merge_settings.py` 時代の前提を含む。Issue #11 で installer は削除済） |
| `VERIFY_SESSION_PLAN.md` | `hooks/verify_session.py` (Stop hook + 整合性チェック) の実装計画 |
| `issue-57-dashboard-multipage-shell.md` | Issue #57 (v0.7.0 基盤・複数ページ化) |
| `issue-58-hourly-heatmap.md` | Issue #58 (v0.7.0 hourly heatmap) |
| `issue-59-cross-tab-viz.md` | Issue #59 (v0.7.0 共起 + project×skill heatmap) |
| `issue-60-subagent-quality.md` | Issue #60 (v0.7.0 subagent percentile + failure trend) |
| `issue-61-friction-signals.md` | Issue #61 (v0.7.0 permission/skill 紐付け + compact 密度) |
| `issue-62-skill-surface.md` | Issue #62 (v0.7.0 skill surface insights) |

## 注意

- 各 plan の記述は **当時のコードベース前提** であり、現在の実装と一致しない記述が含まれる
- 現行仕様は `docs/spec/` および `CLAUDE.md` を参照
- 過去 plan を後追いで参照したい場合のみ、ここから読む
