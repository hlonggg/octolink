# Octolink Code Monitor Bot

## Biến môi trường cần khai báo trên Railway (Settings → Variables)

Bắt buộc:
- BOT_TOKEN
- MY_EMAIL
- MY_PASSWORD
- TARGET_CHAT_ID

Tùy chọn (có giá trị mặc định):
- LOGIN_URL, TASKS_URL
- CHECK_INTERVAL (mặc định 300s)
- MAX_RETRIES (mặc định 3)
- RETRY_DELAY (mặc định 10s)
- PAGE_TIMEOUT (mặc định 30000ms)

Repo dùng Dockerfile để build (tự cài Playwright + Chromium),
không cần set thêm biến RAILPACK_* hay MISE_* nữa.
