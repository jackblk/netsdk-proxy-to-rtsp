import relay  # noqa: F401  (path bootstrap)
from types import SimpleNamespace

from relay.streams_config import StreamEntry, load, merge, save


def _r(channel, stream, ok, codec=None, width=0, height=0):
    return SimpleNamespace(channel=channel, stream=stream, ok=ok,
                           codec=codec, width=width, height=height)


def test_merge_adds_new_valid_and_invalid():
    out = merge([], [
        _r(3, "main", True, "h264", 960, 576),
        _r(3, "sub", False),
    ])
    by = {(e.channel, e.stream): e for e in out}
    assert by[(3, "main")].enable is True
    assert by[(3, "main")].name == "cam3-main"
    assert by[(3, "main")].metadata == {"codec": "h264", "resolution": "960x576"}
    assert by[(3, "sub")].enable is False


def test_merge_preserves_user_edits_on_valid_stream():
    existing = [StreamEntry(channel=3, stream="main", name="front-door", enable=False)]
    out = merge(existing, [_r(3, "main", True, "h265", 2880, 1620)])
    assert out[0].name == "front-door"          # user rename kept
    assert out[0].enable is False               # user disable kept
    assert out[0].metadata == {"codec": "h265", "resolution": "2880x1620"}


def test_merge_disables_now_invalid_existing():
    existing = [StreamEntry(channel=3, stream="main", name="cam3-main", enable=True)]
    out = merge(existing, [_r(3, "main", False)])
    assert out[0].enable is False


def test_merge_leaves_unprobed_existing_untouched():
    existing = [StreamEntry(channel=9, stream="main", name="cam9-main", enable=True)]
    out = merge(existing, [_r(3, "main", True, "h264", 960, 576)])
    keep = next(e for e in out if e.channel == 9)
    assert keep.enable is True and keep.name == "cam9-main"
    assert any(e.channel == 3 for e in out)     # new one appended


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "streams.yml"
    entries = [
        StreamEntry(channel=3, stream="main", name="cam3-main", enable=True,
                    metadata={"codec": "h264", "resolution": "960x576"}),
        StreamEntry(channel=3, stream="sub", name="cam3-sub", enable=False),
    ]
    save(str(p), entries)
    loaded = load(str(p))
    assert loaded == entries


def test_load_missing_file_returns_empty(tmp_path):
    assert load(str(tmp_path / "nope.yml")) == []


def test_load_invalid_yaml_returns_empty(tmp_path):
    # Reproduces the crash report: a file that isn't valid YAML must not raise.
    p = tmp_path / "streams.yml"
    p.write_text("streams:\n  - channel: 3\nthis is : : not valid\n")
    assert load(str(p)) == []


def test_load_non_mapping_returns_empty(tmp_path):
    p = tmp_path / "streams.yml"
    p.write_text("- just\n- a\n- list\n")
    assert load(str(p)) == []


def test_save_omits_empty_metadata(tmp_path):
    p = tmp_path / "streams.yml"
    save(str(p), [StreamEntry(channel=1, stream="main", name="cam1-main")])
    text = p.read_text()
    assert "metadata" not in text
    assert "enable: true" in text
