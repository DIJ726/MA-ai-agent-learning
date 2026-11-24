# 学习笔记 · 周日 11/22 — ReAct 复盘踩坑 + 原理梳理（第 3 周收口）

> 第 3 周最后一天。目标：把前几天手写的最小 ReAct Agent 端到端跑通，复盘踩坑、补齐注释，整理代码提交，并用自己的话讲清 ReAct 原理（面试话术打底）。

---

## 一、ReAct 循环是什么（一句话版本）

**ReAct = Reasoning（推理）+ Acting（行动）交替进行**：模型先 Thought 思考下一步，再 Action 选一个工具并给参数，工具返回的 Observation 回填上下文，据此继续推理；信息足够后输出 Final Answer。

```
用户问题
  → Thought: 分析现状，推理下一步
  → Action: 工具名 + Action Input(JSON)
  → Observation: 工具执行结果（系统回填，模型不写）
  → 回到 Thought，直到信息足够
  → Final Answer: 最终回答
```

与纯思维链（CoT）的区别：CoT 只在模型内部闭门推理；ReAct 每一步都可以调用外部工具（计算器、查时间、查数据库）拿到新信息，再继续推。

## 二、本项目四组件落点

| Agent 组件 | 本项目对应实现 |
|------------|----------------|
| 感知 | `parse_react_step` 解析模型输出；工具观察回填为 `Observation` |
| 规划 | Thought 推理 + `max_steps` 步数上限，防止死循环 |
| 执行 | `tools.execute_tool` 按名路由到注册表工具，结果转文本回传 |
| 记忆 | `memory.py`：短期对话滑动窗口 + 长期事实字典检索 |

## 三、端到端验证结果（真实千问模型，非假 LLM）

- **复利问题**：本金 10000、年利率 3.5%、按年复利 3 年。
  - Step 1：`calculator`，`10000 * (1 + 0.035) ** 3` → Observation `11087.1787...`
  - Step 2：Final Answer「约 11087.18 元」。
  - 验算：10000 × 1.035³ = 11087.18，正确。
- **混合问题**：「现在几点？算 256×17」。
  - Step 1：`get_current_time` → `2026-11-24 21:48:02 周二`
  - Step 2：`calculator`，`256 * 17` → `4352`
  - Step 3：Final Answer 同时给出时间与乘积。

两轮都正确路由到工具，未出现跳过工具心算。

## 四、踩坑清单与解法（本轮沉淀）

### 1. 解释器/依赖不一致 → `ModuleNotFoundError: No module named 'requests'`
- 现象：IDE 运行用项目虚拟环境 `.venv`（Python 3.13.5，初始只装了 pip），命令行之前用 anaconda 全局解释器（已装依赖）。同一个脚本在两边表现不同。
- 解法：
  - `requirements.txt` 固定第三方依赖（`requests`、`httpx`）。
  - 新环境安装：`.venv\Scripts\python.exe -m pip install -r requirements.txt`。
  - 运行前确认 IDE 选的解释器与命令行一致。

### 2. 端口被占用 → `PermissionError: [WinError 10013]`
- 现象：`weather_assistant.py` 绑定 `127.0.0.1:8765` 报 10013；`netstat` 常规列表看不到占用。
- 定位：`Get-NetTCPConnection -LocalPort 8765` 查到 PID 6908（MultiTip）处于 `Bound` 状态（已绑定未监听，普通 netstat 不显示）；探测 8766/9000/18765 均可绑定。
- 解法：不改其他软件进程，天气助手端口避让为 **8766**，文件头注释与启动 URL 同步。

### 3. 真实模型格式不稳：跳过工具 + 参数混入
- 现象（早期端到端观察到）：
  - 模型偶尔不调 `calculator`，直接心算给答案；
  - `Action Input` 里混入下一段 Thought，解析降级成 `{"_raw": ...}`。
- 解法（本轮加固）：
  - **提示词硬规则**：数值/复利/日期推算必须调 `calculator`，禁止心算；`Action Input` 只写 JSON，不续写 Thought；一轮只出一个 Action。
  - **解析器加固**：JSON 解析失败时，截取首个平衡的 JSON 对象/数组（括号配对、跳过字符串内括号），再解析；仍失败才降级 `_raw`。

### 4. 异常不能崩循环
- 空 Action、未知工具、工具内部异常，统一兜成观察文本回传，让模型下一轮纠正；循环本身不抛断。

## 五、代码结构现状

| 文件 | 职责 |
|------|------|
| `min_agent.py` | ReAct 循环：解析 → 决策 → 工具路由 → 观察回填 → 记忆读写 |
| `tools.py` | 工具定义 + 注册表 + schema + `execute_tool` 分派 |
| `memory.py` | 短期对话记忆 + 长期事实字典检索（词袋 Jaccard） |
| `chat_with_llm.py` | LLM 调用层（重试/计时/JSON/会话历史） |
| `weather_assistant.py` | 网页天气助手（8766） |

## 六、边界（留给后续周次，不在本周实现）

- 原生 `tools` 字段：当前走自写 ReAct 文本协议（`Action:`/`Action Input:`）；`tools.py` 的 schema 已备好，后续联调 `chat/completions` 的原生 tools 参数。
- 长期检索：当前词袋粗召回，同义/近义匹配不上；第 6 周接 embedding + Chroma/FAISS。
- 自动事实抽取：当前由调用方显式 `remember_fact`，后续让模型从对话提炼稳定事实。

## 七、面试话术（可直接背）

> 我用纯 Python 手写了一个最小 ReAct Agent：模型每轮先 Thought 推理，再通过注册表路由调用工具（计算器、查时间），工具结果作为 Observation 回填上下文，循环直到信息足够给出 Final Answer；配了短期对话记忆和长期事实检索，并用步数上限防止死循环。相比纯思维链，它能在推理过程中调用外部工具获取新信息，而不是闭门推导。

## 八、本周产物
- 代码：`min_agent.py`（解析加固 + 提示词硬规则 + 记忆接入），端到端真实模型跑通。
- 笔记：本文件；前序见 `2026-11-17_react-skeleton`、`2026-11-18_function-calling-tools`、`2026-11-19_tool-routing`、`2026-11-20_memory`。
