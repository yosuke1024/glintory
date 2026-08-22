# Glintory v2 Cloudflare セットアップ手順(要ユーザー作業)

Phase 0 の完了条件「D1/R2 に空の本番リソースが存在する」を満たすためのチェックリスト。Cloudflare アカウントの操作が必要なため、ユーザーが実施する。所要 15 分程度。

## 1. D1 データベース作成

Cloudflare ダッシュボード → Workers & Pages → D1:

- [ ] データベース `glintory` を作成(リージョンは自動で可)
- [ ] 表示される **Database ID** を控える

## 2. R2 バケット作成

Cloudflare ダッシュボード → R2:

- [ ] R2 を有効化(無料枠内でもクレジットカード登録が求められる場合がある)
- [ ] バケット `glintory-raw` を作成(ロケーション: APAC 推奨)
- [ ] Object lifecycle rules で「作成から 90 日後に削除」ルールを追加(プレフィックス指定なし・バケット全体)
- [ ] R2 の S3 API 用トークン(Access Key ID / Secret Access Key)を作成: R2 → Manage R2 API Tokens → 権限は **このバケット限定の Object Read & Write**

## 3. API トークン作成(D1 用)

My Profile → API Tokens → Create Token(カスタム):

- [ ] 権限: `Account / D1 / Edit` のみ
- [ ] 対象アカウントを限定
- [ ] トークン値を控える(再表示不可)

## 4. GitHub リポジトリへの登録

`yosuke1024/glintory` → Settings → Secrets and variables → Actions:

**Secrets:**

- [ ] `CLOUDFLARE_ACCOUNT_ID` — ダッシュボード右下等で確認できるアカウント ID
- [ ] `CLOUDFLARE_D1_DATABASE_ID` — 手順 1 の Database ID
- [ ] `CLOUDFLARE_API_TOKEN` — 手順 3 のトークン
- [ ] `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — 手順 2 のキー
- [ ] `R2_ENDPOINT` — `https://<account_id>.r2.cloudflarestorage.com`
- [ ] `GEMINI_API_KEY` — Google AI Studio で発行

**Variables:**

- [ ] `GLINTORY_R2_BUCKET` = `glintory-raw`

## 5. 確認

Phase 1 で実装する `glintory doctor` コマンド(接続確認)を CI から実行し、D1 への SELECT 1、R2 への put/get/delete、Gemini への疎通が通ることを確認する。それまでは登録値の目視確認のみでよい。

## セキュリティ注意

- トークンは最小権限(D1 Edit のみ / バケット限定)で作成すること。Workers のデプロイ権限等を含むトークンを使い回さない。
- 値は GitHub Secrets のみに保存し、`.env` はローカル開発専用・コミット禁止(`.env.example` に項目名のみ記載)。
