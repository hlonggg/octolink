"""
Octolink Code Monitor Bot - v3.2
Sửa port cho Render
"""

import asyncio
import json
import logging
import re
import random
import os
from datetime import datetime
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ======================== CẤU HÌNH ========================
BOT_TOKEN      = "8801698234:AAFJQACza2NqYnYs0CKvmTP8-F4S7ZOyK-c"
MY_EMAIL       = "hoanglongphan711@gmail.com"
MY_PASSWORD    = "longdzvcl12@"
LOGIN_URL      = "https://kiemcom.site/login"
TASKS_URL      = "https://kiemcom.site/dashboard/tasks"
TARGET_CHAT_ID = "-1003948095853"

CHECK_INTERVAL  = 300     # giây (5 phút)
MAX_RETRIES     = 3
RETRY_DELAY     = 10
PAGE_TIMEOUT    = 30_000
STATE_FILE      = "bot_state.json"

# ======================== LOGGING ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ======================== STATE ========================
def load_state() -> list[str]:
    try:
        if Path(STATE_FILE).exists():
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def save_state(codes: list[str]):
    try:
        Path(STATE_FILE).write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"Không lưu state: {e}")

last_known_codes: list[str] = load_state()
consecutive_failures: int = 0

# ======================== BROWSER SINGLETON ========================
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_logged_in: bool = False

async def get_browser_context() -> tuple[Browser, BrowserContext]:
    global _playwright, _browser, _context

    if _browser and _browser.is_connected() and _context:
        return _browser, _context

    log.info("Khởi động browser...")
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--disable-gpu",
            "--mute-audio",
        ],
    )
    _context = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )

    # Block ảnh, font, media, stylesheet → load nhanh hơn
    await _context.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in {"image", "media", "font", "stylesheet"}
        else route.continue_(),
    )

    return _browser, _context

async def close_browser():
    global _browser, _context, _playwright, _logged_in
    try:
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    _browser = _context = _playwright = None
    _logged_in = False

# ======================== LOGIN ========================
async def ensure_logged_in(page: Page) -> bool:
    global _logged_in

    if _logged_in:
        try:
            await page.goto(TASKS_URL, wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(1)
            if "/login" not in page.url:
                log.info(f"Session OK")
                return True
            log.info("Session hết hạn, đăng nhập lại...")
        except Exception:
            pass
        _logged_in = False

    log.info("Đăng nhập...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    try:
        await page.wait_for_selector("#email", state="visible", timeout=15_000)
        await page.wait_for_selector("#password", state="visible", timeout=15_000)
    except PlaywrightTimeout:
        log.error("Form login không xuất hiện!")
        return False

    await page.locator("#email").click()
    await page.locator("#email").fill("")
    await page.locator("#email").type(MY_EMAIL, delay=30)

    await page.locator("#password").click()
    await page.locator("#password").fill("")
    await page.locator("#password").type(MY_PASSWORD, delay=30)

    await asyncio.sleep(random.uniform(0.4, 0.8))

    # Click submit
    submitted = False
    for btn_selector in [
        "button[type='submit']",
        "button:has-text('Đăng nhập')",
        "input[type='submit']",
    ]:
        try:
            btn = page.locator(btn_selector)
            if await btn.count() > 0:
                await btn.first.click()
                submitted = True
                break
        except Exception:
            continue

    if not submitted:
        await page.locator("#password").press("Enter")

    # Chờ thoát login
    deadline = asyncio.get_event_loop().time() + 15
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        if "/login" not in page.url:
            log.info(f"Đăng nhập thành công")
            _logged_in = True
            return True

    log.error("Login timeout")
    return False

# ======================== SCRAPE ========================
CODE_SELECTORS = [
    'span.text-xs.font-bold.tracking-wider.font-mono',
    'span.font-mono.font-bold',
    'span.font-mono',
    '[class*="font-mono"]',
]

CODE_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9\-_]{2,19}$')

def looks_like_code(t: str) -> bool:
    t = t.strip()
    return bool(t and CODE_PATTERN.match(t))

async def _scrape_codes(page: Page) -> list[str]:
    if "/tasks" not in page.url:
        await page.goto(TASKS_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await asyncio.sleep(0.5)

    if "/login" in page.url:
        raise RuntimeError("Redirect về login")

    # Chờ element
    try:
        await page.wait_for_selector(CODE_SELECTORS[0], timeout=10_000)
    except PlaywrightTimeout:
        for sel in CODE_SELECTORS[1:]:
            try:
                await page.wait_for_selector(sel, timeout=5_000)
                break
            except PlaywrightTimeout:
                continue

    # Lấy text bằng JS
    texts: list[str] = await page.evaluate(f"""
        () => {{
            const selectors = {json.dumps(CODE_SELECTORS)};
            for (const sel of selectors) {{
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {{
                    return Array.from(els).map(e => e.innerText.trim()).filter(t => t.length > 0);
                }}
            }}
            return [];
        }}
    """)

    codes = list(dict.fromkeys(t for t in texts if looks_like_code(t)))

    # Fallback regex
    if not codes:
        html = await page.content()
        candidates = re.findall(r'>([A-Z0-9][A-Z0-9\-_]{3,19})<', html)
        codes = list(dict.fromkeys(c for c in candidates if looks_like_code(c)))

    log.info(f"Tìm được {len(codes)} mã")
    return codes

# ======================== MAIN SCRAPE ========================
async def get_octolink_codes() -> list[str]:
    global consecutive_failures, _logged_in

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(f"Quét lần {attempt}/{MAX_RETRIES}")
        page = None
        try:
            _, ctx = await get_browser_context()
            page = await ctx.new_page()

            ok = await ensure_logged_in(page)
            if not ok:
                raise RuntimeError("Đăng nhập thất bại")

            codes = await _scrape_codes(page)
            await page.close()

            consecutive_failures = 0
            return codes

        except Exception as e:
            log.warning(f"Lần {attempt} lỗi: {e}")
            _logged_in = False
            try:
                if page:
                    await page.close()
            except Exception:
                pass

            if "closed" in str(e).lower():
                await close_browser()

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

    consecutive_failures += 1
    log.error(f"Thất bại {consecutive_failures} lần")
    return []

# ======================== TELEGRAM ========================
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang quét...")
    codes = await get_octolink_codes()

    if not codes:
        await update.message.reply_text("❌ Không lấy được mã. Kiểm tra mật khẩu hoặc site đổi giao diện.")
        return

    lines = "\n".join(f"🎯 `{c}`" for i, c in enumerate(codes, 1))
    await update.message.reply_text(
        f"📊 *Live Octolink:*\n\n{lines}\n\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}",
        parse_mode="Markdown",
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    codes = last_known_codes
    if codes:
        lines = "\n".join(f"- `{c}`" for c in codes)
        msg = f"📋 *Cache ({len(codes)} mã):*\n{lines}"
    else:
        msg = "📋 Chưa có cache. Gõ /check"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ======================== AUTO CHECK ========================
async def auto_check_task(context: ContextTypes.DEFAULT_TYPE):
    global last_known_codes, consecutive_failures
    chat_id = context.job.data

    log.info("Auto-check...")
    current_codes = await get_octolink_codes()

    if not current_codes:
        if consecutive_failures >= 3:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⚠️ *CẢNH BÁO:* Thất bại *{consecutive_failures} lần liên tiếp*!\n"
                        f"Kiểm tra mật khẩu hoặc site thay đổi."
                    ),
                    parse_mode="Markdown",
                )
                consecutive_failures = 0
            except Exception as e:
                log.error(f"Gửi alert lỗi: {e}")
        return

    current_set = set(current_codes)
    old_set = set(last_known_codes)

    if not old_set:
        log.info(f"Lần đầu: cache {len(current_codes)} mã")
        last_known_codes = current_codes
        save_state(current_codes)
        return

    if current_set != old_set:
        new_items = current_set - old_set
        removed_items = old_set - current_set
        parts = ["🚨 *THAY ĐỔI MÃ SỐ!*\n"]
        if new_items:
            parts.append("➕ *Mới:*\n" + "\n".join(f"  `{c}`" for c in sorted(new_items)))
        if removed_items:
            parts.append("➖ *Biến mất:*\n" + "\n".join(f"  `{c}`" for c in sorted(removed_items)))
        parts.append("\n📌 *Hiện tại:*\n" + "\n".join(f"- `{c}`" for c in current_codes))
        parts.append(f"\n⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
        try:
            await context.bot.send_message(chat_id=chat_id, text="\n".join(parts), parse_mode="Markdown")
        except Exception as e:
            log.error(f"Gửi thông báo lỗi: {e}")
    else:
        log.info("Không đổi")

    last_known_codes = current_codes
    save_state(current_codes)

# ======================== FLASK WEB SERVER ========================
from flask import Flask, Response

flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return Response("Bot is running", status=200, mimetype='text/plain')

@flask_app.route('/ping')
def ping():
    return Response("pong", status=200, mimetype='text/plain')

def run_flask():
    # Lấy port từ biến môi trường Render (mặc định 10000)
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ======================== MAIN ========================
def main():
    log.info("🤖 Bot khởi động...")

    # Chạy Flask trong thread riêng
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info(f"✅ Flask web server chạy trên cổng {os.environ.get('PORT', 10000)}, ping /ping để giữ bot thức")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("status", status_command))

    if app.job_queue and TARGET_CHAT_ID:
        jq = app.job_queue
        jq.run_once(auto_check_task, when=10, data=TARGET_CHAT_ID)
        jq.run_repeating(auto_check_task, interval=CHECK_INTERVAL, first=CHECK_INTERVAL, data=TARGET_CHAT_ID)
        log.info(f"✅ Auto-check mỗi {CHECK_INTERVAL}s")
    else:
        log.warning("job_queue không chạy!")

    log.info("Bot sẵn sàng. /check để quét, /status xem cache")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        try:
            asyncio.run(close_browser())
        except RuntimeError:
            pass

if __name__ == "__main__":
    main()
