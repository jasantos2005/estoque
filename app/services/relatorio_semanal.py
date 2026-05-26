"""
relatorio_semanal.py — Relatório semanal de eficiência dos agentes
Todo domingo às 20h BRT
"""
import os, sys, sqlite3, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

sys.path.insert(0, "/opt/automacoes/cliquedf/tecnico")
from app.services.ixc_db import ixc_select
from app.services.notificador import enviar_telegram

DB_ESTOQUE      = str(BASE_DIR / "data" / "estoque.db")
TELEGRAM_AILTON = os.getenv("TELEGRAM_AILTON", "2135602169")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def _db():
    db = sqlite3.connect(DB_ESTOQUE, timeout=30)
    db.row_factory = sqlite3.Row
    return db

def _semana():
    agora = datetime.now() - timedelta(hours=3)
    fim   = agora.strftime("%Y-%m-%d")
    ini   = (agora - timedelta(days=7)).strftime("%Y-%m-%d")
    return ini, fim

def gerar_relatorio():
    ini, fim = _semana()
    log.info(f"Relatório semanal {ini} a {fim}")

    db = _db()

    # Requisições automáticas da semana
    reqs = db.execute("""
        SELECT tecnico_nome, status, COUNT(*) as total,
               data_referencia, itens_json
        FROM ht_requisicoes_auto
        WHERE DATE(criado_em) BETWEEN ? AND ?
        GROUP BY tecnico_nome, status
        ORDER BY tecnico_nome
    """, (ini, fim)).fetchall()

    # Agrupar por técnico
    por_tecnico = {}
    for r in reqs:
        nome = r["tecnico_nome"]
        if nome not in por_tecnico:
            por_tecnico[nome] = {"pendente": 0, "aprovada": 0, "cancelada": 0}
        por_tecnico[nome][r["status"]] = r["total"]

    # Instalações realizadas por técnico
    ID_ASSUNTO = 227
    try:
        inst = ixc_select("""
            SELECT f.nome as tecnico, COUNT(*) as total
            FROM ixcprovedor.su_oss_chamado s
            JOIN ixcprovedor.funcionario f ON f.id = s.id_tecnico
            WHERE s.id_assunto = %s
            AND s.status = 'F'
            AND DATE(s.data_fechamento) BETWEEN %s AND %s
            GROUP BY s.id_tecnico
            ORDER BY total DESC
        """, (ID_ASSUNTO, ini, fim))
        inst_map = {r["tecnico"]: r["total"] for r in inst}
    except:
        inst_map = {}

    # Montar relatório
    total_reqs = sum(v.get("aprovada",0) + v.get("pendente",0) + v.get("cancelada",0) for v in por_tecnico.values())
    total_aprovadas = sum(v.get("aprovada",0) for v in por_tecnico.values())
    total_inst = sum(inst_map.values())
    acuracia = round(total_aprovadas / total_reqs * 100) if total_reqs > 0 else 0

    linhas = [
        f"📊 <b>RELATÓRIO SEMANAL — {ini} a {fim}</b>",
        f"",
        f"🔢 <b>Resumo geral:</b>",
        f"  Instalações realizadas: {total_inst}",
        f"  Requisições geradas: {total_reqs}",
        f"  Requisições aprovadas: {total_aprovadas}",
        f"  Acurácia do sistema: {acuracia}%",
        f"",
        f"👤 <b>Por técnico:</b>",
    ]

    for nome, dados in sorted(por_tecnico.items()):
        inst_tec = inst_map.get(nome, 0)
        apr = dados.get("aprovada", 0)
        can = dados.get("cancelada", 0)
        linhas.append(f"  <b>{nome}</b>")
        linhas.append(f"    Instalações: {inst_tec} | Req aprovadas: {apr} | Canceladas: {can}")

    if not por_tecnico:
        linhas.append("  Nenhuma requisição automática esta semana")

    linhas.append("")
    linhas.append(f"🤖 Sistema de agentes rodando normalmente")

    db.close()
    enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
    log.info("Relatório semanal enviado")

if __name__ == "__main__":
    gerar_relatorio()
