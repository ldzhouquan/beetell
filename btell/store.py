"""SQLite state and atomic chunk storage; no user-controlled filesystem paths."""
import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.chunks = self.root / "meetings"
        self.chunks.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.chunks, 0o700)
        database = self.root / "db.sqlite"
        try:
            fd = os.open(str(database), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        os.chmod(database, 0o600)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, agent TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meetings(id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'recording', created REAL NOT NULL, minutes TEXT);
            CREATE TABLE IF NOT EXISTS chunks(meeting_id TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', transcript TEXT NOT NULL DEFAULT '', PRIMARY KEY(meeting_id,seq));
            CREATE TABLE IF NOT EXISTS marks(meeting_id TEXT NOT NULL, ts REAL NOT NULL, label TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS marks_unique ON marks(meeting_id,ts,label);
            CREATE TABLE IF NOT EXISTS pairing(code_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.root / "db.sqlite"), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, agent):
        sid = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?)", (sid, agent))
        return sid

    def get_session(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT agent FROM sessions WHERE id=?", (sid,)).fetchone()
            return row[0] if row else None

    def meeting(self):
        mid = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO meetings(id,created) VALUES (?,?)", (mid, time.time()))
        return mid

    def get_meeting(self, mid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
            return dict(row) if row else None

    def list_meetings(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,state,created,minutes IS NOT NULL AS ready FROM meetings ORDER BY created DESC")]

    def put_chunk(self, mid, seq, body):
        if seq < 0 or seq > 100000 or not body or len(body) > 2_000_000:
            raise ValueError("invalid chunk")
        if not self.get_meeting(mid):
            raise KeyError(mid)
        digest = hashlib.sha256(body).hexdigest()
        folder = self.chunks / mid
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / f"{seq}.aac"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT hash,status FROM chunks WHERE meeting_id=? AND seq=?", (mid, seq)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError("conflicting chunk seq")
                return old[1] == "done"
            temp = folder / f".{seq}.{uuid.uuid4().hex}.tmp"
            with open(temp, "wb") as out:
                out.write(body)
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            db.execute("INSERT INTO chunks(meeting_id,seq,hash) VALUES (?,?,?)", (mid, seq, digest))
        return False

    def pending(self, mid):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT seq FROM chunks WHERE meeting_id=? AND status='pending' ORDER BY seq", (mid,))]

    def transcript(self, mid, seq, text, retention="off"):
        with self.connect() as db:
            db.execute("UPDATE chunks SET transcript=?,status='done' WHERE meeting_id=? AND seq=?", (text, mid, seq))
        if retention == "off":
            (self.chunks / mid / f"{seq}.aac").unlink(missing_ok=True)

    def prune_raw(self, retention):
        if retention == "keep_all":
            return
        cutoff = time.time() - 7 * 86400
        with self.connect() as db:
            done = db.execute("SELECT meeting_id,seq FROM chunks WHERE status='done'").fetchall()
        for mid, seq in done:
            path = self.chunks / mid / f"{seq}.aac"
            try:
                if retention == "off" or path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass

    def segments(self, mid):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT seq,status,transcript FROM chunks WHERE meeting_id=? ORDER BY seq", (mid,))]

    def mark(self, mid, ts, label):
        if not self.get_meeting(mid):
            raise KeyError(mid)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO marks VALUES (?,?,?)", (mid, ts, label[:120]))

    def marks(self, mid):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT ts,label FROM marks WHERE meeting_id=? ORDER BY ts", (mid,))]

    def finish(self, mid, minutes):
        with self.connect() as db:
            db.execute("UPDATE meetings SET state='ready',minutes=? WHERE id=?", (minutes, mid))

    def pair_code(self, code, ttl=300):
        with self.connect() as db:
            db.execute("DELETE FROM pairing WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO pairing VALUES (?,?)", (hashlib.sha256(code.encode()).hexdigest(), time.time() + ttl))

    def consume_code(self, code):
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT expires FROM pairing WHERE code_hash=?", (digest,)).fetchone()
            db.execute("DELETE FROM pairing WHERE code_hash=?", (digest,))
            return bool(row and row[0] >= time.time())
