"""OpenClaw SaaS Management API"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from api.database import init_db, seed_admin
from api.routers import agents, auth, billing, channels, tenants, usage
from api.services.k8s_client import k8s_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    await init_db()
    await seed_admin()
    await k8s_client.initialize()
    yield
    await k8s_client.close()


app = FastAPI(
    title="OpenClaw SaaS Management API",
    version="0.2.0",
    description="Control plane for OpenClaw on EKS SaaS platform",
    lifespan=lifespan,
)

# CORS - restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(agents.router)
app.include_router(channels.router)
app.include_router(usage.router)
app.include_router(billing.router)

# Serve web console static files if directory exists
CONSOLE_DIR = Path(__file__).parent.parent / "web-console" / "dist"
if CONSOLE_DIR.exists():
    from fastapi.responses import FileResponse

    # SPA fallback: serve index.html for all /console/* routes
    @app.get("/console/{full_path:path}")
    async def serve_console(full_path: str):
        file_path = CONSOLE_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(CONSOLE_DIR / "index.html")

    # Also serve /console exactly
    @app.get("/console")
    async def console_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/console/")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {
        "service": "OpenClaw SaaS Management API",
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/health",
    }
