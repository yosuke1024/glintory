# Public Data Contract v1

Glintoryが蓄積・選別したOpportunity（案件候補）データを、JuryPressをはじめとする外部システムから安全かつ安定して参照できるようにするためのパブリックデータ仕様です。

## 概要

Glintoryは収集したシグナルをクラスタリング・スコアリングし、最終的に静的サイトとしてパブリッシュします。
その際、人間用のHTMLページと並行して、システム連携用の機械可読なJSONフィードおよび各種メタデータファイルを `/data/v1/` 配下に出力します。

## ディレクトリ構造

静的サイトのビルド先ディレクトリ（例：`dist/`）の配下に、以下の構成で出力されます。

```text
dist/
├── data/
│   └── v1/
│       ├── manifest.json
│       ├── opportunities.json
│       ├── opportunities/
│       │   ├── opp_de6838e1642c49cd9f089893eed60aa3.json
│       │   └── ...
│       ├── feeds/
│       │   └── jurypress.json
│       └── schemas/
│           ├── manifest.schema.json
│           ├── opportunity-list.schema.json
│           ├── opportunity-detail.schema.json
│           └── jurypress-feed.schema.json
```

## 各データファイルの仕様

### 1. マニフェスト (`manifest.json`)
データセット全体のリビジョンと、各フィードへのエンドポイントURI、データセット全体のコンテンツハッシュが含まれます。

- `dataset_revision`: 日時ベースのリビジョン表記 (`YYYYMMDDTHHMMSSZ`)。
- `source_commit`: 現在のGitコミットハッシュ。
- `content_hash`: 公開Opportunity全体の整合性を検証するためのハッシュ値。
  公開案件一覧の `public_id`, `public_revision`, `public_content_hash` を `public_id` の昇順（アルファベット順）でソートし、`public_id:revision:content_hash` の形式で結合した文字列の SHA-256 ハッシュです。

### 2. 案件一覧 (`opportunities.json`)
Glintoryのv2コンテキストで管理されているすべての案件の一覧です（LOW confidenceやゲート却下された案件も含まれます）。
各案件は `detail_url`（詳細JSON）および `html_url`（人間用HTML詳細ページ）への相対パスを含みます。

### 3. 案件詳細 (`opportunities/<public_id>.json`)
各案件の完全なデータモデル（Pydantic v2スキーマに準拠）を詳細に出力したJSONです。
AIによる多言語（日/英）の解説・課題分析、スコアの内訳、ゲートの合否、証拠（Evidence）となったシグナルの詳細、JuryPress配信適格性チェックの結果を含みます。

### 4. JuryPressフィード (`feeds/jurypress.json`)
後述する **JuryPress配信基準** をすべて満たした（`jurypress.ready` が `True` の）案件のみを抽出した軽量なシステム連携用フィードです。

### 5. JSON Schema (`schemas/`)
出力された各JSON形式の整合性を外部システム（JuryPress等）側で自律的に検証できるようにするため、Pydanticモデルから自動生成した標準的な JSON Schema ファイルを出力します。

---

## 安定した公開IDと非破壊的リビルド

JuryPressをはじめとする外部システムから案件を安定的かつ一意に識別するため、各Opportunityには不変な **`public_id`** (`opp_<32桁hex_uuid>`) を割り当てます。

### 非破壊的リビルド (`opportunities rebuild`)
収集シグナルが増加した際に `opportunities rebuild` コマンドを実行し、ゼロベースでクラスタリングを行います。
その際、既存の案件を物理削除せず、以下の評価基準でマッチングを行い、レビュー状態（ステータス等）や `public_id` を非破壊的に引き継ぎます：
- 代表シグナルID（重心）の一致
- クラスタ構成シグナルの重複率（Jaccard係数）
- 最古のシグナルIDの一致

#### クラスタ統合時の Canonical 選択ルール
複数の既存案件が単一の新クラスタにマージされる場合、**最も `created_at` が古い案件を Canonical（正当な継承先）として維持**し、他の案件は削除されます。
削除された案件の `public_id` は `OpportunityPublicAlias` テーブルに記録され、静的サイトビルド時に、旧URLから Canonical 案件の新しいURLへの自動HTTPリダイレクトHTML（`<meta http-equiv="refresh">`）が自動的に生成されます。

### 改訂履歴とハッシュ値 (`public_revision` と `content_hash`)
案件のコアコンテンツ（解説テキストなど）および関連証拠リスト（昇順ソート済）の内容から決定論的に SHA-256 ハッシュ値（`public_content_hash`）を計算します。
- 証拠の追加・削除や、LLM解説の更新によってハッシュ値が変化した場合のみ、`public_revision` を `+1` インクリメントし、`last_published_at` を更新します。
- ハッシュ値に変化がない場合はリビジョンと更新日時は維持されます。
- 計算時、証拠（Evidence）の `excerpt` は最大500文字に制限され、ハッシュ値の決定論的な安定性を担保します。
