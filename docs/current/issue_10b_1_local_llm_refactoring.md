# Optional Local LLM Enrichment Refactoring (Issue 10B.1)

本ドキュメントは、Optional Local LLM Opportunity Enrichment 機能の Production 安定化、実行環境整合性の確保、および二言語対応のためのリファクタリング仕様を定義した恒久的な SSOT (Single Source of Truth) です。

---

## 1. 概要
決定論的解析およびスコアリングの結果を受けて実行される Optional Local LLM Enrichment は、GHA (GitHub Actions) 上の限られた実行リソースで動作し、バイナリやモデルの整合性を保証しつつ、日英二言語の Opportunity Brief を安全に生成できるように構成されています。

## 2. 実行時整合性とハッシュ固定
実行時の不整合を避けるため、使用するバイナリおよびモデル、そのハッシュ値は設定デフォルト値として完全に固定され、実行時に毎回検証されます。

### 2.1. アセット定義
- **Llama.cpp Version**: `b3600` (ggml-org/llama.cpp)
- **llama-server 実行バイナリ (Linux x64)**:
  - アーカイブ ZIP ハッシュ (SHA-256): `fa7e7a980d7ffdd152fbd56d6c14f943b4e6f1a5b10d103f2bba3eb0f46eed3a`
  - バイナリハッシュ (SHA-256): `5edcfae5739f313a9d30bf9e59116b53f5240c3e6ffeb9f25be9bb7b8c303222`
- **モデル**: `Qwen/Qwen3-1.7B-GGUF`
  - ファイル名: `Qwen3-1.7B-Q8_0.gguf`
  - モデルハッシュ (SHA-256): `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`
  - リビジョン (HF Commit SHA): `90862c4b9d2787eaed51d12237eafdfe7c5f6077`

### 2.2. アセットのキャッシュとオンザフライ検証
GitHub Actions における `actions/cache@v4` によるキャッシュ再利用時も、毎回 `sha256sum` を用いてアセットの整合性を検証します。検証に失敗した場合は自動的に再ダウンロードを行います。

## 3. バジェット管理アルゴリズム
LLM への入力データの切断による JSON 崩壊を防ぐため、単純な文字列スライス (`user_json_str[:max_chars]`) は禁止されています。

以下のステップに従い、シリアライズ前に厳密な文字数計算を行います：
1. Opportunity ID, Title, Proposed Solution などの基本構造を含んだ最小限の JSON を仮構築し、ベース文字数を計算する。
2. 関連する Evidence を Relevance Score 順にソートする。
3. 各 Evidence の Excerpt を最大 1000 文字に制限した上で、1つずつ Evidence リストに追加して全体の JSON シリアライズ長をテスト計算する。
4. 設定値 `local_llm_max_input_chars` (12000文字) を超える直前で追加を停止する。
5. 構築された正常な JSON 構造体のみを LLM に送信する。

## 4. 二言語対応 Opportunity Brief
1回の LLM 推論リクエストにより、英語 (English) と日本語 (Japanese) の Opportunity Brief を同時に出力させます。

### 4.1. データ構造
生成されたデータは新設された `opportunity_enrichment_localizations` テーブルに、ロケール（`en` / `ja`）ごとに保存されます。

#### `opportunity_enrichment_localizations` テーブル定義:
- `id` (VARCHAR(36), PK): UUID
- `enrichment_id` (VARCHAR(36), FK -> `opportunity_enrichments.id`, ON DELETE CASCADE)
- `locale` (VARCHAR(10)): `en` または `ja` (チェック制約: `locale IN ('en', 'ja')`)
- `generated_title` (VARCHAR(100))
- `generated_summary` (VARCHAR(500))
- `problem_statement` (VARCHAR(500))
- `target_users` (JSON list of strings)
- `why_now` (VARCHAR(500))
- `evidence_synthesis` (VARCHAR(800))
- `build_direction` (VARCHAR(500))
- `risks` (JSON list of strings)
- `tags` (JSON list of strings)

後方互換性のため、親テーブルである `opportunity_enrichments` には英語の Brief 内容がミラーリング保存されます。

### 4.2. 表示とフォールバック
- **英語詳細**: デフォルトの `/opportunities/{id}/index.html` にレンダリング。
- **日本語詳細**: `/opportunities/{id}/ja/index.html` にレンダリング。
- **Route Switcher**:
  - 英語詳細には `View in Japanese (日本語)` へのリンクを配置。
  - 日本語詳細には `View in English` へのリンクを配置。
- **Fallback Rendering**:
  - ローカライズデータが不足またはエラーで生成されなかった場合は、親テーブルのミラーカラムの値を使用します。
  - LLM 自体が未実行の場合は、ルールベースの `Opportunity.title` および `proposed_solution` をフォールバック表示します。

## 5. インプットハッシュ不一致 (Stale) 警告
最新の deterministic 分析やスコアリングで関連証拠データが変更された場合、既存の AI 要約が古くなっていることを示す警告 (Stale Warning) を詳細ページの上部に出力します。

- 警告判定:
  - Opportunity に紐づく現在の Evidence および Score を用いて `calculated_input_hash` を計算。
  - 取得した最新の成功した `OpportunityEnrichment.input_hash` と比較し、不一致であれば Stale 状態とする。
- 表示文面:
  - 英語: `⚠️ This AI summary is based on outdated evidence data. (AI-generated content is stale)`
  - 日本語: `⚠️ このAI要約は古い証拠データに基づいている可能性があります。(AI生成コンテンツは最新ではありません)`

## 6. 手動 Smoke Test ワークフロー
GitHub Actions 上で本物と同じ runtime（`llama-server`）とモデルを用いて、推論が 100% 成功し valid な JSON が生成できることを検証可能にする手動ワークフロー `.github/workflows/local-llm-smoke.yml` を導入しました。
- **Trigger**: `workflow_dispatch`
- **動作**: データベースにダミーデータを挿入し、`uv run glintory enrich run --json` を実行して、出力形式や例外のロギングなどを仮想環境上で実証できます。
