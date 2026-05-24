import sqlite3, json
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from passlib.hash import bcrypt
from jose import jwt

DB_PATH  = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET   = "hub_estoque_secret_trocar_depois"
ALGORITMO = "HS256"

router = APIRouter()

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur  = conn.cursor()
    # Tabela users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin     INTEGER DEFAULT 0,
        is_active    INTEGER DEFAULT 1,
        perms        TEXT DEFAULT '{}',
        created_at   TEXT DEFAULT (datetime('now'))
    )""")
    # Garante master/master
    cur.execute("SELECT id FROM users WHERE username='master'")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO users (username,password_hash,is_admin,is_active,perms)
            VALUES (?,?,1,1,?)
        """, ("master", bcrypt.hash("master"),
              json.dumps({"dashboard":True,"estoque_casa":True,"estoque_infra":True,"compras":True})))
    conn.commit()
    conn.close()

def make_token(user_id, username, is_admin):
    payload = {
        "sub": str(user_id),
        "username": username,
        "is_admin": is_admin,
        "exp": datetime.utcnow() + timedelta(hours=72)
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITMO)

class LoginBody(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(body: LoginBody):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (body.username,))
    u = cur.fetchone()
    conn.close()
    if not u:
        raise HTTPException(401, "Credenciais inválidas")
    if not u["is_active"]:
        raise HTTPException(401, "Usuário inativo")
    if not bcrypt.verify(body.password, u["password_hash"]):
        raise HTTPException(401, "Credenciais inválidas")
    perms = json.loads(u["perms"] or "{}")
    tok   = make_token(u["id"], u["username"], bool(u["is_admin"]))
    return {
        "token": tok,
        "usuario": {
            "id":       u["id"],
            "username": u["username"],
            "is_admin": bool(u["is_admin"]),
            "perms":    perms
        }
    }
