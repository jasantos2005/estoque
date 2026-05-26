"""
requisicoes.py — Rotas de acompanhamento de requisições automáticas
"""
import sqlite3, json
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from jose import jwt, JWTError

DB_PATH   = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET    = "hub_estoque_secret_trocar_depois"
ALGORITMO = "HS256"
router    = APIRouter()

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def verificar_token(authorization: str = ""):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token inválido")
    try:
        return jwt.decode(authorization[7:], SECRET, algorithms=[ALGORITMO])
    except JWTError:
        raise HTTPException(401, "Token expirado")

@router.get("/")
def listar_requisicoes(dias: int = 7, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    rows = db.execute("""
        SELECT id, ixc_tecnico_id, tecnico_nome, ixc_requisicao_id,
               status, data_referencia, os_referencia, itens_json,
               criado_em, atualizado_em
        FROM ht_requisicoes_auto
        WHERE DATE(criado_em) >= DATE('now','-3 hours',? || ' days')
        ORDER BY criado_em DESC
    """, (f"-{dias}",)).fetchall()
    db.close()
    result = []
    for r in rows:
        row = dict(r)
        row["itens"] = json.loads(r["itens_json"] or "[]")
        row["os_ids"] = json.loads(r["os_referencia"] or "[]")
        result.append(row)
    return result

@router.get("/resumo")
def resumo_requisicoes(authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    resumo = db.execute("""
        SELECT status, COUNT(*) as total
        FROM ht_requisicoes_auto
        WHERE DATE(criado_em) >= DATE('now','-3 hours','-30 days')
        GROUP BY status
    """).fetchall()
    db.close()
    return {r["status"]: r["total"] for r in resumo}

@router.get("/fila-almoxarife")
def fila_almoxarife(authorization: str = Header("")):
    """Retorna requisições pendentes ordenadas por horário da primeira OS do técnico."""
    verificar_token(authorization)
    import sys
    sys.path.insert(0, "/opt/automacoes/cliquedf/tecnico")
    from app.services.ixc_db import ixc_select
    from datetime import datetime, timedelta

    db = get_db()
    hoje = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d")
    amanha = (datetime.now() - timedelta(hours=3) + timedelta(days=1)).strftime("%Y-%m-%d")

    reqs = db.execute("""
        SELECT id, ixc_tecnico_id, tecnico_nome, ixc_requisicao_id,
               status, data_referencia, itens_json, criado_em
        FROM ht_requisicoes_auto
        WHERE status = 'pendente'
        ORDER BY data_referencia, criado_em
    """).fetchall()
    db.close()

    if not reqs:
        return []

    # Buscar horário da primeira OS de cada técnico
    result = []
    for r in reqs:
        row = dict(r)
        row["itens"] = json.loads(r["itens_json"] or "[]")
        row["primeira_os"] = None
        row["prioridade"] = 99

        try:
            os_tec = ixc_select("""
                SELECT MIN(TIME(data_agenda)) as primeira_os
                FROM ixcprovedor.su_oss_chamado
                WHERE id_tecnico = %s
                AND id_assunto = 227
                AND status IN ('A','AG')
                AND DATE(data_reservada) = %s
            """, (r["ixc_tecnico_id"], r["data_referencia"]))

            if os_tec and os_tec[0]["primeira_os"]:
                row["primeira_os"] = str(os_tec[0]["primeira_os"])
                # Converter para minutos para ordenação
                h, m, s = str(os_tec[0]["primeira_os"]).split(":")
                row["prioridade"] = int(h) * 60 + int(m)
        except:
            pass

        result.append(row)

    # Ordenar por horário da primeira OS
    result.sort(key=lambda x: x["prioridade"])
    return result
