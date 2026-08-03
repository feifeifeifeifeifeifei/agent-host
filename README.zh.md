# agent-host(中文说明)

> English version: [README.md](README.md)。代码、命令、标识符、配置键一律保留原样,不翻译。

`agent-host` 是一个小巧、可插拔的**宿主(host)**,用来运行接入 Telegram 的 AI agent,**以 serverless 方式跑在 AWS 上**。宿主持有共享的底层设施——一个 Telegram 通道、一个基于 OpenRouter 的 LLM 客户端、一个可插拔的存储后端(本地 SQLite,云上 DynamoDB),以及消息路由——各个 **agent** 插进来干实际的活。开箱自带三个:`BriefAgent`(按计划推送每日新闻简报)、`ChatAgent`(带每会话记忆的自由对话),以及 `StockAgent`(仅命令式的、每个美股交易日收盘后的复盘简报,支持截图导入自选股)。**同一套代码**有两种跑法:本地 long-polling 进程(开发),AWS Lambda 函数(生产)。

## 分支说明

| 分支 | 是干嘛的 |
|---|---|
| `main` | 已部署的宿主 + `BriefAgent`(每日新闻简报)+ `ChatAgent`。 |
| `feat/stock-agent` | 加入 **`StockAgent`** —— 美股收盘后复盘 + 映射 OWASP 的输入校验护栏 + 截图导入自选股(**当前这个分支**)。 |

## 目录

- [分支说明](#分支说明)
- [架构总览](#架构总览)
  - [BriefAgent 与 ChatAgent 的宿主架构](#briefagent-与-chatagent-的宿主架构)
  - [StockAgent — 美股收盘后每日复盘(护栏亮点)](#stockagent--美股收盘后每日复盘护栏亮点)
- [技术栈与亮点](#技术栈与亮点)
- [本地快速开始](#本地快速开始)
  - [BriefAgent 与 ChatAgent 的用法](#briefagent-与-chatagent-的用法)
  - [StockAgent 用法](#stockagent-用法)
- [如何添加一个新 agent](#如何添加一个新-agent)
- [部署到 AWS](#部署到-aws)

## 架构总览

### BriefAgent 与 ChatAgent 的宿主架构

一个 Lambda 函数、两条入口、一张表:

- **Telegram webhook**(经由 **Lambda Function URL**)驱动实时聊天。
- 每日 **EventBridge 定时**触发**同一个** Lambda,去组装并推送新闻简报。
- **DynamoDB** 保存对话记忆和新闻去重状态——给这个无状态的函数一个持久的"记忆仓库"。

```mermaid
flowchart LR
    subgraph 触发源
      TG[你在 Telegram 发消息]
      EB[EventBridge 定时器<br/>每日 cron]
    end
    TG -->|HTTPS POST + secret 头| FU[Lambda Function URL<br/>auth: NONE]
    FU --> L[AWS Lambda: agent-host<br/>Python 3.12]
    EB -->|常量 JSON: mode=scheduled, agent=brief| L
    L <-->|GetItem / PutItem| D[(DynamoDB<br/>单表, 仅 pk)]
    L -->|prompt| OR[OpenRouter 大模型]
    L -->|sendMessage| OUT[Telegram → 你]
```

**为什么这么设计:**

- **Serverless(Lambda)。** 个人 bot 绝大多数时间是空闲的;按调用计费意味着免费额度内 ~$0/月,也没有一直开着的服务器要运维、打补丁。
- **用 Function URL 而不是 API Gateway。** 唯一的调用方是 Telegram 服务器,所以 Lambda Function URL 就是那个最小的公开 HTTPS 端点。它是 `auth: NONE`(Telegram 无法对 AWS SigV4 请求签名),因此保护落在**代码里**:一个**失败关闭的 secret 校验**(`hmac.compare_digest`;secret 未配置直接返 `403`,绝不敞开)外加一个**发件人白名单**(只服务本人的 `chat_id`)。
- **DynamoDB 单表。** Lambda 文件系统是临时的,状态必须放到外部。代码全程只对一个分区键做 `GetItem` / `PutItem`(命名空间编进 key 里,如 `brief#memory#42`)——所以 IAM 策略也就只授予这两个动作、只限这一张表。**最小权限,天然如此。**
- **调度不写在代码里。** Lambda 是被动的、不能自己唤醒自己,所以"下午 4 点跑简报"住在 EventBridge 规则里,而不是环境变量。每个需要定时的 agent 各有一条规则,于是不同 agent 可以在不同时间跑,**零代码改动**。

### StockAgent — 美股收盘后每日复盘(护栏亮点)

`StockAgent` 是插在同一个宿主上的**纯命令式** agent(没有自由聊天),每个美股交易日收盘后推送一条 Telegram 复盘简报。简报本身不复杂——真正的亮点是**自选股是怎么被安全地建起来的**。

**亮点展示:把输入校验映射到 OWASP Top 10 for LLM Applications(2025)。** 股票代码可以通过三条通道进入自选股——文本 `/add`、截图上传、以及对一个待确认导入的 `/confirm`——这三条通道最终都会走进**同一个**确定性闸门。设计的北极星是:**LLM(不管是文本还是视觉模型)只是一个不可信的、尽力而为的"提取器",绝不是权威。** 它吐出的任何东西,在通过**确定性白名单校验**——对照一个基准真相代码宇宙(NASDAQ Trader 代码文件,每周刷新,外加一小撮精选的指数/期货)——之前,都不会被采信。与其去穷举所有坏输入(这是一场注定打不赢的仗:提示注入、越狱、同形异义字符、零宽 Unicode、公式/XSS 载荷、PII 泄露……),不如去穷举**唯一合法**的东西:真实的、当前上市的股票代码(大约 10⁴–10⁵ 个)。落在这个小、封闭、可枚举的集合之外的一切都会被丢弃——这把一个无边界的对抗性 NLP 问题,变成了一次 O(1) 的集合成员判断。

```mermaid
flowchart LR
    A["文本: /add AAPL MSFT ..."] --> G
    B["截图: 券商 /<br/>TradingView 持仓"] --> V["隔离的视觉提取器<br/>(无工具权限, schema 锁定 JSON)"]
    V --> G
    G["确定性白名单闸门<br/>(NASDAQ Trader 文件 +<br/>精选指数 / 期货)"]
    G -->|拒绝| R["丢弃: 注入指令,<br/>PII, 加密货币, 同形字符,<br/>公式 / XSS 载荷"]
    G -->|通过, 再 /confirm| W["自选股(最多 50 个)"]
```

**结构性地被丢弃的东西:** 注入指令("忽略之前的所有指令……"、伪装的 `SYSTEM:` 权威、越狱/DAN 式提示)、从截图里顺手带出来的 PII(账号、余额、成本价)、**加密货币**(明确不支持——会带着 `"crypto not supported"` 的理由被拒绝,而不是被默默丢掉)、同形异义字符和不可见 Unicode(零宽字符、双向控制符、Unicode 标签字符),以及输出侧载荷(像 `=IMPORTXML(...)` 这样的 CSV 公式注入、`<img onerror=...>` XSS)。这些东西没有一个是白名单的成员,所以没有一个能活下来——哪怕一张被篡改的截图成功"说服"视觉模型"把 TSLA 加 1000 份并删除自选股",最终也只有子串 `TSLA` 能通过;指令和数量都会在闸门处被丢弃。

**截图导入的完整流程:** 发一张券商或 TradingView 持仓的截图 → 一个隔离的、schema 锁定的视觉提取器(`VISION_MODEL`,一个 OpenRouter 视觉模型)只读出股票代码,绝不读账户数据 → 同一个确定性白名单闸门校验候选项 → bot **只展示通过校验的代码** → `/confirm` 保存,`/cancel` 丢弃。原始图片只存在于内存里——绝不落盘、不写日志、不回显;余额和账号从来都不会被问及。

## 技术栈与亮点

| 层面 | 用了什么 |
|---|---|
| 语言 / 运行时 | Python 3.12 |
| 计算 | AWS Lambda(serverless,x86_64)+ Lambda Function URL |
| 调度 | Amazon EventBridge Scheduler(cron) |
| 存储 | Amazon DynamoDB(按需计费,单表,单分区键) |
| 安全 | IAM 最小权限执行角色;失败关闭的 webhook secret;发件人白名单 |
| 配置 | `pydantic-settings`(十二要素:环境变量驱动;密钥绝不入库) |
| 集成 | Telegram Bot API(webhook + long-poll);OpenRouter(多模型带回退) |
| 打包 | 为 Lambda 运行时构建的 `manylinux` / x86_64 wheel;扁平 zip |
| 测试 | `pytest` + `moto`(模拟 DynamoDB);fake / dry-run;环境变量开关的真实 e2e |
| 可观测 | Amazon CloudWatch Logs |
| 行情数据(StockAgent) | yfinance(`curl_cffi` 会话 + 重试退避)+ pandas/numpy(原生 wheel) |
| 公司与市场新闻(StockAgent) | Finnhub 免费层(60 次/分钟;key 留空 ⇒ 优雅降级到 yfinance) |
| 代码宇宙/基准真相(StockAgent) | NASDAQ Trader 代码文件(`nasdaqlisted.txt` / `otherlisted.txt`),每周刷新 |
| 截图识别(StockAgent) | OpenRouter 视觉模型(`VISION_MODEL`),无工具权限的提取器 |
| 交易日历(StockAgent) | `holidays` 包(XNYS 交易日闸门) |
| 输入校验护栏(StockAgent) | 映射到 OWASP Top 10 for LLM Applications(2025)的确定性白名单闸门 |

**体现的工程实践:** 可插拔的 agent 架构(加一个 agent ≈ 一个类 + 一行注册)、十二要素配置、最小权限 IAM、跨平台依赖打包、webhook 安全、测试驱动开发。完整的点击级部署指南见 **[`infra/aws-runbook.zh.md`](infra/aws-runbook.zh.md)**([English](infra/aws-runbook.md))。

**StockAgent 带来的:** 一套免费行情数据管线(yfinance + Finnhub)、一个用于截图识别的 OpenRouter 视觉模型,以及——亮点所在——一个把 LLM 当作不可信提取器(而非权威)的、映射到 OWASP 的确定性输入校验护栏。详见上文 [StockAgent — 美股收盘后每日复盘(护栏亮点)](#stockagent--美股收盘后每日复盘护栏亮点)。

---

## 本地快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# 然后编辑 .env 填入真实值(见下)
```

至少需要:

- **`TELEGRAM_BOT_TOKEN`** —— 在 Telegram 上找 [@BotFather](https://t.me/BotFather),运行 `/newbot`,它会给你一个 token。
- **`TELEGRAM_CHAT_ID`** —— 先给你的新 bot 发任意一条消息(bot 在你先发消息之前无法主动给你发),然后在浏览器打开 `https://api.telegram.org/bot<TOKEN>/getUpdates`,从 JSON 里读 `message.chat.id`。(或者直接找 [@userinfobot](https://t.me/userinfobot) 拿你自己的 chat ID。)
- **`OPENROUTER_API_KEY`** —— 从 https://openrouter.ai/keys 获取。默认模型是 `deepseek/deepseek-v3.2`(见 `.env.example` 里的 `LLM_MODEL`),并配置了 `qwen/qwen3.6-plus` 和 `google/gemini-2.5-flash` 作为主模型不可用时的自动回退模型。

`.env.example` 里其余项都有合理的本地默认值(`STORE_BACKEND=sqlite`、`TIMEZONE=Asia/Shanghai`、`ENABLED_AGENTS=brief, chat, stock`、`DEFAULT_AGENT=chat`、……)——开始时保持原样即可。

跑测试:

```bash
pytest
```

有一个测试(`tests/test_e2e_local.py`)默认被跳过,因为它会给你真实的 Telegram 聊天发一条**真消息**——只有当你在填好 `.env` 的前提下 export `RUN_E2E=1` 时它才会运行。

### BriefAgent 与 ChatAgent 的用法

本地试用这些 agent:

```bash
# 立刻从命令行运行一次 brief agent:
python -m agent_host.entrypoints.local_run run brief

# 或运行一个 long-poll 循环,回应真实的 Telegram 消息(Ctrl-C 停止):
python -m agent_host.entrypoints.local_run serve
```

要让 `serve` 收到消息,先打开 Telegram 给你的 bot 发一个 `/start`(或任意消息)——Telegram 只有在用户至少给 bot 发过一次消息后才会向该 bot 投递消息。发 `/brief` 可按需获取新闻简报,或者直接正常和它聊天(默认路由到 `ChatAgent`)。

### StockAgent 用法

`StockAgent` 在 `.env.example` 里默认就是开启的(`ENABLED_AGENTS=brief, chat, stock`、`IMAGE_AGENT=stock`)。可以再设置 `FINNHUB_API_KEY`(推荐——更好的新闻链接 + 同业股票传播;留空会优雅降级为只用 yfinance)以及 `VISION_MODEL` / `STOCK_MAX_TICKERS` / `STOCK_MOVER_THRESHOLD_PCT` / `STOCK_MAX_MOVERS` / `STOCK_PEER_LIMIT` / `STOCK_SCHEDULE_TZ` 这些调优参数——都写在 `.env.example` 里。

立刻从命令行运行一次:

```bash
python -m agent_host.entrypoints.local_run run stock
```

或者运行 `serve`(见上文),然后完全靠命令来操作它——`StockAgent` 是**纯命令式**的,它从不参与自由聊天:

| 命令 | 行为 |
|---|---|
| `/tickers` | 显示当前自选股(为空则提示"empty → tracking the market by default")。 |
| `/add AAPL MSFT …` | 校验并添加代码;报告哪些被添加/拒绝以及原因。 |
| `/remove AAPL …` | 从自选股里移除代码。 |
| `/reset` | 清空自选股 → 回到默认的"跟踪大盘"模式。 |
| `/help` | 列出命令并解释截图导入。 |
| *(发一张照片)* | 截图导入——展示通过校验的代码,然后 `/confirm` 或 `/cancel`。 |
| `/confirm`、`/cancel` | 接受或丢弃一个待确认的截图导入。 |

最多 50 个代码。自选股为空不是错误——那就是默认的"跟踪大盘"模式。

**每日复盘包含什么:** 四个指数(标普 500、纳斯达克、道琼斯,以及 SOX 费半)、你自选股里的显著异动(涨跌幅绝对值 top 5,且 ≥ 4%)**并附上归因**(财报、新闻,或诚实地写"没有明确催化剂"——绝不编造)、相关新闻(1-2 句话 + 链接,没有值得说的新闻的代码会被跳过),以及一个专门的财报小节。它在**美股假日或周末不会发送任何东西**(通过 `holidays` 包对照 XNYS 交易日历判断)。自选股为空时,默认改为一份追踪大盘的复盘,而不是盯着 500 家公司。

**推送时间:** 温哥华时间(`America/Vancouver`)下午 4 点,周一到周五,走它自己的 EventBridge 定时计划(payload 为 `{"mode": "scheduled", "agent": "stock"}`)——和简报是同一个钟点,但作为一条独立的消息发送。

## 如何添加一个新 agent

一个 agent 就是任意实现了 `Agent` 接口(`src/agent_host/agents/base.py`)的类:

```python
class Agent:
    name: str = "agent"
    schedule: str | None = None       # cron expr, informational — actual scheduling is external (EventBridge)
    commands: list[str] = []          # slash-commands this agent owns, e.g. ["/brief"]
    intent: str | None = None         # NL description, for future LLM-based routing

    def run_scheduled(self, svc: Services) -> None: ...
    def handle_message(self, msg: InboundMessage, svc: Services) -> str | None: ...
```

添加你自己的:

1. **继承 `Agent`**,放在 `src/agent_host/agents/` 下某处(以现有的 `agents/brief/` 或 `agents/chat/` 包布局为模板——如果你的 agent 需要多个数据源模块和一层校验逻辑,也可以参考更复杂的 `agents/stock/`)。把 `name` 设为一个简短唯一的字符串——它是查找该 agent 时到处用到的键。
2. **实现适用的入口方法:**
   - `run_scheduled(self, svc)` —— 用于定时/cron 触发的工作(例如 `BriefAgent` 每天做的事)。`svc` 是一个 `Services` 束,给你 `svc.channel`(发消息)、`svc.llm`(调用配置好的 LLM)、`svc.store`(一个 `Store`,已按你 agent 的 `name` 做好命名空间,所以你的键不会和别的 agent 冲突)、以及 `svc.config`。
   - `handle_message(self, msg, svc)` —— 当有入站 Telegram 消息路由到这个 agent 时被调用(`msg: InboundMessage` 有 `.chat_id`、`.text`、`.message_id`、`.raw`)。返回一个 `str` 让 `Host` 把它作为回复发回,或返回 `None` 表示不发。
   - 如果你想让 `Host` 把以某个 slash 命令开头的消息不管默认 agent 是谁都直接路由给你的 agent,就设 `commands = ["/yourcommand"]`(见 `src/agent_host/host.py` 里的 `Host._route`);否则你的 agent 只有在它是配置的 `DEFAULT_AGENT` 时才会收到消息。
3. **注册它**,在 `src/agent_host/registry.py` 里。这里**没有**模块级的 `AGENT_FACTORIES` 常量——注册点是 `_agent_factories()` 函数:
   ```python
   def _agent_factories() -> dict:
       from agent_host.agents.brief.agent import BriefAgent
       from agent_host.agents.chat.agent import ChatAgent
       from agent_host.agents.stock.agent import StockAgent
       from agent_host.agents.yours.agent import YourAgent   # add your import
       return {"brief": BriefAgent, "chat": ChatAgent, "stock": StockAgent,
               "yours": YourAgent}  # add your entry
   ```
   `build_agents()` 只会实例化那些名字**既**出现在这个 dict 里**又**出现在 `config.enabled_agents` 里的 agent,所以下面两步都必需。
4. **启用它**,把它的名字加进 `.env` 的 `ENABLED_AGENTS`(逗号分隔,例如 `ENABLED_AGENTS=brief, chat, stock, yours`)——这会被 `Config.enabled_agents` 读取,也是 `build_agents()` 过滤的依据。
5. 如果它应在生产中定时运行,加一条对应的 EventBridge 计划,目标为该 Lambda、输入为 `{"mode": "scheduled", "agent": "yours"}`——见 [`infra/aws-runbook.zh.md` 第 8 节](infra/aws-runbook.zh.md#8-创建每日定时任务eventbridge)。

以现有的 `test_brief_agent.py` / `test_chat_agent.py` 为模板,在 `tests/` 下写一个测试——host 和 services 都足够简单,可以直接用 fake/dry-run 模式构造,不需要 AWS 或网络访问。

## 部署到 AWS

本地 `serve`(long-polling)开发时够用,但不适合长期无人值守运行。要做一个真正常驻的部署——通过 Lambda Function URL 接 Telegram webhook,加上每日 EventBridge 定时任务推简报,存储用 DynamoDB 而非本地 SQLite 文件——请照着 **[`infra/aws-runbook.zh.md`](infra/aws-runbook.zh.md)** 里完整的点击级指南。它涵盖了准备工作、DynamoDB 表、IAM 角色、打包并上传 Lambda zip、接好 webhook、EventBridge cron 定时、端到端验证,以及成本/拆除。

`StockAgent` 已经在本地构建完成并测试通过,但**还没有部署到生产环境**。把它上线走的是和简报**同样的套路**:注册它(代码里已经做好了),确认 `ENABLED_AGENTS` 里有 `stock`;重新打包(yfinance 会带进原生的 `pandas`/`numpy`/`curl_cffi` 依赖——参见上文技术栈表格,记得提前验证 Lambda zip 的大小);加一条它自己的 EventBridge 定时计划,指向同一个 Lambda,payload 为 `{"mode": "scheduled", "agent": "stock"}`;再在 Lambda 控制台里设置它的环境变量(`FINNHUB_API_KEY`、`VISION_MODEL`,以及任何 `STOCK_*` 覆盖项)——具体步骤见 runbook。
