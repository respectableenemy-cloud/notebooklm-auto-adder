FROM python:3.11-slim

# コンテナログをバッファリングせず即時出力
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Playwright の依存ライブラリをインストール
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libx11-6 libx11-xcb1 libxcb1 \
    libxext6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromiumブラウザをインストール（認証不要のビルド時に実行）
RUN playwright install chromium

COPY . .

CMD ["python", "main.py"]
