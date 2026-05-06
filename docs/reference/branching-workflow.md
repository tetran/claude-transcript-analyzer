# Release-branch model — base 確定 / 作成べき等性 / branch protection

CTA は CLAUDE.md「Branching workflow」で **release branch model** を採用している
（`main` ← `vX.Y.Z` ← `feature/<issue>-<slug>`）。このモデルに乗ると、generic な
trunk-based projects では出ない 3 種類の罠が固有に出る:

1. Feature branch を **どの base から切るか** の判断 — `main` は最新ではない
2. Release branch（= shared coordination branch）の **作成べき等性** — 同時並列の writer 衝突
3. Solo public repo 向け **branch protection** preset

このページはその 3 つの recipe を集める。

---

## Base branch の確定 — feature を切る前

Release branch model では `git log origin/main` だけだと誤誘導される: 直近マージ
された PR は release branch を target にしている可能性があり、`main` には来ていな
いことがある。Feature branch の base を決める前に、プロジェクトの branching
convention を **直近マージ済 sibling PR の base を確認** して検証する:

```bash
gh pr view <recent-PR> --json baseRefName
```

新規 feature branch は通常 release branch を target にすべきで、main ではない
（その release branch 自体が main にマージされるまでは）。

等価な症状: ローカル `main` が「stale」に見えるが `git fetch` で何も来ない —
これが `baseRefName` を確認すべき cue（sync 問題と決めつけてはいけない）。

### より高速なシグナル — `gh pr view` の前に

- **Merge commit pattern** — `git log --merges` の中の `Merge pull request #N from <user>/vX.Y.Z` 行は「release branch が main にマージされた」シグナル。1 件あれば十分、`gh` 呼び出し不要
- **Batch base view** — `gh pr list --state merged --limit 3 --json baseRefName,headRefName,number` で直近 3 件のマージ先が `main` か `vX.Y.Z` かを 1 コールで確認（リポジトリ onboarding 時に有用）
- **Milestone heuristic** — Issue に milestone（例 `v0.7.1`）が付いていれば、PR base = その milestone branch（main ではない）と仮定 → `gh pr view <past-PR>` で裏取り

---

## Coordination branch のべき等性 — 作成 push 前にチェック

Release branch（または共有 coordination branch 全般）の作成は **「first writer wins」 coordination problem**。明示的なハンドリングなしだと、second writer は noisy に失敗するか、最悪、divergent な branch を silent に再作成する。`git push -u origin <new-branch>` は典型的な feature procedure の中で **唯一の non-reversible step** である（file 編集は local-reversible、commit は amendable、しかし origin coordination branch の force-overwrite は他 feature の commit を消し得る）。

### Idempotency recipe

create step を以下の binary split で前置する。ref が無ければ create + push、ref が
あれば fetch + checkout + pull。両 path とも「最新の `<ref>` に local が乗っている」状態で抜ける:

```bash
if [ -z "$(git ls-remote --heads origin <ref>)" ]; then
    git checkout -b <ref> && git push -u origin <ref>
else
    git fetch origin <ref> && git checkout <ref> && git pull --ff-only origin <ref>
fi
```

CTA では release branch / stacked-PR integration branch / hotfix branch 全般に適用。
release branch 特化では「サイクル中で create once, reuse for all features」が前提
（coordination cost を 1 回だけ払う設計）。`patch-release` / `start-issue-planning` /
`stacked-pr-workflow` の各 skill はこの check を手順に組み込んでおり、planner が
記憶を頼りにする方式は取らない。

実例: `docs/plans/81-overview-counts-uncap.md` および `docs/plans/session-page-cost-estimation.md` の Step 0 でこの recipe を踏襲している。

---

## Branch protection — solo public repo preset

CTA は「個人 public repo + owner が唯一 committer + CI gated PR を欲しい」preset。
推奨 baseline:

| 設定 | 値 | 理由 |
|---|---|---|
| `enforce_admins` | `false` | owner の emergency hotfix path を保つ。同時に accidental direct push の弱体化はしない |
| `required_pull_request_reviews.required_approving_review_count` | `0` | 「PR 経由 merge のみ」を強制しつつ solo owner が self-merge できる（≥1 だと外部レビュアー必須 = solo では不可能。`required_pull_request_reviews: null` だと PR 強制自体が消える） |
| `restrictions` | `null` | リポジトリが GitHub team plan に乗っていない場合、唯一許される値。`{}` を送ると 422 |
| `required_status_checks.strict` | `true` | PR は merge 前に base と up-to-date であることを強制 — auto-merge / queue tooling と相性が良い |
| `required_status_checks.contexts` | `[<workflow check display names>]` | display name vs job key の罠あり（下記） |

事前確認:

```bash
gh api repos/<owner>/<repo>/branches/main/protection
```

unprotected branch では `404` + `"message":"Branch not protected"` が **期待通り**
の応答（エラー扱いしない）。PUT / PATCH endpoint の完全なフィールドリファレンスは
GitHub REST API docs を見る — フィールドセットは進化しているのでここに pin した
snippet を置くと confabulation に drift する。

stricter rules（`enforce_admins: true` または required reviewers ≥1）を選ぶ場合の
tradeoff: owner が唯一の available reviewer な状況での emergency hotfix を block
する可能性。protection を tightening するなら、emergency 時にどう扱うかを上流で
決めておく。

### Required status check 周辺の罠（github-authoring skill 参照）

`required_status_checks.contexts` を運用する際に踏みやすい罠は universal な GitHub
プラットフォーム挙動で、詳細は `~/.claude/skills/github-authoring/SKILL.md` の
「Gotchas」セクション（`#3` display name vs job key / `#4` workflow rename →
silent BLOCKED / `#5` job 名の `#` truncation）に集約されている。CTA 文脈での
1 行サマリ:

- **Display name vs job key**: `contexts` は GitHub Actions が emit する display name で照合される。Matrix job は `build (3.10)` 形式で render される（`build` ではない）。protection rule に貼る前に最新 run から正確な display name を取る
- **Workflow rename → 永久 BLOCKED**: `.github/workflows/` を rename すると context name が live coupling のまま陳腐化し、PR が「Expected — Waiting for status to be reported」 状態で永久 block。release PR の `mergeStateStatus: BLOCKED` は transient flake より先に context 陳腐化を疑う
- **Job 名の `#` truncation**: job-level `name:` に `#` を含むと check name で silent truncation される。Issue 参照は YAML コメント（`# Issue #33 regression guard`）に移し、`name:` フィールドからは外す
