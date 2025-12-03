import os
import json
import random
import logging
from datetime import datetime, time as dtime

from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from apscheduler.schedulers.background import BackgroundScheduler
import database
import sqlite3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("Please set TELEGRAM_TOKEN environment variable. Exiting.")
    exit(1)

# Load safely
def load_json(fname):
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {fname}: {e}")
        return []

grammar = load_json("grammar.json")
vocab = load_json("vocabulary.json")
puzzles = load_json("puzzles.json")
lessons = load_json("lessons.json")

# Utility
def choose_for_level(items, level):
    filtered = [i for i in items if ('level' not in i) or (i['level'] == level)]
    if not filtered:
        filtered = items
    return random.choice(filtered)

def format_daily_payload(uid):
    user = database.get_user(uid)
    level = user[2] if user else "Beginner"

    g = choose_for_level(grammar, level)
    v = choose_for_level(vocab, level)
    p = choose_for_level(puzzles, level)
    l = choose_for_level(lessons, level)

    return {
        "text": (
            "🌅 *Good morning!* Here is your daily English practice:\n\n"
            f"📝 *Grammar:*\n{g['q']}\n\n"
            f"📚 *Vocabulary:*\nWord: *{v['word']}*\nMeaning: {v['meaning']}\nExample: {v['example']}\n\n"
            f"🧠 *Puzzle:*\n{p['q']}\n\n"
            f"📖 *Mini Lesson:*\n{l['text']}\n\n"
            "➡ Reply like: `B || my answer`"
        ),
        "answers": {
            "grammar": g.get("answer"),
            "puzzle": p.get("answer")
        }
    }

# Commands
def start(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    database.add_user(uid, username)
    update.message.reply_text(
        "🎓 English THALA Bot\nDaily practice ready!\nUse /daily, /score, /level, /streak.",
        parse_mode=ParseMode.MARKDOWN
    )

def help_cmd(update: Update, context: CallbackContext):
    update.message.reply_text(
        "/daily - today's tasks\n"
        "/score - your score\n"
        "/level - your level\n"
        "/streak - daily streak\n"
        "Answer format: B || answer"
    )

def daily_cmd(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    database.add_user(uid, update.effective_user.username or "")
    payload = format_daily_payload(uid)

    context.user_data["pending"] = payload["answers"]
    update.message.reply_text(payload["text"], parse_mode=ParseMode.MARKDOWN)

def check_answer(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    msg = update.message.text.strip()

    if "pending" not in context.user_data:
        update.message.reply_text("No quiz active. Use /daily.")
        return

    parts = [p.strip() for p in msg.split("||")]
    if len(parts) < 2:
        update.message.reply_text("Wrong format. Use: B || answer")
        return

    grammar_ans = parts[0].lower()
    puzzle_ans = parts[1].lower()

    correct = context.user_data["pending"]
    gained = 0

    if grammar_ans == str(correct["grammar"]).lower():
        gained += 1
    if puzzle_ans == str(correct["puzzle"]).lower():
        gained += 1

    if gained > 0:
        database.increment_score(uid, gained)
        database.set_level_by_score(uid)

    streak = database.update_last_active_and_streak(uid)

    u = database.get_user(uid)
    update.message.reply_text(
        f"✅ Correct: {gained}\n🏆 Score: {u[3]}\n🎯 Level: {u[2]}\n🔥 Streak: {u[4]}"
    )

    context.user_data.pop("pending", None)

def score_cmd(update, context):
    u = database.get_user(update.effective_chat.id)
    if not u:
        update.message.reply_text("No record. Use /start.")
        return
    update.message.reply_text(f"🏆 Score: {u[3]}\n🎯 Level: {u[2]}\n🔥 Streak: {u[4]}")

def level_cmd(update, context):
    u = database.get_user(update.effective_chat.id)
    if not u:
        update.message.reply_text("No record. Use /start.")
        return
    update.message.reply_text(f"🎯 Level: {u[2]}")

def streak_cmd(update, context):
    u = database.get_user(update.effective_chat.id)
    if not u:
        update.message.reply_text("No record. Use /start.")
        return
    update.message.reply_text(f"🔥 Streak: {u[4]} days")

# Scheduler
def send_daily_to_all(context):
    conn = sqlite3.connect("data.db", check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = [r[0] for r in cur.fetchall()]
    conn.close()

    for uid in users:
        try:
            payload = format_daily_payload(uid)
            context.bot.send_message(
                chat_id=uid,
                text=payload["text"],
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(CommandHandler("daily", daily_cmd))
    dp.add_handler(CommandHandler("score", score_cmd))
    dp.add_handler(CommandHandler("level", level_cmd))
    dp.add_handler(CommandHandler("streak", streak_cmd))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, check_answer))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        send_daily_to_all,
        trigger="cron",
        hour=8,
        minute=0,
        args=[updater.dispatcher],
    )
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
