import relay  # noqa: F401
import yaml
from relay.mediamtx_config import build


def test_build_sets_rtsp_address_and_no_auth_when_creds_absent():
    out = build({"logLevel": "info"}, port=8554, username="", password="")
    assert out["rtspAddress"] == ":8554"
    assert "authInternalUsers" not in out


def test_build_adds_auth_when_both_creds_present():
    out = build({}, port=9000, username="bob", password="pw")
    users = out["authInternalUsers"]
    assert users[0]["user"] == "bob" and users[0]["pass"] == "pw"
    assert {"action": "publish"} in users[0]["permissions"]
    assert {"action": "read"} in users[0]["permissions"]
    assert users[1]["user"] == "any"
    assert users[1]["ips"] == ["127.0.0.1", "::1"]


def test_special_characters_in_creds_roundtrip_safely():
    out = build({}, port=8554, username="us'er:1", password="p@ss'w:rd")
    dumped = yaml.safe_dump(out)
    reloaded = yaml.safe_load(dumped)
    assert reloaded["authInternalUsers"][0]["user"] == "us'er:1"
    assert reloaded["authInternalUsers"][0]["pass"] == "p@ss'w:rd"
