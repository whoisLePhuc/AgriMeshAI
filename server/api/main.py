"""AgriMeshAI Central Server — FastAPI Application."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.routes import auth_routes, core_routes, edge_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="AgriMeshAI Central Server",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend from any origin (tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routes
app.include_router(auth_routes.router)
app.include_router(core_routes.router)
app.include_router(edge_routes.router)


@app.get("/")
async def root():
    return {"service": "AgriMeshAI Central Server", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
