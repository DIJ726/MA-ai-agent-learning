# 学习笔记 · 周五 11/13 — Cline 插件（VS Code 内 AI Agent）+ 小重构任务

> 学习目标：了解 Cline 这种"IDE 内 AI Agent"和普通聊天助手的区别，知道怎么安装配置，并实际完成一个小重构。

---

## 一、Cline 是什么

**Cline**（原名 Claude Dev）是 VS Code 里的 AI Agent 扩展。和 Cursor 一样能读代码、改代码，但它的定位是**自主执行**：你给一个目标，它自己读文件 → 改代码 → 跑命令 → 看报错 → 再改，循环到任务完成。

和普通 AI 聊天的区别：

| | AI 聊天 / Chat 面板 | Cline Agent |
|---|---|---|
| 读文件 | 你手动贴进去 | 自己扫项目目录 |
| 改代码 | 给你代码片段，你复制 | 直接在文件里改，diff 给你确认 |
| 跑命令 | 不能跑 | 自己执行终端命令（测试、构建） |
| 纠错 | 你反馈它再答 | 看报错自己修 |

它能干我们学习计划里说的"小重构任务"：比如"把这段私有属性访问封装成公开方法"。

---

## 二、安装步骤（本机 VS Code 目前未装，按此操作即可）

1. 装 VS Code：https://code.visualstudio.com
2. 打开扩展面板（`Ctrl+Shift+X`），搜索 **Cline**（作者 saoudrizwan），安装
3. 首次打开侧边栏 Cline 面板，配置 API：
   - API Provider 选 **OpenAI Compatible**（千问提供 OpenAI 兼容接口）
   - Base URL：填 `.env` 里的 `LLM_BASE_URL`（`https://ws-...maas.aliyuncs.com/compatible-mode/v1`）
   - API Key：填 `DASHSCOPE_API_KEY`
   - Model：`qwen-plus`
4. 打开项目文件夹 `E:\VSagent\DY`，就能让它读我们的代码

> 界面随版本更新可能略有差异；找不到 OpenAI Compatible 就选"OpenAI"，填同一个 Base URL。

---

## 三、今天实际完成的小重构

### 重构前的问题

`stream_chat.py` 出错回退时直接访问了 `Conversation` 的私有属性：

```python
conv._history.pop()   # ← 私有属性，外部不该碰
```

封装被破坏：哪天 `Conversation` 内部存储结构改了（比如换成 deque、加缓存），终端脚本就会跟着崩。

### 重构后

`Conversation` 暴露公开方法：

```python
def pop_last(self) -> Optional[dict]:
    """撤回最后一条历史（API 调用失败时用，避免留下没有回答的 user 消息）。"""
    return self._history.pop() if self._history else None
```

终端脚本只调用公开接口：

```python
conv.pop_last()
```

### 如果让 Cline 做，提示词这么写

> 在 `chat_with_llm.py` 的 `Conversation` 类里加一个公开方法 `pop_last()`，撤回最后一条历史消息并返回它；历史为空时返回 None。然后把 `stream_chat.py` 里对 `conv._history.pop()` 的直接访问改成调用 `conv.pop_last()`。改完跑 `python -c "import ast; ast.parse(open('stream_chat.py').read())"` 确认语法。

Cline 会自己找到两个文件、改完、跑语法检查。

---

## 四、重构提效点（今天实际感受到的）

1. **封装边界清晰**：外部只看得到 `add_user / add_assistant / pop_last / to_messages`，内部 `_history` 怎么存是类自己的事
2. **空历史安全**：`pop_last()` 对空历史返回 None，不会抛 IndexError（已自测：空历史 pop 返回 None）
3. **改动面小**：只动两行，行为不变，回归风险低
4. **可测试**：Conversation 的截断、配对、撤回都能脱离大模型纯逻辑自测

---

## 五、Cline vs Cursor（周三笔记的补充）

| 维度 | Cursor | Cline |
|------|--------|-------|
| 形态 | 独立 IDE | VS Code 扩展 |
| 强项 | 编辑器内补全、Composer 多文件生成 | 自主跑命令、按任务闭环执行 |
| 适合 | 日常写代码 | 给目标、让它自己重构/修 bug/跑测试 |
| 价格 | 付费订阅为主 | 自带模型额度，也可接自己的 API Key（我们接千问） |

学习计划第 2 周要求"全程用 Cursor/Cline 编码"。两个任选其一即可；你已经有千问 API Key，接 Cline 走 OpenAI Compatible 最省事。

---

## 六、自测清单

- [x] `pop_last()` 撤回最后一条消息，返回该消息 dict
- [x] 空历史调用 `pop_last()` 返回 None，不报错
- [x] `stream_chat.py`、`weather_assistant.py`、`chat_with_llm.py` 全部 import 通过
- [ ] （本机待做）装 VS Code + Cline，按第三节提示词让它复跑这个重构，对比 AI 改法
