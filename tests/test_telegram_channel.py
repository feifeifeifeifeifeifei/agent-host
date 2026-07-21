import logging
from agent_host.channels.telegram import TelegramChannel


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


class FakeHttp:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        return self._response


def test_send_logs_warning_on_non_2xx_non_429_response(caplog):
    http = FakeHttp(FakeResponse(400, {"description": "Bad Request"}))
    ch = TelegramChannel(token="t", chat_id="42", http=http, dry_run=False)
    with caplog.at_level(logging.WARNING):
        ch.send("hello")   # must not raise
    assert len(http.calls) == 1   # 400 != 429, no retry loop
    assert any("telegram sendmessage failed" in rec.message.lower()
               for rec in caplog.records)


def test_send_dry_run_builds_html_payload():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    ch.send("<b>hello</b>")
    assert ch.sent == [{
        "chat_id": "42",
        "text": "<b>hello</b>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }]


def test_parse_update_reads_message():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    msg = ch.parse_update(
        {"update_id": 5, "message": {"message_id": 9,
         "chat": {"id": 42}, "text": "hi there"}}
    )
    assert msg.chat_id == "42" and msg.text == "hi there" and msg.message_id == 9


def test_parse_update_ignores_non_message():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    assert ch.parse_update({"update_id": 5, "edited_channel_post": {}}) is None
