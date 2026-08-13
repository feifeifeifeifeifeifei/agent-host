import base64
import time

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    def __init__(self, api_key, model, fallback_models=None, client=None,
                 attempts=2, sleep=time.sleep, vision_model=None):
        self._model = model
        self._fallbacks = list(fallback_models or [])
        self._vision_model = vision_model
        self._attempts = attempts
        self._sleep = sleep
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
        self._client = client

    def complete(self, messages: list[dict], model: str | None = None) -> str:
        last_exc = None
        for m in [model or self._model, *self._fallbacks]:
            for attempt in range(self._attempts):
                try:
                    resp = self._client.chat.completions.create(
                        model=m, messages=messages
                    )
                    return resp.choices[0].message.content
                except Exception as exc:  # noqa: BLE001 - retry, then next model
                    last_exc = exc
                    if attempt + 1 < self._attempts:
                        self._sleep(0.5 * (2 ** attempt))   # exponential backoff
        raise RuntimeError(f"all models failed; last error: {last_exc}")

    def complete_vision(self, messages: list[dict], image_bytes: bytes, *,
                        mime: str = "image/png", max_tokens: int = 256) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime};base64,{b64}"
        payload = list(messages) + [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": data_uri}}],
        }]
        model = self._vision_model or self._model
        last_exc = None
        for attempt in range(self._attempts):
            try:
                resp = self._client.chat.completions.create(
                    model=model, messages=payload, max_tokens=max_tokens,
                )
                return resp.choices[0].message.content
            except Exception as exc:  # noqa: BLE001 - bounded retry on the vision model
                last_exc = exc
                if attempt + 1 < self._attempts:
                    self._sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"vision model failed; last error: {last_exc}")
