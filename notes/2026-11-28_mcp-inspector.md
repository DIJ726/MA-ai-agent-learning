# 学习笔记 · 周六 11/28 — MCP Inspector 调试工具（第 4 周）

> 今天任务：学习 MCP Inspector 调试工具，用 Inspector 抓一次完整调用链路并存档截图（W4 检查点：MCP 调用链路截图，README/Demo 用）。
>
> **本轮实际情况（如实标注）**：本机 npm registry 下载严重限速，Inspector 未能安装启动，**未生成 Inspector 界面截图**；链路先用既有 `mcp_client.py` 内存自测输出以文本形式存档。受阻原因、实测数据与恢复步骤见第六、七节。

---

## 一、Inspector 是什么

MCP Inspector 是官方提供的 MCP Server 调试客户端：不接真实 LLM、不挂 Agent，单独连到某个 MCP Server，把它暴露的 **Tools / Resources / Prompts** 列出来，手动填参数调用，并在界面里观察底层报文。

作用定位：
- 开发/调试 Server 时，先在 Inspector 里验证工具能否被发现、参数是否合法、返回是否正确，再接到真实客户端；
- 看一次完整的「Client 请求 → Server 响应」链路，定位是工具注册、schema 校验还是传输层的问题；
- **Inspector 不等于最终 AI 客户端**，发布前仍要在真实目标客户端复测。

## 二、三种运行形态（官方文档）

- **Web UI**：默认浏览器界面，主端口 `6274`（v2 单 Web 服务；v1 另有代理端口 6277，新版已取消）。
- **`--cli`**：命令行模式，可脚本化、用于 CI。
- **`--tui`**：终端交互界面（Ink 实现），无需浏览器。

环境要求：Node.js `^22.7.5`（部分新版要求 22.19.0+）。本机 Node `v22.23.2`，版本满足。

## 三、两种连接方式

**stdio**：Inspector 作为父进程拉起 Server 子进程，参数直接透传：

```
npx @modelcontextprotocol/inspector <server可执行文件> <server参数...>
```

**streamable-http / SSE**：先把 Server 起在本地端口，再用 URL 连接（也可在界面的连接配置里填 URL）：

```
# 先起订单 Server（8768）
python mcp_order_server.py http
# 再连 HTTP 端点
npx @modelcontextprotocol/inspector --url http://127.0.0.1:8768/mcp
```

## 四、界面能观察到什么

- **Tools / Resources / Prompts 面板**：列表、参数 schema、手动调用、查看返回结果。
- **Protocol 标签**：JSON-RPC 报文，请求与响应配对、通知内联、按一轮会话分组，协议错误按类型标出。
- **Network 标签**：HTTP 层的状态码、请求/响应头与体（仅 SSE / Streamable HTTP）；密钥在头和体里默认打码。
- **Console 标签**：stdio Server 子进程的 `stderr`，服务端自身诊断日志看这里。
- **History**：保留整段调用链路，可回看。

## 五、关键注意点（踩坑前置）

1. **必须开启动时打印的带 token 的 URL**。后端用每次启动生成的会话令牌保护 `/api/*` 路由，启动日志会打印形如 `http://localhost:6274/?MCP_INSPECTOR_API_TOKEN=...` 的地址；手敲裸 `http://localhost:6274` 可能因缺令牌连不上代理。
2. **stdio 模式下 stdout 是协议通道**。服务端调试日志只能写 `stderr`，写 `stdout` 的 `console.log`/`print` 会污染 JSON-RPC 流，导致连接中断或乱码。
3. **端口冲突**：默认 `6274`（UI），可用 `CLIENT_PORT` / `SERVER_PORT` 环境变量覆盖；Apps 沙箱另用 `6275`。
4. **传输方式要匹配**：连接被拒先确认 Server 是否起来、传输类型（stdio / http / sse）是否选对；工具不显示就去 Console / 通知面板看启动报错。

## 六、本项目待执行命令（网络恢复后补齐）

仓库 `E:\VSagent\DY`，虚拟环境 `E:\VSagent\.venv\Scripts\python.exe`，订单 Server 端口 `8768`。

**stdio 模式（推荐，直接绑本地 Server 进程）**：

```
npx @modelcontextprotocol/inspector E:\VSagent\.venv\Scripts\python.exe mcp_order_server.py stdio
```

**HTTP 模式**：

```
# 终端 1：起 Server
E:\VSagent\.venv\Scripts\python.exe mcp_order_server.py http
# 终端 2：连端点
npx @modelcontextprotocol/inspector --url http://127.0.0.1:8768/mcp
```

界面流程：Connect → List Tools → 选 `get_order` / `list_orders` 填参数 → Run Tool → 在 Protocol / History 看请求与响应 → 截图存入 `docs/screenshots/`，并在 README 引用。

## 七、本轮受阻记录（事实，未夸大）

### 已完成的前置核对

- 本地仓库基线 = `4275469`，与 `origin/main` 同步；Node `v22.23.2`；venv 内 `mcp 2.3.0`。
- 订单 Server 离线自测通过：`get_order(SO-20261101)` → 张伟 / 机械键盘 K87 / ¥329.00 / 已完成；`list_orders(status=已完成)` → 2 单已完成；不存在单号返回「未找到」不崩。
- Client 内存链路自测通过，文本输出存档：`docs/mcp-call-chain-2026-11-28.txt`。

### 受阻原因

- Inspector 仅以 npm 包分发（`@modelcontextprotocol/inspector`，主包解压 6.6MB / 44 文件），依赖树含 `vite`、`react`、`ink`、`@napi-rs/keyring`（原生二进制）、`@modelcontextprotocol/*` 等，整体体量远大于主包。
- 本机到 npm registry（官方源与 npmmirror 镜像）下载严重限速：实测同一 tarball 取 200KB 分别耗时 38 秒、8 秒，约 **5–25 KB/s**，且连接建立后数据流停滞；`npx` 直跑与下载目录本地预装两种方式均长时间 stall。
- 本机无 Docker（无法用官方镜像 `ghcr.io/modelcontextprotocol/inspector`），各本地缓存目录也无现成 Inspector 副本。

### 处置边界

- 未启动 Inspector、未生成界面截图；未伪造任何截图或「已调试」结论。
- 链路降级为 Client 控制台文本日志存档，**不是** Inspector UI 截图，不能替代检查点要求的交互截图。
- 本轮未改动任何 `.py` 代码；远程 `main` 基线保持不变。

## 八、遗留项

- [ ] 网络恢复后按第六节命令启动 Inspector，完成一次 `list_tools` + `call_tool` 调用并截图，存入 `docs/screenshots/`。
- [ ] W4 检查点第二条「MCP 调用链路截图存档（README/Demo 用）」仍未达成；第一条「查库 Server 能被 Agent 调用」已由周五 `mcp_db_tool.py` 离线验证达成。
- [ ] 周日：整理 MCP 模块 + 写博客《给 Agent 接上数据库：MCP 实战》。

## 九、面试话术（可直接背）

> MCP Inspector 是官方的 Server 调试客户端，不接真实模型就能把 Server 的工具、资源、提示列出来手动调用，底层在 Protocol 标签看 JSON-RPC 请求响应、Network 标签看 HTTP 层、Console 标签看 stdio 子进程的 stderr。stdio 模式下服务端日志只能走 stderr，写 stdout 会污染协议流；启动后要打开带会话令牌的 URL，不能只敲裸端口。调试顺序是先在 Inspector 验证工具注册、参数 schema 和返回，再接到真实 Agent 客户端复测。

## 参考来源

- npm 包文档：https://www.npmjs.org/package/@modelcontextprotocol/inspector
- 官方 Web Inspector 说明：https://modelcontextprotocol.io/docs/draft/tools/inspector/web
- 调试实践与常见问题：https://gingerlabs.ai/blog/how-to-debug-mcp-server-using-mcp-inspector
- v2 变更与模式说明：https://mcp.so/blog/mcp-inspector-guide
