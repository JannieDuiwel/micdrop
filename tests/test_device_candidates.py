from micdrop.audio import DeviceInfo, device_candidates

VM = "Voicemeeter Input (VB-Audio Voicemeeter VAIO)"
VM_MME = VM[:31]  # MME truncates device names to 31 characters


def _dev(index, name, hostapi):
    return DeviceInfo(index=index, name=name, samplerate=48000, max_output_channels=2, hostapi=hostapi)


# Already host-API ranked, the way list_output_devices returns them.
DEVICES = [
    _dev(10, "Speakers (Realtek)", "Windows WASAPI"),
    _dev(11, VM, "Windows WASAPI"),
    _dev(5, "Speakers (Realtek)", "Windows DirectSound"),
    _dev(6, VM, "Windows DirectSound"),
    _dev(1, "Speakers (Realtek)", "MME"),
    _dev(2, VM_MME, "MME"),
]


def _indexes(cands):
    return [d.index for d in cands]


def test_preferred_device_then_its_other_host_apis():
    assert _indexes(device_candidates(DEVICES, [DEVICES[1]])) == [11, 6, 2]


def test_mme_truncated_name_counts_as_the_same_device():
    assert 2 in _indexes(device_candidates(DEVICES, [DEVICES[1]]))


def test_later_preferences_follow_and_duplicates_are_dropped():
    speakers = DEVICES[0]
    got = _indexes(device_candidates(DEVICES, [DEVICES[1], None, speakers, DEVICES[1]]))
    assert got == [11, 6, 2, 10, 5, 1]


def test_short_names_need_an_exact_match():
    devices = [_dev(1, "Speakers", "Windows WASAPI"), _dev(2, "Speakers (USB)", "MME")]
    assert _indexes(device_candidates(devices, [devices[0]])) == [1]


def test_nothing_preferred_gives_nothing():
    assert device_candidates(DEVICES, [None]) == []
