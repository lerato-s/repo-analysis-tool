"""FastAPI application entry point for the Repo Analysis Tool."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import config, db
from .metrics import engine
from .routers import authors, explore, repos


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.reset_interrupted()
    yield


app = FastAPI(title="Repo Analysis Tool", version="1.0.0", lifespan=lifespan)

# The Vite dev server runs on another origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repos.router)
app.include_router(authors.router)
app.include_router(explore.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "cache": engine.cache_info()}


if config.FRONTEND_DIST.is_dir():
    # Serve the built dashboard (SPA) at the root. A plain
    # ``StaticFiles(html=True)`` mount would 404 on client-side routes such as
    # ``/r/2``, breaking reloads and deep links, so resolve each request
    # manually: real files (assets, favicon) win, everything else falls back
    # to index.html and lets the router take over.
    _dist = config.FRONTEND_DIST.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (_dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(_dist):
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")

else:

    @app.get("/")
    def index() -> JSONResponse:
        return JSONResponse(
            {
                "message": "RAT API is running. Build the dashboard with "
                "`npm --prefix frontend run build` (or run its dev server) "
                "to use the UI.",
                "docs": "/docs",
            }
        )
