"""
planos.py — CRUD de configuração de planos × grupos × produtos
"""
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from jose import jwt, JWTError

DB_PATH = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET  = "hub_estoque_secret_trocar_depois"
ALGORITMO = "HS256"
router = APIRouter()

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def verificar_token(authorization: str = ""):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token inválido")
    try:
        return jwt.decode(authorization[7:], SECRET, algorithms=[ALGORITMO])
    except JWTError:
        raise HTTPException(401, "Token expirado")

# ── MODELS ──────────────────────────────────────────────────────────────────

class PlanoIn(BaseModel):
    id_plano_ixc: int
    nome_plano: str
    ativo: Optional[int] = 1

class GrupoIn(BaseModel):
    nome_grupo: str
    quantidade: float
    unidade: Optional[str] = "un"
    fixo: Optional[int] = 0

class GrupoProdutoIn(BaseModel):
    id_produto: str
    prioridade: Optional[int] = 1

# ── PLANOS ───────────────────────────────────────────────────────────────────

@router.get("/")
def listar_planos(authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    planos = db.execute("""
        SELECT p.*, COUNT(DISTINCT g.id) as total_grupos
        FROM ht_plano_config p
        LEFT JOIN ht_plano_grupo g ON g.id_plano_config = p.id
        GROUP BY p.id ORDER BY p.nome_plano
    """).fetchall()
    db.close()
    return [dict(r) for r in planos]

@router.post("/")
def criar_plano(data: PlanoIn, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO ht_plano_config (id_plano_ixc, nome_plano, ativo) VALUES (?,?,?)",
            (data.id_plano_ixc, data.nome_plano, data.ativo)
        )
        db.commit()
        id_ = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": id_}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()

@router.put("/{id_plano}")
def atualizar_plano(id_plano: int, data: PlanoIn, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute(
        "UPDATE ht_plano_config SET id_plano_ixc=?, nome_plano=?, ativo=? WHERE id=?",
        (data.id_plano_ixc, data.nome_plano, data.ativo, id_plano)
    )
    db.commit()
    db.close()
    return {"ok": True}

@router.delete("/{id_plano}")
def deletar_plano(id_plano: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute("DELETE FROM ht_plano_config WHERE id=?", (id_plano,))
    db.commit()
    db.close()
    return {"ok": True}

# ── GRUPOS ───────────────────────────────────────────────────────────────────

@router.get("/{id_plano}/grupos")
def listar_grupos(id_plano: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    grupos = db.execute("""
        SELECT g.*, COUNT(gp.id) as total_produtos
        FROM ht_plano_grupo g
        LEFT JOIN ht_plano_grupo_produto gp ON gp.id_grupo = g.id
        WHERE g.id_plano_config=?
        GROUP BY g.id ORDER BY g.fixo DESC, g.nome_grupo
    """, (id_plano,)).fetchall()
    db.close()
    return [dict(r) for r in grupos]

@router.post("/{id_plano}/grupos")
def criar_grupo(id_plano: int, data: GrupoIn, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute(
        "INSERT INTO ht_plano_grupo (id_plano_config, nome_grupo, quantidade, unidade, fixo) VALUES (?,?,?,?,?)",
        (id_plano, data.nome_grupo, data.quantidade, data.unidade, data.fixo)
    )
    db.commit()
    id_ = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return {"ok": True, "id": id_}

@router.put("/{id_plano}/grupos/{id_grupo}")
def atualizar_grupo(id_plano: int, id_grupo: int, data: GrupoIn, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute(
        "UPDATE ht_plano_grupo SET nome_grupo=?, quantidade=?, unidade=?, fixo=? WHERE id=? AND id_plano_config=?",
        (data.nome_grupo, data.quantidade, data.unidade, data.fixo, id_grupo, id_plano)
    )
    db.commit()
    db.close()
    return {"ok": True}

@router.delete("/{id_plano}/grupos/{id_grupo}")
def deletar_grupo(id_plano: int, id_grupo: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute("DELETE FROM ht_plano_grupo WHERE id=? AND id_plano_config=?", (id_grupo, id_plano))
    db.commit()
    db.close()
    return {"ok": True}

# ── PRODUTOS DO GRUPO ────────────────────────────────────────────────────────

@router.get("/{id_plano}/grupos/{id_grupo}/produtos")
def listar_produtos_grupo(id_plano: int, id_grupo: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    rows = db.execute("""
        SELECT gp.id, gp.id_produto, gp.prioridade,
               p.descricao, COALESCE(s.saldo, 0) as saldo
        FROM ht_plano_grupo_produto gp
        JOIN produtos p ON p.id_produto = gp.id_produto
        LEFT JOIN saldos s ON s.id_produto = gp.id_produto
        WHERE gp.id_grupo=?
        ORDER BY gp.prioridade
    """, (id_grupo,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@router.post("/{id_plano}/grupos/{id_grupo}/produtos")
def adicionar_produto_grupo(id_plano: int, id_grupo: int, data: GrupoProdutoIn, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO ht_plano_grupo_produto (id_grupo, id_produto, prioridade) VALUES (?,?,?)",
            (id_grupo, data.id_produto, data.prioridade)
        )
        db.commit()
        id_ = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": id_}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()

@router.delete("/{id_plano}/grupos/{id_grupo}/produtos/{id_gp}")
def remover_produto_grupo(id_plano: int, id_grupo: int, id_gp: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    db.execute("DELETE FROM ht_plano_grupo_produto WHERE id=? AND id_grupo=?", (id_gp, id_grupo))
    db.commit()
    db.close()
    return {"ok": True}

# ── BUSCA DE PRODUTOS (para autocomplete) ───────────────────────────────────

@router.get("/produtos/buscar")
def buscar_produtos(q: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    rows = db.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, COALESCE(s.saldo,0) as saldo
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        WHERE p.descricao LIKE ? OR p.id_produto LIKE ?
        ORDER BY p.descricao LIMIT 20
    """, (f"%{q}%", f"%{q}%")).fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── PLANO COMPLETO (para o agente) ──────────────────────────────────────────

@router.get("/{id_plano}/completo")
def plano_completo(id_plano: int, authorization: str = Header("")):
    verificar_token(authorization)
    db = get_db()
    plano = db.execute("SELECT * FROM ht_plano_config WHERE id=?", (id_plano,)).fetchone()
    if not plano:
        raise HTTPException(404, "Plano não encontrado")
    grupos = db.execute("""
        SELECT g.*, COUNT(gp.id) as total_produtos
        FROM ht_plano_grupo g
        LEFT JOIN ht_plano_grupo_produto gp ON gp.id_grupo = g.id
        WHERE g.id_plano_config=? GROUP BY g.id
    """, (id_plano,)).fetchall()
    resultado = dict(plano)
    resultado["grupos"] = []
    for g in grupos:
        prods = db.execute("""
            SELECT gp.id, gp.id_produto, gp.prioridade,
                   p.descricao, COALESCE(s.saldo,0) as saldo
            FROM ht_plano_grupo_produto gp
            JOIN produtos p ON p.id_produto = gp.id_produto
            LEFT JOIN saldos s ON s.id_produto = gp.id_produto
            WHERE gp.id_grupo=? ORDER BY gp.prioridade
        """, (g["id"],)).fetchall()
        grupo_dict = dict(g)
        grupo_dict["produtos"] = [dict(p) for p in prods]
        resultado["grupos"].append(grupo_dict)
    db.close()
    return resultado
