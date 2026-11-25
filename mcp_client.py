"""mcp_client.py — MCP Client：发现并调用自己 Server 的工具（第 4 周周三产物）。

今天任务：从 Client 侧走通 MCP 链路——先 ``list_tools`` 发现 Server 暴露了哪些工具
（名称、描述、参数 schema），再 ``call_tool`` 传参调用，取回结果文本。

Client 与 Server 的分工：
  - Server（mcp_order_server.py / mcp_hello_server.py）：把能力注册成工具，不直接和模型对话。
  - Client（本文件）：住在 Host 里，按协议连接 Server，发现工具并发起调用。

两种连接方式：
  - 内存直连：``Client(server)``，测试夹具，不起端口、不走网络。
  - HTTP：``Client("http://127.0.0.1:8768/mcp")``，连一个已启动的 streamable-http Server。

运行方式：
  python mcp_client.py          # 内存连订单 Server，发现 + 调用自测
  python mcp_client.py http    # 连本地 8768 的订单 Server（需先起 mcp_order_server.py http）
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from mcp import Client

import mcp_order_server


# ---------------------------------------------------------------------------
# 结果提取辅助
# ---------------------------------------------------------------------------
def _result_text(result: Any) -> str:
    """从 CallToolResult 取出文本内容（兼容纯文本与结构化返回）。"""
    contents = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", str(c)) for c in contents)


# ---------------------------------------------------------------------------
# Client 侧动作：发现工具 / 调用工具
# ---------------------------------------------------------------------------
async def discover_tools(client: Client) -> list[Any]:
    """列出 Server 暴露的全部工具，打印名称/描述/参数 schema，返回工具对象列表。"""
    tools = await client.list_tools()
    print("=== Client 发现的工具 ===")
    for t in tools.tools:
        print(f"- {t.name}: {t.description}")
        print(f"  input_schema: {t.input_schema}")
    return list(tools.tools)


async def call_tool(client: Client, name: str, arguments: dict[str, Any]) -> str:
    """按工具名传参调用，返回结果文本；调用报错时抛出异常，不静默吞掉。"""
    result = await client.call_tool(name, arguments)
    text = _result_text(result)
    if result.is_error:
        raise RuntimeError(f"工具 {name} 调用报错: {text}")
    return text


# ---------------------------------------------------------------------------
# 自测：内存直连订单 Server
# ---------------------------------------------------------------------------
async def _self_test() -> None:
    """内存直连订单 Server：发现工具并实际调用，验证 Client 链路打通。"""
    mcp_order_server._init_db()
    async with Client(mcp_order_server.server) as client:
        tools = await discover_tools(client)
        names = [t.name for t in tools]
        assert "get_order" in names and "list_orders" in names

        order = await call_tool(client, "get_order", {"order_id": "SO-20261101"})
        print("\n=== call get_order(SO-20261101) ===")
        print(order)
        assert "张伟" in order and "329.00" in order

        orders = await call_tool(client, "list_orders", {"status": "已完成"})
        print("\n=== call list_orders(status=已完成) ===")
        print(orders)
        assert "已完成" in orders and "待支付" not in orders

        print("\nMCP Client 内存自测通过。")


# ---------------------------------------------------------------------------
# HTTP 联调：连已启动的订单 Server
# ---------------------------------------------------------------------------
async def _run_http(url: str) -> None:
    """连接已启动的 HTTP Server，发现工具并做一次演示调用。"""
    async with Client(url) as client:
        await discover_tools(client)
        order = await call_tool(client, "get_order", {"order_id": "SO-20261103"})
        print("\n=== call get_order(SO-20261103) ===")
        print(order)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP client demo")
    parser.add_argument(
        "mode",
        nargs="?",
        default="test",
        choices=["test", "http"],
        help="test=内存直连自测；http=连接本地 8768 订单 Server",
    )
    args = parser.parse_args()

    if args.mode == "http":
        asyncio.run(_run_http("http://127.0.0.1:8768/mcp"))
    else:
        asyncio.run(_self_test())
