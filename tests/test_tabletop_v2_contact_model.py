"""Contact dimensions and visual identities are part of the frozen contract."""

from copy import deepcopy

from openroboto_protocol import tabletop_v2 as v2


def test_vision_markers_must_remain_unambiguous_and_contacts_explicit():
    d = v2.example_definition()
    d["observation"] = "vision"
    for i in range(16):
        zone = deepcopy(d["zones"][0])
        zone["id"] = f"marker_{i}"
        d["zones"].append(zone)
    assert "visual_zone_identity" in {i.code for i in v2.validate_definition(d)}
    assert all(
        a["contact_dimension"] == 6 and len(a["license_sha256"]) == 64
        for a in v2.asset_catalog()
    )
    assert v2.profile_catalog()["camera"]["image_size"] == {
        "state": [320, 180],
        "vision": [1280, 720],
    }
