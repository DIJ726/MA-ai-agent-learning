# 我用 Cursor 搭第一个调大模型的脚本

> 博客草稿 · 2026-11-15 · 第 2 周学习产物
> 目标平台：掘金 / 知乎（发布时任选其一）。本文基于本人 8 周 AI Agent 学习计划第 2 周的真实练习代码整理。

---

## 引子

开始学 Agent 的第二周，我想先把最底层的一件事跑通：**用 Python 脚本直接调一次大模型，拿到回复**。

听起来简单，但第一次把"代码 → HTTP 请求 → 模型 → 文本回复"这条链路完整走一遍，才搞明白聊天框背后到底发生了什么。这篇笔记记录全过程：怎么拿 Key、怎么发第一个请求、怎么读返回结果，以及踩过的几个坑。

> 说明：本文用的是千问（Qwen）OpenAI 兼容接口，换成 DeepSeek、GPT 等其他兼容 OpenAI 格式的服务，代码结构完全一样，只改地址、Key 和模型名。

## 一、准备工作

### 1. 拿到 API Key
在模型平台控制台创建一个 API Key，保存好（只显示一次）。Key 等同于密码，**不要写进代码、不要提交到 Git**，统一放在 `.env` 文件里。

### 2. 安装依赖
只需要一个 `requests`：

```bash
pip install requests
```

### 3. 项目里放一个 `.env`
```bash
# .env（已在 .gitignore 里忽略）
DASHSCOPE_API_KEY=sk-你的key
```

## 二、最小调用：五条消息结构看懂一次对话

OpenAI 兼容接口的请求体核心是 `messages` 列表，每条消息有 `role` 和 `content`：

| role | 作用 |
|------|------|
| `system` | 设定人设和规则，最靠前 |
| `user` | 用户说的话 |
| `assistant` | 模型上一轮的回答（多轮对话才需要） |

最小脚本：

```python
# llm_demo.py
import os
import requests
from pathlib import Path

# 手写解析 .env（避免额外装 python-dotenv）
def load_env(path: str = ".env") -> dict:
    config = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            config[k.strip()] = v.strip()
    return config

env = load_env()
API_KEY = env["DASHSCOPE_API_KEY"]
BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"
MODEL = "qwen-plus"

messages = [
    {"role": "system", "content": "你是一个简洁的中文助手，回答不超过两句话。"},
    {"role": "user", "content": "用一句话说明：大语言模型本身有没有记忆？"},
]

resp = requests.post(
    f"{BASE_URL}/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
    },
    timeout=30,
)
resp.raise_for_status()

body = resp.json()
reply = body["choices"][0]["message"]["content"]
print(reply)
print("token 用量:", body.get("usage"))
```

运行：

```bash
python llm_demo.py
```

成功输出（实测）：

```
助手: 大语言模型本身没有长期记忆，每次推理仅依赖输入的上下文和训练时学到的统计规律。
但可通过外部机制（如向量数据库、对话历史缓存）模拟记忆功能。

token 用量: {'prompt_tokens': 36, 'total_tokens': 80, 'completion_tokens': 44, 'cached_tokens': 0}
```

## 三、看懂返回结构

响应 JSON 的关键路径：

```
body
├─ choices[0].message.content   # 模型回复文本（我们要的东西）
├─ usage
│   ├─ prompt_tokens            # 输入 token 数
│   ├─ completion_tokens        # 输出 token 数
│   └─ total_tokens             # 合计
└─ model / id / created ...      # 元信息
```

`usage` 很重要：它是算成本和控制上下文长度的依据（后面写用量统计、做上下文截断都靠它）。

## 四、三个必加的健壮性处理

第一版能跑之后，实际使用时补了三件事：

### 1. 失败重试
网络抖动、限流偶发失败，重试 2–3 次比直接报错友好。用装饰器包住请求函数即可（`requests.RequestException` 和运行时错误才重试）。

### 2. 超时
`timeout=30` 一定要给，否则网络卡住时脚本会一直挂着。

### 3. 结构化输出 + 解析降级
需要模型返回 JSON 时，请求里加：

```python
payload["response_format"] = {"type": "json_object"}
```

收到文本后 `json.loads` 解析；解析失败就重试，多次失败再降级成普通自然语言回复，保证用户总能看到内容，而不是直接报错。

## 五、踩过的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| `401 Unauthorized` | Key 写错、过期，或 Key 和端点不配套 | 核对 Key；确认端点和 Key 属于同一平台 |
| 返回格式异常 / KeyError | 响应不是预期结构（报错页、限流页） | 先打印 `resp.text`，解析前判空 |
| JSON 解析失败 | 模型输出带了代码块包裹或多余文字 | JSON mode + 重试；失败降级纯文本 |
| 历史越攒越长、越来越慢 | 每轮都把全部历史带上 | 滑动窗口只保留最近 N 轮（下一篇讲） |

## 六、这一步离 Agent 还有多远

这个脚本只是**一问一答**：你发问题，模型回一句，结束。它还不具备 Agent 的核心特征——自主规划、调用工具、多轮循环决策。

接下来第 3 周的计划：在这个最小调用之上手写一个最小 ReAct Agent，补上
- **工具（执行）**：让模型能调用计算器、查时间这类函数；
- **规划（循环）**：Thought → Action → Observation 反复推进；
- **记忆**：用字典/历史列表跨轮保存上下文。

到那时，才真正从"调模型"走到"做 Agent"。

## 附：项目里的相关文件

- `llm_demo.py`：本文最小调用 demo
- `chat_with_llm.py`：封装好的调用层（普通对话 / 用量 / 流式 / JSON 重试 / 历史类）
- `stream_chat.py`：终端流式对话脚本
- `token_cost.py`：token 计数与成本估算

---

*草稿备注（发布前删）：配图可放 ① 请求/响应流程截图 ② 终端运行结果截图 ③ 目录树；标签建议：Python、大模型、Qwen、AI Agent、OpenAI 兼容接口。*
