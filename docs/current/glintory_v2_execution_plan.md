# Glintory v2 実行計画: 共通情報基盤への再構築

対象Issue: [pixapps_strategy#22 企画: Glintory共通情報基盤とLoka自律メディア](https://github.com/yosuke1024/pixapps_strategy/issues/22)

作成日: 2026-08-16(改訂1) / 対象期間: 2026-10-下旬 〜 2026-12月末

---

## 0. 設計原則: ゼロベース

**Glintory v1(Opportunity Intelligence)は現在稼働しておらず、保持すべきデータも利用者も存在しない。** したがって本計画は以下を前提とする。

- v2 は **Issue #22 の要件のみを入力として設計する**。v1 の設計・スキーマ・実行順序は設計の入力にしない。
- 互換性・データ移行・稼働停止の調整は一切不要。v1 のコード・ワークフロー・state リリース・旧ドキュメントは Phase 1 でリポジトリから **削除** する(Git 履歴には残るため参照は可能)。
- v1 コードの再利用は「設計確定後のハーベスト(収穫)」としてのみ許可する。手順は必ず次の順とする。
  1. v2 の仕様をゼロベースで確定する
  2. 確定した仕様の各コンポーネントについて、v1 に仕様へ**そのまま**一致する実装・テストがあるか確認する
  3. 一致する場合のみ移植し、一致しない場合は迷わず新規実装する(デフォルトは新規実装)
- 「既存コードがあるから」という理由で仕様を曲げることを明示的に禁止する。逆に、v1 のテスト・fixture 資産は回帰防止のハーベスト候補として積極的に評価してよい(テストは仕様を縛らないため)。

## 1. 目的とスコープ

本計画は、Glintory を「日本の公開情報を収集・正規化し、複数メディアで再利用可能な事実データへ変換する共通情報基盤」としてゼロベースで構築するための実行計画である。

**スコープ内:**

- Glintory v2 のアーキテクチャ・データモデル・パイプラインの新規設計と実装
- Cloudflare D1 / R2 を用いた期限付きデータパイプライン(TTL削除)の実装
- Gemini による事実抽出・品質評価の組み込み
- Loka(最初の編集プロダクト)へ事実データを供給するデータ契約の定義と実装
- サブエージェントを活用した実装体制の定義(§10)

**スコープ外:**

- Loka 本体(記事生成・ペルソナ・サイト)の実装 — 別計画とするが、本計画で供給契約を定義する
- Voygie およびその他メディア(2027年以降の候補)
- 多数の国・テーマへの初期展開
- v1 データの移行(存在意義がないため全て破棄)

## 2. To-Be アーキテクチャ

| 項目 | v2 |
|---|---|
| ドメイン | 日本の有用な公開情報 → 正規化された事実データ(トーン・結論を持たない) |
| 状態管理 | Cloudflare D1(構造化作業データ、expires_at 付き)+ R2(元HTML・生レスポンス等の大きな一時データ) |
| AI | Gemini(事実抽出、選定、品質評価) |
| 公開面 | Glintory 自体は公開サイトを持たない。公開面は Loka 側(Cloudflare Pages) |
| 実行 | GitHub Actions Scheduled Workflow(常駐サーバーなし) |
| 永続保持 | 公開記事の出典メタデータ(URL・公開日・取得日・ハッシュ)のみ Git 管理 |

### 主要決定

1. **D1 を作業データの唯一のストアとする。** D1 は SQLite 互換のため、ローカル・CI のテストは素の SQLite で決定論的に実行できる。GitHub Actions からは Cloudflare REST API(D1 query endpoint)の薄いクライアントでアクセスする。ORM は使わず、リポジトリ層の SQL を D1/SQLite 両対応にする。
2. **R2 は「大きな一時データ」専用。** 元 HTML、API 生レスポンス、画像。キーに収集日を含め(`raw/{source_id}/{yyyy-mm-dd}/{content_hash}.html.gz`)、R2 ライフサイクルルールで 90 日削除を二重に保証する。
3. **期限付きパイプラインを最初から組み込む。** 全作業テーブルに `expires_at` を必須カラムとして持ち、`expires_at = max(collected_at, last_used_at, article_published_at) + 90 days` を唯一の計算式とする。削除は専用ジョブが毎実行の最後に行い、削除件数をレポートする。
4. **Gemini 呼び出しは全てバジェット制御下に置く。** 1 実行あたりの最大リクエスト数・トークン数を設定値で強制し、超過分は次回へ持ち越す。抽出された fact は必ず出典スパン(元テキストの該当箇所)を伴い、スパンが機械検証できない fact は破棄する(誤生成の混入防止)。
5. **Loka との契約は D1 上の契約テーブル + スキーマバージョンで行う。** 同一 Cloudflare アカウント内で Loka のワークフローが読み取る。破壊的変更はバージョンを上げ、旧バージョンを 1 サイクル併存させる。
6. **fail-closed 原則。** 収集失敗・スキーマ不整合・出典検証失敗時は下流(Loka への供給)へ進めない。

### 実装言語・スタック(Phase 0 で最終確定)

言語は v1 が Python だったからではなく、v2 要件から選定する。Phase 0 で以下の 2 案を比較して確定する。

- **Python 案(推奨)**: SQLite でのローカルテスト、HTML/フィード解析ライブラリの成熟度、TDD・fixture 運用の実績。Cloudflare へは REST API 経由で十分(compute は Actions であり Workers ではないため、Cloudflare ネイティブ SDK の優位は小さい)。
- **TypeScript 案**: wrangler / D1 ツールチェーンとの親和性。将来 Workers へ寄せる場合に有利。

いずれの場合も、選定理由を ADR として `docs/current/` に記録する。

## 3. データモデル(初期案)

Issue の「想定データ」を正として、以下のテーブル構成から始める(Phase 0 で SSOT 仕様書へ昇格)。

```text
sources               情報源定義(Git 管理の manifest から同期)
  id, name, url, country, language, license_note, schedule, enabled

raw_items             収集した生アイテム
  id, source_id, url, url_normalized, title, excerpt,
  published_at, collected_at, content_hash, r2_key,
  status(collected|normalized|deduped|extracted|expired),
  expires_at

duplicate_clusters    重複クラスタ
  id, representative_raw_item_id, member_count
cluster_members       raw_item_id, cluster_id

facts                 正規化された事実(読者・媒体に非依存)
  id, cluster_id, statement, evidence_span, evidence_raw_item_id,
  topic, entities(JSON), country, region, language,
  reliability(source メタデータ由来), extracted_at, model_version,
  status(extracted|verified|rejected),
  last_used_at, expires_at

editorial_candidates  媒体向け供給ビュー(契約テーブル, schema_version 付き)
  fact_id, candidate_media(loka-th|loka-id|...), score_hint, offered_at

article_usages        記事との関連(Loka 側が書き戻す)
  fact_id, media, article_id, article_url, article_published_at

published_provenance  永続監査情報(Git へ書き出し)
  article_url, source_urls, published_at, collected_at, content_hashes
```

## 4. パイプライン(実行順)

```text
1. Preflight(設定・契約バージョン検証)
2. Manifest Sync(情報源定義を D1 へ同期)
3. Collect(due の情報源から取得、生データを R2 へ、メタを D1 へ)
4. Normalize(テキスト抽出・URL/テキスト正規化・content hash)
5. Dedupe(hash + 類似判定でクラスタ化)
6. Extract(Gemini による事実抽出、出典スパン検証、バジェット制御)
7. Offer(editorial_candidates へ供給、候補メディアの付与)
8. Feedback取込(article_usages から last_used_at 更新、provenance を Git へ書き出し)
9. Retention(expires_at 超過データの削除、削除件数レポート)
10. Notify / Summary(失敗時 Issue 通知、Actions Summary レポート)
```

## 5. フェーズ計画

Issue の実施順序(10月後半〜年末)に合わせる。8〜10月中旬は PixWork / Simple Games / Gemini 移行が優先のため、Glintory は Phase 0(設計)のみ先行着手可能な状態にしておく。

### Phase 0: 設計確定(〜10/31)

- v2 スキーマ確定(§3 を SSOT 仕様書 `docs/current/glintory_v2_spec.md` へ昇格)
- 実装言語・スタックの確定(ADR 化)
- 初期情報源リストの確定(§6 の選定基準で 5〜10 源)
- Loka 供給契約 v1 の確定(editorial_candidates / article_usages / provenance)
- Cloudflare アカウント準備(D1 / R2 作成、API トークンを Actions Secrets へ)
- コスト試算(無料枠と Gemini 単価に基づく 1 記事あたり原価の見積り)

完了条件: 仕様書レビュー済み、D1/R2 に空の本番リソースが存在する。

### Phase 1: クリーンスレート化と基盤(11月前半)

- **v1 の全削除**: 旧ソースコード・ワークフロー・Web UI・state リリース・旧ドキュメントを削除し、v2 スケルトンのみのリポジトリ構成にする
- D1 クライアント(REST API ラッパ)+ ローカル SQLite 互換のリポジトリ層
- v2 スキーマのマイグレーション機構(シンプルな番号付き SQL ファイル方式)
- Retention ジョブ(expires_at 削除)と R2 ライフサイクル設定
- 新ワークフロー `glintory-automation.yml` の骨格(Preflight → Manifest Sync → Retention → Summary)

完了条件: 空実行がスケジュールで安定稼働し、D1 への読み書きと削除がテスト済み。

### Phase 2: 収集〜事実抽出(11月中旬〜11月末)

- RSS/フィードコレクタの新規実装 + 日本情報源の登録(必要なら sitemap / HTML コレクタを 1 種のみ追加)
- Normalize / Dedupe の実装(日本語前提の正規化・類似判定)
- Gemini 事実抽出(構造化出力、出典スパン検証、バジェット制御、model_version 記録)
- 抽出品質の観測(検証失敗率・破棄率を Summary へ)

完了条件: 5 源以上から毎日収集し、重複排除済みの facts が継続的に生成され、明らかな誤生成が出典スパン検証で遮断されている。

### Phase 3: Loka 供給と初公開支援(12月前半)

- Offer ステップと editorial_candidates 供給の実装
- article_usages の書き戻し受け入れと provenance の Git 書き出し
- 契約テスト用のダミー消費者を Glintory 側に用意し、Loka 実装を待たずに供給契約を単体検証
- Loka 1か国目(MVP)の記事生成・公開を供給側から支援(データの過不足を実運用で修正)

完了条件: Loka 1か国目の公開記事が、Glintory の facts と出典メタデータに完全にトレースできる。

### Phase 4: 安定化と2か国目(12月中旬〜年末)

- 2か国目向けの candidate_media 追加(Glintory 側の変更が設定追加のみで済むことを検証 = 限界費用の実証)
- 短縮 TTL テストによる削除フローの実地検証
- コスト実測(Gemini 費用 / 記事、無料枠消費率)と成功条件レビュー(§8)

完了条件: 国の追加が再実装なしで完了し、運用コストと品質指標が計測されている。

## 6. 初期情報源の選定方針

選定基準(すべて満たすこと):

1. 公開情報であり、転載・要約に法的・規約的問題がない(利用規約を情報源ごとに `license_note` へ記録)
2. RSS / 構造化フィードなど機械可読で安定取得できる(スクレイピング依存は初期は避ける)
3. 更新頻度が週次以上で、Loka の読者(タイ・インドネシア在住者)に有用なテーマを含む
4. 一次情報または公的情報源である(まとめサイト・二次配信は除外)

初期候補カテゴリ: 官公庁・政府系の告知(観光・生活・防災・制度変更)、公的統計のリリース、自治体・公的機関のイベント情報、企業の公式プレスリリース(製品・サービスの日本発情報)。具体的な情報源リストは Phase 0 で確定する。

## 7. コストとガードレール

- 目標: AI 費用を除くインフラ費を無料枠内(D1 / R2 / Pages / Actions)。Workers Paid・常駐サーバーは使わない。
- Gemini: 1 実行あたりの上限(リクエスト数・トークン)を設定で強制。月次予算超過が見えたら抽出頻度を落とす(持ち越しで対応)。
- 無料枠の監視: D1 行数・ストレージ、R2 容量、Actions 実行時間を Summary に毎回出力し、閾値 80% で警告 Issue を出す。
- 品質: 人手確認が常態化しないことを成功条件とするため、「出典スパン検証で機械的に破棄する」ことを人手レビューより優先する。

## 8. 成功条件(Issue の条件を Glintory 側指標へ変換)

| Issue の条件 | Glintory 側の計測指標 |
|---|---|
| 情報源を継続的に取得できる | 収集成功率(源別・週次)、PARTIAL/FAILED 率 |
| 重複排除が機能する | クラスタ化率、公開記事間の重複起源ゼロ |
| 出典と事実を追跡できる | 全公開記事が provenance に解決できる(100%) |
| 明らかな誤生成を抑えられる | 出典スパン検証の破棄率が観測され、未検証 fact の流出ゼロ |
| 期限切れデータが自動削除される | Retention ジョブの削除実績、expires_at 超過残存ゼロ |
| 国の追加に再実装が不要 | 2か国目追加の変更が設定 + プロンプトのみ |
| 記事生成・公開の現金原価を把握できる | 1 記事あたり Gemini 費用・インフラ費の月次レポート |

## 9. リスクと対応

| リスク | 対応 |
|---|---|
| 情報源の規約・著作権問題 | 選定基準 §6 の遵守、license_note の必須化、疑義ある源は採用しない |
| Gemini の誤生成が記事へ混入 | 出典スパン検証で fail-closed。fact は原文該当箇所なしでは通さない |
| D1 REST API のレイテンシ・レート制限 | バッチ書き込み(一括 statement)、実行内ローカルキャッシュ、閾値監視 |
| 無料枠超過 | §7 の 80% 警告、TTL 90 日により総量が自然に頭打ちになる設計 |
| ゼロベース実装で v1 が解決済みの問題を再発 | v1 のテスト・fixture を回帰チェック用にハーベスト(テストは仕様を縛らない)。URL正規化・SSRF対策・エラーサニタイズ等の教訓は仕様書側に明文化して引き継ぐ |
| Loka 側の遅延で契約が検証できない | Phase 3 のダミー消費者(契約テスト)で供給契約を単体検証する |

## 10. 実装体制: サブエージェント運用

実装はオーケストレータ + サブエージェントの体制で進め、タスクの性質に応じて効率的なモデルを使い分けることでコストと速度を最適化する。

### 役割分担

| 役割 | 担当 | モデル方針 |
|---|---|---|
| 設計・仕様策定・タスク分解・統合レビュー | オーケストレータ(メインセッション) | 高性能モデル |
| コンポーネント実装 + 単体テスト(TDD) | 実装サブエージェント | 効率的なモデル(Sonnet / Haiku 級)を基本とする |
| スキーマ・データ契約・セキュリティ・削除ロジック | オーケストレータ直轄 or 高性能モデルのサブエージェント | 高性能モデル |
| コードレビュー・敵対的検証 | 独立レビューサブエージェント | 実装と別インスタンス(同一エージェントに自己レビューさせない) |
| 定型作業(fixture 整備、設定ファイル、ドキュメント同期) | 実装サブエージェント | 最も効率的なモデル(Haiku 級) |

### 運用ルール

1. **タスク粒度**: 「1 コンポーネント + そのテスト」を 1 サブエージェントタスクとする。仕様書の該当節と受け入れ条件(テストが通ること)をプロンプトで明示し、サブエージェントに設計判断を委ねない。
2. **並列化**: 依存のないコンポーネントは並列に実装する。例:
   - Phase 1: D1 クライアント / マイグレーション機構 / Retention ジョブ / ワークフロー骨格 → 4 並列
   - Phase 2: コレクタ / Normalize / Dedupe / Gemini 抽出 → 契約(インターフェース)を先に確定した上で並列
3. **品質ゲート**: サブエージェントの成果物は (a) テスト全通過、(b) 独立レビューエージェントの検証、(c) オーケストレータの統合確認、の 3 段階を通す。特に Retention(削除)・出典スパン検証・供給契約は敵対的検証(壊しにいくレビュー)を必須とする。
4. **モデルのエスカレーション**: 効率的なモデルで 2 回失敗したタスクは、粒度を見直すか高性能モデルへ引き上げる。逆に、定型化が確認できた作業は随時効率的なモデルへ下げる。
5. **インターフェース先行**: 並列化の前提として、コンポーネント間のインターフェース(関数シグネチャ・テーブルスキーマ・fixture 形式)をオーケストレータが先に確定・コミットしてからサブエージェントへ配る。手戻りの最大要因を潰す。

## 11. 非目標(Issue と同じ)

- 2026 年中の Voygie 開発
- 最初から多数の国・テーマへの展開
- Glintory の永続データレイク化(TTL 設計で構造的に防止)
- 送客目的での単体赤字の正当化
