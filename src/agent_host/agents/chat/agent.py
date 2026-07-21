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
