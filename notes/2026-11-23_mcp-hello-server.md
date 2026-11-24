# 学习笔记 · 周一 11/23 — MCP 概念 + SDK 安装 + Hello Server（第 4 周开周）

> 第 4 周任务：掌握 MCP（Model Context Protocol），让 Agent 标准化调用外部工具。今天先搞清 Host/Client/Server 角色与三种原语，装好官方 Python SDK，用官方写法跑通一个最小 hello server。

---

## 一、MCP 是什么（一句话版本）

**MCP 是给 LLM 应用接外部工具/数据的标准化协议**：Server 把能力（函数、文件、提示模板）暴露出来，Host 里的 Client 按协议发现并调用，两边不用各自私对接。

### 三个角色

| 角色 | 含义 | 对应本项目 |
|------|------|-----------|
| Host | LLM 应用本体，和用户对话的一方 | 我们的 Agent 运行时 / IDE / Claude 这类产品 |
| Client | 住在 Host 里、说 MCP 协议的一侧；Host 给每个 Server 起一个 Client | 后面要写的 `mcp_client` |
| Server | 用 SDK 写的一侧，向外暴露能力，**不直接和模型对话** | 今天的 `mcp_hello_server.py` |

### 三种原语（分界是谁决定调用）

| 原语 | 控制方 | 类比 | 装饰器 |
|------|--------|------|--------|
| Tool | 模型决定调用的动作函数 | HTTP POST（做事、可能有副作用） | `@server.tool()` |
| Resource | 应用决定喂进上下文的数据 | HTTP GET（只读加载） | `@server.resource("uri")` |
| Prompt | 用户按名字选的消息模板 | 斜杠命令 | `@server.prompt()` |

## 二、SDK 安装与导入路径

- 官方包名：`mcp`（本轮版本 2.3.0）。
- 两条导入路径（**没有** `from mcp import MCPServer`）：

```python
from mcp import Client              # Client：Host 侧，连 Server
from mcp.server import MCPServer   # Server：我们写的一侧
```

- 注册工具：装饰器即全部注册逻辑；工具名、描述、参数 schema 都从函数本身读：
  - 函数名 → 工具名；docstring → 工具描述；类型提示 → 参数 schema。

```python
server = MCPServer("hello-agent")

@server.tool()
def add(a: int, b: int) -> int:
    """把两个整数相加，返回它们的和。"""
    return a + b
```

## 三、验证结果

### 1. 内存 Client 直连（自测，不起端口）

SDK 测试夹具：直接把 Server 对象交给 `Client(server)`，不走网络。

- `list_tools` 列出 `greet`、`add` 两个工具；`input_schema` 由类型提示生成（`a`/`b` 为 integer、required）。
- `call_tool("add", {"a": 1, "b": 2})` → `3`。
- `call_tool("greet", {"name": "World"})` → `你好，World！欢迎来到 MCP。`

### 2. HTTP 远程 Client（streamable-http）

- 启动：`python mcp_hello_server.py http`，监听 `127.0.0.1:8767`，路径 `/mcp`。
- 外部 Client 连 `http://127.0.0.1:8767/mcp`：`list_tools` 正常，`add(4,5)` → `9`，`greet("MCP")` 回显正确。

## 四、踩坑清单与解法

### 1. 编译 wheel 解压反复 `WinError 32`（文件被占用）

- 现象：pip 在解压编译 wheel 阶段报 `另一个程序正在使用此文件`，先后卡在 `pywin32`、`pydantic_core` 等；纯 Python 小包不受影响。判断是实时防护扫描刚写入的 wheel 把文件锁住。
- 绕行方案（同 ABI 复用兼容包）：
  - 项目 `.venv` 与 anaconda 全局都是 CPython 3.13，编译扩展可直接复制；把 anaconda 的 `pydantic` / `pydantic_core` / `rpds` / `jsonschema` / `referencing` / `jwt` 等目录与对应 `.dist-info` 复制进 `.venv\Lib\site-packages`。
  - 再 `pip install mcp`，编译依赖已满足，只下载剩余纯 Python wheel（`starlette`、`uvicorn`、`sse-starlette`、`mcp`、`mcp-types`、`httpx2`、`httpcore2`、`truststore`、`opentelemetry-api`、`python-multipart`、`pyjwt` 等）。
  - 安装时把临时目录与 pip 缓存迁到项目内（`E:\VSagent\tmp`、`E:\VSagent\tmp\pipcache`），避开用户 Temp 的占用。
- 备注：华为云镜像索引里没有 `mcp` 包；清华源可用但带宽低，大 wheel 需耐心。

### 2. 字段名是蛇形，不是 OpenAI 式驼峰

- `CallToolResult` / `Tool` 对象上读得到的是 `is_error`、`input_schema`，不是 `isError`、`inputSchema`；直接取驼峰名会 `AttributeError`，报错提示里会给出正确蛇形名。

### 3. 模块 docstring 内不能再嵌套三引号

- 在模块顶层 `"""..."""` 文档串里写示例代码时，内层若再用 `"""docstring"""`，会让外层文档串在第一个内层三引号处提前闭合，报 `SyntaxError: invalid character '；'`。示例里的函数说明改用注释行即可。

## 五、代码结构现状

| 文件 | 职责 |
|------|------|
| `mcp_hello_server.py` | 最小 MCP Server：`greet` / `add` 两个工具；`test` 内存自测、`stdio`、`http`(8767) 三种运行模式 |
| `requirements.txt` | 新增直接依赖 `mcp>=2.3.0` |

端口避让记录：8765 被其他进程绑定占用，天气助手用 8766；MCP hello 用 **8767**，订单库 Server 用 **8768**。

## 六、边界（留给后续，今天不做）

- 周三：写 MCP Client，连上自己的 Server，体验「发现工具 → 调用工具」的完整链路。
- 周四：暴露 Resource（把知识库 PDF 作为 resource）与 Prompt。
- 周五：把订单库 MCP 工具接进第 3 周的 ReAct Agent。

## 七、面试话术（可直接背）

> MCP 是 LLM 应用接外部工具的标准协议：Server 暴露工具/数据/提示模板，Host 里的 Client 按协议发现和调用。我用官方 Python SDK 写过最小 Server，工具用装饰器注册，参数 schema 从类型提示自动生成；测试可以把 Server 对象直接交给内存 Client 自测，也可以起 streamable-http 让远程 Client 连。Server 只负责封装能力，不直接和模型对话。
