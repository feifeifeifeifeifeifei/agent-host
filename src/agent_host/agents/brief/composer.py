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
