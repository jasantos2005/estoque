import sqlite3, json
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from passlib.hash import bcrypt
from jose import jwt, JWTError

DB_PATH   = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET    = "hub_estoque_secret_trocar_depois"
ALGORITMO = "HS256"

router = APIRouter()

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def verificar_admin(authorization: str):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token inválido")
    try:
        payload = jwt.decode(authorization[7:], SECRET, algorithms=[ALGORITMO])
    except JWTError:
        raise HTTPException(401, "Token expirado")
    if not payload.get("is_admin"):
        raise HTTPException(403, "Acesso negado — somente admins")
    return payload

@router.get("/usuarios")
def listar_usuarios(authorization: str = Header("")):
    verificar_admin(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT id,username,is_admin,is_active,perms,created_at FROM users ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    result = []
    for u in rows:
        result.append({
            "id":        u["id"],
            "username":  u["username"],
            "login":     u["username"],
            "is_admin":  bool(u["is_admin"]),
            "is_active": bool(u["is_active"]),
            "perms":     json.loads(u["perms"] or "{}"),
            "created_at": u["created_at"],
        })
    return {"usuarios": result}

class CreateBody(BaseModel):
    login: str
    senha: str
    is_admin: bool = False
    is_active: bool = True

@router.post("/create")
def criar_usuario(body: CreateBody, authorization: str = Header("")):
    verificar_admin(authorization)
    perms = {"dashboard": True, "estoque_casa": body.is_admin,
             "estoque_infra": body.is_admin, "compras": body.is_admin}
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users (username,password_hash,is_admin,is_active,perms)
            VALUES (?,?,?,?,?)
        """, (body.login, bcrypt.hash(body.senha),
              int(body.is_admin), int(body.is_active), json.dumps(perms)))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Usuário já existe")
    finally:
        conn.close()
    return {"ok": True, "login": body.login}

class ToggleBody(BaseModel):
    login: str

@router.post("/toggle")
def toggle_usuario(body: ToggleBody, authorization: str = Header("")):
    verificar_admin(authorization)
    if body.login == "master":
        raise HTTPException(400, "Não é possível alterar o master")
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET is_active = 1 - is_active WHERE username=?", (body.login,))
    conn.commit()
    conn.close()
    return {"ok": True}

class PermBody(BaseModel):
    login: str
    perm:  str

@router.post("/toggle_perm")
def toggle_perm(body: PermBody, authorization: str = Header("")):
    verificar_admin(authorization)
    if body.login == "master":
        raise HTTPException(400, "Não é possível alterar permissões do master")
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT perms FROM users WHERE username=?", (body.login,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Usuário não encontrado")
    perms = json.loads(row["perms"] or "{}")
    perms[body.perm] = not perms.get(body.perm, False)
    cur.execute("UPDATE users SET perms=? WHERE username=?", (json.dumps(perms), body.login))
    conn.commit()
    conn.close()
    return {"ok": True, "perm": body.perm, "valor": perms[body.perm]}
