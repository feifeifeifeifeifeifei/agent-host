import time

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    def __init__(self, api_key, model, fallback_models=None, client=None,
                 attempts=2, sleep=time.sleep):
        self._model = model
        self._fallbacks = list(fallback_models or [])
        self._attempts = attempts
        self._sleep = sleep
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
        self._client = client

    def complete(self, messages: list[dict]) -> str:
        last_exc = None
        for model in [self._model, *self._fallbacks]:
            for attempt in range(self._attempts):
                try:
                    resp = self._client.chat.completions.create(
                        model=model, messages=messages
                    )
                    return resp.choices[0].message.content
                except Exception as exc:  # noqa: BLE001 - retry, then next model
                    last_exc = exc
                    if attempt + 1 < self._attempts:
                        self._sleep(0.5 * (2 ** attempt))   # exponential backoff
        raise RuntimeError(f"all models failed; last error: {last_exc}")
