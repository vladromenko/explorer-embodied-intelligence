"""ENGINEERING_GLUE: durable idempotent local event delivery, SQLite WAL."""
import json
import sqlite3
from pathlib import Path


class EventOutbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def append(self, event_id, payload):
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self.connect() as db:
            old = db.execute('SELECT payload FROM outbox WHERE event_id=?', (event_id,)).fetchone()
            if old and old[0] != serialized:
                raise ValueError('An event ID cannot identify different payloads')
            result = db.execute('INSERT OR IGNORE INTO outbox(event_id,payload) VALUES (?,?)', (event_id, serialized))
            return bool(result.rowcount)

    def pending(self, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT event_id,payload FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT ?', (min(max(int(limit), 1), 1000),)).fetchall()
        return [{'event_id': row[0], 'payload': json.loads(row[1])} for row in rows]

    def acknowledge(self, event_id):
        with self.connect() as db:
            db.execute('UPDATE outbox SET delivered=1 WHERE event_id=?', (event_id,))

