"""REST: sanctuary — proxied to hearth-hub (Cloudflare Worker).

The viewer now talks directly to hearth-hub for state/portraits/backgrounds.
These routes redirect any remaining callers to the cloud API.
"""

# ANAM GUIDE: SANCTUARY CLOUD REDIRECTS
# What: A thin leftover shim — just redirects old sanctuary URLs (state, portraits, backgrounds) to the hearth-hub Cloudflare Worker in the cloud.
# Called by: barely anything anymore; static/js/sanctuary-viewer.js talks to hearth-hub directly. This only catches stragglers still hitting /api/sanctuary.
# Edit here when: the hearth-hub worker's address changes (the HEARTH_HUB line below). Real sanctuary behavior lives in the cloud worker, not here.

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

HEARTH_HUB = "https://hearth-hub.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev"

router = APIRouter(prefix="/api/sanctuary")


@router.get("/state")
async def get_state():
    return RedirectResponse(f"{HEARTH_HUB}/api/sanctuary/state", status_code=302)


@router.get("/portrait/{identity}")
async def get_portrait(identity: str, mood: str = "default"):
    return RedirectResponse(
        f"{HEARTH_HUB}/api/sanctuary/portrait/{identity}?mood={mood}",
        status_code=302,
    )


@router.get("/background/{identity}")
async def get_background(identity: str):
    return RedirectResponse(
        f"{HEARTH_HUB}/api/sanctuary/background/{identity}",
        status_code=302,
    )
