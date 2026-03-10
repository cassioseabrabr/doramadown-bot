import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

telegram_app = Application.builder().token(BOT_TOKEN).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online! Envie um link do Telegram.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Recebi sua mensagem!")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    await telegram_app.process_update(update)
    return "ok"


@app.route("/")
def index():
    return "Bot rodando."


if __name__ == "__main__":
    telegram_app.initialize()
    telegram_app.start()
    telegram_app.bot.set_webhook(url=f"https://doramadown-bot.onrender.com/{BOT_TOKEN}")
    app.run(host="0.0.0.0", port=PORT)
