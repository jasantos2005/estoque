import os, requests, logging
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(ENV_PATH, override=True)

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

def enviar_telegram(msg: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    load_dotenv(ENV_PATH, override=True)
    cid = chat_id or os.getenv("TELEGRAM_GRUPO")
    if not TELEGRAM_TOKEN or not cid:
        logger.warning(f"Telegram não configurado")
        return False
    payload = {"chat_id": cid, "text": msg, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload, timeout=10
        )
        logger.info(f"Telegram {cid}: {r.status_code} | {r.text[:100]}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram erro: {e}")
        return False

def enviar_todos(msg: str, parse_mode: str = "HTML"):
    load_dotenv(ENV_PATH, override=True)
    destinos = {"AILTON": os.getenv("TELEGRAM_AILTON")}
    for nome, cid in destinos.items():
        if cid:
            enviar_telegram(msg, chat_id=cid, parse_mode=parse_mode)
        else:
            logger.warning(f"{nome} sem chat_id")
