from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.admin_router import admin
from app.provider_config import load_config
from app.router import router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()  # preload on startup
    yield


app = FastAPI(title="gotoken-gateway", version="0.1.0", lifespan=lifespan)

app.include_router(router)
app.include_router(admin)

# Mount static files at /static (for future CSS/JS if needed)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/gateway", response_class=HTMLResponse)
def gateway_page() -> str:
    return (STATIC_DIR / "gateway.html").read_text()
