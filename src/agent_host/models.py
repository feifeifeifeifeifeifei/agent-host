from datetime import datetime
from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    chat_id: str
    text: str
    message_id: int | None = None
    photo_file_ids: list[str] = Field(default_factory=list)
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
