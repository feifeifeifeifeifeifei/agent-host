# AWS Deploy Runbook — agent-host

This is a click-by-click guide to deploying `agent-host` on AWS Lambda, with Telegram as the
messaging channel and DynamoDB as the storage backend. It assumes **no prior AWS experience** —
every step names the exact console path or the exact CLI command, what to type/select, and how
to confirm it worked before you move to the next step.

Total time: 45–75 minutes the first time. Total cost: effectively $0/month for personal use (see
[Section 10](#10-cost--teardown)).

**Architecture in one sentence:** Telegram messages hit a public Lambda Function URL (webhook);
a daily EventBridge schedule invokes the same Lambda to push the news brief; the Lambda reads/
writes conversation memory and dedup state in one DynamoDB table.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Create the DynamoDB table](#2-create-the-dynamodb-table)
3. [Create the IAM execution role](#3-create-the-iam-execution-role)
4. [Package the code](#4-package-the-code)
5. [Create the Lambda function](#5-create-the-lambda-function)
6. [Add a Function URL (the webhook endpoint)](#6-add-a-function-url-the-webhook-endpoint)
7. [Register the Telegram webhook](#7-register-the-telegram-webhook)
8. [Create the daily schedule (EventBridge)](#8-create-the-daily-schedule-eventbridge)
9. [Verify everything end to end](#9-verify-everything-end-to-end)
10. [Cost & teardown](#10-cost--teardown)

---

## 1. Prerequisites

Before you touch the AWS console, gather these:

- **An AWS account.** If you don't have one, sign up at https://aws.amazon.com/ (requires a
  credit card, but everything in this runbook fits in the AWS Free Tier for a single-user bot —
  see [Section 10](#10-cost--teardown)).
- **The AWS CLI installed and configured.**
  - Install: `brew install awscli` (macOS) or see
    https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html.
  - Create an access key: Console → click your account name (top right) → **Security
    credentials** → **Access keys** → **Create access key** → choose "Command Line Interface
    (CLI)" → **Create**. Copy the **Access key ID** and **Secret access key** immediately (the
    secret is shown only once).
  - Configure the CLI:
    ```bash
    aws configure
    ```
    Enter the access key ID, secret access key, a default region (pick one close to you or your
    Telegram users, e.g. `ap-southeast-1` for Singapore or `us-east-1`), and output format `json`.
  - **Confirm it worked:**
    ```bash
    aws sts get-caller-identity
    ```
    You should see your account ID and user ARN printed back as JSON. If you get an error here,
    stop and fix it before continuing — nothing else in this runbook will work without it.
- **Python 3.12** installed locally (`python3 --version`). Needed to package the Lambda zip.
- **A Telegram bot token and your chat ID.** If you haven't done this yet, see the "Local
  quickstart" section of the [README](../README.md) — message `@BotFather` on Telegram to create
  a bot and get `TELEGRAM_BOT_TOKEN`, then message your bot once and read `getUpdates` to get your
  `TELEGRAM_CHAT_ID`.
- **An OpenRouter API key** from https://openrouter.ai/keys (used by the LLM client).

Everywhere below, "Console → X → Y" means: open https://console.aws.amazon.com/, make sure the
region selector in the top-right corner matches the region you configured above, then use the
search bar or left-hand navigation to reach **X**, then click **Y**.

---

## 2. Create the DynamoDB table

DynamoDB stores everything the code persists: chat memory (`ChatAgent`), seen-item dedup keys and
run metadata (`BriefAgent`). The code only ever reads/writes by a single partition key `pk` — no
secondary indexes needed.

**Console path:**

1. Console → search "DynamoDB" → **DynamoDB** → **Create table**.
2. **Table name:** `agent_host`
3. **Partition key:** `pk`, type **String**. Leave "Sort key" empty — the code namespaces its own
   keys inside the value of `pk` (e.g. `brief#memory#42`), so it doesn't need a sort key.
4. **Table settings:** choose **Customize settings** → under "Read/write capacity settings"
   choose **On-demand**. (On-demand means you pay per request with no capacity to provision or
   tune — the right choice for a low-traffic personal bot, and it's what the Free Tier covers.)
5. Leave encryption/other settings at their defaults → **Create table**.
6. **Confirm it worked:** wait ~30–60 seconds, refresh, and the table's **Status** column should
   read **Active**.

**Equivalent CLI command** (skip the console steps above if you use this):

```bash
aws dynamodb create-table \
  --table-name agent_host \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

**Confirm it worked:**

```bash
aws dynamodb describe-table --table-name agent_host --query "Table.TableStatus"
```

Should print `"ACTIVE"` (it may briefly show `"CREATING"` right after you run the command — wait
a few seconds and re-run).

---

## 3. Create the IAM execution role

Every Lambda function runs "as" an IAM role — a set of permissions that says what the function is
allowed to touch. Our function needs two things:

- **Permission to write its own logs** to CloudWatch (so you can debug it later). AWS ships a
  ready-made managed policy for exactly this: `AWSLambdaBasicExecutionRole`.
- **Permission to read and write one DynamoDB table.** The code (`DynamoStore` in
  `src/agent_host/store/dynamo_store.py`) only ever calls `get_item` and `put_item` — it never
  scans, queries, or deletes — so the inline policy grants exactly `dynamodb:GetItem` and
  `dynamodb:PutItem`, scoped to the one table's ARN. This is the principle of least privilege: if
  the Lambda's credentials ever leaked, the blast radius is "can read/write this one table," not
  "can do anything in this AWS account."

**Console path:**

1. Console → search "IAM" → **IAM** → **Roles** (left nav) → **Create role**.
2. **Trusted entity type:** **AWS service**. **Use case:** **Lambda**. Click **Next**.
3. **Add permissions:** in the search box type `AWSLambdaBasicExecutionRole` and check its box.
   Click **Next**.
4. **Role name:** `agent-host-lambda-role`. (Optional) Description: "Execution role for the
   agent-host Lambda function." Click **Create role**.
5. **Confirm it worked:** you land back on the Roles list; search for `agent-host-lambda-role` and
   confirm it appears with `AWSLambdaBasicExecutionRole` listed under its permissions.
6. Now add the DynamoDB permission. Click into the role → **Add permissions** (button, top right)
   → **Create inline policy**.
7. Switch the policy editor to **JSON** (tab near the top of the editor) and paste, replacing
   `<ACCOUNT_ID>` and `<REGION>` with your own (find your account ID with
   `aws sts get-caller-identity --query Account`, and your region is what you set in
   `aws configure`):

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

8. Click **Next**. **Policy name:** `agent-host-dynamo-access`. Click **Create policy**.
9. **Confirm it worked:** back on the role's **Permissions** tab, you should now see two entries:
   the AWS-managed `AWSLambdaBasicExecutionRole` and the inline `agent-host-dynamo-access`.

**Equivalent CLI** (alternative to the console steps above):

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

**Confirm it worked:**

```bash
aws iam get-role --role-name agent-host-lambda-role --query "Role.Arn"
```

Copy this Role ARN somewhere — you'll paste it into `--role` when creating the Lambda in
Section 5 if you use the CLI path there.

> IAM changes can take a few seconds to propagate. If Lambda creation in the next section
> complains it can't find/assume the role, wait 10–15 seconds and retry.

---

## 4. Package the code

Lambda runs your code from a `.zip` file. It unzips the file to `/var/task` and imports your
handler by dotted path, so **your package and every one of its dependencies must sit at the top
level of the zip** — not inside a `src/` folder, not inside a nested `build/` folder.

Run this from the **repository root**:

```bash
rm -rf build function.zip
pip install . -t build/
```

`pip install . -t build/` installs the `agent_host` package **and every dependency listed in
`pyproject.toml`** (openai, httpx, pydantic, pydantic-settings, boto3, and their sub-dependencies)
into `build/`, flat — so `build/agent_host/`, `build/openai/`, `build/httpx/`, `build/boto3/`,
`build/pydantic_core/`, etc. all sit side-by-side. That flat layout is exactly what the zip needs.

> **Cross-platform gotcha (important if you're on a Mac or Windows machine):** two of the
> dependencies — `pydantic-core` and `jiter` — are compiled native extensions, not pure Python.
> `pip install` picks a wheel that matches **your** machine. AWS Lambda's Python 3.12 runtime runs
> on **Amazon Linux, x86_64**. If you run the plain command above on macOS (especially Apple
> Silicon) or Windows, pip installs a `macosx_*` or `win_*` build of those two packages, and the
> Lambda will fail at import time with an error like `No module named 'pydantic_core._pydantic_core'`
> or `invalid ELF header` — the binary simply won't run on Lambda's OS. This was confirmed while
> writing this runbook: building on an Apple Silicon Mac installs
> `pydantic_core-...-macosx_11_0_arm64`, which is unusable on Lambda.
>
> **Fix — force Lambda-compatible wheels** by adding four flags:
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
> This tells pip "download wheels built for manylinux (Amazon Linux-compatible) x86_64, CPython
> 3.12, and refuse anything that isn't a prebuilt wheel" — regardless of what OS you're running
> the command on. (If you already develop directly on Linux x86_64, the plain command from above
> works fine and you can skip this.) You'll match this with the Lambda's **Architecture** setting
> in Section 5 (choose **x86_64**, the console default).
>
> You may see a line like `ERROR: pip's dependency resolver does not currently take into account
> all the packages that are installed... (aiobotocore/streamlit ...)`. That refers to *other*
> unrelated packages already in your local Python environment, not to anything in `build/` — as
> long as the command ends with `Successfully installed ... agent-host-0.1.0`, it worked.

Now zip it up:

```bash
cd build && zip -rq ../function.zip . && cd ..
```

**Confirm it worked** — inspect the zip's contents without unzipping it:

```bash
unzip -l function.zip | head -20
```

You should see top-level entries for `agent_host/`, `openai/`, `httpx/`, `boto3/`,
`pydantic_core/`, etc. — **not** a single `build/` or `src/` prefix. Then specifically confirm the
handler file is present at the expected path:

```bash
unzip -l function.zip | grep "agent_host/entrypoints/lambda_handler.py"
```

This should print one line showing that file. If it's missing, or everything is nested under
`build/...`, redo the zip step (you likely ran `zip` from the repo root instead of from inside
`build/`).

The resulting `function.zip` is roughly 25–30 MB — comfortably under Lambda's 50 MB "upload
directly" limit for the console/CLI (packages over that need to go via S3 first; `boto3`'s
dependency tree is the biggest contributor to the size, since it's technically already provided
by the Lambda runtime — see the optional trimming note at the end of Section 5).

---

## 5. Create the Lambda function

**Console path:**

1. Console → search "Lambda" → **Lambda** → **Create function**.
2. Choose **Author from scratch**.
3. **Function name:** `agent-host`.
4. **Runtime:** **Python 3.12**.
5. **Architecture:** **x86_64** (matches the `manylinux2014_x86_64` wheels from Section 4).
6. Expand **Change default execution role** → choose **Use an existing role** → select
   `agent-host-lambda-role`.
7. Click **Create function**.
8. **Confirm it worked:** you land on the function's page with a green "Successfully created the
   function" banner.

Now upload your code:

9. On the function page, in the **Code** tab, click **Upload from** (top right) → **.zip file**.
10. Click **Upload** and select the `function.zip` you built in Section 4. Click **Save**.
11. **Confirm it worked:** the page reloads and the file explorer on the left should show
    `agent_host/`, `openai/`, etc. as top-level folders (same layout you verified with `unzip -l`).

Set the handler:

12. Scroll to **Runtime settings** (still on the Code tab) → click **Edit**.
13. **Handler:** replace the default with exactly:
    ```
    agent_host.entrypoints.lambda_handler.lambda_handler
    ```
    (This is `<module path with dots>.<function name>` — Lambda imports
    `agent_host/entrypoints/lambda_handler.py` and calls the `lambda_handler` function inside it.)
14. Click **Save**.

Set timeout and memory:

15. Go to the **Configuration** tab → **General configuration** → **Edit**.
16. **Memory:** `256` MB. **Timeout:** `1` min `0` sec (60 seconds — LLM calls to OpenRouter can
    take several seconds, and the default 3-second timeout will cut them off).
17. Click **Save**.

Set environment variables — this replaces your local `.env` file, since Lambda has no filesystem
`.env` to read from (pydantic-settings' `Config` reads real process environment variables the same
way either way):

18. Configuration tab → **Environment variables** (left of that tab's sub-nav) → **Edit** →
    **Add environment variable**, once per row below. Use the same keys as `.env.example` in the
    repo root, with these values:

    | Key | Value |
    |---|---|
    | `TELEGRAM_BOT_TOKEN` | your bot token from `@BotFather` |
    | `TELEGRAM_CHAT_ID` | your chat ID |
    | `TELEGRAM_WEBHOOK_SECRET` | **a long random string you generate now** — see the security warning below |
    | `OPENROUTER_API_KEY` | your OpenRouter key |
    | `LLM_MODEL` | `deepseek/deepseek-v3.2` (or leave unset to use this default) |
    | `LLM_FALLBACK_MODELS` | `qwen/qwen3.6-plus, google/gemini-2.5-flash` (or leave unset) |
    | `TIMEZONE` | `Asia/Shanghai` (or your timezone) |
    | `STORE_BACKEND` | `dynamo` — **must be `dynamo`, not the local default `sqlite`; Lambda's filesystem is ephemeral so SQLite would lose all data between invocations** |
    | `DYNAMO_TABLE` | `agent_host` |
    | `ENABLED_AGENTS` | `brief, chat` |
    | `DEFAULT_AGENT` | `chat` |
    | `OUTPUT_LANGUAGE` | `zh` (or `en`) |

    Generate a strong random secret for `TELEGRAM_WEBHOOK_SECRET`, e.g.:
    ```bash
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    ```

19. Click **Save**.
20. **Confirm it worked:** reload the Environment variables page and check every key above is
    listed with a non-empty value (values are masked in the console — click the eye icon to
    reveal one and spot-check it if you're unsure you typed it correctly).

> ### 🔒 Security warning — do not skip `TELEGRAM_WEBHOOK_SECRET`
>
> In Section 6 you'll create a **Function URL with Auth type `NONE`** — meaning *anyone on the
> internet* who has the URL can invoke this Lambda over HTTP, no AWS credentials required. This is
> required because Telegram's servers (not AWS) call this URL, and Telegram can't sign AWS
> requests. The **only** thing standing between "anyone on the internet" and your bot's message
> handler is the `TELEGRAM_WEBHOOK_SECRET` check in `lambda_handler.py`:
> ```python
> if cfg.telegram_webhook_secret:
>     got = headers.get("x-telegram-bot-api-secret-token")
>     if got != cfg.telegram_webhook_secret:
>         return {"statusCode": 403, "body": "forbidden"}
> ```
> **Look closely at that `if`: it only checks the secret if one is configured.** `Config` defaults
> `telegram_webhook_secret` to `""` (empty string), and an empty string is falsy in Python — so if
> you leave this environment variable unset, the check is **silently skipped entirely** and your
> webhook accepts requests from anyone, with no authentication at all. Always set a long random
> value here (the `secrets.token_urlsafe(32)` command above), and make sure the exact same value
> is passed as `secret_token` when you register the webhook in Section 7.

**Optional — shrink the zip:** the Lambda Python runtime already includes `boto3`/`botocore`
pre-installed in its base image, so bundling your own copy is redundant (it's the biggest single
contributor to the zip's size). You can safely delete `build/boto3`, `build/botocore`,
`build/s3transfer`, `build/dateutil` (or `build/python_dateutil*`), `build/jmespath`, and
`build/urllib3` before re-zipping, and the function will still work by falling back to the
runtime's built-in `boto3`. This is optional — the un-trimmed zip works fine and stays under the
50 MB limit — skip it unless you want the smaller/faster upload.

---

## 6. Add a Function URL (the webhook endpoint)

A Function URL gives your Lambda a plain HTTPS address that Telegram's servers can POST to
directly — no API Gateway needed.

**Console path:**

1. On the function's page → **Configuration** tab → **Function URL** (left sub-nav) → **Create
   function URL**.
2. **Auth type:** select **NONE**. (This is what makes the URL publicly callable — Telegram's
   webhook mechanism has no way to sign AWS SigV4 requests, so IAM auth isn't an option here. This
   is exactly why the `TELEGRAM_WEBHOOK_SECRET` check above is the only thing protecting this
   endpoint — re-read the security warning in Section 5 if you skipped it.)
3. Leave "Configure cross-origin resource sharing (CORS)" unchecked — not needed; Telegram's
   servers call this directly, not a browser.
4. Click **Save**.
5. **Confirm it worked:** the page now shows a **Function URL** field with a value like
   `https://abcdefghij1234567890.lambda-url.ap-southeast-1.on.aws/`. Copy this URL — you'll need
   it in the next section.

**Sanity check before wiring up Telegram** — hit the URL directly and confirm you get *some*
response (not a network error):

```bash
curl -i "<FUNCTION_URL>"
```

Because there's no `X-Telegram-Bot-Api-Secret-Token` header on this manual request, if you set
`TELEGRAM_WEBHOOK_SECRET`, you should get back `HTTP/1.1 403 Forbidden`, body `forbidden` — that
403 is actually a **good sign**: it means the Lambda is reachable and the secret check is active.
If you get a timeout or 5xx instead, check CloudWatch logs (Section 9c) before continuing.

---

## 7. Register the Telegram webhook

Telling Telegram "send updates to this URL from now on" is one API call to Telegram's servers
(not AWS):

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=<FUNCTION_URL>" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Replace `<TOKEN>` with your bot token, `<FUNCTION_URL>` with the URL from Section 6, and
`<TELEGRAM_WEBHOOK_SECRET>` with the **exact same** secret string you set as the Lambda's
`TELEGRAM_WEBHOOK_SECRET` environment variable. From this point on, every message sent to your bot
makes Telegram's servers POST the update to your Function URL, with an
`X-Telegram-Bot-Api-Secret-Token` header set to that secret — which is what `lambda_handler.py`
checks against.

**Confirm it worked:**

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

You should get JSON back with:
- `"url"` matching your Function URL exactly
- `"last_error_message"` absent (if it's present, it shows the last delivery failure — e.g. a
  wrong secret, or a 5xx from your Lambda)
- `"pending_update_count": 0` (or a small number that drains down)

If `setWebhook` itself returned `{"ok":true,"result":true, ...}`, the registration call succeeded;
`getWebhookInfo` is what confirms Telegram can actually *deliver* to it.

---

## 8. Create the daily schedule (EventBridge)

This is what makes the daily brief arrive automatically without you sending anything.

**Console path:**

1. Console → search "EventBridge" → **Amazon EventBridge** → left nav → **Scheduler** →
   **Schedules** → **Create schedule**.
2. **Schedule name:** `agent-host-daily-brief`.
3. **Schedule pattern:** choose **Recurring schedule** → **Cron-based schedule**.
4. **Cron expression:** enter
   ```
   0 8 * * ? *
   ```
   The console will wrap this as `cron(0 8 * * ? *)`. EventBridge cron has **six** fields, in this
   order — one more field than standard Unix cron:

   | Field | Meaning | Our value | What it means here |
   |---|---|---|---|
   | 1 | Minutes | `0` | at the top of the hour |
   | 2 | Hours | `8` | 8 AM |
   | 3 | Day-of-month | `*` | every day of the month |
   | 4 | Month | `*` | every month |
   | 5 | Day-of-week | `?` | "no specific value" — required because day-of-month is already `*`; EventBridge cron never lets both day-of-month and day-of-week be `*` at once, one of the pair must be `?` |
   | 6 | Year | `*` | every year |

   Read together: "at minute 0 of hour 8, every day, every month, any day-of-week, every year" —
   i.e., 08:00 every day.
5. **Timezone:** this is a separate dropdown, not part of the cron string — set it to
   **`Asia/Shanghai`**. EventBridge evaluates the cron expression in whatever timezone you pick
   here, so `0 8 * * ? *` + `Asia/Shanghai` fires at 08:00 Shanghai time regardless of what UTC
   offset that is on a given day (important if your chosen timezone observes daylight saving).
6. **Flexible time window:** choose **Off** (fire at exactly 08:00, not within a window) — simpler
   to reason about while you're learning; you can switch to a flex window later once this is
   working, to spread invocation load.
7. Click **Next**.
8. **Target:** choose **AWS Lambda** → **Invoke**. **Lambda function:** select `agent-host`.
9. Expand **Additional settings** → **Input** → choose **Constant JSON text** → paste exactly:
   ```json
   {"mode": "scheduled", "agent": "brief"}
   ```
   This is the `event` dict your `lambda_handler` receives — its code checks
   `event.get("mode") == "scheduled"` and, if true, calls `host.run_scheduled(event["agent"])`,
   i.e. `run_scheduled("brief")`, which is what actually assembles and sends the daily brief.
10. Still on this Target step, find **Execution role**. This is a *different* role from
    `agent-host-lambda-role` — it's the permission that lets the **EventBridge Scheduler service
    itself** call `lambda:InvokeFunction` on your function (your Lambda's own role only governs
    what the code does *once it's running*, not who's allowed to trigger it). Choose **Create new
    role for this schedule** — the console will generate a minimal role, scoped to invoking just
    this one Lambda, automatically. (If you'd rather manage it yourself, "Use existing role" would
    need a role trusted by `scheduler.amazonaws.com` with a `lambda:InvokeFunction` policy on the
    `agent-host` function's ARN — but letting the console create it is simpler and just as secure
    here.)
11. Click **Next**, leave retry/DLQ settings and the schedule group (`default`) at their defaults
    for now, **Next** again, review, and click **Create schedule**.
12. **Confirm it worked:** back on the Schedules list, `agent-host-daily-brief` should appear with
    **State: Enabled**.

**Test it immediately without waiting until tomorrow 8 AM:** open the schedule → there's no
built-in "run now" button for Scheduler schedules, so instead invoke the Lambda directly with the
same payload to prove the wiring works end to end:

```bash
aws lambda invoke \
  --function-name agent-host \
  --payload '{"mode":"scheduled","agent":"brief"}' \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

**Confirm it worked:** `response.json` should contain `{"statusCode": 200, "body": "ok"}`, and
your Telegram chat should receive the brief message within a few seconds.

---

## 9. Verify everything end to end

**(a) Webhook path — send your bot a message.**

Open Telegram, find your bot, and send it any text (e.g. "hello"). You should get a reply within a
few seconds (routed to `ChatAgent` by default — see the README's "how to add a new agent" section
for how routing works). Send `/brief` and you should get the news brief on demand (routed to
`BriefAgent` via its registered `/brief` command).

If nothing comes back: re-check `getWebhookInfo` (Section 7) for a `last_error_message`, then go
to (c) below and read the logs.

**(b) Scheduled path — confirm the daily run.**

You already exercised this with the manual `aws lambda invoke` in Section 8. To confirm the
*actual* schedule (not just the manual invoke) is wired correctly, you can temporarily edit the
schedule's cron expression to a couple of minutes in the future, wait for it to fire, confirm the
brief arrives, then edit it back to `0 8 * * ? *`.

**(c) Read CloudWatch logs — how to find a Python traceback.**

Every Lambda invocation's `print()` output and unhandled exceptions land in CloudWatch Logs.

1. Console → search "CloudWatch" → **CloudWatch** → left nav → **Log groups**.
2. Find the log group named `/aws/lambda/agent-host` (Lambda auto-creates one log group per
   function, named `/aws/lambda/<function-name>`) and click it.
3. You'll see a list of **Log streams**, one per "cold start" batch of invocations, sorted with
   the most recent at the top. Click the top one.
4. Scroll through the events. A Python traceback looks like this and is usually the last thing
   before a `REPORT` line:
   ```
   [ERROR] Runtime.HandlerNotFound: ...
   ```
   or, for exceptions raised inside your own code (remember: `Host.run_scheduled` and
   `Host.handle_message` catch and log exceptions from individual agents rather than crash, so an
   agent-level bug shows up as a `log.exception(...)` traceback here rather than a Lambda error):
   ```
   [ERROR] agent brief run_scheduled failed
   Traceback (most recent call last):
     File "/var/task/agent_host/host.py", line 26, in run_scheduled
       agent.run_scheduled(self._svc_for(agent))
     File "/var/task/agent_host/agents/brief/agent.py", line 42, in run_scheduled
   ...
   SomeException: description of what went wrong
   ```
   Read from the **bottom up**: the last line (`SomeExceptionType: message`) names the error and
   the message; the line just above it is the exact line of code that raised it; and working
   upward shows you the call chain that got there. The `File "/var/task/..."` paths map directly
   to the zip layout from Section 4 (`/var/task` is where Lambda unzips `function.zip`).
5. Every invocation also ends with a `REPORT RequestId: ...` line showing `Duration`,
   `Billed Duration`, `Memory Size`, and `Max Memory Used` — useful for checking you're not close
   to timing out (60s) or running out of memory (256MB).

**Quick CLI alternative to browsing the console:**

```bash
aws logs tail /aws/lambda/agent-host --since 10m --follow
```

Streams new log lines live — send your bot a message in another window and watch the invocation's
logs appear.

---

## 10. Cost & teardown

**Cost.** For a single-user personal bot (a handful of chat messages a day + one scheduled run a
day), you should stay entirely within the AWS Free Tier:

- **Lambda:** Free Tier includes 1M requests/month and 400,000 GB-seconds of compute/month,
  forever (not just 12 months). A few hundred invocations a month at 256MB/few seconds each is a
  rounding error against that.
- **DynamoDB on-demand:** Free Tier includes 25GB storage and a monthly allotment of read/write
  request units — a personal bot's memory/dedup rows (a handful of small items) won't come close.
- **EventBridge Scheduler:** free for this volume (charges only apply per-invocation at a scale
  far beyond one cron a day).
- **Function URLs and CloudWatch Logs:** no charge for the URL itself; CloudWatch Logs has a Free
  Tier allotment (5GB ingestion) that a personal bot's log volume won't approach, though logs do
  accumulate indefinitely by default — consider setting a retention period (Log group → **Actions**
  → **Edit retention setting**, e.g. 30 days) so old logs don't linger.
- **What you pay for regardless:** OpenRouter API usage (outside AWS) for the LLM calls — check
  https://openrouter.ai/ for current pricing on whatever model you configured.

**Teardown — delete everything in this order to fully stop all charges and dependencies:**

1. **EventBridge schedule:** Console → EventBridge → Scheduler → Schedules → select
   `agent-host-daily-brief` → **Delete**.
2. **Telegram webhook** (optional but tidy — stops Telegram from trying to call a URL you're about
   to delete):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
   ```
3. **Function URL:** Console → Lambda → `agent-host` → Configuration → Function URL → **Delete**.
4. **Lambda function:** Console → Lambda → select `agent-host` → **Actions** → **Delete** → type
   `confirm` and click **Delete**.
5. **DynamoDB table:** Console → DynamoDB → Tables → select `agent_host` → **Delete** → type
   `confirm` → **Delete table**. (This permanently deletes all stored chat memory and dedup
   state — there's no undo.)
6. **IAM roles** — delete both roles now nothing references them:
   - `agent-host-lambda-role` (the Lambda's own role from Section 3).
   - The Scheduler's auto-created execution role from Section 8 step 10, named something like
     `Amazon_EventBridge_Scheduler_LAMBDA_<random-suffix>`. Console → IAM → Roles → search
     "scheduler" or "eventbridge" to find it. (Deleting the schedule in step 1 above does **not**
     delete this role automatically — it's a separate resource.)

   For each: select the role → **Delete** → type the role name to confirm → **Delete**.

**Confirm it worked:** re-run
`aws lambda get-function --function-name agent-host` and
`aws dynamodb describe-table --table-name agent_host` — both should now return a "not found"
error (`ResourceNotFoundException`), confirming nothing billable is left running.
