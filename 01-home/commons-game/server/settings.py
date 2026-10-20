"""Configuration belongs to this installation; sibling credentials are never discovered."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)
STATE_ROOT = Path(os.environ.get('COMMONS_STATE_ROOT', str(ROOT.parent / 'commons-state'))).expanduser().resolve()
PRIVATE_ROOT = STATE_ROOT / 'private'
MASTER_ROOT = STATE_ROOT / 'master'
ASSET_ROOT = Path(os.environ.get('COMMONS_ASSET_ROOT', str(ROOT / 'starter-assets'))).expanduser().resolve()
ADMIN_NAME = os.environ.get('COMMONS_ADMIN_NAME', 'Admin').strip().lower()
if not ADMIN_NAME or len(ADMIN_NAME) > 40 or not all(c.isalnum() or c in '_-' for c in ADMIN_NAME):
    raise ValueError('COMMONS_ADMIN_NAME must be a simple name of 1 to 40 characters.')
COMPANIONS = tuple(n.strip().lower() for n in os.environ.get('COMMONS_COMPANIONS', '').split(',') if n.strip())
ANAM_URL = os.environ.get('ANAM_URL', 'http://127.0.0.1:8790').rstrip('/')
PUBLIC_URL = os.environ.get('COMMONS_PUBLIC_URL', 'http://127.0.0.1:8791/').rstrip('/') + '/'
PUBLIC_CONFIG = {
    'adminName': ADMIN_NAME,
    'avatarNames': [p.name for p in (ASSET_ROOT / 'art/anim').glob('*') if p.is_dir()],
    'cropNames': [p.stem for p in (ASSET_ROOT / 'art/crops').glob('*.png')],
    'remoteMcpUrl': os.environ.get('COMMONS_MCP_URL', ''),
    'hearthHubUrl': os.environ.get('COMMONS_HEARTH_HUB_URL', ''),
}
