"""Точка входа для Hugging Face Space: API + собранный фронтенд (SPA) на одном порту."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("LL_BACKEND_DIR", "/app/backend"))
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402

DIST = Path(os.environ.get("LL_DIST_DIR", "/app/frontend/dist"))
app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    f = DIST / path
    if path and f.is_file():
        return FileResponse(f)
    return FileResponse(DIST / "index.html")
