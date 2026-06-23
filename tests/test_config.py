import relay  # noqa: F401  (path bootstrap)
from relay.config import Config, STREAM_TYPES


def test_from_env_reads_all_fields():
    env = {
        "HOST": "cam.example.tv", "HOST_PORT": "8100",
        "USERNAME": "user", "PASSWORD": "secret",
        "TARGET_HOST": "0.0.0.0", "TARGET_PORT": "8554",
        "TARGET_USERNAME": "bb", "TARGET_PASSWORD": "121124",
    }
    cfg = Config.from_env(env)
    assert cfg.host == "cam.example.tv"
    assert cfg.port == 8100
    assert cfg.username == "user"
    assert cfg.password == "secret"
    assert cfg.target_host == "0.0.0.0"
    assert cfg.target_port == 8554
    assert cfg.target_username == "bb"
    assert cfg.target_password == "121124"


def test_defaults_when_optional_missing():
    cfg = Config.from_env({"HOST": "h", "USERNAME": "u", "PASSWORD": "p"})
    assert cfg.port == 37777
    assert cfg.target_host == "0.0.0.0"
    assert cfg.target_port == 8554
    assert cfg.target_username == ""
    assert cfg.target_password == ""


def test_missing_required_raises():
    import pytest
    with pytest.raises(ValueError) as e:
        Config.from_env({"USERNAME": "u", "PASSWORD": "p"})
    assert "HOST" in str(e.value)


def test_urls_without_auth_have_no_userinfo():
    cfg = Config.from_env({"HOST": "h", "USERNAME": "u", "PASSWORD": "p",
                           "TARGET_HOST": "1.2.3.4", "TARGET_PORT": "8554"})
    assert not cfg.rtsp_auth_enabled
    assert cfg.publish_url("cam") == "rtsp://127.0.0.1:8554/cam"
    assert cfg.viewer_url("cam") == "rtsp://1.2.3.4:8554/cam"


def test_urls_with_auth_embed_credentials():
    cfg = Config.from_env({"HOST": "h", "USERNAME": "u", "PASSWORD": "p",
                           "TARGET_HOST": "1.2.3.4", "TARGET_PORT": "8554",
                           "TARGET_USERNAME": "bb", "TARGET_PASSWORD": "121124"})
    assert cfg.rtsp_auth_enabled
    assert cfg.publish_url("cam") == "rtsp://bb:121124@127.0.0.1:8554/cam"
    assert cfg.viewer_url("cam") == "rtsp://bb:121124@1.2.3.4:8554/cam"


def test_url_credentials_are_url_encoded():
    cfg = Config.from_env({"HOST": "h", "USERNAME": "u", "PASSWORD": "p",
                           "TARGET_USERNAME": "a@b", "TARGET_PASSWORD": "p:s/w@rd"})
    assert cfg.publish_url("x") == "rtsp://a%40b:p%3As%2Fw%40rd@127.0.0.1:8554/x"


def test_auth_disabled_if_only_username_set():
    cfg = Config.from_env({"HOST": "h", "USERNAME": "u", "PASSWORD": "p",
                           "TARGET_USERNAME": "bb"})
    assert not cfg.rtsp_auth_enabled


def test_stream_type_play_values():
    assert STREAM_TYPES["main"] == 0
    assert STREAM_TYPES["sub"] == 3
    assert STREAM_TYPES["sub2"] == 4
