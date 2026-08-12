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


class FakeBytesResponse:
    def __init__(self, content):
        self.status_code = 200
        self.content = content


class FakeGetHttp:
    """Routes getFile to a JSON response and the file URL to raw bytes."""
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if "getFile" in url:
            return FakeResponse(200, {"ok": True,
                                      "result": {"file_path": "photos/file_7.jpg"}})
        return FakeBytesResponse(b"IMAGE_BYTES")


def test_parse_update_reads_photo_largest_size_and_caption():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    msg = ch.parse_update({"update_id": 6, "message": {
        "message_id": 11, "chat": {"id": 42}, "caption": "my holdings",
        "photo": [
            {"file_id": "small", "file_size": 100},
            {"file_id": "big", "file_size": 900},
        ],
    }})
    assert msg.chat_id == "42"
    assert msg.photo_file_ids == ["big"]     # largest size selected
    assert msg.text == "my holdings"         # caption becomes text
    assert msg.message_id == 11


def test_parse_update_photo_without_caption_has_empty_text():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    msg = ch.parse_update({"update_id": 7, "message": {
        "chat": {"id": 42},
        "photo": [{"file_id": "only", "file_size": 5}],
    }})
    assert msg.photo_file_ids == ["only"] and msg.text == ""


def test_download_file_fetches_bytes_via_getfile_then_url():
    http = FakeGetHttp()
    ch = TelegramChannel(token="TKN", chat_id="42", http=http, dry_run=False)
    data = ch.download_file("big")
    assert data == b"IMAGE_BYTES"
    # first call is getFile with the file_id, second is the file URL with the token+path
    assert "getFile" in http.calls[0][0] and http.calls[0][1] == {"file_id": "big"}
    assert http.calls[1][0] == "https://api.telegram.org/file/botTKN/photos/file_7.jpg"
