"""tools.py — Agent 工具定义层（第 3 周周三产物）。

今天任务：Function Calling / Tool Use，定义两个工具：
  - calculator：安全求值算术表达式
  - get_current_time：查询当前北京时间

边界（本周排期）：
  - 今天只定义工具函数 + OpenAI 兼容的 tools schema 元信息 + 按名分派的最小入口。
  - 周四：把工具注册进 min_agent.py 的 ReAct 循环，替换 _execute_action 桩。
  - 模型原生 tools 参数接入（chat/completions 的 tools 字段）在端到端阶段再联调。

参考格式（OpenAI 兼容，千问/百炼同构）：
    {
      "type": "function",
      "function": {
        "name": "工具名",
        "description": "工具描述（帮模型判断何时调用）",
        "parameters": { JSON Schema；无参工具为空字典 {} }
      }
    }
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# 工具数据结构
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    """一个可被 Agent 调用的工具。

    属性:
        name: 工具名（字母数字 + 下划线/短划线，≤64）。
        description: 功能描述，模型据此判断何时调用。
        parameters: JSON Schema 参数定义；无参为空字典 {}。
        func: 实际执行函数，签名 ``func(arguments: dict) -> str``，返回结果文本。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[[dict[str, Any]], str]

    def run(self, arguments: dict[str, Any]) -> str:
        """按参数字典执行工具，返回结果文本。"""
        return self.func(arguments)

    def schema(self) -> dict[str, Any]:
        """输出 OpenAI/千问兼容的 function calling schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# 工具 1：安全计算器（ast 白名单求值，禁止函数调用/属性/名字）
# ---------------------------------------------------------------------------
_ALLOWED_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node: ast.AST) -> Any:
    """递归求值表达式节点，只放行数字常量和允许的算术运算。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只允许数字常量")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        op = _ALLOWED_BINOPS[type(node.op)]
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        op = _ALLOWED_UNARYOPS[type(node.op)]
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


def calculator(arguments: dict[str, Any]) -> str:
    """计算算术表达式，返回结果字符串；出错时返回错误说明（不中断循环）。"""
    expression = str(arguments.get("expression", "")).strip()
    if not expression:
        return "计算失败：缺少 expression 参数。"
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
        # 整除结果去掉多余 .0
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{result}"
    except Exception as exc:  # noqa: BLE001 - 工具结果以文本形式回传，统一兜住
        return f"计算失败：{exc}"


# ---------------------------------------------------------------------------
# 工具 2：当前时间查询（北京时间，无参）
# ---------------------------------------------------------------------------
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_BEIJING_TZ = timezone(timedelta(hours=8))


def get_current_time(arguments: dict[str, Any]) -> str:
    """返回当前北京时间（年月日 时分秒 + 星期）。"""
    now = datetime.now(_BEIJING_TZ)
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {_WEEKDAYS[now.weekday()]}"


# ---------------------------------------------------------------------------
# 注册表：工具名 -> Tool（周四由 min_agent 读取这里的表）
# ---------------------------------------------------------------------------
TOOL_REGISTRY: dict[str, Tool] = {
    "calculator": Tool(
        name="calculator",
        description="计算算术表达式，支持 + - * / // % ** 和括号、一元正负号。需要数值计算时使用，不要自己心算。",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "算术表达式，例如 128 * 46 + 750",
                }
            },
            "required": ["expression"],
        },
        func=calculator,
    ),
    "get_current_time": Tool(
        name="get_current_time",
        description="查询当前北京时间（年-月-日 时:分:秒 + 星期）。需要知道现在几点、今天日期时使用。",
        parameters={},  # 无参工具：parameters 为空字典
        func=get_current_time,
    ),
}


def list_tool_schemas() -> list[dict[str, Any]]:
    """输出全部工具的 OpenAI 兼容 schema 列表（供 chat/completions 的 tools 参数使用）。"""
    return [tool.schema() for tool in TOOL_REGISTRY.values()]


def execute_tool(name: str, arguments: Optional[dict[str, Any]] = None) -> str:
    """按工具名分派执行；未知工具或参数异常时返回错误说明文本。"""
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        available = ", ".join(TOOL_REGISTRY.keys())
        return f"未知工具: {name}。可用工具: {available}"
    try:
        return tool.run(arguments or {})
    except Exception as exc:  # noqa: BLE001 - 工具异常转成观察文本回传
        return f"工具 {name} 执行出错: {exc}"


# ---------------------------------------------------------------------------
# 直接运行时自测：python tools.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== 工具 schema 列表 ===")
    for schema in list_tool_schemas():
        func = schema["function"]
        print(f"- {func['name']}: {func['description']}")

    print("\n=== calculator 正常计算 ===")
    print(execute_tool("calculator", {"expression": "128 * 46 + 750"}))
    print(execute_tool("calculator", {"expression": "(3 + 5) * 2 ** 3"}))

    print("\n=== calculator 非法表达式（不崩溃，返回错误说明） ===")
    print(execute_tool("calculator", {"expression": "__import__('os').system('echo hi')"}))

    print("\n=== get_current_time ===")
    print(execute_tool("get_current_time", {}))

    print("\n=== 未知工具 ===")
    print(execute_tool("not_a_tool", {}))
