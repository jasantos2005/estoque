"""
agente_auditor.py — Auditor do Estoque Principal
08h: Requisições do dia anterior — prazo vencido, canceladas sem justificativa
12h: Acompanhamento requisições da manhã
18h: Fechamento — saldo final vs IXC, movimentos não justificados
18h30: Relatório completo Telegram Ailton
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
PRAZO_HORAS     = 8  # requisições devem ser aprovadas em até 8h

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def _db():
    db = sqlite3.connect(DB_ESTOQUE, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def _agora():
    return datetime.now() - timedelta(hours=3)

def _hoje():
    return (_agora()).strftime("%Y-%m-%d")

def _ontem():
    return (_agora() - timedelta(days=1)).strftime("%Y-%m-%d")

# ── CICLO 08H ─────────────────────────────────────────────────────────────────

def auditar_requisicoes_manha():
    """Verifica requisições do dia anterior — prazo vencido e canceladas sem justificativa."""
    log.info("Auditoria 08h — requisições")
    alertas = []

    try:
        db = _db()
        # Requisições automáticas pendentes de ontem (prazo vencido)
        reqs_vencidas = db.execute("""
            SELECT id, tecnico_nome, ixc_requisicao_id, criado_em, data_referencia
            FROM ht_requisicoes_auto
            WHERE status = 'pendente'
            AND DATE(criado_em) <= ?
        """, (_ontem(),)).fetchall()

        if reqs_vencidas:
            alertas.append("⏰ <b>REQUISIÇÕES PENDENTES — PRAZO VENCIDO</b>")
            alertas.append(f"Criadas até ontem ({_ontem()}) sem aprovação:\n")
            for r in reqs_vencidas:
                alertas.append(f"  📋 Req IXC #{r['ixc_requisicao_id']} — {r['tecnico_nome']} ({r['criado_em'][:10]})")
            alertas.append("")

        # Requisições canceladas sem justificativa
        reqs_canceladas = db.execute("""
            SELECT id, tecnico_nome, ixc_requisicao_id, criado_em
            FROM ht_requisicoes_auto
            WHERE status = 'cancelada'
            AND DATE(criado_em) >= ?
        """, (_ontem(),)).fetchall()

        if reqs_canceladas:
            alertas.append("❌ <b>CANCELADAS ({len(reqs_canceladas)})</b>\n")
            for r in reqs_canceladas:
                alertas.append(f"  Req #{r['ixc_requisicao_id']} — {r['tecnico_nome']}")
            alertas.append("")
        db.close()

    except Exception as e:
        log.warning(f"Erro auditoria requisições: {e}")
        alertas.append(f"⚠️ Erro ao verificar requisições IXC: {e}")

    if alertas:
        msg = f"🔍 <b>AUDITORIA 08H — {_hoje()}</b>\n\n" + "\n".join(alertas)
        enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
        log.info(f"Alerta 08h enviado — {len(alertas)} linhas")
    else:
        log.info("08h — sem alertas")

# ── CICLO 12H ─────────────────────────────────────────────────────────────────

def auditar_requisicoes_tarde():
    """Acompanhamento das requisições criadas hoje de manhã."""
    log.info("Auditoria 12h — acompanhamento")

    try:
        reqs_hoje = ixc_select("""
            SELECT r.id, r.status, r.id_funcionario, r.data,
                   f.nome as tecnico
            FROM ixcprovedor.requisicao r
            LEFT JOIN ixcprovedor.funcionario f ON f.id = r.id_funcionario
            WHERE DATE(r.data) = %s
            ORDER BY r.id DESC
        """, (_hoje(),))

        if not reqs_hoje:
            log.info("12h — sem requisições hoje")
            return

        pendentes  = [r for r in reqs_hoje if r['status'] in ('A','P')]
        aprovadas  = [r for r in reqs_hoje if r['status'] == 'F']
        canceladas = [r for r in reqs_hoje if r['status'] == 'C']

        if pendentes:
            linhas = [f"⏳ <b>REQUISIÇÕES PENDENTES — 12H</b>",
                      f"Data: {_hoje()}\n"]
            for r in pendentes:
                linhas.append(f"  📋 Req #{r['id']} — {r['tecnico'] or 'Técnico'}")
            linhas.append(f"\n✅ Aprovadas: {len(aprovadas)} | ❌ Canceladas: {len(canceladas)}")
            enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
            log.info(f"12h — {len(pendentes)} pendentes alertadas")

    except Exception as e:
        log.warning(f"Erro auditoria 12h: {e}")

# ── CICLO 18H ─────────────────────────────────────────────────────────────────

def auditar_fechamento():
    """Fechamento do dia — saldo Hub vs IXC, movimentos não justificados."""
    log.info("Auditoria 18h — fechamento")

    alertas = []

    # 1. Divergências de saldo Hub vs IXC
    try:
        ixc_rows = ixc_select("""
            SELECT id, SUM(saldo) as saldo
            FROM ixcprovedor.view_estoque_produtos_almox_filial
            WHERE almox_id = 1
            GROUP BY id
            HAVING saldo > 0
        """)
        ixc_map = {str(r['id']): float(r['saldo']) for r in ixc_rows}

        db = _db()
        hub_rows = db.execute("""
            SELECT p.id_produto, p.descricao, s.saldo
            FROM saldos s JOIN produtos p ON p.id_produto = s.id_produto
            WHERE s.saldo > 0
        """).fetchall()
        db.close()

        divs = []
        for r in hub_rows:
            hub_s = float(r['saldo'])
            ixc_s = ixc_map.get(r['id_produto'], 0)
            diff  = abs(hub_s - ixc_s)
            tol   = max(5, hub_s * 0.15)
            if diff > tol:
                divs.append((r['id_produto'], r['descricao'], hub_s, ixc_s, diff))

        if divs:
            alertas.append(f"📦 <b>DIVERGÊNCIAS DE SALDO ({len(divs)} produtos)</b>")
            for d in sorted(divs, key=lambda x: x[4], reverse=True)[:8]:
                alertas.append(f"  ID:{d[0]} Hub:{d[2]:.0f} | IXC:{d[3]:.0f} | {d[1][:30]}")
            alertas.append("")

    except Exception as e:
        log.warning(f"Erro divergências saldo: {e}")

    # 2. Movimentos sem OS ou requisição hoje
    try:
        movs_sem_os = ixc_select("""
            SELECT mp.id, mp.id_produto, mp.quantidade, mp.tipo,
                   p.descricao
            FROM ixcprovedor.movimento_produtos mp
            JOIN ixcprovedor.produtos p ON p.id = mp.id_produto
            WHERE mp.id_almox = 1
            AND DATE(mp.data) = %s
            AND (mp.id_oss_chamado IS NULL OR mp.id_oss_chamado = 0)
            AND mp.tipo = 'S'
            ORDER BY mp.id DESC
            LIMIT 20
        """, (_hoje(),))

        if movs_sem_os:
            alertas.append(f"🔴 <b>SAÍDAS SEM OS HOJE ({len(movs_sem_os)})</b>")
            for m in movs_sem_os[:5]:
                alertas.append(f"  ID:{m['id_produto']} {m['descricao'][:30]} — {float(m['quantidade']):.0f}")
            alertas.append("")

    except Exception as e:
        log.warning(f"Erro movimentos sem OS: {e}")

    # 3. Status das requisições do dia
    try:
        db2 = _db()
        reqs_hub = db2.execute("""
            SELECT status, COUNT(*) as total
            FROM ht_requisicoes_auto
            WHERE DATE(criado_em) >= ?
            GROUP BY status
        """, (_ontem(),)).fetchall()
        db2.close()
        if reqs_hub:
            linhas_req = [f"{r['status']}: {r['total']}" for r in reqs_hub]
            alertas.append(f"📋 <b>REQUISIÇÕES AUTO</b>: {' | '.join(linhas_req)}\n")

    except Exception as e:
        log.warning(f"Erro status requisições: {e}")

    if alertas:
        msg = f"📊 <b>FECHAMENTO DO DIA — {_hoje()}</b>\n\n" + "\n".join(alertas)
        enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
        log.info(f"Fechamento 18h enviado")
    else:
        log.info("18h — estoque OK, sem divergências")
        enviar_telegram(f"✅ <b>FECHAMENTO {_hoje()}</b>\nEstoque sem divergências. Saldos conferidos.", chat_id=TELEGRAM_AILTON)

# ── CICLO 18H30 — RELATÓRIO COMPLETO ─────────────────────────────────────────

def relatorio_completo():
    """Relatório consolidado do dia para Ailton."""
    log.info("Relatório 18h30")

    try:
        # Requisições automáticas do dia
        db = _db()
        reqs_auto = db.execute("""
            SELECT tecnico_nome, status, data_referencia, itens_json
            FROM ht_requisicoes_auto
            WHERE DATE(criado_em) >= ?
            ORDER BY criado_em DESC
        """, (_ontem(),)).fetchall()
        db.close()

        # Requisições no IXC hoje
        try:
            reqs_ixc = ixc_select("""
                SELECT r.status, COUNT(*) as total
                FROM ixcprovedor.requisicao r
                WHERE DATE(r.data) = %s
                GROUP BY r.status
            """, (_hoje(),))
            status_map = {'A':'Pendente','F':'Aprovada','C':'Cancelada','P':'Em processo'}
            reqs_resumo = " | ".join([f"{status_map.get(r['status'],r['status'])}: {r['total']}" for r in reqs_ixc])
        except:
            reqs_resumo = "N/D"

        linhas = [
            f"📊 <b>RELATÓRIO DIÁRIO — {_hoje()}</b>",
            f"",
            f"📋 <b>Requisições IXC:</b> {reqs_resumo}",
            f"",
        ]

        if reqs_auto:
            linhas.append(f"🤖 <b>Requisições automáticas ({len(reqs_auto)}):</b>")
            for r in reqs_auto:
                itens = json.loads(r['itens_json'] or '[]')
                linhas.append(f"  • {r['tecnico_nome']} — {len(itens)} itens — {r['status']}")
        else:
            linhas.append("🤖 Nenhuma requisição automática gerada")

        enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
        log.info("Relatório 18h30 enviado")

    except Exception as e:
        log.error(f"Erro relatório: {e}")

def monitorar_aprovacoes():
    """Verifica requisições pendentes no IXC e notifica quando aprovadas."""
    import base64, requests as req
    log.info("Monitor aprovacoes")
    load_dotenv(BASE_DIR / ".env")
    ixc_url   = os.getenv("IXC_API_URL","https://sistema.cliquedf.com.br")
    ixc_user  = os.getenv("IXC_API_USER","64")
    ixc_token = os.getenv("IXC_API_TOKEN","")
    auth = base64.b64encode(f"{ixc_user}:{ixc_token}".encode()).decode()
    TELEGRAM_GRUPO = os.getenv("TELEGRAM_GRUPO","")

    db = _db()
    pendentes = db.execute("""
        SELECT id, ixc_requisicao_id, tecnico_nome, itens_json, data_referencia
        FROM ht_requisicoes_auto
        WHERE status = 'pendente'
        AND ixc_requisicao_id IS NOT NULL
    """).fetchall()

    if not pendentes:
        log.info("Sem requisicoes pendentes")
        db.close()
        return

    for r in pendentes:
        try:
            resp = req.get(
                f"{ixc_url}/webservice/v1/requisicao_material?qtype=id&query={r['ixc_requisicao_id']}&oper=%3D&page=1&rp=1",
                headers={"Authorization": f"Basic {auth}"},
                timeout=15
            )
            if not resp.ok:
                continue
            data = resp.json()
            registros = data.get("registros", []) or []
            if not registros:
                continue
            status_ixc = registros[0].get("status","")

            if status_ixc == "F":
                db.execute("UPDATE ht_requisicoes_auto SET status='aprovada', atualizado_em=? WHERE id=?",
                           (_agora().strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
                itens = json.loads(r["itens_json"] or "[]")
                linhas = [
                    f"📦 <b>REQUISIÇÃO APROVADA</b>",
                    f"",
                    f"👤 Técnico: <b>{r['tecnico_nome']}</b>",
                    f"📋 Req #{r['ixc_requisicao_id']} — {r['data_referencia']}",
                    f"",
                ]
                if itens:
                    linhas.append("Itens liberados:")
                    for item in itens[:8]:
                        linhas.append(f"  • {item.get('nome','')[:35]} — {item.get('qtd_falta',0):.0f}")
                linhas.append(f"")
                linhas.append(f"⚠️ <b>{r['tecnico_nome']}</b>, dirija-se ao estoque para retirar!")
                msg = "\n".join(linhas)
                enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
                if TELEGRAM_GRUPO:
                    enviar_telegram(msg, chat_id=TELEGRAM_GRUPO)
                log.info(f"Req #{r['ixc_requisicao_id']} aprovada — notificado")

            elif status_ixc == "C":
                db.execute("UPDATE ht_requisicoes_auto SET status='cancelada', atualizado_em=? WHERE id=?",
                           (_agora().strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
                enviar_telegram(
                    f"❌ <b>REQUISIÇÃO CANCELADA</b>\nReq #{r['ixc_requisicao_id']} — {r['tecnico_nome']}",
                    chat_id=TELEGRAM_AILTON)
                log.info(f"Req #{r['ixc_requisicao_id']} cancelada")

        except Exception as e:
            log.warning(f"Erro verificar req #{r['ixc_requisicao_id']}: {e}")

    db.commit()
    db.close()
def monitorar_aprovacoes():
    """Verifica requisições pendentes no IXC e notifica quando aprovadas."""
    import base64, requests as req
    log.info("Monitor aprovacoes")
    load_dotenv(BASE_DIR / ".env")
    ixc_url   = os.getenv("IXC_API_URL","https://sistema.cliquedf.com.br")
    ixc_user  = os.getenv("IXC_API_USER","64")
    ixc_token = os.getenv("IXC_API_TOKEN","")
    auth = base64.b64encode(f"{ixc_user}:{ixc_token}".encode()).decode()
    TELEGRAM_GRUPO = os.getenv("TELEGRAM_GRUPO","")


def verificar_estoque_critico():
    """Verifica se almox principal tem estoque para todas as instalações de amanhã."""
    from datetime import datetime, timedelta
    amanha = (datetime.now() - timedelta(hours=3) + timedelta(days=1)).strftime("%Y-%m-%d")
    log.info(f"Verificação estoque crítico para {amanha}")

    sys.path.insert(0, "/opt/automacoes/cliquedf/tecnico")
    from app.services.ixc_db import ixc_select
    ID_ASSUNTO = 227

    # Buscar todos os técnicos ativos
    tecnicos = [
        {"id": 47, "nome": "LEANDRO",    "almox": 54},
        {"id": 13, "nome": "ALEXANDRE",  "almox": 56},
        {"id": 17, "nome": "DENISON",    "almox": 14},
        {"id": 50, "nome": "RICARDO",    "almox": 46},
        {"id": 32, "nome": "RODRIGO",    "almox": 33},
        {"id": 35, "nome": "RODRIGO 2",  "almox": 33},
        {"id": 56, "nome": "ROGERIO",    "almox": 51},
        {"id": 55, "nome": "VICTOR",     "almox": 48},
        {"id": 60, "nome": "WELINTON",   "almox": 51},
        {"id": 46, "nome": "WELLINGTON", "almox": 43},
    ]

    # Saldo almox principal
    saldo_principal = ixc_select("""
        SELECT id, SUM(saldo) as saldo
        FROM ixcprovedor.view_estoque_produtos_almox_filial
        WHERE almox_id = 1
        GROUP BY id HAVING saldo > 0
    """)
    saldo_map = {str(r["id"]): float(r["saldo"]) for r in saldo_principal}

    db = _db()
    necessidade_total = {}  # id_produto -> {nome, qtd_necessaria, tecnicos}

    for tec in tecnicos:
        # OS de instalação amanhã
        os_amanha = ixc_select("""
            SELECT s.id, s.id_contrato_kit
            FROM ixcprovedor.su_oss_chamado s
            WHERE s.id_tecnico = %s AND s.id_assunto = %s
            AND s.status IN ('A','AG') AND DATE(s.data_reservada) = %s
        """, (tec["id"], ID_ASSUNTO, amanha))

        if not os_amanha:
            continue

        for os_row in os_amanha:
            contrato = os_row.get("id_contrato_kit")
            if not contrato:
                continue
            cc = ixc_select("SELECT id_vd_contrato FROM ixcprovedor.cliente_contrato WHERE id=%s", (contrato,))
            if not cc:
                continue
            id_plano_ixc = cc[0]["id_vd_contrato"]
            plano = db.execute("SELECT id FROM ht_plano_config WHERE id_plano_ixc=? AND ativo=1", (id_plano_ixc,)).fetchone()
            if not plano:
                continue

            grupos = db.execute("""
                SELECT g.nome_grupo, g.quantidade, gp.id_produto
                FROM ht_plano_grupo g
                JOIN ht_plano_grupo_produto gp ON gp.id_grupo = g.id
                WHERE g.id_plano_config = ?
                ORDER BY g.nome_grupo, gp.prioridade
            """, (plano["id"],)).fetchall()

            vistos = set()
            for g in grupos:
                nome_grupo = g["nome_grupo"]
                if nome_grupo in vistos:
                    continue
                vistos.add(nome_grupo)
                pid = str(g["id_produto"])
                qtd = float(g["quantidade"])
                if pid not in necessidade_total:
                    necessidade_total[pid] = {"qtd": 0, "tecnicos": set(), "grupo": nome_grupo}
                necessidade_total[pid]["qtd"] += qtd
                necessidade_total[pid]["tecnicos"].add(tec["nome"])

    db.close()

    # Comparar necessidade vs saldo
    criticos = []
    for pid, info in necessidade_total.items():
        saldo = saldo_map.get(pid, 0)
        falta = info["qtd"] - saldo
        if falta > 0:
            criticos.append({
                "id_produto": pid,
                "grupo": info["grupo"],
                "necessario": info["qtd"],
                "saldo": saldo,
                "falta": falta,
                "tecnicos": list(info["tecnicos"])
            })

    if not criticos:
        log.info(f"Estoque crítico OK — almox principal suficiente para {amanha}")
        return

    linhas = [f"🚨 <b>ALERTA ESTOQUE CRÍTICO — {amanha}</b>",
              f"Almox principal insuficiente para as instalações de amanhã!\n"]
    for c in sorted(criticos, key=lambda x: x["falta"], reverse=True)[:10]:
        linhas.append(f"⚠️ Grupo: <b>{c['grupo']}</b> (ID:{c['id_produto']})")
        linhas.append(f"   Necessário: {c['necessario']:.0f} | Saldo: {c['saldo']:.0f} | Falta: {c['falta']:.0f}")
        linhas.append(f"   Técnicos: {', '.join(c['tecnicos'])}")
        linhas.append("")

    linhas.append("🔔 Providencie reposição antes das 18h!")
    enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
    log.info(f"Alerta crítico enviado — {len(criticos)} produtos insuficientes")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ciclo", choices=["manha","tarde","fechamento","relatorio","monitor","critico"], required=True)
    args = parser.parse_args()

    ciclos = {
        "manha":     auditar_requisicoes_manha,
        "tarde":     auditar_requisicoes_tarde,
        "fechamento": auditar_fechamento,
        "relatorio": relatorio_completo,
        "monitor":   monitorar_aprovacoes,
        "critico":   verificar_estoque_critico,
    }
    ciclos[args.ciclo]()


