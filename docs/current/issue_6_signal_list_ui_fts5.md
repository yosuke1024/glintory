---
status: implemented
---

# Issue 6: Signal List UI & FTS5 Search

SQLite に蓄積されたシグナルをブラウザおよび JSON API から一覧表示・全文検索・絞り込み可能にする機能の仕様。

## Specification

### 1. データベースレイヤー (Alembic & FTS5)
- SQLite FTS5 を用いた `signals_fts` 仮想テーブル (External Content Table 方式) を使用する。
- `signals` テーブルの変更（INSERT, DELETE, UPDATE）に同期するトリガーを実装する。
- 既存データの Backfill (REBUILD) を行う。
- Alembic の自動生成機能が FTS5 テーブルや自動生成されるシャドウテーブル群を不要なものとして削除検知するのを防ぐため、 `migrations/env.py` 内の `include_object` フックにフィルタを追加する。

### 2. インフラ・クエリサービスレイヤー
- **FTS管理ユーティリティ**: SQLite が FTS5 に対応しているかの検証、インデックス再構築、整合性チェックを行う。
- **安全なクエリパーサー**: ユーザー入力から安全な FTS5 AND 検索クエリを生成する。
  - Unicode (NFC) 正規化、NUL バイト除去、200文字以下、10語以下、1語100文字以下の厳密な制約検査と SQL インジェクション攻撃の無効化処理を行う。
- **検索リポジトリ**: `signals_fts` 仮想テーブルと `signals` テーブルを結合し、BM25 による関連度ソート（タイトル 8.0、抜粋 2.0、著者 1.0）、各種フィルタ（Source, Signal Type, 公開日）、OFFSET/LIMIT ページネーションを行う。

### 3. Web UI & API レイヤー
- **ダッシュボード**: デモデータを廃止し、本物のシグナル統計情報（収集数、最終収集日時、新規追加）を表示するリアルなダッシュボードを実装。
- **シグナル一覧 & 詳細画面**: 全文検索フォーム、サイドバーフィルタ、ページネーションを含む UI を Vanilla CSS で実装。
- **JSON API**: Web UI と同等の検索・絞り込み結果を返す JSON API エンドポイント。日時は UTC ISO 8601 (末尾 `Z` 拡張) に統一。

---

## Implementation Report

### 実施した主な変更
* **[NEW] Migration**: SQLite FTS5 マイグレーション ([da4fadf39e75_signals_fts.py](file:///workspace/glintory/migrations/versions/da4fadf39e75_signals_fts.py)) を追加。
* **[MODIFY] env.py**: Alembic オブジェクトフィルタを `migrations/env.py` に追加。
* **[NEW] FTS管理ユーティリティ**: [fts.py](file:///workspace/glintory/src/glintory/infrastructure/fts.py) を実装。
* **[NEW] 安全なクエリパーサー**: [search_query.py](file:///workspace/glintory/src/glintory/services/search_query.py) を実装.
* **[NEW] 検索リポジトリ**: [signal_search.py](file:///workspace/glintory/src/glintory/infrastructure/signal_search.py) を実装。
* **[MODIFY] ダッシュボード**: [today.py](file:///workspace/glintory/src/glintory/web/routes/today.py) を実装。
* **[NEW] シグナル一覧 & 詳細画面**: [signals.py](file:///workspace/glintory/src/glintory/web/routes/signals.py) および Vanilla CSS を実装。
* **[NEW] JSON API**: [api.py](file:///workspace/glintory/src/glintory/web/routes/api.py) を実装。

### 検証結果
- `uv run pytest -W error` による自動テスト（全220件）がパス。
- Ruff Linter/Formatter チェック、Pyright 型チェックがすべてパス。
