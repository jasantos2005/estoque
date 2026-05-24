"""
Cron de alertas de estoque crítico/alerta — HubEstoque
Envia relatório HTML como documento no Telegram
"""
import os, sys, requests, logging, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
BRT = timezone(timedelta(hours=-3))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL      = "http://127.0.0.1:8011"
TOKEN        = os.getenv("TELEGRAM_TOKEN")
DESTINOS     = {"AILTON": os.getenv("TELEGRAM_AILTON")}

def gerar_token():
    import jwt, datetime as dt
    payload = {"sub": "cron_alerta", "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}
    return jwt.encode(payload, "hub_estoque_secret_trocar_depois", algorithm="HS256")

def enviar_documento(filepath: str, caption: str):
    for nome, cid in DESTINOS.items():
        if not cid:
            continue
        with open(filepath, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                data={"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
                files={"document": (Path(filepath).name, f, "text/html")},
                timeout=30
            )
        logger.info(f"Documento para {nome} ({cid}): {r.status_code}")

def gerar_html(criticos, alertas, agora):
    def linhas(itens, cor, label):
        if not itens:
            return ""
        rows = ""
        for i, p in enumerate(itens, 1):
            dias = int(p.get("dias_cobertura", 0))
            cob  = f"{dias}d" if dias > 0 else "zerado"
            bg   = "#fff0f0" if cor == "#c0392b" else "#fffbe6"
            rows += f"""
            <tr style="background:{bg if i%2==0 else 'white'}">
              <td>{i}</td>
              <td>{p.get('descricao','')}</td>
              <td>{p.get('categoria','—')}</td>
              <td style="text-align:center">{float(p.get('saldo',0)):.0f} {p.get('unidade','')}</td>
              <td style="text-align:center;font-weight:bold;color:{cor}">{cob}</td>
            </tr>"""
        return f"""
        <h2 style="color:{cor};margin-top:24px">{label} — {len(itens)} produto(s)</h2>
        <table>
          <thead><tr><th>#</th><th>Produto</th><th>Cat.</th><th>Saldo</th><th>Cobertura</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; padding: 24px; color: #222; }}
  h1   {{ color: #1a1a2e; border-bottom: 3px solid #00d4ff; padding-bottom: 8px; }}
  h2   {{ margin-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 13px; }}
  th   {{ background: #1a1a2e; color: white; padding: 8px 12px; text-align: left; }}
  td   {{ padding: 7px 12px; border-bottom: 1px solid #eee; }}
  tr:hover {{ background: #f5f5f5 !important; }}
  .footer {{ margin-top: 24px; font-size: 11px; color: #999; }}
</style>
</head>
<body>
  <h1>📦 Alerta de Estoque — {agora}</h1>
  <p style="color:#666">Produtos com movimentação ativa e cobertura abaixo de 20 dias.</p>
  {linhas(criticos, '#c0392b', '🔴 CRÍTICO (abaixo de 10 dias)')}
  {linhas(alertas,  '#e67e22', '⚠️ ALERTA (10 a 20 dias)')}
  <div class="footer">Gerado automaticamente pelo HubEstoque · {agora}</div>
</body>
</html>"""

def rodar():
    try:
        token = gerar_token()
        r = requests.get(f"{API_URL}/api/estoque/sugestao",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code != 200:
            logger.error(f"API {r.status_code}")
            return

        produtos = r.json().get("itens", [])
        ativos   = [p for p in produtos
                    if float(p.get("consumo_dia", 0)) > 0
                    and not (int(p.get("dias_cobertura", 0)) == 0 and float(p.get("saldo", 0)) == 0)]
        criticos = sorted([p for p in ativos if int(p.get("dias_cobertura", 999)) < 10],
                          key=lambda x: int(x.get("dias_cobertura", 0)))
        alertas  = sorted([p for p in ativos if 10 <= int(p.get("dias_cobertura", 999)) < 20],
                          key=lambda x: int(x.get("dias_cobertura", 0)))

        if not criticos and not alertas:
            logger.info("Nenhum produto em alerta.")
            return

        agora   = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
        html    = gerar_html(criticos, alertas, agora)
        caption = f"<b>📦 Alerta de Estoque</b> — {agora}\n🔴 {len(criticos)} críticos · ⚠️ {len(alertas)} em alerta"

        agora_nome = datetime.now(BRT).strftime("%Y%m%d_%H%M")
        nome_arquivo = f"alerta_estoque_{agora_nome}.html"
        tmppath = f"/tmp/{nome_arquivo}"
        with open(tmppath, "w", encoding="utf-8") as f:
            f.write(html)

        enviar_documento(tmppath, caption)
        os.unlink(tmppath)
        logger.info(f"Enviado: {len(criticos)} críticos, {len(alertas)} em alerta.")

    except Exception as e:
        logger.error(f"Erro: {e}")
        raise

if __name__ == "__main__":
    rodar()
