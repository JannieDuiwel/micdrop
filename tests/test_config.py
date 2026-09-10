from micdrop.config import Config


def test_route_fields_default():
    cfg = Config.from_dict({})
    assert cfg.route_source_name == ""
    assert cfg.route_volume == 0.8


def test_route_fields_roundtrip():
    cfg = Config(route_source_name="Sonar - Media", route_volume=0.5)
    cfg2 = Config.from_dict(cfg.to_dict())
    assert cfg2.route_source_name == "Sonar - Media"
    assert cfg2.route_volume == 0.5
