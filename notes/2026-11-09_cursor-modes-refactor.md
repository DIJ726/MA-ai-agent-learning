# 学习笔记 · 周一 11/09 — Cursor 三模式（Chat / Composer / Agent）+ 重构提效点

> 学习目标：搞清 Cursor 官方文档里的三种 AI 交互模式，以及怎么用它把第 1 周的代码重构得更干净。

---

## 一、三种模式对比（来自 cursor.com/docs）

| 模式 | 快捷键 | 干什么 | 什么时候用 |
|------|--------|--------|-----------|
| **Chat** | `Ctrl+L` | 侧边栏对话，你选一段代码贴进去问/让它改 | 问问题、解释代码、小修小补 |
| **Composer** | `Ctrl+I` | 跨文件一次性生成/修改多个文件，能看整个项目上下文 | 新功能、重构、一次改多个文件 |
| **Agent** | Chat 里切 Agent 模式 | 自己读文件、跑命令、改代码、根据报错再改，循环直到搞定 | 复杂任务、跑测试、修 bug、跑构建 |

一句话记忆：
- Chat = 问答
- Composer = 一次写一堆文件
- Agent = 给它一个目标，它自己折腾到完

---

## 二、今天的重构我们是怎么做的（对照 Cursor 思路）

今天实际的重构动作（由我直接执行，等价于 Cursor Composer 模式）：

1. **把 LLM 调用层从 weather_assistant.py 抽出来**，变成独立的 `chat_with_llm.py`
   - 抽走：`chat_completion`、`retry`、`timed`、`load_env`、`DASHSCOPE_KEY`
   - 新增：高层 `chat_json()`（自动重试 + 降级）
2. **weather_assistant.py 改成 import 这个模块**
   - 文件从 35086 bytes → 32434 bytes（净减 2.6KB 重复代码）
   - 业务逻辑（天气查询、网页服务）和 LLM 调用解耦
3. **跑通自测**：
   - `python chat_with_llm.py`：普通对话 + JSON 提取都正常
   - 重启服务后实测"北京明天"：完整卡片照常返回

### 如果用 Cursor 做，提示词大概会这么写

在 Composer 里选中整个 `weather_assistant.py`，然后：

> 把所有跟千问大模型调用相关的代码（chat_completion、retry、timed、load_env、DASHSCOPE_KEY、LLM_BASE_URL/LLM_MODEL_NAME）抽到一个新文件 `chat_with_llm.py`。
> 在 chat_with_llm.py 里加一个 `chat_json(messages)` 函数：先 json_mode=True 调 3 次，json.loads 解析；都失败则降级成 json_mode=False 的普通对话。
> weather_assistant.py 改成 `from chat_with_llm import ...`，不要重复定义。
> 改完跑一次 `python -c "import ast; ast.parse(open('weather_assistant.py').read())"` 确认语法。

---

## 三、重构的提效点（今天实际感受到的）

1. **可复用**：以后任何项目（下周的 min_agent、再下周的 MCP server）只要 `from chat_with_llm import chat_completion, chat_json`，不用再写一遍 HTTP 调用。
2. **职责清晰**：weather_assistant.py 现在只关心"天气业务"，千问相关的坑都在 chat_with_llm.py 里修。
3. **可独立测试**：`python chat_with_llm.py` 直接跑自测，不用起整个 web 服务。
4. **改 LLM 不影响业务**：以后换模型（qwen-plus → qwen-max）只改 chat_with_llm.py 一行常量。
5. **git diff 更干净**：每次改业务逻辑和改 LLM 调用是两个 commit，历史可读性好。

---

## 四、Cursor 还没装，但学习路径明确

你电脑上目前没装 Cursor。如果要装：
- 官网：https://cursor.com （免费版够学习用）
- 装好后用你现有的 GitHub 账号登录，直接打开 `E:\VSagent\DY` 项目
- 第一次建议按这个顺序试：
  1. `Ctrl+L` 打开 Chat，问它"解释一下 weather_assistant.py 里 answer() 函数的流程"
  2. 选中 `extract_cities_and_dates` 函数，让 Chat 帮你写单测
  3. `Ctrl+I` 打开 Composer，让它给 chat_with_llm.py 加一个"流式输出"方法
  4. 最后试 Agent 模式："跑一遍 weather_assistant.py 的所有自测，把报错修好"

---

## 五、今天的 commit 预告

- 新文件 `chat_with_llm.py`（约 170 行）
- 改 `weather_assistant.py`（删重复、加 import、净减 2.6KB）
- 新增 `notes/` 目录（两份学习笔记）
