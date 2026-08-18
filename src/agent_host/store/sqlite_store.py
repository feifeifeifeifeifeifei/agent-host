import json
import sqlite3

from agent_host.models import ConversationTurn
from agent_host.store.base import Store


class SqliteStore(Store):
    def __init__(self, path: str, namespace: str = "default"):
        self._path = path
        self._ns = namespace
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv "
            "(ns TEXT, kind TEXT, key TEXT, value TEXT, PRIMARY KEY (ns, kind, key))"
        )
        self._conn.commit()

    def namespaced(self, agent: str) -> "Store":
        return SqliteStore(self._path, namespace=agent)

    def _put(self, kind: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO kv (ns, kind, key, value) VALUES (?,?,?,?)",
            (self._ns, kind, key, value),
        )
        self._conn.commit()

    def _get(self, kind: str, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM kv WHERE ns=? AND kind=? AND key=?",
            (self._ns, kind, key),
        ).fetchone()
        return row[0] if row else None

    def load_memory(self, chat_id: str) -> list[ConversationTurn]:
        raw = self._get("memory", chat_id)
        if not raw:
            return []
        return [ConversationTurn(**t) for t in json.loads(raw)]

    def save_memory(self, chat_id: str, turns: list[ConversationTurn]) -> None:
        self._put("memory", chat_id, json.dumps([t.model_dump() for t in turns]))

    def get_prefs(self, chat_id: str) -> dict:
        raw = self._get("prefs", chat_id)
        return json.loads(raw) if raw else {}

    def set_prefs(self, chat_id: str, prefs: dict) -> None:
        self._put("prefs", chat_id, json.dumps(prefs))

    def seen(self, key: str) -> bool:
        return self._get("seen", key) is not None

    def mark_seen(self, keys: list[str]) -> None:
        for k in keys:
            self._put("seen", k, "1")

    def record_run(self, meta: dict) -> None:
        # append-only log keyed by an incrementing counter within the namespace
        n = self._conn.execute(
            "SELECT COUNT(*) FROM kv WHERE ns=? AND kind='run'", (self._ns,)
        ).fetchone()[0]
        self._put("run", str(n), json.dumps(meta))
