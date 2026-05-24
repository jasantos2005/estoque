#!/usr/bin/env python3
import sys, os, sqlite3, argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "estoque.db"
ENV_PATH = BASE_DIR / ".env"

def load_env(path):
    if not path.exists():
        print(f"[ERRO] .env nao encontrado em {path}")
        sys.exit(1)
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env(ENV_PATH)

def ixc_conn():
    try:
        import pymysql
        from pymysql.cursors import DictCursor
        conn = pymysql.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            database=os.getenv("DB_NAME"),
            charset="utf8mb4",
            cursorclass=DictCursor,
            connect_timeout=10,
        )
        conn.cursor().execute("SET SESSION time_zone = '-03:00'")
        return conn
    except Exception as e:
        print(f"[ERRO] Conexao IXC falhou: {e}")
        sys.exit(1)

def ixc_select(sql, params=()):
    conn = ixc_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()

def local_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

# produto_unidade IXC -> sigla local
UNIDADE_MAP = {
    1:  "un",   # unidade
    2:  "cx",   # caixa
    3:  "pct",  # pacote
    4:  "kg",   # quilograma
    5:  "l",    # litro
    6:  "m",    # metro
    7:  "rl",   # rolo
    8:  "m",    # metro (variante)
    13: "un",   # servico (ignorado no filtro)
    14: "rl",   # rolo/carretel
}

def unidade_str(id_un):
    return UNIDADE_MAP.get(int(id_un or 1), "un")

PALAVRAS_CASA = [
    "drop","onu","roteador","router","conector","patch cord","patch-cord",
    "cordao","splitter","roseta","cpe","ont ","wifi","wi-fi","indoor",
    "residencial","adaptador","cord sc","acoplador","esticador","arame",
]
PALAVRAS_INFRA = [
    "poste","fibra","cto","ceo","caixa de emenda","caixa emenda",
    "abracadeira","abraçadeira","duto","conduite","cabo optico",
    "cabo óptico","cabo de fibra","tubete","calha","rack","dgo","dio",
    "cabo utp","cabo ftp","cabo drop",
]

def inferir_categoria(descricao):
    d = descricao.lower()
    for p in PALAVRAS_CASA:
        if p in d: return "CASA"
    for p in PALAVRAS_INFRA:
        if p in d: return "INFRA"
    return "GERAL"

# ── LISTAR ────────────────────────────────────────────────────────────────────
def cmd_listar():
    print("\n📦  Produtos com saldo no almox principal (id=1)...\n")
    rows = ixc_select("""
        SELECT id, produto_descricao, produto_unidade, SUM(saldo) AS saldo_total
        FROM view_estoque_produtos_almox_filial
        WHERE almox_id = 1
          AND produto_ativo = 'S'
          AND produto_controla_estoque = 'S'
        GROUP BY id, produto_descricao, produto_unidade
        HAVING saldo_total > 0
        ORDER BY produto_descricao
    """)
    if not rows:
        print("  Nenhum produto com saldo.")
        return
    print(f"  {'ID':>6}  {'Descricao':<50}  {'Un':>4}  {'Saldo':>10}  Categoria")
    print(f"  {'─'*6}  {'─'*50}  {'─'*4}  {'─'*10}  {'─'*8}")
    for r in rows:
        un  = unidade_str(r["produto_unidade"])
        cat = inferir_categoria(r["produto_descricao"])
        print(f"  {r['id']:>6}  {r['produto_descricao']:<50}  {un:>4}  {float(r['saldo_total']):>10.2f}  {cat}")
    print(f"\n  Total: {len(rows)} produtos\n")

# ── SYNC COMPLETO ─────────────────────────────────────────────────────────────
def cmd_sync():
    print(f"\n🔄  Sync IXC -> estoque.db  [{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}]\n")

    print("  [1/3] Buscando produtos do IXC...")
    rows = ixc_select("""
        SELECT id, produto_descricao, produto_unidade, SUM(saldo) AS saldo_total
        FROM view_estoque_produtos_almox_filial
        WHERE almox_id = 1
          AND produto_ativo = 'S'
          AND produto_controla_estoque = 'S'
        GROUP BY id, produto_descricao, produto_unidade
        HAVING saldo_total >= 0
        ORDER BY produto_descricao
    """)
    if not rows:
        print("  [AVISO] Nenhum produto encontrado. Abortando.")
        return
    print(f"  -> {len(rows)} produtos encontrados")

    print("  [2/3] Gravando no SQLite...")
    conn = local_conn()
    cur  = conn.cursor()
    novos = atualizados = 0

    for r in rows:
        pid  = str(r["id"])
        desc = r["produto_descricao"].strip()
        un   = unidade_str(r["produto_unidade"])
        cat  = inferir_categoria(desc)
        sal  = float(r["saldo_total"])

        if cur.execute("SELECT id_produto FROM produtos WHERE id_produto=?", (pid,)).fetchone():
            cur.execute(
                "UPDATE produtos SET descricao=?,categoria=?,unidade=? WHERE id_produto=?",
                (desc, cat, un, pid)
            )
            atualizados += 1
        else:
            cur.execute(
                "INSERT INTO produtos (id_produto,descricao,categoria,unidade) VALUES (?,?,?,?)",
                (pid, desc, cat, un)
            )
            novos += 1

        cur.execute(
            "INSERT INTO saldos (id_produto,saldo) VALUES (?,?) ON CONFLICT(id_produto) DO UPDATE SET saldo=excluded.saldo",
            (pid, sal)
        )

    conn.commit()
    print(f"  -> {novos} novos  |  {atualizados} atualizados")

    print("  [3/3] Registrando ajuste de saldo...")
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO movimentacoes (id_produto,tipo,quantidade,responsavel,obs,data)
        SELECT id_produto,'ajuste',saldo,'sync_ixc','Sync automatico IXC',?
        FROM saldos
    """, (agora,))
    conn.commit()
    conn.close()
    print(f"\n✅  Concluido  [{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}]\n")

# ── SO SALDOS ─────────────────────────────────────────────────────────────────
def cmd_saldos():
    print(f"\n🔄  Atualizando saldos  [{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}]\n")
    rows = ixc_select("""
        SELECT id, SUM(saldo) AS saldo_total
        FROM view_estoque_produtos_almox_filial
        WHERE almox_id = 1
          AND produto_ativo = 'S'
          AND produto_controla_estoque = 'S'
        GROUP BY id
        HAVING saldo_total >= 0
    """)
    conn = local_conn()
    cur  = conn.cursor()
    ok = skip = 0
    for r in rows:
        pid = str(r["id"])
        if not cur.execute("SELECT id_produto FROM produtos WHERE id_produto=?", (pid,)).fetchone():
            skip += 1; continue
        cur.execute(
            "INSERT INTO saldos (id_produto,saldo) VALUES (?,?) ON CONFLICT(id_produto) DO UPDATE SET saldo=excluded.saldo",
            (pid, float(r["saldo_total"]))
        )
        ok += 1
    conn.commit(); conn.close()
    print(f"  ✅  {ok} saldos atualizados  |  {skip} sem cadastro local ignorados\n")

# ── MOVIMENTOS ────────────────────────────────────────────────────────────────
def cmd_movimentos():
    print(f"\n📋  Importando movimentacoes IXC (ultimos 30 dias)...\n")
    rows = ixc_select("""
        SELECT
            mp.id_produto,
            SUM(mp.qtde_saida) AS total_saida,
            MAX(mp.data)       AS ultima_data
        FROM movimento_produtos mp
        WHERE mp.tipo = 'S'
          AND mp.id_almox = 1
          AND mp.data >= DATE_SUB(NOW(), INTERVAL 90 DAY)
        GROUP BY mp.id_produto
    """)
    if not rows:
        print("  Nenhuma movimentacao encontrada.")
        return
    conn = local_conn()
    cur  = conn.cursor()
    ok = skip = 0
    for r in rows:
        pid = str(r["id_produto"])
        if not cur.execute("SELECT id_produto FROM produtos WHERE id_produto=?", (pid,)).fetchone():
            skip += 1; continue
        cur.execute(
            "DELETE FROM movimentacoes WHERE id_produto=? AND responsavel='sync_ixc' AND tipo='saida'",
            (pid,)
        )
        cur.execute(
            "INSERT INTO movimentacoes (id_produto,tipo,quantidade,responsavel,obs,data) VALUES (?,?,?,?,?,?)",
            (pid, "saida", float(r["total_saida"]), "sync_ixc", "Saida IXC 30d", str(r["ultima_data"]))
        )
        ok += 1
    conn.commit(); conn.close()
    print(f"  ✅  {ok} movimentacoes importadas  |  {skip} ignorados\n")

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync IXC -> Hub Estoque")
    parser.add_argument("--listar",     action="store_true")
    parser.add_argument("--saldos",     action="store_true")
    parser.add_argument("--movimentos", action="store_true")
    args = parser.parse_args()
    if args.listar:       cmd_listar()
    elif args.saldos:     cmd_saldos()
    elif args.movimentos: cmd_movimentos()
    else:                 cmd_sync()
