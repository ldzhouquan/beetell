import base64
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient

from btell.api import create_app
from btell.asr import ASRError
from btell.config import Config, init
from btell.store import Store

TOKEN = "test-token-" + "x" * 40


class FakeASR:
    calls = 0
    fail = False

    def ready(self):
        return True

    async def pcm(self, pcm):
        if self.fail:
            raise ASRError()
        return "语音转写"

    async def aac(self, data):
        self.calls += 1
        if self.fail:
            raise ASRError()
        return "文字" + data.decode()


class FakeAdapter:
    capabilities = {"streaming": True, "tools": False, "memory": False, "audio_in": False, "long_task": False}
    cancelled = False

    def ready(self):
        return True

    async def send(self, text):
        yield {"type": "agent.delta", "text": "回复"}
        yield {"type": "agent.done", "text": "回复"}

    async def cancel(self):
        self.cancelled = True


class SlowAdapter(FakeAdapter):
    async def send(self, text):
        import asyncio
        await asyncio.sleep(30)
        yield {"type": "agent.done", "text": text}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Config(self.root, TOKEN)
        self.asr = FakeASR()
        self.adapter = FakeAdapter()
        self.store = Store(self.root)
        self.app = create_app(self.config, self.store, self.asr, {"llm": self.adapter, "slow": SlowAdapter()})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer " + TOKEN}

    def meeting(self):
        return self.client.post("/v1/meetings", headers=self.auth).json()["meeting_id"]

    def test_auth_capabilities_and_errors(self):
        self.assertEqual(self.client.get("/v1/agents").status_code, 401)
        self.assertEqual(self.client.get("/v1/agents", headers=self.auth).json()[0]["name"], "llm")
        session = self.client.post("/v1/sessions", json={"capabilities": ["tools", "streaming"]}, headers=self.auth).json()
        self.assertEqual(session["degraded"], ["tools"])
        self.assertTrue(session["capabilities"]["streaming"])
        self.assertEqual(self.store.get_session(session["session_id"]), "llm")
        self.assertEqual(self.client.post("/v1/sessions", json={"agent": "missing"}, headers=self.auth).json()["detail"]["code"], 4003)
        self.assertEqual(self.client.post("/v1/sessions", json={"capabilities": ["other"]}, headers=self.auth).json()["detail"]["code"], 4002)
        self.assertEqual(self.client.get("/v1/meetings/invalid/minutes", headers=self.auth).json()["detail"]["code"], 4003)

    def test_ws_version_frames_ptt_and_cancel(self):
        sid = self.client.post("/v1/sessions", headers=self.auth).json()["session_id"]
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 2, "token": TOKEN})
            self.assertEqual(ws.receive_json()["code"], 4002)
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": "wrong"})
            self.assertEqual(ws.receive_json()["code"], 4001)
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": TOKEN})
            self.assertEqual(ws.receive_json()["type"], "auth.ok")
            ws.send_json({"type": "audio.start", "session_id": sid, "fmt": "pcm16-16000-mono"})
            ws.send_json({"type": "audio.chunk", "seq": 0, "pcm_b64": base64.b64encode(b"\x00\x00").decode()})
            ws.send_json({"type": "audio.end"})
            self.assertEqual(ws.receive_json()["type"], "asr.final")
            self.assertEqual(ws.receive_json()["type"], "agent.delta")
            self.assertEqual(ws.receive_json()["type"], "agent.done")
            ws.send_json({"type": "cancel"})
            self.assertEqual(ws.receive_json()["type"], "cancel.ok")
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": TOKEN})
            ws.receive_json()
            ws.send_json({"type": "audio.start", "session_id": sid, "fmt": "pcm16-16000-mono"})
            ws.send_json({"type": "audio.chunk", "seq": 3, "pcm_b64": "AA=="})
            self.assertEqual(ws.receive_json()["code"], 4004)
            ws.send_json({"type": "audio.end"})
            self.assertEqual(ws.receive_json()["code"], 4002)
        self.assertTrue(self.adapter.cancelled)

    def test_cancel_slow_agent(self):
        sid = self.client.post("/v1/sessions", json={"agent": "slow"}, headers=self.auth).json()["session_id"]
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": TOKEN})
            ws.receive_json()
            ws.send_json({"type": "text", "session_id": sid, "text": "hello"})
            ws.send_json({"type": "cancel"})
            self.assertEqual(ws.receive_json()["type"], "cancel.ok")

    def test_cancel_while_asr_is_running(self):
        import asyncio
        from threading import Event
        started = Event()
        async def slow_pcm(pcm):
            started.set()
            await asyncio.sleep(30)
            return "late"
        self.asr.pcm = slow_pcm
        sid = self.client.post("/v1/sessions", headers=self.auth).json()["session_id"]
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": TOKEN})
            ws.receive_json()
            ws.send_json({"type": "audio.start", "session_id": sid, "fmt": "pcm16-16000-mono"})
            ws.send_json({"type": "audio.chunk", "seq": 0, "pcm_b64": "AAA="})
            ws.send_json({"type": "audio.end"})
            self.assertTrue(started.wait(2), "ASR task did not start")
            ws.send_json({"type": "cancel"})
            self.assertEqual(ws.receive_json()["type"], "cancel.ok")

    def test_incremental_replay_gap_persistence_and_minutes(self):
        mid = self.meeting()
        url = f"/v1/meetings/{mid}/chunks?seq="
        self.assertEqual(self.client.post(url + "2", data=b"two", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post(url + "0", data=b"zero", headers=self.auth).status_code, 200)
        self.assertEqual(self.asr.calls, 2)
        self.assertEqual(self.client.post(url + "2", data=b"two", headers=self.auth).status_code, 200)
        self.assertEqual(self.asr.calls, 2)
        self.assertEqual(self.client.post(url + "2", data=b"other", headers=self.auth).json()["detail"]["code"], 4004)
        self.assertEqual(self.client.post(f"/v1/meetings/{mid}/finalize", headers=self.auth).json()["detail"]["code"], 4004)
        self.assertEqual(self.client.post(url + "1", data=b"one", headers=self.auth).status_code, 200)
        self.client.post(f"/v1/meetings/{mid}/marks", json={"ts": 30, "label": "重点"}, headers=self.auth)
        self.assertEqual(self.client.post(f"/v1/meetings/{mid}/finalize", json={"expected_chunks": 4}, headers=self.auth).json()["detail"]["code"], 4004)
        self.assertEqual(self.client.post(f"/v1/meetings/{mid}/finalize", json={"expected_chunks": 3}, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post(f"/v1/meetings/{mid}/finalize", headers=self.auth).status_code, 200)
        text = self.client.get(f"/v1/meetings/{mid}/minutes", headers=self.auth).text
        self.assertIn("重点", text)
        self.assertLess(text.index("文字zero"), text.index("文字one"))
        self.assertFalse((self.root / "meetings" / mid / "0.aac").exists())
        self.assertEqual(Store(self.root).get_meeting(mid)["state"], "ready")

    def test_asr_failure_keeps_pending_for_retry(self):
        mid = self.meeting()
        self.asr.fail = True
        url = f"/v1/meetings/{mid}/chunks?seq=0"
        self.assertEqual(self.client.post(url, data=b"hello", headers=self.auth).json()["detail"]["code"], 5001)
        self.assertEqual(self.store.pending(mid), [0])
        self.asr.fail = False
        self.assertEqual(self.client.post(url, data=b"hello", headers=self.auth).status_code, 200)
        self.assertEqual(self.store.pending(mid), [])

    def test_pairing_and_redaction(self):
        self.store.pair_code("blocked", ttl=60)
        self.assertEqual(self.client.post("/v1/pair/exchange", json={"code": "blocked"}).status_code, 503)
        self.config.public_base_url = "http://100.64.1.2:8765"
        self.store.pair_code("one-time", ttl=60)
        self.assertEqual(self.client.post("/v1/pair/exchange", json={"code": "wrong"}).status_code, 403)
        response = self.client.post("/v1/pair/exchange", json={"code": "one-time"})
        self.assertIn("beetell://100.64.1.2:8765?t=", response.json()["uri"])
        self.assertIn("<svg", response.json()["qr_svg"])
        self.assertEqual(self.client.post("/v1/pair/exchange", json={"code": "one-time"}).status_code, 403)
        for route in ("/healthz", "/setup"):
            self.assertNotIn(TOKEN, self.client.get(route).text)
        self.assertNotIn(TOKEN, self.client.get("/v1/agents", headers=self.auth).text)
        self.config.public_base_url = "http://100.64.1.2.attacker.example:8765"
        self.assertEqual(self.client.post("/v1/pair/exchange", json={"code": "blocked"}).status_code, 503)

    def test_config_permissions_and_doctor_output(self):
        path = init(self.root / "new")
        self.assertEqual((self.root / "db.sqlite").stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertGreaterEqual(len(Config.load(self.root / "new").token), 32)
        os.chmod(path, 0o644)
        with self.assertRaises(ValueError):
            Config.load(self.root / "new")


    def test_seven_day_raw_retention_and_list_shape(self):
        mid = self.meeting()
        self.assertIs(self.client.get("/v1/meetings", headers=self.auth).json()[0]["ready"], False)
        self.store.put_chunk(mid, 0, b"aac")
        self.store.transcript(mid, 0, "text", "keep_raw_7d")
        path = self.root / "meetings" / mid / "0.aac"
        self.assertTrue(path.exists())
        past = time.time() - 8 * 86400
        os.utime(path, (past, past))
        self.store.prune_raw("keep_raw_7d")
        self.assertFalse(path.exists())

    def test_concurrent_conflicting_chunk_keeps_first_body(self):
        mid = self.meeting()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda body: self._put_conflict(mid, body), (b"first", b"second")))
        self.assertEqual(sorted(results), ["conflict", "saved"])
        path = self.root / "meetings" / mid / "0.aac"
        import hashlib
        with self.store.connect() as db:
            digest = db.execute("SELECT hash FROM chunks WHERE meeting_id=? AND seq=0", (mid,)).fetchone()[0]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def _put_conflict(self, mid, body):
        try:
            self.store.put_chunk(mid, 0, body)
            return "saved"
        except ValueError:
            return "conflict"

    def test_ws_mark_idempotence_and_storage_failure(self):
        mid = self.meeting()
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "auth", "v": 1, "token": TOKEN})
            ws.receive_json()
            for _ in range(2):
                ws.send_json({"type": "mark", "meeting_id": mid, "ts": 12, "label": "重点"})
                self.assertEqual(ws.receive_json()["type"], "mark.ok")
            ws.send_json({"type": "mark", "meeting_id": "absent", "ts": 0})
            self.assertEqual(ws.receive_json()["code"], 4003)
        self.assertEqual(len(self.store.marks(mid)), 1)
        with patch.object(self.store, "session", side_effect=sqlite3.OperationalError("sensitive path")):
            response = self.client.post("/v1/sessions", headers=self.auth)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], 5003)
        self.assertNotIn("sensitive path", response.text)
if __name__ == "__main__":
    unittest.main()
