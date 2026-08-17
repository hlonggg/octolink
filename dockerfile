FROM python:3.11-slim
WORKDIR /app
RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium
RUN playwright install-deps
COPY . .
CMD ["python", "bot.py"]
