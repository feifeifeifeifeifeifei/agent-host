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
