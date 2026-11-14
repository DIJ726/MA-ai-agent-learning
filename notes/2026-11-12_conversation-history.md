# 学习笔记 · 周四 11/12 — 多轮对话上下文管理（history 记忆）

> 学习目标：搞懂大模型为什么"记不住"、多轮历史在请求里长什么样、怎么用滑动窗口把上下文控制在窗口内。

---

## 一、大模型本身没有记忆

每次 API 调用都是**无状态**的：模型不会记得上一次跟你聊过什么。所谓"多轮对话"，本质是**你把之前的对话历史重新塞进 messages 列表，一起发给模型**。

OpenAI / 千问兼容格式的三种角色：

| role | 作用 | 谁写的 |
|------|------|--------|
| `system` | 设定人设、规则、输出格式，最靠前 | 程序固定写 |
| `user` | 用户说的话 | 用户输入 |
| `assistant` | 模型上一轮的回答 | 程序存下来，下一轮带上 |

一轮完整对话在 messages 里就是两条：`user` + `assistant`。

---

## 二、为什么不能把全部历史都带上

1. **上下文窗口有限**：qwen-plus 128K token，但越长越慢、越贵（见周二笔记）
2. **注意力衰减**：历史太长时，模型对中间内容的关注度下降（"大海捞针"问题）
3. **成本线性增长**：第 10 轮的输入 token ≈ 前 9 轮历史 + 本轮问题，每轮都在重复付费
4. **可能触发限流**：TPM（每分钟 token 数）超限会被拒绝

所以要做**上下文管理**：只带最近的几轮，老的丢掉。

---

## 三、滑动窗口（我们今天的实现）

`chat_with_llm.py` 新增 `Conversation` 类：

```python
conv = Conversation(system_prompt="你是助手", max_turns=20)
conv.add_user("北京天气")
reply = chat_completion(conv.to_messages())
conv.add_assistant(reply)
```

`to_messages()` 的截断规则：
- 只保留最近 `max_turns` 轮（一轮 = user + assistant 两条）
- system 永远置顶，不参与截断
- 如果截断后第一条是 assistant（孤儿回答），丢掉它，保证序列从 user 开始

```python
def to_messages(self) -> list[dict]:
    recent = self._history[-2 * self.max_turns:]
    if recent and recent[0]["role"] == "assistant":
        recent = recent[1:]
    messages = [{"role": "system", "content": self.system_prompt}] if self.system_prompt else []
    messages.extend(recent)
    return messages
```

**自测结果**（25 轮历史、max_turns=5）：截断后剩 10 条历史，system 置顶，首条 user、末条 assistant，序列成对不残缺。

---

## 四、更高级的上下文策略（了解即可，后面会用到）

| 策略 | 做法 | 适用 |
|------|------|------|
| **滑动窗口**（今天） | 只留最近 N 轮，老的直接丢 | 短对话、简单助手 |
| **历史摘要** | 老对话先让模型压缩成一段摘要，摘要 + 最近 N 轮一起发 | 长对话、需要保留早期结论 |
| **外部记忆库** | 关键信息存数据库/向量库，每轮按需检索相关片段（RAG） | 第 6 周要学的 RAG |
| **变量抽取** | 把"用户城市""日期"等结构化信息单独存字段，不靠自然语言历史 | 我们天气助手就是这么做的（cities/dates 单独提取） |

---

## 五、我们项目里的三处历史管理对照

| 文件 | 历史存储 | 截断 |
|------|---------|------|
| `weather_assistant.py` 网页版 | `ChatHandler.conversation` 列表 | `history[-MAX_HISTORY_TURNS:]`，20 轮 |
| `stream_chat.py` 终端版 | 改用 `Conversation` 类 | 类内部滑动窗口，20 轮 |
| `Conversation`（新） | `_history` 列表 + system 分离 | 成对截断 + 孤儿 assistant 剔除 |

网页版那套是裸列表切片；终端版今天换成了封装好的 `Conversation`，逻辑更严谨（角色配对、system 分离）。后续网页版也可以迁移到这个类。

---

## 六、常见坑

1. **只存 user 不存 assistant**：模型看不到自己上一轮说了什么，对话会断片
2. **截断后 user/assistant 不成对**：模型收到孤立的 assistant 消息，可能输出格式错乱
3. **system 被当成普通历史截掉**：人设规则丢失，模型行为漂移
4. **失败的轮次也进历史**：API 报错那轮的 user 存下来，下一轮模型看到一个没有回答的问题，容易答非所问（我们终端版出错时 `pop()` 撤掉了这条）

---

## 七、今天的代码产物

- `chat_with_llm.py`：新增 `Conversation` 类（add_user / add_assistant / to_messages / clear / __len__）
- `stream_chat.py`：裸 history 列表 → `Conversation` 类
- 纯逻辑自测通过：截断、配对、孤儿剔除、空历史四种情况
