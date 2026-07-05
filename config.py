# -*- coding: utf-8 -*-
"""QMT MCP 服务配置"""

import os
import time

# QMT mini 客户端路径
QMT_PATH = os.environ.get("QMT_PATH", r"..\..\userdata_mini")

# 交易账号
ACCOUNT_ID = os.environ.get("QMT_ACCOUNT", "")

# 账户类型
ACCOUNT_TYPE = os.environ.get("QMT_ACCOUNT_TYPE", "STOCK")

# MCP 服务监听端口 (HTTP 模式)
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))

# HTTP 模式 API Token 认证 (留空则不启用认证)
# 客户端通过 Authorization: Bearer <token> 头部传递
MCP_API_TOKEN = os.environ.get("MCP_API_TOKEN", "")

# Session ID (每次启动唯一)
SESSION_ID = int(time.time())
