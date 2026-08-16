# Glintory ↔ Loka 供給契約 v1

ステータス: Phase 0 ドラフト(レビュー後に確定)/ `schema_version = 1`

本書は Glintory(共通情報基盤)と Loka(編集プロダクト)の間のデータ契約を定義する。両者は同一 Cloudflare アカウントの D1 データベースを共有し、本契約に列挙されたテーブル以外への相互アクセスを禁止する。

---

## 1. 責務分担

| 責務 | Glintory | Loka |
|---|---|---|
| 収集・正規化・重複排除・事実抽出 | ✅ | — |
| editorial_candidates への供給 | ✅ 書込 | 読取のみ |
| 記事の選定・生成・翻訳・公開 | — | ✅ |
| article_usages の書き戻し | 読取のみ | ✅ 書込 |
| provenance の Git 永続化 | ✅ | — |
| facts / raw_items 等の内部テーブル | ✅ | アクセス禁止 |

Loka は facts の `statement` と出典メタデータのみを信頼し、Glintory の内部処理(クラスタ・スパン検証)には依存しない。

## 2. Glintory → Loka: editorial_candidates

Loka が読み取るビュー。1 行 = 1 fact × 1 media。

| 列 | 型 | 意味 |
|---|---|---|
| id | TEXT | ULID |
| schema_version | INTEGER | 本契約の版。**Loka は自分が知らない版の行を無視する** |
| fact_id | TEXT | 事実 ID(article_usages の書き戻しに使う) |
| media | TEXT | `loka-th` / `loka-id` / …(Loka は自 media の行のみ読む) |
| topic | TEXT | 統制語彙(仕様書 §6.3) |
| score_hint | REAL | 参考優先度。Loka は無視してよい |
| offered_at | TEXT | 提供日時(UTC ISO 8601) |
| consumed_at | TEXT | Loka が読み取り済みを記録(Loka が更新してよい唯一の列) |
| expires_at | TEXT | この日時以降、行は削除され得る |

fact 本体(statement / evidence / 出典 URL / published_at)は結合ビュー `v_editorial_candidates` で提供する:

```sql
CREATE VIEW v_editorial_candidates AS
SELECT ec.id, ec.schema_version, ec.fact_id, ec.media, ec.topic,
       ec.score_hint, ec.offered_at, ec.consumed_at, ec.expires_at,
       f.statement, f.entities, f.region, f.language, f.reliability,
       f.evidence_span,
       r.url  AS source_url,
       r.title AS source_title,
       r.published_at AS source_published_at,
       r.collected_at AS source_collected_at,
       r.content_hash AS source_content_hash,
       s.name AS source_name,
       s.license_note AS source_license_note
FROM editorial_candidates ec
JOIN facts f      ON f.id = ec.fact_id
JOIN raw_items r  ON r.id = f.evidence_raw_item_id
JOIN sources s    ON s.id = r.source_id;
```

### Loka 側の遵守事項

- 記事に使う全ての事実について `source_url` を出典として明示すること。
- `expires_at` を過ぎた候補を参照し続けないこと(記事生成は候補の取得と同一実行内で完結させる)。
- `license_note` に転載制限がある源の候補は、制限に従うこと。

## 3. Loka → Glintory: article_usages

Loka が記事公開時に書き戻す。1 行 = 1 記事 × 1 使用 fact。

| 列 | 型 | 意味 |
|---|---|---|
| id | TEXT | ULID(Loka が採番) |
| media | TEXT | 自 media 識別子 |
| article_id | TEXT | Loka 内の記事 ID(安定であること) |
| article_url | TEXT | 公開 URL |
| article_published_at | TEXT | 公開日時(UTC) |
| fact_id | TEXT | 使用した fact |
| recorded_at | TEXT | 書き戻し日時 |

- `UNIQUE (media, article_id, fact_id)`。再送は upsert として扱われ冪等。
- Glintory はこれを受けて `last_used_at` / `expires_at` を更新し、provenance を Git へ書き出す。
- **書き戻しが無い fact は 90 日で消える。** 記事に使ったのに書き戻さないと、監査可能性が失われる(Loka 側の実装必須事項)。

## 4. 永続 provenance(Git)

Glintory が Feedback ステップで書き出す永続監査情報。リポジトリ内 `provenance/{media}/{yyyy-mm}/{article_id}.json`:

```json
{
  "schema_version": 1,
  "media": "loka-th",
  "article_id": "…",
  "article_url": "https://pixapps.ai/loka/thailand/…",
  "article_published_at": "2026-12-05T09:00:00Z",
  "facts": [
    {
      "fact_id": "…",
      "statement": "…",
      "source_url": "https://…",
      "source_name": "…",
      "source_published_at": "…",
      "source_collected_at": "…",
      "source_content_hash": "sha256:…"
    }
  ]
}
```

- 一度書き出した provenance は変更しない(追記のみ)。
- D1 の作業データが期限切れで消えた後も、公開記事の出典はこのファイルで追跡できる(成功条件「全公開記事が provenance に解決できる」の実体)。

## 5. バージョニングと互換性

- 破壊的変更(列の削除・意味変更)は `schema_version` を上げ、旧版の行の提供を 1 サイクル(最低 7 日)併存させる。
- 追加列は非破壊とみなし版を上げない(Loka は未知の列を無視する)。
- 契約変更は本書の改訂 + 双方のリポジトリの Issue 相互リンクで通知する。

## 6. 契約テスト(ダミー消費者)

Glintory リポジトリ内に、Loka を模したダミー消費者テストを置く:

1. `v_editorial_candidates` から自 media の候補を読み取れること(必須列が全て非 NULL)。
2. `article_usages` へ upsert でき、再送しても行が増えないこと。
3. 書き戻し後の実行で `last_used_at` が更新され、provenance ファイルが生成されること。
4. 未知の `schema_version` の行が混ざっても既知の行の処理に影響しないこと。

このテストが通ることを「供給契約が成立している」ことの定義とし、Loka 実装の完成を待たずに Phase 3 の完了条件を判定できるようにする。
