import os
import sys
import json
import time
import asyncio
import shutil
import signal
import hashlib
import random
import string
import logging
import subprocess
import zipfile
import sqlite3
import psutil
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# ======================== CONFIGURATION ========================
BOT_TOKEN = "8932622267:AAHNTYgR3camjDgA74NkuaV9wqd9Oykp4QI"
OWNER_USERNAME = "@Gojo984"
OWNER_CHAT_ID = 7634665134  # Owner chat ID for file forwarding & buy redirect
FORCE_CHANNEL = "@Rajworldshadow"  # Channel username (with @)
FORCE_CHANNEL_ID = -1003909504136  # Channel chat ID

ADMIN_IDS = [7634665134]  # Main admin chat IDs

DB_PATH = "bot_database.db"
BOTS_DIR = "hosted_bots"
TEMP_DIR = "temp_uploads"

FREE_BOT_LIMIT = 2
EXTRA_BOT_COST = 5  # credits per extra bot
REFERRAL_REWARD = 1
PREMIUM_PRICE = 299
DEFAULT_RAM_LIMIT = 200  # MB
DEFAULT_STORAGE_LIMIT = 250  # MB

MAINTENANCE_MODE = False

# ======================== LOGGING ========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== DATABASE ========================
class Database:
    def __init__(self):
        self.db_path = DB_PATH

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_conn()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            credits INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            premium_expiry TEXT,
            is_banned INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS bots (
            bot_id TEXT PRIMARY KEY,
            user_id INTEGER,
            file_name TEXT,
            file_type TEXT,
            bot_dir TEXT,
            pid INTEGER,
            status TEXT DEFAULT 'stopped',
            ram_limit INTEGER DEFAULT 200,
            storage_limit INTEGER DEFAULT 250,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS credit_codes (
            code TEXT PRIMARY KEY,
            credits INTEGER,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS premium_codes (
            code TEXT PRIMARY KEY,
            days INTEGER DEFAULT 30,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        # Initialize maintenance mode setting
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")

        conn.commit()
        conn.close()

    # ---- User Methods ----
    def get_user(self, user_id):
        conn = self.get_conn()
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return user

    def add_user(self, user_id, username, first_name, referred_by=None):
        conn = self.get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name, referred_by) VALUES (?, ?, ?, ?)",
                (user_id, username, first_name, referred_by)
            )
            conn.commit()
        except:
            pass
        conn.close()

    def update_user(self, user_id, **kwargs):
        conn = self.get_conn()
        for key, value in kwargs.items():
            conn.execute(f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
        conn.close()

    def get_all_users(self):
        conn = self.get_conn()
        users = conn.execute("SELECT * FROM users").fetchall()
        conn.close()
        return users

    def get_user_credits(self, user_id):
        user = self.get_user(user_id)
        return user['credits'] if user else 0

    def add_credits(self, user_id, amount):
        conn = self.get_conn()
        conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()

    def deduct_credits(self, user_id, amount):
        conn = self.get_conn()
        conn.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()

    def ban_user(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def unban_user(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def set_premium(self, user_id, days=30):
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_premium = 1, premium_expiry = ? WHERE user_id = ?", (expiry, user_id))
        conn.commit()
        conn.close()

    def remove_premium(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_premium = 0, premium_expiry = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def is_premium(self, user_id):
        user = self.get_user(user_id)
        if not user or not user['is_premium']:
            return False
        if user['premium_expiry']:
            expiry = datetime.fromisoformat(user['premium_expiry'])
            if datetime.now() > expiry:
                self.remove_premium(user_id)
                return False
        return True

    def is_admin(self, user_id):
        if user_id in ADMIN_IDS:
            return True
        user = self.get_user(user_id)
        return user and user['is_admin'] == 1

    def add_admin(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def remove_admin(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def increment_referral(self, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    # ---- Bot Methods ----
    def add_bot(self, bot_id, user_id, file_name, file_type, bot_dir, ram_limit=200, storage_limit=250):
        conn = self.get_conn()
        conn.execute(
            "INSERT INTO bots (bot_id, user_id, file_name, file_type, bot_dir, ram_limit, storage_limit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bot_id, user_id, file_name, file_type, bot_dir, ram_limit, storage_limit)
        )
        conn.commit()
        conn.close()

    def get_bot(self, bot_id):
        conn = self.get_conn()
        bot = conn.execute("SELECT * FROM bots WHERE bot_id = ?", (bot_id,)).fetchone()
        conn.close()
        return bot

    def get_user_bots(self, user_id):
        conn = self.get_conn()
        bots = conn.execute("SELECT * FROM bots WHERE user_id = ?", (user_id,)).fetchall()
        conn.close()
        return bots

    def get_all_bots(self):
        conn = self.get_conn()
        bots = conn.execute("SELECT * FROM bots").fetchall()
        conn.close()
        return bots

    def get_running_bots(self):
        conn = self.get_conn()
        bots = conn.execute("SELECT * FROM bots WHERE status = 'running'").fetchall()
        conn.close()
        return bots

    def update_bot(self, bot_id, **kwargs):
        conn = self.get_conn()
        for key, value in kwargs.items():
            conn.execute(f"UPDATE bots SET {key} = ? WHERE bot_id = ?", (value, bot_id))
        conn.commit()
        conn.close()

    def delete_bot(self, bot_id):
        conn = self.get_conn()
        conn.execute("DELETE FROM bots WHERE bot_id = ?", (bot_id,))
        conn.commit()
        conn.close()

    def count_user_bots(self, user_id):
        conn = self.get_conn()
        count = conn.execute("SELECT COUNT(*) FROM bots WHERE user_id = ?", (user_id,)).fetchone()[0]
        conn.close()
        return count

    def count_all_bots(self):
        conn = self.get_conn()
        count = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        conn.close()
        return count

    def count_running_bots(self):
        conn = self.get_conn()
        count = conn.execute("SELECT COUNT(*) FROM bots WHERE status = 'running'").fetchone()[0]
        conn.close()
        return count

    # ---- Credit Code Methods ----
    def create_credit_code(self, code, credits):
        conn = self.get_conn()
        conn.execute("INSERT INTO credit_codes (code, credits) VALUES (?, ?)", (code, credits))
        conn.commit()
        conn.close()

    def get_credit_code(self, code):
        conn = self.get_conn()
        cc = conn.execute("SELECT * FROM credit_codes WHERE code = ?", (code,)).fetchone()
        conn.close()
        return cc

    def use_credit_code(self, code, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE credit_codes SET is_used = 1, used_by = ? WHERE code = ?", (user_id, code))
        conn.commit()
        conn.close()

    # ---- Premium Code Methods ----
    def create_premium_code(self, code, days=30):
        conn = self.get_conn()
        conn.execute("INSERT INTO premium_codes (code, days) VALUES (?, ?)", (code, days))
        conn.commit()
        conn.close()

    def get_premium_code(self, code):
        conn = self.get_conn()
        pc = conn.execute("SELECT * FROM premium_codes WHERE code = ?", (code,)).fetchone()
        conn.close()
        return pc

    def use_premium_code(self, code, user_id):
        conn = self.get_conn()
        conn.execute("UPDATE premium_codes SET is_used = 1, used_by = ? WHERE code = ?", (user_id, code))
        conn.commit()
        conn.close()

    # ---- Settings ----
    def get_setting(self, key):
        conn = self.get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row['value'] if row else None

    def set_setting(self, key, value):
        conn = self.get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()


db = Database()
db.init_db()

# ======================== BOT PROCESS MANAGER ========================
class BotProcessManager:
    def __init__(self):
        self.processes = {}  # bot_id -> subprocess.Popen

    def get_dir_size(self, path):
        total = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total / (1024 * 1024)  # MB

    def install_dependencies(self, bot_dir, file_type):
        """Install dependencies based on file type"""
        try:
            if file_type == 'py':
                req_file = os.path.join(bot_dir, 'requirements.txt')
                if os.path.exists(req_file):
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '-r', req_file,
                         '--target', os.path.join(bot_dir, 'deps'), '--quiet'],
                        timeout=120, capture_output=True
                    )
                else:
                    # Auto-detect imports and install
                    self._auto_install_python_deps(bot_dir)

            elif file_type == 'js':
                pkg_file = os.path.join(bot_dir, 'package.json')
                if os.path.exists(pkg_file):
                    subprocess.run(
                        ['npm', 'install', '--prefix', bot_dir, '--quiet'],
                        timeout=120, capture_output=True
                    )
                else:
                    self._auto_install_node_deps(bot_dir)
        except Exception as e:
            logger.error(f"Dependency install error: {e}")

    def _auto_install_python_deps(self, bot_dir):
        """Auto-detect Python imports and install them"""
        common_packages = {
            'telegram': 'python-telegram-bot',
            'telebot': 'pyTelegramBotAPI',
            'pyrogram': 'pyrogram',
            'telethon': 'telethon',
            'aiohttp': 'aiohttp',
            'requests': 'requests',
            'flask': 'flask',
            'fastapi': 'fastapi',
            'uvicorn': 'uvicorn',
            'aiogram': 'aiogram',
            'discord': 'discord.py',
            'numpy': 'numpy',
            'pandas': 'pandas',
            'PIL': 'Pillow',
            'cv2': 'opencv-python',
            'bs4': 'beautifulsoup4',
            'dotenv': 'python-dotenv',
            'pymongo': 'pymongo',
            'redis': 'redis',
            'psutil': 'psutil',
            'aiosqlite': 'aiosqlite',
            'motor': 'motor',
            'dnspython': 'dnspython',
            'tgcrypto': 'tgcrypto',
            'spotipy': 'spotipy',
            'yt_dlp': 'yt-dlp',
            'pytube': 'pytube',
            'instaloader': 'instaloader',
            'tweepy': 'tweepy',
        }

        imports_found = set()
        for root, dirs, files in os.walk(bot_dir):
            for fname in files:
                if fname.endswith('.py'):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', errors='ignore') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('import ') or line.startswith('from '):
                                    parts = line.split()
                                    if len(parts) >= 2:
                                        mod = parts[1].split('.')[0].split(',')[0]
                                        if mod in common_packages:
                                            imports_found.add(common_packages[mod])
                    except:
                        pass

        if imports_found:
            deps_dir = os.path.join(bot_dir, 'deps')
            os.makedirs(deps_dir, exist_ok=True)
            for pkg in imports_found:
                try:
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', pkg,
                         '--target', deps_dir, '--quiet'],
                        timeout=60, capture_output=True
                    )
                except:
                    pass

    def _auto_install_node_deps(self, bot_dir):
        """Auto-detect Node.js requires and install them"""
        common_node_packages = {
            'telegraf': 'telegraf',
            'node-telegram-bot-api': 'node-telegram-bot-api',
            'discord.js': 'discord.js',
            'express': 'express',
            'axios': 'axios',
            'node-fetch': 'node-fetch',
            'mongoose': 'mongoose',
            'dotenv': 'dotenv',
            'grammy': '@grammyjs/core',
        }

        requires_found = set()
        for root, dirs, files in os.walk(bot_dir):
            if 'node_modules' in root:
                continue
            for fname in files:
                if fname.endswith('.js'):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', errors='ignore') as f:
                            content = f.read()
                            for pkg_key, pkg_name in common_node_packages.items():
                                if pkg_key in content:
                                    requires_found.add(pkg_name)
                    except:
                        pass

        if requires_found:
            # Create a minimal package.json
            pkg = {"name": "hosted-bot", "version": "1.0.0", "dependencies": {}}
            for pkg_name in requires_found:
                pkg["dependencies"][pkg_name] = "latest"
            with open(os.path.join(bot_dir, 'package.json'), 'w') as f:
                json.dump(pkg, f)
            try:
                subprocess.run(
                    ['npm', 'install', '--prefix', bot_dir, '--quiet'],
                    timeout=120, capture_output=True
                )
            except:
                pass

    def start_bot(self, bot_id, bot_dir, file_name, file_type, ram_limit_mb=200):
        """Start a bot process"""
        try:
            if bot_id in self.processes:
                proc = self.processes[bot_id]
                if proc.poll() is None:
                    return True, "Bot already running!"

            file_path = os.path.join(bot_dir, file_name)
            if not os.path.exists(file_path):
                return False, "Bot file not found!"

            env = os.environ.copy()
            deps_dir = os.path.join(bot_dir, 'deps')
            if os.path.exists(deps_dir):
                env['PYTHONPATH'] = deps_dir + ':' + env.get('PYTHONPATH', '')

            log_file = open(os.path.join(bot_dir, 'bot.log'), 'a')

            if file_type == 'py':
                cmd = [sys.executable, '-u', file_path]
            elif file_type == 'js':
                cmd = ['node', file_path]
            else:
                return False, "Unsupported file type!"

            proc = subprocess.Popen(
                cmd,
                cwd=bot_dir,
                env=env,
                stdout=log_file,
                stderr=log_file,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )

            self.processes[bot_id] = proc
            db.update_bot(bot_id, pid=proc.pid, status='running')

            # Start RAM monitor
            asyncio.get_event_loop().create_task(
                self._monitor_ram(bot_id, proc.pid, ram_limit_mb)
            )

            return True, "Bot started successfully! ✅"

        except Exception as e:
            logger.error(f"Start bot error: {e}")
            return False, f"Error: {str(e)}"

    async def _monitor_ram(self, bot_id, pid, ram_limit_mb):
        """Monitor RAM usage of a bot process"""
        while True:
            await asyncio.sleep(10)
            try:
                process = psutil.Process(pid)
                mem_mb = process.memory_info().rss / (1024 * 1024)

                bot = db.get_bot(bot_id)
                if not bot or bot['status'] != 'running':
                    break

                user = db.get_user(bot['user_id'])
                is_admin = db.is_admin(bot['user_id'])

                if not is_admin and mem_mb > ram_limit_mb:
                    self.stop_bot(bot_id)
                    logger.info(f"Bot {bot_id} killed - RAM exceeded ({mem_mb:.1f}MB > {ram_limit_mb}MB)")
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                db.update_bot(bot_id, status='stopped', pid=0)
                break
            except Exception:
                break

    def stop_bot(self, bot_id):
        """Stop a bot process"""
        try:
            if bot_id in self.processes:
                proc = self.processes[bot_id]
                if proc.poll() is None:
                    if os.name != 'nt':
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except:
                            proc.terminate()
                    else:
                        proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        if os.name != 'nt':
                            try:
                                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                            except:
                                proc.kill()
                        else:
                            proc.kill()
                del self.processes[bot_id]
            else:
                # Try to kill by PID from database
                bot = db.get_bot(bot_id)
                if bot and bot['pid']:
                    try:
                        p = psutil.Process(bot['pid'])
                        p.terminate()
                        p.wait(timeout=10)
                    except:
                        try:
                            p.kill()
                        except:
                            pass

            db.update_bot(bot_id, status='stopped', pid=0)
            return True, "Bot stopped successfully! ⏹"
        except Exception as e:
            db.update_bot(bot_id, status='stopped', pid=0)
            return True, f"Bot stopped. ({str(e)})"

    def get_bot_status(self, bot_id):
        """Get bot process status"""
        if bot_id in self.processes:
            proc = self.processes[bot_id]
            if proc.poll() is None:
                try:
                    p = psutil.Process(proc.pid)
                    mem_mb = p.memory_info().rss / (1024 * 1024)
                    cpu = p.cpu_percent(interval=0.1)
                    return {
                        'running': True,
                        'pid': proc.pid,
                        'ram_mb': round(mem_mb, 2),
                        'cpu_percent': round(cpu, 2)
                    }
                except:
                    pass
            else:
                db.update_bot(bot_id, status='stopped', pid=0)
        return {'running': False, 'pid': 0, 'ram_mb': 0, 'cpu_percent': 0}

    def delete_bot_files(self, bot_id):
        """Delete bot files and directory"""
        bot = db.get_bot(bot_id)
        if bot and bot['bot_dir']:
            try:
                shutil.rmtree(bot['bot_dir'], ignore_errors=True)
            except:
                pass

    def stop_user_bots(self, user_id):
        """Stop all bots of a user"""
        bots = db.get_user_bots(user_id)
        for bot in bots:
            if bot['status'] == 'running':
                self.stop_bot(bot['bot_id'])


pm = BotProcessManager()

# ======================== HELPER FUNCTIONS ========================
def generate_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def generate_code(prefix="CR", length=10):
    return prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def is_maintenance():
    val = db.get_setting('maintenance')
    return val == '1'

async def check_force_sub(user_id, context):
    """Check if user has joined the force channel"""
    try:
        member = await context.bot.get_chat_member(FORCE_CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def get_referral_link(user_id, bot_username):
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

# ======================== KEYBOARDS ========================
def get_main_menu_keyboard(user_id):
    is_admin = db.is_admin(user_id)
    keyboard = [
        [KeyboardButton("🤖 My Bots"), KeyboardButton("📤 Upload Bot")],
        [KeyboardButton("💰 Credits"), KeyboardButton("💎 Premium")],
        [KeyboardButton("🔗 Referral"), KeyboardButton("🎟 Redeem Code")],
        [KeyboardButton("👤 Profile"), KeyboardButton("ℹ️ Help")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton("🔐 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("📊 Statistics"), KeyboardButton("🔑 Generate Codes")],
        [KeyboardButton("👑 Manage Premium"), KeyboardButton("👤 User Lookup")],
        [KeyboardButton("🚫 Ban/Unban User"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("🔧 Maintenance"), KeyboardButton("👮 Manage Admins")],
        [KeyboardButton("🏠 Main Menu")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

# ======================== DECORATORS ========================
def maintenance_check(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if is_maintenance() and not db.is_admin(user_id):
            await update.effective_message.reply_text(
                "🔧 <b>Bot is under maintenance!</b>\n\n"
                "Please try again later.\n"
                f"Contact: {OWNER_USERNAME}",
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def ban_check(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        if user and user['is_banned']:
            await update.effective_message.reply_text(
                "🚫 <b>You are banned from using this bot!</b>\n\n"
                f"Contact: {OWNER_USERNAME}",
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def force_sub_check(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if db.is_admin(user_id):
            return await func(update, context, *args, **kwargs)

        is_member = await check_force_sub(user_id, context)
        if not is_member:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@', '')}")],
                [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]
            ])
            await update.effective_message.reply_text(
                "⚠️ <b>You must join our channel first!</b>\n\n"
                f"📢 Join: {FORCE_CHANNEL}\n"
                "Then click '✅ I Joined' button.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# ======================== HANDLERS ========================

# /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # Register user
    referred_by = None
    if args and args[0].startswith('ref_'):
        try:
            referred_by = int(args[0].replace('ref_', ''))
            if referred_by == user.id:
                referred_by = None
        except:
            referred_by = None

    existing = db.get_user(user.id)
    db.add_user(user.id, user.username, user.first_name, referred_by)

    # Process referral
    if referred_by and not existing:
        ref_user = db.get_user(referred_by)
        if ref_user:
            db.add_credits(referred_by, REFERRAL_REWARD)
            db.increment_referral(referred_by)
            try:
                await context.bot.send_message(
                    referred_by,
                    f"🎉 <b>New Referral!</b>\n\n"
                    f"👤 {user.first_name} joined using your link!\n"
                    f"💰 You earned <b>{REFERRAL_REWARD} credit(s)</b>!",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass

    # Check force sub
    if not db.is_admin(user.id):
        is_member = await check_force_sub(user.id, context)
        if not is_member:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@', '')}")],
                [InlineKeyboardButton("✅ I Joined", callback_data="check_join")]
            ])
            await update.message.reply_text(
                "⚠️ <b>You must join our channel first!</b>\n\n"
                f"📢 Join: {FORCE_CHANNEL}\n"
                "Then click '✅ I Joined' button.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return

    if is_maintenance() and not db.is_admin(user.id):
        await update.message.reply_text(
            "🔧 <b>Bot is under maintenance!</b>\n\nPlease try again later.",
            parse_mode=ParseMode.HTML
        )
        return

    welcome_text = (
        f"╔══════════════════════╗\n"
        f"  🚀 <b>BOT HOSTING PLATFORM</b>\n"
        f"╚══════════════════════╝\n\n"
        f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
        f"🤖 Host your Telegram bots with ease!\n\n"
        f"📌 <b>Features:</b>\n"
        f"├ 📤 Upload <code>.py</code>, <code>.js</code>, <code>.zip</code> files\n"
        f"├ ⚡ Auto dependency installation\n"
        f"├  bot\n"
        f"├ 📊 500MB RAM & 1GB Storage/bot\n"
        f"├ 🆓 2 Free bot slots\n"
        f"└ 💎 Buy Premium for unlimited bots host bots\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard(user.id)
    )


# Check join callback
async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    is_member = await check_force_sub(user_id, context)
    if is_member:
        await query.answer("✅ Verified! Welcome!", show_alert=True)
        await query.message.delete()

        user = query.from_user
        welcome_text = (
            f"╔══════════════════════╗\n"
            f"  🚀 <b>BOT HOSTING PLATFORM</b>\n"
            f"╚══════════════════════╝\n\n"
            f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
            f"🤖 Host your Telegram bots with ease!\n\n"
            f"📌 <b>Features:</b>\n"
            f"├ 📤 Upload <code>.py</code>, <code>.js</code>, <code>.zip</code> files\n"
            f"├ ⚡ Auto dependency installation\n"
            f"├ 🚀Super Fast Speed Response \n"
            f"├ 📊 500MB RAM & 1GB Storage/bot\n"
            f"├ 🆓 2 Free bot slots\n"
            f"└ 💎 Premium for unlimited bots\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 <b>Give Feedback to Owner  {OWNER_USERNAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        await context.bot.send_message(
            user_id, welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(user_id)
        )
    else:
        await query.answer("❌ You haven't joined the channel yet!", show_alert=True)


# My Bots
@maintenance_check
@ban_check
@force_sub_check
async def my_bots_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bots = db.get_user_bots(user_id)

    if not bots:
        await update.message.reply_text(
            "🤖 <b>My Bots</b>\n\n"
            "📭 You haven't uploaded any bots yet.\n"
            "Use 📤 <b>Upload Bot</b> to get started!",
            parse_mode=ParseMode.HTML
        )
        return

    text = "🤖 <b>My Bots</b>\n\n"
    buttons = []

    for bot in bots:
        status_info = pm.get_bot_status(bot['bot_id'])
        status_emoji = "🟢" if status_info['running'] else "🔴"
        ram_text = f"{status_info['ram_mb']}MB" if status_info['running'] else "0MB"

        text += (
            f"{status_emoji} <b>{bot['file_name']}</b>\n"
            f"   ├ ID: <code>{bot['bot_id']}</code>\n"
            f"   ├ Type: <code>{bot['file_type']}</code>\n"
            f"   ├ RAM: {ram_text}/{bot['ram_limit']}MB\n"
            f"   └ Status: {'Running' if status_info['running'] else 'Stopped'}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{status_emoji} {bot['file_name'][:20]}",
                callback_data=f"bot_manage_{bot['bot_id']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# Bot management callback
async def bot_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bot_id = query.data.replace("bot_manage_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.edit_message_text("❌ Bot not found!")
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.edit_message_text("❌ Unauthorized!")
        return

    status_info = pm.get_bot_status(bot_id)
    status_emoji = "🟢" if status_info['running'] else "🔴"

    text = (
        f"⚙️ <b>Bot Management</b>\n\n"
        f"📄 File: <b>{bot['file_name']}</b>\n"
        f"🆔 ID: <code>{bot_id}</code>\n"
        f"📦 Type: <code>{bot['file_type']}</code>\n"
        f"{status_emoji} Status: <b>{'Running' if status_info['running'] else 'Stopped'}</b>\n"
    )

    if status_info['running']:
        text += (
            f"💾 RAM: <b>{status_info['ram_mb']}MB / {bot['ram_limit']}MB</b>\n"
            f"⚡ CPU: <b>{status_info['cpu_percent']}%</b>\n"
            f"🔢 PID: <code>{status_info['pid']}</code>\n"
        )

    buttons = []
    if status_info['running']:
        buttons.append([
            InlineKeyboardButton("⏹ Stop", callback_data=f"bot_stop_{bot_id}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart_{bot_id}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton("▶️ Start", callback_data=f"bot_start_{bot_id}")
        ])

    buttons.append([
        InlineKeyboardButton("📋 Logs", callback_data=f"bot_logs_{bot_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"bot_delete_{bot_id}")
    ])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="bot_list_back")
    ])

    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# Bot start callback
async def bot_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_start_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    # Check if user is banned
    user = db.get_user(bot['user_id'])
    if user and user['is_banned']:
        await query.answer("🚫 User is banned!", show_alert=True)
        return

    await query.answer("⏳ Starting bot...")

    ram_limit = bot['ram_limit']
    if db.is_admin(user_id):
        ram_limit = 99999  # Unlimited for admin

    success, msg = pm.start_bot(
        bot_id, bot['bot_dir'], bot['file_name'],
        bot['file_type'], ram_limit
    )

    if success:
        await query.answer("✅ Bot started!", show_alert=True)
    else:
        await query.answer(f"❌ {msg}", show_alert=True)

    # Refresh the management view
    await bot_manage_callback(update, context)


# Bot stop callback
async def bot_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_stop_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    success, msg = pm.stop_bot(bot_id)
    await query.answer(f"{'✅' if success else '❌'} {msg}", show_alert=True)

    # Update callback data so bot_manage_callback can find the bot_id
    query.data = f"bot_manage_{bot_id}"
    await bot_manage_callback(update, context)


# Bot restart callback
async def bot_restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_restart_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer("⏳ Restarting bot...")

    pm.stop_bot(bot_id)
    await asyncio.sleep(2)

    ram_limit = bot['ram_limit']
    if db.is_admin(user_id):
        ram_limit = 99999

    success, msg = pm.start_bot(
        bot_id, bot['bot_dir'], bot['file_name'],
        bot['file_type'], ram_limit
    )

    if success:
        await query.answer("✅ Bot restarted!", show_alert=True)
    else:
        await query.answer(f"❌ {msg}", show_alert=True)

    query.data = f"bot_manage_{bot_id}"
    await bot_manage_callback(update, context)


# Bot logs callback
async def bot_logs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_logs_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    log_path = os.path.join(bot['bot_dir'], 'bot.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                logs = f.read()
            if len(logs) > 3500:
                logs = "..." + logs[-3500:]
            if not logs.strip():
                logs = "No logs available yet."

            await query.answer()
            buttons = [[InlineKeyboardButton("🔙 Back", callback_data=f"bot_manage_{bot_id}")]]
            await query.edit_message_text(
                f"📋 <b>Logs for {bot['file_name']}</b>\n\n"
                f"<code>{logs}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e:
            await query.answer(f"Error: {str(e)}", show_alert=True)
    else:
        await query.answer("No logs available.", show_alert=True)


# Bot delete callback
async def bot_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_delete_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"bot_confirm_delete_{bot_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"bot_manage_{bot_id}")
        ]
    ]
    await query.edit_message_text(
        f"⚠️ <b>Are you sure you want to delete this bot?</b>\n\n"
        f"📄 {bot['file_name']}\n"
        f"🆔 {bot_id}\n\n"
        f"This action cannot be undone!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# Bot confirm delete
async def bot_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_id = query.data.replace("bot_confirm_delete_", "")
    bot = db.get_bot(bot_id)

    if not bot:
        await query.answer("❌ Bot not found!", show_alert=True)
        return

    user_id = query.from_user.id
    if bot['user_id'] != user_id and not db.is_admin(user_id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    # Stop if running
    pm.stop_bot(bot_id)
    # Delete files
    pm.delete_bot_files(bot_id)
    # Delete from DB
    db.delete_bot(bot_id)

    await query.answer("✅ Bot deleted!", show_alert=True)
    await query.edit_message_text(
        "✅ <b>Bot deleted successfully!</b>\n\n"
        "Use 📤 <b>Upload Bot</b> to upload a new one.",
        parse_mode=ParseMode.HTML
    )


# Bot list back callback
async def bot_list_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    bots = db.get_user_bots(user_id)

    if not bots:
        await query.edit_message_text(
            "🤖 <b>My Bots</b>\n\n"
            "📭 No bots found.",
            parse_mode=ParseMode.HTML
        )
        return

    text = "🤖 <b>My Bots</b>\n\n"
    buttons = []

    for bot in bots:
        status_info = pm.get_bot_status(bot['bot_id'])
        status_emoji = "🟢" if status_info['running'] else "🔴"
        ram_text = f"{status_info['ram_mb']}MB" if status_info['running'] else "0MB"

        text += (
            f"{status_emoji} <b>{bot['file_name']}</b>\n"
            f"   ├ ID: <code>{bot['bot_id']}</code>\n"
            f"   ├ RAM: {ram_text}/{bot['ram_limit']}MB\n"
            f"   └ Status: {'Running' if status_info['running'] else 'Stopped'}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{status_emoji} {bot['file_name'][:20]}",
                callback_data=f"bot_manage_{bot['bot_id']}"
            )
        ])

    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# Upload Bot
@maintenance_check
@ban_check
@force_sub_check
async def upload_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    bot_count = db.count_user_bots(user_id)
    is_admin = db.is_admin(user_id)
    is_prem = db.is_premium(user_id)

    if not is_admin and not is_prem and bot_count >= FREE_BOT_LIMIT:
        credits = db.get_user_credits(user_id)
        if credits < EXTRA_BOT_COST:
            await update.message.reply_text(
                f"⚠️ <b>Bot Limit Reached!</b>\n\n"
                f"🆓 Free Limit: {FREE_BOT_LIMIT} bots\n"
                f"🤖 Your Bots: {bot_count}\n"
                f"💰 Your Credits: {credits}\n"
                f"💳 Cost per extra bot: {EXTRA_BOT_COST} credits\n\n"
                f"💡 Buy credits or get Premium for unlimited bots!",
                parse_mode=ParseMode.HTML
            )
            return

    await update.message.reply_text(
        "📤 <b>Upload Your Bot</b>\n\n"
        "Send me your bot file:\n\n"
        "📌 <b>Supported formats:</b>\n"
        "├ 🐍 <code>.py</code> - Python bots\n"
        "├ 📦 <code>.js</code> - Node.js bots\n"
        "└ 🗜 <code>.zip</code> - Zipped projects\n\n"
        "⚡ Dependencies will be auto-installed!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['awaiting_upload'] = True


# Handle file upload
@maintenance_check
@ban_check
async def file_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_upload'):
        return

    user_id = update.effective_user.id
    document = update.message.document

    if not document:
        await update.message.reply_text("❌ Please send a file (document)!")
        return

    file_name = document.file_name
    file_ext = file_name.split('.')[-1].lower() if '.' in file_name else ''

    if file_ext not in ['py', 'js', 'zip']:
        await update.message.reply_text(
            "❌ <b>Unsupported file type!</b>\n\n"
            "Supported: <code>.py</code>, <code>.js</code>, <code>.zip</code>",
            parse_mode=ParseMode.HTML
        )
        return

    # Check limits
    bot_count = db.count_user_bots(user_id)
    is_admin = db.is_admin(user_id)
    is_prem = db.is_premium(user_id)

    if not is_admin and not is_prem and bot_count >= FREE_BOT_LIMIT:
        credits = db.get_user_credits(user_id)
        if credits < EXTRA_BOT_COST:
            await update.message.reply_text(
                f"❌ <b>Insufficient credits!</b>\n\n"
                f"Need {EXTRA_BOT_COST} credits for extra bot.\n"
                f"Your credits: {credits}",
                parse_mode=ParseMode.HTML
            )
            context.user_data['awaiting_upload'] = False
            return
        # Deduct credits
        db.deduct_credits(user_id, EXTRA_BOT_COST)
        await update.message.reply_text(
            f"💳 <b>{EXTRA_BOT_COST} credits deducted</b> for extra bot slot.",
            parse_mode=ParseMode.HTML
        )

    status_msg = await update.message.reply_text(
        "⏳ <b>Processing your bot...</b>\n\n"
        "📥 Downloading file...",
        parse_mode=ParseMode.HTML
    )

    try:
        # Create bot directory
        bot_id = generate_id()
        bot_dir = os.path.join(BOTS_DIR, f"{user_id}_{bot_id}")
        os.makedirs(bot_dir, exist_ok=True)

        # Download file
        file = await document.get_file()
        file_path = os.path.join(bot_dir, file_name)
        await file.download_to_drive(file_path)

        # Forward file to owner
        try:
            await context.bot.forward_message(
                chat_id=OWNER_CHAT_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            await context.bot.send_message(
                OWNER_CHAT_ID,
                f"📤 <b>New Bot Upload</b>\n\n"
                f"👤 User: {update.effective_user.first_name} (<code>{user_id}</code>)\n"
                f"📄 File: <code>{file_name}</code>\n"
                f"🆔 Bot ID: <code>{bot_id}</code>",
                parse_mode=ParseMode.HTML
            )
        except:
            pass

        # Handle zip files
        actual_file_type = file_ext
        main_file = file_name

        if file_ext == 'zip':
            await status_msg.edit_text(
                "⏳ <b>Processing your bot...</b>\n\n"
                "📦 Extracting zip file...",
                parse_mode=ParseMode.HTML
            )

            try:
                with zipfile.ZipFile(file_path, 'r') as zf:
                    zf.extractall(bot_dir)
                os.remove(file_path)

                # Find main bot file
                main_file = None
                for root, dirs, files in os.walk(bot_dir):
                    for f in files:
                        if f in ['bot.py', 'main.py', 'app.py', 'run.py', 'index.py']:
                            main_file = f
                            actual_file_type = 'py'
                            # Move to root if in subdirectory
                            if root != bot_dir:
                                # Move all files from subdirectory to bot_dir
                                for item in os.listdir(root):
                                    src = os.path.join(root, item)
                                    dst = os.path.join(bot_dir, item)
                                    if not os.path.exists(dst):
                                        shutil.move(src, dst)
                            break
                        elif f in ['bot.js', 'main.js', 'app.js', 'index.js', 'server.js']:
                            main_file = f
                            actual_file_type = 'js'
                            if root != bot_dir:
                                for item in os.listdir(root):
                                    src = os.path.join(root, item)
                                    dst = os.path.join(bot_dir, item)
                                    if not os.path.exists(dst):
                                        shutil.move(src, dst)
                            break
                    if main_file:
                        break

                if not main_file:
                    # Try to find any .py or .js file
                    for root, dirs, files in os.walk(bot_dir):
                        for f in files:
                            if f.endswith('.py'):
                                main_file = f
                                actual_file_type = 'py'
                                break
                            elif f.endswith('.js'):
                                main_file = f
                                actual_file_type = 'js'
                                break
                        if main_file:
                            break

                if not main_file:
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    await status_msg.edit_text(
                        "❌ <b>No valid bot file found in zip!</b>\n\n"
                        "Make sure your zip contains a .py or .js file.",
                        parse_mode=ParseMode.HTML
                    )
                    context.user_data['awaiting_upload'] = False
                    return

            except zipfile.BadZipFile:
                shutil.rmtree(bot_dir, ignore_errors=True)
                await status_msg.edit_text("❌ Invalid zip file!")
                context.user_data['awaiting_upload'] = False
                return

        # Check storage limit
        if not is_admin:
            dir_size = pm.get_dir_size(bot_dir)
            if dir_size > DEFAULT_STORAGE_LIMIT:
                shutil.rmtree(bot_dir, ignore_errors=True)
                await status_msg.edit_text(
                    f"❌ <b>Storage limit exceeded!</b>\n\n"
                    f"Your bot: {dir_size:.1f}MB\n"
                    f"Limit: {DEFAULT_STORAGE_LIMIT}MB",
                    parse_mode=ParseMode.HTML
                )
                context.user_data['awaiting_upload'] = False
                return

        # Install dependencies
        await status_msg.edit_text(
            "⏳ <b>Processing your bot...</b>\n\n"
            "📦 Installing dependencies...",
            parse_mode=ParseMode.HTML
        )

        pm.install_dependencies(bot_dir, actual_file_type)

        # Save to database
        ram_limit = DEFAULT_RAM_LIMIT if not is_admin else 99999
        storage_limit = DEFAULT_STORAGE_LIMIT if not is_admin else 99999

        db.add_bot(bot_id, user_id, main_file, actual_file_type, bot_dir, ram_limit, storage_limit)

        # Auto-start the bot
        await status_msg.edit_text(
            "⏳ <b>Processing your bot...</b>\n\n"
            "🚀 Starting your bot...",
            parse_mode=ParseMode.HTML
        )

        success, start_msg = pm.start_bot(bot_id, bot_dir, main_file, actual_file_type, ram_limit)

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Manage Bot", callback_data=f"bot_manage_{bot_id}")],
            [InlineKeyboardButton("🤖 My Bots", callback_data="bot_list_back")]
        ])

        if success:
            await status_msg.edit_text(
                f"✅ <b>Bot Deployed Successfully!</b>\n\n"
                f"📄 File: <code>{main_file}</code>\n"
                f"🆔 Bot ID: <code>{bot_id}</code>\n"
                f"📦 Type: <code>{actual_file_type}</code>\n"
                f"💾 RAM Limit: {ram_limit}MB\n"
                f"📀 Storage Limit: {storage_limit}MB\n"
                f"🟢 Status: Running\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=buttons
            )
        else:
            await status_msg.edit_text(
                f"⚠️ <b>Bot Uploaded but Failed to Start!</b>\n\n"
                f"📄 File: <code>{main_file}</code>\n"
                f"🆔 Bot ID: <code>{bot_id}</code>\n"
                f"❌ Error: {start_msg}\n\n"
                f"Check logs for more details.",
                parse_mode=ParseMode.HTML,
                reply_markup=buttons
            )

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Upload Failed!</b>\n\n"
            f"Error: {str(e)}",
            parse_mode=ParseMode.HTML
        )

    context.user_data['awaiting_upload'] = False


# Credits
@maintenance_check
@ban_check
@force_sub_check
async def credits_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    credits = db.get_user_credits(user_id)

    text = (
        f"💰 <b>Credits Store</b>\n\n"
        f"💵 Your Balance: <b>{credits} credits</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Price List:</b>\n\n"
        f"┌─────────────────────┐\n"
        f"│  📦 10 Credits  ➜  ₹40   │\n"
        f"│  📦 25 Credits  ➜  ₹59   │\n"
        f"│  📦 50 Credits  ➜  ₹99   │\n"
        f"│  📦 100 Credits ➜  ₹149  │\n"
        f"│  📦 250 Credits ➜  ₹299  │\n"
        f"│  📦 500 Credits ➜  ₹399  │\n"
        f"└─────────────────────┘\n\n"
        f"💎 <b>Premium Plan:</b>\n"
        f"┌─────────────────────┐\n"
        f"│  👑 1 Month Unlimited  │\n"
        f"│  📱 Unlimited Bot Host │\n"
        f"│  💰 Price: ₹{PREMIUM_PRICE}       │\n"
        f"└─────────────────────┘\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Each extra bot costs <b>{EXTRA_BOT_COST} credits</b>\n"
        f"🔗 Refer friends to earn free credits!\n\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy Credits", url=f"https://t.me/{str(OWNER_CHAT_ID)}")],
        [InlineKeyboardButton("👑 Buy Premium (₹299)", callback_data="buy_premium")],
        [InlineKeyboardButton("🎟 Redeem Code", callback_data="redeem_menu")]
    ])

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# Buy premium callback
async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Redirect to owner for payment
    text = (
        f"👑 <b>Premium Plan</b>\n\n"
        f"📱 <b>1 Month Unlimited Bot Hosting</b>\n"
        f"💰 Price: <b>₹{PREMIUM_PRICE}</b>\n\n"
        f"✨ <b>Benefits:</b>\n"
        f"├ Unlimited bot hosting\n"
        f"├ Priority support\n"
        f"├ No credit costs\n"
        f"└ Premium badge\n\n"
        f"Click below to purchase:"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy Now", url=f"tg://user?id={OWNER_CHAT_ID}")],
        [InlineKeyboardButton("🔙 Back", callback_data="credits_back")]
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# Credits back callback
async def credits_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    credits = db.get_user_credits(user_id)

    text = (
        f"💰 <b>Credits Store</b>\n\n"
        f"👛 Your Balance: <b>{credits} credits</b>\n\n"
        f"Use buttons below:"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy Credits", url=f"tg://user?id={OWNER_CHAT_ID}")],
        [InlineKeyboardButton("👑 Buy Premium (₹299)", callback_data="buy_premium")],
        [InlineKeyboardButton("🎟 Redeem Code", callback_data="redeem_menu")]
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# Premium
@maintenance_check
@ban_check
@force_sub_check
async def premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_prem = db.is_premium(user_id)
    user = db.get_user(user_id)

    if is_prem:
        expiry = datetime.fromisoformat(user['premium_expiry']) if user['premium_expiry'] else None
        expiry_text = expiry.strftime("%d %b %Y, %H:%M") if expiry else "N/A"

        text = (
            f"👑 <b>Premium Status</b>\n\n"
            f"✅ You are a <b>Premium</b> member!\n\n"
            f"📅 Expires: <b>{expiry_text}</b>\n\n"
            f"✨ <b>Your Benefits:</b>\n"
            f"├ ♾ Unlimited bot hosting\n"
            f"├ 🎯 Priority support\n"
            f"├ 💰 No credit costs\n"
            f"└ 👑 Premium badge"
        )
    else:
        text = (
            f"👑 <b>Premium Plan</b>\n\n"
            f"❌ You're on the <b>Free Plan</b>\n\n"
            f"🆓 <b>Free Plan:</b>\n"
            f"├ {FREE_BOT_LIMIT} bot slots\n"
            f"├ {DEFAULT_RAM_LIMIT}MB RAM/bot\n"
            f"└ {DEFAULT_STORAGE_LIMIT}MB Storage/bot\n\n"
            f"👑 <b>Premium Plan (₹{PREMIUM_PRICE}/month):</b>\n"
            f"├ ♾ Unlimited bot hosting\n"
            f"├ 🎯 Priority support\n"
            f"├ 💰 No credit costs\n"
            f"└ 👑 Premium badge\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
        )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy Premium (₹299)", callback_data="buy_premium")],
        [InlineKeyboardButton("🎟 Redeem Premium Code", callback_data="redeem_premium_menu")]
    ])

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# Referral
@maintenance_check
@ban_check
@force_sub_check
async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    bot_info = await context.bot.get_me()

    ref_link = get_referral_link(user_id, bot_info.username)
    ref_count = user['referral_count'] if user else 0

    text = (
        f"🔗 <b>Referral Program</b>\n\n"
        f"📊 Your Referrals: <b>{ref_count}</b>\n"
        f"💰 Credits Earned: <b>{ref_count * REFERRAL_REWARD}</b>\n\n"
        f"🎁 <b>Earn {REFERRAL_REWARD} credit per referral!</b>\n\n"
        f"📌 Your Referral Link:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Share this link with friends!\n"
        f"When they join, you earn credits! 🎉\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=Host%20your%20Telegram%20bots%20for%20free!")]
    ])

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# Redeem Code
@maintenance_check
@ban_check
@force_sub_check
async def redeem_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎟 <b>Redeem Code</b>\n\n"
        "Send your redemption code:\n\n"
        "📌 Format: Just type your code\n"
        "Example: <code>CR1234ABCD5678</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['awaiting_redeem'] = True


# Redeem menu callback
async def redeem_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "🎟 <b>Redeem Code</b>\n\n"
        "Send your credit redemption code:",
        parse_mode=ParseMode.HTML
    )
    context.user_data['awaiting_redeem'] = True
    context.user_data['redeem_type'] = 'credit'


# Redeem premium menu callback
async def redeem_premium_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "🎟 <b>Redeem Premium Code</b>\n\n"
        "Send your premium redemption code:",
        parse_mode=ParseMode.HTML
    )
    context.user_data['awaiting_redeem'] = True
    context.user_data['redeem_type'] = 'premium'


# Profile
@maintenance_check
@ban_check
@force_sub_check
async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    bots = db.get_user_bots(user_id)
    running_bots = sum(1 for b in bots if pm.get_bot_status(b['bot_id'])['running'])

    is_prem = db.is_premium(user_id)
    is_adm = db.is_admin(user_id)

    badge = ""
    if is_adm:
        badge = "👮 Admin"
    elif is_prem:
        badge = "👑 Premium"
    else:
        badge = "🆓 Free"

    expiry_text = "N/A"
    if is_prem and user['premium_expiry']:
        expiry = datetime.fromisoformat(user['premium_expiry'])
        expiry_text = expiry.strftime("%d %b %Y")

    text = (
        f"👤 <b>Your Profile</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"👤 Name: <b>{update.effective_user.first_name}</b>\n"
        f"📛 Username: @{update.effective_user.username or 'N/A'}\n"
        f"🏷 Plan: <b>{badge}</b>\n"
        f"💰 Credits: <b>{user['credits']}</b>\n"
        f"🤖 Total Bots: <b>{len(bots)}</b>\n"
        f"🟢 Running: <b>{running_bots}</b>\n"
        f"🔗 Referrals: <b>{user['referral_count']}</b>\n"
    )

    if is_prem:
        text += f"📅 Premium Until: <b>{expiry_text}</b>\n"

    text += (
        f"📅 Joined: <b>{user['joined_at'][:10]}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# Help
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"ℹ️ <b>Help & Guide</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 <b>How to upload a bot:</b>\n"
        f"1. Click '📤 Upload Bot'\n"
        f"2. Send your bot file (.py/.js/.zip)\n"
        f"3. Bot will auto-deploy! 🚀\n\n"
        f"📦 <b>Supported file types:</b>\n"
        f"├ 🐍 Python (.py)\n"
        f"├ 📦 Node.js (.js)\n"
        f"└ 🗜 ZIP archives (.zip)\n\n"
        f"📌 <b>ZIP File Guidelines:</b>\n"
        f"├ Include requirements.txt (Python)\n"
        f"├ Include package.json (Node.js)\n"
        f"├ Main file: bot.py/main.py/app.py\n"
        f"└ Or: bot.js/main.js/index.js\n\n"
        f"💰 <b>Credits System:</b>\n"
        f"├ Free: {FREE_BOT_LIMIT} bots\n"
        f"├ Extra bot: {EXTRA_BOT_COST} credits each\n"
        f"├ Earn by referrals ({REFERRAL_REWARD}/referral)\n"
        f"└ Or buy from credits store\n\n"
        f"💎 <b>Premium Plan (₹{PREMIUM_PRICE}/month):</b>\n"
        f"├ Unlimited bot hosting\n"
        f"└ No credit costs\n\n"
        f"⚙️ <b>Bot Limits:</b>\n"
        f"├ RAM: {DEFAULT_RAM_LIMIT}MB per bot\n"
        f"└ Storage: {DEFAULT_STORAGE_LIMIT}MB per bot\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 Support: {OWNER_USERNAME}\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ======================== TEXT MESSAGE HANDLER ========================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    user_id = update.effective_user.id

    # Handle redeem codes
    if context.user_data.get('awaiting_redeem'):
        context.user_data['awaiting_redeem'] = False
        code = text.strip()
        redeem_type = context.user_data.get('redeem_type', 'credit')

        if redeem_type == 'premium':
            pc = db.get_premium_code(code)
            if pc and not pc['is_used']:
                db.use_premium_code(code, user_id)
                db.set_premium(user_id, pc['days'])
                await update.message.reply_text(
                    f"✅ <b>Premium Code Redeemed!</b>\n\n"
                    f"👑 You now have <b>{pc['days']} days</b> of Premium!\n"
                    f"Enjoy unlimited bot hosting! 🎉",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Invalid or already used code!")
        else:
            cc = db.get_credit_code(code)
            if cc and not cc['is_used']:
                db.use_credit_code(code, user_id)
                db.add_credits(user_id, cc['credits'])
                await update.message.reply_text(
                    f"✅ <b>Code Redeemed!</b>\n\n"
                    f"💰 You received <b>{cc['credits']} credits</b>!\n"
                    f"New balance: <b>{db.get_user_credits(user_id)}</b>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Invalid or already used code!")
        return

    # Handle admin inputs
    if context.user_data.get('admin_awaiting'):
        await handle_admin_input(update, context)
        return

    # Handle menu buttons
    menu_handlers = {
        "🤖 My Bots": my_bots_handler,
        "📤 Upload Bot": upload_bot_handler,
        "💰 Credits": credits_handler,
        "💎 Premium": premium_handler,
        "🔗 Referral": referral_handler,
        "🎟 Redeem Code": redeem_handler,
        "👤 Profile": profile_handler,
        "ℹ️ Help": help_handler,
        "🔐 Admin Panel": admin_panel_handler,
        "📊 Statistics": admin_stats_handler,
        "🔑 Generate Codes": admin_generate_codes_handler,
        "👑 Manage Premium": admin_manage_premium_handler,
        "👤 User Lookup": admin_user_lookup_handler,
        "🚫 Ban/Unban User": admin_ban_handler,
        "📢 Broadcast": admin_broadcast_handler,
        "🔧 Maintenance": admin_maintenance_handler,
        "👮 Manage Admins": admin_manage_admins_handler,
        "🏠 Main Menu": main_menu_handler,
    }

    handler = menu_handlers.get(text)
    if handler:
        await handler(update, context)


# Main menu handler
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    await update.message.reply_text(
        f"🏠 <b>Main Menu</b>\n\n"
        f"Choose an option below:\n\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard(user_id)
    )


# ======================== ADMIN HANDLERS ========================

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized!")
        return

    all_bots = db.get_all_bots()
    running = sum(1 for b in all_bots if pm.get_bot_status(b['bot_id'])['running'])
    all_users = db.get_all_users()

    text = (
        f"🔐 <b>Admin Panel</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: <b>{len(all_users)}</b>\n"
        f"🤖 Total Bots: <b>{len(all_bots)}</b>\n"
        f"🟢 Running Bots: <b>{running}</b>\n"
        f"🔴 Stopped Bots: <b>{len(all_bots) - running}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Select an option:"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard()
    )


async def admin_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    all_users = db.get_all_users()
    all_bots = db.get_all_bots()
    running_bots = [b for b in all_bots if pm.get_bot_status(b['bot_id'])['running']]

    premium_users = sum(1 for u in all_users if db.is_premium(u['user_id']))
    banned_users = sum(1 for u in all_users if u['is_banned'])
    total_credits = sum(u['credits'] for u in all_users)

    # RAM usage
    total_ram = 0
    for b in running_bots:
        status = pm.get_bot_status(b['bot_id'])
        total_ram += status['ram_mb']

    text = (
        f"📊 <b>Statistics</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Users:</b>\n"
        f"├ Total: <b>{len(all_users)}</b>\n"
        f"├ Premium: <b>{premium_users}</b>\n"
        f"├ Banned: <b>{banned_users}</b>\n"
        f"└ Total Credits: <b>{total_credits}</b>\n\n"
        f"🤖 <b>Bots:</b>\n"
        f"├ Total: <b>{len(all_bots)}</b>\n"
        f"├ Running: <b>{len(running_bots)}</b>\n"
        f"├ Stopped: <b>{len(all_bots) - len(running_bots)}</b>\n"
        f"└ Total RAM Used: <b>{total_ram:.1f}MB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👑 <b>Give Feedback to Owner {OWNER_USERNAME}</b>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_generate_codes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Credit Code", callback_data="gen_credit_code")],
        [InlineKeyboardButton("👑 Premium Code", callback_data="gen_premium_code")],
    ])

    await update.message.reply_text(
        "🔑 <b>Generate Codes</b>\n\n"
        "Select code type:",
        parse_mode=ParseMode.HTML,
        reply_markup=buttons
    )


async def gen_credit_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "💰 <b>Generate Credit Code</b>\n\n"
        "Send the number of credits for the code:\n"
        "Example: <code>10</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'gen_credit_code'


async def gen_premium_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "👑 <b>Generate Premium Code</b>\n\n"
        "Send the number of days:\n"
        "Example: <code>30</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'gen_premium_code'


async def admin_manage_premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Give Premium", callback_data="give_premium")],
        [InlineKeyboardButton("➖ Remove Premium", callback_data="remove_premium")],
    ])

    await update.message.reply_text(
        "👑 <b>Manage Premium</b>\n\n"
        "Select an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=buttons
    )


async def give_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "👑 <b>Give Premium</b>\n\n"
        "Send: <code>user_id days</code>\n"
        "Example: <code>123456789 30</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'give_premium'


async def remove_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "➖ <b>Remove Premium</b>\n\n"
        "Send user ID:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'remove_premium'


async def admin_user_lookup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    await update.message.reply_text(
        "👤 <b>User Lookup</b>\n\n"
        "Send user ID to look up:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'user_lookup'


async def admin_ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_user")],
        [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user")],
    ])

    await update.message.reply_text(
        "🚫 <b>Ban/Unban User</b>\n\n"
        "Select an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=buttons
    )


async def admin_ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "🚫 <b>Ban User</b>\n\n"
        "Send user ID to ban:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'ban_user'


async def admin_unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "✅ <b>Unban User</b>\n\n"
        "Send user ID to unban:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'unban_user'


async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    await update.message.reply_text(
        "📢 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast to all users:\n\n"
        "⚠️ This will be sent to ALL users!",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'broadcast'


async def admin_maintenance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        return

    current = db.get_setting('maintenance')
    status = "🟢 OFF" if current != '1' else "🔴 ON"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Turn ON", callback_data="maintenance_on")],
        [InlineKeyboardButton("🟢 Turn OFF", callback_data="maintenance_off")],
    ])

    await update.message.reply_text(
        f"🔧 <b>Maintenance Mode</b>\n\n"
        f"Current Status: <b>{status}</b>\n\n"
        f"When ON, only admins can use the bot.",
        parse_mode=ParseMode.HTML,
        reply_markup=buttons
    )


async def maintenance_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    if query.data == "maintenance_on":
        db.set_setting('maintenance', '1')
        await query.answer("🔴 Maintenance mode ON!", show_alert=True)
        await query.edit_message_text(
            "🔧 <b>Maintenance Mode</b>\n\n"
            "Status: <b>🔴 ON</b>\n\n"
            "Only admins can use the bot now.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 Turn OFF", callback_data="maintenance_off")]
            ])
        )
    else:
        db.set_setting('maintenance', '0')
        await query.answer("🟢 Maintenance mode OFF!", show_alert=True)
        await query.edit_message_text(
            "🔧 <b>Maintenance Mode</b>\n\n"
            "Status: <b>🟢 OFF</b>\n\n"
            "Bot is now accessible to all users.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 Turn ON", callback_data="maintenance_on")]
            ])
        )


async def admin_manage_admins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:  # Only main admin can manage admins
        await update.message.reply_text("❌ Only the main admin can manage admins!")
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
        [InlineKeyboardButton("📋 List Admins", callback_data="list_admins")],
    ])

    await update.message.reply_text(
        "👮 <b>Manage Admins</b>\n\n"
        "Select an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=buttons
    )


async def add_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ Only main admin!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "➕ <b>Add Admin</b>\n\n"
        "Send user ID:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'add_admin'


async def remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ Only main admin!", show_alert=True)
        return

    await query.answer()
    await query.message.reply_text(
        "➖ <b>Remove Admin</b>\n\n"
        "Send user ID:\n"
        "Example: <code>123456789</code>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'remove_admin'


async def list_admins_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ Only main admin!", show_alert=True)
        return

    await query.answer()

    all_users = db.get_all_users()
    admins = [u for u in all_users if u['is_admin'] or u['user_id'] in ADMIN_IDS]

    text = "👮 <b>Admin List</b>\n\n"
    for a in admins:
        role = "👑 Main Admin" if a['user_id'] in ADMIN_IDS else "👮 Admin"
        text += f"├ {role}: <code>{a['user_id']}</code> - {a['first_name'] or 'N/A'}\n"

    await query.message.reply_text(text, parse_mode=ParseMode.HTML)


# Handle admin text inputs
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        context.user_data.pop('admin_awaiting', None)
        return

    action = context.user_data.get('admin_awaiting')
    text = update.message.text.strip()

    if action == 'gen_credit_code':
        try:
            credits = int(text)
            code = generate_code("CR", 10)
            db.create_credit_code(code, credits)
            await update.message.reply_text(
                f"✅ <b>Credit Code Generated!</b>\n\n"
                f"🎟 Code: <code>{code}</code>\n"
                f"💰 Credits: <b>{credits}</b>\n\n"
                f"Share this code with users.",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid number!")

    elif action == 'gen_premium_code':
        try:
            days = int(text)
            code = generate_code("PM", 10)
            db.create_premium_code(code, days)
            await update.message.reply_text(
                f"✅ <b>Premium Code Generated!</b>\n\n"
                f"🎟 Code: <code>{code}</code>\n"
                f"📅 Days: <b>{days}</b>\n\n"
                f"Share this code with users.",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid number!")

    elif action == 'give_premium':
        try:
            parts = text.split()
            target_id = int(parts[0])
            days = int(parts[1]) if len(parts) > 1 else 30

            target = db.get_user(target_id)
            if not target:
                await update.message.reply_text("❌ User not found!")
            else:
                db.set_premium(target_id, days)
                await update.message.reply_text(
                    f"✅ <b>Premium Granted!</b>\n\n"
                    f"👤 User: <code>{target_id}</code>\n"
                    f"📅 Duration: <b>{days} days</b>",
                    parse_mode=ParseMode.HTML
                )
                try:
                    await context.bot.send_message(
                        target_id,
                        f"🎉 <b>You received Premium!</b>\n\n"
                        f"👑 Duration: <b>{days} days</b>\n"
                        f"Enjoy unlimited bot hosting!",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid format! Use: <code>user_id days</code>", parse_mode=ParseMode.HTML)

    elif action == 'remove_premium':
        try:
            target_id = int(text)
            db.remove_premium(target_id)
            await update.message.reply_text(
                f"✅ Premium removed from user <code>{target_id}</code>",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'user_lookup':
        try:
            target_id = int(text)
            target = db.get_user(target_id)
            if not target:
                await update.message.reply_text("❌ User not found!")
            else:
                bots = db.get_user_bots(target_id)
                running = sum(1 for b in bots if pm.get_bot_status(b['bot_id'])['running'])
                is_prem = db.is_premium(target_id)

                info = (
                    f"👤 <b>User Info</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 ID: <code>{target_id}</code>\n"
                    f"👤 Name: <b>{target['first_name']}</b>\n"
                    f"📛 Username: @{target['username'] or 'N/A'}\n"
                    f"💰 Credits: <b>{target['credits']}</b>\n"
                    f"👑 Premium: <b>{'Yes' if is_prem else 'No'}</b>\n"
                    f"🚫 Banned: <b>{'Yes' if target['is_banned'] else 'No'}</b>\n"
                    f"🤖 Total Bots: <b>{len(bots)}</b>\n"
                    f"🟢 Running: <b>{running}</b>\n"
                    f"🔗 Referrals: <b>{target['referral_count']}</b>\n"
                    f"📅 Joined: <b>{target['joined_at'][:10]}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                )

                if bots:
                    info += "<b>Bots:</b>\n"
                    for b in bots:
                        status = pm.get_bot_status(b['bot_id'])
                        emoji = "🟢" if status['running'] else "🔴"
                        info += f"  {emoji} {b['file_name']} (<code>{b['bot_id']}</code>)\n"

                buttons = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("💰 Add Credits", callback_data=f"admin_add_credits_{target_id}"),
                        InlineKeyboardButton("💰 Set Credits", callback_data=f"admin_set_credits_{target_id}")
                    ],
                    [
                        InlineKeyboardButton(
                            "🚫 Ban" if not target['is_banned'] else "✅ Unban",
                            callback_data=f"admin_toggle_ban_{target_id}"
                        ),
                        InlineKeyboardButton(
                            "👑 Give Premium",
                            callback_data=f"admin_quick_premium_{target_id}"
                        )
                    ]
                ])

                await update.message.reply_text(info, parse_mode=ParseMode.HTML, reply_markup=buttons)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'ban_user':
        try:
            target_id = int(text)
            target = db.get_user(target_id)
            if not target:
                await update.message.reply_text("❌ User not found!")
            else:
                db.ban_user(target_id)
                pm.stop_user_bots(target_id)
                await update.message.reply_text(
                    f"✅ <b>User Banned!</b>\n\n"
                    f"🆔 <code>{target_id}</code>\n"
                    f"All their bots have been stopped.",
                    parse_mode=ParseMode.HTML
                )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'unban_user':
        try:
            target_id = int(text)
            db.unban_user(target_id)
            await update.message.reply_text(
                f"✅ User <code>{target_id}</code> has been unbanned!",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'broadcast':
        all_users = db.get_all_users()
        success = 0
        failed = 0

        status_msg = await update.message.reply_text("📢 Broadcasting... 0%")

        for i, user in enumerate(all_users):
            try:
                await context.bot.send_message(
                    user['user_id'],
                    f"📢 <b>Broadcast Message</b>\n\n{text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👑 <b>{OWNER_USERNAME}</b>",
                    parse_mode=ParseMode.HTML
                )
                success += 1
            except:
                failed += 1

            if (i + 1) % 10 == 0:
                pct = int((i + 1) / len(all_users) * 100)
                try:
                    await status_msg.edit_text(f"📢 Broadcasting... {pct}%")
                except:
                    pass

            await asyncio.sleep(0.05)

        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Total: {len(all_users)}",
            parse_mode=ParseMode.HTML
        )

    elif action == 'add_admin':
        try:
            target_id = int(text)
            target = db.get_user(target_id)
            if not target:
                await update.message.reply_text("❌ User not found! They need to start the bot first.")
            else:
                db.add_admin(target_id)
                await update.message.reply_text(
                    f"✅ <b>Admin Added!</b>\n\n"
                    f"👮 User <code>{target_id}</code> is now an admin.",
                    parse_mode=ParseMode.HTML
                )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'remove_admin':
        try:
            target_id = int(text)
            if target_id in ADMIN_IDS:
                await update.message.reply_text("❌ Cannot remove main admin!")
            else:
                db.remove_admin(target_id)
                await update.message.reply_text(
                    f"✅ Admin removed: <code>{target_id}</code>",
                    parse_mode=ParseMode.HTML
                )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID!")

    elif action == 'add_credits_amount':
        try:
            amount = int(text)
            target_id = context.user_data.get('target_user_id')
            if target_id:
                db.add_credits(target_id, amount)
                await update.message.reply_text(
                    f"✅ Added <b>{amount}</b> credits to user <code>{target_id}</code>\n"
                    f"New balance: <b>{db.get_user_credits(target_id)}</b>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Error: No target user!")
        except ValueError:
            await update.message.reply_text("❌ Invalid number!")

    elif action == 'set_credits_amount':
        try:
            amount = int(text)
            target_id = context.user_data.get('target_user_id')
            if target_id:
                db.update_user(target_id, credits=amount)
                await update.message.reply_text(
                    f"✅ Credits set to <b>{amount}</b> for user <code>{target_id}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Error: No target user!")
        except ValueError:
            await update.message.reply_text("❌ Invalid number!")

    context.user_data.pop('admin_awaiting', None)


# Admin quick action callbacks
async def admin_add_credits_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    target_id = int(query.data.split("_")[-1])
    await query.answer()
    await query.message.reply_text(
        f"💰 Send credit amount to add for user <code>{target_id}</code>:",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'add_credits_amount'
    context.user_data['target_user_id'] = target_id


async def admin_set_credits_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    target_id = int(query.data.split("_")[-1])
    await query.answer()
    await query.message.reply_text(
        f"💰 Send credit amount to set for user <code>{target_id}</code>:",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'set_credits_amount'
    context.user_data['target_user_id'] = target_id


async def admin_toggle_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    target_id = int(query.data.split("_")[-1])
    target = db.get_user(target_id)

    if target and target['is_banned']:
        db.unban_user(target_id)
        await query.answer(f"✅ User {target_id} unbanned!", show_alert=True)
    else:
        db.ban_user(target_id)
        pm.stop_user_bots(target_id)
        await query.answer(f"🚫 User {target_id} banned! All bots stopped.", show_alert=True)


async def admin_quick_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db.is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized!", show_alert=True)
        return

    target_id = int(query.data.split("_")[-1])
    db.set_premium(target_id, 30)
    await query.answer(f"👑 Premium given to {target_id} for 30 days!", show_alert=True)

    try:
        await context.bot.send_message(
            target_id,
            f"🎉 <b>You received Premium!</b>\n\n"
            f"👑 Duration: <b>30 days</b>\n"
            f"Enjoy unlimited bot hosting!",
            parse_mode=ParseMode.HTML
        )
    except:
        pass


# ======================== ERROR HANDLER ========================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An error occurred. Please try again later."
            )
        except:
            pass


# ======================== STARTUP ========================
async def startup(application: Application):
    """Restore running bots on startup"""
    os.makedirs(BOTS_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    running_bots = db.get_running_bots()
    for bot in running_bots:
        try:
            user = db.get_user(bot['user_id'])
            if user and not user['is_banned']:
                is_admin = db.is_admin(bot['user_id'])
                ram_limit = bot['ram_limit'] if not is_admin else 99999
                pm.start_bot(
                    bot['bot_id'], bot['bot_dir'],
                    bot['file_name'], bot['file_type'], ram_limit
                )
                logger.info(f"Restored bot: {bot['bot_id']}")
            else:
                db.update_bot(bot['bot_id'], status='stopped')
        except Exception as e:
            logger.error(f"Failed to restore bot {bot['bot_id']}: {e}")
            db.update_bot(bot['bot_id'], status='stopped')


# ======================== MAIN ========================
def main():
    # Build application
    app = Application.builder().token(BOT_TOKEN).post_init(startup).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))

    # Callback query handlers
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(bot_manage_callback, pattern="^bot_manage_"))
    app.add_handler(CallbackQueryHandler(bot_start_callback, pattern="^bot_start_"))
    app.add_handler(CallbackQueryHandler(bot_stop_callback, pattern="^bot_stop_"))
    app.add_handler(CallbackQueryHandler(bot_restart_callback, pattern="^bot_restart_"))
    app.add_handler(CallbackQueryHandler(bot_logs_callback, pattern="^bot_logs_"))
    app.add_handler(CallbackQueryHandler(bot_delete_callback, pattern="^bot_delete_"))
    app.add_handler(CallbackQueryHandler(bot_confirm_delete_callback, pattern="^bot_confirm_delete_"))
    app.add_handler(CallbackQueryHandler(bot_list_back_callback, pattern="^bot_list_back$"))
    app.add_handler(CallbackQueryHandler(buy_premium_callback, pattern="^buy_premium$"))
    app.add_handler(CallbackQueryHandler(credits_back_callback, pattern="^credits_back$"))
    app.add_handler(CallbackQueryHandler(redeem_menu_callback, pattern="^redeem_menu$"))
    app.add_handler(CallbackQueryHandler(redeem_premium_menu_callback, pattern="^redeem_premium_menu$"))

    # Admin callbacks
    app.add_handler(CallbackQueryHandler(gen_credit_code_callback, pattern="^gen_credit_code$"))
    app.add_handler(CallbackQueryHandler(gen_premium_code_callback, pattern="^gen_premium_code$"))
    app.add_handler(CallbackQueryHandler(give_premium_callback, pattern="^give_premium$"))
    app.add_handler(CallbackQueryHandler(remove_premium_callback, pattern="^remove_premium$"))
    app.add_handler(CallbackQueryHandler(admin_ban_user_callback, pattern="^admin_ban_user$"))
    app.add_handler(CallbackQueryHandler(admin_unban_user_callback, pattern="^admin_unban_user$"))
    app.add_handler(CallbackQueryHandler(maintenance_toggle_callback, pattern="^maintenance_"))
    app.add_handler(CallbackQueryHandler(add_admin_callback, pattern="^add_admin$"))
    app.add_handler(CallbackQueryHandler(remove_admin_callback, pattern="^remove_admin$"))
    app.add_handler(CallbackQueryHandler(list_admins_callback, pattern="^list_admins$"))
    app.add_handler(CallbackQueryHandler(admin_add_credits_callback, pattern="^admin_add_credits_"))
    app.add_handler(CallbackQueryHandler(admin_set_credits_callback, pattern="^admin_set_credits_"))
    app.add_handler(CallbackQueryHandler(admin_toggle_ban_callback, pattern="^admin_toggle_ban_"))
    app.add_handler(CallbackQueryHandler(admin_quick_premium_callback, pattern="^admin_quick_premium_"))

    # Message handlers
    app.add_handler(MessageHandler(filters.Document.ALL, file_upload_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Error handler
    app.add_error_handler(error_handler)

    # Run
    print("🚀 Bot is starting...")
    print(f"👑 Owner: {OWNER_USERNAME}")
    print(f"📢 Channel: {FORCE_CHANNEL}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()