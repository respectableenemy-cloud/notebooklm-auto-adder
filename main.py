"""
NotebookLM 自動追加 Web アプリ
================================
Railway にデプロイして iPhone のブラウザから使う。

環境変数（Railway に設定）:
  NOTEBOOKLM_AUTH_JSON  : Mac の ~/.notebooklm/storage_state.json の中身
  GOOGLE_CLIENT_ID      : Google OAuth クライアント ID
  GOOGLE_CLIENT_SECRET  : Google OAuth クライアントシークレット
  ALLOWED_EMAILS        : 許可するメールアドレス（カンマ区切り）例: a@gmail.com,b@gmail.com
  SESSION_SECRET        : セッション署名用の秘密鍵（任意の長い文字列）
"""

import asyncio
import os
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

# ---- Playwright サンドボックス無効化（Railway/Docker コンテナ対応） ----
import playwright.async_api as _pw

_orig_launch = _pw.BrowserType.launch  # type: ignore[attr-defined]

async def _sandboxless_launch(self, **kwargs):
    args = list(kwargs.pop("args", []))
    for flag in ("--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"):
        if flag not in args:
            args.append(flag)
    return await _orig_launch(self, args=args, **kwargs)

_pw.BrowserType.launch = _sandboxless_launch  # type: ignore[method-assign]
# -----------------------------------------------------------------------

import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import FastAPI, HTTPException, Depends, status, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from ddgs import DDGS
from notebooklm import NotebookLMClient

# ---- OAuth 設定 -------------------------------------------------------
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ALLOWED_EMAILS       = [e.strip() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
SESSION_SECRET       = os.environ.get("SESSION_SECRET", "fallback-change-me")

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_INFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"

signer = URLSafeTimedSerializer(SESSION_SECRET)


# ---- セッション検証 ---------------------------------------------------
def require_login(session: str = Cookie(default=None)):
    if not session:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    try:
        email = signer.loads(session, max_age=86400 * 7)  # 7日間有効
        return email
    except (BadSignature, SignatureExpired):
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )


# ---- 起動時に認証ファイルを書き出す -----------------------------------
def setup_auth():
    auth_json = os.environ.get("NOTEBOOKLM_AUTH_JSON")
    if not auth_json:
        return
    storage_path = Path.home() / ".notebooklm" / "storage_state.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(auth_json, encoding="utf-8")


setup_auth()


# ---- NotebookLM クライアント -----------------------------------------
@asynccontextmanager
async def get_notebooklm():
    if not os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        raise RuntimeError("NOTEBOOKLM_AUTH_JSON が設定されていません")
    async with await NotebookLMClient.from_storage() as client:
        yield client


# ---- セッションキープアライブ ----------------------------------------
async def _keepalive_loop():
    """Googleセッションを維持するため48時間ごとにNotebookLMへアクセスする"""
    while True:
        await asyncio.sleep(60 * 60 * 48)
        if not os.environ.get("NOTEBOOKLM_AUTH_JSON"):
            continue
        try:
            async with get_notebooklm() as client:
                await client.notebooks.list()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_keepalive_loop())
    yield


# ---- アプリ ----------------------------------------------------------
app = FastAPI(title="NotebookLM Auto Adder", lifespan=lifespan)


# ---- リクエスト / レスポンス モデル ----------------------------------
class RunRequest(BaseModel):
    keyword: str
    num_results: int = 20
    notebook_name: str = ""
    region: str = "jp-jp"


class SourceResult(BaseModel):
    title: str
    url: str
    ok: bool
    error: str = ""


class RunResponse(BaseModel):
    notebook_id: str
    notebook_url: str
    notebook_name: str
    sources: list[SourceResult]
    success_count: int
    fail_count: int


# ---- エンドポイント --------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(email: str = Depends(require_login)):
    return HTML_UI.replace("__USER_EMAIL__", email)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE


def get_redirect_uri(request: Request) -> str:
    # Railway のリバースプロキシ経由では request.base_url が http:// になるため、
    # RAILWAY_PUBLIC_DOMAIN が設定されていれば https:// で明示的に組み立てる。
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        return f"https://{domain}/auth/callback"
    return str(request.base_url) + "auth/callback"


@app.get("/auth/start")
async def auth_start(request: Request):
    redirect_uri = get_redirect_uri(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = None, error: str = None):
    if error or not code:
        return HTMLResponse(f"<p>ログインエラー: {error}</p>", status_code=400)

    redirect_uri = get_redirect_uri(request)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        token_data = token_resp.json()

        if "error" in token_data:
            return HTMLResponse(f"<p>トークン取得エラー: {token_data['error']}</p>", status_code=400)

        info_resp = await client.get(
            GOOGLE_INFO_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        userinfo = info_resp.json()

    email = userinfo.get("email", "")
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return HTMLResponse(
            f"<p style='font-family:sans-serif;padding:40px'>アクセス権限がありません（{email}）</p>",
            status_code=403,
        )

    session_token = signer.dumps(email)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "session", session_token,
        httponly=True, secure=True, samesite="lax", max_age=86400 * 7,
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


@app.get("/health")
async def health():
    auth_ok = bool(os.environ.get("NOTEBOOKLM_AUTH_JSON"))
    return {
        "status": "ok" if auth_ok else "error",
        "NOTEBOOKLM_AUTH_JSON": "set" if auth_ok else "missing",
        "GOOGLE_CLIENT_ID": "set" if GOOGLE_CLIENT_ID else "missing",
        "ALLOWED_EMAILS": ALLOWED_EMAILS,
    }


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest, email: str = Depends(require_login)):
    if not os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="NOTEBOOKLM_AUTH_JSON が設定されていません",
        )

    notebook_name = req.notebook_name.strip() or req.keyword

    # 1. DuckDuckGo 検索
    raw_sources: list[tuple[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(req.keyword, region=req.region, max_results=req.num_results):
                url = r.get("href", "")
                title = r.get("title", url)
                if url:
                    raw_sources.append((title, url))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DuckDuckGo 検索エラー: {e}")

    if not raw_sources:
        raise HTTPException(status_code=404, detail="検索結果が見つかりませんでした")

    # 2. NotebookLM にノートブック作成 & ソース追加
    results: list[SourceResult] = []
    try:
        async with get_notebooklm() as client:
            notebook = await client.notebooks.create(notebook_name)

            async def add_one(title: str, url: str) -> SourceResult:
                try:
                    await client.sources.add_url(notebook.id, url, wait=True)
                    return SourceResult(title=title, url=url, ok=True)
                except Exception as e:
                    return SourceResult(title=title, url=url, ok=False, error=str(e))

            tasks = [add_one(t, u) for t, u in raw_sources]
            results = await asyncio.gather(*tasks)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    ok_count = sum(1 for r in results if r.ok)
    return RunResponse(
        notebook_id=notebook.id,
        notebook_url=f"https://notebooklm.google.com/notebook/{notebook.id}",
        notebook_name=notebook_name,
        sources=list(results),
        success_count=ok_count,
        fail_count=len(results) - ok_count,
    )


# ---- ログインページ --------------------------------------------------
LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NotebookLM Auto Adder - ログイン</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f5f5f7; color: #1d1d1f;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
    .card { background: #fff; border-radius: 20px; padding: 40px 32px; width: 100%; max-width: 360px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08); text-align: center; }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
    p { color: #6e6e73; font-size: 14px; margin-bottom: 32px; }
    .google-btn { display: flex; align-items: center; justify-content: center; gap: 12px;
                  width: 100%; padding: 16px; background: #fff; border: 1.5px solid #d2d2d7;
                  border-radius: 12px; font-size: 16px; font-weight: 600; cursor: pointer;
                  text-decoration: none; color: #1d1d1f; transition: background 0.15s; }
    .google-btn:hover { background: #f5f5f7; }
    .google-icon { width: 20px; height: 20px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>📚 NotebookLM<br>Auto Adder</h1>
    <p>続けるには Google アカウントでログインしてください</p>
    <a href="/auth/start" class="google-btn">
      <svg class="google-icon" viewBox="0 0 24 24">
        <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
        <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
        <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
        <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
      </svg>
      Google でログイン
    </a>
  </div>
</body>
</html>"""


# ---- メイン UI -------------------------------------------------------
HTML_UI = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NotebookLM Auto Adder</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; }
    h1 { font-size: 22px; font-weight: 700; }
    .user-info { text-align: right; }
    .user-email { font-size: 11px; color: #6e6e73; margin-bottom: 4px; }
    .logout { font-size: 12px; color: #ff3b30; text-decoration: none; }
    label { display: block; font-size: 13px; font-weight: 600; color: #6e6e73; margin-bottom: 6px; }
    input, select { width: 100%; padding: 14px; border: 1px solid #d2d2d7; border-radius: 12px;
                    font-size: 16px; background: #fff; margin-bottom: 16px; appearance: none; }
    input:focus, select:focus { outline: none; border-color: #0071e3; }
    button { width: 100%; padding: 16px; background: #0071e3; color: #fff; border: none;
             border-radius: 12px; font-size: 17px; font-weight: 600; cursor: pointer; }
    button:disabled { background: #a0c4f1; }
    #result { margin-top: 24px; display: none; }
    .card { background: #fff; border-radius: 16px; padding: 20px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
    .nb-link { display: block; text-align: center; padding: 14px; background: #34c759;
               color: #fff; border-radius: 12px; font-weight: 600; text-decoration: none; margin-bottom: 16px; }
    .stats { display: flex; gap: 12px; margin-bottom: 16px; }
    .stat { flex: 1; text-align: center; padding: 12px; border-radius: 12px; background: #f5f5f7; }
    .stat .num { font-size: 28px; font-weight: 700; }
    .stat .lbl { font-size: 12px; color: #6e6e73; }
    .ok { color: #34c759; } .ng { color: #ff3b30; }
    .source-item { padding: 10px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
    .source-item:last-child { border-bottom: none; }
    .source-title { font-weight: 500; margin-bottom: 2px; }
    .source-url { color: #0071e3; font-size: 12px; word-break: break-all; }
    .source-err { color: #ff3b30; font-size: 12px; }
    .spinner { text-align: center; padding: 40px; color: #6e6e73; }
  </style>
</head>
<body>
  <div class="header">
    <h1>📚 NotebookLM<br>Auto Adder</h1>
    <div class="user-info">
      <div class="user-email">__USER_EMAIL__</div>
      <a href="/logout" class="logout">ログアウト</a>
    </div>
  </div>

  <label>検索キーワード</label>
  <input id="keyword" type="text" placeholder="例: Claude Code Skills" />

  <label>ノートブック名（空欄でキーワードと同じ）</label>
  <input id="name" type="text" placeholder="省略可" />

  <label>取得件数</label>
  <select id="num">
    <option value="10">10件</option>
    <option value="20" selected>20件</option>
    <option value="30">30件</option>
  </select>

  <label>検索地域</label>
  <select id="region">
    <option value="jp-jp" selected>日本語（jp-jp）</option>
    <option value="us-en">英語（us-en）</option>
  </select>

  <button id="btn" onclick="run()">検索して追加する</button>

  <div id="result"></div>

  <script>
    async function run() {
      const keyword = document.getElementById('keyword').value.trim();
      if (!keyword) { alert('キーワードを入力してください'); return; }
      const btn = document.getElementById('btn');
      const result = document.getElementById('result');

      btn.disabled = true;
      btn.textContent = '処理中...';
      result.style.display = 'block';
      result.innerHTML = '<div class="spinner">🔍 検索してNotebookLMに追加中…<br>しばらくお待ちください</div>';

      try {
        const res = await fetch('/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            keyword,
            notebook_name: document.getElementById('name').value.trim(),
            num_results: parseInt(document.getElementById('num').value),
            region: document.getElementById('region').value,
          }),
        });

        if (!res.ok) {
          if (res.status === 307) { location.href = '/login'; return; }
          let errMsg = `HTTP ${res.status}: ${res.statusText}`;
          try {
            const err = await res.json();
            errMsg = err.detail || errMsg;
          } catch {
            try { errMsg = await res.text(); } catch {}
          }
          throw new Error(errMsg);
        }

        const data = await res.json();
        result.innerHTML = `
          <a class="nb-link" href="${data.notebook_url}" target="_blank">
            📖 ${data.notebook_name} を開く
          </a>
          <div class="stats">
            <div class="stat"><div class="num ok">${data.success_count}</div><div class="lbl">成功</div></div>
            <div class="stat"><div class="num ng">${data.fail_count}</div><div class="lbl">失敗</div></div>
          </div>
          <div class="card">
            ${data.sources.map(s => `
              <div class="source-item">
                <div class="source-title">${s.ok ? '✓' : '✗'} ${s.title}</div>
                <div class="source-url">${s.url}</div>
                ${s.error ? `<div class="source-err">${s.error}</div>` : ''}
              </div>
            `).join('')}
          </div>`;
      } catch (e) {
        result.innerHTML = `<div class="card" style="color:#ff3b30">エラー: ${e.message}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = '検索して追加する';
      }
    }
  </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
