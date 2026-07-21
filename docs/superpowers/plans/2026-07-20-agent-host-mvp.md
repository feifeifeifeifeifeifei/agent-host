# Personal Agent Host — MVP (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pluggable agent host that pushes a daily placeholder brief to Telegram and chats back, running locally first and then on AWS serverless.

**Architecture:** A thin `Host` owns shared `Services` (Telegram channel, OpenRouter LLM client, a namespaced `Store`, `Config`) and a registry of `Agent`s. Two runtime paths — a scheduled path (cron → `run_scheduled`) and a conversational path (Telegram webhook / long-poll → `handle_message`) — both dispatch into agents. MVP ships two agents: `BriefAgent` (scheduled + `/brief`, placeholder content) and `ChatAgent` (default free-text handler with memory).

**Tech Stack:** Python 3.12, `pydantic` + `pydantic-settings`, `openai` SDK (pointed at OpenRouter), `httpx`, `boto3` (DynamoDB), `pytest` + `moto` (dev). AWS Lambda + EventBridge Scheduler + Lambda Function URL + DynamoDB for deploy.

## Global Constraints

- **Python:** 3.12.
- **Runtime deps only:** `openai`, `httpx`, `pydantic>=2`, `pydantic-settings`, `boto3`. **Dev deps:** `pytest`, `moto`. Keep deps light so serverless packaging stays simple.
- **Core is runtime-agnostic:** nothing under `src/agent_host/` except `entrypoints/lambda_handler.py` may import `boto3`/AWS or any web-server framework. Channels/stores are the only place vendor SDKs appear.
- **LLM gateway:** OpenRouter, `base_url = "https://openrouter.ai/api/v1"`. Default model `deepseek/deepseek-v3.2`; fallback chain `["qwen/qwen3.6-plus", "google/gemini-2.5-flash"]`.
- **Telegram formatting:** always `parse_mode="HTML"` (never MarkdownV2). Escape untrusted item text with `html.escape` before embedding.
- **Contract:** `Agent.handle_message(...)` returns `Optional[str]`; the `Host` sends any non-`None` reply via the channel. `Agent.run_scheduled(...)` sends via `svc.channel` directly (no return value).
- **State namespacing:** every agent gets `svc.store` already namespaced to its own `name` (agents never see each other's keys).
- **Webhook auth:** verify the `x-telegram-bot-api-secret-token` header against `Config.telegram_webhook_secret` before processing an update.
- **Secrets:** never commit secrets; `.env` is git-ignored (already in `.gitignore`).
- **Commits:** conventional-commit messages; one commit per task (end of each task).

---

## File Structure

```
daily-brief-agent/
  pyproject.toml                         # deps + pytest config          (Task 1)
  .env.example                           # documented env template       (Task 1)
  src/agent_host/
    __init__.py
    config.py                            # Config (pydantic-settings)    (Task 1)
    models.py                            # InboundMessage/Turn/DigestItem (Task 2)
    store/
      __init__.py
      base.py                            # Store ABC                     (Task 3)
      sqlite_store.py                    # SqliteStore                   (Task 3)
      dynamo_store.py                    # DynamoStore                   (Task 12)
    llm.py                               # LLMClient (OpenRouter)        (Task 4)
    channels/
      __init__.py
      base.py                            # Channel ABC                   (Task 5)
      telegram.py                        # TelegramChannel               (Task 5)
    services.py                          # Services dataclass            (Task 6)
    agents/
      __init__.py
      base.py                            # Agent ABC                     (Task 6)
      brief/
        __init__.py
        sources/{__init__.py, base.py, placeholder.py}  # Source+Placeholder (Task 8)
        composer.py                      # Composer                      (Task 8)
        agent.py                         # BriefAgent                    (Task 9)
      chat/
        __init__.py
        agent.py                         # ChatAgent                     (Task 10)
    host.py                              # Host + routing                (Task 7)
    registry.py                          # build_services/agents/host    (Task 7, +Task 12)
    entrypoints/
      __init__.py
      local_run.py                       # CLI: run <agent> | serve      (Task 11)
      lambda_handler.py                  # AWS entry                     (Task 13)
  tests/                                 # one test module per component
  infra/
    aws-runbook.md                       # click-by-click AWS steps      (Task 14)
  README.md                              # overview + local run          (Task 14)
```

---

## Task 1: Project scaffold + Config

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/agent_host/__init__.py` (empty)
- Create: `src/agent_host/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` (pydantic-settings `BaseSettings`) with fields:
  `telegram_bot_token: str`, `telegram_chat_id: str`, `telegram_webhook_secret: str=""`,
  `openrouter_api_key: str`, `llm_model: str="deepseek/deepseek-v3.2"`,
  `llm_fallback_models: list[str]=["qwen/qwen3.6-plus","google/gemini-2.5-flash"]`,
  `timezone: str="Asia/Shanghai"`, `store_backend: str="sqlite"`,
  `sqlite_path: str="agent_host.sqlite"`, `dynamo_table: str="agent_host"`,
  `enabled_agents: list[str]=["brief","chat"]`, `default_agent: str="chat"`,
  `output_language: str="zh"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from agent_host.config import Config

def test_config_reads_env_and_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "a/x, b/y")  # comma-separated
    cfg = Config()
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "42"
    assert cfg.llm_model == "deepseek/deepseek-v3.2"        # default
    assert cfg.llm_fallback_models == ["a/x", "b/y"]        # split on comma
    assert cfg.enabled_agents == ["brief", "chat"]          # default list
    assert cfg.store_backend == "sqlite"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_host'` (package not installed yet) or import error.

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "agent-host"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "openai>=1.40",
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "boto3>=1.34",
]

[project.optional-dependencies]
dev = ["pytest>=8", "moto[dynamodb]>=5"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Create `.env.example`**

```bash
# Telegram (from @BotFather; chat_id from getUpdates or @userinfobot)
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=42
TELEGRAM_WEBHOOK_SECRET=choose-a-long-random-string

# OpenRouter (openrouter.ai → Keys)
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=deepseek/deepseek-v3.2
LLM_FALLBACK_MODELS=qwen/qwen3.6-plus, google/gemini-2.5-flash

# Runtime
TIMEZONE=Asia/Shanghai
STORE_BACKEND=sqlite
SQLITE_PATH=agent_host.sqlite
DYNAMO_TABLE=agent_host
ENABLED_AGENTS=brief, chat
DEFAULT_AGENT=chat
OUTPUT_LANGUAGE=zh
```

- [ ] **Step 5: Create `src/agent_host/__init__.py`** (empty file)

- [ ] **Step 6: Write `src/agent_host/config.py`**

```python
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    telegram_chat_id: str
    telegram_webhook_secret: str = ""

    openrouter_api_key: str
    llm_model: str = "deepseek/deepseek-v3.2"
    # NoDecode: stop pydantic-settings from json.loads()-ing the env value at the
    # source level, so our _split_csv before-validator receives the raw CSV string.
    llm_fallback_models: Annotated[list[str], NoDecode] = [
        "qwen/qwen3.6-plus", "google/gemini-2.5-flash"
    ]

    timezone: str = "Asia/Shanghai"
    store_backend: str = "sqlite"          # "sqlite" | "dynamo"
    sqlite_path: str = "agent_host.sqlite"
    dynamo_table: str = "agent_host"

    enabled_agents: Annotated[list[str], NoDecode] = ["brief", "chat"]
    default_agent: str = "chat"
    output_language: str = "zh"

    @field_validator("llm_fallback_models", "enabled_agents", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v
```

> Why `NoDecode`: pydantic-settings v2 treats `list[str]` as a "complex" field and
> `json.loads()`-es the raw env value **before** any validator runs, so a comma-separated
> `LLM_FALLBACK_MODELS=a/x, b/y` would raise `SettingsError`. `Annotated[list[str], NoDecode]`
> (pydantic-settings ≥ 2.2) disables that source-level decode so `_split_csv` handles the CSV.

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example src/agent_host/__init__.py src/agent_host/config.py tests/test_config.py
git commit -m "feat: project scaffold + Config loader"
```

---

## Task 2: Core data models

**Files:**
- Create: `src/agent_host/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  `InboundMessage(chat_id: str, text: str, message_id: int|None=None, raw: dict={})`,
  `ConversationTurn(role: str, content: str)`,
  `DigestItem(source: str, title: str, url: str|None=None, published_at: datetime|None=None, summary: str|None=None, category: str="general", raw: dict={})`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from agent_host.models import InboundMessage, ConversationTurn, DigestItem

def test_models_construct_with_defaults():
    m = InboundMessage(chat_id="42", text="hi")
    assert m.message_id is None and m.raw == {}
    t = ConversationTurn(role="user", content="hello")
    assert t.role == "user"
    d = DigestItem(source="placeholder", title="T")
    assert d.category == "general" and d.url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_host.models'`.

- [ ] **Step 3: Write `src/agent_host/models.py`**

```python
from datetime import datetime
from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    chat_id: str
    text: str
    message_id: int | None = None
    raw: dict = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    role: str          # "user" | "assistant" | "system"
    content: str


class DigestItem(BaseModel):
    source: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    category: str = "general"
    raw: dict = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_host/models.py tests/test_models.py
git commit -m "feat: core data models"
```

---

## Task 3: Store — base + SqliteStore (with namespacing)

**Files:**
- Create: `src/agent_host/store/__init__.py` (empty)
- Create: `src/agent_host/store/base.py`
- Create: `src/agent_host/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

**Interfaces:**
- Consumes: `ConversationTurn` (Task 2).
- Produces: `Store` ABC and `SqliteStore(path: str)` implementing:
  `namespaced(agent: str) -> Store`, `load_memory(chat_id: str) -> list[ConversationTurn]`,
  `save_memory(chat_id: str, turns: list[ConversationTurn]) -> None`,
  `get_prefs(chat_id: str) -> dict`, `set_prefs(chat_id: str, prefs: dict) -> None`,
  `seen(key: str) -> bool`, `mark_seen(keys: list[str]) -> None`, `record_run(meta: dict) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_store.py
from agent_host.store.sqlite_store import SqliteStore
from agent_host.models import ConversationTurn

def test_memory_roundtrip_and_namespacing(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite"))
    brief = store.namespaced("brief")
    chat = store.namespaced("chat")

    chat.save_memory("42", [ConversationTurn(role="user", content="hi")])
    assert [t.content for t in chat.load_memory("42")] == ["hi"]
    # namespaces are isolated
    assert brief.load_memory("42") == []

def test_prefs_and_seen(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite")).namespaced("brief")
    store.set_prefs("42", {"lang": "zh"})
    assert store.get_prefs("42") == {"lang": "zh"}
    assert store.seen("h1") is False
    store.mark_seen(["h1", "h2"])
    assert store.seen("h1") is True and store.seen("h2") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/store/base.py`**

```python
from abc import ABC, abstractmethod
from agent_host.models import ConversationTurn


class Store(ABC):
    @abstractmethod
    def namespaced(self, agent: str) -> "Store": ...
    @abstractmethod
    def load_memory(self, chat_id: str) -> list[ConversationTurn]: ...
    @abstractmethod
    def save_memory(self, chat_id: str, turns: list[ConversationTurn]) -> None: ...
    @abstractmethod
    def get_prefs(self, chat_id: str) -> dict: ...
    @abstractmethod
    def set_prefs(self, chat_id: str, prefs: dict) -> None: ...
    @abstractmethod
    def seen(self, key: str) -> bool: ...
    @abstractmethod
    def mark_seen(self, keys: list[str]) -> None: ...
    @abstractmethod
    def record_run(self, meta: dict) -> None: ...
```

- [ ] **Step 4: Write `src/agent_host/store/sqlite_store.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_sqlite_store.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/store/ tests/test_sqlite_store.py
git commit -m "feat: Store ABC + SqliteStore with per-agent namespacing"
```

---

## Task 4: LLMClient (OpenRouter)

**Files:**
- Create: `src/agent_host/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `LLMClient(api_key: str, model: str, fallback_models: list[str]|None=None, client=None, attempts: int=2, sleep=time.sleep)`
  with `complete(messages: list[dict]) -> str`. It retries each model up to `attempts` times with
  exponential backoff (via the injectable `sleep`) before falling through to the next model in the
  chain; if all models fail it raises `RuntimeError`. `client` is an injectable object exposing
  `client.chat.completions.create(model=..., messages=...)` (the OpenAI SDK shape); when
  `None`, a real OpenAI client pointed at OpenRouter is built.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/llm.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_host/llm.py tests/test_llm.py
git commit -m "feat: OpenRouter LLM client with fallback chain"
```

---

## Task 5: Channel — base + TelegramChannel

**Files:**
- Create: `src/agent_host/channels/__init__.py` (empty)
- Create: `src/agent_host/channels/base.py`
- Create: `src/agent_host/channels/telegram.py`
- Test: `tests/test_telegram_channel.py`

**Interfaces:**
- Consumes: `InboundMessage` (Task 2).
- Produces: `Channel` ABC (`send(text: str) -> None`, `parse_update(raw: dict) -> InboundMessage|None`).
  `TelegramChannel(token: str, chat_id: str, http=None, dry_run: bool=False)` implementing it,
  plus `get_updates(offset: int|None) -> list[dict]` (Telegram long-poll, Telegram-specific).
  In `dry_run`, `send` appends the HTML payload dict to `self.sent` and makes no network call.
  `http` is an injectable object exposing `.post(url, json=...)` and `.get(url, params=...)`
  returning an object with `.json()` and `.status_code` (the `httpx` shape).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram_channel.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_channel.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/channels/base.py`**

```python
from abc import ABC, abstractmethod
from agent_host.models import InboundMessage


class Channel(ABC):
    @abstractmethod
    def send(self, text: str) -> None: ...
    @abstractmethod
    def parse_update(self, raw: dict) -> InboundMessage | None: ...
```

- [ ] **Step 4: Write `src/agent_host/channels/telegram.py`**

```python
import time
from agent_host.channels.base import Channel
from agent_host.models import InboundMessage

API = "https://api.telegram.org/bot{token}/{method}"


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
            return
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_telegram_channel.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/channels/ tests/test_telegram_channel.py
git commit -m "feat: Channel ABC + TelegramChannel (HTML send, parse, long-poll)"
```

---

## Task 6: Agent base + Services

**Files:**
- Create: `src/agent_host/agents/__init__.py` (empty)
- Create: `src/agent_host/agents/base.py`
- Create: `src/agent_host/services.py`
- Test: `tests/test_agent_base.py`

**Interfaces:**
- Consumes: `Channel` (Task 5), `LLMClient` (Task 4), `Store` (Task 3), `Config` (Task 1), `InboundMessage` (Task 2).
- Produces:
  `Services` dataclass with `channel: Channel`, `llm: LLMClient`, `store: Store`, `config: Config`.
  `Agent` ABC with class attrs `name: str`, `schedule: str|None=None`, `commands: list[str]=[]`,
  `intent: str|None=None`, and methods `run_scheduled(self, svc: Services) -> None` (default no-op),
  `handle_message(self, msg: InboundMessage, svc: Services) -> str|None` (default returns None).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_base.py
from agent_host.agents.base import Agent
from agent_host.services import Services

def test_agent_defaults_and_override():
    class Echo(Agent):
        name = "echo"
        commands = ["/echo"]
        def handle_message(self, msg, svc):
            return f"echo: {msg.text}"

    a = Echo()
    assert a.name == "echo" and a.schedule is None and a.commands == ["/echo"]
    # base defaults are safe no-ops
    assert Agent.run_scheduled(a, None) is None

def test_services_is_a_container():
    svc = Services(channel="c", llm="l", store="s", config="cfg")
    assert svc.channel == "c" and svc.store == "s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_base.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/services.py`**

```python
from dataclasses import dataclass
from agent_host.channels.base import Channel
from agent_host.llm import LLMClient
from agent_host.store.base import Store
from agent_host.config import Config


@dataclass
class Services:
    channel: Channel
    llm: LLMClient
    store: Store
    config: Config
```

- [ ] **Step 4: Write `src/agent_host/agents/base.py`**

```python
from agent_host.models import InboundMessage
from agent_host.services import Services


class Agent:
    name: str = "agent"
    schedule: str | None = None       # cron expr if scheduled, else None
    commands: list[str] = []          # slash-commands this agent owns
    intent: str | None = None         # NL description for future LLM routing

    def run_scheduled(self, svc: Services) -> None:
        return None

    def handle_message(self, msg: InboundMessage, svc: Services) -> str | None:
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_agent_base.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/agents/__init__.py src/agent_host/agents/base.py src/agent_host/services.py tests/test_agent_base.py
git commit -m "feat: Agent base class + Services container"
```

---

## Task 7: Host + registry + routing

**Files:**
- Create: `src/agent_host/host.py`
- Create: `src/agent_host/registry.py`
- Test: `tests/test_host.py`

**Interfaces:**
- Consumes: `Agent` (Task 6), `Services` (Task 6), `Channel.parse_update` (Task 5), `Store.namespaced` (Task 3).
- Produces:
  `Host(agents: list[Agent], services: Services, default_agent: str)` with
  `run_scheduled(agent_name: str) -> None` and `handle_message(update: dict) -> str|None`
  (parses the update, routes, calls the agent, sends any non-None reply via the channel).
  `registry.build_services(config) -> Services`, `registry.build_agents(config) -> list[Agent]`,
  `registry.build_host(config) -> Host`, and module-level `AGENT_FACTORIES: dict[str, type[Agent]]`.
- NOTE: `build_agents`/`AGENT_FACTORIES` reference `BriefAgent` (Task 9) and `ChatAgent` (Task 10).
  Implement the registry factories using lazy imports inside `build_agents` so this task's tests
  (which construct `Host` directly with fake agents) pass before Tasks 9–10 exist.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_host.py
from agent_host.host import Host
from agent_host.services import Services
from agent_host.agents.base import Agent
from agent_host.channels.telegram import TelegramChannel

class FakeStore:
    def namespaced(self, agent): return self

def _svc():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    return Services(channel=ch, llm=None, store=FakeStore(), config=None)

def test_routes_command_to_owning_agent():
    class Brief(Agent):
        name = "brief"; commands = ["/brief"]
        def handle_message(self, msg, svc): return "BRIEF"
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"

    svc = _svc()
    host = Host([Brief(), Chat()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "text": "/brief"}})
    assert reply == "BRIEF"
    assert svc.channel.sent[-1]["text"] == "BRIEF"    # host sent the reply

def test_free_text_goes_to_default_agent():
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"
    svc = _svc()
    host = Host([Chat()], svc, default_agent="chat")
    assert host.handle_message({"message": {"chat": {"id": 42}, "text": "hello"}}) == "CHAT"

def test_run_scheduled_dispatches():
    seen = {}
    class Brief(Agent):
        name = "brief"
        def run_scheduled(self, svc): seen["ran"] = True
    host = Host([Brief()], _svc(), default_agent="brief")
    host.run_scheduled("brief")
    assert seen.get("ran") is True

def test_failing_agent_does_not_crash_host():
    class Boom(Agent):
        name = "boom"; commands = ["/boom"]
        def handle_message(self, msg, svc): raise RuntimeError("kaboom")
    host = Host([Boom()], _svc(), default_agent="boom")
    # isolated: returns None instead of propagating
    assert host.handle_message({"message": {"chat": {"id": 42}, "text": "/boom"}}) is None

def test_failing_scheduled_agent_does_not_crash_host():
    class Boom(Agent):
        name = "boom"
        def run_scheduled(self, svc): raise RuntimeError("kaboom")
    host = Host([Boom()], _svc(), default_agent="boom")
    host.run_scheduled("boom")   # does not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_host.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/host.py`**

```python
import logging
from dataclasses import replace
from agent_host.agents.base import Agent
from agent_host.services import Services

log = logging.getLogger(__name__)


class Host:
    def __init__(self, agents: list[Agent], services: Services, default_agent: str):
        self._agents = {a.name: a for a in agents}
        self._services = services
        self._default = default_agent
        self._commands = {cmd: a for a in agents for cmd in a.commands}

    def _svc_for(self, agent: Agent) -> Services:
        return replace(self._services, store=self._services.store.namespaced(agent.name))

    def run_scheduled(self, agent_name: str) -> None:
        agent = self._agents[agent_name]
        try:
            agent.run_scheduled(self._svc_for(agent))
        except Exception:  # noqa: BLE001 - a failing agent must not crash the host
            log.exception("agent %s run_scheduled failed", agent_name)

    def _route(self, text: str) -> Agent:
        if text.startswith("/"):
            cmd = text.split()[0]
            if cmd in self._commands:
                return self._commands[cmd]
        return self._agents[self._default]

    def handle_message(self, update: dict) -> str | None:
        msg = self._services.channel.parse_update(update)
        if msg is None:
            return None
        agent = self._route(msg.text)
        try:
            reply = agent.handle_message(msg, self._svc_for(agent))
        except Exception:  # noqa: BLE001 - isolate agent failures from the host
            log.exception("agent %s handle_message failed", agent.name)
            return None
        if reply:
            self._services.channel.send(reply)
        return reply
```

- [ ] **Step 4: Write `src/agent_host/registry.py`**

```python
from agent_host.config import Config
from agent_host.services import Services
from agent_host.llm import LLMClient
from agent_host.channels.telegram import TelegramChannel
from agent_host.store.base import Store
from agent_host.host import Host


def build_store(config: Config) -> Store:
    if config.store_backend == "sqlite":
        from agent_host.store.sqlite_store import SqliteStore
        return SqliteStore(config.sqlite_path)
    raise ValueError(f"unknown store_backend: {config.store_backend}")


def build_services(config: Config, dry_run: bool = False) -> Services:
    return Services(
        channel=TelegramChannel(config.telegram_bot_token, config.telegram_chat_id,
                                dry_run=dry_run),
        llm=LLMClient(config.openrouter_api_key, config.llm_model,
                      config.llm_fallback_models),
        store=build_store(config),
        config=config,
    )


def _agent_factories() -> dict:
    # lazy imports so Host tests don't require the concrete agents to exist yet
    from agent_host.agents.brief.agent import BriefAgent
    from agent_host.agents.chat.agent import ChatAgent
    return {"brief": BriefAgent, "chat": ChatAgent}


def build_agents(config: Config) -> list:
    factories = _agent_factories()
    return [factories[name]() for name in config.enabled_agents if name in factories]


def build_host(config: Config, dry_run: bool = False) -> Host:
    return Host(build_agents(config), build_services(config, dry_run=dry_run),
                default_agent=config.default_agent)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_host.py -v`
Expected: PASS (5 tests). (`registry.py` imports fine; its lazy factories aren't called by these tests.)

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/host.py src/agent_host/registry.py tests/test_host.py
git commit -m "feat: Host with command/default routing + registry wiring"
```

---

## Task 8: BriefAgent building blocks — Source + PlaceholderSource + Composer

**Files:**
- Create: `src/agent_host/agents/brief/__init__.py` (empty)
- Create: `src/agent_host/agents/brief/sources/__init__.py` (empty)
- Create: `src/agent_host/agents/brief/sources/base.py`
- Create: `src/agent_host/agents/brief/sources/placeholder.py`
- Create: `src/agent_host/agents/brief/composer.py`
- Test: `tests/test_brief_sources.py`, `tests/test_composer.py`

**Interfaces:**
- Consumes: `DigestItem` (Task 2), `LLMClient` (Task 4).
- Produces:
  `Source` ABC with class attr `name: str` and `fetch(self) -> list[DigestItem]`.
  `PlaceholderSource()` returning a fixed list of `DigestItem`s.
  `Composer(llm: LLMClient, language: str="zh")` with
  `compose(self, items: list[DigestItem], prefs: dict|None=None) -> str`. Returns an HTML
  string. When `items` is empty, returns a fixed "no news" HTML string WITHOUT calling the LLM.
  Untrusted item fields are `html.escape`-d before being placed in the prompt.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_brief_sources.py
from agent_host.agents.brief.sources.placeholder import PlaceholderSource
from agent_host.models import DigestItem

def test_placeholder_returns_digest_items():
    items = PlaceholderSource().fetch()
    assert len(items) >= 1
    assert all(isinstance(i, DigestItem) for i in items)
    assert all(i.source == "placeholder" for i in items)
```

```python
# tests/test_composer.py
from agent_host.agents.brief.composer import Composer
from agent_host.models import DigestItem

class StubLLM:
    def __init__(self, out="<b>今日要闻</b>"):
        self.out = out
        self.messages = None
    def complete(self, messages):
        self.messages = messages
        return self.out

def test_compose_returns_llm_html_for_items():
    llm = StubLLM()
    html = Composer(llm).compose([DigestItem(source="s", title="T", summary="S")])
    assert "今日要闻" in html

def test_compose_escapes_item_text_in_prompt():
    llm = StubLLM()
    Composer(llm).compose([DigestItem(source="s", title="A & B <c>")])
    prompt_text = " ".join(m["content"] for m in llm.messages)
    assert "A &amp; B &lt;c&gt;" in prompt_text
    assert "A & B <c>" not in prompt_text

def test_compose_empty_items_skips_llm():
    llm = StubLLM()
    html = Composer(llm).compose([])
    assert llm.messages is None            # LLM not called
    assert html                            # returns a non-empty "no news" message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_brief_sources.py tests/test_composer.py -v`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `src/agent_host/agents/brief/sources/base.py`**

```python
from abc import ABC, abstractmethod
from agent_host.models import DigestItem


class Source(ABC):
    name: str = "source"

    @abstractmethod
    def fetch(self) -> list[DigestItem]: ...
```

- [ ] **Step 4: Write `src/agent_host/agents/brief/sources/placeholder.py`**

```python
from agent_host.agents.brief.sources.base import Source
from agent_host.models import DigestItem


class PlaceholderSource(Source):
    name = "placeholder"

    def fetch(self) -> list[DigestItem]:
        return [
            DigestItem(source="placeholder", category="macro",
                       title="[占位] 美联储维持利率不变",
                       summary="这是一个占位新闻条目,用于验证整条推送链路。"),
            DigestItem(source="placeholder", category="ai",
                       title="[占位] 某公司发布新一代模型",
                       summary="占位 AI 要闻,后续会由真实 RSS/API 源替换。"),
            DigestItem(source="placeholder", category="market",
                       title="[占位] 主要股指小幅收涨",
                       summary="占位市场要闻。"),
        ]
```

- [ ] **Step 5: Write `src/agent_host/agents/brief/composer.py`**

```python
import html

NO_NEWS = {"zh": "今天没有新的要闻。", "en": "No new items today."}


class Composer:
    def __init__(self, llm, language: str = "zh"):
        self._llm = llm
        self._lang = language

    def compose(self, items: list, prefs: dict | None = None) -> str:
        if not items:
            return f"<b>{NO_NEWS.get(self._lang, NO_NEWS['zh'])}</b>"

        lines = []
        for i in items:
            title = html.escape(i.title)
            summary = html.escape(i.summary or "")
            lines.append(f"- [{html.escape(i.category)}] {title}: {summary}")
        data_block = "\n".join(lines)

        system = (
            "You are a concise personal news editor. Given raw items, write a short "
            "daily brief. Respond in "
            + ("Chinese" if self._lang == "zh" else "English")
            + ". Use ONLY Telegram-supported HTML tags: <b>, <i>, <a href>. "
            "Do NOT use Markdown, <ul>, <li>, or <h1>. Keep it under ~250 words."
        )
        user = f"Today's raw items:\n{data_block}\n\nWrite the brief."
        return self._llm.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}]
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_brief_sources.py tests/test_composer.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/agent_host/agents/brief/ tests/test_brief_sources.py tests/test_composer.py
git commit -m "feat: brief Source ABC + PlaceholderSource + Composer"
```

---

## Task 9: BriefAgent

**Files:**
- Create: `src/agent_host/agents/brief/agent.py`
- Test: `tests/test_brief_agent.py`

**Interfaces:**
- Consumes: `Agent` (Task 6), `Services` (Task 6), `Source`/`PlaceholderSource` + `Composer` (Task 8), `InboundMessage` (Task 2).
- Produces: `BriefAgent(sources: list[Source]|None=None)` with `name="brief"`,
  `schedule="0 8 * * *"`, `commands=["/brief"]`. `run_scheduled(svc)` gathers items
  (per-source try/except), dedups via `svc.store.seen`, composes, sends via `svc.channel`,
  marks seen, records the run. `handle_message(msg, svc)` returns an on-demand brief
  (no dedup) as a string.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_agent.py
from agent_host.agents.brief.agent import BriefAgent
from agent_host.agents.brief.sources.base import Source
from agent_host.services import Services
from agent_host.channels.telegram import TelegramChannel
from agent_host.models import DigestItem, InboundMessage

class OneItem(Source):
    name = "one"
    def fetch(self): return [DigestItem(source="one", title="T1", url="u1")]

class Boom(Source):
    name = "boom"
    def fetch(self): raise RuntimeError("feed down")

class StubLLM:
    def complete(self, messages): return "<b>BRIEF</b>"

class MemStore:
    def __init__(self): self._seen=set(); self.runs=[]
    def namespaced(self, a): return self
    def seen(self, k): return k in self._seen
    def mark_seen(self, ks): self._seen.update(ks)
    def get_prefs(self, c): return {}
    def record_run(self, meta): self.runs.append(meta)

def _svc(store):
    return Services(channel=TelegramChannel("t","42",dry_run=True),
                    llm=StubLLM(), store=store, config=type("C",(),{"output_language":"zh"}))

def test_run_scheduled_sends_and_survives_bad_source():
    store = MemStore()
    svc = _svc(store)
    BriefAgent(sources=[OneItem(), Boom()]).run_scheduled(svc)
    assert svc.channel.sent[-1]["text"] == "<b>BRIEF</b>"   # bad source didn't crash
    assert len(store.runs) == 1

def test_run_scheduled_dedups_second_run():
    store = MemStore()
    agent = BriefAgent(sources=[OneItem()])
    agent.run_scheduled(_svc(store))          # first run: item is fresh
    svc2 = _svc(store)                          # same store (seen persists)
    svc2.store = store
    agent.run_scheduled(svc2)
    # second run: only item already seen -> composer got empty list -> "no news"
    assert "没有新的要闻" in svc2.channel.sent[-1]["text"]

def test_handle_message_returns_brief_without_dedup():
    store = MemStore()
    reply = BriefAgent(sources=[OneItem()]).handle_message(
        InboundMessage(chat_id="42", text="/brief"), _svc(store))
    assert reply == "<b>BRIEF</b>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_brief_agent.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/agents/brief/agent.py`**

```python
import hashlib
from agent_host.agents.base import Agent
from agent_host.agents.brief.sources.placeholder import PlaceholderSource
from agent_host.agents.brief.composer import Composer


def _key(item) -> str:
    basis = item.url or item.title
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class BriefAgent(Agent):
    name = "brief"
    schedule = "0 8 * * *"          # 08:00 daily (timezone from Config/EventBridge)
    commands = ["/brief"]
    intent = "Produce the daily news brief."

    def __init__(self, sources=None):
        self._sources = sources if sources is not None else [PlaceholderSource()]

    def _gather(self, svc):
        items = []
        for src in self._sources:
            try:
                items.extend(src.fetch())
            except Exception:  # noqa: BLE001 - one dead source must not kill the brief
                continue
        return items

    def _build(self, svc, dedup: bool) -> str:
        items = self._gather(svc)
        if dedup:
            fresh = [i for i in items if not svc.store.seen(_key(i))]
            svc.store.mark_seen([_key(i) for i in fresh])
        else:
            fresh = items
        composer = Composer(svc.llm, getattr(svc.config, "output_language", "zh"))
        return composer.compose(fresh, svc.store.get_prefs(svc.config.telegram_chat_id)
                                if hasattr(svc.config, "telegram_chat_id") else {})

    def run_scheduled(self, svc) -> None:
        html = self._build(svc, dedup=True)
        svc.channel.send(html)
        svc.store.record_run({"agent": "brief", "chars": len(html)})

    def handle_message(self, msg, svc) -> str | None:
        return self._build(svc, dedup=False)
```

> NOTE: the test's stub `config` has no `telegram_chat_id`, so `_build` guards it with
> `hasattr`. In production `Config` always has `telegram_chat_id`, so prefs are fetched.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_brief_agent.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_host/agents/brief/agent.py tests/test_brief_agent.py
git commit -m "feat: BriefAgent (scheduled + /brief, per-source try/except, dedup)"
```

---

## Task 10: ChatAgent

**Files:**
- Create: `src/agent_host/agents/chat/__init__.py` (empty)
- Create: `src/agent_host/agents/chat/agent.py`
- Test: `tests/test_chat_agent.py`

**Interfaces:**
- Consumes: `Agent` (Task 6), `Services` (Task 6), `ConversationTurn` (Task 2), `InboundMessage` (Task 2).
- Produces: `ChatAgent(max_turns: int=12)` with `name="chat"`, no schedule, no commands.
  `handle_message(msg, svc)` loads memory, calls the LLM with a system prompt + history +
  the new message, appends both turns, trims to the last `max_turns`, saves, and returns the reply.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_agent.py
from agent_host.agents.chat.agent import ChatAgent
from agent_host.services import Services
from agent_host.models import InboundMessage, ConversationTurn

class StubLLM:
    def __init__(self): self.messages=None
    def complete(self, messages):
        self.messages = messages
        return "你好,我在。"

class MemStore:
    def __init__(self): self._m={}
    def namespaced(self, a): return self
    def load_memory(self, c): return self._m.get(c, [])
    def save_memory(self, c, turns): self._m[c] = turns

def test_reply_and_memory_persist():
    llm = StubLLM(); store = MemStore()
    svc = Services(channel=None, llm=llm, store=store,
                   config=type("C", (), {"output_language": "zh"}))
    agent = ChatAgent()
    reply = agent.handle_message(InboundMessage(chat_id="42", text="在吗"), svc)
    assert reply == "你好,我在。"
    # system prompt present + user message forwarded
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[-1] == {"role": "user", "content": "在吗"}
    # both turns saved
    saved = store.load_memory("42")
    assert [t.role for t in saved] == ["user", "assistant"]
    assert saved[1].content == "你好,我在。"

def test_history_is_replayed_and_trimmed():
    llm = StubLLM(); store = MemStore()
    store.save_memory("42", [ConversationTurn(role="user", content="prior")])
    svc = Services(channel=None, llm=llm, store=store,
                   config=type("C", (), {"output_language": "zh"}))
    ChatAgent(max_turns=2).handle_message(InboundMessage(chat_id="42", text="new"), svc)
    # prior history replayed between system and the new user message
    assert {"role": "user", "content": "prior"} in llm.messages
    # trimmed to last 2 turns
    assert len(store.load_memory("42")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_agent.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/agents/chat/agent.py`**

```python
from agent_host.agents.base import Agent
from agent_host.models import ConversationTurn

SYSTEM = {
    "zh": "你是我的个人助理,回答简洁、务实、用中文。",
    "en": "You are my personal assistant. Answer concisely and practically.",
}


class ChatAgent(Agent):
    name = "chat"
    intent = "General free-form conversation and follow-up questions."

    def __init__(self, max_turns: int = 12):
        self._max_turns = max_turns

    def handle_message(self, msg, svc) -> str | None:
        lang = getattr(svc.config, "output_language", "zh")
        history = svc.store.load_memory(msg.chat_id)
        messages = (
            [{"role": "system", "content": SYSTEM.get(lang, SYSTEM["zh"])}]
            + [{"role": t.role, "content": t.content} for t in history]
            + [{"role": "user", "content": msg.text}]
        )
        reply = svc.llm.complete(messages)
        turns = history + [
            ConversationTurn(role="user", content=msg.text),
            ConversationTurn(role="assistant", content=reply),
        ]
        svc.store.save_memory(msg.chat_id, turns[-self._max_turns:])
        return reply
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chat_agent.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_host/agents/chat/ tests/test_chat_agent.py
git commit -m "feat: ChatAgent (memory-backed free conversation)"
```

---

## Task 11: `local_run` entrypoint (CLI: run / serve)

**Files:**
- Create: `src/agent_host/entrypoints/__init__.py` (empty)
- Create: `src/agent_host/entrypoints/local_run.py`
- Test: `tests/test_local_run.py`

**Interfaces:**
- Consumes: `registry.build_host` (Task 7), `TelegramChannel.get_updates` (Task 5), `Config` (Task 1).
- Produces: `main(argv: list[str], build_host=registry.build_host, load_config=Config) -> None`
  dispatching `run <agent>` (calls `host.run_scheduled(agent)`) and `serve` (long-poll loop).
  Both `build_host` and `load_config` are injectable for testing (so tests need no real env).
  A `serve_once(host, offset, channel)` helper polls once and returns the next offset, so the
  loop is testable without running forever.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local_run.py
from agent_host.entrypoints import local_run

class FakeHost:
    def __init__(self): self.ran=[]; self.handled=[]
    def run_scheduled(self, name): self.ran.append(name)
    def handle_message(self, update): self.handled.append(update)

def test_run_subcommand_invokes_run_scheduled():
    host = FakeHost()
    local_run.main(["run", "brief"],
                   build_host=lambda cfg=None: host,
                   load_config=lambda: None)          # no real env needed
    assert host.ran == ["brief"]

def test_serve_once_handles_updates_and_advances_offset():
    host = FakeHost()
    class Ch:
        def get_updates(self, offset):
            return [{"update_id": 7, "message": {"chat": {"id": 1}, "text": "hi"}}]
    next_offset = local_run.serve_once(host, offset=None, channel=Ch())
    assert host.handled and next_offset == 8      # update_id + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_local_run.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/entrypoints/local_run.py`**

```python
import sys
from agent_host import registry
from agent_host.config import Config


def serve_once(host, offset, channel):
    updates = channel.get_updates(offset)
    new_offset = offset
    for u in updates:
        host.handle_message(u)
        new_offset = u["update_id"] + 1
    return new_offset


def main(argv=None, build_host=registry.build_host, load_config=Config):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: local_run (run <agent> | serve)")
        return
    cmd = argv[0]
    cfg = load_config()
    host = build_host(cfg)
    if cmd == "run":
        host.run_scheduled(argv[1])
    elif cmd == "serve":
        # host._services is internal; expose the channel via a small accessor
        channel = host.channel
        offset = None
        print("serving (long-poll). Ctrl-C to stop.")
        while True:
            offset = serve_once(host, offset, channel)
    else:
        print(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add a `channel` accessor to `Host`**

Modify `src/agent_host/host.py` — add this property to the `Host` class:

```python
    @property
    def channel(self):
        return self._services.channel
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_local_run.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Add the env-gated real end-to-end send test** (satisfies spec DoD §6.6 / §10)

```python
# tests/test_e2e_local.py
import os
import pytest
from agent_host.config import Config
from agent_host.registry import build_host

@pytest.mark.skipif(
    os.getenv("RUN_E2E") != "1",
    reason="set RUN_E2E=1 with a real .env to perform a live Telegram send",
)
def test_real_local_brief_send():
    host = build_host(Config())
    host.run_scheduled("brief")          # sends a real message to your Telegram
```

Verify it is skipped by default: `pytest tests/test_e2e_local.py -v` → `SKIPPED`.
To actually confirm a live send (with `.env` filled in): `RUN_E2E=1 pytest tests/test_e2e_local.py -v` → PASS **and** a placeholder brief arrives in your Telegram.

- [ ] **Step 7: Commit**

```bash
git add src/agent_host/entrypoints/__init__.py src/agent_host/entrypoints/local_run.py src/agent_host/host.py tests/test_local_run.py tests/test_e2e_local.py
git commit -m "feat: local_run CLI (run <agent> | serve long-poll) + env-gated e2e send"
```

---

## Task 12: DynamoStore + registry wiring

**Files:**
- Create: `src/agent_host/store/dynamo_store.py`
- Modify: `src/agent_host/registry.py` (add the `"dynamo"` branch in `build_store`)
- Test: `tests/test_dynamo_store.py`

**Interfaces:**
- Consumes: `Store` ABC (Task 3), `ConversationTurn` (Task 2), `Config` (Task 1).
- Produces: `DynamoStore(table_name: str, namespace: str="default", resource=None)` implementing
  the full `Store` interface against one DynamoDB table with partition key `pk` (string).
  Key layout: `pk = f"{namespace}#{kind}#{id}"` where kind ∈ {memory, prefs, seen, run}.
  `resource` is an injectable boto3 DynamoDB resource (for `moto` tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dynamo_store.py
import boto3
from moto import mock_aws
from agent_host.store.dynamo_store import DynamoStore
from agent_host.models import ConversationTurn

@mock_aws
def test_dynamo_roundtrip_and_namespacing():
    res = boto3.resource("dynamodb", region_name="us-east-1")
    res.create_table(
        TableName="t",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    store = DynamoStore("t", resource=res)
    brief = store.namespaced("brief")
    chat = store.namespaced("chat")

    chat.save_memory("42", [ConversationTurn(role="user", content="hi")])
    assert [t.content for t in chat.load_memory("42")] == ["hi"]
    assert brief.load_memory("42") == []          # namespace isolation

    brief.set_prefs("42", {"lang": "zh"})
    assert brief.get_prefs("42") == {"lang": "zh"}
    assert brief.seen("h1") is False
    brief.mark_seen(["h1"])
    assert brief.seen("h1") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dynamo_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/store/dynamo_store.py`**

```python
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
```

- [ ] **Step 4: Wire it into the registry**

Modify `build_store` in `src/agent_host/registry.py` to add the dynamo branch:

```python
def build_store(config: Config) -> Store:
    if config.store_backend == "sqlite":
        from agent_host.store.sqlite_store import SqliteStore
        return SqliteStore(config.sqlite_path)
    if config.store_backend == "dynamo":
        from agent_host.store.dynamo_store import DynamoStore
        return DynamoStore(config.dynamo_table)
    raise ValueError(f"unknown store_backend: {config.store_backend}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dynamo_store.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/store/dynamo_store.py src/agent_host/registry.py tests/test_dynamo_store.py
git commit -m "feat: DynamoStore + registry dynamo backend"
```

---

## Task 13: `lambda_handler` entrypoint

**Files:**
- Create: `src/agent_host/entrypoints/lambda_handler.py`
- Test: `tests/test_lambda_handler.py`

**Interfaces:**
- Consumes: `registry.build_host` (Task 7/12), `Config` (Task 1).
- Produces: `lambda_handler(event: dict, context=None) -> dict`. Routing:
  - Scheduled event `{"mode": "scheduled", "agent": "<name>"}` → `host.run_scheduled(agent)` → `{"statusCode": 200}`.
  - HTTP (Function URL) event → verify `x-telegram-bot-api-secret-token` header vs
    `Config.telegram_webhook_secret` (403 on mismatch), parse `event["body"]` JSON,
    call `host.handle_message(body)` → `{"statusCode": 200}`.
  `build_host` and `load_config` are injectable for testing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lambda_handler.py
import json
from agent_host.entrypoints import lambda_handler as lh

class FakeHost:
    def __init__(self): self.ran=[]; self.handled=[]
    def run_scheduled(self, name): self.ran.append(name)
    def handle_message(self, body): self.handled.append(body)

class FakeCfg:
    telegram_webhook_secret = "s3cr3t"
    store_backend = "dynamo"

def _handler_with(host):
    return lambda event, context=None: lh.lambda_handler(
        event, context, build_host=lambda cfg: host, load_config=lambda: FakeCfg())

def test_scheduled_event_runs_agent():
    host = FakeHost()
    resp = _handler_with(host)({"mode": "scheduled", "agent": "brief"})
    assert resp["statusCode"] == 200 and host.ran == ["brief"]

def test_http_event_with_good_secret_handles_message():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"},
             "body": json.dumps({"message": {"chat": {"id": 1}, "text": "hi"}})}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 200 and host.handled

def test_http_event_with_bad_secret_is_rejected():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "wrong"},
             "body": "{}"}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 403 and not host.handled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lambda_handler.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/agent_host/entrypoints/lambda_handler.py`**

```python
import json
from agent_host import registry
from agent_host.config import Config


def _default_load_config() -> Config:
    return Config()


def lambda_handler(event, context=None, build_host=registry.build_host,
                   load_config=_default_load_config):
    cfg = load_config()

    # Scheduled path (EventBridge payload)
    if event.get("mode") == "scheduled":
        host = build_host(cfg)
        host.run_scheduled(event["agent"])
        return {"statusCode": 200, "body": "ok"}

    # HTTP path (Lambda Function URL → Telegram webhook)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if cfg.telegram_webhook_secret:
        got = headers.get("x-telegram-bot-api-secret-token")
        if got != cfg.telegram_webhook_secret:
            return {"statusCode": 403, "body": "forbidden"}

    body = json.loads(event.get("body") or "{}")
    host = build_host(cfg)
    host.handle_message(body)
    return {"statusCode": 200, "body": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_lambda_handler.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the FULL suite**

Run: `pytest -v`
Expected: PASS — all tests from Tasks 1–13 green.

- [ ] **Step 6: Commit**

```bash
git add src/agent_host/entrypoints/lambda_handler.py tests/test_lambda_handler.py
git commit -m "feat: lambda_handler (scheduled + webhook routing, secret check)"
```

---

## Task 14: AWS runbook + README (deploy + docs)

**Files:**
- Create: `infra/aws-runbook.md`
- Create: `README.md`

This task has no automated tests; its deliverable is documentation accurate enough that
following it produces a working cloud deployment. Verify each command/console step exists as
written before committing.

- [ ] **Step 1: Write `infra/aws-runbook.md`**

Write a click-by-click runbook with these sections (fill each with concrete, current steps):

1. **Prerequisites** — AWS account, `aws` CLI installed + `aws configure` (access key, region
   e.g. `ap-southeast-1`), Python 3.12, the Telegram bot token + your chat_id.
2. **Create the DynamoDB table** — Console → DynamoDB → *Create table* → name `agent_host`,
   partition key `pk` (String), *Customer managed / On-demand* capacity. (Or the exact
   `aws dynamodb create-table` command with `--billing-mode PAY_PER_REQUEST`.)
3. **Create the IAM execution role** — Console → IAM → Roles → *Create role* → trusted entity
   **Lambda** → attach `AWSLambdaBasicExecutionRole` (CloudWatch logs) → add an inline policy
   granting `dynamodb:GetItem`, `PutItem`, `Query` on the `agent_host` table ARN. Name it
   `agent-host-lambda-role`. Explain what each permission is for.
4. **Package the code** — from repo root: `rm -rf build && pip install . -t build/`
   (this installs `agent_host` **and its dependencies** into `build/` with `agent_host/` at the
   top level), then `cd build && zip -r ../function.zip . && cd ..`. Lambda unzips this at the
   root and imports the handler `agent_host.entrypoints.lambda_handler.lambda_handler`, so
   `agent_host/` and the dep folders (e.g. `openai/`, `httpx/`) must sit at the zip root — verify
   with `unzip -l function.zip | head`.
5. **Create the Lambda function** — Console → Lambda → *Create function* → author from scratch,
   runtime **Python 3.12**, use role `agent-host-lambda-role`; upload `function.zip`; set the
   handler to `agent_host.entrypoints.lambda_handler.lambda_handler`; set timeout 60s, memory
   256MB. Add environment variables for every key in `.env.example` **except** switch
   `STORE_BACKEND=dynamo` and set real secrets (bot token, OpenRouter key, webhook secret).
6. **Add a Function URL (the webhook endpoint)** — Lambda → Configuration → *Function URL* →
   *Create* → Auth type **NONE** (Telegram calls it publicly; the secret header is our auth).
   Copy the URL.
7. **Register the Telegram webhook** — run:
   `curl "https://api.telegram.org/bot<TOKEN>/setWebhook" -d "url=<FUNCTION_URL>" -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"`.
   Verify with `getWebhookInfo`. Explain that Telegram now POSTs every message to the Lambda.
8. **Create the daily schedule** — Console → EventBridge → *Scheduler* → *Create schedule* →
   recurring cron `cron(0 8 * * ? *)` (minute 0, hour 8) with timezone **`Asia/Shanghai`** =
   08:00 local; target = the Lambda; set the input payload to
   `{"mode": "scheduled", "agent": "brief"}`. Explain the six EventBridge cron fields
   (min hour day-of-month month day-of-week year) and the timezone selector.
9. **Verify** — (a) send your bot a message → confirm a reply (webhook path). (b) In EventBridge,
   *Run now* or wait → confirm the brief arrives (scheduled path). (c) Read logs: CloudWatch →
   Log groups → `/aws/lambda/<function>` → latest stream; explain how to spot a Python traceback.
10. **Cost & teardown** — note free-tier coverage; how to delete the schedule, Function URL,
    Lambda, table, and role to stop all charges.

Each step must name the exact console path or the exact CLI command, and say how to confirm it
worked before moving on (this is a learning-oriented runbook).

- [ ] **Step 2: Write `README.md`**

Include: one-paragraph overview (agent host + pluggable agents), the local quickstart
(`python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`, copy `.env.example`
to `.env` and fill it, `pytest`, `python -m agent_host.entrypoints.local_run run brief`,
`python -m agent_host.entrypoints.local_run serve`), a "how to add a new agent" section
(subclass `Agent`, implement `run_scheduled` and/or `handle_message`, register it in
`registry.py` by adding the class to the dict returned by the `_agent_factories()` helper —
there is NO module-level `AGENT_FACTORIES` constant, reference the function — then add its
name to `ENABLED_AGENTS`), and a pointer to `infra/aws-runbook.md`.

- [ ] **Step 3: Commit**

```bash
git add infra/aws-runbook.md README.md
git commit -m "docs: AWS deploy runbook + README"
```

---

## Self-Review (completed by plan author + adversarial verification pass)

- **Spec coverage:** host+agents (Tasks 6–7), Telegram channel (5), OpenRouter/DeepSeek
  LLM (4), SqliteStore + DynamoStore + namespacing (3, 12), BriefAgent placeholder + Composer
  (8–9), ChatAgent memory (10), local + lambda entrypoints (11, 13), AWS deploy + README (14),
  config/secrets (1), error handling (per-source try/except in 9, **per-agent isolation in the
  Host in 7**, LLM **retry+backoff+fallback** in 4, 429 in 5, webhook secret in 13), the
  spec-DoD **real end-to-end local send** as an env-gated test (11 Step 6), testing throughout.
  Phase 2/3 items (real RSS/API sources, prefs UI, tools, WeChat mirror) are intentionally out
  of this MVP plan.
- **Placeholder scan:** no "TBD"/"implement later"; every code step has complete code; the
  runbook (Task 14) is a documentation deliverable with concrete per-step instructions.
- **Type consistency:** `Store`/`Channel`/`Agent`/`Services`/`LLMClient.complete`/
  `Composer.compose`/`DigestItem` signatures match across all tasks; `handle_message` returns
  `str|None` and the `Host` sends it everywhere; `build_host(cfg)` signature consistent in
  Tasks 7, 11, 13; `main(..., load_config=Config)` is injectable so entrypoint tests need no env.
- **Adversarial verification (3 independent reviewers) findings — all resolved:**
  1. *(blocker)* pydantic-settings JSON-decodes `list[str]` env vars before validators run →
     CSV env values crashed `Config()`. **Fixed** with `Annotated[list[str], NoDecode]` (Task 1).
  2. *(minor)* LLM retry+backoff was missing (only model fallback). **Fixed**: per-model retry
     with exponential backoff + injectable `sleep` (Task 4).
  3. *(minor)* Host lacked per-agent try/except. **Fixed**: agent calls isolated in the Host (Task 7).
  4. *(minor)* DoD "one real end-to-end local send" had no task. **Fixed**: env-gated test (Task 11).
  - Domain accuracy (Telegram/OpenRouter/AWS/moto/pydantic-settings API specifics) verified correct
    as written; no further issues found.
```
