"""
agente_compras.py — Sugestão automática de compras
Quando produtos caem abaixo do mínimo, cria pedido e notifica
"""
import os, sqlite3, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")
import sys
sys.path.insert(0, str(BASE_DIR))
from app.services.notificador import enviar_telegram

DB_ESTOQUE      = str(BASE_DIR / "data" / "estoque.db")
TELEGRAM_AILTON = os.getenv("TELEGRAM_AILTON", "2135602169")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def _db():
    db = sqlite3.connect(DB_ESTOQUE, timeout=30)
    db.row_factory = sqlite3.Row
    return db

def verificar_estoque_minimo():
    log.info("Verificando estoque mínimo")
    db = _db()

    # Produtos abaixo do mínimo
    criticos = db.execute("""
        SELECT p.id_produto, p.descricao, p.categoria, p.unidade,
               COALESCE(s.saldo,0) as saldo,
               COALESCE(p.estoque_minimo,0) as estoque_minimo
        FROM produtos p
        LEFT JOIN saldos s ON s.id_produto = p.id_produto
        WHERE p.estoque_minimo > 0
        AND COALESCE(s.saldo,0) < p.estoque_minimo
        ORDER BY (COALESCE(s.saldo,0) / p.estoque_minimo) ASC
        LIMIT 20
    """).fetchall()

    if not criticos:
        log.info("Nenhum produto abaixo do mínimo")
        db.close()
        return

    # Verificar se já existe pedido pendente com esses produtos
    pedidos_recentes = db.execute("""
        SELECT itens FROM pedidos_compra
        WHERE status='pendente'
        AND DATE(criado_em) >= DATE('now','-3 hours','-7 days')
    """).fetchall()

    ids_em_pedido = set()
    for p in pedidos_recentes:
        try:
            itens = json.loads(p["itens"] or "[]")
            for it in itens:
                ids_em_pedido.add(str(it.get("id_produto","")))
        except:
            pass

    # Filtrar produtos sem pedido recente
    novos = [r for r in criticos if str(r["id_produto"]) not in ids_em_pedido]

    if not novos:
        log.info("Todos os produtos críticos já têm pedido pendente")
        db.close()
        return

    # Criar pedido de compra agrupado
    itens_pedido = []
    for p in novos:
        saldo = float(p["saldo"])
        minimo = float(p["estoque_minimo"])
        qtd_sugerida = round(max(minimo * 2 - saldo, minimo))
        itens_pedido.append({
            "id_produto": p["id_produto"],
            "descricao": p["descricao"],
            "quantidade": qtd_sugerida,
            "unidade": p["unidade"],
            "saldo_atual": saldo,
            "estoque_minimo": minimo
        })

    db.execute("""
        INSERT INTO pedidos_compra (itens, status, criado_por, criado_em)
        VALUES (?, 'pendente', 'agente_compras', datetime('now','-3 hours'))
    """, (json.dumps(itens_pedido),))
    db.commit()
    db.close()

    # Notificar
    linhas = [
        f"🛒 <b>SUGESTÃO DE COMPRA AUTOMÁTICA</b>",
        f"Data: {(datetime.now()-timedelta(hours=3)).strftime('%d/%m/%Y')}",
        f"{len(novos)} produtos abaixo do mínimo:\n"
    ]
    for p in itens_pedido[:10]:
        pct = round(p["saldo_atual"]/p["estoque_minimo"]*100) if p["estoque_minimo"] > 0 else 0
        linhas.append(f"  📦 {p['descricao'][:35]}")
        linhas.append(f"     Saldo:{p['saldo_atual']:.0f} | Mín:{p['estoque_minimo']:.0f} ({pct}%) | Comprar:{p['quantidade']:.0f}")

    linhas.append(f"\n🔔 Acesse Hub Estoque → Pedidos para aprovar")
    enviar_telegram("\n".join(linhas), chat_id=TELEGRAM_AILTON)
    log.info(f"Sugestão criada — {len(novos)} produtos")

if __name__ == "__main__":
    verificar_estoque_minimo()

def criar_pedido_ixc(itens_pedido):
    """Cria pedido de compra automaticamente no IXC."""
    import os as _os
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")

    try:
        import pymysql
        from pymysql.cursors import DictCursor
        conn = pymysql.connect(
            host=_os.getenv("DB_HOST"), port=int(_os.getenv("DB_PORT",3306)),
            user=_os.getenv("DB_USER"), password=_os.getenv("DB_PASS"),
            database=_os.getenv("DB_NAME"), charset="utf8mb4",
            cursorclass=DictCursor, connect_timeout=10
        )
        cur = conn.cursor()
        hoje = (datetime.now()-timedelta(hours=3)).strftime("%Y-%m-%d")

        # Buscar fornecedor padrão (primeiro ativo)
        cur.execute("SELECT id FROM fornecedor WHERE ativo='S' ORDER BY id LIMIT 1")
        forn = cur.fetchone()
        id_forn = forn["id"] if forn else 1

        # Criar pedido
        cur.execute("""
            INSERT INTO pedido_compra (data, id_fornecedor, id_condicoes_pagamento,
                previsao_faturamento, previsao_entrega, status, filial_id,
                id_modelo, valor_negociado, obs, status_liberado, tipo_frete,
                valor_frete, tipo_desconto, valor_desconto)
            VALUES (%s,%s,1,%s,%s,'A',1,1,0,%s,'N','S',0,'V',0)
        """, (hoje, id_forn, hoje, hoje, "Pedido automático — HubEstoque"))
        conn.commit()

        cur.execute("SELECT MAX(id) as id FROM pedido_compra")
        ixc_id = cur.fetchone()["id"]

        # Inserir itens
        for item in itens_pedido:
            # Buscar último preço
            cur.execute("""
                SELECT valor_unitario FROM pedido_compra_itens pci
                JOIN pedido_compra pc ON pc.id = pci.id_pedido_compra
                WHERE pci.id_produto = %s AND pc.status != 'C'
                ORDER BY pc.data DESC LIMIT 1
            """, (int(item["id_produto"]),))
            preco_row = cur.fetchone()
            preco = float(preco_row["valor_unitario"]) if preco_row else 0.0
            vt = round(preco * item["quantidade"], 2)

            cur.execute("""
                INSERT INTO pedido_compra_itens
                    (id_produto, id_unidade, quantidade, valor_unitario, valor_total,
                     id_pedido_compra, status, tipo, filial_id, unidade_sigla, observacao)
                VALUES (%s, 1, %s, %s, %s, %s, 'A', 'E', 1, %s, 'Auto HubEstoque')
            """, (int(item["id_produto"]), item["quantidade"], preco, vt, ixc_id,
                  item.get("unidade","un") or "un"))

        conn.commit()
        conn.close()
        log.info(f"Pedido IXC #{ixc_id} criado com {len(itens_pedido)} itens")
        return ixc_id

    except Exception as e:
        log.error(f"Erro criar pedido IXC: {e}")
        return None
