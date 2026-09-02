#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenHub 零依赖本地网关（Anthropic 协议 ↔ OpenAI 协议转换器）

为什么需要它：
  TokenHub 的免费体验 Key 只在 OpenAI 协议端点 tokenhub.tencentmaas.com/v1 生效，
  而 Claude Code / cc-switch 走的是 Anthropic 协议（POST /v1/messages）。
  本网关把 Claude Code 发来的 Anthropic 请求转成 OpenAI 请求转发给 TokenHub，
  再把响应包回 Anthropic SSE 格式，从而让 Claude Code 用上你的 25 款免费额度。

零依赖：只用 Python 标准库。
用法：
  export TOKENHUB_API_KEY="sk-xxxx"        # 你的 TokenHub Key（必填）
  export TOKENHUB_BASE_URL="https://tokenhub.tencentmaas.com/v1"   # 可选，默认即此
  export GATEWAY_PORT="4000"               # 可选
  python3 tokenhub_minigate.py

然后 Claude Code / cc-switch 的 base_url 指向 http://localhost:4000 即可。
"""
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKENHUB_API_KEY = os.environ.get("TOKENHUB_API_KEY", "")
TOKENHUB_BASE_URL = os.environ.get("TOKENHUB_BASE_URL", "https://tokenhub.tencentmaas.com/v1").rstrip("/")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "4000"))
GATEWAY_BIND = os.environ.get("GATEWAY_BIND", "127.0.0.1")  # 默认仅本机；设 0.0.0.0 可让局域网其他设备访问
# 网关对外暴露的"壳"模型：CC 只认 claude/anthropic 开头的名，所以让网关把 TokenHub 模型伪装成
# 一个合法的 Claude 模型名。收到该名时翻译成本地 TOKENHUB_MODEL 再转发给 TokenHub。
# 这样 CC 2.1.231 的"非 Anthropic 模型拦截"被彻底绕过（它看到的全程是合法 Claude 模型）。
TOKENHUB_MODEL = os.environ.get("TOKENHUB_MODEL", "deepseek-v4-flash")
# CC 2.1.231 对自定义 base_url 会用 /v1/models 实时校验"这个模型你有没有"。
# 所以网关必须把 CC 配置里用到的"壳"模型名也写进 /v1/models，否则即使名合法也会被拒。
# 默认广播几个常用 Claude 模型名；也可由 GATEWAY_ADVERTISE 自定义（逗号分隔）。
GATEWAY_ADVERTISE = [m.strip() for m in os.environ.get(
    "GATEWAY_ADVERTISE", "claude-sonnet-4-20250514,claude-opus-4-20250514,claude-haiku-4-20250514"
).split(",") if m.strip()]

# CC 网关发现需要的"合法 Claude 模型名册"：覆盖各代 sonnet/opus/haiku 与常见别名。
# CC 在自定义网关上会拿"配置/解析后的模型 id"去 /v1/models 列表里比对，必须命中才放行。
CLAUDE_ROSTER = [
    "claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-20250514",
    "claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5",
    "claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-6",
    "claude-sonnet-5", "claude-opus-5", "claude-haiku-5",
    "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229",
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
]

# 25 款模型（与 tokenhub_litellm_config.yaml 对齐，用于 /v1/models 发现）
MODELS = [
    "hy3", "kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed",
    "minimax-m3", "minimax-m2.7", "deepseek-v4-pro", "deepseek-v4-flash",
    "deepseek-v4-pro-202606", "deepseek-v4-flash-202605",
    "glm-5", "glm-5.1", "glm-5.2", "glm-5-turbo", "glm-5v-turbo",
    "kimi-k2.6", "kimi-k2.5", "mimo-v2.5-pro",
    "qwen3.5-flash", "qwen3.5-plus",
    "hy-mt2-pro", "hy-mt2-lite", "hy-mt2-plus", "hy-role", "hy-role-latest",
]


def _log(msg):
    sys.stderr.write(f"[minigate] {msg}\n")
    sys.stderr.flush()


def _strip_window(model):
    """Claude Code 用 [1M] 等窗口标记后缀；网关转发到 TokenHub 前必须把 [..] 剥掉，否则上游不认。"""
    if not model:
        return model
    return model.split("[")[0].strip()


def _resolve_model(model):
    """把 CC 发来的（合法 Claude）模型名翻译成真正要调用的 TokenHub 模型。
    CC 只会发 claude/anthropic 开头的名；我们统一映射到本地 TOKENHUB_MODEL。
    若来名恰好是本网关已登记的 TokenHub 裸名（极少数情况），则原样透传。"""
    if not model:
        return TOKENHUB_MODEL
    bare = model.split("[")[0].strip()
    if bare.startswith("claude") or bare.startswith("anthropic"):
        return TOKENHUB_MODEL
    if bare in MODELS:
        return bare
    return TOKENHUB_MODEL


def anthropic_to_openai(body):
    """把 Anthropic /v1/messages 请求体转成 OpenAI chat/completions 请求体。"""
    out = {
        "model": _resolve_model(body.get("model", "deepseek-v4-flash")),
        "messages": [],
        "max_tokens": body.get("max_tokens", 1024),
        "stream": False,  # 网关侧总是非流式调 TokenHub，再自己包装成 SSE
    }
    # system
    system = body.get("system")
    if system:
        if isinstance(system, list):
            txt = "".join(b.get("text", "") for b in system if b.get("type") == "text")
        else:
            txt = str(system)
        if txt:
            out["messages"].append({"role": "system", "content": txt})
    # messages（含 tool_use / tool_result 转换）
    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            if isinstance(content, str):
                out["messages"].append({"role": "user", "content": content})
            else:
                parts = []
                for c in content:
                    t = c.get("type")
                    if t == "text":
                        parts.append({"type": "text", "text": c.get("text", "")})
                    elif t == "tool_result":
                        parts.append({
                            "type": "tool_result",
                            "tool_use_id": c.get("tool_use_id"),
                            "content": _extract_text(c.get("content", "")),
                        })
                out["messages"].append({"role": "user", "content": parts})
        elif role == "assistant":
            if isinstance(content, str):
                msg = {"role": "assistant", "content": content}
            else:
                msg = {"role": "assistant", "content": None, "tool_calls": []}
                texts = []
                for c in content:
                    t = c.get("type")
                    if t == "text":
                        texts.append(c.get("text", ""))
                    elif t == "tool_use":
                        msg["tool_calls"].append({
                            "id": c.get("id"),
                            "type": "function",
                            "function": {
                                "name": c.get("name"),
                                "arguments": json.dumps(c.get("input", {}), ensure_ascii=False),
                            },
                        })
                if texts:
                    msg["content"] = "".join(texts)
                if not msg["tool_calls"]:
                    del msg["tool_calls"]
            out["messages"].append(msg)
        elif role == "tool":
            out["messages"].append({
                "role": "tool",
                "tool_call_id": m.get("tool_use_id"),
                "content": _extract_text(m.get("content", "")),
            })
    # tools
    tools = body.get("tools")
    if tools:
        out["tools"] = [{
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        } for t in tools]
    return out


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return str(content)


def call_tokenhub(openai_body):
    """非流式调用 TokenHub OpenAI 端点，返回 (text, input_tokens, output_tokens)。"""
    url = TOKENHUB_BASE_URL + "/chat/completions"
    data = json.dumps(openai_body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + TOKENHUB_API_KEY)
    req.add_header("User-Agent", "tokenhub-minigate/1.0")
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choice = payload.get("choices", [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    usage = payload.get("usage", {})
    return text, reasoning, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def sse_events(text, reasoning, model, in_tok, out_tok):
    """把文本包成 Anthropic SSE 事件序列（兼容 Claude Code）。"""
    mid = "msg_" + uuid.uuid4().hex[:24]
    events = []
    events.append(("message_start", {
        "type": "message_start",
        "message": {
            "id": mid, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": 0},
        },
    }))
    # 思考过程（如果有）先作为 thinking block 可选输出
    if reasoning:
        events.append(("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }))
        events.append(("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": reasoning},
        }))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    # 正文 text block
    events.append(("content_block_start", {
        "type": "content_block_start", "index": 1,
        "content_block": {"type": "text", "text": ""},
    }))
    events.append(("content_block_delta", {
        "type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": text},
    }))
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 1}))
    events.append(("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": out_tok},
    }))
    events.append(("message_stop", {"type": "message_stop"}))
    return events


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # 静默

    def _send(self, code, body_bytes, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        # 解析路径（去掉 query string），兼容 CC 网关发现可能的各种写法
        path = self.path.split("?", 1)[0].rstrip("/")
        _log(f"GET {self.path} -> 匹配路径 '{path}'")
        if path in ("/v1/models", "/models"):
            # CC 2.1.231 对网关发现列表只保留 claude/anthropic 开头的 id，非此类直接丢弃，
            # 导致 deepseek/glm/kimi 等自定义模型永远"不存在"。
            # 对策：给 id 加 claude/ 前缀骗过过滤；网关转发前会在 _strip_window 里剥掉该前缀。
            # 同时保留裸名 + [1m]/[1M] 变体以兼容不同 CC 版本与 /model 直选。
            ids = []
            for m in MODELS:
                ids.append("claude/" + m)
                ids.append("claude/" + m + "[1m]")
                ids.append("claude/" + m + "[1M]")
                ids.append(m)
                ids.append(m + "[1m]")
                ids.append(m + "[1M]")
            # 广播 CC 配置里用到的"壳"模型名（claude/anthropic 开头），让 CC 实时校验通过
            for adv in GATEWAY_ADVERTISE:
                ids.append(adv)
            # 宽覆盖 Claude 名册：覆盖别名解析后的各种具体 id，确保网关发现必定命中
            for r in CLAUDE_ROSTER:
                ids.append(r)
            data = {"data": [
                {"id": i, "type": "model", "display_name": i, "created": 1700000000, "object": "model"}
                for i in ids
            ], "has_more": False}
            self._send(200, json.dumps(data).encode("utf-8"))
        elif self.path.rstrip("/") in ("/", "/health"):
            self._send(200, b'{"status":"ok","service":"tokenhub-minigate"}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._send(400, b'{"error":"bad json"}')
            return

        if self.path.rstrip("/") in ("/v1/messages", "/messages"):
            if not TOKENHUB_API_KEY:
                self._send(500, b'{"error":"TOKENHUB_API_KEY not set"}')
                return
            try:
                oa = anthropic_to_openai(body)
                model = body.get("model", "deepseek-v4-flash")  # 回传 CC 原始模型名（含 [1M] 标记），与请求一致
                text, reasoning, in_tok, out_tok = call_tokenhub(oa)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:500]
                _log(f"TokenHub HTTPError: {e.code} {detail}")
                self._send(502, json.dumps({"error": f"TokenHub upstream error: {detail}"}).encode("utf-8"))
                return
            except Exception as e:
                _log(f"gateway error: {e}")
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"))
                return
            # 以 SSE 返回，兼容 Claude Code（无论它请求 stream 与否都能解析）
            events = sse_events(text, reasoning, model, in_tok, out_tok)
            buf = []
            for name, data in events:
                buf.append(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
            payload = ("".join(buf)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._send(404, b'{"error":"not found"}')


def main():
    if not TOKENHUB_API_KEY:
        _log("⚠️ 未设置 TOKENHUB_API_KEY，网关将无法调用 TokenHub。请先 export。")
    _log(f"启动 TokenHub 网关 → http://localhost:{GATEWAY_PORT}")
    _log(f"上游 OpenAI 端点: {TOKENHUB_BASE_URL}/chat/completions")
    _log(f"已注册 {len(MODELS)} 款模型供 /v1/models 发现")
    server = ThreadingHTTPServer((GATEWAY_BIND, GATEWAY_PORT), Handler)
    if os.environ.get("GATEWAY_TLS") == "1":
        import ssl
        cert = os.environ.get("GATEWAY_CERT", "/tmp/gw_cert.pem")
        key = os.environ.get("GATEWAY_KEY", "/tmp/gw_key.pem")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        _log(f"TLS 已启用 (cert={cert})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
