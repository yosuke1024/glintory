# Glintory

Find the signals worth building on.

## GitHub Collector

Glintory に実装された `GitHubCollector` は、GitHub REST API から公開リポジトリおよび公開 Issue 情報を収集します。

### 特徴
- 公開リポジトリ情報の検索・収集
- 公開 Issue 情報の検索・収集
- 収集した情報は正規化された `RawItem` 形式で返されます（現段階では Signal DB への保存、Opportunity の生成、Today 画面への表示は行いません。コレクター単体の実装フェーズです）。

### 設定方法と認証

GitHub API はトークンなしでも公開情報を取得可能ですが、トークンなしの場合はレートリミットが低くなります。大量のデータを取得する場合やエラーを避けるために、GitHub Personal Access Token の設定を推奨します。

#### 1. 環境変数の設定

`.env` ファイルに以下を追加します。

```env
# オプション。レートリミットを緩和するためのGitHubトークン
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

以下は、`HackerNewsCollector` を利用するための Source 設定の記述例です。

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

> [!NOTE]
> 現段階では、正規化・永続化した Signal を UI（Today 画面など）へ表示する処理、FTS5、Opportunity 生成、およびスコアリング処理は実装されていません。
