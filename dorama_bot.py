#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import asyncio
import hashlib
import logging
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument

# =============================================================================
# CONFIG
# =============================================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHAVE_SECRETA = os.environ.get("CHAVE_SECRETA", "DORAMA2026KEY")

LIMITE_GRATIS = 5
DATA_FILE = Path("usuarios.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)

# =============================================================================
# HEALTHCHECK SERVER
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
# BANCO LOCAL
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


def get_user(uid: int):
    data = load_data()
    uid = str(uid)

    if uid not in data:
        data[uid] = {
            "downloads": 0,
            "serial": None,
            "nome_serial": None,
        }
        save_data(data)

    return data[uid]


def update_user(uid: int, **kwargs):
    data = load_data()
    uid = str(uid)

    if uid not in data:
        data[uid] = {
            "downloads": 0,
            "serial": None,
            "nome_serial": None,
        }

    data[uid].update(kwargs)
    save_data(data)


# =============================================================================
# SERIAL / PRO
# =============================================================================

def gerar_serial(nome: str, data_inicio=None):
    if data_inicio is None:
        data_inicio = datetime.now()

    periodo = data_inicio.strftime("%Y%m%d")
    entrada = f"{nome.upper().strip()}|{periodo}|{CHAVE_SECRETA}"
    h = hashlib.sha256(entrada.encode()).hexdigest().upper()
    expira = (data_inicio + timedelta(days=30)).strftime("%d%m")
    return f"KWAI-{h[0:4]}-{h[8:12]}-{expira}"


def validar_serial(nome: str, serial: str):
    serial = serial.strip().upper()
    partes = serial.split("-")

    if len(partes) != 4 or partes[0] != "KWAI":
        return False, 0, "Formato inválido"

    try:
        ddmm = partes[3]
        dia, mes = int(ddmm[0:2]), int(ddmm[2:4])
        ano = datetime.now().year
        expira = datetime(ano, mes, dia)

        if expira < datetime.now() - timedelta(days=35):
            expira = datetime(ano + 1, mes, dia)
    except Exception:
        return False, 0, "Data inválida"

    hoje = datetime.now()
    if expira < hoje:
        return False, 0, f"Serial expirado há {(hoje - expira).days} dia(s)"

    dias = (expira - hoje).days + 1

    for delta in range(31):
        dt = expira - timedelta(days=30) + timedelta(days=delta)
        if gerar_serial(nome, dt) == serial:
            return True, dias, f"Válido por {dias} dia(s)"

    return False, 0, "Serial inválido"


def is_pro(uid: int):
    u = get_user(uid)
    if not u.get("serial") or not u.get("nome_serial"):
        return False, 0

    ok, dias, _ = validar_serial(u["nome_serial"], u["serial"])
    return ok, dias


def pode_baixar(uid: int):
    pro, dias = is_pro(uid)
    if pro:
        return True, f"PRO ativo — {dias} dia(s) restante(s)"

    u = load_data().get(str(uid), {})
    n = u.get("downloads", 0)
    restantes = LIMITE_GRATIS - n

    if restantes > 0:
        return True, f"Grátis: {restantes} download(s) restante(s)"

    return False, "Limite grátis atingido"


# =============================================================================
# TELETHON
# =============================================================================

_tg_client = None


async def get_client():
    global _tg_client

    if _tg_client is None:
        _tg_client = TelegramClient("sessao_bot", API_ID, API_HASH)
        await _tg_client.start(bot_token=BOT_TOKEN)
        log.info("Cliente Telethon iniciado")

    return _tg_client


# =============================================================================
# UTIL
# =============================================================================

def parse_tg_link(text: str):
    text = text.strip()

    m = re.search(r"(?:t\.me|telegram\.me)/c/(\d+)/(\d+)", text)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))

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

    uid = update.effective_user.id
    nome = update.effective_user.first_name or "amigo"
    pro, dias = is_pro(uid)
    u = get_user(uid)
    restantes = max(0, LIMITE_GRATIS - u.get("downloads", 0))

    if pro:
        status = f"⭐ *PRO ativo* — {dias} dia(s) restante(s)"
    else:
        status = f"🆓 *Grátis* — {restantes}/{LIMITE_GRATIS} downloads restantes"

    texto = (
        f"👋 Olá, *{nome}*!\n\n"
        f"📺 *DoramaDown Bot*\n"
        f"Baixe vídeos do Telegram.\n\n"
        f"{status}\n\n"
        f"*Como usar:*\n"
        f"1. Copie o link da mensagem no Telegram\n"
        f"2. Cole aqui no bot\n"
        f"3. Aguarde o download\n\n"
        f"_Exemplo:_ `https://t.me/canal/123`"
    )

    kbd = []
    if not pro:
        kbd.append([InlineKeyboardButton("⭐ Ativar PRO", callback_data="ativar_pro")])
    kbd.append([InlineKeyboardButton("❓ Ajuda", callback_data="ajuda")])

    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kbd),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    pro, dias = is_pro(uid)
    u = get_user(uid)
    n = u.get("downloads", 0)

    if pro:
        await update.message.reply_text(
            f"⭐ *Status: PRO*\n\n"
            f"✅ Downloads ilimitados\n"
            f"📅 Expira em: {dias} dia(s)",
            parse_mode="Markdown",
        )
    else:
        restantes = max(0, LIMITE_GRATIS - n)
        await update.message.reply_text(
            f"🆓 *Status: Grátis*\n\n"
            f"📥 Downloads usados: {n}/{LIMITE_GRATIS}\n"
            f"📥 Restantes: {restantes}",
            parse_mode="Markdown",
        )


async def cmd_ativar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "🔑 *Ativar PRO*\n\n"
        "Use:\n"
        "`/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`\n\n"
        "_Exemplo:_ `/serial João Silva | KWAI-A1B2-C3D4-1504`",
        parse_mode="Markdown",
    )


async def cmd_serial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    text = update.message.text.replace("/serial", "", 1).strip()

    if "|" not in text:
        await update.message.reply_text(
            "❌ Formato incorreto.\n\n"
            "Use: `/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`",
            parse_mode="Markdown",
        )
        return

    nome, serial = [p.strip() for p in text.split("|", 1)]
    ok, dias, msg = validar_serial(nome, serial)

    if ok:
        update_user(uid, serial=serial.upper(), nome_serial=nome.upper(), downloads=0)
        await update.message.reply_text(
            f"✅ *PRO ativado!*\n\n"
            f"👤 Nome: {nome}\n"
            f"📅 Válido por {dias} dia(s)",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"❌ *Serial inválido*\n\nMotivo: {msg}",
            parse_mode="Markdown",
        )


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    data = load_data()
    total = len(data)
    pros = sum(1 for u in data.values() if u.get("serial"))
    dls = sum(u.get("downloads", 0) for u in data.values())

    await update.message.reply_text(
        f"📊 *Painel Admin*\n\n"
        f"👥 Usuários: {total}\n"
        f"⭐ PRO ativos: {pros}\n"
        f"📥 Downloads totais: {dls}\n\n"
        f"Use `/gerar NOME DO CLIENTE`",
        parse_mode="Markdown",
    )


async def cmd_gerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    nome = update.message.text.replace("/gerar", "", 1).strip()
    if not nome:
        await update.message.reply_text(
            "Use: `/gerar NOME DO CLIENTE`",
            parse_mode="Markdown"
        )
        return

    serial = gerar_serial(nome)
    expira = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")

    await update.message.reply_text(
        f"✅ Serial gerado para *{nome}*:\n\n"
        f"`{serial}`\n\n"
        f"Expira: {expira}",
        parse_mode="Markdown",
    )


# =============================================================================
# CALLBACKS
# =============================================================================

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()

    if q.data == "ativar_pro":
        await q.message.reply_text(
            "⭐ *Ativar PRO*\n\n"
            "Depois use:\n"
            "`/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`",
            parse_mode="Markdown",
        )

    elif q.data == "ajuda":
        await q.message.reply_text(
            "❓ *Ajuda*\n\n"
            "1. Copie o link do vídeo no Telegram\n"
            "2. Cole aqui no bot\n"
            "3. Aguarde o download\n\n"
            "*Comandos:*\n"
            "/start\n"
            "/status\n"
            "/ativar\n"
            "/serial",
            parse_mode="Markdown",
        )


# =============================================================================
# DOWNLOAD ESTÁVEL COM BARRA DE PROGRESSO
# =============================================================================

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    text = update.message.text.strip()

    if "t.me" not in text and "telegram.me" not in text:
        await update.message.reply_text(
            "📎 Envie um link válido do Telegram.\n\n"
            "_Exemplo:_ `https://t.me/canal/123`",
            parse_mode="Markdown",
        )
        return

    pode, _ = pode_baixar(uid)
    if not pode:
        await update.message.reply_text(
            "🚫 Você atingiu o limite grátis.\nUse /ativar para liberar o PRO.",
            parse_mode="Markdown",
        )
        return

    chat_id, msg_id = parse_tg_link(text)
    if not chat_id or not msg_id:
        await update.message.reply_text(
            "❌ Link inválido.\nCertifique-se de copiar o link completo da mensagem.",
            parse_mode="Markdown",
        )
        return

    status_msg = await update.message.reply_text("⏳ Preparando download...")

    try:
        client = await get_client()
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

        if tamanho_mb > 700:
            await status_msg.edit_text(
                f"❌ Vídeo muito grande ({tamanho_mb} MB).\n"
                f"No plano atual o limite seguro é 700 MB."
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
                log.info("Tentativa de download %s/3", tentativa + 1)

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
                log.warning("Download excedeu o tempo na tentativa %s", tentativa + 1)
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
                "Tente novamente com outro vídeo ou mais tarde."
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

        pro, _ = is_pro(uid)
        if not pro:
            u = get_user(uid)
            novos = u.get("downloads", 0) + 1
            update_user(uid, downloads=novos)

    except Exception as e:
        log.exception("Erro no download: %s", e)
        try:
            await status_msg.edit_text(
                "❌ Erro ao baixar o vídeo.\n"
                "Confira se o link está certo e se o bot tem acesso."
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
    app.add_handler(CommandHandler("ativar", cmd_ativar))
    app.add_handler(CommandHandler("serial", cmd_serial))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("gerar", cmd_gerar))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    log.info("🤖 DoramaDown Bot iniciado!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
