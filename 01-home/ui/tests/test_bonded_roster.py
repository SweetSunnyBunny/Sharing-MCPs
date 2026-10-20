'Every hand-kept roster must include every bonded boy.'
from config import BONDED_PACK, IDENTITIES


def test_bonded_pack_is_every_non_mask_identity():
    assert set(BONDED_PACK) == {
        n for n, spec in IDENTITIES.items() if spec.get("type") != "character"
    }
    assert "River" in BONDED_PACK and "Atlas" in BONDED_PACK
    assert not any(IDENTITIES[n].get("type") == "character" for n in BONDED_PACK)


def test_pack_night_seats_every_brother():
    from services.session_manager import PACK_NIGHT_ORDER

    assert set(BONDED_PACK) <= set(PACK_NIGHT_ORDER)


def test_hearth_roster_shows_every_brother():
    from services.face_store import ROSTER

    assert list(ROSTER) == list(BONDED_PACK)
