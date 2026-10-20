# -*- coding: utf-8 -*-
"""本地模拟 DeepSeek 服务器（OpenAI 兼容格式）

用途：没有真实 API Key 时，测试助手的"真实 AI 模式"全链路
（设置界面填 Key/地址/模型 → 测试连接 → 自由对话 → 动作执行）。

用法:
    python tools/mock_ai_server.py          # 监听 8000 端口
    python tools/mock_ai_server.py 9001     # 自定义端口

设置界面填写:
    API Key:  sk-test（随便填，非空即可）
    接口地址: http://127.0.0.1:8000
    模型:     任意（如 deepseek-chat）

返回的动作与真实 DeepSeek 一致（JSON 动作序列），
并带简单智能：算术会真算、讲笑话、工具意图走 open_app/search_app。
"""

import json
import random
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

JOKES = [
    "程序员最讨厌两件事：别人不写注释，和让自己写注释。",
    "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。",
    "我给我家猫取名叫'递归'，因为它一叫就停不下来。",
    "世界上有10种人：懂二进制的，和不懂的。",
]

POEMS = [
    "夜色温柔，代码如诗；一行一句，皆是心事。",
    "屏幕微光照晚风，敲键声声入梦中； bugs 清零心自静，抬头已是月当空。",
]

# 工具意图关键词
OPEN_WORDS = ("打开", "启动", "运行")
FIND_WORDS = ("找", "查找", "在哪", "搜索")


def extract_app(text: str) -> str:
    for w in OPEN_WORDS + FIND_WORDS:
        if w in text:
            name = text.replace(w, " ")
            for t in ("在哪", "一下", "帮我", "请问", "呢", "？", "?", "吧",
                      "啦", "应用", "程序", "找"):
                name = name.replace(t, " ")
            name = name.strip()
            if name:
                return name.split()[0]
    return ""


def try_math(text: str) -> str:
    """尝试解析并计算算式，失败返回空串"""
    m = re.search(r"([\d\s+\-*/×÷().]+)", text)
    if not m:
        return ""
    expr = (m.group(1).replace("×", "*").replace("÷", "/").strip())
    if not re.search(r"[\d]\s*[\+\-\*/]\s*[\d]", expr):
        return ""
    try:
        val = eval(expr, {"__builtins__": {}})  # noqa: S307 受限求值
        val = int(val) if float(val).is_integer() else round(val, 6)
        steps = f"先算 {expr.replace('*', '×').replace('/', '÷')}，结果是 {val}。"
        return (f"[{{\"action\":\"show_bubble\",\"text\":\"{expr} = {val}。"
                f"{steps}\"}}, {{\"action\":\"jump\"}}]")
    except Exception:  # noqa: BLE001
        return ""


def build_reply(user_text: str) -> str:
    """根据用户输入构造与真实 AI 一致的 JSON 动作序列字符串"""
    text = user_text.strip()

    # 工具意图
    if any(w in text for w in OPEN_WORDS):
        app = extract_app(text)
        return json.dumps([
            {"action": "show_bubble", "text": f"遵命，这就打开{app}。"},
            {"action": "move_to", "anchor": "taskbar"},
            {"action": "open_app", "app": app},
            {"action": "jump"},
        ], ensure_ascii=False)
    if any(w in text for w in FIND_WORDS) and len(text) > 1:
        app = extract_app(text)
        return json.dumps([
            {"action": "show_bubble", "text": f"这就去找{app}。"},
            {"action": "search_app", "app": app},
        ], ensure_ascii=False)

    # 算术（模拟推理模型）
    math = try_math(text)
    if math:
        return math

    # 闲聊话题
    if "笑话" in text:
        return json.dumps([{"action": "show_bubble",
                            "text": random.choice(JOKES)}], ensure_ascii=False)
    if "诗" in text or "自我介绍" in text or "文案" in text:
        return json.dumps([{"action": "show_bubble",
                            "text": random.choice(POEMS)}], ensure_ascii=False)
    if any(w in text for w in ("累", "难过", "烦", "压力", "心情")):
        return json.dumps([{"action": "show_bubble",
                            "text": "累了便歇息片刻，我为你守着。"}],
                          ensure_ascii=False)
    if any(w in text for w in ("你好", "在吗", "hello", "hi")):
        return json.dumps([
            {"action": "show_bubble",
             "text": "我在，有何吩咐？我是凌零。"},
            {"action": "jump"},
        ], ensure_ascii=False)

    # 默认：像真模型一样自由回答
    return json.dumps([
        {"action": "show_bubble",
         "text": f"你说「{text}」，这个问题我来答：这是一个很好的问题！"
                 f"（本地模拟服务器回答，真实 Key 将由 DeepSeek 生成）"}
    ], ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静一点
        print(f"[MockAI] {fmt % args}")

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self.send_error(404, "only /v1/chat/completions")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        user_text = next((m["content"] for m in reversed(messages)
                          if m.get("role") == "user"), "")
        print(f"[MockAI] 收到: {user_text!r}  model={body.get('model')}")

        content = build_reply(user_text)
        resp = {
            "id": "chatcmpl-mock", "object": "chat.completion",
            "model": body.get("model", "deepseek-chat"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                      "total_tokens": 30},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[MockAI] 本地模拟 DeepSeek 已启动: http://127.0.0.1:{port}")
    print("[MockAI] 设置界面填: 接口地址=http://127.0.0.1:%d  Key=sk-test" % port)
    print("[MockAI] Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MockAI] 已停止")


if __name__ == "__main__":
    main()
