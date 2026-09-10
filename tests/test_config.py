from micdrop.config import Clip, Config


def test_route_fields_default():
    cfg = Config.from_dict({})
    assert cfg.route_source_name == ""
    assert cfg.route_volume == 0.8


def test_route_fields_roundtrip():
    cfg = Config(route_source_name="Sonar - Media", route_volume=0.5)
    cfg2 = Config.from_dict(cfg.to_dict())
    assert cfg2.route_source_name == "Sonar - Media"
    assert cfg2.route_volume == 0.5


def test_trim_fields_default_to_untrimmed():
    clip = Clip.from_dict({"path": "a.wav", "label": "A"})
    assert clip.start_ms == 0
    assert clip.end_ms == 0


def test_trim_fields_roundtrip():
    cfg = Config(clips=[Clip(path="a.wav", label="A", start_ms=400, end_ms=2900)])
    cfg2 = Config.from_dict(cfg.to_dict())
    assert cfg2.clips[0].start_ms == 400
    assert cfg2.clips[0].end_ms == 2900


def test_config_written_before_trim_existed_still_loads():
    # Exactly the shape an older MicDrop wrote: no start_ms / end_ms keys.
    old = {"clips": [{"path": "a.wav", "label": "A", "hotkey": "ctrl+alt+1", "volume": 0.5}]}
    cfg = Config.from_dict(old)
    assert cfg.clips[0].volume == 0.5
    assert cfg.clips[0].start_ms == 0
    assert cfg.clips[0].end_ms == 0
