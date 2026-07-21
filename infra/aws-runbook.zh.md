# AWS 部署手册 — agent-host(中文版)

> 英文原版见 [aws-runbook.md](aws-runbook.md)。控制台按钮/字段名保留**英文原词**(加中文说明),这样无论你的 AWS 控制台是中文还是英文界面都能对上。命令、数值、JSON、handler 字符串等一律原样,不要翻译或改动。

这是一份把 `agent-host` 部署到 AWS Lambda 的**点击级**手册,消息通道用 Telegram、存储后端用 DynamoDB。默认你**没有任何 AWS 经验**——每一步都给出确切的控制台路径或命令行,要填什么/选什么,以及**怎么确认这一步成功了**再进入下一步。

首次部署总耗时:45–75 分钟。总成本:个人使用基本 $0/月(见[第 10 节](#10-成本与拆除))。

**一句话架构:** Telegram 的消息打到一个公开的 Lambda Function URL(webhook);一个 EventBridge 每日定时任务调用同一个 Lambda 推送新闻简报;Lambda 在一张 DynamoDB 表里读写对话记忆和去重状态。

---

## 目录

1. [准备工作](#1-准备工作)
2. [创建 DynamoDB 表](#2-创建-dynamodb-表)
3. [创建 IAM 执行角色](#3-创建-iam-执行角色)
4. [打包代码](#4-打包代码)
5. [创建 Lambda 函数](#5-创建-lambda-函数)
6. [添加 Function URL(webhook 端点)](#6-添加-function-urlwebhook-端点)
7. [注册 Telegram webhook](#7-注册-telegram-webhook)
8. [创建每日定时任务(EventBridge)](#8-创建每日定时任务eventbridge)
9. [端到端验证](#9-端到端验证)
10. [成本与拆除](#10-成本与拆除)

---

## 1. 准备工作

动手进控制台之前,先备齐这些:

- **一个 AWS 账号。** 没有的话到 https://aws.amazon.com/ 注册(需要信用卡,但本手册里的一切对单用户 bot 都落在 AWS 免费额度内——见[第 10 节](#10-成本与拆除))。
- **装好并配置好 AWS CLI。**
  - 安装:`brew install awscli`(macOS),或见 https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html。
  - 创建 access key:Console(控制台)→ 右上角点你的账号名 → **Security credentials**(安全凭证)→ **Access keys** → **Create access key** → 选 "Command Line Interface (CLI)" → **Create**。立刻复制 **Access key ID** 和 **Secret access key**(secret 只显示这一次)。
  - 配置 CLI:
    ```bash
    aws configure
    ```
    依次输入 access key ID、secret access key、默认区域(选离你或你的 Telegram 用户近的,例如新加坡 `ap-southeast-1` 或 `us-east-1`),输出格式填 `json`。
  - **确认成功:**
    ```bash
    aws sts get-caller-identity
    ```
    应打印出你的账号 ID 和用户 ARN 的 JSON。这里若报错,先停下解决——没有它后面全都跑不通。
- **本地装好 Python 3.12**(`python3 --version`)。打包 Lambda zip 时需要。
- **一个 Telegram bot token 和你的 chat ID。** 还没弄的话,见 [README](../README.md) 的 "Local quickstart" 一节——在 Telegram 上找 `@BotFather` 创建 bot 拿到 `TELEGRAM_BOT_TOKEN`,然后给你的 bot 发一条消息、用 `getUpdates` 读出你的 `TELEGRAM_CHAT_ID`。
- **一个 OpenRouter API key**,在 https://openrouter.ai/keys 生成(LLM 客户端要用)。

下文里 "Console → X → Y" 的意思是:打开 https://console.aws.amazon.com/,确认右上角的区域选择器与你上面配置的区域一致,然后用搜索栏或左侧导航进入 **X**,再点 **Y**。

---

## 2. 创建 DynamoDB 表

DynamoDB 存放代码持久化的一切:对话记忆(`ChatAgent`)、已读条目去重键和运行元数据(`BriefAgent`)。代码只用一个分区键 `pk` 读写——不需要二级索引。

**控制台路径:**

1. Console → 搜索 "DynamoDB" → **DynamoDB** → **Create table**(创建表)。
2. **Table name(表名):** `agent_host`
3. **Partition key(分区键):** `pk`,类型 **String**。**Sort key(排序键)留空**——代码把自己的命名空间编进 `pk` 的值里(例如 `brief#memory#42`),不需要排序键。
4. **Table settings(表设置):** 选 **Customize settings**(自定义设置)→ 在 "Read/write capacity settings" 下选 **On-demand**(按需)。(按需意味着按请求付费、无需预置或调容量——低流量个人 bot 的正确选择,也是免费额度覆盖的。)
5. 加密/其他设置保持默认 → **Create table**。
6. **确认成功:** 等约 30–60 秒,刷新,表的 **Status(状态)** 列应显示 **Active**。

**等价的 CLI 命令**(用它就可跳过上面的控制台步骤):

```bash
aws dynamodb create-table \
  --table-name agent_host \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

**确认成功:**

```bash
aws dynamodb describe-table --table-name agent_host --query "Table.TableStatus"
```

应打印 `"ACTIVE"`(刚运行完命令时可能短暂显示 `"CREATING"`——等几秒重跑)。

---

## 3. 创建 IAM 执行角色

每个 Lambda 函数都"以"某个 IAM 角色的身份运行——一组权限,规定函数被允许碰哪些东西。我们的函数需要两样:

- **把自己的日志写到 CloudWatch 的权限**(方便你以后调试)。AWS 正好有一个现成的托管策略:`AWSLambdaBasicExecutionRole`。
- **读写一张 DynamoDB 表的权限。** 代码(`src/agent_host/store/dynamo_store.py` 里的 `DynamoStore`)只调用 `get_item` 和 `put_item`——从不 scan、query、delete——所以内联策略只授予 `dynamodb:GetItem` 和 `dynamodb:PutItem`,并限定到这一张表的 ARN。这就是最小权限原则:万一 Lambda 的凭证泄露,影响面只是"能读写这一张表",而不是"能在这个 AWS 账号里为所欲为"。

**控制台路径:**

1. Console → 搜索 "IAM" → **IAM** → 左侧 **Roles(角色)** → **Create role**(创建角色)。
2. **Trusted entity type(信任实体类型):** **AWS service**。**Use case(用例):** **Lambda**。点 **Next**。
3. **Add permissions(添加权限):** 搜索框里输入 `AWSLambdaBasicExecutionRole` 并勾选它。点 **Next**。
4. **Role name(角色名):** `agent-host-lambda-role`。(可选)描述:"Execution role for the agent-host Lambda function."。点 **Create role**。
5. **确认成功:** 回到角色列表,搜索 `agent-host-lambda-role`,确认它出现且权限里列着 `AWSLambdaBasicExecutionRole`。
6. 现在加 DynamoDB 权限。点进该角色 → 右上 **Add permissions** → **Create inline policy**(创建内联策略)。
7. 把策略编辑器切到 **JSON** 标签,粘贴以下内容,并把 `<ACCOUNT_ID>` 和 `<REGION>` 换成你自己的(账号 ID 用 `aws sts get-caller-identity --query Account` 查,区域就是你 `aws configure` 设的那个):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "AgentHostTableAccess",
         "Effect": "Allow",
         "Action": [
           "dynamodb:GetItem",
           "dynamodb:PutItem"
         ],
         "Resource": "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/agent_host"
       }
     ]
   }
   ```

8. 点 **Next**。**Policy name(策略名):** `agent-host-dynamo-access`。点 **Create policy**。
9. **确认成功:** 回到角色的 **Permissions(权限)** 标签,现在应看到两条:AWS 托管的 `AWSLambdaBasicExecutionRole` 和内联的 `agent-host-dynamo-access`。

**等价 CLI**(替代上面的控制台步骤):

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

aws iam create-role \
  --role-name agent-host-lambda-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam attach-role-policy \
  --role-name agent-host-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam put-role-policy \
  --role-name agent-host-lambda-role \
  --policy-name agent-host-dynamo-access \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [\"dynamodb:GetItem\", \"dynamodb:PutItem\"],
      \"Resource\": \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/agent_host\"
    }]
  }"
```

**确认成功:**

```bash
aws iam get-role --role-name agent-host-lambda-role --query "Role.Arn"
```

把这个 Role ARN 记下来——第 5 节用 CLI 创建 Lambda 时要粘到 `--role`。

> IAM 变更可能要几秒才生效。若下一节创建 Lambda 时报找不到/无法 assume 该角色,等 10–15 秒重试。

---

## 4. 打包代码

Lambda 从一个 `.zip` 文件运行你的代码。它把 zip 解压到 `/var/task`,再按点分路径导入你的 handler,所以**你的包和它的每一个依赖都必须位于 zip 的顶层**——不能套在 `src/` 里,也不能套在嵌套的 `build/` 里。

在**仓库根目录**运行:

```bash
rm -rf build function.zip
pip install . -t build/
```

`pip install . -t build/` 会把 `agent_host` 包**以及 `pyproject.toml` 里列出的每一个依赖**(openai、httpx、pydantic、pydantic-settings、boto3 及其子依赖)平铺装进 `build/`——于是 `build/agent_host/`、`build/openai/`、`build/httpx/`、`build/boto3/`、`build/pydantic_core/` 等并排放在一起。这个扁平布局正是 zip 需要的。

> **跨平台的坑(如果你用 Mac 或 Windows,务必看):** 两个依赖——`pydantic-core` 和 `jiter`——是编译型原生扩展,不是纯 Python。`pip install` 会挑一个匹配**你这台机器**的 wheel。而 AWS Lambda 的 Python 3.12 运行时跑在 **Amazon Linux, x86_64** 上。如果你在 macOS(尤其是 Apple Silicon)或 Windows 上直接跑上面的普通命令,pip 会装 `macosx_*` 或 `win_*` 版的那两个包,Lambda 在导入时就会报类似 `No module named 'pydantic_core._pydantic_core'` 或 `invalid ELF header` 的错——二进制根本无法在 Lambda 的操作系统上运行。写这份手册时已实测:在 Apple Silicon Mac 上打包会装成 `pydantic_core-...-macosx_11_0_arm64`,在 Lambda 上不可用。
>
> **修复——强制使用 Lambda 兼容的 wheel**,加四个 flag:
>
> ```bash
> rm -rf build function.zip
> pip install . \
>   --platform manylinux2014_x86_64 \
>   --implementation cp \
>   --python-version 3.12 \
>   --only-binary=:all: \
>   -t build/
> ```
>
> 这是在告诉 pip"下载为 manylinux(兼容 Amazon Linux)x86_64、CPython 3.12 构建的 wheel,并拒绝任何非预编译 wheel"——无论你在什么操作系统上跑这条命令。(如果你本来就在 Linux x86_64 上开发,上面的普通命令即可,可跳过此步。)这要与第 5 节里 Lambda 的 **Architecture(架构)** 设置对应(选 **x86_64**,控制台默认值)。
>
> 你可能看到一行 `ERROR: pip's dependency resolver does not currently take into account all the packages that are installed... (aiobotocore/streamlit ...)`。那说的是你本地 Python 环境里**其它**无关的包,和 `build/` 里的东西无关——只要命令最后打印 `Successfully installed ... agent-host-0.1.0`,就成功了。

现在打成 zip:

```bash
cd build && zip -rq ../function.zip . && cd ..
```

**确认成功**——不解压直接看 zip 内容:

```bash
unzip -l function.zip | head -20
```

应看到顶层就有 `agent_host/`、`openai/`、`httpx/`、`boto3/`、`pydantic_core/` 等条目——**不应**有统一的 `build/` 或 `src/` 前缀。然后专门确认 handler 文件在预期路径:

```bash
unzip -l function.zip | grep "agent_host/entrypoints/lambda_handler.py"
```

应打印出显示该文件的一行。若缺失,或一切都嵌套在 `build/...` 下,重做打 zip 那一步(多半是你在仓库根目录跑了 `zip` 而不是在 `build/` 里面)。

生成的 `function.zip` 约 25–30 MB——稳稳低于 Lambda 控制台/CLI"直接上传"的 50 MB 上限(超过的要先经 S3;`boto3` 的依赖树是体积的最大来源,因为它其实已由 Lambda 运行时自带——见第 5 节末尾的可选瘦身说明)。

---

## 5. 创建 Lambda 函数

**控制台路径:**

1. Console → 搜索 "Lambda" → **Lambda** → **Create function**(创建函数)。
2. 选 **Author from scratch**(从头开始创作)。
3. **Function name(函数名):** `agent-host`。
4. **Runtime(运行时):** **Python 3.12**。
5. **Architecture(架构):** **x86_64**(与第 4 节的 `manylinux2014_x86_64` wheel 匹配)。
6. 展开 **Change default execution role**(更改默认执行角色)→ 选 **Use an existing role**(使用现有角色)→ 选 `agent-host-lambda-role`。
7. 点 **Create function**。
8. **确认成功:** 进入函数页面并有绿色 "Successfully created the function" 横幅。

现在上传代码:

9. 在函数页面的 **Code(代码)** 标签,点右上 **Upload from** → **.zip file**。
10. 点 **Upload** 选择你第 4 节构建的 `function.zip`。点 **Save**。
11. **确认成功:** 页面重载,左侧文件浏览器应把 `agent_host/`、`openai/` 等显示为顶层文件夹(和你用 `unzip -l` 验证的布局一致)。

设置 handler:

12. 滚到 **Runtime settings(运行时设置)**(仍在 Code 标签)→ 点 **Edit**。
13. **Handler:** 把默认值替换为完全一致的:
    ```
    agent_host.entrypoints.lambda_handler.lambda_handler
    ```
    (这是 `<点分模块路径>.<函数名>`——Lambda 导入 `agent_host/entrypoints/lambda_handler.py` 并调用其中的 `lambda_handler` 函数。)
14. 点 **Save**。

设置超时和内存:

15. 进 **Configuration(配置)** 标签 → **General configuration**(常规配置)→ **Edit**。
16. **Memory(内存):** `256` MB。**Timeout(超时):** `1` min `0` sec(60 秒——调用 OpenRouter 的 LLM 可能要好几秒,默认 3 秒超时会把它掐断)。
17. 点 **Save**。

设置环境变量——这替代你本地的 `.env` 文件,因为 Lambda 没有文件系统上的 `.env` 可读(pydantic-settings 的 `Config` 两种情况都一样地读真实进程环境变量):

18. Configuration 标签 → **Environment variables(环境变量)** → **Edit** → **Add environment variable**,下表每行加一个。键与仓库根的 `.env.example` 相同,值如下:

    | Key | Value |
    |---|---|
    | `TELEGRAM_BOT_TOKEN` | 你从 `@BotFather` 拿到的 bot token |
    | `TELEGRAM_CHAT_ID` | 你的 chat ID |
    | `TELEGRAM_WEBHOOK_SECRET` | **现在生成的一串长随机字符串**——见下面的安全警告 |
    | `OPENROUTER_API_KEY` | 你的 OpenRouter key |
    | `LLM_MODEL` | `deepseek/deepseek-v3.2`(或不设,用此默认值) |
    | `LLM_FALLBACK_MODELS` | `qwen/qwen3.6-plus, google/gemini-2.5-flash`(或不设) |
    | `TIMEZONE` | `Asia/Shanghai`(或你的时区) |
    | `STORE_BACKEND` | `dynamo` —— **必须是 `dynamo`,不能用本地默认的 `sqlite`;Lambda 的文件系统是临时的,SQLite 会在每次调用之间丢失所有数据** |
    | `DYNAMO_TABLE` | `agent_host` |
    | `ENABLED_AGENTS` | `brief, chat` |
    | `DEFAULT_AGENT` | `chat` |
    | `OUTPUT_LANGUAGE` | `zh`(或 `en`) |

    给 `TELEGRAM_WEBHOOK_SECRET` 生成一个强随机 secret,例如:
    ```bash
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    ```

19. 点 **Save**。
20. **确认成功:** 重载环境变量页,核对上面每个 key 都在、且值非空(控制台里值是打码的——不确定是否输对时点眼睛图标查看核对一下)。

> ### 🔒 安全警告 —— 千万别省掉 `TELEGRAM_WEBHOOK_SECRET`
>
> 第 6 节你会创建一个 **Auth type 为 `NONE` 的 Function URL**——意味着*互联网上任何人*只要拿到这个 URL,就能通过 HTTP 调用这个 Lambda,不需要任何 AWS 凭证。这是必需的,因为调用这个 URL 的是 Telegram 的服务器(不是 AWS),而 Telegram 无法对 AWS 请求签名。挡在"互联网上任何人"和你 bot 的消息处理器之间的**唯一**东西,就是 `lambda_handler.py` 里的 `TELEGRAM_WEBHOOK_SECRET` 校验:
> ```python
> if cfg.telegram_webhook_secret:
>     got = headers.get("x-telegram-bot-api-secret-token")
>     if got != cfg.telegram_webhook_secret:
>         return {"statusCode": 403, "body": "forbidden"}
> ```
> **仔细看那个 `if`:它只在配置了 secret 时才校验。** `Config` 把 `telegram_webhook_secret` 默认成 `""`(空串),而空串在 Python 里是假值——所以如果你不设这个环境变量,校验会被**静默地整个跳过**,你的 webhook 会毫无鉴权地接受任何人的请求。这里务必设一串长随机值(用上面的 `secrets.token_urlsafe(32)`),并确保第 7 节注册 webhook 时作为 `secret_token` 传入的是**完全相同**的值。
>
> (注:本仓库的代码在英文原版手册撰写后又做了安全加固——`lambda_handler` 已改为**失败关闭**:secret 为空时直接 403,并改用 `hmac.compare_digest` 比较;同时 `Host` 只处理来自你本人 `TELEGRAM_CHAT_ID` 的消息,拒绝陌生人。即便如此,**仍然必须设置一个强随机 secret**。)

**可选——给 zip 瘦身:** Lambda 的 Python 运行时其基础镜像里已预装 `boto3`/`botocore`,所以自己再打包一份是冗余的(也是 zip 体积的最大单一来源)。重新打 zip 前,你可以安全删除 `build/boto3`、`build/botocore`、`build/s3transfer`、`build/dateutil`(或 `build/python_dateutil*`)、`build/jmespath`、`build/urllib3`,函数仍能靠运行时自带的 `boto3` 正常工作。这是可选的——不瘦身的 zip 也能用且在 50 MB 以内——除非你想要更小/更快的上传,否则可跳过。

---

## 6. 添加 Function URL(webhook 端点)

Function URL 给你的 Lambda 一个普通的 HTTPS 地址,Telegram 的服务器可以直接向它 POST——不需要 API Gateway。

**控制台路径:**

1. 在函数页面 → **Configuration** 标签 → 左侧 **Function URL** → **Create function URL**(创建函数 URL)。
2. **Auth type(鉴权类型):** 选 **NONE**。(这就是让 URL 可公开调用的原因——Telegram 的 webhook 机制无法对 AWS SigV4 请求签名,所以这里用不了 IAM 鉴权。这也正是为什么上面的 `TELEGRAM_WEBHOOK_SECRET` 校验是保护这个端点的唯一屏障——若你跳过了第 5 节的安全警告,回去重读。)
3. "Configure cross-origin resource sharing (CORS)" 不勾——不需要;调用方是 Telegram 服务器,不是浏览器。
4. 点 **Save**。
5. **确认成功:** 页面出现一个 **Function URL** 字段,值形如 `https://abcdefghij1234567890.lambda-url.ap-southeast-1.on.aws/`。复制它——下一节要用。

**接 Telegram 之前先做个 sanity check**——直接打这个 URL,确认能拿到*某种*响应(不是网络错误):

```bash
curl -i "<FUNCTION_URL>"
```

因为这个手动请求没有 `X-Telegram-Bot-Api-Secret-Token` 头,如果你设了 `TELEGRAM_WEBHOOK_SECRET`,你应该拿到 `HTTP/1.1 403 Forbidden`、body 为 `forbidden`——这个 403 其实是**好兆头**:说明 Lambda 可达、且 secret 校验在生效。若得到超时或 5xx,先看 CloudWatch 日志(第 9c 节)再继续。

---

## 7. 注册 Telegram webhook

告诉 Telegram"从现在起把更新发到这个 URL"就是对 Telegram 服务器(不是 AWS)的一次 API 调用:

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=<FUNCTION_URL>" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

把 `<TOKEN>` 换成你的 bot token,`<FUNCTION_URL>` 换成第 6 节的 URL,`<TELEGRAM_WEBHOOK_SECRET>` 换成你设为 Lambda `TELEGRAM_WEBHOOK_SECRET` 环境变量的**完全相同**的 secret。从此,每条发给你 bot 的消息都会让 Telegram 服务器把更新 POST 到你的 Function URL,并带上一个值为该 secret 的 `X-Telegram-Bot-Api-Secret-Token` 头——这正是 `lambda_handler.py` 校验的东西。

**确认成功:**

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

应返回 JSON,其中:
- `"url"` 与你的 Function URL 完全一致
- 没有 `"last_error_message"`(若有,它显示最近一次投递失败原因——例如 secret 错,或你的 Lambda 返回 5xx)
- `"pending_update_count": 0`(或一个会逐渐清零的小数字)

如果 `setWebhook` 本身返回 `{"ok":true,"result":true, ...}`,说明注册调用成功;而 `getWebhookInfo` 才是确认 Telegram 真能*投递*到它的依据。

---

## 8. 创建每日定时任务(EventBridge)

这是让每日简报自动到达、你无需手动发送的关键。

**控制台路径:**

1. Console → 搜索 "EventBridge" → **Amazon EventBridge** → 左侧 **Scheduler** → **Schedules** → **Create schedule**(创建计划)。
2. **Schedule name(计划名):** `agent-host-daily-brief`。
3. **Schedule pattern(计划模式):** 选 **Recurring schedule**(周期性)→ **Cron-based schedule**(基于 cron)。
4. **Cron expression(cron 表达式):** 输入
   ```
   0 8 * * ? *
   ```
   控制台会把它包成 `cron(0 8 * * ? *)`。EventBridge 的 cron 有**六**个字段,顺序如下——比标准 Unix cron 多一个:

   | 字段 | 含义 | 我们的值 | 在这里的意思 |
   |---|---|---|---|
   | 1 | Minutes(分) | `0` | 整点 |
   | 2 | Hours(时) | `8` | 早上 8 点 |
   | 3 | Day-of-month(日) | `*` | 每天 |
   | 4 | Month(月) | `*` | 每月 |
   | 5 | Day-of-week(星期) | `?` | "无特定值"——因为日已经是 `*`,EventBridge cron 不允许日和星期同时为 `*`,两者必有一个是 `?` |
   | 6 | Year(年) | `*` | 每年 |

   连起来读:"在第 8 小时的第 0 分钟,每天、每月、任意星期、每年"——即每天 08:00。
5. **Timezone(时区):** 这是一个单独的下拉,不属于 cron 字符串——设为 **`Asia/Shanghai`**。EventBridge 按你这里选的时区来解释 cron 表达式,所以 `0 8 * * ? *` + `Asia/Shanghai` 会在上海时间 08:00 触发,不管当天对应的 UTC 偏移是多少(如果你选的时区有夏令时,这点很重要)。
6. **Flexible time window(弹性时间窗):** 选 **Off**(正好 08:00 触发,而非在一个窗口内)——学习阶段更好理解;跑通后可改成弹性窗以分散调用负载。
7. 点 **Next**。
8. **Target(目标):** 选 **AWS Lambda** → **Invoke**。**Lambda function:** 选 `agent-host`。
9. 展开 **Additional settings** → **Input** → 选 **Constant JSON text**(常量 JSON 文本)→ 粘贴完全一致的:
   ```json
   {"mode": "scheduled", "agent": "brief"}
   ```
   这就是你 `lambda_handler` 收到的 `event` 字典——它的代码检查 `event.get("mode") == "scheduled"`,为真则调用 `host.run_scheduled(event["agent"])`,即 `run_scheduled("brief")`,这才是真正组装并发送每日简报的动作。
10. 仍在这个 Target 步骤,找到 **Execution role(执行角色)**。这是与 `agent-host-lambda-role` **不同**的角色——它是让 **EventBridge Scheduler 服务本身**能对你的函数调用 `lambda:InvokeFunction` 的权限(你 Lambda 自己的角色只管代码*运行起来后*能做什么,不管谁被允许触发它)。选 **Create new role for this schedule**(为此计划新建角色)——控制台会自动生成一个最小角色,仅限调用这一个 Lambda。(若你想自己管理,"Use existing role" 需要一个被 `scheduler.amazonaws.com` 信任、且对 `agent-host` 函数 ARN 有 `lambda:InvokeFunction` 策略的角色——但让控制台创建更简单,且在这里同样安全。)
11. 点 **Next**,retry/DLQ 设置和 schedule group(`default`)暂时保持默认,再 **Next**,检查后点 **Create schedule**。
12. **确认成功:** 回到 Schedules 列表,`agent-host-daily-brief` 应出现且 **State(状态): Enabled**。

**不用等到明天 8 点就立刻测试:** 打开该计划——Scheduler 的计划没有内置的"立即运行"按钮,所以改为用相同的 payload 直接调用 Lambda,以证明整条链路能通:

```bash
aws lambda invoke \
  --function-name agent-host \
  --payload '{"mode":"scheduled","agent":"brief"}' \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

**确认成功:** `response.json` 应包含 `{"statusCode": 200, "body": "ok"}`,且你的 Telegram 应在几秒内收到简报消息。

---

## 9. 端到端验证

**(a) Webhook 路径——给你的 bot 发消息。**

打开 Telegram,找到你的 bot,发任意文字(例如 "hello")。你应在几秒内收到回复(默认路由到 `ChatAgent`——路由机制见 README 的 "how to add a new agent" 一节)。发 `/brief`,应按需收到新闻简报(通过其注册的 `/brief` 命令路由到 `BriefAgent`)。

若没有回音:重新检查 `getWebhookInfo`(第 7 节)看有没有 `last_error_message`,然后到下面 (c) 看日志。

**(b) 定时路径——确认每日运行。**

你已在第 8 节用手动 `aws lambda invoke` 验证过。若要确认*真正的定时*(不只是手动调用)接线正确,可临时把计划的 cron 表达式改到几分钟后,等它触发、确认简报到达,再改回 `0 8 * * ? *`。

**(c) 看 CloudWatch 日志——怎么找 Python traceback。**

每次 Lambda 调用的 `print()` 输出和未捕获异常都会进 CloudWatch Logs。

1. Console → 搜索 "CloudWatch" → **CloudWatch** → 左侧 **Log groups**(日志组)。
2. 找到名为 `/aws/lambda/agent-host` 的日志组(Lambda 会为每个函数自动建一个,名为 `/aws/lambda/<函数名>`),点进去。
3. 你会看到一列 **Log streams(日志流)**,按调用批次(每次冷启动一批)排列,最新的在最上。点最上面那个。
4. 翻看事件。Python traceback 长这样,通常是 `REPORT` 行之前的最后内容:
   ```
   [ERROR] Runtime.HandlerNotFound: ...
   ```
   或者,对于你自己代码里抛出的异常(记住:`Host.run_scheduled` 和 `Host.handle_message` 会捕获并记录来自各 agent 的异常而不是崩溃,所以 agent 级的 bug 会以 `log.exception(...)` 的 traceback 形式出现在这里,而不是 Lambda 错误):
   ```
   [ERROR] agent brief run_scheduled failed
   Traceback (most recent call last):
     File "/var/task/agent_host/host.py", line 26, in run_scheduled
       agent.run_scheduled(self._svc_for(agent))
     File "/var/task/agent_host/agents/brief/agent.py", line 42, in run_scheduled
   ...
   SomeException: description of what went wrong
   ```
   **从下往上读**:最后一行(`SomeExceptionType: message`)点明错误类型和信息;紧上一行是抛出它的确切代码行;再往上是走到这里的调用链。`File "/var/task/..."` 路径直接对应第 4 节的 zip 布局(`/var/task` 是 Lambda 解压 `function.zip` 的地方)。
5. 每次调用还会以一行 `REPORT RequestId: ...` 结尾,显示 `Duration`、`Billed Duration`、`Memory Size`、`Max Memory Used`——用来检查你是否接近超时(60s)或内存耗尽(256MB)。

**不想在控制台里翻的话,用 CLI 快速看:**

```bash
aws logs tail /aws/lambda/agent-host --since 10m --follow
```

实时流式打印新日志行——在另一个窗口给 bot 发消息,看着这次调用的日志出现。

---

## 10. 成本与拆除

**成本。** 对单用户个人 bot(一天几条聊天 + 一次定时运行),你应完全落在 AWS 免费额度内:

- **Lambda:** 免费额度含每月 100 万次请求和 40 万 GB-秒 计算,且永久有效(不只是前 12 个月)。一个月几百次、每次 256MB/几秒的调用,相对这个额度只是零头。
- **DynamoDB 按需:** 免费额度含 25GB 存储和每月一定量的读/写请求单元——个人 bot 的记忆/去重行(几条小 item)远远用不到。
- **EventBridge Scheduler:** 这个量级免费(收费只在远超每天一条 cron 的规模才按调用产生)。
- **Function URL 与 CloudWatch Logs:** URL 本身不收费;CloudWatch Logs 有免费额度(5GB 摄入),个人 bot 的日志量到不了,不过日志默认会无限累积——建议设个保留期(Log group → **Actions** → **Edit retention setting**,例如 30 天),别让旧日志一直堆着。
- **无论如何都要付的:** OpenRouter 的 API 使用费(在 AWS 之外),用于 LLM 调用——当前价格见 https://openrouter.ai/ 上你所配置模型的定价。

**拆除——按此顺序删除一切,彻底停掉所有计费和依赖:**

1. **EventBridge 计划:** Console → EventBridge → Scheduler → Schedules → 选 `agent-host-daily-brief` → **Delete**。
2. **Telegram webhook**(可选但整洁——让 Telegram 别再去调一个你即将删除的 URL):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
   ```
3. **Function URL:** Console → Lambda → `agent-host` → Configuration → Function URL → **Delete**。
4. **Lambda 函数:** Console → Lambda → 选 `agent-host` → **Actions** → **Delete** → 输入 `confirm` 点 **Delete**。
5. **DynamoDB 表:** Console → DynamoDB → Tables → 选 `agent_host` → **Delete** → 输入 `confirm` → **Delete table**。(这会永久删除所有存储的聊天记忆和去重状态——没有撤销。)
6. **IAM 角色**——现在没人引用了,把两个角色都删:
   - `agent-host-lambda-role`(第 3 节里 Lambda 自己的角色)。
   - 第 8 节步骤 10 里 Scheduler 自动创建的执行角色,名字形如 `Amazon_EventBridge_Scheduler_LAMBDA_<随机后缀>`。Console → IAM → Roles → 搜 "scheduler" 或 "eventbridge" 找到它。(步骤 1 删除计划**不会**自动删这个角色——它是独立资源。)

   每个:选中角色 → **Delete** → 输入角色名确认 → **Delete**。

**确认成功:** 重跑
`aws lambda get-function --function-name agent-host` 和
`aws dynamodb describe-table --table-name agent_host`——两者现在都应返回 "not found" 错误(`ResourceNotFoundException`),确认没有可计费的东西还在跑。
