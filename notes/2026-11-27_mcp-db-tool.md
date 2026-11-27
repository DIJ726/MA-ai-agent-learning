# 学习笔记 · 周五 11/27 — 把 MCP 订单工具接进 ReAct Agent（第 4 周）

> 今天任务：`mcp_db_tool.py`——Agent 能通过 MCP 查数据库。把订单 MCP Server 的工具桥接进第 3 周 ReAct Agent，复用既有工具注册表与路由，不改 Agent 主体。

---

## 一、整条调用链

```
ReActAgent 循环
  └─ Action: get_order / list_orders
       └─ tools.execute_tool(工具名, 参数)        # 既有同步分派入口
            └─ mcp_db_tool 桥接 Tool.func        # 本模块新增
                 └─ MCP Client.call_tool(...)     # 异步，走 MCP 协议
                      └─ mcp_order_server         # Server 侧查 SQLite
                           └─ 结果文本回填为 Observation
```

- Agent 只认注册表 `TOOL_REGISTRY` 里的工具，不直接碰数据库连接；查库能力封装在 Server 侧，两边靠 MCP 协议解耦。
- `min_agent.py` 的 `_execute_action` 本来就走 `execute_tool`，工具注册进注册表后自动可被调用，**无需改 Agent 主体**。

## 二、关键写法

### 1. 复用既有 Tool 数据结构

`tools.Tool` 字段：`name / description / parameters / func`，`func(arguments: dict) -> str` 同步返回文本。桥接工具就是往这个结构里填，`parameters` 与 Server 工具签名一致：

```python
Tool(
    name="get_order",
    description=...,
    parameters={"type": "object", "properties": {...}, "required": [...]},
    func=_wrap_mcp_call("get_order"),
)
```

### 2. 同步 func 包裹异步 MCP 调用

既有工具链是同步的，而 MCP Client 的 `call_tool` 是协程。本模块用 `_run_async` 把单次协程跑成同步结果：

- 当前线程没有 running loop：`asyncio.run(coro)`；
- 已有 running loop：放到独立线程开新 loop 执行，避免嵌套 `asyncio.run` 报错，Client 不跨 loop 复用。

```python
async def _call_order_tool(name, arguments):
    async with Client(mcp_order_server.server) as client:
        result = await client.call_tool(name, arguments)
        text = _result_text(result)
        if result.is_error:
            raise RuntimeError(text)
        return text
```

`func` 内 `try/except` 把异常兜成错误文本回传，对齐 `execute_tool` 的兜底风格，不中断 Agent 循环。

### 3. 描述动态发现 + 兜底

注册时先 `list_tools` 拉取 Server 工具描述（来自函数 docstring），失败回退内置描述。MCP 描述是多行 docstring，进系统工具目录只取首行，保持单行简洁。

### 4. 注册时机

必须在**创建 ReActAgent 之前**调用一次 `register_mcp_order_tools()`——Agent 的系统提示词工具清单来自注册表快照，后补注册不会更新已建好的 Agent。

## 三、验证结果（离线自测，假 LLM，不消耗真实 token）

`python mcp_db_tool.py`：

- 桥接注册 `get_order`、`list_orders`，描述来自 Server。
- 注册表直连：
  - `get_order("SO-20261101")` → 张伟 / 机械键盘 K87 / ¥329.00 / 已完成。
  - `list_orders(status="已完成")` → 2 单已完成，不含待支付。
  - 不存在单号 → 返回「未找到」，不崩。
- 既有 `calculator` 未被污染，仍正常分派。
- ReAct Agent 端到端：假 LLM 先选 `get_order`，Observation 是 MCP 查回的订单文本，再据此给 Final Answer。

回归：`min_agent.py`、`mcp_order_server.py`、`mcp_client.py` 既有自测均通过。

## 四、踩坑清单与解法

### 1. 同步工具签名 vs 异步 MCP

- 现象：`Tool.func` 是同步的，直接在里面 `await client.call_tool(...)` 不成立；`asyncio.run` 又不能在已有 loop 里嵌套调用。
- 解法：`_run_async` 区分「无 loop 直接 run / 有 loop 独立线程跑新 loop」；每次调用在协程内新开内存 Client，不跨 loop 持有连接。

### 2. 注册晚于建 Agent 会导致工具目录缺失

- 现象：先实例化 `ReActAgent` 再注册 MCP 工具，系统提示词里没有新工具，模型选不到。
- 解法：先 `register_mcp_order_tools()`，再创建 Agent。

### 3. docstring 多行描述别整段塞进提示词

- 现象：直接用 Server 完整 docstring 当描述，会把 `Args:` 段落一起带进系统提示词，冗余。
- 解法：取首个非空行作为功能描述。

## 五、代码结构现状

| 文件 | 职责 |
|------|------|
| `mcp_db_tool.py`（今天） | 订单 MCP 工具桥接进 `TOOL_REGISTRY`；同步包裹异步 MCP 调用；离线自测含 ReAct Agent 场景 |
| `mcp_order_server.py` | 订单库 MCP Server（被连接的一方，查 SQLite） |
| `mcp_client.py` | 通用 MCP 发现/调用封装（`discover_tools` / `call_tool`） |
| `tools.py` | 工具注册表 `TOOL_REGISTRY` + 同步分派 `execute_tool` |
| `min_agent.py` | ReAct 循环，按 Action 路由到注册表执行 |

## 六、边界（留给后续）

- 周六：用 MCP Inspector 抓一次完整调用链路并存档截图（W4 检查点要求）。
- 周日：整理 MCP 模块，写博客《给 Agent 接上数据库：MCP 实战》。
- 当前桥接固定连内存订单 Server；接 HTTP Server（8768）时把 `Client(mcp_order_server.server)` 换成 URL 即可。
- 端到端接真实 LLM：本地 `.env` 密钥齐备后，用默认 `chat_completion` 跑一次真实查库对话验证（本轮离线用假 LLM 验证链路）。

## 七、面试话术（可直接背）

> 我把自己写的订单 MCP Server 接进了 ReAct Agent：在工具桥接层把 MCP 工具适配成 Agent 既有的工具注册表项，Agent 按 Action 分派时，桥接层通过 MCP Client `call_tool` 调 Server 查 SQLite，结果文本回填成 Observation。既有工具是同步签名、MCP 调用是异步的，我用独立事件循环做了同步包裹并做了异常兜底。Agent 不直接碰数据库，查库逻辑全在 Server 侧，两边靠标准 MCP 协议解耦。
