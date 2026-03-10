#!/usr/bin/env python3
"""
DoramaDown Bot — Bot do Telegram
5 downloads grátis, depois PRO (serial 30 dias)
Pronto para Render Web Service (abre porta HTTP para healthcheck)
"""

import os
import re
import json
import hashlib
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from pathlib import Path

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

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
CHAVE_SECRETA = os.environ.get("CHAVE_SECRETA", "KWAIBOOST_DORAMA_2026_X9Z")
LIMITE_GRATIS = 5
ADMIN_ID = int(os.environ["ADMIN_ID"])

DATA_FILE = Path("usuarios.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s"
)
log = logging.getLogger(__name__)

# ── Servidor HTTP simples para o Render não derrubar o serviço ───────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return  # evita poluir os logs


def start_http_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Servidor HTTP iniciado na porta %s", port)
    server.serve_forever()


# ── Dados de usuários ─────────────────────────────────────────────────────────
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


def get_user(uid):
    data = load_data()
    uid = str(uid)
    if uid not in data:
        data[uid] = {
            "downloads": 0,
            "serial": None,
            "nome_serial": None,
            "expira": None,
        }
        save_data(data)
    return data[uid]


def update_user(uid, **kwargs):
    data = load_data()
    uid = str(uid)
    if uid not in data:
        data[uid] = {
            "downloads": 0,
            "serial": None,
            "nome_serial": None,
            "expira": None,
        }
    data[uid].update(kwargs)
    save_data(data)


# ── Serial ────────────────────────────────────────────────────────────────────
def gerar_serial(nome, data_inicio=None):
    if data_inicio is None:
        data_inicio = datetime.now()

    periodo = data_inicio.strftime("%Y%m%d")
    entrada = f"{nome.upper().strip()}|{periodo}|{CHAVE_SECRETA}"
    h = hashlib.sha256(entrada.encode()).hexdigest().upper()
    expira = (data_inicio + timedelta(days=30)).strftime("%d%m")
    return f"KWAI-{h[0:4]}-{h[8:12]}-{expira}"


def validar_serial(nome, serial):
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


def is_pro(uid):
    u = get_user(uid)
    if not u.get("serial") or not u.get("nome_serial"):
        return False, 0

    ok, dias, _ = validar_serial(u["nome_serial"], u["serial"])
    return ok, dias


def pode_baixar(uid):
    pro, dias = is_pro(uid)
    if pro:
        return True, f"PRO ativo — {dias} dia(s) restante(s)"

    u = load_data().get(str(uid), {})
    n = u.get("downloads", 0)
    restantes = LIMITE_GRATIS - n

    if restantes > 0:
        return True, f"Grátis: {restantes} download(s) restante(s)"

    return False, "Limite grátis atingido"


# ── Cliente Telethon ──────────────────────────────────────────────────────────
_tg_client = None


async def get_client():
    global _tg_client
    if _tg_client is None:
        _tg_client = TelegramClient("sessao_bot", API_ID, API_HASH)
        await _tg_client.start(bot_token=BOT_TOKEN)
        log.info("Cliente Telethon iniciado")
    return _tg_client


# ── Extrair link do Telegram ──────────────────────────────────────────────────
def parse_tg_link(text):
    """
    Aceita formatos:
    - https://t.me/canal/123
    - https://t.me/c/1234567890/123
    - https://telegram.me/canal/123
    - t.me/canal/123
    """
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


# ── Handlers ──────────────────────────────────────────────────────────────────
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
        f"Baixe vídeos direto do Telegram!\n\n"
        f"{status}\n\n"
        f"*Como usar:*\n"
        f"1. Abra o vídeo no Telegram\n"
        f"2. Toque em *Compartilhar*\n"
        f"3. Copie o link\n"
        f"4. Cole aqui no bot\n\n"
        f"_Exemplo:_ `https://t.me/canal/123`"
    )

    kbd = []
    if not pro:
        kbd.append([InlineKeyboardButton("⭐ Ativar PRO (30 dias)", callback_data="ativar_pro")])
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
            f"📥 Restantes: {restantes}\n\n"
            f"Para downloads ilimitados, ative o PRO!\n"
            f"Use /ativar para inserir seu serial.",
            parse_mode="Markdown",
        )


async def cmd_ativar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "🔑 *Ativar PRO*\n\n"
        "Digite seu nome e serial no formato:\n\n"
        "`/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`\n\n"
        "_Exemplo:_\n"
        "`/serial João Silva | KWAI-A1B2-C3D4-1504`",
        parse_mode="Markdown",
    )


async def cmd_serial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    text = update.message.text.replace("/serial", "", 1).strip()

    if "|" not in text:
        await update.message.reply_text(
            "❌ Formato incorreto!\n\n"
            "Use: `/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`",
            parse_mode="Markdown",
        )
        return

    nome, serial = [p.strip() for p in text.split("|", 1)]
    ok, dias, msg = validar_serial(nome, serial)

    if ok:
        update_user(uid, serial=serial.upper(), nome_serial=nome.upper(), downloads=0)
        await update.message.reply_text(
            f"✅ *PRO ativado com sucesso!*\n\n"
            f"👤 Nome: {nome}\n"
            f"📅 Válido por {dias} dia(s)\n\n"
            f"Agora você tem downloads ilimitados! 🎉",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"❌ *Serial inválido*\n\n"
            f"Motivo: {msg}\n\n"
            f"Verifique o serial e tente novamente.",
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
        f"Para gerar serial:\n"
        f"`/gerar NOME DO CLIENTE`",
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
    inicio = datetime.now().strftime("%d/%m/%Y")

    username_bot = ctx.bot.username or "seu_bot"

    msg = (
        f"✅ *DoramaDown PRO* ativado!\n\n"
        f"🔑 Seu serial: `{serial}`\n"
        f"📅 Válido de {inicio} até {expira} (30 dias)\n\n"
        f"Para ativar:\n"
        f"1. Abra o bot @{username_bot}\n"
        f"2. Digite `/serial {nome} | {serial}`\n\n"
        f"Bom uso! 🎬"
    )

    await update.message.reply_text(
        f"✅ Serial gerado para *{nome}*:\n\n"
        f"`{serial}`\n\n"
        f"Expira: {expira}\n\n"
        f"📋 Mensagem pronta (copie e mande ao cliente):\n\n"
        f"{msg}",
        parse_mode="Markdown",
    )


async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    text = update.message.text.strip()

    if "t.me" not in text and "telegram.me" not in text:
        await update.message.reply_text(
            "📎 Mande um link de vídeo do Telegram!\n\n"
            "_Exemplo:_ `https://t.me/canal/123`\n\n"
            "Use /start para ver as instruções.",
            parse_mode="Markdown",
        )
        return

    pode, _ = pode_baixar(uid)
    if not pode:
        kbd = [[InlineKeyboardButton("⭐ Ativar PRO — Downloads Ilimitados", callback_data="ativar_pro")]]
        await update.message.reply_text(
            f"🚫 *Limite atingido!*\n\n"
            f"Você usou todos os {LIMITE_GRATIS} downloads grátis.\n\n"
            f"Ative o *PRO* para downloads ilimitados por 30 dias! ⭐",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kbd),
        )
        return

    chat_id, msg_id = parse_tg_link(text)
    if not chat_id or not msg_id:
        await update.message.reply_text(
            "❌ Link inválido!\n\n"
            "Certifique-se de copiar o link completo do vídeo.",
            parse_mode="Markdown",
        )
        return

    status_msg = await update.message.reply_text("⏳ Baixando vídeo...")

    try:
        client = await get_client()
        msg_tg = await client.get_messages(chat_id, ids=msg_id)

        if not msg_tg or not msg_tg.media:
            await status_msg.edit_text("❌ Vídeo não encontrado. Verifique o link.")
            return

        if not isinstance(msg_tg.media, MessageMediaDocument):
            await status_msg.edit_text("❌ Este link não contém um vídeo.")
            return

        doc = msg_tg.media.document
        if not getattr(doc, "mime_type", "").startswith("video"):
            await status_msg.edit_text("❌ O arquivo não é um vídeo.")
            return

        tamanho_mb = round(doc.size / 1024 / 1024, 1)

        if tamanho_mb > 2000:
            await status_msg.edit_text(
                f"❌ Vídeo muito grande ({tamanho_mb} MB).\n"
                f"Limite: 2 GB."
            )
            return

        await status_msg.edit_text(f"⏳ Baixando... ({tamanho_mb} MB)")

        nome_arq = f"video_{uid}_{msg_id}.mp4"
        for attr in doc.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                nome_arq = attr.file_name
                break

        caminho = Path("/tmp") / nome_arq
        await client.download_media(msg_tg, str(caminho))

        await status_msg.edit_text("📤 Enviando vídeo...")

        with open(caminho, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption=f"✅ *{nome_arq}*\n📦 {tamanho_mb} MB",
                parse_mode="Markdown",
            )

        caminho.unlink(missing_ok=True)
        await status_msg.delete()

        pro, _ = is_pro(uid)
        if not pro:
            u = get_user(uid)
            novos_dls = u.get("downloads", 0) + 1
            update_user(uid, downloads=novos_dls)
            restantes = max(0, LIMITE_GRATIS - novos_dls)

            if restantes > 0:
                await update.message.reply_text(
                    f"✅ Download concluído!\n"
                    f"🆓 Restantes: {restantes} download(s) grátis"
                )
            else:
                kbd = [[InlineKeyboardButton("⭐ Ativar PRO", callback_data="ativar_pro")]]
                await update.message.reply_text(
                    f"✅ Download concluído!\n\n"
                    f"⚠️ Você usou todos os downloads grátis!\n"
                    f"Ative o PRO para continuar. ⭐",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kbd),
                )

    except Exception as e:
        log.exception("Erro no download: %s", e)
        await status_msg.edit_text(
            "❌ Erro ao baixar o vídeo.\n\n"
            "Certifique-se que:\n"
            "• O link é válido\n"
            "• O vídeo ainda existe\n"
            "• O link está completo\n"
            "• O bot tem acesso ao conteúdo"
        )


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()

    if q.data == "ativar_pro":
        await q.message.reply_text(
            "⭐ *Ativar PRO — 30 dias*\n\n"
            "Para obter seu serial PRO:\n"
            "📱 Entre em contato pelo WhatsApp\n\n"
            "Após receber o serial, use:\n"
            "`/serial SEU NOME | KWAI-XXXX-XXXX-XXXX`",
            parse_mode="Markdown",
        )
    elif q.data == "ajuda":
        await q.message.reply_text(
            "❓ *Ajuda*\n\n"
            "1️⃣ Abra um vídeo no Telegram\n"
            "2️⃣ Toque em *⋮* → *Compartilhar* → *Copiar link*\n"
            "3️⃣ Cole o link aqui no bot\n"
            "4️⃣ O bot baixa e te envia o vídeo!\n\n"
            "*Comandos:*\n"
            "/start — Início\n"
            "/status — Ver seus downloads\n"
            "/ativar — Ativar serial PRO\n"
            "/serial NOME | KWAI-... — Inserir serial",
            parse_mode="Markdown",
        )


# ── Main ──────────────────────────────────────────────────────────────────────
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
