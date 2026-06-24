"""Generate the runtime MediaMTX config (pure build + thin CLI). No SDK imports."""
import argparse
import os

import yaml


def build(base: dict, port: int, username: str, password: str) -> dict:
    cfg = dict(base)
    cfg["rtspAddress"] = f":{port}"
    if username and password:
        cfg["authInternalUsers"] = [
            {"user": username, "pass": password, "ips": [],
             "permissions": [{"action": "publish"}, {"action": "read"}]},
            {"user": "any", "ips": ["127.0.0.1", "::1"],
             "permissions": [{"action": "api"}, {"action": "metrics"},
                             {"action": "pprof"}]},
        ]
    return cfg


def main():
    ap = argparse.ArgumentParser(prog="relay.mediamtx_config")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.base) as f:
        base = yaml.safe_load(f) or {}
    cfg = build(
        base,
        port=int(os.environ.get("TARGET_PORT") or 8554),
        username=os.environ.get("TARGET_USERNAME") or "",
        password=os.environ.get("TARGET_PASSWORD") or "",
    )
    with open(args.out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"mediamtx_config: wrote {args.out} "
          f"(auth {'enabled' if 'authInternalUsers' in cfg else 'disabled'})")


if __name__ == "__main__":
    main()
