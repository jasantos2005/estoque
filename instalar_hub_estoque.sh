#!/bin/bash
# ============================================================
# HUB ESTOQUE — Script de instalação
# Cole no servidor: bash instalar_hub_estoque.sh
# ============================================================

set -e

PROJETO="/opt/automacoes/cliquedf/estoque"
SERVICO="hub_estoque"
PORTA="8001"

echo "======================================================"
echo " Hub Estoque — Instalação"
echo "======================================================"

# 1. Criar estrutura de diretórios
echo "[1/7] Criando estrutura de diretórios..."
mkdir -p $PROJETO/app/routes
mkdir -p $PROJETO/app/services
mkdir -p $PROJETO/static
mkdir -p $PROJETO/data
mkdir -p $PROJETO/logs
touch $PROJETO/app/__init__.py
touch $PROJETO/app/routes/__init__.py
touch $PROJETO/app/services/__init__.py

# 2. Criar virtualenv
echo "[2/7] Criando virtualenv..."
cd $PROJETO
python3 -m venv venv

# 3. Instalar dependências
echo "[3/7] Instalando dependências..."
$PROJETO/venv/bin/pip install --upgrade pip -q
$PROJETO/venv/bin/pip install fastapi uvicorn[standard] passlib[bcrypt] python-jose[cryptography] python-multipart -q

# 4. Criar requirements.txt
echo "[4/7] Gerando requirements.txt..."
cat > $PROJETO/requirements.txt << 'EOF'
fastapi
uvicorn[standard]
passlib[bcrypt]
python-jose[cryptography]
python-multipart
EOF

# 5. Criar main.py
echo "[5/7] Criando main.py..."
cat > $PROJETO/main.py << 'EOF'
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

BASE_DIR   = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR   = BASE_DIR / "data"
LOGS_DIR   = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Hub Estoque", version="1.0.0")

from app.routes import auth, estoque, compras, admin as admin_router
app.include_router(auth.router,          prefix="/api/auth",    tags=["Auth"])
app.include_router(estoque.router,       prefix="/api/estoque", tags=["Estoque"])
app.include_router(compras.router,       prefix="/api/compras", tags=["Compras"])
app.include_router(admin_router.router,  prefix="/api/admin",   tags=["Admin"])

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    p = STATIC_DIR / "hub_estoque.html"
    return p.read_text(encoding="utf-8") if p.exists() else "<h2>Hub Estoque OK</h2>"

@app.get("/health")
async def health():
    return {"status": "ok", "app": "HubEstoque", "versao": "1.0.0"}

from app.routes.auth import init_db

@app.on_event("startup")
def startup():
    init_db()
EOF

# 6. Criar app/routes/auth.py
echo "[6/7] Criando rotas..."
cat > $PROJETO/app/routes/auth.py << 'EOF'
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
        "exp": datetime.utcnow() + timedelta(hours=12)
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
EOF

# ── estoque.py ────────────────────────────────────────────────
cat > $PROJETO/app/routes/estoque.py << 'EOF'
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from jose import jwt, JWTError

DB_PATH = Path(__file__).parent.parent.parent / "data" / "estoque.db"
SECRET  = "hub_estoque_secret_trocar_depois"
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

def calcular_dias(saldo, consumo_dia):
    if not consumo_dia or consumo_dia <= 0:
        return 999
    return int(saldo / consumo_dia)

@router.get("/dashboard")
def dashboard(de: str = "", ate: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()

    # Busca todos os produtos com movimentação
    cur.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo, 0) as saldo,
               COALESCE(m.saida_periodo, 0) as saida_periodo
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        LEFT JOIN (
            SELECT id_produto, SUM(quantidade) as saida_periodo
            FROM movimentacoes
            WHERE tipo='saida'
              AND (? = '' OR data >= ?)
              AND (? = '' OR data <= ?)
            GROUP BY id_produto
        ) m ON m.id_produto = p.id_produto
    """, (de, de, ate, ate))
    rows = cur.fetchall()
    conn.close()

    dias_list = []
    itens_criticos = []
    valor_total = 0

    for r in rows:
        consumo_dia = r["saida_periodo"] / 30 if r["saida_periodo"] else 0
        dias = calcular_dias(r["saldo"], consumo_dia)
        dias_list.append(dias)
        valor_total += r["saldo"] * 0  # será preenchido quando tiver custo unitário

        if dias < 20:
            itens_criticos.append({
                "id_produto":     r["id_produto"],
                "descricao":      r["descricao"],
                "categoria":      r["categoria"],
                "unidade":        r["unidade"],
                "saldo":          r["saldo"],
                "saida_periodo":  r["saida_periodo"],
                "consumo_dia":    round(consumo_dia, 2),
                "dias_cobertura": dias,
            })

    casa = [d for d in dias_list if d < 999]
    infra = casa  # separar quando tiver categoria
    cob_casa  = int(sum(casa)  / len(casa))  if casa  else 0
    cob_infra = int(sum(infra) / len(infra)) if infra else 0

    return {
        "resumo": {
            "cobertura_casa":    cob_casa,
            "cobertura_infra":   cob_infra,
            "ruptura_pct_casa":  round(len([d for d in casa  if d < 5]) / max(len(casa), 1)  * 100),
            "ruptura_pct_infra": round(len([d for d in infra if d < 5]) / max(len(infra), 1) * 100),
            "itens_criticos":    sorted(itens_criticos, key=lambda x: x["dias_cobertura"]),
            "pedidos_pendentes": 0,
            "valor_estoque":     valor_total,
        }
    }

def get_itens(categoria_like: str, de: str, ate: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo, 0) as saldo,
               COALESCE(m.saida_periodo, 0) as saida_periodo
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        LEFT JOIN (
            SELECT id_produto, SUM(quantidade) as saida_periodo
            FROM movimentacoes
            WHERE tipo='saida'
              AND (? = '' OR data >= ?)
              AND (? = '' OR data <= ?)
            GROUP BY id_produto
        ) m ON m.id_produto = p.id_produto
        WHERE p.categoria LIKE ?
        ORDER BY p.descricao
    """, (de, de, ate, ate, categoria_like))
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        consumo_dia = r["saida_periodo"] / 30 if r["saida_periodo"] else 0
        dias = calcular_dias(r["saldo"], consumo_dia)
        result.append({
            "id_produto":     r["id_produto"],
            "descricao":      r["descricao"],
            "categoria":      r["categoria"],
            "unidade":        r["unidade"],
            "saldo":          round(r["saldo"], 2),
            "saida_periodo":  round(r["saida_periodo"], 2),
            "consumo_dia":    round(consumo_dia, 2),
            "dias_cobertura": dias,
        })
    return result

@router.get("/casa")
def estoque_casa(de: str = "", ate: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    return {"itens": get_itens("%CASA%", de, ate)}

@router.get("/infra")
def estoque_infra(de: str = "", ate: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    return {"itens": get_itens("%INFRA%", de, ate)}

@router.get("/sugestao")
def sugestao(de: str = "", ate: str = "", authorization: str = Header("")):
    verificar_token(authorization)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo, 0) as saldo,
               COALESCE(m.saida_periodo, 0) as saida_periodo
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        LEFT JOIN (
            SELECT id_produto, SUM(quantidade) as saida_periodo
            FROM movimentacoes
            WHERE tipo='saida'
              AND (? = '' OR data >= ?)
              AND (? = '' OR data <= ?)
            GROUP BY id_produto
        ) m ON m.id_produto = p.id_produto
    """, (de, de, ate, ate))
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        consumo_dia = r["saida_periodo"] / 30 if r["saida_periodo"] else 0
        dias = calcular_dias(r["saldo"], consumo_dia)
        if dias < 20:
            qtd_sugerida = max(20, int(consumo_dia * 30 * 2 - r["saldo"]))
            result.append({
                "id_produto":     r["id_produto"],
                "descricao":      r["descricao"],
                "unidade":        r["unidade"],
                "saldo":          round(r["saldo"], 2),
                "consumo_dia":    round(consumo_dia, 2),
                "dias_cobertura": dias,
                "qtd_sugerida":   qtd_sugerida,
            })
    return {"itens": sorted(result, key=lambda x: x["dias_cobertura"])}
EOF

# ── compras.py ────────────────────────────────────────────────
cat > $PROJETO/app/routes/compras.py << 'EOF'
import sqlite3, json
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import List
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
EOF

# ── admin.py ──────────────────────────────────────────────────
cat > $PROJETO/app/routes/admin.py << 'EOF'
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
EOF

# ── init_db de produtos (dados de exemplo) ────────────────────
cat > $PROJETO/data/seed.py << 'EOF'
"""
Roda uma vez para criar as tabelas de produtos/saldos/movimentacoes
e inserir dados de exemplo. Execute:
  python3 /opt/automacoes/cliquedf/estoque/data/seed.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "estoque.db"

conn = sqlite3.connect(str(DB))
cur  = conn.cursor()

cur.executescript("""
CREATE TABLE IF NOT EXISTS produtos (
    id_produto  TEXT PRIMARY KEY,
    descricao   TEXT NOT NULL,
    categoria   TEXT DEFAULT 'GERAL',
    unidade     TEXT DEFAULT 'un'
);
CREATE TABLE IF NOT EXISTS saldos (
    id_produto  TEXT PRIMARY KEY,
    saldo       REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS movimentacoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_produto  TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    quantidade  REAL NOT NULL,
    responsavel TEXT,
    obs         TEXT,
    data        TEXT DEFAULT (datetime('now'))
);
""")

produtos = [
    ("CAB-DROP-1X4",  "Cabo Drop 1x4 (rolo 500m)",    "CASA",  "m"),
    ("CAB-DROP-2X4",  "Cabo Drop 2x4 (rolo 500m)",    "CASA",  "m"),
    ("ONU-GPON-1P",   "ONU GPON 1 porta",             "CASA",  "un"),
    ("ONU-GPON-4P",   "ONU GPON 4 portas",            "CASA",  "un"),
    ("ROTEADOR-AC",   "Roteador AC Dual Band",        "CASA",  "un"),
    ("CONECTOR-SC",   "Conector SC/APC (pct 100un)",  "CASA",  "pct"),
    ("PATCH-CORD-1M", "Patch Cord SC/APC 1m",         "CASA",  "un"),
    ("POSTE-9M",      "Poste Concreto 9m",            "INFRA", "un"),
    ("POSTE-11M",     "Poste Concreto 11m",           "INFRA", "un"),
    ("CABO-FIBRA-6F", "Cabo Fibra 6 fibras (metro)",  "INFRA", "m"),
    ("CABO-FIBRA-12F","Cabo Fibra 12 fibras (metro)", "INFRA", "m"),
    ("CTO-8P",        "Caixa CTO 8 portas",           "INFRA", "un"),
    ("CTO-16P",       "Caixa CTO 16 portas",          "INFRA", "un"),
    ("CEO-24F",       "Caixa CEO 24 fibras",          "INFRA", "un"),
    ("ABRACADEIRA",   "Abraçadeira para poste",       "INFRA", "un"),
]

saldos = {
    "CAB-DROP-1X4":  120,
    "CAB-DROP-2X4":  800,
    "ONU-GPON-1P":   45,
    "ONU-GPON-4P":   4,
    "ROTEADOR-AC":   30,
    "CONECTOR-SC":   500,
    "PATCH-CORD-1M": 80,
    "POSTE-9M":      28,
    "POSTE-11M":     15,
    "CABO-FIBRA-6F": 2000,
    "CABO-FIBRA-12F":3500,
    "CTO-8P":        12,
    "CTO-16P":       6,
    "CEO-24F":       4,
    "ABRACADEIRA":   350,
}

saidas_30d = {
    "CAB-DROP-1X4":  1200,
    "CAB-DROP-2X4":  400,
    "ONU-GPON-1P":   30,
    "ONU-GPON-4P":   9,
    "ROTEADOR-AC":   18,
    "CONECTOR-SC":   200,
    "PATCH-CORD-1M": 40,
    "POSTE-9M":      9,
    "POSTE-11M":     4,
    "CABO-FIBRA-6F": 600,
    "CABO-FIBRA-12F":700,
    "CTO-8P":        6,
    "CTO-16P":       3,
    "CEO-24F":       2,
    "ABRACADEIRA":   120,
}

for p in produtos:
    cur.execute("INSERT OR IGNORE INTO produtos VALUES (?,?,?,?)", p)
for pid, saldo in saldos.items():
    cur.execute("INSERT OR REPLACE INTO saldos VALUES (?,?)", (pid, saldo))
    saida = saidas_30d.get(pid, 0)
    if saida:
        cur.execute("""
            INSERT INTO movimentacoes (id_produto,tipo,quantidade,responsavel,obs,data)
            VALUES (?,?,?,?,?,date('now','-15 days'))
        """, (pid, "saida", saida, "seed", "dados iniciais"))

conn.commit()
conn.close()
print("✅ Banco de dados populado com sucesso!")
print(f"   {len(produtos)} produtos inseridos.")
EOF

# ── Serviço systemd ───────────────────────────────────────────
echo "[7/7] Criando serviço systemd..."
cat > /etc/systemd/system/hub_estoque.service << EOF
[Unit]
Description=Hub Estoque — FastAPI
After=network.target

[Service]
User=root
WorkingDirectory=/opt/automacoes/cliquedf/estoque
ExecStart=/opt/automacoes/cliquedf/estoque/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001 --workers 1
Restart=always
RestartSec=5
StandardOutput=append:/opt/automacoes/cliquedf/estoque/logs/app.log
StandardError=append:/opt/automacoes/cliquedf/estoque/logs/error.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# ── Recarrega e inicia ────────────────────────────────────────
systemctl daemon-reload
systemctl enable hub_estoque
systemctl restart hub_estoque

echo ""
echo "======================================================"
echo " ✅ Instalação concluída!"
echo "======================================================"
echo ""
echo " Próximos passos:"
echo ""
echo "  1. Copie o hub_estoque.html para o servidor:"
echo "     scp hub_estoque.html root@SEU_IP:/opt/automacoes/cliquedf/estoque/static/"
echo ""
echo "  2. Popule o banco com dados de exemplo:"
echo "     python3 /opt/automacoes/cliquedf/estoque/data/seed.py"
echo ""
echo "  3. Verifique se está rodando:"
echo "     systemctl status hub_estoque"
echo "     curl http://localhost:8001/health"
echo ""
echo "  4. Acesse no navegador:"
echo "     http://SEU_IP:8001"
echo "     Login: master / Senha: master"
echo "======================================================"
