"""
NotebookLM 自動追加 Web アプリ
================================
Railway にデプロイして iPhone のブラウザから使う。

環境変数（Railway に設定）:
  NOTEBOOKLM_AUTH_JSON  : Mac の ~/.notebooklm/storage_state.json の中身
  APP_SECRET            : アクセス用パスワード（任意の文字列）
"""

import asyncio
import os
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from ddgs import DDGS
from notebooklm import NotebookLMClient

APP_SECRET = os.environ.get("APP_SECRET", "")


# ---- 認証 ---------------------------------------------------
security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if APP_SECRET and credentials.credentials != APP_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# ---- 起動時に認証ファイルを書き出す --------------------------
def setup_auth():
    auth_json = os.environ.get("NOTEBOOKLM_AUTH_JSON")
    if not auth_json:
        return
    storage_path = Path.home() / ".notebooklm" / "storage_state.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(auth_json, encoding="utf-8")


setup_auth()


# ---- NotebookLM クライアント初期化 --------------------------
@asynccontextmanager
async def get_notebooklm():
    if not os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        raise RuntimeError("NOTEBOOKLM_AUTH_JSON が設定されていません")
    async with await NotebookLMClient.from_storage() as client:
        yield client


# ---- アプリ -------------------------------------------------
app = FastAPI(title="NotebookLM Auto Adder")


# ---- リクエスト / レスポンス モデル -------------------------
class RunRequest(BaseModel):
    keyword: str
    num_results: int = 20
    notebook_name: str = ""   # 空なら keyword をそのまま使う
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


# ---- エンドポイント -----------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_UI


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest, _=Depends(verify_token)):
    notebook_name = req.notebook_name.strip() or req.keyword

    # 1. DuckDuckGo 検索
    raw_sources: list[tuple[str, str]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(req.keyword, region=req.region, max_results=req.num_results):
            url = r.get("href", "")
            title = r.get("title", url)
            if url:
                raw_sources.append((title, url))

    if not raw_sources:
        raise HTTPException(status_code=404, detail="検索結果が見つかりませんでした")

    # 2. NotebookLM にノートブック作成 & ソース追加
    results: list[SourceResult] = []
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

    ok_count = sum(1 for r in results if r.ok)
    return RunResponse(
        notebook_id=notebook.id,
        notebook_url=f"https://notebooklm.google.com/notebook/{notebook.id}",
        notebook_name=notebook_name,
        sources=list(results),
        success_count=ok_count,
        fail_count=len(results) - ok_count,
    )


# ---- モバイル UI（HTML） ------------------------------------
HTML_UI = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NotebookLM Auto Adder</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 20px; }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 24px; }
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
  <h1>📚 NotebookLM<br>Auto Adder</h1>

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

  <label>アクセストークン</label>
  <input id="token" type="password" placeholder="APP_SECRET の値" />

  <button id="btn" onclick="run()">検索して追加する</button>

  <div id="result"></div>

  <script>
    async function run() {
      const keyword = document.getElementById('keyword').value.trim();
      if (!keyword) { alert('キーワードを入力してください'); return; }
      const token = document.getElementById('token').value.trim();
      const btn = document.getElementById('btn');
      const result = document.getElementById('result');

      btn.disabled = true;
      btn.textContent = '処理中...';
      result.style.display = 'block';
      result.innerHTML = '<div class="spinner">🔍 検索してNotebookLMに追加中…<br>しばらくお待ちください</div>';

      try {
        const res = await fetch('/run', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
          },
          body: JSON.stringify({
            keyword,
            notebook_name: document.getElementById('name').value.trim(),
            num_results: parseInt(document.getElementById('num').value),
            region: document.getElementById('region').value,
          }),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || res.statusText);
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
