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


def test_build_without_on_demand_unchanged():
    cfg = build({}, 8554, "", "")
    assert cfg == {"rtspAddress": ":8554"}
    assert "paths" not in cfg and "pathDefaults" not in cfg


def test_build_on_demand_emits_paths_and_defaults():
    cfg = build({}, 8554, "", "",
                on_demand_names=["cam3-main", "cam3-sub"],
                run_on_demand_cmd="uv run python -m relay serve $MTX_PATH")
    assert cfg["paths"] == {"cam3-main": {}, "cam3-sub": {}}
    pd = cfg["pathDefaults"]
    assert pd["runOnDemand"] == "uv run python -m relay serve $MTX_PATH"
    assert pd["runOnDemandRestart"] is True
    assert pd["runOnDemandCloseAfter"] == "10s"
    assert pd["runOnDemandStartTimeout"] == "10s"


def test_build_on_demand_empty_list_is_noop():
    cfg = build({}, 8554, "", "", on_demand_names=[])
    assert "paths" not in cfg and "pathDefaults" not in cfg


def test_build_on_demand_with_auth_keeps_auth_block():
    cfg = build({}, 8554, "user", "pass",
                on_demand_names=["cam3-main"],
                run_on_demand_cmd="cmd $MTX_PATH")
    assert "authInternalUsers" in cfg
    assert cfg["paths"] == {"cam3-main": {}}
