'Door guards for her princess page (sticker book + coloring drawer).'

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PUBLIC = ("princess", "sticker", "coloring", "colouring")


def _public_block() -> str:
    src = (ROOT / "core" / "middleware.py").read_text(encoding="utf-8")
    start = src.index("public = (")
    end = src.index("if not public", start)
    return src[start:end]


def test_public_block_is_found():
    block = _public_block()
    # Sanity: the block we parse really is the carve-out list.
    assert '"/auth/"' in block
    assert '"/api/images/file/"' in block


def test_no_princess_carve_out():
    block = _public_block().lower()
    for word in FORBIDDEN_PUBLIC:
        assert word not in block, (
            f"'{word}' appeared in the public carve-out list in core/middleware.py. "
            "Her sticker book and coloring drawer stay behind her login. "
            "Copy a finished page out on 'hang it up' instead."
        )


def _princess_modules():
    names = ("sticker", "coloring", "colouring")
    for folder in ("services", "api"):
        for path in (ROOT / folder).glob("*.py"):
            if any(n in path.name.lower() for n in names):
                yield path


def test_princess_code_never_touches_remote_hub():
    remote = re.compile(r"_remote_hub_(request|enabled)|HUB_API_BASE")
    for path in _princess_modules():
        text = path.read_text(encoding="utf-8")
        assert not remote.search(text), (
            f"{path.relative_to(ROOT)} reaches for the remote hub. "
            "Stickers and her paint stay in the local file only."
        )


def test_princess_code_never_names_the_public_images_folder():
    # Source check: no princess module reaches for config.IMAGES_DIR at all.
    for path in _princess_modules():
        text = path.read_text(encoding="utf-8")
        assert "IMAGES_DIR" not in text, (
            f"{path.relative_to(ROOT)} names IMAGES_DIR. data/images is public; "
            "her pages and paint stay under data/coloring."
        )


def test_coloring_drawer_lives_outside_data_images():
    # Resolve check: wherever the drawer actually writes, it is not inside data/images.
    import config
    from services import coloring_drawer

    images = Path(config.IMAGES_DIR).resolve()
    for d in (coloring_drawer.COLORING_DIR, coloring_drawer.PAGES_DIR):
        resolved = Path(d).resolve()
        assert resolved != images and images not in resolved.parents, (
            f"The coloring drawer writes to {resolved}, inside the public images folder."
        )
