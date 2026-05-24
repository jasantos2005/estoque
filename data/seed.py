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
