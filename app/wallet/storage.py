"""Single-process demo ledger persistence, with atomic snapshots and audit outbox."""
import copy
import functools
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def atomic_write(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            before = copy.deepcopy(self._snapshot())
            depth = getattr(self, "_write_depth", 0)
            self._write_depth = depth + 1
            try:
                result = fn(self, *args, **kwargs)
                if depth == 0:
                    self._save()
                return result
            except Exception:
                self._restore(before)
                raise
            finally:
                self._write_depth = depth
    return wrapped


class LedgerStorage:
    def _snapshot(self):
        return {k: getattr(self, k) for k in ('world', 'payments', 'recent_exec_times', 'outbox', 'webhook_events')}

    def _restore(self, data):
        for k, v in data.items():
            setattr(self, k, v)

    def _load(self):
        if not self.path:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            row = db.execute('SELECT data FROM ledger WHERE id=1').fetchone()
        if row:
            data = json.loads(row[0])
            if 'payments' in data:          # a ledger file from an older version is simply ignored
                self._restore(data)

    def _save(self):
        if self.path:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('INSERT OR REPLACE INTO ledger VALUES (1, ?)',
                           (json.dumps(self._snapshot(), ensure_ascii=False),))

    def flush_audit(self, audit):
        # Posting remains successful if the audit sink is unavailable. Events stay durable for retry.
        with self._lock:
            for event in list(self.outbox):
                try:
                    audit.append(event['event'], event['session_id'], event['data'])
                except (OSError, sqlite3.Error):
                    break
                self.outbox.remove(event)
            try:
                self._save()
            except (OSError, sqlite3.Error):
                pass  # ledger already committed; a later retry may duplicate an audit event, never a payment
