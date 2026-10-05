import os
import time
import sqlite3
from collections import defaultdict
from types import SimpleNamespace
from html import escape

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Kolkata")
SPAM_LIMIT = int(os.getenv("SPAM_LIMIT", "3"))
SPAM_WINDOW = int(os.getenv("SPAM_WINDOW", "3"))

# Vercel uses an external Postgres database when DATABASE_URL is set.
# Local Windows use falls back to SQLite.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    db = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)
else:
    DB_NAME = os.getenv(
        "DB_NAME",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "blackberry_bot.db"),
    )
    db = sqlite3.connect(DB_NAME, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")

def q(sql):
    """Translate simple SQLite placeholders to PostgreSQL placeholders."""
    return sql.replace("?", "%s") if USE_POSTGRES else sql

def execute(sql, params=()):
    cur = db.cursor()
    cur.execute(q(sql), params)
    return cur

def commit():
    if not USE_POSTGRES:
        db.commit()

def init_db():
    if USE_POSTGRES:
        statements = [
            """CREATE TABLE IF NOT EXISTS approved_users (
                chat_id BIGINT, user_id BIGINT, name TEXT,
                approved_by BIGINT, created_at BIGINT,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS tracked_members (
                chat_id BIGINT, user_id BIGINT, username TEXT, full_name TEXT,
                joined_at BIGINT, last_seen BIGINT,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS blocked_users (
                chat_id BIGINT, user_id BIGINT, name TEXT,
                blocked_by BIGINT, created_at BIGINT,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS admin_logs (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT, actor_id BIGINT, action TEXT,
                target_id BIGINT, target_name TEXT, created_at BIGINT
            )""",
            """CREATE TABLE IF NOT EXISTS spam_messages (
                chat_id BIGINT, user_id BIGINT, message_id BIGINT,
                created_at BIGINT
            )""",
        ]
    else:
        statements = [
            """CREATE TABLE IF NOT EXISTS approved_users (
                chat_id INTEGER, user_id INTEGER, name TEXT,
                approved_by INTEGER, created_at INTEGER,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS tracked_members (
                chat_id INTEGER, user_id INTEGER, username TEXT, full_name TEXT,
                joined_at INTEGER, last_seen INTEGER,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS blocked_users (
                chat_id INTEGER, user_id INTEGER, name TEXT,
                blocked_by INTEGER, created_at INTEGER,
                PRIMARY KEY(chat_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, actor_id INTEGER, action TEXT,
                target_id INTEGER, target_name TEXT, created_at INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS spam_messages (
                chat_id INTEGER, user_id INTEGER, message_id INTEGER,
                created_at INTEGER
            )""",
        ]
    for statement in statements:
        execute(statement)
    commit()

init_db()

def log_action(chat_id, actor_id, action, target_id=None, target_name=None):
    execute(
        """INSERT INTO admin_logs
        (chat_id,actor_id,action,target_id,target_name,created_at)
        VALUES (?,?,?,?,?,?)""",
        (chat_id, actor_id, action, target_id, target_name, int(time.time())),
    )
    commit()

def track_member(user, chat_id):
    if not user or user.is_bot:
        return
    now = int(time.time())
    if USE_POSTGRES:
        execute(
            """INSERT INTO tracked_members
            (chat_id,user_id,username,full_name,joined_at,last_seen)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              username=EXCLUDED.username, full_name=EXCLUDED.full_name,
              last_seen=EXCLUDED.last_seen""",
            (chat_id,user.id,user.username,user.full_name,now,now),
        )
    else:
        execute(
            """INSERT INTO tracked_members
            (chat_id,user_id,username,full_name,joined_at,last_seen)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              username=excluded.username, full_name=excluded.full_name,
              last_seen=excluded.last_seen""",
            (chat_id,user.id,user.username,user.full_name,now,now),
        )
    commit()

def is_approved(chat_id, user_id):
    cur = execute(
        "SELECT 1 FROM approved_users WHERE chat_id=? AND user_id=?",
        (chat_id,user_id),
    )
    return cur.fetchone() is not None

def is_blocked(chat_id, user_id):
    cur = execute(
        "SELECT 1 FROM blocked_users WHERE chat_id=? AND user_id=?",
        (chat_id,user_id),
    )
    return cur.fetchone() is not None

async def is_admin(update, user_id=None):
    chat = update.effective_chat
    if not chat:
        return False
    if user_id is None:
        user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return False
    try:
        member = await chat.get_member(user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        return False

async def admin_required(update):
    if not update.effective_chat or update.effective_chat.type not in ("group","supergroup"):
        if update.message:
            await update.message.reply_text("❌ Ye command group me use karo.")
        return False
    if not await is_admin(update):
        if update.message:
            await update.message.reply_text("❌ Sirf group admins use kar sakte hain.")
        return False
    return True

def resolve_user_identifier(update, args=None):
    args = args or []
    if update.message and update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        if u:
            return u
    if not args:
        return None
    raw = args[0].strip()
    if raw.startswith("@"):
        raw = raw[1:]
    if raw.lstrip("-").isdigit():
        uid = int(raw)
        cur = execute(
            "SELECT user_id,username,full_name FROM tracked_members WHERE chat_id=? AND user_id=?",
            (update.effective_chat.id,uid),
        )
        row = cur.fetchone()
        if row:
            return SimpleNamespace(id=uid,username=row["username"] if USE_POSTGRES else row[1],
                                   full_name=row["full_name"] if USE_POSTGRES else row[2],
                                   is_bot=False)
        return SimpleNamespace(id=uid,username=None,full_name=str(uid),is_bot=False)
    cur = execute(
        """SELECT user_id,username,full_name FROM tracked_members
           WHERE chat_id=? AND lower(username)=lower(?)
           ORDER BY last_seen DESC LIMIT 1""",
        (update.effective_chat.id,raw),
    )
    row = cur.fetchone()
    if row:
        return SimpleNamespace(
            id=row["user_id"] if USE_POSTGRES else row[0],
            username=row["username"] if USE_POSTGRES else row[1],
            full_name=(row["full_name"] if USE_POSTGRES else row[2]) or raw,
            is_bot=False,
        )
    return None

async def approve_user(chat_id, actor_id, user_id, name, chat=None):
    if USE_POSTGRES:
        execute(
            """INSERT INTO approved_users(chat_id,user_id,name,approved_by,created_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET
               name=EXCLUDED.name,approved_by=EXCLUDED.approved_by,created_at=EXCLUDED.created_at""",
            (chat_id,user_id,name,actor_id,int(time.time())),
        )
    else:
        execute(
            """INSERT OR REPLACE INTO approved_users
               (chat_id,user_id,name,approved_by,created_at)
               VALUES (?,?,?,?,?)""",
            (chat_id,user_id,name,actor_id,int(time.time())),
        )
    execute("DELETE FROM blocked_users WHERE chat_id=? AND user_id=?", (chat_id,user_id))
    commit()
    log_action(chat_id,actor_id,"approve_free",user_id,name)

    if chat:
        try:
            await chat.approve_chat_join_request(user_id)
        except Exception:
            pass

async def approve_command(update, context):
    if not await admin_required(update):
        return
    u = resolve_user_identifier(update, context.args)
    if not u:
        await update.message.reply_text(
            "Reply to kisi user ke message par /approve ya /free likho.\n"
            "Ya: /approve @username\nYa: /approve USER_ID"
        )
        return
    await approve_user(
        update.effective_chat.id, update.effective_user.id,
        u.id, u.full_name, update.effective_chat
    )
    await update.message.reply_text(
        f"✅ Approved / Freed\n👤 {escape(u.full_name)}\n🆔 <code>{u.id}</code>\n"
        "🛡️ Spam protection se exempt.",
        parse_mode="HTML",
    )

async def free_command(update, context):
    await approve_command(update, context)

async def unapprove_command(update, context):
    if not await admin_required(update):
        return
    u = resolve_user_identifier(update, context.args)
    if not u:
        await update.message.reply_text("Reply + /unapprove, ya /unapprove @username / ID")
        return
    execute("DELETE FROM approved_users WHERE chat_id=? AND user_id=?",
            (update.effective_chat.id,u.id))
    commit()
    log_action(update.effective_chat.id,update.effective_user.id,"unapprove",u.id,u.full_name)
    await update.message.reply_text(
        f"🔒 Protection re-applied for {escape(u.full_name)}.",
        parse_mode="HTML",
    )

async def block_command(update, context):
    if not await admin_required(update):
        return
    u = resolve_user_identifier(update, context.args)
    if not u:
        await update.message.reply_text("Reply + /block, ya /block @username / ID")
        return
    cid = update.effective_chat.id
    execute(
        """INSERT INTO blocked_users(chat_id,user_id,name,blocked_by,created_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET name=excluded.name,
           blocked_by=excluded.blocked_by,created_at=excluded.created_at""" if not USE_POSTGRES else
        """INSERT INTO blocked_users(chat_id,user_id,name,blocked_by,created_at)
           VALUES(%s,%s,%s,%s,%s)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET name=EXCLUDED.name,
           blocked_by=EXCLUDED.blocked_by,created_at=EXCLUDED.created_at""",
        (cid,u.id,u.full_name,update.effective_user.id,int(time.time())),
    )
    commit()
    try:
        await update.effective_chat.ban_member(u.id)
    except Exception:
        pass
    log_action(cid,update.effective_user.id,"block_ban",u.id,u.full_name)
    await update.message.reply_text(f"🚫 Blocked/Banned: {escape(u.full_name)}",parse_mode="HTML")

async def unblock_command(update, context):
    if not await admin_required(update):
        return
    u = resolve_user_identifier(update, context.args)
    if not u:
        await update.message.reply_text("Reply + /unblock, ya /unblock @username / ID")
        return
    cid=update.effective_chat.id
    execute("DELETE FROM blocked_users WHERE chat_id=? AND user_id=?",(cid,u.id))
    commit()
    try:
        await update.effective_chat.unban_member(u.id, only_if_banned=True)
    except Exception:
        pass
    log_action(cid,update.effective_user.id,"unblock_unban",u.id,u.full_name)
    await update.message.reply_text(f"✅ Unblocked/Unbanned: {escape(u.full_name)}",parse_mode="HTML")

async def mute_command(update, context):
    if not await admin_required(update):
        return
    u=resolve_user_identifier(update,context.args)
    if not u:
        await update.message.reply_text("Reply + /mute, ya /mute @username / ID")
        return
    try:
        from telegram import ChatPermissions
        await update.effective_chat.restrict_member(
            u.id, permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"🔇 Muted: {escape(u.full_name)}",parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {e}")

async def unmute_command(update, context):
    if not await admin_required(update):
        return
    u=resolve_user_identifier(update,context.args)
    if not u:
        await update.message.reply_text("Reply + /unmute, ya /unmute @username / ID")
        return
    try:
        from telegram import ChatPermissions
        await update.effective_chat.restrict_member(
            u.id, permissions=ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await update.message.reply_text(f"🔊 Unmuted: {escape(u.full_name)}",parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {e}")

async def start(update, context):
    await update.message.reply_text("🤖 Blackberry Bot Active!")

async def ping_command(update, context):
    await update.message.reply_text("🏓 Pong! Active.")

async def help_command(update, context):
    await update.message.reply_text(
        "🤖 Blackberry Bot\n\n"
        "/approve or /free — reply to user to exempt from protection\n"
        "/unapprove — protection wapas\n"
        "/block — ban/block\n"
        "/unblock — unban/unblock\n"
        "/mute /unmute — mute controls\n"
        "/ping — status"
    )

def add_spam_message(chat_id,user_id,message_id):
    now=int(time.time())
    execute("DELETE FROM spam_messages WHERE created_at < ?",(now-SPAM_WINDOW,))
    execute("INSERT INTO spam_messages(chat_id,user_id,message_id,created_at) VALUES(?,?,?,?)",
            (chat_id,user_id,message_id,now))
    cur=execute(
        """SELECT message_id FROM spam_messages
           WHERE chat_id=? AND user_id=? AND created_at>=?
           ORDER BY created_at DESC LIMIT 20""",
        (chat_id,user_id,now-SPAM_WINDOW),
    )
    rows=cur.fetchall()
    commit()
    return [r["message_id"] if USE_POSTGRES else r[0] for r in rows]

def clear_spam_messages(chat_id,user_id):
    execute("DELETE FROM spam_messages WHERE chat_id=? AND user_id=?",(chat_id,user_id))
    commit()

async def send_spam_admin_controls(message, user):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVE / FREE", callback_data=f"free:{message.chat_id}:{user.id}"),
            InlineKeyboardButton("🚫 BAN", callback_data=f"ban:{message.chat_id}:{user.id}"),
        ],
        [
            InlineKeyboardButton("🔒 UNAPPROVE", callback_data=f"unapprove:{message.chat_id}:{user.id}"),
        ],
    ])
    return await message.reply_text(
        f"⚠️ <b>Spam detected</b>\n👤 {escape(user.full_name)}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        "Admin button dabakar user ko Free/Approve ya Ban kar sakte ho.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

async def message_handler(update, context):
    message=update.message
    if not message:
        return
    chat=update.effective_chat
    user=update.effective_user
    if not chat or chat.type not in ("group","supergroup") or not user or user.is_bot:
        return

    track_member(user,chat.id)

    if await is_admin(update,user.id) or is_approved(chat.id,user.id):
        return

    if is_blocked(chat.id,user.id):
        try:
            await message.delete()
        except Exception:
            pass
        return

    ids=add_spam_message(chat.id,user.id,message.message_id)
    if len(ids) >= SPAM_LIMIT:
        # Delete the recent spam burst, not just the last message.
        for mid in ids:
            try:
                await context.bot.delete_message(chat.id,mid)
            except Exception:
                pass
        clear_spam_messages(chat.id,user.id)

        # Send controls as a reply to the spammer's last message if possible.
        try:
            control=await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    f"⚠️ <b>Spam deleted</b>\n"
                    f"👤 {escape(user.full_name)}\n"
                    f"🆔 <code>{user.id}</code>\n\n"
                    "Admins: user ko Approve/Free ya Ban karne ke liye button use karo."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ APPROVE / FREE", callback_data=f"free:{chat.id}:{user.id}"),
                        InlineKeyboardButton("🚫 BAN", callback_data=f"ban:{chat.id}:{user.id}"),
                    ],
                    [InlineKeyboardButton("🔒 UNAPPROVE", callback_data=f"unapprove:{chat.id}:{user.id}")]
                ])
            )
            return
        except Exception:
            return

async def callback_handler(update, context):
    query=update.callback_query
    if not query:
        return
    await query.answer()
    data=(query.data or "").split(":")
    if len(data)!=3:
        return
    action,chat_id_s,user_id_s=data
    try:
        chat_id=int(chat_id_s); user_id=int(user_id_s)
    except ValueError:
        return

    # Verify the button clicker is actually an admin in that group.
    try:
        member=await context.bot.get_chat_member(chat_id,query.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER):
            await query.answer("❌ Sirf admins use kar sakte hain.",show_alert=True)
            return
    except Exception:
        await query.answer("❌ Admin verification failed.",show_alert=True)
        return

    cur=execute(
        "SELECT full_name,username FROM tracked_members WHERE chat_id=? AND user_id=?",
        (chat_id,user_id)
    )
    row=cur.fetchone()
    if row:
        name=row["full_name"] if USE_POSTGRES else row[0]
    else:
        name=str(user_id)

    if action=="free":
        await approve_user(chat_id,query.from_user.id,user_id,name,context.bot)
        result=f"✅ {name} is now APPROVED/FREE."
    elif action=="unapprove":
        execute("DELETE FROM approved_users WHERE chat_id=? AND user_id=?",(chat_id,user_id))
        commit()
        log_action(chat_id,query.from_user.id,"unapprove",user_id,name)
        result=f"🔒 Protection re-applied to {name}."
    elif action=="ban":
        execute(
            """INSERT INTO blocked_users(chat_id,user_id,name,blocked_by,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET name=excluded.name,
               blocked_by=excluded.blocked_by,created_at=excluded.created_at""" if not USE_POSTGRES else
            """INSERT INTO blocked_users(chat_id,user_id,name,blocked_by,created_at)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET name=EXCLUDED.name,
               blocked_by=EXCLUDED.blocked_by,created_at=EXCLUDED.created_at""",
            (chat_id,user_id,name,query.from_user.id,int(time.time()))
        )
        commit()
        try:
            await context.bot.ban_chat_member(chat_id,user_id)
        except Exception:
            pass
        log_action(chat_id,query.from_user.id,"block_ban",user_id,name)
        result=f"🚫 {name} banned."
    else:
        return

    try:
        await query.edit_message_text(result)
    except Exception:
        pass

def register_handlers(app):
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("ping",ping_command))
    app.add_handler(CommandHandler("help",help_command))
    app.add_handler(CommandHandler("approve",approve_command))
    app.add_handler(CommandHandler("free",free_command))
    app.add_handler(CommandHandler("unapprove",unapprove_command))
    app.add_handler(CommandHandler("block",block_command))
    app.add_handler(CommandHandler("unblock",unblock_command))
    app.add_handler(CommandHandler("mute",mute_command))
    app.add_handler(CommandHandler("unmute",unmute_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(~filters.COMMAND, message_handler))

def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to .env locally or Vercel Environment Variables.")
    app=Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    return app

def main():
    app=build_application()
    print("🤖 Blackberry Bot running...")
    app.run_polling(drop_pending_updates=False)

if __name__=="__main__":
    main()
