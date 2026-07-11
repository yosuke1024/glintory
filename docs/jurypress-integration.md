# JuryPress Integration Guide

Glintoryで選別されたOpportunity（案件候補）データを、外部プラットフォーム「JuryPress」へと安全かつ安定して配信・連携するためのインテグレーション仕様です。

## JuryPress 配信基準 (JuryPress Readiness)

Glintoryは案件（Opportunity）ごとに **JuryPress配信適格性 (Readiness)** を自動的に評価します。
以下の条件を**すべて**満たす案件のみが、`jurypress.ready` = `True` と判定され、`/data/v1/feeds/jurypress.json` フィードに掲載されます。

### 評価基準

1. **Scoring Version**: 最新のスコアリングロジックである `v2` コンテキストで処理されていること。
2. **Quality & Evidence Gate**: Hard Gate（品質ゲートおよび証拠ゲート）に合格していること (`gate_status == "passed"`)。
3. **Workflow Status**: レビューステータスが除外（Rejected/Archived）されておらず、有効であること (`status` が `INBOX` または `RESEARCH`）。
4. **LLM Enrichment**: AIによる解説抽出が完了していること (`enrichment_status == "completed"` 又は `"succeeded"` などの完了ステータス)。
5. **Freshness & Stale Check**: 証拠シグナルが更新された後にAIによる解説が最新化されていること（AI生成日時 `enriched_at` が `evidence_updated_at` 以上であること。AI生成後に証拠の追加等が発生した場合はStale状態とみなされ除外されます）。
6. **Bilingual Localization**: 日本語・英語の双方で、以下の必須解説テキスト9ペアすべてが空文字ではなく存在すること：
   - Title (タイトル)
   - Summary (要約)
   - Problem Statement (課題定義)
   - Target Users (ターゲットユーザー)
   - Current Workaround (現在の回避策)
   - Existing Solution Gap (既存製品の不足/ギャップ)
   - MVP Direction (推奨されるMVPの方向性)
   - Why Selected (選定理由)
   - Risks (リスク定義)
7. **Evidence Summaries**: 案件に関連付けられたアクティブな証拠（Evidence）シグナルのうち、少なくとも1件以上に対して、AIによる「証拠の要約（日本語または英語）」が設定されていること。
8. **Confidence Level**: スコアリングにおける確信度（Confidence）が `MEDIUM` または `HIGH` であること。
9. **Total Score**: スコアリングの総合点（`total_score`）が閾値以上であること。
   - 閾値は環境変数 `GLINTORY_JURYPRESS_MIN_SCORE` で設定可能です（デフォルトは `60`）。
10. **Evidence Volume Constraints**:
    - 独立した情報源（証拠スレッド）が **2件以上** あること (`independent_evidence_count >= 2`)。
    - その中に「需要（Demand）を表すシグナル」が **1件以上** 含まれていること (`demand_evidence_count >= 1`)。

---

## 配信除外理由コード (Readiness Reason Codes)

`jurypress.ready` が `False` になった案件については、理由が以下のコードで詳細JSON (`jurypress.reasons`) に記録されます。これらを監視することで、運用側で案件の不足情報（翻訳の未実施、AI分析の遅れ、スコア不足など）を特定できます。

| 理由コード | 説明 |
| :--- | :--- |
| `INVALID_SCORING_VERSION` | スコアリングバージョンが `v2` ではない |
| `GATE_REJECTED` | ハードゲート判定で却下されている |
| `STATUS_EXCLUDED` | ステータスが `rejected` または `archived` |
| `LOW_CONFIDENCE` | 確信度が `LOW` である |
| `SCORE_BELOW_THRESHOLD` | 総合点数が設定された閾値（デフォルト60点）未満 |
| `INSUFFICIENT_INDEPENDENT_EVIDENCE` | 独立した情報源（スレッド）が2件未満 |
| `INSUFFICIENT_DEMAND_EVIDENCE` | 需要を示すシグナルが1件未満 |
| `ENRICHMENT_MISSING` | AIによる解説抽出データ（Enrichment）が存在しない |
| `ENRICHMENT_STALE` | 証拠更新後にAI解説の再生成が行われていない |
| `JAPANESE_LOCALIZATION_MISSING` | 日本語ローカライズ必須テキスト（9項目）のいずれかが不足している |
| `ENGLISH_LOCALIZATION_MISSING` | 英語ローカライズ必須テキスト（9項目）のいずれかが不足している |
| `EVIDENCE_SUMMARY_MISSING` | 証拠シグナルの要約が1件も設定されていない |

---

## 運用監視用バリデーション CLI

配信されるデータコントラクト全体の整合性検証、および適格性判定結果の可視化のために、以下のCLIコマンドを提供しています。

### 1. データコントラクト検証
出力された静的サイトディレクトリ全体のJSON feedとスキーマの整合性・不整合を検証します。

```bash
# distディレクトリ配下の出力内容を検証
glintory publish validate-contract --dir dist
```

**検証内容：**
- 必須ファイル（`manifest.json`, `opportunities.json`, `feeds/jurypress.json`）の存在チェック
- Pydantic v2モデルによる全JSONデータのスキーマ検証
- 詳細Opportunity JSONと一覧JSON間でのハッシュ不一致チェック
- 公開案件全体のハッシュとマニフェスト `content_hash` の一致チェック

### 2. JuryPressフィード配信インスペクタ
JuryPressに配信予定の案件と、除外された案件の一覧および除外理由（Readiness Reason Codes）を人間が読みやすい形式で可視化します。

```bash
glintory publish inspect-jurypress-feed --dir dist
```

**出力例：**
```text
=== JuryPress Ready Opportunities ===
- [opp_de6838e1642c49cd9f089893eed60aa3] 開発者向けデータベース自動マイグレーションツール (Score: 85, Confidence: HIGH)

=== Excluded Opportunities ===
- [opp_a9d3e869311a43a90629bb7e6cc5f1a3] 企業内ナレッジベース検索AI (Score: 55, Confidence: MEDIUM)
  Reasons: SCORE_BELOW_THRESHOLD, JAPANESE_LOCALIZATION_MISSING
- [opp_b68c7eff7f714eb6bc1f6a9970b12f2c] Cloudflare Pages 向け多言語ルーティングモジュール (Score: 78, Confidence: HIGH)
  Reasons: ENRICHMENT_STALE
```
