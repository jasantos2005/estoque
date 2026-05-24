import sqlite3, json
from pathlib import Path
from datetime import datetime
try:
    from app.services.notificador import enviar_todos as _tg
except Exception:
    _tg = None
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from jose import jwt, JWTError

DB_PATH   = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET    = "hub_estoque_secret_trocar_depois"
ALGORITMO = "HS256"

router = APIRouter()

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def verificar_token(authorization: str = ""):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token inválido")
    try:
        return jwt.decode(authorization[7:], SECRET, algorithms=[ALGORITMO])
    except JWTError:
        raise HTTPException(401, "Token expirado")

def init_compras_db():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pedidos_compra (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        itens       TEXT NOT NULL,
        status      TEXT DEFAULT 'pendente',
        criado_por  TEXT,
        criado_em   TEXT DEFAULT (datetime('now'))
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS historico_compras (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        id_produto  TEXT,
        descricao   TEXT,
        quantidade  REAL,
        fornecedor  TEXT,
        valor_total REAL DEFAULT 0,
        data        TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()

init_compras_db()

class PedidoBody(BaseModel):
    itens: List[str]


def _notificar_pedido(ixc_id, itens_data, criado_por, nome_fornecedor=""):
    try:
        if not _tg:
            return
        from datetime import datetime, timezone, timedelta
        BRT = timezone(timedelta(hours=-3))
        agora = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
        partes = []
        partes.append("\U0001f6d2 <b>Novo Pedido de Compra</b> \u2014 " + agora)
        partes.append("\U0001f4cb Pedido <b>#" + str(ixc_id) + "</b> enviado ao IXC")
        partes.append("\U0001f464 Criado por: <b>" + criado_por + "</b>")
        if nome_fornecedor:
            partes.append("\U0001f3ed Fornecedor: <b>" + nome_fornecedor + "</b>")
        partes.append("")
        partes.append("<b>Itens (" + str(len(itens_data)) + "):</b>")
        valor_total = 0
        for it in itens_data:
            preco = float(it.get("preco", 0))
            qtd   = float(it.get("qtd", 0))
            saldo = float(it.get("saldo", 0))
            vt    = round(preco * qtd, 2)
            valor_total += vt
            partes.append("  \u2022 <b>" + str(it.get("descricao",""))[:45] + "</b>")
            partes.append("    Pedido: " + str(int(qtd)) + " " + str(it.get("unidade","")) + " | Saldo: " + str(int(saldo)) + " | R$ " + f"{preco:.2f}" + "/un | Total: R$ " + f"{vt:.2f}")
        partes.append("")
        partes.append("\U0001f4b0 <b>Valor total: R$ " + f"{valor_total:.2f}" + "</b>")
        _tg("\n".join(partes), parse_mode="HTML")
    except Exception:
        pass


def _notificar_cancelamento(pedido_id, itens, cancelado_por):
    try:
        if not _tg:
            return
        from datetime import datetime, timezone, timedelta
        BRT = timezone(timedelta(hours=-3))
        agora = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
        partes = []
        partes.append("\u274c <b>Pedido Cancelado</b> \u2014 " + agora)
        partes.append("\U0001f4cb Pedido <b>#" + str(pedido_id) + "</b> cancelado")
        partes.append("\U0001f464 Cancelado por: <b>" + cancelado_por + "</b>")
        if itens:
            partes.append("")
            partes.append("<b>Itens cancelados (" + str(len(itens)) + "):</b>")
            for it in itens:
                partes.append("  \u2022 " + str(it.get("descricao",""))[:45])
        _tg("\n".join(partes), parse_mode="HTML")
    except Exception:
        pass


def _notificar_cancelamento(pedido_id, itens, cancelado_por):
    try:
        if not _tg:
            return
        from datetime import datetime, timezone, timedelta
        BRT = timezone(timedelta(hours=-3))
        agora = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
        NL = "\n"
        msg  = f"\u274c <b>Pedido Cancelado</b> \u2014 {agora}{NL}"
        msg += f"\U0001f4cb Pedido <b>#{pedido_id}</b> cancelado{NL}"
        msg += f"\U0001f464 Cancelado por: <b>{cancelado_por}</b>{NL}"
        if itens:
            msg += f"{NL}<b>Itens cancelados ({len(itens)}):</b>{NL}"
            for it in itens:
                msg += f"  \u2022 {str(it.get('descricao',''))[:45]}{NL}"
        _tg(msg, parse_mode="HTML")
    except Exception:
        pass

def buscar_ultimo_preco(cur_ixc, id_produto: int) -> float:
    """Busca o último preço de compra do produto via movimento_produtos."""
    try:
        cur_ixc.execute("""
            SELECT COALESCE(NULLIF(custo,0), valor_unitario, 0) as preco
            FROM ixcprovedor.movimento_produtos
            WHERE id_produto = %s
            AND COALESCE(NULLIF(custo,0), valor_unitario, 0) > 0
            ORDER BY data DESC
            LIMIT 1
        """, (id_produto,))
        r = cur_ixc.fetchone()
        return float(r["preco"]) if r else 0.0
    except Exception:
        return 0.0


@router.post("/pedido")
def criar_pedido(body: PedidoBody, authorization: str = Header("")):
    payload = verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO pedidos_compra (itens, criado_por)
        VALUES (?, ?)
    """, (json.dumps(body.itens), payload.get("username", "—")))
    conn.commit()
    pedido_id = cur.lastrowid
    conn.close()
    return {"id": pedido_id, "status": "pendente", "itens": body.itens}

@router.get("/pedidos")
def listar_pedidos(authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM pedidos_compra ORDER BY criado_em DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        itens = json.loads(r["itens"] or "[]")
        result.append({
            "id":        r["id"],
            "status":    r["status"],
            "qtd_itens": len(itens),
            "criado_por": r["criado_por"],
            "criado_em": r["criado_em"],
        })
    return {"pedidos": result}

@router.get("/historico")
def historico(de: str = "", ate: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM historico_compras
        WHERE (? = '' OR data >= ?)
          AND (? = '' OR data <= ?)
        ORDER BY data DESC LIMIT 100
    """, (de, de, ate, ate))
    rows = cur.fetchall()
    conn.close()
    return {"itens": [dict(r) for r in rows]}


# ── IXC: fornecedores e condições ────────────────────────────────────────────
import os, pymysql
from pymysql.cursors import DictCursor as DC
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

def ixc_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT",3306)),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME"), charset="utf8mb4", cursorclass=DC, connect_timeout=10
    )

@router.get("/fornecedores")
def listar_fornecedores(authorization: str = Header("")):
    verificar_token(authorization)
    try:
        conn = ixc_conn()
        cur  = conn.cursor()
        cur.execute("SELECT id, razao, fantasia FROM fornecedor WHERE ativo='S' ORDER BY razao")
        rows = cur.fetchall()
        conn.close()
        return {"fornecedores": [{"id": r["id"], "nome": r["fantasia"] or r["razao"]} for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"Erro IXC: {e}")

@router.get("/condicoes")
def listar_condicoes(authorization: str = Header("")):
    verificar_token(authorization)
    try:
        conn = ixc_conn()
        cur  = conn.cursor()
        cur.execute("SELECT id, nome FROM condicoes_pagamento WHERE ativo='S' AND compra_venda IN ('A','C') ORDER BY nome")
        rows = cur.fetchall()
        conn.close()
        return {"condicoes": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"Erro IXC: {e}")

class PedidoIXCBody(BaseModel):
    itens: List[str]
    id_fornecedor: int
    id_condicao: int
    obs: Optional[str] = ""

@router.post("/pedido-ixc")

def criar_pedido_ixc(body: PedidoIXCBody, authorization: str = Header("")):
    payload = verificar_token(authorization)
    conn_local = get_db()
    cur_local   = conn_local.cursor()

    # Busca dados dos produtos
    itens_data = []
    for pid in body.itens:
        p = cur_local.execute(
            "SELECT p.id_produto, p.descricao, p.unidade, COALESCE(s.saldo,0) as saldo, m.consumo_dia FROM produtos p LEFT JOIN saldos s ON s.id_produto=p.id_produto LEFT JOIN (SELECT id_produto, SUM(quantidade)/90.0 as consumo_dia FROM movimentacoes WHERE tipo='saida' GROUP BY id_produto) m ON m.id_produto=p.id_produto WHERE p.id_produto=?",
            (pid,)
        ).fetchone()
        if p:
            consumo = float(p["consumo_dia"] or 0)
            qtd_sug = max(20, int(consumo * 60 - float(p["saldo"])))
            itens_data.append({"id_produto": pid, "descricao": p["descricao"], "unidade": p["unidade"], "qtd": qtd_sug, "saldo": float(p["saldo"])})

    if not itens_data:
        conn_local.close()
        raise HTTPException(400, "Nenhum produto válido")

    hoje = datetime.now().strftime("%Y-%m-%d")

    try:
        conn_ixc = ixc_conn()
        cur_ixc  = conn_ixc.cursor()

        # Cria pedido no IXC
        cur_ixc.execute("""
            INSERT INTO pedido_compra (data, id_fornecedor, id_condicoes_pagamento,
                previsao_faturamento, previsao_entrega, status, filial_id,
                id_modelo, valor_negociado, obs, status_liberado, tipo_frete,
                valor_frete, tipo_desconto, valor_desconto)
            VALUES (%s,%s,%s,%s,%s,'A',1,1,0,%s,'N','S',0,'V',0)
        """, (hoje, body.id_fornecedor, body.id_condicao, hoje, hoje, body.obs or "Gerado pelo HubEstoque"))
        conn_ixc.commit()

        cur_ixc.execute("SELECT MAX(id) as id FROM pedido_compra")
        ixc_id = cur_ixc.fetchone()["id"]

        # Insere itens no IXC com último preço de compra
        for it in itens_data:
            preco = buscar_ultimo_preco(cur_ixc, int(it["id_produto"]))
            vt    = round(preco * it["qtd"], 2)
            it["preco"] = preco
            cur_ixc.execute("""
                INSERT INTO pedido_compra_itens
                    (id_produto, id_unidade, quantidade, valor_unitario, valor_total,
                     id_pedido_compra, status, tipo, filial_id, unidade_sigla, observacao)
                VALUES (%s, 1, %s, %s, %s, %s, 'A', 'E', 1, %s, '')
            """, (int(it["id_produto"]), it["qtd"], preco, vt, ixc_id, it["unidade"] or "un"))
        conn_ixc.commit()
        conn_ixc.close()

        # Salva local
        criado_por = payload.get("username","master")
        cur_local.execute("""
            INSERT INTO pedidos_compra (itens, status, criado_por, criado_em)
            VALUES (?, 'enviado_ixc', ?, datetime('now','-3 hours'))
        """, (json.dumps([i["id_produto"] for i in itens_data]), criado_por))
        pid_local = cur_local.lastrowid
        conn_local.commit()
        conn_local.close()

        # Buscar nome do fornecedor
        nome_forn = ""
        try:
            cur_ixc2 = ixc_conn().cursor()
            cur_ixc2.execute("SELECT COALESCE(fantasia, razao) as nome FROM fornecedor WHERE id=%s", (body.id_fornecedor,))
            rf = cur_ixc2.fetchone()
            if rf: nome_forn = rf["nome"]
        except Exception:
            pass
        _notificar_pedido(ixc_id, itens_data, criado_por, nome_forn)
        return {"ok": True, "id_local": pid_local, "id_ixc": ixc_id, "msg": f"Pedido #{ixc_id} criado no IXC!"}

    except Exception as e:
        conn_local.close()
        raise HTTPException(500, f"Erro ao criar pedido no IXC: {e}")


# ── IXC: fornecedores e condicoes ────────────────────────────────────────────
import os, pymysql
from pymysql.cursors import DictCursor as DC
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

def ixc_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME"), charset="utf8mb4",
        cursorclass=DC, connect_timeout=10
    )

@router.get("/fornecedores")
def listar_fornecedores(authorization: str = Header("")):
    verificar_token(authorization)
    try:
        conn = ixc_conn()
        cur  = conn.cursor()
        cur.execute("SELECT id, razao, fantasia FROM fornecedor WHERE ativo='S' ORDER BY razao")
        rows = cur.fetchall()
        conn.close()
        return {"fornecedores": [{"id": r["id"], "nome": r["fantasia"] or r["razao"]} for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"Erro IXC: {e}")

@router.get("/condicoes")
def listar_condicoes(authorization: str = Header("")):
    verificar_token(authorization)
    try:
        conn = ixc_conn()
        cur  = conn.cursor()
        cur.execute("SELECT id, nome FROM condicoes_pagamento WHERE ativo='S' AND compra_venda IN ('A','C') ORDER BY nome")
        rows = cur.fetchall()
        conn.close()
        return {"condicoes": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"Erro IXC: {e}")

class PedidoIXCBody(BaseModel):
    itens: List[str]
    id_fornecedor: int
    id_condicao: int
    obs: Optional[str] = ""

@router.post("/pedido-ixc")
def criar_pedido_ixc(body: PedidoIXCBody, authorization: str = Header("")):
    payload = verificar_token(authorization)
    conn_local = get_db()
    cur_local  = conn_local.cursor()

    itens_data = []
    for pid in body.itens:
        p = cur_local.execute("""
            SELECT p.id_produto, p.descricao, p.unidade,
                   COALESCE(s.saldo, 0) as saldo,
                   COALESCE(m.consumo_dia, 0) as consumo_dia
            FROM produtos p
            LEFT JOIN saldos s ON s.id_produto = p.id_produto
            LEFT JOIN (
                SELECT id_produto, SUM(quantidade)/90.0 as consumo_dia
                FROM movimentacoes WHERE tipo='saida'
                GROUP BY id_produto
            ) m ON m.id_produto = p.id_produto
            WHERE p.id_produto = ?
        """, (pid,)).fetchone()
        if p:
            consumo  = float(p["consumo_dia"] or 0)
            qtd_sug  = max(20, int(consumo * 60 - float(p["saldo"])))
            itens_data.append({
                "id_produto": pid,
                "descricao":  p["descricao"],
                "unidade":    p["unidade"] or "un",
                "qtd":        qtd_sug,
                "saldo":      float(p["saldo"]),
            })

    if not itens_data:
        conn_local.close()
        raise HTTPException(400, "Nenhum produto valido")

    hoje = datetime.now().strftime("%Y-%m-%d")

    try:
        conn_ixc = ixc_conn()
        cur_ixc  = conn_ixc.cursor()

        cur_ixc.execute("""
            INSERT INTO pedido_compra
                (data, id_fornecedor, id_condicoes_pagamento,
                 previsao_faturamento, previsao_entrega, status, filial_id,
                 id_modelo, valor_negociado, obs, status_liberado,
                 tipo_frete, valor_frete, tipo_desconto, valor_desconto)
            VALUES (%s,%s,%s,%s,%s,'A',1,1,0,%s,'N','S',0,'V',0)
        """, (hoje, body.id_fornecedor, body.id_condicao, hoje, hoje,
              body.obs or "Gerado pelo HubEstoque"))
        conn_ixc.commit()

        cur_ixc.execute("SELECT MAX(id) as id FROM pedido_compra")
        ixc_id = cur_ixc.fetchone()["id"]

        for it in itens_data:
            preco = buscar_ultimo_preco(cur_ixc, int(it["id_produto"]))
            vt    = round(preco * it["qtd"], 2)
            it["preco"] = preco
            cur_ixc.execute("""
                INSERT INTO pedido_compra_itens
                    (id_produto, id_unidade, quantidade, valor_unitario,
                     valor_total, id_pedido_compra, status, tipo,
                     filial_id, unidade_sigla, observacao)
                Values (%s,1,%s,%s,%s,%s,'A','E',1,%s,'')
            """, (int(it["id_produto"]), it["qtd"], preco, vt, ixc_id, it["unidade"]))
        conn_ixc.commit()
        conn_ixc.close()

        criado_por = payload.get("username", "master")
        cur_local.execute("""
            INSERT INTO pedidos_compra (itens, status, criado_por, criado_em)
            VALUES (?, 'enviado_ixc', ?, datetime('now','-3 hours'))
        """, (json.dumps([i["id_produto"] for i in itens_data]), criado_por))
        conn_local.commit()
        pid_local = cur_local.lastrowid
        conn_local.close()

        # Buscar nome do fornecedor
        nome_forn = ""
        try:
            cur_ixc2 = ixc_conn().cursor()
            cur_ixc2.execute("SELECT COALESCE(fantasia, razao) as nome FROM fornecedor WHERE id=%s", (body.id_fornecedor,))
            rf = cur_ixc2.fetchone()
            if rf: nome_forn = rf["nome"]
        except Exception:
            pass
        _notificar_pedido(ixc_id, itens_data, criado_por, nome_forn)
        return {"ok": True, "id_local": pid_local, "id_ixc": ixc_id,
                "msg": f"Pedido #{ixc_id} criado no IXC com {len(itens_data)} itens!"}

    except Exception as e:
        conn_local.close()
        raise HTTPException(500, f"Erro ao criar pedido no IXC: {e}")


@router.get("/pedido/{id}")
def ver_pedido(id: int, authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    ped = cur.execute("SELECT * FROM pedidos_compra WHERE id=?", (id,)).fetchone()
    if not ped:
        conn.close()
        raise HTTPException(404, "Pedido nao encontrado")
    itens_ids = json.loads(ped["itens"] or "[]")
    itens_data = []
    for pid in itens_ids:
        p = cur.execute("""
            SELECT p.id_produto, p.descricao, p.unidade,
                   COALESCE(s.saldo,0) as saldo,
                   COALESCE(m.consumo_dia,0) as consumo_dia
            FROM produtos p
            LEFT JOIN saldos s ON s.id_produto=p.id_produto
            LEFT JOIN (
                SELECT id_produto, SUM(quantidade)/90.0 as consumo_dia
                FROM movimentacoes WHERE tipo='saida' GROUP BY id_produto
            ) m ON m.id_produto=p.id_produto
            WHERE p.id_produto=?
        """, (pid,)).fetchone()
        if p:
            consumo = float(p["consumo_dia"] or 0)
            qtd_sug = max(20, int(consumo * 60 - float(p["saldo"])))
            itens_data.append({
                "id_produto":   pid,
                "descricao":    p["descricao"],
                "unidade":      p["unidade"] or "un",
                "saldo":        round(float(p["saldo"]), 2),
                "consumo_dia":  round(consumo, 2),
                "qtd_sugerida": qtd_sug,
            })
        else:
            itens_data.append({"id_produto": pid, "descricao": pid, "unidade": "un", "saldo": 0, "consumo_dia": 0, "qtd_sugerida": 20})
    conn.close()
    return {"id": id, "status": ped["status"], "itens": itens_data}

@router.post("/pedido/{id}/cancelar")
def cancelar_pedido(id: int, authorization: str = Header("")):
    payload_tk = verificar_token(authorization)
    cancelado_por = payload_tk.get("username", "—") if isinstance(payload_tk, dict) else "—"
    conn = get_db()
    # Buscar itens antes de cancelar
    p_itens = conn.execute("SELECT itens FROM pedidos_compra WHERE id=?", (id,)).fetchone()
    conn.execute("UPDATE pedidos_compra SET status='cancelado' WHERE id=?", (id,))
    conn.commit()
    # Notificar
    try:
        itens_lista = []
        if p_itens:
            ids = json.loads(p_itens["itens"] or "[]")
            for pid in ids:
                row = conn.execute("SELECT descricao FROM produtos WHERE id_produto=?", (pid,)).fetchone()
                if row: itens_lista.append({"descricao": row["descricao"]})
        _notificar_cancelamento(id, itens_lista, cancelado_por)
    except Exception:
        pass
    conn.close()
    return {"ok": True}


@router.get("/produtos-consumo")
def produtos_consumo(authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo, 0) as saldo,
               COALESCE(m.saida_total, 0) as saida_total
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        INNER JOIN (
            SELECT id_produto, SUM(quantidade) as saida_total
            FROM movimentacoes WHERE tipo='saida'
            GROUP BY id_produto
        ) m ON m.id_produto = p.id_produto
        WHERE m.saida_total > 0 AND COALESCE(s.saldo, 0) >= 0
        ORDER BY p.descricao
    """)
    rows = cur.fetchall()
    conn.close()

    def calc_dias(saldo, consumo_dia):
        if not consumo_dia or consumo_dia <= 0: return 999
        return int(saldo / consumo_dia)

    result = []
    for r in rows:
        consumo_dia = float(r["saida_total"]) / 90
        dias = calc_dias(float(r["saldo"]), consumo_dia)
        result.append({
            "id_produto":     r["id_produto"],
            "descricao":      r["descricao"],
            "categoria":      r["categoria"] or "GERAL",
            "unidade":        r["unidade"] or "un",
            "saldo":          round(float(r["saldo"]), 2),
            "saida_total":    round(float(r["saida_total"]), 2),
            "consumo_dia":    round(consumo_dia, 2),
            "dias_cobertura": dias,
        })
    return {"itens": result}


@router.get("/projecao-ativacoes")
def projecao_ativacoes(ativacoes: int = 100, authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT ca.id_produto, ca.qtd_os, ca.total_saida, ca.media_por_os, ca.atualizado,
               p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo, 0) as saldo
        FROM consumo_por_ativacao ca
        JOIN produtos p ON p.id_produto = ca.id_produto
        LEFT JOIN saldos s ON s.id_produto = ca.id_produto
        WHERE ca.media_por_os > 0 AND ca.id_assunto = 227
        ORDER BY ca.total_saida DESC
    """)
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        necessario   = round(float(r["media_por_os"]) * ativacoes, 2)
        saldo        = float(r["saldo"])
        a_comprar    = max(0, round(necessario - saldo, 2))
        result.append({
            "id_produto":    r["id_produto"],
            "descricao":     r["descricao"],
            "categoria":     r["categoria"] or "GERAL",
            "unidade":       r["unidade"] or "un",
            "saldo":         round(saldo, 2),
            "media_por_os":  round(float(r["media_por_os"]), 4),
            "qtd_os":        r["qtd_os"],
            "necessario":    necessario,
            "a_comprar":     a_comprar,
            "atualizado":    r["atualizado"],
        })
    return {"itens": result, "ativacoes": ativacoes, "total": len(result)}


@router.post("/pedido/{id}/enviado")
def marcar_enviado(id: int, authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    conn.execute("UPDATE pedidos_compra SET status='enviado_ixc' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/relatorio-entradas")
def relatorio_entradas(de: str = "", ate: str = "", tipo: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    FORNECEDORES_DEVOLUCAO = (11, 12, 205)
    forn_devol = ",".join(str(f) for f in FORNECEDORES_DEVOLUCAO)
    try:
        conn = ixc_conn()
        cur  = conn.cursor()
        filtro_data = ""
        params_base = []
        if de:
            filtro_data += " AND e.data_entrada >= %s"
            params_base.append(de)
        if ate:
            filtro_data += " AND e.data_entrada <= %s"
            params_base.append(ate)

        compras = []
        if not tipo or tipo == "compra":
            sql = (
                "SELECT e.data_entrada as data, e.id_fornecedor,"
                " mp.id_produto, mp.descricao,"
                " SUM(mp.quantidade) as total,"
                " e.numero_nf, e.valor_total"
                " FROM ixcprovedor.entrada e"
                " JOIN ixcprovedor.movimento_produtos mp ON mp.id_entrada = e.id"
                " WHERE e.id_fornecedor NOT IN (" + forn_devol + ")"
                " AND e.gera_estoque = 'S'"
                + filtro_data +
                " GROUP BY e.data_entrada, e.id_fornecedor, mp.id_produto,"
                " mp.descricao, e.numero_nf, e.valor_total"
                " ORDER BY e.data_entrada DESC, total DESC"
                " LIMIT 300"
            )
            cur.execute(sql, list(params_base))
            for r in cur.fetchall():
                compras.append({
                    "id_produto":  str(r["id_produto"] or ""),
                    "descricao":   r["descricao"] or "—",
                    "data":        str(r["data"]),
                    "total":       float(r["total"]),
                    "tipo":        "compra",
                    "numero_nf":   r["numero_nf"] or "",
                    "valor_total": float(r["valor_total"] or 0),
                })

        devolucoes = []
        if not tipo or tipo == "devolucao":
            sql2 = (
                "SELECT e.data_entrada as data, e.id_fornecedor,"
                " mp.id_produto, mp.descricao,"
                " SUM(mp.quantidade) as total,"
                " SUM(mp.quantidade * COALESCE(NULLIF(mp.custo,0), mp.valor_unitario, 0)) as valor_itens,"
                " e.numero_nf, e.valor_total"
                " FROM ixcprovedor.entrada e"
                " JOIN ixcprovedor.movimento_produtos mp ON mp.id_entrada = e.id"
                " WHERE e.id_fornecedor IN (" + forn_devol + ")"
                " AND e.gera_estoque = 'S'"
                + filtro_data +
                " GROUP BY e.data_entrada, e.id_fornecedor, mp.id_produto,"
                " mp.descricao, e.numero_nf, e.valor_total"
                " ORDER BY e.data_entrada DESC, total DESC"
                " LIMIT 300"
            )
            cur.execute(sql2, list(params_base))
            for r in cur.fetchall():
                devolucoes.append({
                    "id_produto":  str(r["id_produto"] or ""),
                    "descricao":   r["descricao"] or "—",
                    "data":        str(r["data"]),
                    "total":       float(r["total"]),
                    "tipo":        "devolucao",
                    "numero_nf":   r["numero_nf"] or "",
                    "valor_total": float(r["valor_total"] or 0),
                    "valor_itens": float(r["valor_itens"] or 0),
                })

        conn.close()
        return {
            "compras":          compras,
            "devolucoes":       devolucoes,
            "total_compras":    len(compras),
            "total_devolucoes": len(devolucoes),
        }
    except Exception as e:
        raise HTTPException(500, f"Erro IXC: {e}")
