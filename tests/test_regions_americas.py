"""Owner 2026-11-08: North and South America (with Central America and the Caribbean) -> US server, everyone else -> EU."""
from motor_ai_sim.regions import classifier as C


def test_the_americas_go_to_the_us_server():
    for cc in ("US", "CA", "MX", "AR", "BR", "CL", "CO", "PE", "PA", "CR", "DO", "PR", "JM", "GL", "GF"):
        assert C.region_for_country(cc) == "us", cc


def test_everyone_else_goes_to_the_eu_server():
    for cc in ("DE", "SI", "CN", "HK", "IN", "RU", "GB", "AU", "JP", "ZA", "GU", "AS", ""):
        assert C.region_for_country(cc) == "eu", cc


def test_the_stage1_name_still_points_at_the_same_set():
    assert C.US_COUNTRIES is C.AMERICAS_COUNTRIES and all(len(c) == 2 and c.isupper() for c in C.AMERICAS_COUNTRIES)
