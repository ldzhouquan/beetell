"""AHP v1 gateway. No credentials or raw config are returned by diagnostic routes."""
import asyncio
import base64
import binascii
import hmac
import io
import ipaddress
import sqlite3
from urllib.parse import quote, urlparse
from typing import Optional

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .adapters import AgentError, CodexAdapter, adapters as make_adapters
from .asr import ASRError, WhisperASR
from .config import Config
from .store import Store

MAX_PTT = 16000 * 2 * 60


class SessionInput(BaseModel):
    agent: str = "llm"
    capabilities: list[str] = Field(default_factory=list)


class MarkInput(BaseModel):
    ts: float = Field(ge=0)
    label: str = Field(default="标记", max_length=120)


class FinalizeInput(BaseModel):
    expected_chunks: Optional[int] = Field(default=None, ge=0)


class PairInput(BaseModel):
    code: str


def minutes_md(mid, segments, marks):
    lines = ["---", f"meeting_id: {mid}", "---", "# 会议纪要", "## 摘要", "待整理。", "## 议题（按标记切分）"]
    for mark in marks:
        lines.append(f"- {mark['ts']:.1f}s — {mark['label'].replace(chr(10), ' ')}")
    lines.extend(["## 行动项（owner / deadline / 事项）", "待整理。", "## 风险与待定", "待整理。", "## 原文锚点（时间戳 → 原文）"])
    for seg in segments:
        lines.append(f"- 片段 {seg['seq']}: {seg['transcript']}")
    return "\n".join(lines) + "\n"


def create_app(config=None, store=None, asr=None, adapter_map=None):
    config = config or Config.load()
    store = store or Store(config.root)
    store.prune_raw(config.audio_retention)
    asr = asr or WhisperASR(config.asr_model, config.ptt_model)
    adapter_map = adapter_map or make_adapters(config)
    app = FastAPI(title="BeeTell AHP v1")
    app.state.store = store

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": {"code": 5003, "message": "storage unavailable"}})

    def auth(request):
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer ") or not hmac.compare_digest(value[7:], config.token):
            raise HTTPException(401, {"code": 4001, "message": "authentication failed"})

    def meeting(mid):
        row = store.get_meeting(mid)
        if not row:
            raise HTTPException(404, {"code": 4003, "message": "unknown meeting"})
        return row

    @app.get("/healthz")
    def health():
        return {"service": "beetell", "version": 1, "asr_available": asr.ready()}

    @app.get("/v1/agents")
    def agents(request: Request):
        auth(request)
        return [{"name": k, "available": v.ready(), "capabilities": v.capabilities} for k, v in adapter_map.items()]

    @app.post("/v1/sessions")
    def create_session(request: Request, body: SessionInput = None):
        auth(request)
        body = body or SessionInput()
        adapter = adapter_map.get(body.agent)
        if not adapter:
            raise HTTPException(400, {"code": 4003, "message": "unknown agent"})
        unknown = set(body.capabilities) - set(adapter.capabilities)
        if unknown:
            raise HTTPException(400, {"code": 4002, "message": "unknown capability"})
        return {"session_id": store.session(body.agent), "agent": body.agent, "available": adapter.ready(),
                "capabilities": {k: bool(v) for k, v in adapter.capabilities.items()},
                "degraded": [k for k in body.capabilities if not adapter.capabilities[k]]}

    @app.post("/v1/meetings")
    def create_meeting(request: Request):
        auth(request)
        return {"meeting_id": store.meeting(), "state": "recording"}

    @app.get("/v1/meetings")
    def list_meetings(request: Request):
        auth(request)
        return [{**row, "ready": bool(row["ready"])} for row in store.list_meetings()]

    @app.post("/v1/meetings/{mid}/chunks")
    async def chunk(mid: str, seq: int, request: Request):
        auth(request)
        store.prune_raw(config.audio_retention)
        row = meeting(mid)
        if row["state"] != "recording":
            raise HTTPException(409, {"code": 4003, "message": "meeting finalized"})
        data = await request.body()
        try:
            already_done = store.put_chunk(mid, seq, data)
        except ValueError:
            raise HTTPException(400, {"code": 4004, "message": "invalid or conflicting chunk"})
        if not already_done:
            try:
                text = await asr.aac(data)
            except (ASRError, OSError):
                # Kept pending on disk; identical seq may be retried after backend recovery.
                raise HTTPException(503, {"code": 5001, "message": "ASR unavailable; chunk saved for retry"})
            store.transcript(mid, seq, text, config.audio_retention)
        return {"seq": seq, "state": "transcribed"}

    @app.post("/v1/meetings/{mid}/finalize")
    async def finalize(mid: str, request: Request, body: FinalizeInput = None):
        auth(request)
        row = meeting(mid)
        if row["state"] == "ready":
            return {"meeting_id": mid, "state": "ready"}
        segments = store.segments(mid)
        expected = body.expected_chunks if body else None
        if (not segments or [s["seq"] for s in segments] != list(range(len(segments)))
                or expected is not None and expected != len(segments)):
            raise HTTPException(409, {"code": 4004, "message": "missing chunk seq"})
        # Recovery after a failed incremental transcription processes only pending pieces.
        for s in segments:
            if s["status"] != "pending":
                continue
            try:
                text = await asr.aac((store.chunks / mid / f"{s['seq']}.aac").read_bytes())
            except (ASRError, OSError):
                raise HTTPException(503, {"code": 5001, "message": "ASR unavailable; retry finalize"})
            store.transcript(mid, s["seq"], text, config.audio_retention)
        store.finish(mid, minutes_md(mid, store.segments(mid), store.marks(mid)))
        return {"meeting_id": mid, "state": "ready"}

    @app.post("/v1/meetings/{mid}/marks")
    def add_mark(mid: str, body: MarkInput, request: Request):
        auth(request)
        row = meeting(mid)
        if row["state"] != "recording":
            raise HTTPException(409, {"code": 4003, "message": "meeting finalized"})
        store.mark(mid, body.ts, body.label)
        return {"state": "saved"}

    @app.get("/v1/meetings/{mid}/minutes", response_class=PlainTextResponse)
    def get_minutes(mid: str, request: Request):
        auth(request)
        row = meeting(mid)
        if row["minutes"] is None:
            raise HTTPException(404, {"code": 4003, "message": "minutes not ready"})
        return row["minutes"]

    @app.get("/setup", response_class=HTMLResponse)
    def setup():
        return """<!doctype html><meta name="viewport" content="width=device-width"><title>BeeTell pairing</title>
<h1>BeeTell 配对</h1><p>先将 server.host/public_base_url 设为 Watch 可达的 Tailscale 100.x 地址，再在网关本机运行 <code>btell pair-code</code>。</p>
<form id="pair"><input name="code" autocomplete="off" required><button>解锁二维码</button></form><div id="result"></div>
<script>document.getElementById('pair').onsubmit=async e=>{e.preventDefault();
const r=await fetch('/v1/pair/exchange',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({code:e.target.elements.namedItem('code').value})}); const d=await r.json();
document.getElementById('result').innerHTML=r.ok?d.qr_svg:'代码无效或已过期'; e.target.elements.namedItem('code').value='';};</script>"""

    @app.post("/v1/pair/exchange")
    def exchange(body: PairInput):
        base = config.public_base_url or f"http://{config.host}:{config.port}"
        parsed = urlparse(base)
        try:
            protected = ipaddress.ip_address(parsed.hostname) in ipaddress.ip_network("100.64.0.0/10")
        except (ValueError, TypeError):
            protected = False
        if (parsed.scheme != "http" or not protected or parsed.path not in ("", "/")
                or parsed.username or parsed.password or parsed.fragment or not parsed.port):
            raise HTTPException(503, {"code": 5003, "message": "configure a reachable Tailscale base URL"})
        if not store.consume_code(body.code):
            raise HTTPException(403, {"code": 4001, "message": "invalid or expired pairing code"})
        uri = f"beetell://{parsed.netloc}?t={quote(config.token)}&n=BeeTell"
        svg = io.BytesIO()
        qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(svg)
        return {"uri": uri, "qr_svg": svg.getvalue().decode()}

    @app.websocket("/v1/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        current = None
        active_adapter = None
        try:
            try:
                first = await asyncio.wait_for(ws.receive_json(), timeout=10)
            except (ValueError, asyncio.TimeoutError):
                await ws.close(code=4401)
                return
            if first.get("type") != "auth" or not hmac.compare_digest(str(first.get("token", "")), config.token):
                await ws.send_json({"type": "error", "code": 4001, "message": "authentication failed"})
                await ws.close(code=4401)
                return
            if first.get("v") != 1:
                await ws.send_json({"type": "error", "code": 4002, "message": "protocol version mismatch"})
                await ws.close(code=4402)
                return
            await ws.send_json({"type": "auth.ok", "v": 1})
            session = None
            pcm = bytearray()
            expected = 0
            async def reply(text):
                nonlocal active_adapter
                try:
                    async for event in active_adapter.send(text):
                        await ws.send_json(event)
                except asyncio.CancelledError:
                    raise
                except AgentError:
                    await ws.send_json({"type": "error", "code": 5002, "message": "agent unavailable"})

            async def transcribe_and_reply(audio):
                try:
                    text = await asr.pcm(audio)
                    await ws.send_json({"type": "asr.final", "text": text})
                    if text:
                        await reply(text)
                except asyncio.CancelledError:
                    raise
                except ASRError:
                    await ws.send_json({"type": "error", "code": 5001, "message": "ASR unavailable"})

            while True:
                frame = await ws.receive_json()
                if current and current.done():
                    current = None
                kind = frame.get("type")
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "cancel":
                    if current:
                        current.cancel()
                        await asyncio.gather(current, return_exceptions=True)
                        current = None
                    if active_adapter:
                        await active_adapter.cancel()
                    pcm.clear()
                    session = None
                    await ws.send_json({"type": "cancel.ok"})
                elif kind == "mark":
                    mid = frame.get("meeting_id", "")
                    row = store.get_meeting(mid)
                    if not row or row["state"] != "recording":
                        await ws.send_json({"type": "error", "code": 4003, "message": "unknown or finalized meeting"})
                        continue
                    try:
                        ts = float(frame.get("ts"))
                        label = str(frame.get("label", "标记"))
                        if not 0 <= ts < 864000 or len(label) > 120:
                            raise ValueError
                    except (TypeError, ValueError):
                        await ws.send_json({"type": "error", "code": 4002, "message": "invalid marker"})
                        continue
                    store.mark(mid, ts, label)
                    await ws.send_json({"type": "mark.ok", "meeting_id": mid})
                elif kind in ("text", "audio.start"):
                    sid = frame.get("session_id", "")
                    agent = store.get_session(sid)
                    if not agent:
                        await ws.send_json({"type": "error", "code": 4003, "message": "unknown session"})
                        continue
                    if current:
                        await ws.send_json({"type": "error", "code": 4003, "message": "session busy"})
                        continue
                    template = adapter_map[agent]
                    active_adapter = CodexAdapter(template.version) if isinstance(template, CodexAdapter) else template
                    if kind == "text":
                        text = str(frame.get("text", ""))[:20000]
                        if text:
                            current = asyncio.create_task(reply(text))
                    else:
                        if frame.get("fmt") != "pcm16-16000-mono":
                            await ws.send_json({"type": "error", "code": 4002, "message": "unsupported audio format"})
                            continue
                        session, expected = sid, 0
                        pcm.clear()
                elif kind == "audio.chunk" and session:
                    try:
                        data = base64.b64decode(frame.get("pcm_b64", ""), validate=True)
                        if frame.get("seq") != expected or not data or len(pcm) + len(data) > MAX_PTT:
                            raise ValueError
                        pcm.extend(data)
                        expected += 1
                    except (ValueError, binascii.Error):
                        pcm.clear()
                        session = None
                        await ws.send_json({"type": "error", "code": 4004, "message": "invalid PTT chunk"})
                elif kind == "audio.end" and session:
                    session = None
                    audio = bytes(pcm)
                    pcm.clear()
                    current = asyncio.create_task(transcribe_and_reply(audio))
                else:
                    await ws.send_json({"type": "error", "code": 4002, "message": "invalid frame"})
                if current and current.done():
                    current = None
        except WebSocketDisconnect:
            pass  # D9: unfinished PTT is discarded on disconnect.
        finally:
            if current:
                current.cancel()
                await asyncio.gather(current, return_exceptions=True)
            if active_adapter:
                await active_adapter.cancel()

    return app
