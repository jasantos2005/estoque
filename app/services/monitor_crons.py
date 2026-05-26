"""
monitor_crons.py — Monitor central de todos os crons do sistema
Verifica logs de execução e detecta falhas
"""
import os, re, sqlite3, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

import sys
sys.path.insert(0, str(BASE_DIR))
from app.services.notificador import enviar_telegram

TELEGRAM_AILTON = os.getenv("TELEGRAM_AILTON", "2135602169")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Mapa de crons monitorados
CRONS = [
    # HubTecnico
    {"nome": "Sync Estoque Técnicos",  "log": "/var/log/hubtecnico_sync_estoque.log",  "intervalo_max": 10},
    {"nome": "Auditoria Estoque",      "log": "/var/log/auditoria_estoque.log",         "intervalo_max": 70},
    {"nome": "Auditoria Técnicos",     "log": "/var/log/auditoria_tecnico.log",         "intervalo_max": 70},
    # HubEstoque
    {"nome": "Sync Saldos IXC",        "log": "/opt/automacoes/cliquedf/estoque/logs/sync_ixc.log", "intervalo_max": 35},
    {"nome": "Alerta Estoque",         "log": "/var/log/hubEstoque_alerta.log",          "intervalo_max": 1500},
    # Agentes
    {"nome": "Agente Leandro",         "log": "/var/log/agente_leandro.log",             "intervalo_max": 1500},
    {"nome": "Agente Alexandre",       "log": "/var/log/agente_alexandre.log",           "intervalo_max": 1500},
    {"nome": "Agente Auditor",         "log": "/var/log/agente_auditor.log",             "intervalo_max": 500},
    # HubComercial
    {"nome": "Auditoria Comercial",    "log": "/var/log/hubcomercial_cliquedf_auditoria.log", "intervalo_max": 5},
]

ERROS_KEYWORDS = ["error", "erro", "traceback", "exception", "failed", "falhou", "critical"]
OK_KEYWORDS    = ["ok", "success", "enviado", "atualizado", "sincronizado", "itens"]

def checar_log(cron):
    """Verifica último log de um cron."""
    path = Path(cron["log"])
    if not path.exists():
        return {"status": "sem_log", "ultima": None, "msg": "Arquivo de log não encontrado"}

    # Ler últimas 50 linhas
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            linhas = f.readlines()
        if not linhas:
            return {"status": "vazio", "ultima": None, "msg": "Log vazio"}

        ultimas = linhas[-50:]
        ultimo_texto = "".join(ultimas)

        # Detectar erros
        tem_erro = any(k in ultimo_texto.lower() for k in ERROS_KEYWORDS)
        tem_ok   = any(k in ultimo_texto.lower() for k in OK_KEYWORDS)

        # Tentar extrair timestamp da última linha com data
        ultima_data = None
        for linha in reversed(linhas):
            # Formato: 2026-05-26 18:00:01
            match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', linha)
            if match:
                try:
                    ultima_data = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    break
                except:
                    pass
            # Formato systemd: May 26 18:00:01
            match2 = re.search(r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})', linha)
            if match2:
                try:
                    ano = datetime.now().year
                    ultima_data = datetime.strptime(f"{ano} {match2.group(1)}", "%Y %b %d %H:%M:%S")
                    break
                except:
                    pass

        # Verificar se está atrasado
        agora = datetime.now() - timedelta(hours=3)
        atrasado = False
        minutos_atraso = 0
        if ultima_data:
            diff = (agora - ultima_data).total_seconds() / 60
            if diff > cron["intervalo_max"]:
                atrasado = True
                minutos_atraso = int(diff)

        if tem_erro:
            status = "erro"
        elif atrasado:
            status = "atrasado"
        elif tem_ok:
            status = "ok"
        else:
            status = "incerto"

        ultima_str = ultima_data.strftime("%d/%m %H:%M") if ultima_data else "?"
        return {
            "status": status,
            "ultima": ultima_str,
            "atrasado": atrasado,
            "minutos_atraso": minutos_atraso,
            "msg": ultimas[-1].strip() if ultimas else ""
        }
    except Exception as e:
        return {"status": "erro_leitura", "ultima": None, "msg": str(e)}

def gerar_relatorio():
    agora = (datetime.now() - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
    log.info(f"Monitor crons {agora}")

    resultados = []
    erros = []
    atrasados = []

    for cron in CRONS:
        r = checar_log(cron)
        r["nome"] = cron["nome"]
        resultados.append(r)
        if r["status"] == "erro":
            erros.append(r)
        elif r["status"] in ("atrasado", "sem_log"):
            atrasados.append(r)

    # Montar mensagem
    icones = {"ok":"✅", "erro":"❌", "atrasado":"⏰", "sem_log":"📂", "vazio":"📭", "incerto":"❓", "erro_leitura":"⚠️"}

    linhas = [f"🔍 <b>MONITOR DE CRONS — {agora}</b>\n"]

    if erros:
        linhas.append(f"❌ <b>ERROS DETECTADOS ({len(erros)}):</b>")
        for r in erros:
            linhas.append(f"  • {r['nome']}: {r['msg'][:60]}")
        linhas.append("")

    if atrasados:
        linhas.append(f"⏰ <b>ATRASADOS/SEM LOG ({len(atrasados)}):</b>")
        for r in atrasados:
            if r["status"] == "atrasado":
                linhas.append(f"  • {r['nome']}: último {r['ultima']} ({r['minutos_atraso']}min atrás)")
            else:
                linhas.append(f"  • {r['nome']}: {r['msg']}")
        linhas.append("")

    linhas.append(f"<b>STATUS GERAL:</b>")
    for r in resultados:
        ico = icones.get(r["status"], "❓")
        ult = f" · {r['ultima']}" if r["ultima"] else ""
        linhas.append(f"  {ico} {r['nome']}{ult}")

    if not erros and not atrasados:
        linhas.append(f"\n✅ Todos os crons rodando normalmente!")

    enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
    log.info(f"Relatório enviado — {len(erros)} erros, {len(atrasados)} atrasados")

if __name__ == "__main__":
    gerar_relatorio()
