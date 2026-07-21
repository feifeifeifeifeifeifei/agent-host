from agent_host.channels.telegram import TelegramChannel


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
