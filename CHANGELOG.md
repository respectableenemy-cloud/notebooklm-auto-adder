# Changelog

## 2026-03-20

### Fixed: HTTP 500 エラーの原因特定と修正

#### 問題
Railway に環境変数 (`NOTEBOOKLM_AUTH_JSON`) が設定されていない、または反映されていない状態で `/run` を叩くと、`get_notebooklm()` 内の `RuntimeError` がキャッチされずにそのまま HTTP 500 として返されていた。

#### 変更内容

**`main.py`**

1. **`/run` エンドポイント冒頭で環境変数チェックを追加**
   - `NOTEBOOKLM_AUTH_JSON` が未設定の場合、HTTP 500 + 日本語メッセージで即時返却
   - ブラウザ側で原因が分かるエラーメッセージを表示できるようになった

2. **DuckDuckGo 検索部分に `try/except` を追加**
   - ネットワークエラー等で検索が失敗した場合に HTTP 502 で返却

3. **`RuntimeError` を `HTTPException` に変換**
   - `get_notebooklm()` が raise する `RuntimeError` を `except RuntimeError` でキャッチし、HTTP 500 + detail メッセージに変換
   - FastAPI が適切な JSON エラーレスポンスを返せるようになった

4. **インデント修正**
   - `async with get_notebooklm() as client:` ブロック内のコードが正しくインデントされていなかった箇所を修正

5. **`/health` エンドポイントを追加**
   - `GET /health` で環境変数の設定状況を確認できる
   - Railway デプロイ後に `https://<your-app>.railway.app/health` を開くことで変数が反映されているか即座に確認可能
   - レスポンス例:
     ```json
     {
       "status": "ok",
       "NOTEBOOKLM_AUTH_JSON": "set",
       "APP_SECRET": "set"
     }
     ```

#### Railway 側の確認手順
1. Railway ダッシュボード → Variables で `NOTEBOOKLM_AUTH_JSON` と `APP_SECRET` が設定されているか確認
2. 変数を追加・変更した場合は **Redeploy** が必要（自動適用されない場合がある）
3. デプロイ後に `/health` にアクセスして `"status": "ok"` になっていることを確認

---

### Fixed: Chromium サンドボックス権限エラー（HTTP 500 根本原因）

#### 問題
Railway のコンテナ環境では Chromium のサンドボックスに必要な Linux Capability（`SYS_ADMIN` 等）がないため、`notebooklm-py` が Playwright 経由で Chromium を起動しようとすると失敗し HTTP 500 になっていた。

#### 変更内容

**`main.py`**

- **Playwright モンキーパッチを追加**（`import notebooklm` より前に実行）
  - `playwright.async_api.BrowserType.launch` をラップし、`--no-sandbox`、`--disable-setuid-sandbox`、`--disable-dev-shm-usage` を常に注入
  - `notebooklm-py` が内部で `launch()` を呼ぶ際に自動的に適用される

**`Dockerfile`**

- `ENV PYTHONUNBUFFERED=1` を追加
  - Railway のログ画面にアプリの出力が即時反映されるようになる（デバッグ効率向上）
