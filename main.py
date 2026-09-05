"""
ربات مدیریتی تلگرام برای آپلود و اجرای خودکار بات‌های دیگر (Python / Node.js / PHP)
با نصب خودکار وابستگی‌ها و تفکیک دسترسی ادمین/کاربر عادی.

نقش‌ها:
- ادمین (مالک): آیدی عددی تلگرامش تو متغیر محیطی ADMIN_IDS ست شده. فقط ادمین می‌تونه
  بات هر کاربری رو ببینه/متوقف کنه/لاگش رو بخونه، و کاربرا رو بن/آنبن کنه.
- کاربر عادی: فقط می‌تونه بات خودش رو آپلود، اجرا، لیست، لاگ‌گیری و متوقف کنه.
  کاربر بن‌شده اصلاً نمی‌تونه بات جدید بفرسته یا اجرا کنه.

نحوه کار (برای همه):
1. فایل بات (.py / .js / .php) فرستاده می‌شه.
2. اختیاری: فایل وابستگی مربوطه (requirements.txt / package.json / composer.json).
3. توکن API بات به‌صورت پیام متنی جدا فرستاده می‌شه.
4. ربات خودکار وابستگی‌ها رو نصب و بات رو اجرا می‌کنه.

نیازمندی‌ها: python-telegram-bot >= 20
"""

import os
import re
import json
import signal
import asyncio
import logging
import subprocess
from pathlib import Path

from telegram import Update, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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

# آیدی عددی تلگرام ادمین(ها) - چند تا رو با کاما جدا کن، مثلا: "111111,222222"
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip()
}

MAX_FILE_SIZE_MB = 20
INSTALL_TIMEOUT_SEC = 300

LANG_CONFIG = {
    ".py": {"dep_file": "requirements.txt", "kind": "python"},
    ".js": {"dep_file": "package.json", "kind": "node"},
    ".php": {"dep_file": "composer.json", "kind": "php"},
}

BANNED_FILE = BASE_DIR / "banned.json"

BASIC_COMMANDS = [
    BotCommand("start", "شروع / راهنما"),
    BotCommand("myid", "نمایش آیدی عددی من"),
    BotCommand("list", "لیست بات‌های من"),
    BotCommand("logs", "لاگ یه بات من"),
    BotCommand("stop", "توقف یه بات من"),
]

ADMIN_ONLY_COMMANDS = [
    BotCommand("listall", "لیست بات‌های همه‌ی کاربرا"),
    BotCommand("stopuser", "توقف بات یه کاربر خاص"),
    BotCommand("logsuser", "لاگ بات یه کاربر خاص"),
    BotCommand("ban", "مسدود کردن کاربر"),
    BotCommand("unban", "رفع مسدودیت کاربر"),
    BotCommand("banned", "لیست کاربرای مسدود"),
]

# {user_id: {"file": Path, "dep": Path|None, "stage": "await_dep_or_token"}}
pending: dict[int, dict] = {}

# user_id -> {name: {"process": Popen, "log": Path}}
running_bots: dict[int, dict[str, dict]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def load_banned() -> set[int]:
    if BANNED_FILE.exists():
        try:
            return set(json.loads(BANNED_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_banned(banned: set[int]):
    BANNED_FILE.write_text(json.dumps(sorted(banned)))


banned_users: set[int] = load_banned()


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
# دستورات عمومی
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = [
        "سلام! 👋",
        "",
        "1️⃣ فایل بات (.py / .js / .php) رو بفرست.",
        "2️⃣ اگه وابستگی خاصی لازمه، requirements.txt / package.json / composer.json رو هم بفرست (اختیاری).",
        "3️⃣ توکن API بات رو به‌صورت پیام متنی جدا بفرست.",
        "",
        "دستورات من:",
        "/list — لیست بات‌های خودت",
        "/logs <نام_فایل> — لاگ بات خودت",
        "/stop <نام_فایل> — توقف بات خودت",
        "/myid — نمایش آیدی عددی تلگرامت",
    ]
    if is_admin(user_id):
        lines += [
            "",
            "🔑 دستورات ادمین:",
            "/listall — لیست بات‌های همه‌ی کاربرا",
            "/stopuser <user_id> <نام_فایل> — توقف بات هر کاربری",
            "/logsuser <user_id> <نام_فایل> — لاگ بات هر کاربری",
            "/ban <user_id> — مسدود کردن یه کاربر (و توقف بات‌هاش)",
            "/unban <user_id> — رفع مسدودیت",
            "/banned — لیست کاربرای مسدود",
        ]
    await update.message.reply_text("\n".join(lines))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"آیدی عددی تلگرام شما: `{update.effective_user.id}`", parse_mode="Markdown")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    user_id = update.effective_user.id

    if user_id in banned_users:
        await update.message.reply_text("⛔️ شما مسدود شده‌اید و نمی‌تونید بات جدید بفرستید.")
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"فایل نباید بیشتر از {MAX_FILE_SIZE_MB}MB باشه.")
        return

    raw_name = doc.file_name or "file"
    filename = sanitize_name(raw_name)
    ext = Path(filename).suffix.lower()

    state = pending.get(user_id)

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
                "این فایل انتظار نمی‌رفت. اگه فایل وابستگی نیست، فقط توکن رو به‌صورت متن بفرست."
            )
            return

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

    if user_id in banned_users:
        await update.message.reply_text("⛔️ شما مسدود شده‌اید.")
        return

    state = pending.get(user_id)
    if not state:
        await update.message.reply_text("اول یه فایل بات بفرست، بعد توکنش رو.")
        return

    pending.pop(user_id, None)
    await launch_bot(update, user_id, state["file"], state.get("dep"), token=text)


# ---------------------------------------------------------------------------
# نصب وابستگی‌ها و اجرا
# ---------------------------------------------------------------------------

def run_and_log(cmd: list[str], cwd: Path, log_path: Path, timeout: int) -> tuple[bool, str]:
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


def _stop_process(user_id: int, name: str) -> bool:
    bots = running_bots.get(user_id, {})
    if name not in bots:
        return False
    proc = bots[name]["process"]
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    return True


# ---------------------------------------------------------------------------
# دستورات کاربر عادی (فقط روی بات‌های خودش)
# ---------------------------------------------------------------------------

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
    if _stop_process(user_id, name):
        await update.message.reply_text(f"بات «{name}» متوقف شد.")
    else:
        await update.message.reply_text("همچین باتی پیدا نشد.")


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
    await _reply_tail_log(update, bots[name]["log"])


async def _reply_tail_log(update: Update, log_path: Path):
    if not log_path.exists():
        await update.message.reply_text("لاگی هنوز ثبت نشده.")
        return
    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 3000))
        tail = f.read().decode(errors="ignore")
    await update.message.reply_text(f"آخرین لاگ‌ها:\n```\n{tail[-3500:]}\n```", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# دستورات ادمین (روی بات‌های همه‌ی کاربرا)
# ---------------------------------------------------------------------------

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔️ این دستور فقط برای ادمینه.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running_bots:
        await update.message.reply_text("هیچ باتی در حال اجرا نیست.")
        return
    lines = []
    for uid, bots in running_bots.items():
        if not bots:
            continue
        ban_mark = " 🚫" if uid in banned_users else ""
        lines.append(f"👤 کاربر {uid}{ban_mark}:")
        for name, info in bots.items():
            proc = info["process"]
            status = "🟢" if proc.poll() is None else f"🔴({proc.returncode})"
            lines.append(f"   • {name} — {status}")
    await update.message.reply_text("\n".join(lines) if lines else "هیچ باتی در حال اجرا نیست.")


@admin_only
async def stop_user_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /stopuser <user_id> <نام_فایل>")
        return
    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("آیدی کاربر باید عدد باشه.")
        return
    name = sanitize_name(context.args[1])
    if _stop_process(target_uid, name):
        await update.message.reply_text(f"بات «{name}» متعلق به کاربر {target_uid} متوقف شد.")
    else:
        await update.message.reply_text("همچین باتی پیدا نشد.")


@admin_only
async def logs_user_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /logsuser <user_id> <نام_فایل>")
        return
    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("آیدی کاربر باید عدد باشه.")
        return
    name = sanitize_name(context.args[1])
    bots = running_bots.get(target_uid, {})
    if name not in bots:
        await update.message.reply_text("همچین باتی پیدا نشد.")
        return
    await _reply_tail_log(update, bots[name]["log"])


@admin_only
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /ban <user_id>")
        return
    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("آیدی کاربر باید عدد باشه.")
        return

    banned_users.add(target_uid)
    save_banned(banned_users)

    stopped = []
    for name, info in running_bots.get(target_uid, {}).items():
        if info["process"].poll() is None:
            info["process"].send_signal(signal.SIGTERM)
            stopped.append(name)

    msg = f"کاربر {target_uid} مسدود شد."
    if stopped:
        msg += f"\nبات‌های متوقف‌شده: {', '.join(stopped)}"
    await update.message.reply_text(msg)


@admin_only
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /unban <user_id>")
        return
    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("آیدی کاربر باید عدد باشه.")
        return
    banned_users.discard(target_uid)
    save_banned(banned_users)
    await update.message.reply_text(f"کاربر {target_uid} رفع مسدودیت شد.")


@admin_only
async def list_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not banned_users:
        await update.message.reply_text("هیچ کاربر مسدودی وجود نداره.")
        return
    await update.message.reply_text("کاربرای مسدود:\n" + "\n".join(str(u) for u in sorted(banned_users)))


async def post_init(application: Application):
    """منوی '/' رو برای همه پایه، و برای ادمین(ها) با دستورات اضافه ست می‌کنه."""
    await application.bot.set_my_commands(BASIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.set_my_commands(
                BASIC_COMMANDS + ADMIN_ONLY_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:
            logger.warning(f"تنظیم منوی ادمین برای {admin_id} شکست خورد: {e}")


def main():
    if not MANAGER_TOKEN:
        raise SystemExit("متغیر محیطی MANAGER_BOT_TOKEN تنظیم نشده.")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS تنظیم نشده - هیچکس دسترسی ادمین نخواهد داشت. با /myid آیدی‌تو بگیر و تو Railway ست کن.")

    app = Application.builder().token(MANAGER_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("list", list_bots))
    app.add_handler(CommandHandler("stop", stop_bot))
    app.add_handler(CommandHandler("logs", show_logs))

    app.add_handler(CommandHandler("listall", list_all))
    app.add_handler(CommandHandler("stopuser", stop_user_bot))
    app.add_handler(CommandHandler("logsuser", logs_user_bot))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("banned", list_banned))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Manager bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
