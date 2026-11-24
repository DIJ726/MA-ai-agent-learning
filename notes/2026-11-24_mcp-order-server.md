# 学习笔记 · 周二 11/24 — 查 SQLite 订单表的 MCP Server（第 4 周）

> 今天任务：继续用 `@mcp.tool()` 写一个能查本地 SQLite 订单库的 MCP Server，把数据库查询包成标准工具，供后面的 Client / Agent 调用。

---

## 一、Server 设计

- 数据库：项目内 `data/orders.db`（标准库 `sqlite3`，无需额外装数据库服务）。
- 表 `orders`：

| 字段 | 含义 |
|------|------|
| `order_id` | 订单号，主键 |
| `customer` | 客户名 |
| `product` | 商品 |
| `amount` | 金额 |
| `status` | 状态：待支付/已支付/已发货/已完成 |
| `created_at` | 下单时间 |

- 暴露两个工具：

| 工具 | 入参 | 作用 |
|------|------|------|
| `get_order` | `order_id: str` | 按单号查一单，返回详情 |
| `list_orders` | `customer: str = ""`、`status: str = ""`、`limit: int = 10` | 按客户/状态筛选，返回最近若干单 |

## 二、关键写法

### 1. 工具注册（装饰器 + 类型提示）

```python
@server.tool()
def get_order(order_id: str) -> str:
    """按订单号查询一单，返回该订单的客户、商品、金额、状态和下单时间。

    Args:
        order_id: 订单号，例如 SO-20261101。
    """
    ...
```

- docstring 是工具描述；`order_id: str` 直接生成 `input_schema`；带默认值的参数（`customer`/`status`/`limit`）在 schema 里带 `default`。

### 2. 参数化查询（防 SQL 注入）

- 所有条件用 `?` 占位符，参数放进元组/列表，**不把用户输入拼进 SQL 字符串**。
- 动态筛选：先拼 `WHERE` 条件片段，再按顺序收集参数；客户名用 `LIKE ?` 模糊匹配，状态用 `=` 精确匹配。

```python
where: list[str] = []
params: list[Any] = []
if customer:
    where.append("customer LIKE ?")
    params.append(f"%{customer}%")
if status:
    where.append("status = ?")
    params.append(status)
```

### 3. 连接管理

- 短连接：每次调用 `sqlite3.connect(DB_PATH)`，`try/finally` 用完关闭；不在工具间持有长连接，避免跨线程问题。
- `row_factory = sqlite3.Row`，取值按列名读（`row["customer"]`）。
- 建表 + 种子数据幂等：表为空才插入示例订单，重复运行不重复写。

## 三、验证结果

### 内存 Client 自测（`python mcp_order_server.py test`）

- `list_tools` 列出 `get_order`、`list_orders`，schema 正确。
- `get_order("SO-20261101")` → 张伟 / 机械键盘 K87 / ¥329.00 / 已完成。
- `get_order("SO-00000000")` → `未找到单号 ... 的订单。`（未命中不崩）。
- `list_orders(customer="李娜")` → 只返回李娜的 2 单，不含王强。
- `list_orders(status="已完成")` → 只返回已完成的 2 单，不含待支付。

### HTTP 远程 Client（`127.0.0.1:8768/mcp`）

- 外部 Client 连上后 `list_tools` 正常；`get_order("SO-20261103")` 返回张伟待支付的显示器支架；`list_orders(status="待支付")` 只返回这一单。

## 四、踩坑清单与解法

### 1. 动态 SQL 的空条件

- 三个筛选参数都可空：`WHERE` 片段要按实际命中的条件拼接，无任何条件时不加 `WHERE`；`LIMIT` 参数最后一位占位，和前面的筛选参数一起按顺序传入。
- `limit` 做了边界收敛：`max(1, min(limit, 50))`，防止传 0 或超大值。

### 2. 未命中要返回文本提示，不能抛异常

- 查不到订单时返回「未找到 / 没有符合条件的订单」这类说明文本，而不是让工具抛错；和 ReAct 侧「异常兜成观察文本」的约定一致。

### 3. 字段蛇形（沿周一结论）

- 工具返回结果读取用 `result.is_error`、`tool.input_schema`，驼峰名取不到。

## 五、代码结构现状

| 文件 | 职责 |
|------|------|
| `mcp_order_server.py` | 订单库 MCP Server：`get_order` / `list_orders`；`test` / `stdio` / `http`(8768) |
| `data/orders.db` | SQLite 示例订单库（首次运行自动建表 + 种子） |

## 六、边界（留给后续）

- 周三：写 Client 连上本 Server，跑通「发现工具 → 传参调用 → 拿结果」。
- 周五：把这两个 MCP 工具接进第 3 周 ReAct Agent（`mcp_db_tool.py`），让 Agent 通过 MCP 查数据库。
- 写操作（下单/改状态）本轮不做；当前工具全是只读查询。

## 七、面试话术（可直接背）

> 我用 MCP SDK 写过一个查 SQLite 订单库的 Server：两个工具分别按单号查单、按客户和状态筛选，参数 schema 从函数类型提示生成。查询全部走参数化占位符，动态拼接 WHERE 条件，防注入；用短连接、用完关闭，未命中返回文本提示而不是抛错。Server 既能被内存 Client 自测，也能起 streamable-http 让远程 Client 调用，下一步是把这些工具接进自己的 ReAct Agent。
