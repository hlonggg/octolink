FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cài Playwright và browser
RUN pip install playwright
RUN playwright install chromium
RUN playwright install-deps

COPY bot.py .
COPY runtime.txt .

CMD ["python", "bot.py"]
