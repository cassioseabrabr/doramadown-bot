#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import asyncio
import logging
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import MessageMediaDocument

# =============================================================================
# CONFIG
# =============================================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

DATA_FILE = Path("usuarios.json")
DOWNLOAD_LIMIT_MB = 700

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)

# guarda processo de login pendente em memória
pending_logins = {}

# cache de clientes conectados por usuário
user_clients = {}


# =============================================================================
# HEALTHCHECK
# =============================================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def start_http_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Servidor HTTP iniciado na porta %s", port)
    server.serve_forever()


# =============================================================================
# BANCO SIMPLES
# =============================================================================

def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Erro lendo usuarios.json: %s", e)
    return {}


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_user_data(uid: int):
    data = load_data()
    uid = str(uid)

    if uid not in data:
        data[uid] = {
            "telegram_session": None,
            "telegram_phone": None,
        }
        save_data(data)

    return data[uid]


def update_user_data(uid: int, **kwargs):
    data = load_data()
    uid = str(uid)

    if uid not in data:
        data[uid] = {
            "telegram_session": None,
            "telegram_phone": None,
        }

    data[uid].update(kwargs)
    save_data(data)


# =============================================================================
# TELETHON POR USUÁRIO
# =============================================================================

async def get_user_client(uid: int):
    uid_str = str(uid)

    if uid_str in user_clients:
        client = user_clients[uid_str]
        if not client.is_connected():
            await client.connect()
        return client

    user_data = get_user_data(uid)
    session_str = user_data.get("telegram_session")

    if not session_str:
        return None

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return None

    user_clients[uid_str] = client
    return client


async def disconnect_user_client(uid: int):
    uid_str = str(uid)
    client = user_clients.pop(uid_str, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass


# =============================================================================
# UTIL
# =============================================================================

def parse_tg_link(text: str):
    text = text.strip()

    # links tipo t.me/c/1234567890/123
    m = re.search(r"(?:t\.me|telegram\.me)/c/(\d+)/(\d+)", text)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))

    # links tipo t.me/canal/123
    m = re.search(r"(?:t\.me|telegram\.me)/([^/\s]+)/(\d+)", text)
    if m:
        username = m.group(1)
        if username.lower() == "c":
            return None, None
        return username, int(m.group(2))

    return None, None


def format_bar(percent: int, size=10):
    filled = int((percent / 100) * size)
    return "█" * filled + "░" * (size - filled)


# =============================================================================
# COMANDOS
# =============================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    texto = (
        "👋 *Bot de Download com Conta do Próprio Usuário*\n\n"
        "Esse modo funciona assim:\n"
        "1. Você conecta *a sua conta do Telegram*\n"
        "2. O bot baixa com *o acesso da sua conta*\n"
        "3. Então funciona em canais e grupos que *você* consegue ver\n\n"
        "*Passos:*\n"
        "1. `/login +5511999999999`\n"
        "2. Você recebe um código no Telegram\n"
        "3. Envie: `/code 12345`\n"
        "4. Se pedir 2FA, envie: `/senha SUASENHA`\n"
        "5. Depois mande o link da mensagem\n\n"
        "*Comandos:*\n"
        "/start\n"
        "/status\n"
        "/login +55...\n"
        "/code 12345\n"
        "/senha SUASENHA\n"
        "/logout"
    )

    await update.message.reply_text(texto, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    user_data = get_user_data(uid)

    if user_data.get("telegram_session"):
        phone = user_data.get("telegram_phone") or "conectado"
        await update.message.reply_text(
            f"✅ Conta conectada\n\nTelefone: `{phone}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ Nenhuma conta conectada ainda.\n\nUse `/login +5511999999999`",
            parse_mode="Markdown"
        )


async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    parts = update.message.text.split(maxsplit=1)

    if len(parts) < 2:
        await update.message.reply_text(
            "Use assim:\n`/login +5511999999999`",
            parse_mode="Markdown"
        )
        return

    phone = parts[1].strip()

    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()

        result = await client.send_code_request(phone)

        pending_logins[str(uid)] = {
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
            "client": client,
        }

        await update.message.reply_text(
            "📩 Código enviado para sua conta do Telegram.\n\n"
            "Agora envie:\n"
            "`/code 12345`",
            parse_mode="Markdown"
        )

    except Exception as e:
        log.exception("Erro no login: %s", e)
        await update.message.reply_text(
            "❌ Não consegui iniciar o login.\n"
            "Verifique o número e tente novamente."
        )


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    parts = update.message.text.split(maxsplit=1)

    if len(parts) < 2:
        await update.message.reply_text(
            "Use assim:\n`/code 12345`",
            parse_mode="Markdown"
        )
        return

    code = parts[1].strip()
    pending = pending_logins.get(str(uid))

    if not pending:
        await update.message.reply_text(
            "❌ Não há login pendente.\nUse `/login +5511999999999` primeiro.",
            parse_mode="Markdown"
        )
        return

    client = pending["client"]
    phone = pending["phone"]
    phone_code_hash = pending["phone_code_hash"]

    try:
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=phone_code_hash
        )

        session_str = client.session.save()
        update_user_data(uid, telegram_session=session_str, telegram_phone=phone)

        pending_logins.pop(str(uid), None)
        user_clients[str(uid)] = client

        await update.message.reply_text(
            "✅ Conta conectada com sucesso!\n\n"
            "Agora você já pode mandar o link da mensagem."
        )

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔐 Sua conta tem verificação em duas etapas.\n\n"
            "Envie:\n`/senha SUA_SENHA_2FA`",
            parse_mode="Markdown"
        )

    except Exception as e:
        log.exception("Erro confirmando código: %s", e)
        await update.message.reply_text(
            "❌ Código inválido ou expirado.\n"
            "Tente `/login` novamente."
        )
        try:
            await client.disconnect()
        except Exception:
            pass
        pending_logins.pop(str(uid), None)


async def cmd_senha(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    parts = update.message.text.split(maxsplit=1)

    if len(parts) < 2:
        await update.message.reply_text(
            "Use assim:\n`/senha SUA_SENHA_2FA`",
            parse_mode="Markdown"
        )
        return

    senha = parts[1].strip()
    pending = pending_logins.get(str(uid))

    if not pending:
        await update.message.reply_text(
            "❌ Não há login pendente.\nUse `/login +5511999999999` primeiro.",
            parse_mode="Markdown"
        )
        return

    client = pending["client"]
    phone = pending["phone"]

    try:
        await client.sign_in(password=senha)

        session_str = client.session.save()
        update_user_data(uid, telegram_session=session_str, telegram_phone=phone)

        pending_logins.pop(str(uid), None)
        user_clients[str(uid)] = client

        await update.message.reply_text(
            "✅ Conta conectada com sucesso!\n\n"
            "Agora você já pode mandar o link da mensagem."
        )

    except Exception as e:
        log.exception("Erro no 2FA: %s", e)
        await update.message.reply_text(
            "❌ Senha 2FA inválida."
        )


async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id

    pending = pending_logins.pop(str(uid), None)
    if pending:
        try:
            await pending["client"].disconnect()
        except Exception:
            pass

    await disconnect_user_client(uid)
    update_user_data(uid, telegram_session=None, telegram_phone=None)

    await update.message.reply_text("✅ Conta desconectada.")


# =============================================================================
# DOWNLOAD
# =============================================================================

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    text = update.message.text.strip()

    if "t.me" not in text and "telegram.me" not in text:
        await update.message.reply_text(
            "📎 Envie um link válido de mensagem do Telegram.\n\n"
            "Exemplo:\n`https://t.me/canal/123`",
            parse_mode="Markdown",
        )
        return

    client = await get_user_client(uid)
    if not client:
        await update.message.reply_text(
            "❌ Você ainda não conectou sua conta.\n\n"
            "Use `/login +5511999999999`",
            parse_mode="Markdown"
        )
        return

    chat_id, msg_id = parse_tg_link(text)
    if not chat_id or not msg_id:
        await update.message.reply_text(
            "❌ Link inválido.\n"
            "Copie o link completo da mensagem."
        )
        return

    status_msg = await update.message.reply_text("⏳ Preparando download...")

    try:
        msg_tg = await client.get_messages(chat_id, ids=msg_id)

        if not msg_tg or not msg_tg.media:
            await status_msg.edit_text("❌ Vídeo não encontrado.")
            return

        if not isinstance(msg_tg.media, MessageMediaDocument):
            await status_msg.edit_text("❌ Esta mensagem não contém um vídeo.")
            return

        doc = msg_tg.media.document
        if not getattr(doc, "mime_type", "").startswith("video"):
            await status_msg.edit_text("❌ O arquivo não é um vídeo.")
            return

        tamanho_mb = round(doc.size / 1024 / 1024, 1)

        if tamanho_mb > DOWNLOAD_LIMIT_MB:
            await status_msg.edit_text(
                f"❌ Vídeo muito grande ({tamanho_mb} MB).\n"
                f"Limite atual: {DOWNLOAD_LIMIT_MB} MB."
            )
            return

        nome_arq = f"video_{uid}_{msg_id}.mp4"
        for attr in doc.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                nome_arq = attr.file_name
                break

        caminho = Path("/tmp") / nome_arq
        loop = asyncio.get_running_loop()

        progresso = {
            "ultimo_percent": -1,
            "ultima_atualizacao": 0.0,
        }

        def progress_callback(atual, total):
            if total <= 0:
                return

            percent = int((atual / total) * 100)
            agora = time.time()

            if progresso["ultimo_percent"] != -1:
                if percent < 100 and percent - progresso["ultimo_percent"] < 10 and (agora - progresso["ultima_atualizacao"]) < 3:
                    return

            progresso["ultimo_percent"] = percent
            progresso["ultima_atualizacao"] = agora

            barra = format_bar(percent, 10)
            atual_mb = atual / 1024 / 1024
            total_mb = total / 1024 / 1024

            texto = (
                "⬇️ *Baixando vídeo...*\n\n"
                f"`{barra}` {percent}%\n"
                f"`{atual_mb:.1f} MB / {total_mb:.1f} MB`"
            )

            async def editar():
                try:
                    await status_msg.edit_text(texto, parse_mode="Markdown")
                except Exception:
                    pass

            try:
                loop.create_task(editar())
            except Exception:
                pass

        await status_msg.edit_text(
            f"⏳ *Iniciando download...*\n\n`0.0 MB / {tamanho_mb:.1f} MB`",
            parse_mode="Markdown",
        )

        baixou = False

        for tentativa in range(3):
            try:
                log.info("Tentativa de download %s/3 do usuário %s", tentativa + 1, uid)

                await asyncio.wait_for(
                    client.download_media(
                        msg_tg,
                        str(caminho),
                        progress_callback=progress_callback,
                        part_size_kb=512
                    ),
                    timeout=1800
                )

                baixou = True
                break

            except asyncio.TimeoutError:
                log.warning("Timeout no download, tentativa %s", tentativa + 1)
                if tentativa < 2:
                    await status_msg.edit_text("⚠️ Conexão lenta. Tentando novamente...")
                    await asyncio.sleep(3)

            except Exception as e:
                log.warning("Tentativa %s falhou: %s", tentativa + 1, e)
                if tentativa < 2:
                    await status_msg.edit_text("⚠️ Erro temporário. Tentando novamente...")
                    await asyncio.sleep(3)

        if not baixou:
            await status_msg.edit_text(
                "❌ O download falhou após várias tentativas.\n"
                "Confira se sua conta realmente tem acesso ao link."
            )
            return

        await status_msg.edit_text("📤 Enviando vídeo...")

        with open(caminho, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=nome_arq,
                caption=f"✅ *{nome_arq}*\n📦 {tamanho_mb} MB",
                parse_mode="Markdown",
            )

        try:
            caminho.unlink(missing_ok=True)
        except Exception:
            pass

        await status_msg.delete()

    except Exception as e:
        log.exception("Erro no download: %s", e)
        try:
            await status_msg.edit_text(
                "❌ Erro ao baixar o vídeo.\n"
                "Confira se o link está certo e se a sua conta tem acesso."
            )
        except Exception:
            pass


# =============================================================================
# MAIN
# =============================================================================

def main():
    threading.Thread(target=start_http_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("senha", cmd_senha))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    log.info("🤖 Bot iniciado no modo sessão por usuário")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
