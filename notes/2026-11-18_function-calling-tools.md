# 学习笔记 · 周三 11/18 — Function Calling / Tool Use（定义两个工具）

> 第 3 周第 3 天。目标：理解 Function Calling（工具调用）机制，在 `tools.py` 里定义两个工具——计算器 `calculator`、时间查询 `get_current_time`，并写出 OpenAI 兼容的工具 schema。工具接入 ReAct 循环是周四的任务。

---

## 一、为什么需要工具（Tool Use）

大模型本身只能输出文本：不能查实时时间、不能算数、不能访问数据库或 API。**Function Calling** 让模型在需要时给出"调哪个工具、传什么参数"的指令，由我们的应用真正执行，再把结果回填给模型。

### 标准三步流程（官方机制）
1. **带工具列表调模型**：应用把问题 + 可用工具清单（`tools`）发给模型。
2. **模型返回调用指令**：若需要工具，返回工具名 + 输入参数；若不需要，直接回自然语言。
3. **应用执行并回填**：应用运行工具拿到结果，把结果作为工具消息加回 `messages`，再调一次模型，让它基于观察继续推理或给最终答案。

这和我们周二写的 ReAct 循环是同一套思想：Thought → Action（选工具+参数）→ Observation（工具结果回填）。

## 二、工具 schema 结构（OpenAI 兼容，千问/百炼同构）

每个工具是一个 `type: "function"` 对象：

```json
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "工具描述，帮模型判断何时调用",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {"type": "string", "description": "算术表达式"}
      },
      "required": ["expression"]
    }
  }
}
```

要点（来自千问/百炼 OpenAI 兼容文档）：
- **`name`**：字母数字，可含下划线/短划线，长度 ≤ 64。
- **`description`**：写清工具用途和适用场景，模型靠它决定"要不要用这个工具"，描述越清楚工具选择越准。
- **`parameters`**：合法 JSON Schema；`properties` 描述每个参数类型和说明，`required` 列必填参数。
- **无参工具**：`parameters` 为空字典 `{}`（官方 `get_current_time` 示例即如此）。
- 一次 function call 流程中，发起调用轮次和回传结果轮次都要带 `tools`；`tools` 暂时不能和 `stream=True` 同时用。

## 三、今天定义的两个工具

| 工具名 | 用途 | 参数 | 返回 |
|--------|------|------|------|
| `calculator` | 安全求值算术表达式（`+ - * / // % **`、括号、一元正负号） | `expression: str`（必填） | 计算结果字符串；出错返回错误说明 |
| `get_current_time` | 查询当前北京时间 | 无参 | `YYYY-MM-DD HH:MM:SS 周X` |

### 3.1 计算器为什么不用 `eval`
`eval(expression)` 会原样执行字符串里的任意代码，遇到 `__import__('os').system(...)` 这类注入就是安全事故。这里改用 **ast 白名单求值**：
- 先 `ast.parse(expression, mode="eval")` 解析成语法树；
- 递归只放行：数字常量、二元算术运算、一元正负号；
- 出现函数调用（`Call`）、名字、属性等节点直接抛错。

实测：`128 * 46 + 750 → 6638`；`(3 + 5) * 2 ** 3 → 64`；注入表达式被拦截为"不支持的表达式节点: Call"。

### 3.2 时间工具
固定东八区（北京时间），返回格式化日期时间 + 星期；无参数。

## 四、代码结构（`tools.py`）

| 部分 | 内容 |
|------|------|
| `Tool` dataclass | `name / description / parameters / func`；`run(arguments)` 执行；`schema()` 生成兼容 schema |
| `calculator(arguments)` | ast 白名单安全求值 |
| `get_current_time(arguments)` | 北京时间查询 |
| `TOOL_REGISTRY` | 工具名 → `Tool` 实例的注册表（周四由 Agent 读取） |
| `list_tool_schemas()` | 输出全部工具的 schema 列表 |
| `execute_tool(name, arguments)` | 按名分派执行；未知工具/异常返回错误说明文本 |

工具函数签名统一为 `func(arguments: dict) -> str`：参数用字典传入，结果以文本返回，方便注册表统一分派，也方便把结果直接当 Observation 回填。

## 五、两种工具调用方式的区别（认知）

| 方式 | 模型输出 | 解析方 | 我们项目现状 |
|------|---------|--------|-------------|
| **原生 Function Calling** | 模型返回结构化 `tool_calls`（工具名 + JSON 参数），由接口约定 | 平台/SDK 解析 | 未来端到端阶段可直接用 `tools` 字段联调 |
| **自写 ReAct 文本协议** | 模型按 `Action:` / `Action Input:` 文本格式输出 | 我们自己的 `parse_react_step` 解析 | 当前周二骨架走这条，可控、易调试 |

两者底层流程一致：选工具 → 传参 → 执行 → 回填观察。今天的 `TOOL_REGISTRY` 和 schema 两种方式都能复用。

## 六、本周排期边界

- **今天（周三）**：定义工具函数 + schema + 注册表分派入口。
- **周四**：把 `TOOL_REGISTRY` 接进 `min_agent.py`，替换 `_execute_action` 桩；模型选的工具名/参数经 `execute_tool` 真正执行。
- **周五**：给 Agent 加独立记忆字典。
- **周六**：接真实 LLM 端到端跑通。

## 七、易混点 / 面试话术

- **工具是应用执行的，不是模型执行的**：模型只决定"调谁、传什么参"，真正算数/查时间的是我们的代码。
- **参数 schema 是给模型看的说明书**：`description` 和参数说明直接影响工具选择准确率。
- **工具结果要回填上下文**：执行完把结果作为 Observation 发回模型，它才能继续推理；不回填就等于工具没调用过。
- **计算器必须沙箱化**：绝不用 `eval` 跑不可信表达式，用 ast 白名单或专门的表达式求值库。

## 八、今天的产物
- 代码：`tools.py`（两个工具 + schema + 注册表 + 分派入口，离线自测通过）。
- 笔记：`notes/2026-11-18_function-calling-tools.md`。
- 明天（周四）：工具注册与路由，把工具接入 `min_agent.py` 的 ReAct 循环。

## 参考
- 千问/百炼 OpenAI 兼容 Function Calling：`help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope`
- QwenCloud Function calling：`docs.qwencloud.com/developer-guides/tool-calling/function-calling`
