# ADR-0001: 実装言語・スタックの選定

ステータス: Phase 0 提案(ユーザー承認で確定)
日付: 2026-08-16

## 課題

Glintory v2 の実装言語を、v1 の惰性ではなく v2 要件から選定する。候補は Python と TypeScript。

## 判断材料

v2 の compute は GitHub Actions のバッチ実行であり、Cloudflare Workers 上では動かない。したがって「Cloudflare ネイティブ環境との親和性」は D1/R2 への API アクセス手段の差でしかない。

| 観点 | Python | TypeScript |
|---|---|---|
| D1 アクセス | REST API(素の HTTP)。十分 | REST API または wrangler CLI。やや便利 |
| R2 アクセス | boto3(S3 互換)。成熟 | aws-sdk-js。成熟 |
| フィード/HTML 解析 | feedparser / lxml / trafilatura 等が成熟 | 相当品はあるが本文抽出の質で見劣り |
| 日本語テキスト処理 | NFKC(stdlib)、形態素解析等の拡張余地 | 可能だが選択肢が薄い |
| ローカル SQLite テスト | stdlib sqlite3 で fixture 決定論テスト | better-sqlite3 で可能 |
| Gemini SDK | google-genai(公式) | @google/genai(公式) |
| 将来 Workers 化 | 弱い | 強い |

## 決定

**Python 3.12+ を採用する。**

- 決め手は「収集・本文抽出・日本語正規化」というパイプラインの中核が Python エコシステムで最も薄く書けること。
- Workers 化の可能性は現時点の要件にない(常駐なし・バッチのみ)。Loka 側がフロントを持つ場合も Pages の静的配信であり Workers を要さない。
- wrangler は使わず、D1 マイグレーションも自前の番号付き SQL + REST API で適用する(ツールチェーンを 1 言語に保つ)。

## 構成

- パッケージ管理: uv / `pyproject.toml`
- Lint/Format: ruff
- テスト: pytest(fixture ベース、外部依存は全て Fake)
- 主要依存(最小限): httpx, feedparser, trafilatura(または readability 系), boto3, google-genai, pydantic(設定と構造化出力の検証)
- 依存追加は都度、脆弱性確認の上で行う

## 影響

- v1 も Python のためコード資産のハーベスト(設計確定後の限定移植)が容易になるが、これは選定理由ではなく副次効果である。
- SQLAlchemy / Alembic / FastAPI 等の v1 依存は採用しない(v2 に Web UI・ORM の要件がない)。
