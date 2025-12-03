import os
import json
import random
import logging
from datetime import datetime, time as dtime
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("Please set TELEGRAM_TOKEN environment variable. Exiting.")
    exit(1)

# Load data files
def load_json(fname):
    with open(fname, 'r', encoding='utf-8') as f:
        return json.load(f)

grammar = load_json("grammar.json")
vocab = load_json("vocabulary.json")
puzzles = load_json("puzzles.json")
lessons = load_json("lessons.json")

# Utility functions
def choose_for_level(items, level):
    # items is list of dicts with optional 'level' field
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
    # store correct answers in a simple dict to be checked later via context.user_data
    return {
        "text": (
            "🌅 *Good morning!* Here is your daily English practice:\n\n"
            f"📝 *Grammar:*\n{g['q']}\n\n"
            f"📚 *Vocabulary:*\nWord: *{v['word']}*\nMeaning: {v['meaning']}\nExample: {v['example']}\n\n"
            f"🧠 *Puzzle:*\n{p['q']}\n\n"
            f"📖 *Mini Lesson:*\n{l['text']}\n\n"
            "➡ Reply with your answers in one message, separated by `||` like:\n"
            "`B || I am going to school`\n"
            "Where the first part is the grammar option (A/B/C) and the second is the puzzle answer."
        ),
        "answers": {
            "grammar": g.get('answer'),
            "puzzle": p.get('answer')
        },
        "ids": {
            "grammar_id": g.get('id'),
            "puzzle_id": p.get('id')
        }
    }

# Bot commands
def start(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or ''
    database.add_user(uid, username)
    update.message.reply_text(
        "🎓 *English THALA Bot*\nHello! I will help you learn English daily.\n"
        "Type /daily to get today's practice, /score to see your score, /level to see your level, /help for commands.",
        parse_mode=ParseMode.MARKDOWN
    )

def help_cmd(update: Update, context: CallbackContext):
    update.message.reply_text(
        "/daily - get today's tasks\n"
        "/score - show your score\n"
        "/level - show level\n"
        "/streak - show your streak\n"
        "Reply to quizzes like: B || I am going to school"
    )

def daily_cmd(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    database.add_user(uid, update.effective_user.username or '')
    payload = format_daily_payload(uid)
    # save answers temporarily in user_data
    context.user_data['pending_answers'] = payload['answers']
    context.user_data['pending_ids'] = payload['ids']
    update.message.reply_text(payload['text'], parse_mode=ParseMode.MARKDOWN)

def check_answer(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    text = update.message.text.strip()
    if 'pending_answers' not in context.user_data:
        update.message.reply_text("No active quiz found. Type /daily to get today's tasks.")
        return
    parts = [p.strip() for p in text.split('||')]
    if len(parts) < 2:
        update.message.reply_text("Format wrong. Use: `B || answer to puzzle`.")
        return
    grammar_ans = parts[0]
    puzzle_ans = parts[1]

    correct = context.user_data['pending_answers']
    gained = 0
    if grammar_ans.lower() == str(correct.get('grammar','')).lower():
        gained += 1
    if puzzle_ans.lower() == str(correct.get('puzzle','')).lower():
        gained += 1
    if gained > 0:
        database.increment_score(uid, gained)
        database.set_level_by_score(uid)
    # update streak and last active
    new_streak = database.update_last_active_and_streak(uid)
    database.set_level_by_score(uid)
    update.message.reply_text(f"✅ You got {gained} correct!\n🏆 Total Score: {database.get_user(uid)[3]}\n🎯 Level: {database.get_user(uid)[2]}\n🔥 Streak: {new_streak}")
    # clear pending
    context.user_data.pop('pending_answers', None)
    context.user_data.pop('pending_ids', None)

def score_cmd(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    u = database.get_user(uid)
    if not u:
        update.message.reply_text("No record found. Type /start first.")
        return
    update.message.reply_text(f"🏆 Score: {u[3]}\n🎯 Level: {u[2]}\n🔥 Streak: {u[4]}")

def level_cmd(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    u = database.get_user(uid)
    if not u:
        update.message.reply_text("No record found. Type /start first.")
        return
    update.message.reply_text(f"🎯 Your Level: {u[2]}")

def streak_cmd(update: Update, context: CallbackContext):
    uid = update.effective_chat.id
    u = database.get_user(uid)
    if not u:
        update.message.reply_text("No record found. Type /start first.")
        return
    update.message.reply_text(f"🔥 Your Streak: {u[4]} days")

# Scheduler: send daily auto message at 08:00 server time
def send_daily_to_all(context_dispatcher):
    logger.info("Running scheduled daily send to all users...")
    users = []
    # fetch all users from DB
    import sqlite3
    conn = sqlite3.connect("data.db", check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    users = [r[0] for r in rows]
    for uid in users:
        try:
            payload = format_daily_payload(uid)
            # store answers in a lightweight per-chat store on the bot object
            bot = context_dispatcher.bot
            bot.send_message(chat_id=uid, text=payload['text'], parse_mode=ParseMode.MARKDOWN)
            # we cannot access per-user context here to store pending answers; rely on users to type /daily to get checkable quiz
            # or use an external DB to store pending questions per user if you want answer-check after auto send
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('help', help_cmd))
    dp.add_handler(CommandHandler('daily', daily_cmd))
    dp.add_handler(CommandHandler('score', score_cmd))
    dp.add_handler(CommandHandler('level', level_cmd))
    dp.add_handler(CommandHandler('streak', streak_cmd))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, check_answer))

    # start scheduler
    scheduler = BackgroundScheduler(timezone='UTC')
    # Schedule at 08:00 UTC (change if you want local timezone like Asia/Kolkata)
    scheduler.add_job(lambda: send_daily_to_all(updater), 'cron', hour=8, minute=0)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
