"""mcp_hello_server.py — MCP 最小 Hello Server（第 4 周周一产物）。

今天任务：装好官方 MCP Python SDK（包名 ``mcp``），用官方写法跑通一个 hello server。

MCP（Model Context Protocol）三个角色：
  - Host：LLM 应用本体（Claude、IDE、我们自己的 Agent 运行时），是和用户对话的一方。
  - Client：住在 Host 里、说 MCP 协议的那一侧；Host 给每个连上来的 Server 起一个 Client。
  - Server：我们用 SDK 写的一侧，向 Client 暴露能力；它不直接和模型对话。

三种原语（谁决定调用，是它们的分界）：
  - Tool：模型决定调用的动作函数（查库、调 API），对应 HTTP 的 POST。
  - Resource：应用决定喂进上下文的数据（文件内容、接口响应），对应 HTTP 的 GET。
  - Prompt：用户按名字选的可复用消息模板（斜杠命令）。

SDK 写法（官方）::

    from mcp.server import MCPServer
    server = MCPServer("名字")

    @server.tool()
    def greet(name: str) -> str:
        # docstring 即工具描述；类型提示即参数 schema
        return f"你好，{name}！"

运行方式：
  python mcp_hello_server.py          # 内存 Client 连 Server，列工具 + 调用（自测）
  python mcp_hello_server.py stdio    # 标准输入输出，给 Inspector / 外部 Client 用
  python mcp_hello_server.py http     # streamable-http，127.0.0.1:8767/mcp
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from mcp import Client
from mcp.server import MCPServer

# ---------------------------------------------------------------------------
# Server 实例与工具注册
# ---------------------------------------------------------------------------
server = MCPServer("hello-agent")


@server.tool()
def greet(name: str = "朋友") -> str:
    """向指定的人问好，返回一句问候语。

    Args:
        name: 要问候的名字；不传时默认为“朋友”。
    """
    return f"你好，{name}！欢迎来到 MCP。"


@server.tool()
def add(a: int, b: int) -> int:
    """把两个整数相加，返回它们的和。"""
    return a + b


# ---------------------------------------------------------------------------
# 自测辅助：从 CallToolResult 取文本
# ---------------------------------------------------------------------------
def _result_text(result: Any) -> str:
    """从 CallToolResult 里取出文本内容（兼容纯文本与结构化返回）。"""
    contents = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        parts.append(text if text is not None else str(item))
    return "\n".join(parts)


async def _self_test() -> None:
    """用内存 Client 直连 Server：列出工具并实际调用，验证链路打通。"""
    async with Client(server) as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools.tools]
        print("=== 已注册工具 ===")
        for t in tools.tools:
            print(f"- {t.name}: {t.description}")
            print(f"  input_schema: {t.input_schema}")

        assert "greet" in tool_names, "greet 工具未注册"
        assert "add" in tool_names, "add 工具未注册"

        add_result = await client.call_tool("add", {"a": 1, "b": 2})
        add_text = _result_text(add_result)
        print("\n=== add(1, 2) ===")
        print(add_text)
        assert not add_result.is_error, "add 调用报错"
        assert "3" in add_text, "add 返回值不是 3"

        greet_result = await client.call_tool("greet", {"name": "World"})
        greet_text = _result_text(greet_result)
        print("\n=== greet('World') ===")
        print(greet_text)
        assert not greet_result.is_error, "greet 调用报错"
        assert "World" in greet_text, "greet 未回显名字"

        print("\nhello server 内存自测通过。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP hello server")
    parser.add_argument(
        "mode",
        nargs="?",
        default="test",
        choices=["test", "stdio", "http"],
        help="test=内存自测；stdio=标准输入输出；http=streamable-http",
    )
    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(server.run_stdio_async())
    elif args.mode == "http":
        asyncio.run(server.run_streamable_http_async(host="127.0.0.1", port=8767))
    else:
        asyncio.run(_self_test())
