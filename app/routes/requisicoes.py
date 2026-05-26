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
