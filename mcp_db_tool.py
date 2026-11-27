"""mcp_db_tool.py — 把订单 MCP 工具接进第 3 周 ReAct Agent（第 4 周周五产物）。

今天任务：`mcp_db_tool.py`——Agent 能通过 MCP 查数据库。

链路（ReAct Agent → MCP Client → MCP Server → SQLite）：
  1. ReActAgent 在循环里按模型选择的 Action，走到 ``tools.execute_tool`` 分派；
  2. 本模块把订单 Server 的两个 MCP 工具（``get_order`` / ``list_orders``）桥接成
     既有 ``tools.Tool`` 注册进 ``TOOL_REGISTRY``，对 Agent 透明；
  3. 工具 func 内部通过 MCP Client（内存直连 ``mcp_order_server.server``）``call_tool``，
     结果文本作为 Observation 回填给 Agent；
  4. Server 侧仍只负责查库，Agent 不直接碰数据库连接，两边靠 MCP 协议解耦。

同步 / 异步衔接：
  - 既有 ``Tool.func`` 与 ``execute_tool`` 是同步签名，ReAct 循环也是同步跑的；
  - MCP Client 调用是协程。本模块用 ``_run_async`` 把单次协程放到新事件循环里跑完，
    兼容「主线程无 loop」与「已有 running loop（独立线程兜底）」两种情况。

运行方式：
  python mcp_db_tool.py          # 内存直连订单 Server 的离线自测（不消耗真实 token）
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable, Optional

from mcp import Client

import mcp_order_server
from tools import Tool, TOOL_REGISTRY, execute_tool


# ---------------------------------------------------------------------------
# 结果提取 + 同步/异步衔接
# ---------------------------------------------------------------------------
def _result_text(result: Any) -> str:
    """从 CallToolResult 取出文本内容（兼容纯文本与结构化返回）。"""
    contents = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", str(c)) for c in contents)


def _run_async(coro: Any) -> Any:
    """在同步上下文里跑一个协程并返回结果。

    - 当前线程没有 running loop：直接 ``asyncio.run``。
    - 已有 running loop（例如身处异步程序）：放到独立线程开新 loop 执行，避免
      嵌套 ``asyncio.run`` 报错；Client 在该线程内建连，不跨 loop 复用。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


# ---------------------------------------------------------------------------
# MCP Client 侧动作：发现描述 / 调用工具
# ---------------------------------------------------------------------------
async def _discover_descriptions() -> dict[str, str]:
    """内存直连订单 Server，拉取工具名 -> 描述映射（描述来自 Server 函数 docstring）。"""
    async with Client(mcp_order_server.server) as client:
        tools = await client.list_tools()
        return {t.name: t.description for t in tools.tools}


async def _call_order_tool(name: str, arguments: dict[str, Any]) -> str:
    """通过 MCP Client 调用订单 Server 工具，返回结果文本；报错抛异常。"""
    async with Client(mcp_order_server.server) as client:
        result = await client.call_tool(name, arguments)
        text = _result_text(result)
        if result.is_error:
            raise RuntimeError(f"工具 {name} 调用报错: {text}")
        return text


# ---------------------------------------------------------------------------
# 桥接规格：内置参数 schema（与 Server 工具签名一致），描述运行时从 MCP 发现
# ---------------------------------------------------------------------------
_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_order",
        "description": "按订单号查询一单，返回客户、商品、金额、状态和下单时间。需要查具体某单时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，例如 SO-20261101",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "list_orders",
        "description": "按客户名或订单状态筛选订单，返回最近若干单。需要列表/统计某类订单时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {
                    "type": "string",
                    "description": "客户名，模糊匹配；留空表示不限客户。",
                },
                "status": {
                    "type": "string",
                    "description": "订单状态（待支付/已支付/已发货/已完成）；留空表示不限状态。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，范围 1–50，默认 10。",
                },
            },
            "required": [],
        },
    },
]


def _wrap_mcp_call(name: str) -> Callable[[dict[str, Any]], str]:
    """生成一个同步 Tool func：内部走 MCP Client 调用；异常兜成错误文本，不中断 Agent 循环。"""

    def _runner(arguments: dict[str, Any]) -> str:
        try:
            return _run_async(_call_order_tool(name, arguments or {}))
        except Exception as exc:  # noqa: BLE001 - 工具异常转观察文本回传，对齐 tools.execute_tool 兜底
            return f"工具 {name} 调用出错: {exc}"

    return _runner


def register_mcp_order_tools(registry: dict[str, Tool] = TOOL_REGISTRY) -> list[str]:
    """把订单 MCP 工具桥接注册进工具注册表，返回注册的工具名列表。

    说明：
      - 重复调用会覆盖同名工具，不会重复叠加。
      - 描述优先从 MCP ``list_tools`` 动态拉取；发现失败时回退到内置描述。
      - 必须在创建 ReActAgent 之前调用一次——Agent 的工具清单来自注册表快照。
    """
    descriptions: dict[str, str] = {}
    try:
        descriptions = _run_async(_discover_descriptions())
    except Exception:  # noqa: BLE001 - 发现描述失败不阻断注册，用内置描述兜底
        descriptions = {}

    registered: list[str] = []
    for spec in _TOOL_SPECS:
        name = spec["name"]
        raw_desc = descriptions.get(name) or spec["description"]
        # MCP 描述来自 Server docstring（功能句 + Args 段落）；进系统目录只取首行，保持单行简洁
        description = next((line.strip() for line in raw_desc.splitlines() if line.strip()), "")
        registry[name] = Tool(
            name=name,
            description=description,
            parameters=spec["parameters"],
            func=_wrap_mcp_call(name),
        )
        registered.append(name)
    return registered


# ---------------------------------------------------------------------------
# 离线自测：注册表直连 + ReAct Agent 经 MCP 查库（假 LLM，不消耗真实 token）
# ---------------------------------------------------------------------------
def _self_test() -> None:
    mcp_order_server._init_db()
    registered = register_mcp_order_tools()
    print("=== 已桥接注册的 MCP 工具 ===")
    for name in registered:
        print(f"- {name}: {TOOL_REGISTRY[name].description}")
    assert set(registered) == {"get_order", "list_orders"}

    # 1. 注册表直连：execute_tool 分派到 MCP 桥接工具
    print("\n=== execute_tool(get_order, SO-20261101) ===")
    one = execute_tool("get_order", {"order_id": "SO-20261101"})
    print(one)
    assert "张伟" in one and "329.00" in one

    print("\n=== execute_tool(list_orders, status=已完成) ===")
    done = execute_tool("list_orders", {"status": "已完成"})
    print(done)
    assert "已完成" in done and "待支付" not in done

    print("\n=== execute_tool(get_order, 不存在单号) ===")
    miss = execute_tool("get_order", {"order_id": "SO-00000000"})
    print(miss)
    assert "未找到" in miss

    # 2. 既有工具未被污染：calculator 仍可正常分派
    calc = execute_tool("calculator", {"expression": "12 * 12"})
    assert calc == "144", calc

    # 3. ReAct Agent 端到端：假 LLM 先调 get_order，拿到 MCP 观察后给最终回答
    from min_agent import ReActAgent

    scripts = [
        "Thought: 用户要查具体一单，调用 get_order 按单号查询。\n"
        "Action: get_order\n"
        'Action Input: {"order_id": "SO-20261101"}',
        "Thought: 已从订单库拿到该单信息，可以回答。\n"
        "Final Answer: SO-20261101 是张伟的机械键盘 K87，¥329.00，状态已完成。",
    ]

    def fake_llm(_messages: list[dict]) -> str:
        return scripts.pop(0)

    agent = ReActAgent(llm=fake_llm, max_steps=5)
    answer = agent.run("帮我查一下 SO-20261101 这单的情况")

    observation = agent.trajectory[0].observation or ""
    print("\n=== Agent 轨迹 ===")
    agent.print_trajectory()
    assert "张伟" in observation and "329.00" in observation, observation
    assert "张伟" in answer and "329.00" in answer

    print("\nMCP 订单工具接入 Agent 自测通过。")


if __name__ == "__main__":
    _self_test()
