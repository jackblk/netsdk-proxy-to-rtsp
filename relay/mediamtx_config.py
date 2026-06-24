"""Generate the runtime MediaMTX config (pure build + thin CLI). No SDK imports."""
import argparse
import os
import sys

import yaml

import relay  # noqa: F401  (puts project root on sys.path)
from relay import streams_config
from relay.config import _is_truthy


def build(base: dict, port: int, username: str, password: str,
          on_demand_names=None, run_on_demand_cmd=None,
          close_after: str = "10s", start_timeout: str = "10s") -> dict:
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
    if on_demand_names:
        cfg["pathDefaults"] = {
            "runOnDemand": run_on_demand_cmd,
            "runOnDemandRestart": True,
            "runOnDemandCloseAfter": close_after,
            "runOnDemandStartTimeout": start_timeout,
        }
        cfg["paths"] = {name: {} for name in on_demand_names}
    return cfg


def _run_on_demand_cmd(streams_config_path: str) -> str:
    """The MediaMTX runOnDemand command: cwd-independent uv invocation of `relay serve`."""
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(relay.__file__)))
    cfg_abs = os.path.abspath(streams_config_path)
    return (f"uv run --frozen --directory {project_dir} "
            f"{os.path.basename(sys.executable)} -m relay serve $MTX_PATH --config {cfg_abs}")


def main():
    ap = argparse.ArgumentParser(prog="relay.mediamtx_config")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--streams-config", default="config/streams.yml")
    args = ap.parse_args()

    with open(args.base) as f:
        base = yaml.safe_load(f) or {}

    on_demand_names = None
    run_cmd = None
    if _is_truthy(os.environ.get("ON_DEMAND", "")):
        entries = streams_config.load(args.streams_config)
        on_demand_names = [e.name for e in entries if e.enable]
        run_cmd = _run_on_demand_cmd(args.streams_config)

    cfg = build(
        base,
        port=int(os.environ.get("TARGET_PORT") or 8554),
        username=os.environ.get("TARGET_USERNAME") or "",
        password=os.environ.get("TARGET_PASSWORD") or "",
        on_demand_names=on_demand_names,
        run_on_demand_cmd=run_cmd,
    )
    with open(args.out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    mode = f"on-demand ({len(on_demand_names)} path(s))" if on_demand_names else "always-on"
    print(f"mediamtx_config: wrote {args.out} "
          f"(auth {'enabled' if 'authInternalUsers' in cfg else 'disabled'}, {mode})")


if __name__ == "__main__":
    main()
