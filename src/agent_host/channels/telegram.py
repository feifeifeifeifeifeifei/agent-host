import logging
import time
from agent_host.channels.base import Channel
from agent_host.models import InboundMessage

API = "https://api.telegram.org/bot{token}/{method}"

log = logging.getLogger(__name__)


class TelegramChannel(Channel):
    def __init__(self, token, chat_id, http=None, dry_run=False):
        self._token = token
        self._chat_id = str(chat_id)
        self._dry_run = dry_run
        self.sent: list[dict] = []
        if http is None and not dry_run:
            import httpx
            http = httpx.Client(timeout=30)
        self._http = http

    def _url(self, method: str) -> str:
        return API.format(token=self._token, method=method)

    def send(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self._dry_run:
            self.sent.append(payload)
            return
        for _ in range(3):
            resp = self._http.post(self._url("sendMessage"), json=payload)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 1)
                time.sleep(retry_after)
                continue
            if not (200 <= resp.status_code < 300):
                body = getattr(resp, "text", "") or str(resp.json())
                log.warning("telegram sendMessage failed: status=%s body=%s",
                            resp.status_code, body[:200])
            return
        log.warning("telegram sendMessage gave up after repeated 429s")
        return

    def parse_update(self, raw: dict) -> InboundMessage | None:
        msg = raw.get("message")
        if not msg or "text" not in msg:
            return None
        return InboundMessage(
            chat_id=str(msg["chat"]["id"]),
            text=msg["text"],
            message_id=msg.get("message_id"),
            raw=raw,
        )

    def get_updates(self, offset: int | None) -> list[dict]:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        resp = self._http.get(self._url("getUpdates"), params=params)
        return resp.json().get("result", [])
