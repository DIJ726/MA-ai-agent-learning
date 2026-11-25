# 学习笔记 · 周三 11/25 — MCP Client 发现并调用自己的 Server（第 4 周）

> 今天任务：从 Client 侧走通 MCP 链路——先 `list_tools` 发现 Server 暴露了哪些工具（名称、描述、参数 schema），再 `call_tool` 传参调用，取回结果文本。

---

## 一、Client 与 Server 的分工

| 侧 | 文件 | 职责 |
|----|------|------|
| Server | `mcp_order_server.py` / `mcp_hello_server.py` | 把能力注册成工具，不直接和模型对话 |
| Client | `mcp_client.py`（今天） | 住在 Host 里，按协议连接 Server，发现工具并发起调用 |

一条 MCP 调用链：Host 内 Client →（协议）→ Server 工具函数 → 结果文本回填给 Client。

## 二、关键写法

### 1. 两种连接方式

```python
from mcp import Client

# 内存直连：测试夹具，不起端口、不走网络
async with Client(server) as client:
    ...

# HTTP：连一个已启动的 streamable-http Server
async with Client("http://127.0.0.1:8768/mcp") as client:
    ...
```

### 2. 发现工具：`list_tools`

```python
tools = await client.list_tools()
for t in tools.tools:
    t.name          # 工具名
    t.description   # 来自函数 docstring
    t.input_schema  # 来自类型提示
```

Client 不预先知道有哪些工具，连上后先 `list_tools` 拿到清单和参数 schema，再决定调哪个。

### 3. 调用工具：`call_tool`

```python
result = await client.call_tool("get_order", {"order_id": "SO-20261101"})
result.is_error   # 是否调用报错（蛇形，不是 isError）
result.content    # 内容列表，逐项取 .text
```

- 结果文本从 `content` 列表逐项取 `text`；调用报错（`is_error=True`）时抛异常，不静默吞掉。

### 4. 本文件结构

| 函数 | 作用 |
|------|------|
| `discover_tools(client)` | `list_tools` 列工具，打印名称/描述/schema，返回工具列表 |
| `call_tool(client, name, arguments)` | 按名传参调用，返回结果文本；报错抛异常 |
| `_self_test()` | 内存直连订单 Server，发现 + 调用断言 |
| `_run_http(url)` | 连已启动的 HTTP Server，演示发现 + 调用 |

## 三、验证结果

### 内存自测（`python mcp_client.py test`）

- 发现 `get_order`、`list_orders` 两个工具，schema 正确。
- `get_order("SO-20261101")` → 张伟 / 机械键盘 K87 / ¥329.00 / 已完成。
- `list_orders(status="已完成")` → 只返回已完成的 2 单，不含待支付。

### HTTP 远程（`127.0.0.1:8768/mcp`）

- 后台起订单 Server 后，Client 连接：`list_tools`、`call_tool` 的 POST 均 `200 OK`。
- `get_order("SO-20261103")` → 张伟 / 显示器支架 / ¥189.00 / 待支付。

## 四、踩坑清单与解法

### 1. 云端默认装成旧版 mcp，顶层没有 `Client`

- 现象：`pip install mcp` 装到 1.30.0，`from mcp import Client` 报 `cannot import name 'Client'`。
- 解法：对齐本地项目版本，强制 `pip install mcp==2.3.0`；2.3.0 顶层才导出 `Client`，Server 侧用 `from mcp.server import MCPServer`。
- 备注：本项目 `requirements.txt` 固定 `mcp>=2.3.0`，新环境应按此安装。

### 2. 字段蛇形（沿周一/二结论）

- 读取结果用 `result.is_error`、`tool.input_schema`，驼峰名 `isError` / `inputSchema` 取不到。

### 3. HTTP 端点用普通 GET 探测会返回 400

- MCP streamable-http 端点要求特定请求头与 POST 方法；`curl GET` 返回 400 不代表服务没起，应以 Client 实际调用返回 `200 OK` 为准。

## 五、代码结构现状

| 文件 | 职责 |
|------|------|
| `mcp_client.py` | MCP Client：`discover_tools` 发现 + `call_tool` 调用；`test` 内存直连 / `http` 连 8768 |
| `mcp_order_server.py` | 订单库 Server（被 Client 连接的一方） |
| `mcp_hello_server.py` | 最小 hello Server |

## 六、边界（留给后续）

- 周四：暴露 Resource（把知识库 PDF 作为 MCP resource）与 Prompt。
- 周五：把订单 MCP 工具接进第 3 周 ReAct Agent（`mcp_db_tool.py`），让 Agent 通过 MCP 查数据库。
- 当前 Client 是手写演示；后续可由 Agent 运行时自动按 `list_tools` 结果选工具。

## 七、面试话术（可直接背）

> 我写过一个 MCP Client 对接自己的订单 Server：连上后先 `list_tools` 发现可用工具和参数 schema，再 `call_tool` 传参调用，从结果内容里取回文本。支持内存直连做测试夹具，也能通过 streamable-http 连远程 Server。Client 只管发现和调用，工具实现都封装在 Server 侧，两边通过标准协议解耦。
