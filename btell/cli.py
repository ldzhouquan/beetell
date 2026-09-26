"""Local-only administration CLI."""
import argparse
import ipaddress
import secrets
import shutil
import subprocess

import httpx
from .api import create_app
from .asr import WhisperASR
from .config import Config, init
from .store import Store


def doctor(config):
    checks = {}
    try:
        r = httpx.get(f"http://{config.host}:{config.port}/healthz", timeout=2)
        checks["port"] = r.status_code == 200 and r.json().get("service") == "beetell"
    except (httpx.HTTPError, ValueError):
        checks["port"] = False
    checks["asr"] = WhisperASR(config.asr_model, config.ptt_model).ready()
    checks["ffmpeg"] = bool(shutil.which("ffmpeg"))
    checks["credentials"] = bool(config.openai_key or config.anthropic_key)
    try:
        version = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
        checks["codex"] = bool(config.codex_version and version.returncode == 0 and config.codex_version in version.stdout)
    except (OSError, subprocess.TimeoutExpired):
        checks["codex"] = False
    return checks


def main():
    parser = argparse.ArgumentParser(prog="btell")
    parser.add_argument("command", choices=["init", "serve", "doctor", "pair-code"])
    args = parser.parse_args()
    if args.command == "init":
        print("Created private config at", init())
        return
    config = Config.load()
    if args.command == "doctor":
        checks = doctor(config)
        for k, ok in checks.items():
            print(f"{k}: {'OK' if ok else 'NOT READY'}")
        raise SystemExit(0 if all(checks.values()) else 1)
    if args.command == "pair-code":
        code = secrets.token_urlsafe(20)
        Store(config.root).pair_code(code)
        print("One-time pairing code (expires in 5 minutes):", code)
        return
    try:
        protected = config.host in ("127.0.0.1", "localhost") or ipaddress.ip_address(config.host) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        protected = False
    if not protected:
        raise SystemExit("Refusing non-loopback/non-Tailscale bind; configure a protected network")
    import uvicorn
    uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)


if __name__ == "__main__":
    main()
