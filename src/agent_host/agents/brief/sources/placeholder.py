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
