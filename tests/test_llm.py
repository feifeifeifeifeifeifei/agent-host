from agent_host.llm import LLMClient


class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})})]


class _FakeCompletions:
    def __init__(self, script):   # script: list of (raise_exc_or_None, text)
        self.script = script
        self.calls = []

    def create(self, model, messages, **kw):
        self.calls.append(model)
        exc, text = self.script.pop(0)
        if exc:
            raise exc
        return _Resp(text)


class _FakeClient:
    def __init__(self, script):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(script)})


def _no_sleep(_):     # avoid real backoff delay in tests
    pass


def test_complete_returns_content():
    fake = _FakeClient([(None, "hello")])
    llm = LLMClient(api_key="k", model="primary/m", client=fake, sleep=_no_sleep)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "hello"
    assert fake.chat.completions.calls == ["primary/m"]


def test_retries_same_model_before_succeeding():
    fake = _FakeClient([(RuntimeError("transient"), None), (None, "ok")])
    llm = LLMClient(api_key="k", model="primary/m", client=fake,
                    attempts=2, sleep=_no_sleep)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert fake.chat.completions.calls == ["primary/m", "primary/m"]


def test_falls_back_after_model_exhausts_attempts():
    fake = _FakeClient([(RuntimeError("boom"), None), (RuntimeError("boom"), None),
                        (None, "recovered")])
    llm = LLMClient(api_key="k", model="primary/m", fallback_models=["backup/m"],
                    client=fake, attempts=2, sleep=_no_sleep)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "recovered"
    assert fake.chat.completions.calls == ["primary/m", "primary/m", "backup/m"]


class _CaptureClient:
    """Captures the exact create() kwargs of a single vision call."""
    def __init__(self, text):
        self.captured = {}
        outer = self

        class _Comp:
            def create(self, model, messages, **kw):
                outer.captured = {"model": model, "messages": messages, "kw": kw}
                return _Resp(text)

        self.chat = type("Chat", (), {"completions": _Comp()})()


def test_complete_vision_sends_base64_data_uri_with_max_tokens():
    fake = _CaptureClient('{"candidates": ["AAPL"]}')
    llm = LLMClient(api_key="k", model="text/m", vision_model="vision/m",
                    client=fake, sleep=_no_sleep)
    out = llm.complete_vision(
        [{"role": "system", "content": "extract tickers only"}],
        b"\x89PNG\r\n\x1a\n", mime="image/png", max_tokens=128,
    )
    assert out == '{"candidates": ["AAPL"]}'
    assert fake.captured["model"] == "vision/m"          # uses the vision model
    assert fake.captured["kw"]["max_tokens"] == 128       # bounded output
    content = fake.captured["messages"][-1]["content"]    # image is the last user turn
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[0]["image_url"]["url"] != "data:image/png;base64,"   # bytes encoded


def test_complete_vision_falls_back_to_primary_model_when_unset():
    fake = _CaptureClient("{}")
    llm = LLMClient(api_key="k", model="text/m", client=fake, sleep=_no_sleep)
    llm.complete_vision([{"role": "system", "content": "x"}], b"img")
    assert fake.captured["model"] == "text/m"
