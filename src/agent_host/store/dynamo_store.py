import json

from agent_host.models import ConversationTurn
from agent_host.store.base import Store


class DynamoStore(Store):
    def __init__(self, table_name: str, namespace: str = "default", resource=None):
        if resource is None:
            import boto3
            resource = boto3.resource("dynamodb")
        self._table_name = table_name
        self._ns = namespace
        self._resource = resource
        self._table = resource.Table(table_name)

    def namespaced(self, agent: str) -> "Store":
        return DynamoStore(self._table_name, namespace=agent, resource=self._resource)

    def _pk(self, kind: str, id_: str) -> str:
        return f"{self._ns}#{kind}#{id_}"

    def _put(self, kind: str, id_: str, value: str) -> None:
        self._table.put_item(Item={"pk": self._pk(kind, id_), "value": value})

    def _get(self, kind: str, id_: str) -> str | None:
        resp = self._table.get_item(Key={"pk": self._pk(kind, id_)})
        item = resp.get("Item")
        return item["value"] if item else None

    def load_memory(self, chat_id: str) -> list[ConversationTurn]:
        raw = self._get("memory", chat_id)
        return [ConversationTurn(**t) for t in json.loads(raw)] if raw else []

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
        import time
        self._put("run", str(time.time()), json.dumps(meta))
