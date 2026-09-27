import importlib
import os
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.setdefault("ANAM_ENV", "test")
    os.environ.setdefault("ANAM_USE_DIRECT_API", "false")
    os.environ.setdefault("ANAM_PUBLIC_URL", "http://localhost:8790")
    os.environ.setdefault("SITE_URL", "http://localhost:8790")
    os.environ.setdefault("DISCORD_CLIENT_ID", "")
    os.environ.setdefault("DISCORD_CLIENT_SECRET", "")
    os.environ.setdefault("ALLOWED_DISCORD_IDS", "")

    config = importlib.import_module("config")
    server = importlib.import_module("server")

    report = config.validate_runtime_config()
    assert report["errors"] == [], report["errors"]
    assert config.USE_DIRECT_API is False
    assert getattr(server, "app", None) is not None
    print("smoke_startup_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
