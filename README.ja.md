# Glintory

Find the signals worth building on.

## GitHub-Native Scheduled Collection & Durable State Architecture

Glintory はサーバーへの常駐型スケジューラを排し、GitHub Actions の Scheduled Workflow を外部スケジューラとして利用するサーバーレス型アーキテクチャを採用しています。定刻に一度起動し、状態アセットを取得・実行・検証し、静的サイトを生成して終了します。

### アーキテクチャの実行順序

一連の自動実行は、以下の整合性のある順序で実行されます：

1. **Preflight**: 公開用サイトURLが https スキーマかつ不正なクエリやフラグメントを含まないことをPython上の同一ロジックで完全検証。
2. **State Restore**: 前回保存された SQLite 状態（アーカイブ）を GitHub Release (`glintory-state`) から取得・展開。
3. **Migration**: Alembic を用いたスキーママイグレーションの最新化。
4. **Manifest Sync**: 公開用インプット設定 (`public-sources.json`) をDBに同期。
5. **Collection**: スケジュールに基づき、Due（実行期日）に達しているソースのデータ収集を実行。
6. **Deterministic Opportunity Analysis**: 収集データから重複排除・クラスタリング・証拠紐付け。
7. **Deterministic Scoring**: 決定論的ルールに基づく優先度スコアリング。
8. **Local LLM Enrichment**: GitHub Actions 上のローカル LLM による Opportunity の解説・分析の生成。
9. **Static Site Build**: 最新の状態から Jinja2 を用いて公開用の静的サイトを出力ディレクトリにビルド。
10. **State Snapshot**: DBをWALチェックポイント化し、現在の状態の SQLite スナップショットおよびマニフェストを含むアーカイブを作成。
11. **Local Verify**: アーカイブ構成、ファイル名制限、サイズ、およびハッシュのローカル検証。
12. **Release Upload**: 検証合格したアーカイブを GitHub Release にアップロード（Clobber不可）。
13. **Post-upload Verify**: アップロードされたリモートアセットを再ダウンロードし、SHA-256一致等の整合性を二重検証。
14. **Prune**: 検証成功後、過去 of アーカイブのうち古いものを削除し、最新の5世代のみを維持。
15. **Pages Artifact Upload**: 静的サイトのビルドファイルを Pages アーティファクトとしてアップロード。
16. **Pages Deploy**: サイトをデプロイ。
17. **Notify**: ジョブ成否や収集のステータスに基づき、通知を制御。

> [!WARNING]
> **ビルド失敗時の Fail-Closed 保護**
> 静的サイトのビルド（Static Site Build）が失敗した場合は、新しい State のアップロード、古いアセットの Prune、および Pages のデプロイは一切実行されません。既存の状態アセットは安全に維持されます。

### GitHub API と State 管理 (`github_state_store.py`)

状態（SQLite データベース）は、GitHub Actions の実行終了時に `glintory-state-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}.tar.gz` 形式のアーカイブに圧縮され、GitHub Release `glintory-state` に prerelease 属性付きアセットとしてアップロードされます。

- **最新 State の自動判別**:
  REST API (`repos/:owner/:repo/releases/tags/glintory-state`) に登録されたアセット名から `^glintory-state-[0-9]+-[0-9]+\.tar\.gz$` にマッチするものを検索し、`created_at DESC` および `id DESC` でソートして最も新しいアセット1件をダウンロードします。
- **堅牢な二重検証 (Double Verification)**:
  アップロード終了後、アップロードされたアセットを別の一時フォルダへ再ダウンロードし、展開検証・マニフェスト照合・ローカルファイルとの SHA-256 ハッシュ値の一致確認を行います。いずれかが失敗した場合は、古い世代のアセットは維持され、デプロイせず Workflow は失敗します。
- **5世代保持と Prune**:
  検証完了後に、古いアセットは直近5世代（5アセット）を残して自動的に削除（Prune）されます。
- **First-run（初回実行）のAtomic DB初期化**:
  DBが存在しない場合（初回実行など）、ターゲットのDBファイルを直接削除・作成するのではなく、ターゲットと同じディレクトリに Temporary DB (`.tmp` 拡張子) を作成し、そこで Alembic migrations、整合性チェック（PRAGMA integrity_check）、必須テーブルの存在確認を行います。合格後、コネクションを閉じて `fsync` を行ったのち、`os.replace` を用いてアトミックに配置します。途中失敗時は既存ターゲットを一切変更せず、Temporary DBを削除して `STATE_RESTORE_FAILED` で安全に終了します。
- **※ 運用上の注意**:
  `glintory-state` タグ・リリースは自動管理されています。**手動で削除したり、不変リリース保護（immutable release protection）を有効にしたりしないでください。**

### 必要な環境変数と GitHub Actions 設定

GitHub Actions を有効化し、運用を開始するには以下の設定が必要です。

#### 1. Repository Variables (リポジトリ変数)
GitHub リポジトリの `Settings -> Secrets and variables -> Actions -> Variables` で以下を設定します。
- `GLINTORY_PUBLIC_SITE_URL`: GitHub Pages でホストする公開サイトの絶対 URL（例: `https://<username>.github.io/glintory`）。これは公開サイトの完全な Base URL であり、Sitemap 生成時のパス生成と直接連結されます。内部処理で `base_path`（`/glintory`）が二重に連結されることはありません。
- `GLINTORY_REPOSITORY_URL`: 公開静的サイトのフッターからリンクする、リポジトリの絶対HTTPS URL。
- `GLINTORY_PIXAPPS_URL` (任意): PixApps 連携用の URL。設定されている場合、公開サイトに統合されて表示されます。

#### 2. Workflow Permissions (パーミッション)
ワークフロー実行時、GitHub API にアクセスするために必要なパーミッション（`contents: write`, `pages: write`, `id-token: write`）が割り当てられていることを確認してください。

#### 3. 初回実行 (Manual Trigger)
新しいリポジトリで運用を開始する際は、まず Actions タブから `Glintory Scheduled Automation` ワークフローを `workflow_dispatch` で手動実行してください。これにより初回リリースと初期状態 DB が自動生成されます。

### Failure / Recovery 通知 Issue

定時ジョブが失敗した場合は、自動的に `[Glintory Automation] Failure` という件名の Issue が起票され、`automation-failure` ラベルが付与されます。

- **通知およびクローズのポリシー**:
  * **FAILED (Failed 1件以上、またはインフラエラー、リースの紛失等)**: exit_code 4 などを返し、GitHub Issue を起票または既存の失敗 Issue にコメントを追加します。
  * **PARTIAL (Partial 1件以上かつ Failed 0件)**: exit_code 3 となり、新しい State は保存・公開（Static Build & Upload）されますが、新規の Failure Issue は作成せず、既存の Failure Issue を Close もしません。また、Actions Summary には Warning が表示されます。
  * **SUCCESS (正常終了)**: すべての収集が成功した場合、既存の Failure Issue が存在すれば復旧コメントが追記され、自動的にクローズされます。
- **安全性**:
  セキュリティ監査上の理由から、**データベース接続 URL やシークレット、例外スタックトレースなどの生文字列は露出されません。**

### 完全な Actions Summary

ワークフローの実行完了後、`notify` ジョブによって以下の項目を含む完全な詳細レポートが Actions Summary に生成されます。
* Run ID / Run Attempt
* 開始 / 終了時刻 (UTC)
* 展開した前回アセット名 / ID
* 収集ステータス (Collection Status)
* 収集件数 (Due, Succeeded, Partial, Failed の各カウント)
* 新規アップロードアセット名 / ID
* Prune 結果 (削除数、ステータス)
* データベースサイズ、および各種統計情報 (ソース数、シグナル数、機会数)
* Pages デプロイ結果 / デプロイ先 URL

※ セキュリティ上の制約から、この Summary や JSON/Markdown レポートには **Error Summary、Source Config、Exception Message、Token、DB URL、HTTP Response** などの機密情報や詳細なエラーは一切含めません。

### Public State の安全監査と容量制限

- **Public Safety Audit (安全監査)**:
  SQLite データベースに機密情報（APIキー、トークン）やプライベートなレビュー情報（`notes`、`decisions`、`opportunity_signals` のレビューメモ）が残っていないか監査されます。
  - 環境変数 `GLINTORY_GITHUB_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN` に設定されている実際の秘密値が含まれていないかもチェックされます。
  - 監査に引っかかった場合や不正な JSON 設定が含まれていた場合は、処理を即時中断（fail-closed）して外部へ公開・アップロードしません。
- **容量制限**:
  - 圧縮アーカイブ最大サイズ: 10MB
  - 展開後データベース最大サイズ: 50MB
  - メタデータ/マニフェスト最大サイズ: 1MB
  - 重複ファイル名や、シンボリックリンク/ソケットなどの非レギュラーファイルはセキュリティ保護のため一切拒否されます。

### 公開用検索クエリの目的と対象
`public-sources.json` に設定されている `topic:self-hosted` や `"too expensive"` 等の検索は、ユーザーが SaaS 移行や代替製品の開発機会（Opportunity）を正確に収集・発見できるよう、特定の目的を持って構成されています。

### 停止条件と運用上の注意
GitHub Scheduled Workflow は、リポジトリに 60 日間以上アクティビティ（コミットなど）がない場合、GitHub の仕様により自動的に無効化（停止）されます。そのため、長期にわたり自動収集を継続するには、定期的なリポジトリへのコミットや手動でのワークフローの再有効化が必要です。また、深刻な API エラーや認証エラーが発生した場合、Workflow は fail-closed（即座に終了コード非ゼロで失敗）として停止し、管理者に Issue を通じて障害が通知されます。


---

## GitHub Collector

Glintory に実装された `GitHubCollector` は、GitHub REST API から公開リポジトリおよび公開 Issue 情報を収集します。

### 特徴
- 公開リポジトリ情報の検索・収集
- 公開 Issue 情報の検索・収集
- 収集した情報は正規化された `RawItem` 形式で返されます。

### 設定方法と認証

GitHub API はトークンなしでも公開情報を取得可能ですが、トークンなしの場合はレートリミットが低くなります（1時間あたり60リクエスト）。

* **GitHub Actions上で実行する場合**: 手動でPAT（Personal Access Token）を発行・設定する必要は**ありません**。ワークフローファイル（`glintory-automation.yml`）内で、実行時に自動生成される一時的な `GITHUB_TOKEN` が自動的に `GLINTORY_GITHUB_TOKEN` として渡されるため、安全かつ追加設定なしで動作します。
* **ローカル環境で実行する場合**: 開発やテストで頻繁に収集を実行するとすぐにレートリミットに達するため、必要に応じて個人の PAT を作成し、`.env` ファイルに設定することを推奨します。

#### 1. 環境変数の設定 (ローカル開発時のみ推奨)

`.env` ファイルに以下を追加します。

```env
# オプション。ローカル実行時のレートリミット緩和用
GLINTORY_GITHUB_TOKEN=your_personal_access_token
```


また、テスト等で API の宛先を差し替える場合は以下の環境変数も使用できます。

```env
GLINTORY_GITHUB_API_URL=https://api.github.com
GLINTORY_GITHUB_API_VERSION=2026-03-10
GLINTORY_GITHUB_EXCERPT_MAX_CHARS=2000
```

> [!WARNING]
> セキュリティのため、GitHub Token を Source の `config` JSON 内に直接含めないでください。トークンは必ず環境変数（`GLINTORY_GITHUB_TOKEN`）から読み込まれる設計になっています。

#### 2. Source Config の JSON 例

以下は、`GitHubCollector` を利用するための Source 設定の記述例です。
検索クエリには標準の GitHub Search Query 構文を利用できます。

```json
{
  "repository_queries": [
    {
      "query": "topic:self-hosted pushed:>2026-04-01",
      "sort": "updated",
      "order": "desc",
      "max_items": 20
    }
  ],
  "issue_queries": [
    {
      "query": "\"too expensive\" is:issue created:>2026-04-01",
      "sort": "created",
      "order": "desc",
      "max_items": 20
    }
  ],
  "per_page": 50,
  "max_pages_per_query": 2,
  "include_forks": false,
  "include_archived": false
}
```

> [!NOTE]
> クエリ内の日付条件（例: `pushed:>2026-04-01`）は、実際に利用するタイミングに合わせて適宜調整してください。

### 手動検証（スモークテスト）の実行

実際に外部の GitHub API へ疎通確認を行う場合、以下のスクリプトを明示的に実行できます（CI や通常テストでは実行されません）。

```bash
# コレクター単体の疎通テスト
GLINTORY_GITHUB_TOKEN=your_token uv run python scripts/smoke_github_collector.py

# 永続化と重複チェックを含めた一連の流れの検証テスト
GLINTORY_GITHUB_TOKEN=your_token uv run python scripts/smoke_github_persistence.py
```

## Hacker News Collector

Glintory に実装された `HackerNewsCollector` は、Hacker News 公式 Firebase API から公開情報を収集します。

### 特徴
- **公式 Firebase API の使用**: 指定されたフィード（Ask HN、Show HN、Top Stories、New Stories、Best Stories、Job Stories）から安全に項目を取得します。
- **HTML プレーンテキスト化**: HTML タグの除去、エンティティのデコード、および改行/空白の正規化を行い、プレーンテキストとして取得します。
- **HN Discussion URL を Canonical URL として保存**: `https://news.ycombinator.com/item?id=<id>` をシグナルの Canonical URL に採用し、外部記事 URL は `metadata.outbound_url` に格納します。これにより HN 上の議論とシグナルを紐付けます。
- **Job Stories の任意収集**: `include_jobs` 設定により、Job の収集の有無を制御できます。
- **決定論的な処理**: 外部 AI は使用せず、すべて決定論的ルールに基づいて正規化と分類を行います。
- **重複チェック**: 同一の HN Item は再実行時に Duplicate として処理されます。GitHub など他のコレクターとは別 Source として保存（隔離）されます。
- **コメントツリーの未収集**: 現段階では、コメントツリーの再帰収集は行いません。

### 設定方法

`.env` に以下を追加して HN API や最大文字数を設定できます。

```env
# Hacker News コレクター設定
GLINTORY_HN_API_URL=https://hacker-news.firebaseio.com/v0
GLINTORY_HN_WEB_ITEM_URL_TEMPLATE=https://news.ycombinator.com/item?id={item_id}
GLINTORY_HN_TEXT_MAX_CHARS=5000
```

#### Source Config の JSON 例

以下は、`HackerNewsCollector` を利用するための Source 設定 of 記述例です。

```json
{
  "feeds": [
    "ask",
    "show",
    "new"
  ],
  "max_items_per_feed": 25,
  "include_jobs": false,
  "include_dead": false,
  "include_deleted": false,
  "minimum_score": 2,
  "lookback_days": 90
}
```

### 手動検証（スモークテスト）の実行

実際に外部の Hacker News API へ疎通確認を行う場合、以下のスクリプトを明示的に実行できます（CI や通常テストでは実行されません）。

```bash
# 永続化と重複チェックを含めた一連の流れの検証テスト（外部通信が発生します）
uv run python scripts/smoke_hackernews_persistence.py
```

## RSS / Atom Collector

Glintory に実装された `RSSCollector` は、RSS 2.0、RSS 1.0、および Atom 1.0 フィードをパースし、シグナルとしてSQLiteデータベースへ永続化します。
標準のパブリックRSSソースとして、独立した技術コミュニティ「Lobsters」（`https://lobste.rs/rss`）が登録されています。このフィードは、Hacker NewsやGitHubとは完全に独立しており、開発者が自ら作成したプロジェクト、ツールの代替手段、および個人開発における技術的・運用的な課題について深い議論が行われているため、個人開発の需要や課題（シグナル）を収集するのに適しています。


### 特徴
- **セキュアな HTTP クライアント経由の取得**: `feedparser` 自身には外部通信を行わせず、Glintory共通の HTTP クライアントで安全に取得した bytes をパースします。
- **SSRF（Server Side Request Forgery）対策**: HTTP クライアントは、localhost やプライベートIPアドレスの宛先、credentials を含む URL、および不正なスキームを排除します。リダイレクト発生時にもリダイレクト先を都度検証します。
- **部分成功 (Graceful Fallback)**:
  - XML パースエラー（Bozo例外）が発生した場合、非厳密モード（デフォルト）では、抽出できた有効なエントリーのみを部分成功（`PARTIAL`）として保存します。
  - 一部エントリーの処理（タイトル欠損、不正 URL など）が失敗しても、そのエントリーのみをスキップし、他の正常なエントリーはシグナルとして保存されます。
- **HTML の安全なテキスト化**: エントリーのタイトルおよび本文内の HTML タグは安全にプレーンテキストへ変換されます。
- **メタデータホワイトリスト**: 不要なデータがデータベースに混入しないよう、あらかじめ定義された特定のメタデータキー（`feed_format`, `entry_id`, `entry_tags`, `default_tags`, `default_categories` など）のみをホワイトリストでフィルタして保存します。
- **マルチソース隔離**: GitHub や Hacker News 等と同様、他の Source との URL 重複衝突を防ぐため、Source ごとに独立してシグナルが管理・隔離されます。
- **ルックバック・フィルタと最大スキャン数**: `lookback_days` による期間フィルタ、および `max_entries_to_scan` によるパース対象数の制限をサポートします。

### 設定方法

#### Source Config の JSON 例

以下は、`RSSCollector` を利用するための Source 設定の記述例です。

```json
{
  "feed_url": "https://example.com/feed.xml",
  "max_items": 20,
  "max_entries_to_scan": 100,
  "lookback_days": 90,
  "include_undated": true,
  "signal_type": "trend",
  "use_content_fallback": true,
  "strict_parsing": false,
  "default_categories": ["rss"],
  "default_tags": ["tech"]
}
```

#### 設定パラメータの説明

- `feed_url` (必須): 収集対象の RSS/Atom フィードの URL。
- `max_items`: 今回のランで最大何件の Signal を取り込むか（デフォルト 100）。
- `max_entries_to_scan`: フィード内のエントリーを先頭から最大何件パースするか（デフォルト 100）。
- `lookback_days`: 過去何日以内のエントリーを対象とするか。これより古いエントリーはスキップされます。指定しない場合はフィルタしません。
- `include_undated`: 投稿日時（`published` / `updated`）が取得できないエントリーを含めるか（デフォルト `true`）。
- `signal_type`: RSS エントリーの `SignalType` （`trend`, `request`, `pain`, `launch`, `job_demand` のいずれか、デフォルト `trend`。`manual` は指定不可）。
- `use_content_fallback`: `summary`（要約）が空の場合、`content`（本文）の値を fallback として使用するか（デフォルト `true`）。
- `strict_parsing`: `bozo`（パースエラー等）検出時に、全体を `FAILED` とするか（デフォルト `false`）。
- `default_categories`: シグナルに自動的に割り当てる追加カテゴリリスト（例: `["rss"]`）。
- `default_tags`: シグナルに自動的に割り当てる追加タグリスト（例: `["tech"]`）。

### 手動検証（スモークテスト）の実行

実際に外部の RSS / Atom フィード URL へ疎通確認を行う場合、以下のスクリプトを実行できます（一時データベースが自動作成され、テスト後にクリーンアップされます）。

```bash
uv run python scripts/smoke_rss_persistence.py --feed-url https://hnrss.org/frontpage
```

## Signal Normalization & Persistence

Glintory は、収集した `RawItem` を決定論的なルールに基づいて正規化し、SQLite データベースへ永続化します。

### 特徴
- **決定論的処理**: 外部 AI は使用せず、テキストや URL の正規化、Signal Type 分類、Freshness Score 算出はすべて決定論的ルールに基づいて行われます。
- **一意性と重複排除**:
  - 各 Signal は同一 `Source` 内で一意な `canonical_url` を持ちます（データベース上で `uq_signals_source_canonical_url` として一意制約が張られています）。異なる Source 間であれば同一 URL の登録が許可されます。
  - 重複排除は `source_id + external_id` が最優先され、次に `source_id + canonical_url` の順でマッチングが行われます。
- **インジェスト状態の判定 (Idempotency)**:
  - **Inserted**: 新規シグナルの保存。
  - **Updated**: 既存シグナルと比べ、意味のある内容（タイトル、要約、コンテンツハッシュなど）に変更がある場合のみ更新。
  - **Duplicate**: 意味のある変更がない重複データの場合。`collected_at` と `collection_run_id` のみが更新され、`updated_at` やその他のフィールドは変更されません。
- **統計情報の更新**: `CollectionRun` の `inserted_count`, `updated_count`, `duplicate_count`, `warning_count`, `error_count` が正しく集計・更新されます。

### Signal Type 分類ルール

収集した `RawItem` は、以下の決定論的ルールに基づいて `SignalType` に分類されます。

- **GitHub Repository**: `SignalType.PROJECT` へ変換されます。
- **GitHub Issue**: label およびタイトル・本文の課題表現から、以下の優先順位で分類されます。
  1. `bug` / `regression` / `broken` / `defect` のいずれかのラベルが含まれる場合 → `SignalType.COMPLAINT`
  2. `feature` / `feature request` / `enhancement` / `proposal` / `request` のいずれかのラベルが含まれる場合 → `SignalType.REQUEST`
  3. タイトルまたは要約に明示的な課題表現（pain phrases）が含まれる場合 → `SignalType.PAIN`
  4. 上記のいずれにも該当しない場合 → `SignalType.REQUEST`
- **Hacker News**: アイテムタイプに応じて以下の通りに分類されます。
  - `hn_ask`: タイトルまたは要約に明示的な課題表現（pain phrases）が含まれる場合 → `SignalType.PAIN`、それ以外 → `SignalType.REQUEST`
  - `hn_show`: `SignalType.LAUNCH` へ変換されます。
  - `hn_story`: `SignalType.TREND` へ変換されます。
  - `hn_job`: `SignalType.JOB_DEMAND` へ変換されます。
- **対象外**: `pull_request`、`discussion`、および未知の `item_type` は正規化対象外とし、`unsupported_item_type` エラーとなります。URL構造から型を推測する処理は行いません。

### Migration の実行

データベーススキーマのアップグレードおよびダウングレードは、以下のコマンドで行います。

```bash
# スキーマを最新へアップグレード
uv run alembic upgrade head

# 1つ前のリビジョンへロールバック
uv run alembic downgrade -1

# スキーマの整合性チェック
uv run alembic check
```

## Command Line Interface (CLI)

Glintoryのコマンドラインツール（CLI）を用いて、ソースの管理およびシグナルの収集を実行できます。

### 初期化
データベースマイグレーションは自動実行されません。初回起動時またはスキーマ更新時は手動でマイグレーションを適用してください。

```bash
uv run alembic upgrade head
```

### コマンド一覧

#### 1. Sourceの追加 (Add)
設定ファイル（JSON）を用いて新しい収集ソースを登録します。

```bash
uv run glintory source add \
  --name hn-main \
  --type hackernews \
  --config config/hackernews-source.example.json
```

オプションとして `--disabled` を指定すると無効状態で作成され、`--json` を指定すると結果がJSON形式で出力されます。
> [!IMPORTANT]
> セキュリティのため、GitHub Token などの認証情報は Source config JSON 内に保存せず、環境変数から読み込むようにしてください。また、RSS URL の query などのパラメータにも秘密情報を含めないでください。

#### 2. Sourceの一覧 (List)
登録されているSourceの一覧を表示します。

```bash
uv run glintory source list
```

オプションとして `--enabled-only` や `--json` が指定可能です。

#### 3. Sourceの表示 (Show)
Sourceの詳細な設定と要約を表示します。

```bash
uv run glintory source show hn-main
```

#### 4. Sourceの更新 (Update)
登録済みのSourceのconfigを更新します。`source_type` や `name` の変更はできません。

```bash
uv run glintory source update hn-main \
  --config config/hackernews-source.example.json
```

#### 5. Sourceの有効化 / 無効化 (Enable / Disable)
Sourceの有効/無効状態を切り替えます。

```bash
uv run glintory source enable hn-main
uv run glintory source disable hn-main
```

> [!NOTE]
> Sourceを無効化（disable）しても、過去に収集したSignalやCollectionRunはデータベースから削除されません。

#### 6. 収集実行 (Collect)
指定したSourceまたはすべての有効なSourceから、シグナルの収集を実行します。

```bash
# 単一のSourceを指定して実行
uv run glintory collect --source hn-main

# すべての有効なSourceから順次実行
uv run glintory collect --all
```

オプション：
- `--max-items <int>`: 最大収集件数（1〜1000）。
- `--json`: 実行結果をJSONで出力します。

> [!IMPORTANT]
> `--all` の場合、Sourceは名前（name）の昇順で並列ではなく「逐次実行」されます。あるSourceの実行が失敗しても、以降のSourceの実行は継続されます。

### Dockerでの利用方法

Dockerコンテナ内でもCLIコマンドを利用可能です。

```bash
# マイグレーションの実行
docker compose run --rm app alembic upgrade head

# 登録済みソース一覧の取得
docker compose run --rm app glintory source list

# すべての有効なソースから収集実行
docker compose run --rm app glintory collect --all

# ボリューム上の設定ファイルを利用してソースを追加する例
docker compose run --rm \
  -v "$(pwd)/config:/config:ro" \
  app \
  glintory source add \
    --name hn-main \
    --type hackernews \
    --config /config/hackernews-source.example.json
```

## Web Interface & FTS5 Search

SQLite の FTS5 全文検索エンジンを活用し、収集した Signal の閲覧・検索・絞り込みをブラウザおよび JSON API から行うことができます。

### ローカルでの起動方法

```bash
# データベースの FTS5 マイグレーション（必須）
uv run alembic upgrade head

# サーバーの起動
uv run uvicorn glintory.main:app --reload
```

* UI 画面: `http://localhost:8000/signals`
* Today ダッシュボード: `http://localhost:8000/`

### 検索機能

#### 検索対象
* `title` (タイトル) - BM25重み: 8.0
* `excerpt` (要約) - BM25重み: 2.0
* `author` (著者) - BM25重み: 1.0

#### 絞り込み条件 (Filters)
* **Source**: データソースごと
* **Signal Type**: シグナルの種類
* **Published Date**: 公開日の範囲（From / To）

#### 制限事項・仕様
* ユーザー入力は安全にサニタイズ（ダブルクォートでエスケープされ、複数単語は `AND` で結合）されます。
* Raw FTS5 構文は利用できません。
* `OR` / `NOT` / `NEAR` やワイルドカード `*` などの演算子検索は未対応です。
* `tags` および `categories` は全文検索の対象外です（フィルターリングのみ）。
* 日本語の形態素解析（Mecab等のトークナイザー）は導入していません。
* 収集処理（Collect）は Web UI から実行できません（CLI から実行してください）。
* 外部 AI は使用せず、ローカルの SQLite データベース内のみで検索処理を完結させています。


## Opportunity Analysis & Deterministic Scoring

Glintory は、収集した Signal を類似度に基づき決定論的にクラスタリングし、Opportunity（開発・事業の機会候補）を抽出した上で、公開情報に基づく客観的なルール（バージョン管理された決定論的アルゴリズム）でスコアリング（評価）します。

### CLI コマンド

#### 1. 候補の分析・抽出 (Analyze)
未紐付けの Signal を TF-IDF とコサイン類似度を用いて自動的にクラスタリングし、同一課題ごとに Opportunity をマージまたは新規作成します。

```bash
uv run glintory analyze
```
オプション：
- `--dry-run`: データベースに書き込まず、分析結果のみをシミュレート表示します。
- `--cluster-version <str>`: アルゴリズムのバージョン（デフォルト: `v1`）。
- `--json`: 結果を JSON フォーマットで標準出力します。

#### 2. スコアリングの実行 (Score)
抽出された Opportunity に対し、定義されたスコア算出ルール（V1）を用いてスコアの算出と評価を行います。

```bash
uv run glintory score
```
オプション：
- `--opportunity <uuid>`: 指定した単一の Opportunity のみスコアを計算・更新します。
- `--as-of <YYYY-MM-DD>`: 基準日を明示的に指定して実行します（過去データ評価用）。
- `--max-opportunities <int>`: 最大処理件数（1〜10000）。
- `--dry-run`: データベースを更新せず、スコア計算結果のみを表示します。
- `--json`: スコア結果を JSON 形式で標準出力します。

#### スコア評価システム (V1 Rules)
- **Evidence Score (0〜50点)**: 証拠ボリューム、起原の多様性、カテゴリ網羅性（Demand / Build / Market）、新鮮さ、関連度の加重平均の合計。
- **Feasibility Score (0〜50点)**: 実装前例の多さ、明確な需要、技術的記述の具体性、検証チャネル数などの合計。
- **Penalty Score (-30〜0点)**: 対立する証拠、起原の過度な集中、陳腐化、競合飽和度に基づく減点。
- **Total Score (0〜100点)**: Evidence + Feasibility + Penalty。
- **Confidence (HIGH / MEDIUM / LOW)**: 証拠数と多様性の閾値クリア度で判定する客観的な信頼度。

### Web UI & JSON API

ブラウザおよび API から Opportunity の情報を確認できます。

* **一覧画面 (`/opportunities`)**: 候補を Total Score の高い順（同一の場合は Evidence Score、Confidence 順など）で一覧表示します。各種フィルター（Status, Confidence, Generation, Min Score）に対応しています。
* **詳細画面 (`/opportunities/{id}`)**: 各候補のステータス、計算された各スコアの内訳（Explanation）、根拠となった Evidence シグナルの一覧、および履歴となる過去のスコア推移をグラフィカルに確認できます。
* **JSON API**: 
  - `GET /api/v1/opportunities`: 候補一覧の JSON 取得
  - `GET /api/v1/opportunities/{id}`: 候補詳細、スコア内訳（Raw Explanation JSON含む）、スコア履歴の JSON 取得
* **Today 画面**:
  - ダッシュボードの `Top Opportunities` に、計算された実データの上位3件がスコア順で自動的にレンダリングされます。


## Opportunity Review Workflow

人間が抽出された開発機会（Opportunity）をレビューし、意思決定と証拠の手動調整を行うためのワークフローです。

### 主な機能

1. **ステータス遷移と決定履歴 (Status Transition & Decision Log)**
   - `inbox` から `watch` (Watchlist), `validate`, `rejected`, `archived` など、定義された状態遷移ルールのみを実行できます。
   - `rejected`, `archived` への遷移、またはこれらからの復元（reopen）の際は、3文字以上の理由（Reason）入力が必須です。
   - ステータス遷移時には自動的に決定履歴（Decision Log）が保存され、詳細画面からタイムラインで確認できます。

2. **レビューノート (Review Notes)**
   - Opportunity に対して、任意のメモやレビュー内容をノートとして追加・編集・削除できます。
   - ノートは1件最大500文字のサイズ制限が適用されます。

3. **エビデンス（証拠）の調整と除外・復元 (Evidence Management)**
   - 各証拠シグナルとの関係性（Relation Type: `supporting`, `related`, `contradicting`）や適合度（Relevance Score: 0.0〜1.0）を手動で更新できます（`contradicting` への変更時は3文字以上のレビューノート記述が必須）。
   - 不適切な証拠は削除せず、`Excluded`（除外状態）としてデータベースに保持できます。除外された証拠はクラスタリングやスコアリングの対象から自動で外されます。
   - 除外された証拠は、いつでも `Restore`（復元）してアクティブな証拠に戻せます。

4. **手動エビデンス検索・追加 (Evidence Search & Manual Link)**
   - FTS5 全文検索エンジンを用いて蓄積された Signal から任意のシグナルを検索し、手動で Opportunity のエビデンスとして紐付けることができます（`/opportunities/{id}/evidence/search`）。
   - 同じシグナルが他の Opportunity に紐付いている場合は、件数として表示されます。

5. **スコアの鮮度管理 (Score Staleness)**
   - 人手によるエビデンスの追加・更新・除外・復元が発生した際、Opportunity のスコアが古くなった（`score_is_stale = true`）ことが検知され、詳細画面や一覧で警告が表示されます。
   - スコアが古くなった Opportunity は、Today ダッシュボードの `Top Opportunities` (Top 3) から自動的に除外されます。
   - スコアの再計算（CLI: `uv run glintory score`）を実行することで、警告は消え、最新のスコアで再び Top 3 に入るようになります。

6. **監査ログ (Audit Trail)**
   - 人間によるすべての書き込み操作（ステータス変更、ノート追加・編集・削除、エビデンス追加・更新・除外・復元）は、監査証跡として一貫したフォーマットで標準ログ（`logger.info`）へ出力されます。

7. **セキュリティと保護 (CSRF & Form Limits)**
   - すべての Web 書き込み操作は、Origin / Referer および Cookie トークンを用いた暗号学的に安全な CSRF 検証が行われます。
   - 悪意ある大量データの送信を防ぐため、フォーム送信ボディサイズは最大 50KB に制限されています。

## Local LLM Opportunity Enrichment (AIによるコンテキスト拡張)

Glintory は、収集・クラスタリング・スコアリングされた Opportunity に対して、外部の有償 AI API に依存せず、GitHub Actions の実行環境上でローカル LLM (llama-server) を一時起動してコンテキストを拡張する (Enrichment) 機能を備えています。

### 主な特徴と制約

- **決定論的処理の完全な保護 (Deterministic Isolation)**:
  LLM Enrichment は、Opportunity のスコア (Deterministic Score)、クラスタリング、エビデンス紐付けなどの正本ではありません。LLM の出力によって、これらの決定論的処理結果を変更することは絶対にありません。
- **外部 API 非依存 (External-API Free)**:
  `llama-server` を 127.0.0.1 のローカルポートにバインドして一時的に起動し、完全にローカル環境内で推論を完結させます。外部へのAPIキー露出や従量課金の懸念はありません。
- **インプットハッシュによる重複防止と Stale 検知**:
  Opportunity、関連する証拠シグナル、モデルID、プロンプトバージョン、スキーマバージョンなどを結合した SHA-256 ハッシュ（Input Hash）を計算し、前回の推論時と同一であれば処理を自動でスキップ（キャッシュ再利用）します。エビデンスの変更やスコア再計算が行われた場合は、自動的に Stale（不整合）を検知して次回のワークフローで再推論が実行されます。
- **厳格なバリデーション保護 (Sanitization & Validation)**:
  LLM の出力は指定された JSON Schema に厳密に適合しているか検証されます。未知のキーの混入や、HTML/Scriptタグ、外部URL等の不正な文字列の混入を検知した場合は出力を拒否し、警告ログを記録した上でフォールバックします。
- **静的サイト表示とフォールバック**:
  生成された AI 分析結果（AI タイトル、概要、課題、対象ユーザー、Why Now、証拠合成、構築の方向性、リスク、タグ）は、公開サイトの詳細ページに AI 生成物であることを明記した上で表示されます。データが存在しない場合は、従来の決定論的タイトルおよび概要に安全にフォールバックします。
  ※ 生プロンプト、生出力、推論思考プロセス（Chain of Thought）は、セキュリティおよび容量保護の観点からデータベースおよび静的ファイルには保存・公開されません。

### 実行方法

#### CLI からの実行
```bash
# 未処理または更新されたすべての Opportunity を処理 (最大10件)
uv run glintory enrich run

# 特定の Opportunity を指定して強制再推論
uv run glintory enrich run --opportunity <UUID> --force

# JSON フォーマットで結果を表示
uv run glintory enrich run --json
```

#### 設定項目 (環境変数)
> [!IMPORTANT]
> `GLINTORY_LOCAL_LLM_ENABLED` が `true` の場合、以下の必須と記載されているすべての設定が正しく設定されている必要があります。

- `GLINTORY_LOCAL_LLM_ENABLED`: ローカルLLMを有効化するかどうか (`true` / `false`)
- `GLINTORY_LOCAL_LLM_MODEL_REPO`: モデルのダウンロード元 Hugging Face リポジトリ (デフォルト: `Qwen/Qwen3-1.7B-GGUF`)
- `GLINTORY_LOCAL_LLM_MODEL_FILE`: モデルの GGUF ファイル名 (デフォルト: `Qwen3-1.7B-Q8_0.gguf`)
- `GLINTORY_LOCAL_LLM_MODEL_PATH`: モデル GGUF ファイルのローカルパス (有効化時は必須)
- `GLINTORY_LOCAL_LLM_MODEL_REVISION`: Hugging Face のコミットハッシュ等によるピン留め指定 (有効化時は必須)
- `GLINTORY_LOCAL_LLM_MODEL_SHA256`: モデル GGUF ファイルの SHA-256 期待値 (有効化時は必須、64桁の16進数)
- `GLINTORY_LOCAL_LLM_BINARY_PATH`: `llama-server` バイナリの実行パス (デフォルト: `./bin/llama-server`、有効化時は必須)
- `GLINTORY_LOCAL_LLM_BINARY_SHA256`: `llama-server` バイナリの SHA-256 期待値 (有効化時は必須、64桁の16進数)
- `GLINTORY_LOCAL_LLM_RUNTIME_VERSION`: `llama-server` の期待するバージョン (有効化時は必須)
- `GLINTORY_LOCAL_LLM_RUNTIME_COMMIT`: `llama-server` の期待する Git コミットハッシュ (有効化時は必須、40桁の16進数)
- `GLINTORY_LOCAL_LLM_PORT`: 推論サーバーのバインドポート (デフォルト: `8088`)
- `GLINTORY_LOCAL_LLM_MAX_INPUT_CHARS`: 推論に入力される最大文字数 (デフォルト: `12000`)
- `GLINTORY_LOCAL_LLM_MAX_OPPORTUNITIES`: 1回のワークフローで処理する最大 Opportunity 数 (デフォルト: `10`)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

