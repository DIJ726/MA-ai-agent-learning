# 学习笔记 · 周三 11/11 — 流式输出 SSE 原理 + 流式对话终端版

> 学习目标：搞懂大模型"边生成边显示"背后的 SSE 机制，并在终端里亲手实现一个流式对话。

---

## 一、为什么需要流式输出

不流式（我们之前的 `chat_completion`）：
```
用户提问 → 等模型把整段回答全部生成完 → 一次性返回 → 终端才显示
```
如果回答 300 字、模型要跑 8 秒，这 8 秒用户看着空白屏幕，不知道是卡住了还是在想。

流式（今天的 `chat_stream`）：
```
用户提问 → 模型吐出第 1 个字 → 前端立刻显示
         → 第 2 个字到达 → 追加显示
         → …直到结束
```
**首字延迟（TTFT, Time To First Token）** 通常只有几百毫秒，用户马上看到"在动"，体验像真人打字。

---

## 二、SSE 是什么

**SSE（Server-Sent Events，服务器推送事件）**：基于 HTTP 的一种**服务端 → 客户端单向**长连接推流机制。

- 浏览器/客户端发一个普通 HTTP 请求，带 `Accept: text/event-stream`
- 服务端不立刻 `Content-Length` + 关连接，而是保持连接开着，**一段一段往下写**
- 每段是一行事件，格式固定：

```
data: {"choices":[{"delta":{"content":"你"}}]}

data: {"choices":[{"delta":{"content":"好"}}]}

data: [DONE]

```

要点：
- 每条事件以 `data: ` 开头
- 事件之间用**空行**分隔
- 最后一条 `data: [DONE]` 表示流结束
- 响应头：`Content-Type: text/event-stream`

我们调的千问（OpenAI 兼容接口）就是这套格式，只是把 SSE 的发送端换成了模型 API。

---

## 三、SSE vs WebSocket

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 方向 | **单向**：服务端 → 客户端 | **双向**：两边都能随时发 |
| 协议 | 普通 HTTP 长连接 | 独立的 ws:// 协议，要握手升级 |
| 连接 | 客户端发请求后保持 | 建立后全双工 |
| 断线重连 | 浏览器原生自动重连 | 需要自己写重连 |
| 适合场景 | 模型流式输出、通知推送、日志滚动 | 实时聊天、游戏、协同编辑 |

**大模型对话选 SSE 的原因**：回答本来就是"模型说、用户听"的单向流，不需要 WebSocket 那种双向；SSE 走普通 HTTP，代理/防火墙/调试工具都友好，不用维护额外协议。

---

## 四、代码里怎么落地（今天的实现）

### 1. 请求打开流式开关

`chat_with_llm.py` 的 `chat_stream()`：

```python
payload = {
    "model": LLM_MODEL_NAME,
    "messages": messages,
    "temperature": temperature,
    "stream": True,          # ← 关键：告诉 API 走 SSE 流
}

resp = requests.post(url, headers=headers, json=payload,
                     timeout=..., stream=True)   # ← requests 不一次性读 body
```

### 2. 逐行解析 SSE

```python
for raw_line in resp.iter_lines(decode_unicode=True):
    line = raw_line.strip()
    if not line.startswith("data:"):
        continue
    data_str = line[len("data:"):].strip()
    if data_str == "[DONE]":
        break
    chunk = json.loads(data_str)
    text = chunk["choices"][0].get("delta", {}).get("content")
    if text:
        yield text            # ← 每段文本作为生成器产出
```

**关键点**：
- `iter_lines()` 按行读，不等待整个响应结束
- `delta.content` 是这一段新增的文字（不是完整回答，是增量）
- 生成器 `yield` 把每段推给调用方，调用方拿到一段打一段

### 3. 终端版消费生成器

`stream_chat.py`：

```python
for chunk in chat_stream(messages, temperature=0.7):
    answer_parts.append(chunk)
    sys.stdout.write(chunk)
    sys.stdout.flush()       # ← 必须 flush，否则缓冲在内存里不显示
```

`flush=True` 很容易漏：stdout 默认有行缓冲，不 flush 的话字会攒着，直到一行结束才一起显示，就失去流式效果了。

---

## 五、实测结果

跑 `chat_stream` 问"用两句话介绍大模型"：
- 收到 **21 个片段**，合计 157 字符
- 终端版脚本喂输入后正常输出回复、正常退出

---

## 六、和我们项目的对应关系

| 文件 | 模式 | 说明 |
|------|------|------|
| `chat_completion()` | 一次性 | 网页版天气助手用，要解析成卡片，必须等完整 JSON |
| `chat_with_usage()` | 一次性 + 返回 token | 成本估算用 |
| `chat_stream()` | 流式 SSE | 今天新增，适合长回答的对话体验 |
| `stream_chat.py` | 终端流式演示 | 今天新增，纯命令行多轮对话 |

**什么时候该用流式**：面向用户的长文本回答（聊天、写作、总结）。
**什么时候不该用**：需要结构化 JSON 给程序解析的场景（卡片、提取），流式 JSON 拼装复杂，还是等完整响应更稳。

---

## 七、后续可以做

- 网页版天气助手也可以接流式：前端 `fetch` 读 SSE 流，边收边把气泡刷出来（第 2 周"流式对话"检查点相关）
- 流式响应末尾 API 可能带 usage，可顺手存进 `chat_stream` 的最终返回
- 给终端版加 Markdown 渲染、退出时保存对话记录
