import os
import sqlite3
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_USERNAME = "K2KDeliverBot"
DB = "episodes.db"

conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""
CREATE TABLE IF NOT EXISTS batches (
    code TEXT PRIMARY KEY,
    story TEXT,
    episodes TEXT,
    minutes INTEGER,
    photo TEXT,
    finished INTEGER DEFAULT 0
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    file_id TEXT,
    kind TEXT
)
""")
conn.commit()


def is_admin(update):
    return update.effective_user.id == ADMIN_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Welcome to K2K STORYS! 🎧\n"
            "Episodes पाने के लिए अपना episode link खोलें."
        )
        return

    code = context.args[0]
    batch = conn.execute(
        "SELECT story, episodes, minutes, photo "
        "FROM batches WHERE code=? AND finished=1",
        (code,)
    ).fetchone()

    if not batch:
        await update.message.reply_text("यह episode link उपलब्ध नहीं है.")
        return

    await deliver(update.effective_chat.id, code, context)


async def deliver(chat_id, code, context):
    batch = conn.execute(
        "SELECT story, episodes, minutes, photo "
        "FROM batches WHERE code=? AND finished=1",
        (code,)
    ).fetchone()

    if not batch:
        return

    story, episodes, minutes, photo = batch
    sent = []

    caption = (
        f"🎧 Story: {story}\n"
        f"📚 Episodes: {episodes}\n"
        f"📢 Presented by K2K STORYS"
    )

    if photo:
        msg = await context.bot.send_photo(
            chat_id, photo, caption=caption
        )
    else:
        msg = await context.bot.send_message(chat_id, caption)

    sent.append(msg.message_id)

    files = conn.execute(
        "SELECT file_id, kind FROM media WHERE code=? ORDER BY id",
        (code,)
    ).fetchall()

    for file_id, kind in files:
        if kind == "audio":
            msg = await context.bot.send_audio(chat_id, file_id)
        else:
            msg = await context.bot.send_document(chat_id, file_id)
        sent.append(msg.message_id)

    # Delete delivered messages after the batch timer
    async def cleanup():
        await asyncio.sleep(max(1, minutes) * 60)

        for message_id in sent:
            try:
                await context.bot.delete_message(
                    chat_id, message_id
                )
            except Exception:
                pass

        keyboard = [[
            InlineKeyboardButton(
                "🔁 Get Episodes Again",
                callback_data=f"again:{code}"
            )
        ]]

        await context.bot.send_message(
            chat_id,
            "⏰ आपका episode session समाप्त हो गया है।",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    asyncio.create_task(cleanup())


async def newbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    raw = update.message.text.partition(" ")[2]
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) != 4:
        await update.message.reply_text(
            "Format:\n"
            "/newbatch batch001|Story Name|Episodes 1-10|60"
        )
        return

    code, story, episodes, minutes = parts

    try:
        minutes = int(minutes)
        if minutes < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Timer minutes में 1 या उससे अधिक संख्या डालें."
        )
        return

    conn.execute(
        "INSERT OR REPLACE INTO batches "
        "(code, story, episodes, minutes, photo, finished) "
        "VALUES (?, ?, ?, ?, NULL, 0)",
        (code, story, episodes, minutes)
    )
    conn.execute("DELETE FROM media WHERE code=?", (code,))
    conn.commit()

    context.user_data["batch"] = code

    await update.message.reply_text(
        f"✅ Batch शुरू: {code}\n"
        "अब इस चैट में story photo और audio files भेजो.\n"
        "सब भेजने के बाद /finishbatch लिखो."
    )


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    code = context.user_data.get("batch")
    if not code:
        return

    photo = update.message.photo
    audio = update.message.audio
    document = update.message.document

    if photo:
        file_id = photo[-1].file_id
        conn.execute(
            "UPDATE batches SET photo=? WHERE code=?",
            (file_id, code)
        )
        conn.commit()
        await update.message.reply_text("🖼️ Story photo saved!")
        return

    if audio:
        file_id, kind = audio.file_id, "audio"
    elif document:
        file_id, kind = document.file_id, "document"
    else:
        return

    conn.execute(
        "INSERT INTO media (code, file_id, kind) VALUES (?, ?, ?)",
        (code, file_id, kind)
    )
    conn.commit()
    await update.message.reply_text("✅ Episode saved!")


async def finishbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    code = context.user_data.get("batch")
    if not code:
        await update.message.reply_text(
            "पहले /newbatch से batch बनाओ."
        )
        return

    conn.execute(
        "UPDATE batches SET finished=1 WHERE code=?", (code,)
    )
    conn.commit()

    link = f"https://t.me/{BOT_USERNAME}?start={code}"
    context.user_data.pop("batch", None)

    await update.message.reply_text(
        f"🎉 Batch तैयार है!\n\n"
        f"Story link:\n{link}\n\n"
        "इस लिंक को कॉपी करके शेयर कर सकते हो."
    )


async def again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = query.data.split(":", 1)[1]
    batch = conn.execute(
        "SELECT finished FROM batches WHERE code=?", (code,)
    ).fetchone()

    if batch and batch[0] == 1:
        await deliver(query.message.chat_id, code, context)
    else:
        await query.message.reply_text("यह batch उपलब्ध नहीं है.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "K2K STORYS Help 🎧\n"
        "अपनी episode link खोलकर episodes प्राप्त करें."
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newbatch", newbatch))
    app.add_handler(CommandHandler("finishbatch", finishbatch))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(again, pattern=r"^again:"))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.AUDIO | filters.Document.ALL,
        save_media
    ))

    print("K2K STORYS Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
