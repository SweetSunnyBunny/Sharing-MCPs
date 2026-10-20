'Application module.'


from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services import coloring_drawer as drawer

router = APIRouter(prefix="/api/coloring")


def _missing():
    return JSONResponse(status_code=404, content={"error": "no such page"})


@router.get("/pages")
async def coloring_pages():
    return {"pages": drawer.list_pages()}


@router.get("/shelf")
async def coloring_shelf():
    'Coloring shelf.'
    return {"pages": drawer.shelf()}


@router.get("/palette")
async def coloring_palette():
    return drawer.palette()


@router.get("/page/{page_id}")
async def coloring_page(page_id: str):
    try:
        return drawer.page(page_id)
    except drawer.PageNotFound:
        return _missing()


@router.get("/page/{page_id}/strokes")
async def coloring_strokes(page_id: str):
    try:
        return {"events": drawer.strokes(page_id)}
    except drawer.PageNotFound:
        return _missing()


@router.get("/page/{page_id}/{name}")
async def coloring_file(page_id: str, name: str):
    try:
        path = drawer.file_path(page_id, name)
    except drawer.PageNotFound:
        return _missing()
    # Her paint changes every stroke; never let a phone cache serve an old layer.
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/page/{page_id}/save")
async def coloring_save(page_id: str, paint: UploadFile | None = File(None), events: str = Form("[]")):
    try:
        evs = json.loads(events or "[]")
        if not isinstance(evs, list):
            raise ValueError("events must be a list")
        blob = await paint.read() if paint is not None else None
        return drawer.save_progress(page_id, blob, evs)
    except drawer.PageNotFound:
        return _missing()
    except (ValueError, json.JSONDecodeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


class HangRequest(BaseModel):
    where: str = "fridge"


@router.post("/page/{page_id}/hang")
async def coloring_hang(page_id: str, req: HangRequest):
    try:
        return drawer.hang_up(page_id, req.where)
    except drawer.PageNotFound:
        return _missing()
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
