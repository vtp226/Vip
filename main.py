"""
ربات مدیریتی تلگرام برای آپلود و اجرای خودکار بات‌های دیگر (Python / Node.js / PHP)
با نصب خودکار وابستگی‌ها (requirements.txt / package.json / composer.json)

نحوه کار:
1. کاربر فایل بات (.py / .js / .php) را می‌فرستد.
2. اختیاری: فایل وابستگی مربوطه را می‌فرستد
   (requirements.txt برای پایتون، package.json برای Node، composer.json برای PHP)
3. کاربر توکن API بات جدید را به‌صورت پیام متنی جدا می‌فرستد.
4. ربات به‌صورت خودکار:
   - برای پایتون: یک virtualenv مجزا می‌سازد و pip install می‌زند.
   - برای Node: npm install می‌زند.
   - برای PHP: در صورت وجود composer.json، composer install می‌زند.
   سپس فایل را با interpreter مناسب و توکن به‌عنوان متغیر محیطی اجرا می‌کند.
5. کاربر می‌تواند بات‌های در حال اجرا را لیست کند، لاگ بگیرد یا متوقف کند.

نیازمندی‌ها: python-telegram-bot >= 20
"""

import os
import re
import signal
import asyncio
import logging
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

MANAGER_TOKEN = os.environ.get("MANAGER_BOT_TOKEN")
BASE_DIR = Path(os.environ.get("BOTS_DIR", "/data/bots"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_MB = 20
INSTALL_TIMEOUT_SEC = 300  # حداکثر زمان مجاز برای pip/npm/composer install

# پسوند فایل اصلی -> (دستور اجرا بدون venv، نام فایل وابستگی، شناسه نوع پروژه)
LANG_CONFIG = {
    ".py": {"dep_file": "requirements.txt", "kind": "python"},
    ".js": {"dep_file": "package.json", "kind": "node"},
    ".php": {"dep_file": "composer.json", "kind": "php"},
}

# حافظه‌ی موقت وضعیت هر کاربر در جریان آپلود
# {"file": Path, "dep": Path|None, "stage": "await_dep_or_token"}
pending: dict[int, dict] = {}

# بات‌های در حال اجرا: user_id -> {name: {"process": Popen, "log": Path}}
running_bots: dict[int, dict[str, dict]] = {}


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    return name[:60]


def user_dir(user_id: int) -> Path:
    d = BASE_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_log(log_path: Path, text: str):
    with open(log_path, "a", encoding="utf-8", errors="ignore") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


# ---------------------------------------------------------------------------
# دستورات
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n\n"
        "1️⃣ فایل بات (.py / .js / .php) رو بفرست.\n"
        "2️⃣ اگه وابستگی خاصی لازمه، فایل requirements.txt / package.json / composer.json "
        "رو هم بفرست (اختیاری).\n"
        "3️⃣ توکن API بات رو به‌صورت پیام متنی جدا بفرست.\n\n"
        "ربات خودش وابستگی‌ها رو نصب می‌کنه و بات رو اجرا می‌کنه.\n\n"
        "دستورات:\n"
        "/list — لیست بات‌های در حال اجرا\n"
        "/logs <نام_فایل> — نمایش آخرین لاگ‌ها\n"
        "/stop <نام_فایل> — متوقف کردن یک بات"
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    user_id = update.effective_user.id

    if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"فایل نباید بیشتر از {MAX_FILE_SIZE_MB}MB باشه.")
        return

    raw_name = doc.file_name or "file"
    filename = sanitize_name(raw_name)
    ext = Path(filename).suffix.lower()

    state = pending.get(user_id)

    # اگر منتظر فایل وابستگی هستیم (چون فایل اصلی قبلا اومده)
    if state and state["stage"] == "await_dep_or_token":
        expected_dep = LANG_CONFIG[state["file"].suffix.lower()]["dep_file"]
        if raw_name.lower() == expected_dep or filename.lower() == expected_dep:
            dep_dest = state["file"].parent / expected_dep
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(dep_dest))
            state["dep"] = dep_dest
            await update.message.reply_text(
                f"✅ فایل «{expected_dep}» دریافت شد.\nحالا توکن API بات رو بفرست."
            )
            return
        else:
            await update.message.reply_text(
                f"این فایل انتظار نمی‌رفت. اگه فایل وابستگی نیست، فقط توکن رو به‌صورت متن بفرست."
            )
            return

    # فایل اصلی بات
    if ext not in LANG_CONFIG:
        await update.message.reply_text(
            f"پسوند {ext or 'نامشخص'} پشتیبانی نمی‌شه. فقط: {', '.join(LANG_CONFIG)}"
        )
        return

    dest_dir = user_dir(user_id) / Path(filename).stem
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    tg_file = await doc.get_file()
    await tg_file.download_to_drive(custom_path=str(dest_path))

    pending[user_id] = {"file": dest_path, "dep": None, "stage": "await_dep_or_token"}

    dep_name = LANG_CONFIG[ext]["dep_file"]
    await update.message.reply_text(
        f"فایل «{filename}» دریافت شد ✅\n\n"
        f"اگه این بات به کتابخونه/پکیج خاصی نیاز داره، فایل «{dep_name}» رو بفرست.\n"
        "در غیر این صورت، همین الان توکن API بات رو به‌صورت پیام متنی بفرست."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    state = pending.get(user_id)
    if not state:
        await update.message.reply_text("اول یه فایل بات بفرست، بعد توکنش رو.")
        return

    # هر متنی غیر از فایل وابستگی، توکن در نظر گرفته می‌شه
    pending.pop(user_id, None)
    await launch_bot(update, user_id, state["file"], state.get("dep"), token=text)


# ---------------------------------------------------------------------------
# نصب وابستگی‌ها و اجرا
# ---------------------------------------------------------------------------

def run_and_log(cmd: list[str], cwd: Path, log_path: Path, timeout: int) -> tuple[bool, str]:
    """یک دستور نصب را اجرا می‌کند و خروجی را در لاگ می‌نویسد. (blocking - در thread جدا صدا زده می‌شود)"""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        output = (result.stdout or "") + (result.stderr or "")
        append_log(log_path, f"$ {' '.join(cmd)}\n{output}")
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        append_log(log_path, f"$ {' '.join(cmd)}\n[TIMEOUT after {timeout}s]")
        return False, f"دستور {cmd[0]} بیشتر از {timeout} ثانیه طول کشید و متوقف شد."
    except FileNotFoundError:
        append_log(log_path, f"$ {' '.join(cmd)}\n[NOT FOUND]")
        return False, f"دستور {cmd[0]} روی سرور نصب نیست."


async def launch_bot(update: Update, user_id: int, file_path: Path, dep_path: Path | None, token: str):
    name = file_path.stem
    ext = file_path.suffix.lower()
    kind = LANG_CONFIG[ext]["kind"]
    folder = file_path.parent
    log_path = folder / "run.log"

    running_bots.setdefault(user_id, {})
    if name in running_bots[user_id]:
        old = running_bots[user_id][name]["process"]
        if old.poll() is None:
            old.terminate()

    env = os.environ.copy()
    env["TOKEN"] = token
    env["BOT_TOKEN"] = token

    # -----------------------------------------------------------------
    # نصب وابستگی‌ها (اگر فایل وابستگی داده شده)
    # -----------------------------------------------------------------
    python_bin = "python3"
    if kind == "python":
        venv_dir = folder / "venv"
        if not venv_dir.exists():
            await update.message.reply_text("⏳ در حال آماده‌سازی محیط پایتون (venv)...")
            ok, out = await asyncio.to_thread(run_and_log, ["python3", "-m", "venv", str(venv_dir)], folder, log_path, 120)
            if not ok:
                await update.message.reply_text(f"❌ ساخت venv شکست خورد:\n```\n{out[-1500:]}\n```", parse_mode="Markdown")
                return
        python_bin = str(venv_dir / "bin" / "python")
        pip_bin = str(venv_dir / "bin" / "pip")

        # همیشه چند کتابخونه‌ی رایج بات‌سازی رو نصب می‌کنیم تا اکثر بات‌ها بدون requirements.txt هم کار کنن
        await update.message.reply_text("⏳ در حال نصب کتابخونه‌های پایه (ممکنه کمی طول بکشه)...")
        await asyncio.to_thread(run_and_log, [pip_bin, "install", "--upgrade", "pip"], folder, log_path, INSTALL_TIMEOUT_SEC)
        await asyncio.to_thread(
            run_and_log,
            [pip_bin, "install", "python-telegram-bot==21.6", "pyTelegramBotAPI", "aiogram", "requests"],
            folder, log_path, INSTALL_TIMEOUT_SEC,
        )

        if dep_path and dep_path.exists():
            await update.message.reply_text("⏳ در حال نصب وابستگی‌های اختصاصی از requirements.txt...")
            ok, out = await asyncio.to_thread(run_and_log, [pip_bin, "install", "-r", str(dep_path)], folder, log_path, INSTALL_TIMEOUT_SEC)
            if not ok:
                await update.message.reply_text(
                    f"⚠️ نصب requirements.txt با خطا مواجه شد (لاگ کامل با /logs {name} قابل مشاهده‌ست). "
                    "در صورت امکان با کتابخونه‌های پایه اجرا می‌کنم."
                )

    elif kind == "node":
        if dep_path and dep_path.exists():
            await update.message.reply_text("⏳ در حال نصب پکیج‌های Node (npm install)...")
            ok, out = await asyncio.to_thread(run_and_log, ["npm", "install"], folder, log_path, INSTALL_TIMEOUT_SEC)
            if not ok:
                await update.message.reply_text(f"❌ npm install شکست خورد:\n```\n{out[-1500:]}\n```", parse_mode="Markdown")
                return
        else:
            # حتی بدون package.json اختصاصی، کتابخونه‌های رایج بات‌سازی رو نصب می‌کنیم
            await asyncio.to_thread(run_and_log, ["npm", "init", "-y"], folder, log_path, 30)
            await update.message.reply_text("⏳ در حال نصب کتابخونه‌های پایه Node...")
            await asyncio.to_thread(run_and_log, ["npm", "install", "node-telegram-bot-api", "telegraf", "axios"], folder, log_path, INSTALL_TIMEOUT_SEC)

    elif kind == "php":
        if dep_path and dep_path.exists():
            await update.message.reply_text("⏳ در حال نصب پکیج‌های PHP (composer install)...")
            ok, out = await asyncio.to_thread(run_and_log, ["composer", "install"], folder, log_path, INSTALL_TIMEOUT_SEC)
            if not ok:
                await update.message.reply_text(f"❌ composer install شکست خورد:\n```\n{out[-1500:]}\n```", parse_mode="Markdown")
                return

    # -----------------------------------------------------------------
    # اجرای بات
    # -----------------------------------------------------------------
    interpreter = {
        "python": [python_bin, "-u"],
        "node": ["node"],
        "php": ["php"],
    }[kind]

    log_file = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            interpreter + [str(file_path)],
            cwd=str(folder),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        await update.message.reply_text(f"⚠️ اینتراپریتر {interpreter[0]} روی سرور نصب نیست.")
        return

    running_bots[user_id][name] = {"process": proc, "log": log_path}
    await update.message.reply_text(
        f"🚀 بات «{name}» اجرا شد (PID: {proc.pid}).\n"
        f"اگه ۱-۲ دقیقه بعد جواب نداد، با /logs {name} خطا رو چک کن.\n"
        f"برای توقف: /stop {name}"
    )


async def list_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bots = running_bots.get(user_id, {})
    if not bots:
        await update.message.reply_text("هیچ باتی در حال اجرا نیست.")
        return

    lines = []
    for name, info in bots.items():
        proc = info["process"]
        status = "🟢 در حال اجرا" if proc.poll() is None else f"🔴 متوقف (کد {proc.returncode})"
        lines.append(f"• {name} — {status}")
    await update.message.reply_text("\n".join(lines))


async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("استفاده: /stop <نام_فایل>")
        return

    name = sanitize_name(context.args[0])
    bots = running_bots.get(user_id, {})
    if name not in bots:
        await update.message.reply_text("همچین باتی پیدا نشد.")
        return

    proc = bots[name]["process"]
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        await update.message.reply_text(f"بات «{name}» متوقف شد.")
    else:
        await update.message.reply_text("این بات از قبل متوقف بوده.")


async def show_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("استفاده: /logs <نام_فایل>")
        return

    name = sanitize_name(context.args[0])
    bots = running_bots.get(user_id, {})
    if name not in bots:
        await update.message.reply_text("همچین باتی پیدا نشد.")
        return

    log_path = bots[name]["log"]
    if not log_path.exists():
        await update.message.reply_text("لاگی هنوز ثبت نشده.")
        return

    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 3000))
        tail = f.read().decode(errors="ignore")

    await update.message.reply_text(f"آخرین لاگ‌ها:\n```\n{tail[-3500:]}\n```", parse_mode="Markdown")


def main():
    if not MANAGER_TOKEN:
        raise SystemExit("متغیر محیطی MANAGER_BOT_TOKEN تنظیم نشده.")

    app = Application.builder().token(MANAGER_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_bots))
    app.add_handler(CommandHandler("stop", stop_bot))
    app.add_handler(CommandHandler("logs", show_logs))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Manager bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
