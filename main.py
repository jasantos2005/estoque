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

from app.routes import auth, estoque, compras, admin as admin_router, planos as planos_router, requisicoes as req_router
app.include_router(auth.router,          prefix="/api/auth",    tags=["Auth"])
app.include_router(estoque.router,       prefix="/api/estoque", tags=["Estoque"])
app.include_router(compras.router,       prefix="/api/compras", tags=["Compras"])
app.include_router(admin_router.router,  prefix="/api/admin",   tags=["Admin"])
app.include_router(planos_router.router, prefix="/api/planos",  tags=["Planos"])
app.include_router(req_router.router,   prefix="/api/requisicoes", tags=["Requisicoes"])

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    p = STATIC_DIR / "hub_estoque.html"
    content = p.read_text(encoding="utf-8") if p.exists() else "<h2>Hub Estoque OK</h2>"
    return HTMLResponse(content, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache", "Expires": "0"
    })

@app.get("/health")
async def health():
    return {"status": "ok", "app": "HubEstoque", "versao": "1.0.0"}

from app.routes.auth import init_db

@app.on_event("startup")
def startup():
    init_db()
