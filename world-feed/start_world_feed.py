"""Launch World Feed using an already configured sibling Anam installation."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'ui')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--check', action='store_true', help='Check local installation paths without starting a server')
    args = parser.parse_args(argv)
    ui = args.ui_dir.expanduser().resolve()
    if not 1024 <= args.port <= 65535:
        parser.error('Choose a port between 1024 and 65535')
    python = ui / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not (ui / 'server.py').is_file() or not (ui / 'config.py').is_file():
        parser.error('Anam was not found. Keep world-feed beside ui, or use --ui-dir with your own UI folder.')
    if not python.is_file():
        parser.error('The UI Python environment is missing. Complete ui/README.md installation step 2 first.')
    print(f'World Feed: http://127.0.0.1:{args.port}/world-feed', flush=True)
    if args.check:
        print('UI files and Python environment found. This does not test provider login or runtime health.')
        return 0
    print('Using your existing Anam configuration and data. Keep this window open; Ctrl+C stops it.', flush=True)
    try:
        return subprocess.call([str(python), '-m', 'uvicorn', 'server:app',
                                '--host', '127.0.0.1', '--port', str(args.port)], cwd=ui)
    except KeyboardInterrupt:
        return 130
    except OSError:
        print('Could not start the UI Python environment. Recheck the UI installation guide.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
