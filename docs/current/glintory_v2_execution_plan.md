# Glintory v2 実行計画: 共通情報基盤への再構築

対象Issue: [pixapps_strategy#22 企画: Glintory共通情報基盤とLoka自律メディア](https://github.com/yosuke1024/pixapps_strategy/issues/22)

作成日: 2026-08-16 / 対象期間: 2026-10-下旬 〜 2026-12月末

---

## 1. 目的とスコープ

本計画は、Glintory を「日本の公開情報を収集・正規化し、複数メディアで再利用可能な事実データへ変換する共通情報基盤」へ再構築するための実行計画である。

**スコープ内:**

- Glintory v2 のアーキテクチャ・データモデル・パイプラインの再構築
- Cloudflare D1 / R2 への状態移行と、期限付きデータパイプライン(TTL削除)の実装
- Gemini による事実抽出・品質評価の組み込み
- Loka(最初の編集プロダクト)へ事実データを供給するデータ契約の定義と実装

**スコープ外:**

- Loka 本体(記事生成・ペルソナ・サイト)の実装 — 別計画とするが、本計画で供給契約を定義する
- Voygie およびその他メディア(2027年以降の候補)
- 多数の国・テーマへの初期展開

## 2. 現状 (As-Is) と再構築後 (To-Be)

### As-Is: Glintory v1 (Opportunity Intelligence)

| 項目 | 現状 |
|---|---|
| ドメイン | GitHub / Hacker News からの開発者向けプロダクト機会シグナル |
| 状態管理 | SQLite を tar.gz 化し GitHub Release (`glintory-state`) に保存・復元 |
| AI | GitHub Actions 上で llama-server をスポーン(ローカルLLM) |
| 公開面 | Jinja2 で静的サイト生成 → GitHub Pages |
| 実行 | GitHub Actions Scheduled Workflow(常駐プロセスなし) |

### To-Be: Glintory v2 (共通情報基盤)

| 項目 | 再構築後 |
|---|---|
| ドメイン | 日本の有用な公開情報 → 正規化された事実データ(トーン・結論を持たない) |
| 状態管理 | Cloudflare D1(構造化作業データ、expires_at 付き)+ R2(元HTML・生レスポンス等の大きな一時データ) |
| AI | Gemini(事実抽出、選定、品質評価) |
| 公開面 | Glintory 自体は公開サイトを持たない。公開面は Loka 側(Cloudflare Pages) |
| 実行 | GitHub Actions Scheduled Workflow(継続) |
| 監査情報 | 公開記事の出典メタデータ(URL・公開日・取得日・ハッシュ)のみ Git 管理で永続保持 |

### 資産の仕分け

**再利用する資産(移植・汎用化):**

- Collector フレームワーク(`collectors/base.py`, `registry.py`, RSS コレクタ)と manifest 同期の仕組み
- URL 正規化・テキスト正規化・content hash・重複排除の各サービス
- HTML→テキスト抽出、HTTP クライアント(SSRF 対策・リトライ含む)
- スケジュール管理(due 判定)、エラーサニタイズ、構造化ログ、Actions Summary レポート
- テスト規約(TDD、fixture ベースのオフラインテスト)、Alembic 互換のマイグレーション運用

**廃止・凍結する資産:**

- Opportunity ドメイン一式(scoring / gate v3・v4 / clustering / enrichment / discovery lead)
- ローカルLLM(llama-server)基盤 → Gemini へ置換
- GitHub Release への state 保存(`github_state_store`) → D1/R2 へ移行(「GitHub を一時データストアとして使わない」原則に適合)
- Glintory 自体の公開静的サイトと Web UI(運用確認は Actions Summary と最小限の運用レポートに縮退)

GitHub / Hacker News コレクタは v2 の初期情報源には含めないが、コレクタ契約の参照実装として残す。

## 3. アーキテクチャ方針(主要決定)

1. **D1 を作業データの唯一のストアとする。** D1 は SQLite 互換のため、ローカル・CI のテストは素の SQLite で決定論的に実行できる(fixture 資産と TDD 規約をそのまま活かす)。GitHub Actions からは Cloudflare REST API(D1 query endpoint)経由の薄いクライアントでアクセスする。SQLAlchemy ORM への依存は作業データ層では外し、リポジトリ層の SQL を D1/SQLite 両対応にする。
2. **R2 は「大きな一時データ」専用。** 元 HTML、API 生レスポンス、画像・スクリーンショット。キーに収集日を含め(`raw/{source_id}/{yyyy-mm-dd}/{content_hash}.html.gz`)、ライフサイクルルールで 90 日削除を R2 側でも二重に保証する。
3. **期限付きパイプラインを最初から組み込む。** 全作業テーブルに `expires_at` を必須カラムとして持ち、`expires_at = max(collected_at, last_used_at, article_published_at) + 90 days` を唯一の計算式とする。削除は専用ジョブが毎実行の最後に行い、削除件数を Summary に出す。永続保持は「公開記事の出典メタデータ」だけで、これは Git 管理の JSON として書き出す。
4. **Gemini 呼び出しは全てバジェット制御下に置く。** 1 実行あたりの最大リクエスト数・最大トークン数を設定値で持ち、超過時は fail-open(未処理分を次回へ持ち越し)。抽出結果は必ず出典スパン(元テキストの該当箇所)を伴い、スパンが検証できない fact は破棄する(誤生成の混入防止)。
5. **Loka との契約は D1 上の契約テーブル + スキーマバージョンで行う。** 同一 Cloudflare アカウント内で Loka のワークフローが読み取る。契約を破壊する変更はバージョンを上げ、旧バージョンを 1 サイクル併存させる。
6. **既存の fail-closed 原則を継続。** 収集失敗・スキーマ不整合・出典検証失敗時は下流(Loka への供給)へ進めない。

## 4. データモデル(初期案)

Issue の「想定データ」を正として、以下のテーブル構成から始める。

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

処理ステータスは raw_item / fact のカラムで持ち、独立したジョブテーブルは作らない(v1 の schedule 実行履歴の仕組みを流用)。

## 5. パイプライン(v2 実行順)

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
10. Notify / Summary(v1 の Issue 通知・Summary 資産を流用)
```

## 6. フェーズ計画

Issue の実施順序(10月後半〜年末)に合わせる。8〜10月中旬は PixWork / Simple Games / Gemini 移行が優先のため、Glintory は設計のみ先行して着手可能な状態にしておく。

### Phase 0: 設計確定(〜10/31)

- v2 スキーマ確定(§4 を SSOT 仕様書 `docs/current/glintory_v2_spec.md` へ昇格)
- 初期情報源リストの確定(§7 の選定基準で 5〜10 源)
- Loka 供給契約 v1 の確定(editorial_candidates / article_usages / provenance)
- Cloudflare アカウント準備(D1 / R2 作成、API トークンを Actions Secrets へ)
- コスト試算(無料枠と Gemini 単価に基づく 1 記事あたり原価の見積り)

完了条件: 仕様書レビュー済み、D1/R2 に空の本番リソースが存在する。

### Phase 1: 基盤移行(11月前半)

- D1 クライアント(REST API ラッパ)+ ローカル SQLite 互換のリポジトリ層
- v2 マイグレーション(新テーブル作成。v1 テーブルは移行対象外 — v1 データは引き継がない)
- Retention ジョブ(expires_at 削除)と R2 ライフサイクル設定
- v1 の GitHub Release state store・静的サイト生成・Web UI をワークフローから切り離す
- 新ワークフロー `glintory-v2-automation.yml` の骨格(Preflight → Manifest Sync → Retention → Summary)

完了条件: 空実行がスケジュールで安定稼働し、D1 への読み書きと削除がテスト済み。

### Phase 2: 収集〜事実抽出(11月中旬〜11月末)

- RSS コレクタの v2 移植 + 日本情報源の追加(必要なら sitemap / HTML コレクタを 1 種のみ追加)
- Normalize / Dedupe の移植と日本語対応(正規化・類似判定)
- Gemini 事実抽出(構造化出力、出典スパン検証、バジェット制御、model_version 記録)
- 抽出品質の観測(検証失敗率・破棄率を Summary へ)

完了条件: 5 源以上から毎日収集し、重複排除済みの facts が継続的に生成され、明らかな誤生成が出典スパン検証で遮断されている。

### Phase 3: Loka 供給と初公開支援(12月前半)

- Offer ステップと editorial_candidates 供給の実装
- article_usages の書き戻し受け入れと provenance の Git 書き出し
- Loka 1か国目(MVP)の記事生成・公開を Glintory 側から支援(供給データの過不足を実運用で修正)

完了条件: Loka 1か国目の公開記事が、Glintory の facts と出典メタデータに完全にトレースできる。

### Phase 4: 安定化と2か国目(12月中旬〜年末)

- 2か国目向けの candidate_media 追加(Glintory 側の変更が設定追加のみで済むことを検証 = 限界費用の実証)
- 90 日を待たない短縮 TTL テストで削除フローの実地検証
- コスト実測(Gemini 費用 / 記事、無料枠消費率)と成功条件レビュー(§9)

完了条件: 国の追加が再実装なしで完了し、運用コストと品質指標が計測されている。

## 7. 初期情報源の選定方針

選定基準(すべて満たすこと):

1. 公開情報であり、転載・要約に法的・規約的問題がない(利用規約を情報源ごとに `license_note` へ記録)
2. RSS / 構造化フィードなど機械可読で安定取得できる(スクレイピング依存は初期は避ける)
3. 更新頻度が週次以上で、Loka の読者(タイ・インドネシア在住者)に有用なテーマを含む
4. 一次情報または公的情報源である(まとめサイト・二次配信は除外)

初期候補カテゴリ: 官公庁・政府系の告知(観光・生活・防災・制度変更)、公的統計のリリース、自治体・公的機関のイベント情報、企業の公式プレスリリース(製品・サービスの日本発情報)。具体的な情報源リストは Phase 0 で確定する。

## 8. コストとガードレール

- 目標: AI 費用を除くインフラ費を無料枠内(D1 / R2 / Pages / Actions)。Workers Paid・常駐サーバーは使わない。
- Gemini: 1 実行あたりの上限(リクエスト数・トークン)を設定で強制。月次予算超過が見えたら抽出頻度を落とす(fail-open で持ち越し)。
- 無料枠の監視: D1 行数・ストレージ、R2 容量、Actions 実行時間を Summary に毎回出力し、閾値 80% で警告 Issue を出す(v1 の通知資産を流用)。
- 品質: 人手確認が常態化しないことを成功条件とするため、「出典スパン検証で機械的に破棄する」ことを人手レビューより優先する。

## 9. 成功条件(Issue の条件を Glintory 側指標へ変換)

| Issue の条件 | Glintory 側の計測指標 |
|---|---|
| 情報源を継続的に取得できる | 収集成功率(源別・週次)、PARTIAL/FAILED 率 |
| 重複排除が機能する | クラスタ化率、公開記事間の重複起源ゼロ |
| 出典と事実を追跡できる | 全公開記事が provenance に解決できる(100%) |
| 明らかな誤生成を抑えられる | 出典スパン検証の破棄率が観測され、未検証 fact の流出ゼロ |
| 期限切れデータが自動削除される | Retention ジョブの削除実績、expires_at 超過残存ゼロ |
| 国の追加に再実装が不要 | 2か国目追加の変更が設定 + プロンプトのみ |
| 記事生成・公開の現金原価を把握できる | 1 記事あたり Gemini 費用・インフラ費の月次レポート |

## 10. リスクと対応

| リスク | 対応 |
|---|---|
| 情報源の規約・著作権問題 | 選定基準 §7 の遵守、license_note の必須化、疑義ある源は採用しない |
| Gemini の誤生成が記事へ混入 | 出典スパン検証で fail-closed。fact は原文該当箇所なしでは通さない |
| D1 REST API のレイテンシ・レート制限 | バッチ書き込み(一括 statement)、実行内ローカルキャッシュ、閾値監視 |
| 無料枠超過 | §8 の 80% 警告、TTL 90 日により総量が自然に頭打ちになる設計 |
| v1 廃止による退行 | v1 資産は削除ではなくワークフローからの切り離しで凍結し、参照可能に保つ |
| Loka 側の遅延で契約が検証できない | Phase 3 で Glintory 側にダミー消費者(契約テスト)を用意し、供給契約単体で検証する |

## 11. 非目標(Issue と同じ)

- 2026 年中の Voygie 開発
- 最初から多数の国・テーマへの展開
- Glintory の永続データレイク化(TTL 設計で構造的に防止)
- 送客目的での単体赤字の正当化
