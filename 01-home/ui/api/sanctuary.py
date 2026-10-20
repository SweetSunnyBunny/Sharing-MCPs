"""REST: sanctuary — proxied to hearth-hub (Cloudflare Worker).

The viewer now talks directly to hearth-hub for state/portraits/backgrounds.
These routes redirect any remaining callers to the cloud API.
"""


from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from config import SANCTUARY_API_BASE

_DEFAULT_HEARTH_HUB = "https://hearth-hub.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev"
HEARTH_HUB = SANCTUARY_API_BASE or _DEFAULT_HEARTH_HUB

router = APIRouter(prefix="/api/sanctuary")


@router.get("/state")
async def get_state():
    return RedirectResponse(f"{HEARTH_HUB}/api/sanctuary/state", status_code=302)


@router.get("/portrait/{identity}")
async def get_portrait(identity: str, mood: str = "default"):
    # Quote both caller-supplied pieces so they can't smuggle extra query
    # params, fragments or path segments into the upstream URL.
    return RedirectResponse(
        f"{HEARTH_HUB}/api/sanctuary/portrait/{quote(identity, safe='')}"
        f"?mood={quote(mood, safe='')}",
        status_code=302,
    )


@router.get("/background/{identity}")
async def get_background(identity: str):
    return RedirectResponse(
        f"{HEARTH_HUB}/api/sanctuary/background/{quote(identity, safe='')}",
        status_code=302,
    )
