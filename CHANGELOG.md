# Changelog

## 2026-03-26

### Added: セッションキープアライブ & 認証リフレッシュスクリプト

#### 変更内容

**`main.py`**
- `_keepalive_loop()` を追加: 48時間ごとに `client.notebooks.list()` を呼び出してGoogleセッションを維持する
- FastAPI `lifespan` を導入し、アプリ起動時にキープアライブタスクをバックグラウンドで起動

**`../refresh_railway_auth.sh`**（ローカルスクリプト）
- セッション失効時の復旧を1コマンド化
- `notebooklm login` → `NOTEBOOKLM_AUTH_JSON` をRailwayへ自動反映 → リデプロイ まで自動実行

---

## 2026-03-21（2回目）

### Changed: ファイル構成の整理

#### 変更内容

- `~/AI/` 直下に散在していた NotebookLM 関連ファイルを `~/AI/notebooklm/` フォルダに集約
  - 移動したファイル: `add_to_notebooklm.py`、`export_auth_for_railway.py`、`railway_app/`
  - `venv/` は移動すると壊れるため `~/AI/` 直下に残置
- 各スクリプトはハードコードされたパスを持たないため、移動による動作への影響なし

---

## 2026-03-21

### Fixed: Google OAuth リダイレクト URI の http/https 不一致

#### 問題
Railway のリバースプロキシ内部では通信が `http://` になるため、`request.base_url` が `http://web-production-53229.up.railway.app/` を返していた。Google に送るリダイレクト URI が `http://` になり、Cloud Console に登録した `https://` と不一致になって「アクセスをブロック」エラーが発生していた。

#### 変更内容

**`main.py`**
- `get_redirect_uri()` ヘルパーを追加
  - `RAILWAY_PUBLIC_DOMAIN` 環境変数（Railway が自動設定）が存在する場合は `https://{domain}/auth/callback` を返す
  - ローカル等では従来通り `request.base_url` を使用
- `/auth/start` と `/auth/callback` の両方で `get_redirect_uri()` を使用するよう変更

---

## 2026-03-20（2回目）

### Added: Google OAuth ログイン

#### 変更内容

**`main.py`**
- Google OAuth フロー実装（`/login` → `/auth/start` → Google → `/auth/callback`）
- セッションを `itsdangerous` で署名した HTTP-only Cookie で管理（7日間有効）
- `ALLOWED_EMAILS` 環境変数で許可メールアドレスを制限
- ログアウト（`/logout`）追加
- `APP_SECRET` / `HTTPBearer` 認証を削除
- UI からパスワード入力欄を削除、右上にログイン中メールアドレスとログアウトリンクを追加

**`requirements.txt`**
- `httpx`（OAuth トークン交換）、`itsdangerous`（セッション署名）を追加

#### Railway に追加で必要な環境変数
| 変数名 | 内容 |
|---|---|
| `GOOGLE_CLIENT_ID` | Google Cloud Console で取得 |
| `GOOGLE_CLIENT_SECRET` | 同上 |
| `ALLOWED_EMAILS` | 許可するメール（カンマ区切り） |
| `SESSION_SECRET` | 任意の長い文字列（セッション署名用） |

---

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
