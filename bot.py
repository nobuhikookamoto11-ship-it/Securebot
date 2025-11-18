#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SecureBot (Hybrid) - Railway
Features:
- Hybrid (private + group)
- Welcome messages
- Advanced moderation (ban, kick, mute, unmute)
- Anti-spam (basic flood detect)
- Auto-delete user messages (configurable)
- Auto-reply for simple keywords
- Admin panel + /broadcast
- SQLite user database
"""

import os
import sys
import asyncio
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
)

# -------------------------
# Load env
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # set your numeric telegram id in env
DB_PATH = os.getenv("DB_PATH", "securebot.sqlite")
ABOUT_PATH = os.getenv("ABOUT_PATH", "about_bot.md")

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("securebot")

# -------------------------
# DB init
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            added_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spam (
            user_id INTEGER PRIMARY KEY,
            count INTEGER DEFAULT 0,
            last_ts TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------------
# Helpers
# -------------------------
def save_user(user):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, added_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username or "", user.first_name or "", user.last_name or "", datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("save_user error: %s", e)

def get_all_user_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def generate_password(length=16, use_special=True):
    alphabet = string.ascii_letters + string.digits
    specials = "!@#$%^&*()_-+=<>?/{}[]|"
    if use_special:
        alphabet += specials
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and (not use_special or any(c in specials for c in pwd))):
            return pwd

# -------------------------
# Anti-spam (basic)
# -------------------------
SPAM_LIMIT = 6          # messages allowed in window
SPAM_WINDOW_SEC = 10    # seconds window to count as spam

def spam_record_and_check(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count, last_ts FROM spam WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    now = datetime.now(timezone.utc)
    if not row:
        cur.execute("INSERT INTO spam (user_id, count, last_ts) VALUES (?, ?, ?)", (user_id, 1, now.isoformat()))
        conn.commit()
        conn.close()
        return 1
    else:
        count, last_ts = row
        try:
            last = datetime.fromisoformat(last_ts)
        except:
            last = now
        # reset if last message was long ago
        if (now - last).total_seconds() > SPAM_WINDOW_SEC:
            cur.execute("UPDATE spam SET count=1, last_ts=? WHERE user_id=?", (now.isoformat(), user_id))
            conn.commit()
            conn.close()
            return 1
        else:
            count += 1
            cur.execute("UPDATE spam SET count=?, last_ts=? WHERE user_id=?", (count, now.isoformat(), user_id))
            conn.commit()
            conn.close()
            return count

async def handle_spam_action(update: Update, context: ContextTypes.DEFAULT_TYPE, count:int):
    # simple action: mute for 10 minutes
    try:
        await update.message.reply_text("⚠️ You are sending messages too fast. Muted for 10 minutes.")
        until_date = datetime.now(timezone.utc) + timedelta(minutes=10)
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            update.effective_user.id,
            ChatPermissions(can_send_messages=False),
            until_date=until_date
        )
    except Exception as e:
        logger.warning("mute failed: %s", e)

# -------------------------
# Welcome handler
# -------------------------
async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new = update.chat_member.new_chat_member
        if new.status.name in ("MEMBER", "CREATOR", "ADMINISTRATOR"):
            user = new.user
            save_user(user)
            txt = f"🎉 Welcome {user.mention_html()}!\nPlease read the rules and /help."
            await context.bot.send_message(chat_id=update.chat_member.chat.id, text=txt, parse_mode="HTML")
    except Exception as e:
        logger.warning("chat_member_update error: %s", e)

# -------------------------
# Auto-delete handler (delete message after delay)
# -------------------------
AUTO_DELETE_SECONDS = 12

async def auto_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        await update.message.delete()
    except Exception:
        pass

# -------------------------
# Auto-reply simple
# -------------------------
async def auto_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").lower()
    replies = {
        "hi": "Hello! 👋",
        "hello": "Hi there 😊",
        "how are you": "I'm fine — thanks!",
        "help": "Use /help to see available commands."
    }
    for k, v in replies.items():
        if k in text:
            await update.message.reply_text(v)
            return

# -------------------------
# Commands
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    await update.message.reply_text("👋 Welcome to SecureBot! Use /help to see commands.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/start - show welcome\n"
        "/help - this help\n"
        "/gen [len] - generate password (default 16)\n"
        "/gen10 - generate 10 passwords\n"
        "/about - about bot\n"
        "/status - bot status\n"
        "/broadcast <text> - admin only\n"
        "/ban (reply) - admin\n"
        "/mute (reply) - admin\n"
        "/unmute (reply) - admin\n"
    )
    await update.message.reply_text(txt)

async def cmd_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    length = 16
    if context.args:
        try:
            length = max(6, min(64, int(context.args[0])))
        except:
            length = 16
    pwd = generate_password(length)
    await update.message.reply_text(f"🔐 {pwd}")

async def cmd_gen10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwds = [generate_password(14) for _ in range(10)]
    out = "\n".join(f"{i+1}. {p}" for i, p in enumerate(pwds))
    await update.message.reply_text("🔢 10 Passwords:\n" + out)

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(ABOUT_PATH):
        with open(ABOUT_PATH, "r", encoding="utf-8") as f:
            await update.message.reply_text(f.read())
    else:
        await update.message.reply_text("No about info.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is Online")

async def cmd_visitors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Admin only")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, first_name, added_at FROM users ORDER BY added_at DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return await update.message.reply_text("No visitors.")
    out = "\n".join(f"{r[2]} @{r[1]} ({r[0]})" for r in rows)
    await update.message.reply_text(out[:3900])

# Broadcast (admin)
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Admin only")
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <message>")
    msg = " ".join(context.args)
    users = get_all_user_ids()
    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, msg)
            sent += 1
            await asyncio.sleep(0.05)  # small pause to avoid flood
        except Exception:
            pass
    await update.message.reply_text(f"📢 Sent to {sent} users")

# Moderation commands
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user to ban.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"🚫 Banned {target.mention_html()}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("Failed to ban: " + str(e))

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user to kick.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)  # ensure not banned
        await update.message.reply_text(f"👢 Kicked {target.mention_html()}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("Failed to kick: " + str(e))

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user to mute.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"🔇 Muted {target.mention_html()}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("Failed to mute: " + str(e))

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user to unmute.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, ChatPermissions(can_send_messages=True))
        await update.message.reply_text(f"🔊 Unmuted {target.mention_html()}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("Failed to unmute: " + str(e))

# -------------------------
# Message pipeline handlers
# -------------------------
async def pipeline_handlers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1) Save user
    user = update.effective_user
    if user:
        save_user(user)

    # 2) Anti-spam check
    cnt = spam_record_and_check(user.id)
    if cnt >= SPAM_LIMIT:
        await handle_spam_action(update, context, cnt)
        return

    # 3) Auto-reply
    await auto_reply_handler(update, context)

    # 4) Auto-delete
    # Note: auto-delete runs in background to avoid blocking
    asyncio.create_task(auto_delete_handler(update, context))

# -------------------------
# Main
# -------------------------
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment")
        sys.exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("gen", cmd_gen))
    app.add_handler(CommandHandler("gen10", cmd_gen10))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("visitors", cmd_visitors))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    # welcome handler for groups
    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    # pipeline for messages (hybrid)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pipeline_handlers))

    logger.info("SecureBot (Hybrid) starting...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())