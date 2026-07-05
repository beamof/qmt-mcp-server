# -*- coding: utf-8 -*-
"""轻量 MCP (Model Context Protocol) 服务端 —— 仅依赖 Python 标准库。

实现 JSON-RPC 2.0 over stdio / HTTP，覆盖 MCP 协议核心方法：
    initialize / notifications/initialized / ping
    tools/list / tools/call
    resources/list / resources/read

用法 (业务层注册工具后调用):
    from mcp_server_core import init_server, tool, resource, run_stdio, run_http

    init_server(name="my-server", instructions="...")
    run_stdio()           # stdio 模式
    run_http(host, port)  # HTTP 模式
"""

import json
import sys
import inspect
import typing
import hmac
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ──────────────── 协议常量 ────────────────

PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 标准错误码
ERR_PARSE_ERROR = -32700      # JSON 解析失败
ERR_INVALID_REQUEST = -32600  # 请求格式非法
ERR_METHOD_NOT_FOUND = -32601  # 方法不存在
ERR_INVALID_PARAMS = -32602   # 参数非法
ERR_INTERNAL = -32603         # 内部错误


# ──────────────── 注册表 ────────────────

@dataclass
class Tool:
    """单个 MCP 工具"""
    name: str
    description: str
    func: callable
    input_schema: dict


@dataclass
class Resource:
    """单个 MCP 资源"""
    uri: str
    name: str
    description: str
    mime_type: str
    func: callable


# 全局注册表（模块级单例）
_TOOLS: dict[str, Tool] = {}
_RESOURCES: dict[str, Resource] = {}
_SERVER_INFO = {"name": "mcp-server", "version": "1.0.0"}
_INSTRUCTIONS = ""


# ──────────────── 装饰器 ────────────────

_PY_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _build_input_schema(func: callable) -> dict:
    """从函数签名推导 JSON Schema (inputSchema)。

    - str/int/float/bool 映射到对应 JSON 类型
    - 有默认值的参数不进 required
    - 未识别类型默认 string
    """
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    properties = {}
    required = []
    for pname, param in sig.parameters.items():
        if pname in ("self",):
            continue
        ptype = hints.get(pname)
        json_type = _PY_TO_JSON_TYPE.get(ptype, "string")
        properties[pname] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool(name: str = None, description: str = None):
    """注册工具函数。

    Args:
        name: 工具名（默认用函数名）
        description: 描述（默认用 docstring 首段）
    """
    def decorator(func):
        tname = name or func.__name__
        tdesc = description or (inspect.getdoc(func) or "").strip()
        schema = _build_input_schema(func)
        _TOOLS[tname] = Tool(name=tname, description=tdesc, func=func, input_schema=schema)
        return func
    return decorator


def resource(uri: str, name: str = None, description: str = "", mime_type: str = "text/plain"):
    """注册资源。

    Args:
        uri: 资源 URI，如 "qmt://info"
        name: 资源名（默认用 uri）
        description: 描述
        mime_type: MIME 类型
    """
    def decorator(func):
        rname = name or uri
        rdesc = description or (inspect.getdoc(func) or "").strip()
        _RESOURCES[uri] = Resource(uri=uri, name=rname, description=rdesc,
                                    mime_type=mime_type, func=func)
        return func
    return decorator


def init_server(name: str = "mcp-server", version: str = "1.0.0",
                instructions: str = ""):
    """初始化 serverInfo / instructions"""
    global _SERVER_INFO, _INSTRUCTIONS
    _SERVER_INFO = {"name": name, "version": version}
    _INSTRUCTIONS = instructions


# ──────────────── JSON-RPC 消息处理 ────────────────

def _ok(req_id: typing.Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: typing.Any, code: int, message: str, data: typing.Any = None) -> dict:
    err_obj = {"code": code, "message": message}
    if data is not None:
        err_obj["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err_obj}


def handle_request(message: dict) -> dict | None:
    """处理一条 JSON-RPC 消息，返回响应 dict（通知返回 None）。

    Args:
        message: 已解析的 JSON-RPC 请求对象

    Returns:
        响应 dict（通知或批量请求的非通知子项）；通知返回 None
    """
    if not isinstance(message, dict):
        return _err(None, ERR_INVALID_REQUEST, "Request must be a JSON object")

    req_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {}) or {}
    is_notification = "id" not in message

    try:
        # ── 通知：无 id，无需响应 ──
        if method == "notifications/initialized":
            return None

        # ── 生命周期 ──
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"listChanged": True},
                },
                "serverInfo": _SERVER_INFO,
                "instructions": _INSTRUCTIONS,
            })

        if method == "ping":
            return _ok(req_id, {})

        # ── 工具 ──
        if method == "tools/list":
            tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in _TOOLS.values()
            ]
            return _ok(req_id, {"tools": tools})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            if tool_name not in _TOOLS:
                return _err(req_id, ERR_METHOD_NOT_FOUND,
                            f"Unknown tool: {tool_name}")
            t = _TOOLS[tool_name]
            try:
                result = t.func(**arguments)
                text = result if isinstance(result, str) else json.dumps(
                    result, ensure_ascii=False, default=str)
                return _ok(req_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                })
            except Exception as e:
                return _ok(req_id, {
                    "content": [{"type": "text", "text": f"工具执行失败: {e}"}],
                    "isError": True,
                })

        # ── 资源 ──
        if method == "resources/list":
            resources = [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mimeType": r.mime_type,
                }
                for r in _RESOURCES.values()
            ]
            return _ok(req_id, {"resources": resources})

        if method == "resources/read":
            uri = params.get("uri")
            if uri not in _RESOURCES:
                return _err(req_id, ERR_INVALID_PARAMS,
                            f"Unknown resource: {uri}")
            r = _RESOURCES[uri]
            try:
                content = r.func()
                text = content if isinstance(content, str) else json.dumps(
                    content, ensure_ascii=False, default=str)
                return _ok(req_id, {
                    "contents": [{
                        "uri": r.uri,
                        "mimeType": r.mime_type,
                        "text": text,
                    }]
                })
            except Exception as e:
                return _err(req_id, ERR_INTERNAL,
                            f"Resource read failed: {e}")

        # ── 未知方法 ──
        if is_notification:
            return None
        return _err(req_id, ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    except Exception as e:
        return _err(req_id, ERR_INTERNAL, f"Internal error: {e}")


def handle_raw(raw: str) -> str | None:
    """解析一行 JSON 并处理，返回响应 JSON 字符串（通知返回 None）。

    Args:
        raw: 单行 JSON-RPC 文本

    Returns:
        JSON 响应字符串（compact），通知返回 None
    """
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as e:
        return json.dumps(_err(None, ERR_PARSE_ERROR, f"Parse error: {e}"),
                          ensure_ascii=False)
    response = handle_request(message)
    if response is None:
        return None
    return json.dumps(response, ensure_ascii=False)


# ──────────────── stdio 传输 ────────────────

def run_stdio():
    """以 stdio 模式运行 MCP 服务。

    - 从 stdin 逐行读取 JSON-RPC 请求（每行一条）
    - 处理后将响应写到 stdout（每行一条）
    - 日志/调试输出走 stderr，绝不污染 stdout
    """
    sys.stderr.write(f"[mcp] {getattr(_SERVER_INFO, 'name', 'mcp-server')} "
                     f"stdio server starting (protocol {PROTOCOL_VERSION})\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle_raw(line)
        except Exception as e:
            sys.stderr.write(f"[mcp] handle error: {e}\n")
            sys.stderr.flush()
            continue
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


# ──────────────── HTTP 传输 ────────────────

def run_http(host: str, port: int, api_token: str = ""):
    """以 HTTP 模式运行 MCP 服务。

    - POST / 或 POST /mcp：接收单条 JSON-RPC，返回单条 JSON-RPC 响应
    - GET /：返回服务信息（健康检查 / 浏览器查看）
    - 每请求独立处理，无状态，适合本地单客户端
    - 若 api_token 非空，POST 请求须携带 `Authorization: Bearer <token>`；
      GET 健康检查保持公开（便于探活），但仅返回最少信息
    """
    server_info_local = dict(_SERVER_INFO)
    tools_count = len(_TOOLS)
    resources_count = len(_RESOURCES)
    auth_enabled = bool(api_token)
    # 预期 token 字节，常量时间比较用
    expected_token = api_token.encode("utf-8") if auth_enabled else b""

    def _check_auth(headers) -> bool:
        """校验 Authorization: Bearer <token>，常量时间比较。"""
        auth = headers.get("Authorization", "") or ""
        if not auth.lower().startswith("bearer "):
            return False
        provided = auth[7:].strip().encode("utf-8")
        # hmac.compare_digest 抗时序攻击；长度不同也会尽早返回但已抹平差异
        return hmac.compare_digest(provided, expected_token)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_unauthorized(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("WWW-Authenticate", 'Bearer realm="mcp"')
            body = json.dumps({"error": "Unauthorized: invalid or missing API token"})
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def do_GET(self):
            # 健康检查保持公开，但开启认证时仅返回最少信息（不暴露 tools 数）
            info = {
                "service": server_info_local,
                "protocolVersion": PROTOCOL_VERSION,
                "authRequired": auth_enabled,
            }
            if not auth_enabled:
                info["tools"] = tools_count
                info["resources"] = resources_count
                info["usage"] = "POST a JSON-RPC 2.0 message to this endpoint"
            else:
                info["usage"] = "POST a JSON-RPC 2.0 message with 'Authorization: Bearer <token>' header"
            self._send_json(200, info)

        def do_POST(self):
            # 认证校验（仅在 api_token 配置时启用）
            if auth_enabled and not _check_auth(self.headers):
                self._send_unauthorized()
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            if not raw.strip():
                self._send_json(200, _err(None, ERR_INVALID_REQUEST,
                                          "Empty body"))
                return
            response = handle_raw(raw)
            if response is None:
                # 通知：返回 202 Accepted + 空 result
                self._send_json(202, {"jsonrpc": "2.0", "result": {}})
            else:
                self._send_json(200, json.loads(response))

        def log_message(self, fmt, *args):
            # 日志走 stderr，不污染 stdout
            sys.stderr.write("[mcp-http] " + (fmt % args) + "\n")

    httpd = ThreadingHTTPServer((host, port), Handler)
    auth_note = ", auth=ON" if auth_enabled else ", auth=OFF"
    sys.stderr.write(f"[mcp] HTTP server listening on http://{host}:{port} "
                     f"(protocol {PROTOCOL_VERSION}{auth_note})\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[mcp] HTTP server stopped\n")
        httpd.shutdown()
