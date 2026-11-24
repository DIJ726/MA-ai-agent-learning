"""mcp_order_server.py — 查 SQLite 订单表的 MCP Server（第 4 周周二产物）。

今天任务：用 ``@mcp.tool()`` 写一个 MCP Server，对外暴露「查询订单」的能力，
底层读本地 SQLite 库 data/orders.db。

MCP 边界：
  - Server 只负责把数据库查询包成标准工具，不直接和模型对话；
  - 模型（Host 里的 Client）决定调哪个工具、传什么参数；
  - 参数一律用 ``?`` 占位符走参数化查询，不拼字符串，防 SQL 注入。

订单表 orders：
  order_id 单号（主键）；customer 客户；product 商品；
  amount 金额；status 状态；created_at 下单时间。

暴露工具：
  get_order(order_id)                  按单号查一单。
  list_orders(customer, status, limit) 按客户/状态筛选，返回最近若干单。

运行方式：
  python mcp_order_server.py          # 内存 Client 自测
  python mcp_order_server.py stdio    # 标准输入输出
  python mcp_order_server.py http      # streamable-http，127.0.0.1:8768/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.server import MCPServer

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "orders.db"

server = MCPServer("order-db")

# ---------------------------------------------------------------------------
# 建表 + 示例数据（幂等：表为空才插入种子）
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id   TEXT PRIMARY KEY,
    customer   TEXT NOT NULL,
    product    TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_SEED_ORDERS = [
    ("SO-20261101", "张伟", "机械键盘 K87", 329.00, "已完成", "2026-11-01 10:20"),
    ("SO-20261102", "李娜", "无线鼠标 M2", 129.50, "已发货", "2026-11-03 14:05"),
    ("SO-20261103", "张伟", "显示器支架", 189.00, "待支付", "2026-11-05 09:48"),
    ("SO-20261104", "王强", "USB 扩展坞", 219.90, "已完成", "2026-11-08 16:30"),
    ("SO-20261105", "李娜", "耳机支架", 59.00, "已支付", "2026-11-10 11:12"),
    ("SO-20261106", "赵敏", "笔记本内胆包", 79.90, "已发货", "2026-11-12 20:03"),
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """建表；表为空时写入示例订单（重复调用不重复插入）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(_SCHEMA)
        count = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        if count == 0:
            conn.executemany(
                "INSERT INTO orders (order_id, customer, product, amount, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                _SEED_ORDERS,
            )
        conn.commit()
    finally:
        conn.close()


def _format_row(row: sqlite3.Row) -> str:
    return (
        f"{row['order_id']} | {row['customer']} | {row['product']} | "
        f"¥{row['amount']:.2f} | {row['status']} | {row['created_at']}"
    )


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------
@server.tool()
def get_order(order_id: str) -> str:
    """按订单号查询一单，返回该订单的客户、商品、金额、状态和下单时间。

    Args:
        order_id: 订单号，例如 SO-20261101。
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT order_id, customer, product, amount, status, created_at"
            " FROM orders WHERE order_id = ?",
            (order_id.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return f"未找到单号 {order_id} 的订单。"
    return "订单详情：\n" + _format_row(row)


@server.tool()
def list_orders(customer: str = "", status: str = "", limit: int = 10) -> str:
    """按客户名或订单状态筛选订单；条件留空则不过滤。返回最近的若干单。

    Args:
        customer: 客户名，模糊匹配；留空表示不限客户。
        status: 订单状态（待支付/已支付/已发货/已完成）；留空表示不限状态。
        limit: 最多返回条数，范围 1–50，默认 10。
    """
    where: list[str] = []
    params: list[Any] = []

    customer = customer.strip()
    status = status.strip()
    if customer:
        where.append("customer LIKE ?")
        params.append(f"%{customer}%")
    if status:
        where.append("status = ?")
        params.append(status)

    limit = max(1, min(limit, 50))
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT order_id, customer, product, amount, status, created_at"
            f" FROM orders{where_sql} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "没有符合条件的订单。"
    header = f"共 {len(rows)} 单（按下单时间倒序）："
    return header + "\n" + "\n".join(_format_row(r) for r in rows)


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def _result_text(result: Any) -> str:
    """从 CallToolResult 取出文本内容。"""
    contents = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", str(c)) for c in contents)


async def _self_test() -> None:
    _init_db()
    async with Client(server) as client:
        tools = await client.list_tools()
        print("=== 已注册工具 ===")
        for t in tools.tools:
            print(f"- {t.name}: {t.description}")
            print(f"  input_schema: {t.input_schema}")
        names = [t.name for t in tools.tools]
        assert "get_order" in names and "list_orders" in names

        one = await client.call_tool("get_order", {"order_id": "SO-20261101"})
        one_text = _result_text(one)
        print("\n=== get_order(SO-20261101) ===")
        print(one_text)
        assert not one.is_error and "张伟" in one_text and "329.00" in one_text

        miss = await client.call_tool("get_order", {"order_id": "SO-00000000"})
        miss_text = _result_text(miss)
        print("\n=== get_order(不存在) ===")
        print(miss_text)
        assert "未找到" in miss_text

        by_customer = await client.call_tool("list_orders", {"customer": "李娜"})
        customer_text = _result_text(by_customer)
        print("\n=== list_orders(customer=李娜) ===")
        print(customer_text)
        assert not by_customer.is_error and "李娜" in customer_text
        assert "王强" not in customer_text

        by_status = await client.call_tool("list_orders", {"status": "已完成"})
        status_text = _result_text(by_status)
        print("\n=== list_orders(status=已完成) ===")
        print(status_text)
        assert "已完成" in status_text and "待支付" not in status_text

        print("\n订单库 MCP Server 自测通过。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP order database server")
    parser.add_argument(
        "mode",
        nargs="?",
        default="test",
        choices=["test", "stdio", "http"],
    )
    args = parser.parse_args()
    _init_db()

    if args.mode == "stdio":
        asyncio.run(server.run_stdio_async())
    elif args.mode == "http":
        asyncio.run(server.run_streamable_http_async(host="127.0.0.1", port=8768))
    else:
        asyncio.run(_self_test())
