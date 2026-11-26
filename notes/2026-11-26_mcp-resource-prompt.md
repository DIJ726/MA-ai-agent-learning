# 学习笔记 · 周四 11/26 — MCP Resource 与 Prompt（第 4 周）

> 今天任务：掌握 MCP 另外两种原语——**Resource（资源）** 与 **Prompt（提示）**；编码上把知识库作为 MCP resource 暴露。

---

## 一、三种 MCP 原语回顾

| 原语 | 谁决定使用 | 类比 HTTP | 典型内容 |
|------|-----------|-----------|----------|
| Tool（工具） | 模型决定调用 | POST | 查订单、做计算等可执行动作 |
| Resource（资源） | 应用决定喂进上下文 | GET | 文件、知识库、接口响应等被动数据 |
| Prompt（提示） | 用户按名字选用 | 斜杠命令 | 可复用的消息模板 |

分界要点：Resource 是被动数据，不能产生动作；要执行动作应注册成 Tool。Prompt 只组织消息模板，不直接访问数据库。

## 二、Resource 写法（SDK 2.3.0）

```python
from mcp.server import MCPServer

server = MCPServer("knowledge-base")

@server.resource(
    "knowledge://agent-guide",          # URI，静态资源无 {参数}
    name="agent-guide",
    title="标题",
    description="说明",
    mime_type="text/markdown",          # text/markdown、application/pdf 等
)
def read_agent_guide() -> str:          # 文本资源返回 str
    return Path("data/knowledge/agent-guide.md").read_text(encoding="utf-8")
```

- **文本资源**：函数返回 `str`，`mime_type` 为 `text/*`，读取结果在 `content.text`。
- **二进制资源（PDF 这类 blob）**：函数返回 `bytes`，`mime_type="application/pdf"`，读取结果在 `content.blob`。
- URI 带 `{param}`（如 `resource://{city}`）会注册成**资源模板**，函数参数与 URI 变量一一对应；静态 URI 的函数不能带参数。

## 三、Prompt 写法（SDK 2.3.0）

```python
@server.prompt(
    name="knowledge_qa",
    title="知识库问答提示词",
    description="基于知识库回答某主题。",
)
def knowledge_qa(topic: str) -> str:     # topic 由类型提示变成 PromptArgument
    return f"请基于知识库回答「{topic}」：先引用依据，再给结论。"
```

- 函数返回裸字符串会被包成一条 user 消息；也可返回消息列表。
- 函数参数自动生成提示参数（`PromptArgument`）：必填无默认值，带默认值为可选。
- Client 侧：`list_prompts()` 列模板；`get_prompt(name, arguments)` 渲染出消息列表。

## 四、本次编码产物

| 文件/资源 | 说明 |
|-----------|------|
| `mcp_knowledge_server.py` | 知识库 MCP Server：2 个 Resource + 2 个 Prompt，含内存自测 |
| `data/knowledge/agent-guide.md` | 知识库文本素材（Resource 的文本载体，后续也可作 RAG 语料） |

Resource：
- `knowledge://agent-guide`：Markdown 文本（`text/markdown`）。
- `knowledge://agent-guide.pdf`：PDF 二进制 blob（`application/pdf`）；文件暂缺时返回空 bytes 兜底，不抛异常。把真实 PDF 放到该路径即自动作为二进制资源暴露。

Prompt：
- `knowledge_qa(topic)`：基于知识库问答，先引用依据再给结论。
- `order_insight(customer)`：结合订单数据对客户做点评与跟进建议。

## 五、验证结果（内存直连自测）

- `list_resources`：两个 `knowledge://` 资源均在列，MIME 类型正确。
- `read_resource("knowledge://agent-guide")`：取回知识库文本，含 `ReAct`、`MCP` 等核心概念。
- `read_resource("knowledge://agent-guide.pdf")`：文件暂缺，blob 为 0 字节，未抛异常。
- `list_prompts`：`knowledge_qa`、`order_insight` 均在列，参数分别为 `topic`、`customer`。
- `get_prompt("knowledge_qa", {"topic": "MCP"})`：消息文本正确替换出「MCP」；`order_insight(customer=张伟)` 正确替换出「张伟」。
- 回归：`mcp_order_server.py`、`mcp_hello_server.py` 既有自测仍通过，未改动无关文件。

## 六、踩坑清单与解法

### 1. 字段一律蛇形

- 资源/提示读取用 `content.text`、`content.blob`、`mime_type`、`input_schema`、`is_error`；驼峰名（`blob` 以外的 `inputSchema`、`isError`）取不到。

### 2. 文本资源与二进制资源不要混用返回类型

- 文本资源返回 `str`；PDF 等二进制返回 `bytes`。混用会让读取方拿错字段。

### 3. 静态资源函数不能带参数

- URI 里没有 `{变量}` 时，函数签名带参数会在注册时报错；需要参数化就读成资源模板（URI 带 `{param}`）。

### 4. PDF 素材与解析依赖

- 本仓库虚拟环境未装 PDF 生成/解析库，今天不引入新依赖；二进制资源先按约定路径预留，缺失走空 blob 兜底。后续若接入真实 PDF，再按需加解析库。

## 七、代码结构现状（第 4 周）

| 文件 | 职责 |
|------|------|
| `mcp_hello_server.py` | 最小 hello Server（Tool） |
| `mcp_order_server.py` | 订单库查询 Server（Tool，SQLite） |
| `mcp_client.py` | MCP Client：发现工具 + 调用工具 |
| `mcp_knowledge_server.py` | 知识库 Resource + Prompt（今天） |

## 八、边界（留给后续）

- 周五：`mcp_db_tool.py`，把订单 MCP 工具接进第 3 周 ReAct Agent，让 Agent 通过 MCP 查数据库。
- 周六：用 MCP Inspector 抓一次完整调用链路（Tool / Resource / Prompt）。
- Resource 目前是整份文本；后续可结合 RAG 做切块与检索，按需暴露片段。

## 九、面试话术（可直接背）

> 我在练习项目里除了 Tool，还实现了 MCP 的 Resource 和 Prompt：把知识库 Markdown 作为文本资源、PDF 作为二进制资源暴露给 Client 读取；又注册了带参数的提示模板，Client 按名字取用并渲染成消息。Resource 负责喂上下文数据，Tool 负责执行动作，Prompt 负责组织模板，三者通过标准协议解耦。
