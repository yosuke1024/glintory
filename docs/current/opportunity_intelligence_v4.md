# Glintory Opportunity Intelligence v4 Specification (SSOT)

## 概要
Glintoryのパイプラインに `Discovery Lead` および `Gate v4` アーキテクチャを導入し、データ品質の向上と、LLM生成の要約レポート（AgentRadar等）からの高精度な一次情報検証を可能にしました。

---

## 1. 概念モデルの分離
Glintory内では、以下の概念を厳密に分離します。

1. **Discovery Lead**
   - 上流ソース（AgentRadarのLLM生成レポートなど）から抽出された調査候補。
   - `DiscoveryLead` モデルとしてDBに保存され、URL検証と一次情報の再取得（GitHub / Hacker News等）を経て `verified` 状態になります。
2. **Signal**
   - 一次ソースから直接再取得・検証された個々の証拠。
   - 以下の属性を追加：
     - `document_kind`: ドキュメントの種類。
     - `opportunity_anchor`: クラスタの代表（アンカー）になれるか。
     - `discovery_eligible`: ディスカバリー対象の証拠か。
     - `source_specificity`: 特定製品にどの程度強く依存しているか (`high`, `medium`, `low`, `unknown`)。
3. **Product Opportunity**
   - 独立した需要（Demand Signal）が複数個確認され、ゲートを通過したプロダクト機会。

---

## 2. Gate v4 判定ルール
機会が `Published` 状態として公開されるための基準（Gate判定）を `gate_v4` にアップグレードしました。

- **Condition B の廃止**
  - 品質要素が十分な単一需要であっても、公開（INBOX/Published）にはせず、Research候補として扱います。
- **公開条件 (Passed Published)**
  - 独立した需要（`DEMAND` シグナル）のOrigin数（`demand_count`）が **2件以上** であること。
- **Fork / 同一作者の重複排除**
  - **Fork重複排除**: `raw_metadata` 内の `fork: true` かつ `parent` 情報がある場合は、同一の親リポジトリを Evidence Origin として扱います。
  - **同一作者の重複排除**: 同一の `author` がクロスポストした需要は、独立した需要（`demand_count`）としては1件としてカウントします。
  - `duanyytop/agents-radar` レポートURLそのものは Evidence Origin から除外します。

---

## 3. Generalizability (一般化可能性)
各 Opportunity に対し、需要の広がりと一般性を評価する `generalizability` フィールドを導入しました。

- `confirmed`: 異なるコンテキストで独立した需要が2件以上確認されている。
- `plausible`: 単一の需要だが、製品固有の実装に依存しない一般性がある。
- `source_specific`: 特定製品の UI や API 等に強く依存している。
- `unknown`: 判断材料不足。

---

## 4. 二段階 Enrichment
ローカルLLM（Llama等）を用いた情報付与（Enrichment）処理を2段階に分離し、日英の不一致防止とトークンの節約を実現しました。

- **Stage A: Canonical Opportunity Extraction**
  - 英語で Opportunity Thesis の各項目（課題、Wedge、MVPスコープ等）および `evidence_refs` を高精度に抽出・構築。
- **Stage B: Japanese Localization (Translation)**
  - Stage A で確定した英語の解析結果のみを入力とし、自然で正確な日本語に翻訳。
  - 原文の重複インプットを避けることで、トークン消費量を削減しつつ、日英事実関係の不一致を完全に防止します。
