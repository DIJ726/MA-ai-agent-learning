"""天气智能助手：网页对话式天气查询与分析。

功能：在网页对话框输入任意城市或地区，程序自动调用和风天气 API
获取「过去 5 天历史 + 未来 7 天预报 + 实时空气质量」，再把原始数据
交给千问大模型分析，生成通俗易懂的天气总结和出行建议。

运行方式：python weather_assistant.py，浏览器自动打开 http://127.0.0.1:8765
密钥存放：同目录 .env 文件（DASHSCOPE_API_KEY / QWEATHER_API_KEY / QWEATHER_API_HOST）
"""

import json
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

import requests

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------
ENV_FILE_PATH = Path(__file__).resolve().parent / ".env"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
REQUEST_TIMEOUT_SECONDS = 30
LLM_MODEL_NAME = "qwen-plus"
LLM_BASE_URL = "https://ws-ljhpruzo85ykejwp.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
HISTORY_DAYS = 5   # 过去 5 天
FORECAST_DAYS = 7  # 未来 7 天
MAX_HISTORY_TURNS = 20  # 多轮对话保留的最大轮数

# 千问系统提示词：让模型知道自己的数据来源和输出风格
ANALYST_SYSTEM_PROMPT = (
    "你是一名贴心的天气助理。用户会给你一份某个城市的天气原始数据"
    "（过去若干天历史 + 未来若干天预报 + 实时空气质量），"
    "并指定他关心的具体日期。请用中文回答，**重点回答用户指定的那几天**："
    "当日天气（晴雨、温度范围、风）、和前后一两天对比的趋势（可选）、"
    "以及针对那几天的出行建议（穿衣、雨具、户外活动、空气敏感人群注意事项）。"
    "语气自然简洁，不要把 12 天数据全部罗列一遍。"
)

# 提取城市 + 具体日期的系统提示词
CITY_SYSTEM_PROMPT = (
    "从用户的话里提取：1) 他想查询天气的城市或地区名称；"
    "2) 他明确问到的具体日期。今天是 {today}。"
    "相对说法（今天/明天/后天/大后天/昨天/前天/周几/本周几/这周六等）"
    "和绝对日期（如 11月8日、2026-11-08）都要换算成 yyyy-MM-dd。"
    '只输出 JSON，格式：{{"city": "城市名", "dates": ["日期1", "日期2"]}}。'
    "用户没有明确提到任何具体日期时，dates 必须是空数组 []。"
    "如果城市也提取不到，city 为空字符串。不要输出任何其他内容。"
)


# ---------------------------------------------------------------------------
# dataclass：结构化数据模型
# ---------------------------------------------------------------------------
@dataclass
class CityInfo:
    """城市信息（来自 GeoAPI）。"""

    name: str
    location_id: str
    lat: str
    lon: str
    adm1: str  # 省/直辖市
    adm2: str  # 城市


@dataclass
class DaySummary:
    """单日天气摘要（历史或预报通用）。"""

    fx_date: str
    tag: str        # "历史" 或 "预报"
    temp_max: str
    temp_min: str
    text_day: str
    precip: str
    humidity: str
    wind_dir: str = ""
    wind_scale: str = ""

    def to_line(self) -> str:
        """转成给大模型看的一行文本。"""
        return (
            f"{self.fx_date}（{self.tag}）：{self.text_day}，"
            f"{self.temp_min}~{self.temp_max}℃，降水 {self.precip}mm，"
            f"湿度 {self.humidity}%，{self.wind_dir}{self.wind_scale}级风"
        )


@dataclass
class AirBrief:
    """实时空气质量摘要。"""

    aqi: str
    category: str
    primary_pollutant: str

    def to_line(self) -> str:
        return f"实时空气质量：AQI {self.aqi}（{self.category}），首要污染物 {self.primary_pollutant}"


@dataclass
class WeatherReport:
    """某城市完整天气数据包。"""

    city: CityInfo
    history_days: list[DaySummary] = field(default_factory=list)
    forecast_days: list[DaySummary] = field(default_factory=list)
    air: Optional[AirBrief] = None
    generated_at: str = ""

    def to_prompt_text(self) -> str:
        """把整包数据拼成大模型可读的文本。"""
        lines = [f"城市：{self.city.adm1} {self.city.name}（{self.generated_at} 查询）"]
        lines.extend(d.to_line() for d in self.history_days)
        lines.extend(d.to_line() for d in self.forecast_days)
        if self.air:
            lines.append(self.air.to_line())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 装饰器：通用能力增强
# ---------------------------------------------------------------------------
def retry(attempts: int = 3, delay: float = 1.0) -> Callable:
    """自动重试装饰器：网络抖动时最多重试 attempts 次。"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for _ in range(attempts):
                try:
                    return func(*args, **kwargs)
                except (requests.RequestException, RuntimeError) as exc:
                    last_exc = exc
                    time.sleep(delay)
            raise RuntimeError(f"{func.__name__} 重试 {attempts} 次仍失败: {last_exc}") from last_exc

        return wrapper

    return decorator


def timed(func: Callable) -> Callable:
    """计时装饰器：打印每次调用耗时（调试性能用）。"""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        result = func(*args, **kwargs)
        print(f"[耗时] {func.__name__}: {time.time() - start:.1f}s")
        return result

    return wrapper


def ttl_cache(seconds: int = 600) -> Callable:
    """简单缓存装饰器：相同城市 10 分钟内不重复请求和风 API。"""

    def decorator(func: Callable) -> Callable:
        store: dict[str, tuple[float, Any]] = {}

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{func.__name__}:{args}"
            hit = store.get(key)
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            result = func(*args, **kwargs)
            store[key] = (time.time(), result)
            return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 密钥与基础请求
# ---------------------------------------------------------------------------
def load_env(env_file: Path = ENV_FILE_PATH) -> dict[str, str]:
    """读取 .env 文件为字典。

    Raises:
        FileNotFoundError: .env 不存在。
    """
    if not env_file.exists():
        raise FileNotFoundError(f"未找到配置文件: {env_file}")
    config: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


ENV = load_env()
DASHSCOPE_KEY = ENV.get("DASHSCOPE_API_KEY", "")
QW_KEY = ENV.get("QWEATHER_API_KEY", "")
QW_HOST = ENV.get("QWEATHER_API_HOST", "")


def qweather_get(path: str, params: dict[str, str]) -> dict:
    """请求和风天气 API 并返回 JSON。

    Raises:
        RuntimeError: 状态码非 200 或网络失败。
    """
    url = f"https://{QW_HOST}{path}"
    headers = {"X-QW-Api-Key": QW_KEY}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"和风天气请求失败: {exc}") from exc
    data = resp.json()
    if str(data.get("code", "200")) != "200":
        raise RuntimeError(f"和风天气返回错误: {data.get('code')}")
    return data


def chat_completion(messages: list[dict], temperature: float = 0.7,
                    json_mode: bool = False) -> str:
    """调用千问大模型，返回回复文本。

    Args:
        messages: OpenAI 格式消息列表。
        temperature: 采样温度。
        json_mode: True 时强制返回 JSON（用于城市提取）。

    Raises:
        RuntimeError: 调用失败或返回格式异常。
    """
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": LLM_MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"千问 API 请求失败: {exc}") from exc

    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"千问 API 返回格式异常: {resp.text[:200]}") from exc


# ---------------------------------------------------------------------------
# 数据获取：城市 → 历史 + 预报 + 空气
# ---------------------------------------------------------------------------
@retry(attempts=3)
@ttl_cache(seconds=600)
def search_city(query: str) -> CityInfo:
    """GeoAPI 城市查询，返回第一个匹配结果。

    Raises:
        RuntimeError: 查不到该城市。
    """
    data = qweather_get("/geo/v2/city/lookup", {"location": query, "lang": "zh"})
    locations = data.get("location") or []
    if not locations:
        raise RuntimeError(f"查不到城市「{query}」，换个写法试试（如用城市全名）")
    top = locations[0]
    return CityInfo(
        name=top["name"],
        location_id=top["id"],
        lat=top["lat"],
        lon=top["lon"],
        adm1=top.get("adm1", ""),
        adm2=top.get("adm2", ""),
    )


@retry(attempts=3)
def fetch_forecast(city: CityInfo) -> list[DaySummary]:
    """获取未来 7 天预报。"""
    data = qweather_get("/v7/weather/7d", {"location": city.location_id})
    return [
        DaySummary(
            fx_date=d["fxDate"], tag="预报",
            temp_max=d["tempMax"], temp_min=d["tempMin"],
            text_day=d["textDay"], precip=d["precip"], humidity=d["humidity"],
            wind_dir=d.get("windDirDay", ""), wind_scale=d.get("windScaleDay", ""),
        )
        for d in data.get("daily", [])
    ]


@retry(attempts=2)
def fetch_history_one_day(city: CityInfo, day: date) -> Optional[DaySummary]:
    """获取某一天的历史天气（近 10 天内，不含今天）。失败返回 None，不中断整体。"""
    try:
        data = qweather_get(
            "/v7/historical/weather",
            {"location": city.location_id, "date": day.strftime("%Y%m%d")},
        )
    except RuntimeError:
        return None
    daily = data.get("weatherDaily")
    if not daily:
        return None
    return DaySummary(
        fx_date=daily["date"], tag="历史",
        temp_max=daily["tempMax"], temp_min=daily["tempMin"],
        text_day="", precip=daily["precip"], humidity=daily["humidity"],
    )


def fetch_history(city: CityInfo, days: int = HISTORY_DAYS) -> list[DaySummary]:
    """获取过去 N 天历史天气（逐天请求）。"""
    today = date.today()
    result: list[DaySummary] = []
    for offset in range(1, days + 1):
        day = today - timedelta(days=offset)
        summary = fetch_history_one_day(city, day)
        if summary:
            result.append(summary)
    return result


@retry(attempts=2)
def fetch_air(city: CityInfo) -> Optional[AirBrief]:
    """获取实时空气质量（取中国国标 AQI）。失败返回 None。"""
    try:
        data = qweather_get(
            f"/airquality/v1/current/{float(city.lat):.2f}/{float(city.lon):.2f}",
            {"lang": "zh"},
        )
    except RuntimeError:
        return None
    for idx in data.get("indexes", []):
        if idx.get("code") == "cn-mee":
            pollutant = idx.get("primaryPollutant") or {}
            return AirBrief(
                aqi=str(idx.get("aqiDisplay", "")),
                category=idx.get("category", ""),
                primary_pollutant=pollutant.get("name", "无"),
            )
    return None


@timed
def build_report(city_query: str) -> WeatherReport:
    """组装一个城市的完整天气数据包（历史 5 天 + 预报 7 天 + 空气）。"""
    city = search_city(city_query)
    history = fetch_history(city)
    forecast = fetch_forecast(city)
    air = fetch_air(city)
    return WeatherReport(
        city=city, history_days=history, forecast_days=forecast,
        air=air, generated_at=time.strftime("%Y-%m-%d %H:%M"),
    )


# ---------------------------------------------------------------------------
# 对话逻辑
# ---------------------------------------------------------------------------
def extract_city_and_dates(question: str) -> tuple[str, list[str]]:
    """用大模型从用户问题中提取城市名和具体日期。

    Returns:
        (城市名, 日期列表)。无日期时列表为空；提取失败时城市返回原文。

    Raises:
        RuntimeError: 大模型调用失败时按原文当地名处理。
    """
    today = date.today().strftime("%Y-%m-%d")
    messages = [
        {"role": "system", "content": CITY_SYSTEM_PROMPT.format(today=today)},
        {"role": "user", "content": question},
    ]
    try:
        content = chat_completion(messages, temperature=0.0, json_mode=True)
        data = json.loads(content)
        city = str(data.get("city", "")).strip()
        dates = data.get("dates") or []
        if isinstance(dates, str):
            dates = [dates]
        dates = [str(d).strip() for d in dates if str(d).strip()][:5]
        return city, dates
    except (RuntimeError, json.JSONDecodeError, ValueError):
        return question.strip()[:20], []


def answer(question: str, history: list[dict]) -> str:
    """主入口：先判断用户是否问到具体日期，问到才拉数据并分析。

    Args:
        question: 用户输入。
        history: 对话历史（OpenAI 消息格式，会被截断到最近几轮）。

    Returns:
        大模型的分析与建议文本；未问到日期时返回追问。
    """
    city_name, dates = extract_city_and_dates(question)
    if not city_name:
        return "你想查哪个城市的天气呢？告诉我城市名和具体日期，比如「北京这周六天气怎么样」。"

    if not dates:
        # 用户没有问到具体日期：不查数据、不分析，引导他说出日期
        return (
            f"好的，{city_name}。你想了解哪一天（或哪几天）的天气呢？"
            "可以直接说「明天」「这周六」或具体日期，"
            "我能查过去 5 天到未来 7 天的天气。"
        )

    report = build_report(city_name)
    data_text = report.to_prompt_text()
    dates_text = "、".join(dates)

    messages: list[dict] = [{"role": "system", "content": ANALYST_SYSTEM_PROMPT}]
    messages.extend(history[-MAX_HISTORY_TURNS:])
    messages.append({
        "role": "user",
        "content": (
            f"我的问题：{question}\n\n"
            f"请重点回答 {dates_text} 的天气情况。"
            f"\n\n该城市天气原始数据：\n{data_text}"
        ),
    })
    reply = chat_completion(messages)
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": reply})
    return reply


# ---------------------------------------------------------------------------
# 网页服务器
# ---------------------------------------------------------------------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天气智能助手</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", sans-serif; background: #eef3f8; height: 100vh; display: flex; }
  .side { width: 280px; background: #2c5f8a; color: #fff; padding: 24px 20px; }
  .side h1 { font-size: 20px; margin-bottom: 8px; }
  .side p { font-size: 13px; opacity: .8; line-height: 1.6; }
  .side .tips { margin-top: 24px; font-size: 12px; opacity: .7; line-height: 2; }
  .chat { flex: 1; display: flex; flex-direction: column; }
  .log { flex: 1; overflow-y: auto; padding: 24px 32px; }
  .bubble { max-width: 640px; margin: 10px 0; padding: 12px 16px; border-radius: 12px;
            white-space: pre-wrap; line-height: 1.7; font-size: 15px; }
  .user { background: #2c5f8a; color: #fff; margin-left: auto; }
  .bot { background: #fff; color: #222; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .bot.pending { color: #999; font-style: italic; }
  .inputbar { display: flex; gap: 10px; padding: 16px 32px; background: #fff;
              border-top: 1px solid #e3e8ee; }
  .inputbar input { flex: 1; font-size: 15px; padding: 12px 16px; border: 1px solid #cfd8e3;
                   border-radius: 10px; outline: none; }
  .inputbar input:focus { border-color: #2c5f8a; }
  .inputbar button { font-size: 15px; padding: 12px 28px; background: #2c5f8a; color: #fff;
                    border: none; border-radius: 10px; cursor: pointer; }
  .inputbar button:disabled { background: #9ab; cursor: not-allowed; }
</style>
</head>
<body>
<div class="side">
  <h1>天气智能助手</h1>
  <p>过去 5 天 + 未来 7 天<br>天气分析 & 出行建议</p>
  <div class="tips">
    试试这样问（记得带上日期）：<br>
    · 北京这周六天气怎么样<br>
    · 上海明天适合出去玩吗<br>
    · 广州 11 月 8 日要不要带伞<br>
    · 成都昨天冷不冷
  </div>
</div>
<div class="chat">
  <div class="log" id="log">
    <div class="bubble bot">你好！我是天气智能助手。告诉我城市 + 具体日期（如「北京这周六天气怎么样」），我会查询过去 5 天到未来 7 天的数据，重点回答你问的那一天。</div>
  </div>
  <div class="inputbar">
    <input id="q" placeholder="输入城市或问题，回车发送" autofocus>
    <button id="send">发送</button>
  </div>
</div>
<script>
const log = document.getElementById('log');
const q = document.getElementById('q');
const send = document.getElementById('send');

function addBubble(cls, text) {
  const div = document.createElement('div');
  div.className = 'bubble ' + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function ask() {
  const text = q.value.trim();
  if (!text) return;
  q.value = '';
  addBubble('user', text);
  send.disabled = true;
  const pending = addBubble('bot pending', '正在查询天气数据并分析，约需 10~30 秒……');
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const data = await resp.json();
    pending.className = 'bubble bot';
    pending.textContent = data.reply || data.error || '（空回复）';
  } catch (err) {
    pending.className = 'bubble bot';
    pending.textContent = '请求失败：' + err.message;
  } finally {
    send.disabled = false;
    q.focus();
  }
}

send.onclick = ask;
q.onkeydown = e => { if (e.key === 'Enter') ask(); };
</script>
</body>
</html>"""


class ChatHandler(BaseHTTPRequestHandler):
    """HTTP 服务器：首页 + /api/chat 聊天接口。"""

    conversation: list[dict] = []  # 简单起见，全局一份对话历史

    def do_GET(self) -> None:
        """返回聊天页面。"""
        if self.path != "/":
            self.send_error(404)
            return
        body = PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """处理聊天请求。"""
        if self.path != "/api/chat":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        message = str(payload.get("message", "")).strip()

        result: dict[str, str] = {}
        try:
            if not message:
                raise RuntimeError("请输入内容")
            result["reply"] = answer(message, ChatHandler.conversation)
        except Exception as exc:  # 对话接口兜底：出错也返回可读信息
            result["error"] = f"出错了：{exc}"

        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """静默默认访问日志，只留业务输出。"""
        return


def main() -> None:
    """程序入口：启动本地服务器并自动打开浏览器。"""
    for name, value in (("DASHSCOPE_API_KEY", DASHSCOPE_KEY),
                        ("QWEATHER_API_KEY", QW_KEY),
                        ("QWEATHER_API_HOST", QW_HOST)):
        if not value:
            raise RuntimeError(f"请在 .env 中配置 {name}")

    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), ChatHandler)
    url = f"http://{SERVER_HOST}:{SERVER_PORT}"
    print(f"天气智能助手已启动：{url}  （Ctrl+C 退出）")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()


if __name__ == "__main__":
    main()
