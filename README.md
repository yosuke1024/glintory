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
GLINTORY_GITHUB_TOKEN=your_token uv run python scripts/smoke_github_collector.py
```
