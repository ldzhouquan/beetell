"""Local-only configuration; secrets never appear in API responses."""
import os
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


def _credential(value, fallback=""):
    if not value.startswith("keychain://btell/"):
        return value
    account = value.removeprefix("keychain://btell/")
    if account not in ("openai", "anthropic"):
        raise ValueError("invalid Keychain reference")
    try:
        result = subprocess.run(["security", "find-generic-password", "-s", "btell", "-a", account, "-w"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    print("warning: Keychain credential unavailable; using configured fallback if any", file=sys.stderr)
    return fallback


@dataclass
class Config:
    root: Path
    token: str
    host: str = "127.0.0.1"
    port: int = 8765
    public_base_url: str = ""
    openai_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    asr_model: str = "large-v3"
    ptt_model: str = "small"
    codex_version: str = ""
    audio_retention: str = "off"

    @classmethod
    def load(cls, root=None):
        root = Path(root or os.environ.get("BTELL_HOME", "~/.btell")).expanduser()
        path = root / "config.yaml"
        data = {}
        if path.exists():
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError("config.yaml must be readable only by its owner (chmod 600)")
            data = yaml.safe_load(path.read_text()) or {}
        server, auth, agents, asr = (data.get(k, {}) for k in ("server", "auth", "agents", "asr"))
        llm = agents.get("llm", {})
        privacy = data.get("privacy", {})
        token = os.environ.get("BTELL_TOKEN") or auth.get("btell_token", "")
        if not token or len(token) < 32:
            raise ValueError("BTELL_TOKEN (at least 32 characters) or auth.btell_token is required; run btell init")
        cfg = cls(root=root, token=token, host=server.get("host", "127.0.0.1"),
                  port=int(server.get("port", 8765)), public_base_url=server.get("public_base_url", ""),
                  openai_key=os.environ.get("BTELL_OPENAI_KEY") or _credential(llm.get("openai_key", ""), llm.get("openai_key_fallback", "")),
                  openai_model=llm.get("openai_model", "gpt-4o-mini"),
                  anthropic_key=os.environ.get("BTELL_ANTHROPIC_KEY") or _credential(llm.get("anthropic_key", ""), llm.get("anthropic_key_fallback", "")),
                  anthropic_model=llm.get("anthropic_model", "claude-3-5-haiku-latest"),
                  asr_model=asr.get("model", "large-v3"), ptt_model=asr.get("ptt_model", "small"),
                  codex_version=agents.get("codex", {}).get("version", ""),
                  audio_retention=privacy.get("audio_retention", "off"))
        if cfg.audio_retention not in ("off", "keep_raw_7d", "keep_all"):
            raise ValueError("invalid audio retention")
        return cfg


def init(root=None):
    root = Path(root or os.environ.get("BTELL_HOME", "~/.btell")).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / "config.yaml"
    if path.exists():
        raise FileExistsError("config.yaml already exists; not overwriting it")
    token = secrets.token_urlsafe(48)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump({"server": {"host": "127.0.0.1", "port": 8765},
                        "auth": {"btell_token": token}, "privacy": {"audio_retention": "off"}}, f)
    return path
