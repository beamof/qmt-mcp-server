# QMT MCP Server

将迅投 miniQMT 的常用接口封装为 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) 工具，让 AI 助手（如 Claude、Hermes 等）能够直接查询 A 股行情、查询账户、下达交易指令。

> ⚠️ **风险提示**：本服务包含真实下单 / 撤单功能（`place_order` / `cancel_order`），调用前请务必确认参数，避免造成实际资金损失。

---

## 功能概览

本项目提供三类 MCP 工具：

| 类别 | 工具 | 说明 |
|------|------|------|
| **行情查询** | `get_stock_quote` | 获取实时行情快照（最新价、涨跌幅、成交量等） |
| | `get_kline_data` | 获取历史 K 线（OHLCV），支持 1m / 5m / 15m / 30m / 1h / 1d / 1w / 1M |
| | `get_instrument_detail` | 获取证券详情（名称、涨跌停、行业等） |
| | `get_stock_list` | 获取沪深 A 股股票列表 |
| **账户查询** | `get_account_asset` | 查询账户资产（总资产、可用资金、持仓市值等） |
| | `get_positions` | 查询当前持仓 |
| | `get_orders` | 查询当日委托 |
| | `get_trades` | 查询当日成交 |
| **交易操作** | `place_order` | 提交买卖委托 |
| | `cancel_order` | 撤销指定委托 |

另外提供一个资源 `qmt://info`，用于查看 QMT 连接路径、账号和连接状态。

---

## 前置依赖

1. **miniQMT 客户端**：必须已安装、启动并登录（行情和交易都依赖 miniQMT 进程）。
2. **Python 3.10+**
3. **xtquant**（miniQMT 自带，不在 PyPI）：将 miniQMT 安装目录下的 `xtquant` 加入 `PYTHONPATH`，或拷贝到项目的 `site-packages`。

> 💡 **本项目自身零第三方依赖**：MCP 协议层用 Python 标准库手写（见 [`mcp_server_core.py`](./mcp_server_core.py)），**无需 `pip install mcp` 或 `pandas`**。
> （`xtquant` 内部会 import pandas 作为传递依赖，但我们的代码不直接依赖它。）

设置 xtquant 路径（Windows 示例）：

```bash
set PYTHONPATH=D:\QMT\bin.x64\Lib\site-packages
```

---

## 配置

所有配置项通过环境变量覆盖，默认值见 [`config.py`](./config.py)：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QMT_PATH` | `..\..\userdata_mini` | miniQMT 的 `userdata_mini` 路径 |
| `QMT_ACCOUNT` | （空） | 交易账号，**必填** |
| `QMT_ACCOUNT_TYPE` | `STOCK` | 账户类型 |
| `MCP_HOST` | `0.0.0.0` | HTTP 模式监听地址 |
| `MCP_PORT` | `8765` | HTTP 模式监听端口 |
| `MCP_API_TOKEN` | （空） | HTTP 模式 API Token，留空则不启用认证；设置后 POST 请求须携带 `Authorization: Bearer <token>` |

建议在启动前导出环境变量：

```bash
export QMT_ACCOUNT=你的资金账号
export QMT_PATH="D:/QMT/userdata_mini"
# HTTP 模式建议设置 API Token（留空则不启用认证）
export MCP_API_TOKEN="你的随机 token"
```

---

## 运行方式

### 1. stdio 模式（默认，供 MCP 客户端通过子进程调用）

```bash
python qmt-mcp-server.py
```

### 2. HTTP / SSE 模式（独立运行，远程访问）

```bash
python qmt-mcp-server.py --transport http --host 0.0.0.0 --port 8765
```

**HTTP API Token 认证**：当设置了 `MCP_API_TOKEN` 环境变量时，所有 POST 请求须携带 `Authorization: Bearer <token>` 头部；GET `/` 健康检查保持公开，但仅返回最少信息（不暴露工具数量）。未设置 token 时认证关闭，向后兼容。客户端调用示例：

```bash
curl -X POST http://host:8765/ \
  -H "Authorization: Bearer <你的 token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 在 MCP 客户端中接入

### Claude Desktop / Hermes（stdio）

在客户端配置文件中加入：

```json
{
  "mcpServers": {
    "qmt": {
      "command": "python",
      "args": ["D:/QMT-gjzq/workspace/mcp_server/qmt-mcp-server.py"],
      "env": {
        "QMT_ACCOUNT": "你的资金账号",
        "QMT_PATH": "D:/QMT/userdata_mini"
      }
    }
  }
}
```

### HTTP / SSE 模式

服务以 `--transport http` 启动后，客户端通过 SSE URL 接入：

```
http://<host>:8765/sse
```

---

## 代码结构

```
mcp_server/
├── qmt-mcp-server.py   # MCP Server 入口，定义所有工具/资源（业务层）
├── mcp_server_core.py  # 轻量 MCP 协议层（纯标准库，stdio + HTTP）
├── qmt_client.py       # QMT 客户端封装（xtdata 行情 + xttrader 交易），单例
├── config.py           # 配置项（环境变量读取）
├── requirements.txt    # 依赖说明（项目自身零第三方依赖）
├── ord_attrs.txt       # 委托对象字段参考
├── pos_attrs.txt       # 持仓对象字段参考
├── trd_attrs.txt       # 成交对象字段参考
└── README.md
```

- `qmt_client.QMTClient` 采用懒初始化：行情接口直接走 `xtdata`，交易接口在首次调用时才连接 `XtQuantTrader` 并订阅账户。
- 全局通过 `get_client()` 获取单例，避免重复连接。

---

## 股票代码格式

统一使用 `代码.市场` 格式，例如：

- 上交所：`600519.SH`、`000001.SH`（上证指数）
- 深交所：`000001.SZ`、`300750.SZ`

---

## 使用示例

向 AI 提问示例：

- 「查一下贵州茅台和五粮液的最新行情」→ 调用 `get_stock_quote`
- 「下载 600519 最近 100 根日线」→ 调用 `get_kline_data`
- 「我的账户现在有多少可用资金？」→ 调用 `get_account_asset`
- 「以 1700 元买入 100 股 600519.SH」→ 调用 `place_order` ⚠️ 真实下单

---

## 常见问题

- **连接 miniQMT 失败 (code≠0)**：确认 miniQMT 已启动并登录对应账号；`QMT_PATH` 是否指向正确的 `userdata_mini` 目录。
- **订阅账户失败**：检查 `QMT_ACCOUNT` 是否与 miniQMT 登录账号一致。
- **行情数据为空**：miniQMT 必须在交易/行情会话中，部分数据需要先订阅或下载。
- **`ModuleNotFoundError: xtquant`**：将 miniQMT 自带的 `xtquant` 目录加入 `PYTHONPATH`。

---

## 免责声明

本项目仅作技术交流与个人量化研究用途，不构成任何投资建议。使用本工具进行真实交易造成的任何盈亏，由使用者自行承担。
