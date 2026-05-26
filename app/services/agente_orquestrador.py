"""
agente_orquestrador.py — Agente individual por técnico
Ciclo 17h: Audita consumo do dia vs predeterminado
Ciclo 18h: Calcula necessidade do dia seguinte e cria requisição no IXC
"""
import os, sys, sqlite3, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

sys.path.insert(0, "/opt/automacoes/cliquedf/tecnico")
from app.services.ixc_db import ixc_select
try:
    from app.services.ixc_db import ixc_insert
except ImportError:
    pass

from app.services.notificador import enviar_telegram

DB_ESTOQUE = str(BASE_DIR / "data" / "estoque.db")
DB_TECNICO = "/opt/automacoes/cliquedf/tecnico/hub_tecnico.db"
TELEGRAM_AILTON = os.getenv("TELEGRAM_AILTON", "2135602169")
TELEGRAM_GRUPO  = os.getenv("TELEGRAM_GRUPO", "")
ID_ASSUNTO_INSTALACAO = 227
MAX_ITENS_REQUISICAO  = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def _db_estoque():
    db = sqlite3.connect(DB_ESTOQUE, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def _db_tecnico():
    db = sqlite3.connect(DB_TECNICO, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def _hoje():
    return (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d")

def _amanha():
    return (datetime.now() - timedelta(hours=3) + timedelta(days=1)).strftime("%Y-%m-%d")

# ── CICLO 17H — AUDITORIA DE CONSUMO ─────────────────────────────────────────

def auditar_consumo(ixc_tecnico_id: int, tecnico_nome: str, ixc_almox_id: int):
    """Compara o que foi consumido hoje vs o predeterminado pelos planos."""
    hoje = _hoje()
    log.info(f"[{tecnico_nome}] Auditoria consumo {hoje}")

    db_tec = _db_tecnico()

    # OS de instalação finalizadas hoje
    os_hoje = db_tec.execute("""
        SELECT o.ixc_os_id, o.cliente_nome, o.id_contrato_kit
        FROM ht_os o
        JOIN ht_os_execucao e ON e.ixc_os_id = o.ixc_os_id
        WHERE o.id_tecnico = (SELECT id FROM ht_usuarios WHERE ixc_funcionario_id=?)
        AND o.id_assunto = ?
        AND o.status_hub = 'finalizada'
        AND DATE(e.finalizada_em, '-3 hours') = ?
    """, (ixc_tecnico_id, ID_ASSUNTO_INSTALACAO, hoje)).fetchall()

    if not os_hoje:
        db_tec.close()
        log.info(f"[{tecnico_nome}] Sem instalações hoje")
        return []

    db_est = _db_estoque()
    desvios = []

    for os_row in os_hoje:
        os_id = os_row["ixc_os_id"]
        contrato = os_row["id_contrato_kit"]

        # Plano do contrato
        plano = _buscar_plano_contrato(contrato)
        if not plano:
            continue

        # Produtos predeterminados pelo plano
        esperado = _calcular_esperado_plano(plano["id"], plano["id_plano_ixc"], db_est, ixc_almox_id)

        # Produtos realmente usados na OS
        usados = db_tec.execute("""
            SELECT p.ixc_produto_id, p.nome, m.quantidade
            FROM ht_os_materiais m
            JOIN ht_produtos p ON p.id = m.id_produto
            WHERE m.ixc_os_id = ?
            AND m.id_tecnico = (SELECT id FROM ht_usuarios WHERE ixc_funcionario_id=?)
        """, (os_id, ixc_tecnico_id)).fetchall()
        usados_map = {str(r["ixc_produto_id"]): float(r["quantidade"]) for r in usados}

        for grupo, info in esperado.items():
            prod_id = info.get("id_produto")
            qtd_esp = info.get("quantidade", 0)
            qtd_real = usados_map.get(str(prod_id), 0) if prod_id else 0

            if not prod_id:
                continue

            diff = qtd_real - qtd_esp
            if abs(diff) > max(1, qtd_esp * 0.1):
                desvios.append({
                    "os": os_id,
                    "cliente": os_row["cliente_nome"],
                    "grupo": grupo,
                    "esperado": qtd_esp,
                    "real": qtd_real,
                    "diff": diff
                })

    db_tec.close()
    db_est.close()

    if desvios:
        _notificar_desvios(tecnico_nome, desvios)

    return desvios

# ── CICLO 18H — PREPARAÇÃO DO DIA SEGUINTE ────────────────────────────────────

def preparar_dia_seguinte(ixc_tecnico_id: int, tecnico_nome: str, ixc_almox_id: int):
    """Calcula necessidade do dia seguinte e cria requisição no IXC."""
    amanha = _amanha()
    log.info(f"[{tecnico_nome}] Preparando dia {amanha}")

    # Buscar OS de instalação agendadas para amanhã no IXC
    os_amanha = ixc_select("""
        SELECT s.id, s.id_contrato_kit, c.razao as cliente
        FROM ixcprovedor.su_oss_chamado s
        LEFT JOIN ixcprovedor.cliente c ON c.id = s.id_cliente
        WHERE s.id_tecnico = %s
        AND s.id_assunto = %s
        AND s.status IN ('A','AG')
        AND DATE(s.data_reservada) = %s
    """, (ixc_tecnico_id, ID_ASSUNTO_INSTALACAO, amanha))

    if not os_amanha:
        log.info(f"[{tecnico_nome}] Sem instalações amanhã")
        return

    log.info(f"[{tecnico_nome}] {len(os_amanha)} instalações amanhã")

    db_est = _db_estoque()

    # Calcular necessidade total
    necessidade = {}  # id_produto -> {nome, qtd_necessaria, grupo}

    for os_row in os_amanha:
        contrato = os_row.get("id_contrato_kit")
        plano = _buscar_plano_contrato(contrato)
        if not plano:
            log.warning(f"  OS #{os_row['id']}: plano não encontrado (contrato={contrato})")
            continue

        esperado = _calcular_esperado_plano(plano["id"], plano["id_plano_ixc"], db_est, ixc_almox_id)

        # Alertar grupos sem estoque
        alertas_grupo = esperado.pop("_alertas_sem_estoque", [])
        if alertas_grupo:
            msg_alerta = (f"⚠️ <b>SEM SUBSTITUTO — {tecnico_nome}</b>\n"
                         f"OS #{os_row['id']} — {os_row.get('cliente','')}\n"
                         f"Grupos sem nenhum produto em estoque:\n" +
                         "\n".join([f"  • {g}" for g in alertas_grupo]))
            enviar_telegram(msg_alerta, chat_id=TELEGRAM_AILTON)

        for grupo, info in esperado.items():
            pid = info.get("id_produto")
            if not pid:
                continue
            pid_str = str(pid)
            qtd = info.get("quantidade", 1)
            if pid_str not in necessidade:
                necessidade[pid_str] = {"nome": info.get("nome",""), "qtd": 0, "grupo": grupo}
            necessidade[pid_str]["qtd"] += qtd

    if not necessidade:
        log.info(f"[{tecnico_nome}] Nenhum produto necessário calculado")
        db_est.close()
        return

    # Verificar estoque atual do técnico no IXC
    saldo_tec = ixc_select("""
        SELECT id_produto,
               COALESCE(SUM(CASE WHEN tipo='E' THEN quantidade ELSE -quantidade END),0) as saldo
        FROM ixcprovedor.movimento_produtos
        WHERE id_almox = %s
        GROUP BY id_produto
        HAVING saldo > 0
    """, (ixc_almox_id,))
    saldo_map = {str(r["id_produto"]): float(r["saldo"]) for r in saldo_tec}

    # Verificar requisições automáticas pendentes no Hub
    db_req = _db_estoque()
    reqs_hub = db_req.execute("""
        SELECT itens_json FROM ht_requisicoes_auto
        WHERE ixc_tecnico_id=? AND status='pendente'
        AND data_referencia=?
    """, (ixc_tecnico_id, amanha)).fetchall()
    db_req.close()
    req_map = {}
    for _r in reqs_hub:
        for _it in json.loads(_r["itens_json"] or "[]"):
            _pid = str(_it.get("id_produto",""))
            req_map[_pid] = req_map.get(_pid,0) + float(_it.get("qtd_falta",0))

    # Calcular o que realmente falta
    itens_requisicao = []
    for pid_str, info in necessidade.items():
        qtd_necessaria = info["qtd"]
        qtd_estoque    = saldo_map.get(pid_str, 0)
        qtd_pendente   = req_map.get(pid_str, 0)
        qtd_disponivel = qtd_estoque + qtd_pendente
        qtd_falta      = max(0, qtd_necessaria - qtd_disponivel)

        if qtd_falta > 0:
            itens_requisicao.append({
                "id_produto": int(pid_str),
                "nome": info["nome"],
                "grupo": info["grupo"],
                "qtd_necessaria": qtd_necessaria,
                "qtd_estoque": qtd_estoque,
                "qtd_falta": qtd_falta
            })

    db_est.close()

    if not itens_requisicao:
        msg = f"✅ <b>{tecnico_nome}</b> — {amanha}\nEstoque suficiente para {len(os_amanha)} instalação(ões). Nenhuma requisição necessária."
        enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
        log.info(f"[{tecnico_nome}] Estoque suficiente, sem requisição")
        return

    # Verificar limite de segurança
    if len(itens_requisicao) > MAX_ITENS_REQUISICAO:
        msg = f"⚠️ <b>{tecnico_nome}</b>\nRequisição automática cancelada — {len(itens_requisicao)} itens excedem limite de segurança ({MAX_ITENS_REQUISICAO}). Verificar manualmente."
        enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
        log.warning(f"[{tecnico_nome}] Limite de segurança atingido: {len(itens_requisicao)} itens")
        return

    # Criar requisição no IXC
    _criar_requisicao_ixc(ixc_tecnico_id, tecnico_nome, itens_requisicao, os_amanha, amanha, ixc_almox_id)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _buscar_plano_contrato(id_contrato_kit):
    """Busca o plano de venda do contrato no IXC e no Hub."""
    if not id_contrato_kit:
        return None
    try:
        contrato = ixc_select("""
            SELECT id_vd_contrato, contrato as nome_plano
            FROM ixcprovedor.cliente_contrato
            WHERE id = %s
        """, (id_contrato_kit,))
        if not contrato:
            return None
        id_plano_ixc = contrato[0]["id_vd_contrato"]
        nome = contrato[0]["nome_plano"]

        db_est = _db_estoque()
        plano = db_est.execute(
            "SELECT id, id_plano_ixc, nome_plano FROM ht_plano_config WHERE id_plano_ixc=? AND ativo=1",
            (id_plano_ixc,)
        ).fetchone()
        db_est.close()

        if not plano:
            return None
        return dict(plano)
    except Exception as e:
        log.warning(f"Erro ao buscar plano contrato {id_contrato_kit}: {e}")
        return None

def _calcular_esperado_plano(id_plano, id_plano_ixc, db_est, ixc_almox_id):
    """Retorna dict grupo -> {id_produto, nome, quantidade} baseado no plano."""
    grupos = db_est.execute("""
        SELECT g.id, g.nome_grupo, g.quantidade, g.unidade
        FROM ht_plano_grupo g
        WHERE g.id_plano_config = ?
    """, (id_plano,)).fetchall()

    resultado = {}
    for g in grupos:
        # Buscar produtos do grupo ordenados por prioridade
        prods = db_est.execute("""
            SELECT gp.id_produto, gp.prioridade
            FROM ht_plano_grupo_produto gp
            WHERE gp.id_grupo = ?
            ORDER BY gp.prioridade
        """, (g["id"],)).fetchall()

        if not prods:
            continue

        # Lógica especial para ONT vs ONU
        nome_grupo = g["nome_grupo"]

        if nome_grupo == "ONT":
            # Verificar saldo de cada ONT no almox principal
            prod_escolhido = _escolher_produto_maior_saldo(prods, ixc_almox_id=1)
            if prod_escolhido:
                resultado["ONT"] = {"id_produto": prod_escolhido["id"], "nome": prod_escolhido["nome"], "quantidade": float(g["quantidade"])}
                # Se tem ONT, não adicionar ONU nem Roteador
                resultado["ONU"] = {"id_produto": None, "nome": "", "quantidade": 0}
                resultado["Roteador"] = {"id_produto": None, "nome": "", "quantidade": 0}

        elif nome_grupo == "ONU":
            if resultado.get("ONT", {}).get("id_produto"):
                continue  # ONT já escolhida, pular ONU
            prod_escolhido = _escolher_produto_maior_saldo(prods, ixc_almox_id=1)
            if prod_escolhido:
                resultado["ONU"] = {"id_produto": prod_escolhido["id"], "nome": prod_escolhido["nome"], "quantidade": float(g["quantidade"])}

        elif nome_grupo == "Roteador":
            if resultado.get("ONT", {}).get("id_produto"):
                continue  # ONT já escolhida, não precisa roteador
            prod_escolhido = _escolher_produto_maior_saldo(prods, ixc_almox_id=1)
            if prod_escolhido:
                resultado["Roteador"] = {"id_produto": prod_escolhido["id"], "nome": prod_escolhido["nome"], "quantidade": float(g["quantidade"])}

        else:
            # Insumos fixos — maior saldo
            prod_escolhido = _escolher_produto_maior_saldo(prods, ixc_almox_id=1)
            if prod_escolhido:
                resultado[nome_grupo] = {"id_produto": prod_escolhido["id"], "nome": prod_escolhido["nome"], "quantidade": float(g["quantidade"])}
            else:
                resultado[nome_grupo] = {"id_produto": None, "nome": "", "quantidade": 0,
                                         "sem_estoque": True, "grupo": nome_grupo}

    # Alertar grupos sem nenhum produto disponível
    sem_estoque = [info["grupo"] for grupo, info in resultado.items()
                   if info.get("sem_estoque") and grupo not in ("ONU","Roteador")]
    if sem_estoque:
        resultado["_alertas_sem_estoque"] = sem_estoque

    return resultado

def _escolher_produto_maior_saldo(prods, ixc_almox_id=1):
    """Escolhe o produto com maior saldo no almox principal."""
    if not prods:
        return None
    ids = [int(p["id_produto"]) for p in prods]
    ph = ",".join(["%s"]*len(ids))
    saldos = ixc_select(f"""
        SELECT mp.id_produto, SUM(CASE WHEN mp.tipo='E' THEN mp.quantidade ELSE -mp.quantidade END) as saldo,
               MAX(p.descricao) as nome
        FROM ixcprovedor.movimento_produtos mp
        JOIN ixcprovedor.produtos p ON p.id = mp.id_produto
        WHERE mp.id_almox = %s AND mp.id_produto IN ({ph})
        GROUP BY mp.id_produto
        HAVING saldo > 0
        ORDER BY saldo DESC
        LIMIT 1
    """, tuple([ixc_almox_id] + ids))

    if not saldos:
        return None
    return {"id": saldos[0]["id_produto"], "nome": saldos[0]["nome"], "saldo": float(saldos[0]["saldo"])}

def _criar_requisicao_ixc(ixc_tecnico_id, tecnico_nome, itens, os_amanha, data_ref, ixc_almox_id=1):
    """Cria a requisição de material no IXC."""
    import base64, requests as req
    ixc_url   = os.getenv("IXC_API_URL", "https://sistema.cliquedf.com.br")
    ixc_user  = os.getenv("IXC_API_USER", "64")
    ixc_token = os.getenv("IXC_API_TOKEN", "")
    auth = base64.b64encode(f"{ixc_user}:{ixc_token}".encode()).decode()

    os_ids = [str(o["id"]) for o in os_amanha]
    obs = f"Requisição automática — {len(os_amanha)} instalações {data_ref} — OS: {', '.join(os_ids[:5])}"

    try:
        # Criar via MySQL direto (igual HubTecnico)
        from datetime import datetime as _dt2, timedelta as _td2
        brt_now = (_dt2.now() - _td2(hours=3)).strftime("%Y-%m-%d")
        ixc_insert("""INSERT INTO ixcprovedor.requisicao_material
            (id_tecnico, id_almox, `data`, status, id_filial, obs, pref_almox, tipo)
            VALUES (%s, %s, %s, 'A', 1, %s, 1, 'M')
        """, (ixc_tecnico_id, ixc_almox_id, brt_now, obs))
        ixc_req = ixc_select(
            "SELECT id FROM ixcprovedor.requisicao_material WHERE id_tecnico=%s ORDER BY id DESC LIMIT 1" % ixc_tecnico_id
        )
        if not ixc_req:
            log.error(f"[{tecnico_nome}] Erro ao buscar req criada")
            return
        req_id = ixc_req[0]["id"]
        log.info(f"[{tecnico_nome}] Requisição #{req_id} criada")

        # Adicionar itens via MySQL direto
        itens_ok = 0
        for item in itens:
            try:
                ixc_insert("""INSERT INTO ixcprovedor.requisicao_material_item
                    (id_produto, qtde, qtde_saldo, status, id_requisicao, descricao)
                    VALUES (%s, %s, %s, 'A', %s, '')
                """, (int(item["id_produto"]), item["qtd_falta"], item["qtd_falta"], req_id))
                itens_ok += 1
            except Exception as ei:
                log.warning(f"[{tecnico_nome}] Erro item {item['id_produto']}: {ei}")
        # Salvar no Hub
        # Salvar no Hub
        db_est = _db_estoque()
        db_est.execute("""
            INSERT INTO ht_requisicoes_auto
            (ixc_tecnico_id, tecnico_nome, ixc_requisicao_id, status, data_referencia, os_referencia, itens_json)
            VALUES (?,?,?,'pendente',?,?,?)
        """, (ixc_tecnico_id, tecnico_nome, req_id, data_ref,
              json.dumps(os_ids), json.dumps(itens)))
        db_est.commit()
        db_est.close()

        # Notificar
        linhas = [f"📋 <b>REQUISIÇÃO AUTOMÁTICA CRIADA</b>",
                  f"👤 Técnico: <b>{tecnico_nome}</b>",
                  f"📅 Para: {data_ref} ({len(os_amanha)} instalações)",
                  f"📦 Itens ({itens_ok}):"]
        for item in itens:
            linhas.append(f"  • {item['nome'][:35]} — {item['qtd_falta']:.0f} {item['grupo']}")
        linhas.append(f"\n🔔 Aguardando aprovação do almoxarife")

        msg = "\n".join(linhas)
        enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
        if TELEGRAM_GRUPO:
            enviar_telegram(msg, chat_id=TELEGRAM_GRUPO)

        log.info(f"[{tecnico_nome}] Requisição #{req_id} com {itens_ok} itens — notificado")

    except Exception as e:
        log.error(f"[{tecnico_nome}] Erro criar requisição: {e}")
        enviar_telegram(f"❌ <b>{tecnico_nome}</b>\nErro ao criar requisição: {e}", chat_id=TELEGRAM_AILTON)

def _notificar_desvios(tecnico_nome, desvios):
    linhas = [f"⚠️ <b>DESVIO DE CONSUMO — {tecnico_nome}</b>", f"Data: {_hoje()}", ""]
    for d in desvios[:10]:
        sinal = "📈" if d["diff"] > 0 else "📉"
        linhas.append(f"{sinal} OS #{d['os']} — {d['cliente'][:25]}")
        linhas.append(f"   {d['grupo']}: esperado {d['esperado']:.0f} | real {d['real']:.0f}")
    enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)

def calcular_devolucao(ixc_tecnico_id: int, tecnico_nome: str, ixc_almox_id: int):
    """Calcula material sobrado do dia e notifica almoxarife para devolução."""
    hoje = _hoje()
    log.info(f"[{tecnico_nome}] Calculando devolução {hoje}")

    # Requisições aprovadas hoje
    db_est = _db_estoque()
    reqs = db_est.execute("""
        SELECT itens_json FROM ht_requisicoes_auto
        WHERE ixc_tecnico_id=? AND status='aprovada'
        AND DATE(atualizado_em)=?
    """, (ixc_tecnico_id, hoje)).fetchall()
    db_est.close()

    if not reqs:
        log.info(f"[{tecnico_nome}] Sem requisições aprovadas hoje")
        return

    # Total requisitado hoje
    requisitado = {}
    for r in reqs:
        itens = json.loads(r["itens_json"] or "[]")
        for item in itens:
            pid = str(item.get("id_produto",""))
            qtd = float(item.get("qtd_falta", 0))
            nome = item.get("nome","")
            if pid not in requisitado:
                requisitado[pid] = {"nome": nome, "qtd": 0}
            requisitado[pid]["qtd"] += qtd

    # Total consumido hoje (OS finalizadas)
    db_tec = _db_tecnico()
    consumido = {}
    os_hoje = db_tec.execute("""
        SELECT o.ixc_os_id FROM ht_os o
        JOIN ht_os_execucao e ON e.ixc_os_id = o.ixc_os_id
        WHERE o.id_tecnico=(SELECT id FROM ht_usuarios WHERE ixc_funcionario_id=?)
        AND o.id_assunto=? AND o.status_hub='finalizada'
        AND DATE(e.finalizada_em,'-3 hours')=?
    """, (ixc_tecnico_id, 227, hoje)).fetchall()

    for os_row in os_hoje:
        mats = db_tec.execute("""
            SELECT p.ixc_produto_id, p.nome, m.quantidade
            FROM ht_os_materiais m
            JOIN ht_produtos p ON p.id=m.id_produto
            WHERE m.ixc_os_id=? AND m.id_tecnico=(SELECT id FROM ht_usuarios WHERE ixc_funcionario_id=?)
        """, (os_row["ixc_os_id"], ixc_tecnico_id)).fetchall()
        for m in mats:
            pid = str(m["ixc_produto_id"])
            if pid not in consumido:
                consumido[pid] = {"nome": m["nome"], "qtd": 0}
            consumido[pid]["qtd"] += float(m["quantidade"])
    db_tec.close()

    # Calcular sobras
    sobras = []
    for pid, info in requisitado.items():
        qtd_req = info["qtd"]
        qtd_con = consumido.get(pid, {}).get("qtd", 0)
        sobra   = qtd_req - qtd_con
        if sobra > 0.5:
            sobras.append({"id_produto": pid, "nome": info["nome"],
                          "requisitado": qtd_req, "consumido": qtd_con, "sobra": sobra})

    if not sobras:
        log.info(f"[{tecnico_nome}] Sem sobras hoje")
        return

    TELEGRAM_GRUPO = os.getenv("TELEGRAM_GRUPO","")
    linhas = [f"📦 <b>DEVOLUÇÃO DE MATERIAL — {tecnico_nome}</b>",
              f"Data: {hoje}\n",
              f"Os itens abaixo devem ser devolvidos ao almoxarifado:\n"]
    for s in sobras:
        linhas.append(f"  • {s['nome'][:35]}")
        linhas.append(f"    Req:{s['requisitado']:.0f} | Usado:{s['consumido']:.0f} | Sobra:{s['sobra']:.0f}")
    linhas.append(f"\n🔔 {tecnico_nome}, devolva os itens ao almoxarife!")

    msg = "\n".join(linhas)
    enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
    if TELEGRAM_GRUPO:
        enviar_telegram(msg, chat_id=TELEGRAM_GRUPO)
    log.info(f"[{tecnico_nome}] Devolução notificada — {len(sobras)} itens")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tecnico-id",  type=int, required=True)
    parser.add_argument("--tecnico-nome", type=str, required=True)
    parser.add_argument("--almox-id",    type=int, required=True)
    parser.add_argument("--ciclo",       choices=["auditoria","preparar","canceladas","devolucao"], required=True)
    args = parser.parse_args()

    if args.ciclo == "auditoria":
        auditar_consumo(args.tecnico_id, args.tecnico_nome, args.almox_id)
    elif args.ciclo == "canceladas":
        verificar_os_canceladas(args.tecnico_id, args.tecnico_nome)
    elif args.ciclo == "devolucao":
        calcular_devolucao(args.tecnico_id, args.tecnico_nome, args.almox_id)
    else:
        preparar_dia_seguinte(args.tecnico_id, args.tecnico_nome, args.almox_id)

def verificar_os_canceladas(ixc_tecnico_id: int, tecnico_nome: str):
    """Verifica se OS agendadas para hoje foram canceladas após a requisição."""
    hoje = _hoje()
    log.info(f"[{tecnico_nome}] Verificando OS canceladas {hoje}")

    db_est = _db_estoque()

    # Requisições automáticas criadas ontem para hoje
    reqs = db_est.execute("""
        SELECT id, ixc_requisicao_id, os_referencia, itens_json
        FROM ht_requisicoes_auto
        WHERE status = 'pendente'
        AND data_referencia = ?
        AND ixc_tecnico_id = ?
    """, (hoje, ixc_tecnico_id)).fetchall()

    if not reqs:
        log.info(f"[{tecnico_nome}] Sem requisições para hoje")
        db_est.close()
        return

    import base64, requests as req
    ixc_url   = os.getenv("IXC_API_URL","https://sistema.cliquedf.com.br")
    ixc_user  = os.getenv("IXC_API_USER","64")
    ixc_token = os.getenv("IXC_API_TOKEN","")
    auth = base64.b64encode(f"{ixc_user}:{ixc_token}".encode()).decode()

    for r in reqs:
        os_ids = json.loads(r["os_referencia"] or "[]")
        if not os_ids:
            continue

        # Verificar status das OS no IXC
        ph = ",".join(["%s"]*len(os_ids))
        os_status = ixc_select(
            f"SELECT id, status FROM ixcprovedor.su_oss_chamado WHERE id IN ({ph})",
            tuple(int(x) for x in os_ids)
        )
        status_map = {str(o["id"]): o["status"] for o in os_status}

        canceladas = [oid for oid in os_ids if status_map.get(str(oid)) in ("C","CA","X")]
        ativas     = [oid for oid in os_ids if status_map.get(str(oid)) in ("A","AG","AS","E")]

        if not canceladas:
            log.info(f"[{tecnico_nome}] Req #{r['ixc_requisicao_id']} — todas OS ativas")
            continue

        log.warning(f"[{tecnico_nome}] {len(canceladas)} OS canceladas — req #{r['ixc_requisicao_id']}")

        if not ativas:
            # Todas canceladas — cancelar requisição no IXC
            try:
                rc = req.put(
                    f"{ixc_url}/webservice/v1/requisicao_material/{r['ixc_requisicao_id']}",
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                    json={"status": "C", "obs": f"Cancelada automaticamente — todas OS canceladas"},
                    timeout=30
                )
                if rc.ok:
                    db_est.execute(
                        "UPDATE ht_requisicoes_auto SET status='cancelada' WHERE id=?", (r["id"],)
                    )
                    msg = (f"🚫 <b>REQUISIÇÃO CANCELADA — {tecnico_nome}</b>\n"
                           f"Req #{r['ixc_requisicao_id']} cancelada automaticamente.\n"
                           f"Motivo: todas as {len(canceladas)} OS foram canceladas.")
                    enviar_telegram(msg, chat_id=TELEGRAM_AILTON)
                    log.info(f"[{tecnico_nome}] Req #{r['ixc_requisicao_id']} cancelada")
            except Exception as e:
                log.error(f"[{tecnico_nome}] Erro cancelar req: {e}")
        else:
            # Parte cancelada — apenas alertar
            msg = (f"⚠️ <b>OS CANCELADAS — {tecnico_nome}</b>\n"
                   f"Data: {hoje}\n"
                   f"OS canceladas: {', '.join(str(x) for x in canceladas)}\n"
                   f"OS ainda ativas: {len(ativas)}\n"
                   f"Req #{r['ixc_requisicao_id']} mantida — verificar quantidade necessária.")
            enviar_telegram(msg, chat_id=TELEGRAM_AILTON)

    db_est.commit()
    db_est.close()

# ── CICLO 19H — DEVOLUÇÃO DE SOBRAS ──────────────────────────────────────────

