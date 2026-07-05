# -*- coding: utf-8 -*-
"""
QMT MCP Server — 将 miniQMT 常用接口封装为 MCP 工具

用法 (stdio 模式, 供 Hermes 通过 SSH 调用):
    python qmt-mcp-server.py

用法 (HTTP 模式, 独立运行):
    python qmt-mcp-server.py --transport http --port 8765

注: 仅依赖 Python 标准库 + xtquant，无需安装 mcp / pandas 等第三方包。
"""

import sys
import os
import argparse
import json

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xtquant import xtconstant

from mcp_server_core import init_server, tool, resource, run_stdio, run_http
from qmt_client import get_client
from config import MCP_HOST, MCP_PORT, MCP_API_TOKEN

# ──────────────── 初始化 MCP Server ────────────────

init_server(
    name="qmt",
    version="1.0.0",
    instructions="QMT 迷你量化交易系统 MCP 服务。提供 A 股行情查询、账户查询、交易下单等功能。"
                 "股票代码格式: {代码}.{市场}，如 600519.SH、000001.SZ。"
                 "miniQMT 客户端必须处于运行登录状态。",
)


# ══════════════════════════════════════════════════
#                  行情查询工具
# ══════════════════════════════════════════════════

@tool()
def get_stock_quote(stock_codes: str) -> str:
    """获取股票实时行情快照。输入股票代码，逗号分隔，如 "600519.SH,000001.SZ"。

    返回最新价、涨跌幅、成交量、成交额等实时数据。
    miniQMT 客户端必须运行才能获取实时行情。
    """
    try:
        codes = [c.strip() for c in stock_codes.split(",") if c.strip()]
        if not codes:
            return "请提供至少一个股票代码。"
        client = get_client()
        ticks = client.get_full_tick(codes)

        if not ticks:
            return "未获取到行情数据。请确认 miniQMT 客户端已启动并登录，且输入代码格式正确（如 600519.SH）。"

        lines = []
        for code, tick in ticks.items():
            if tick is None:
                lines.append(f"{code}: 无数据")
                continue
            last = tick.get("lastPrice", 0)
            prev = tick.get("lastClose", 0)
            change_pct = (last - prev) / prev * 100 if prev else 0
            lines.append(
                f"【{code}】\n"
                f"  最新价: {last:.3f}  涨跌幅: {change_pct:+.2f}%\n"
                f"  今开: {tick.get('open', 0):.3f}  最高: {tick.get('high', 0):.3f}  "
                f"最低: {tick.get('low', 0):.3f}\n"
                f"  成交量: {tick.get('volume', 0)}  成交额: {tick.get('amount', 0):.0f}\n"
                f"  换手率: {tick.get('turnoverRate', 0):.2f}%  量比: {tick.get('volumeRatio', 0):.2f}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"获取行情失败: {e}"


@tool()
def get_kline_data(stock_code: str, period: str = "1d", count: int = 100,
                   start_time: str = "", end_time: str = "") -> str:
    """获取股票历史K线数据。

    Args:
        stock_code: 股票代码，如 "600519.SH"
        period: K线周期，支持 1m/5m/15m/30m/1h/1d/1w/1M，默认 1d
        count: 获取数量，默认 100
        start_time: 起始日期 "20240101"（可选）
        end_time: 结束日期 "20241231"（可选）

    返回最近N根K线的 OHLCV 数据。
    """
    try:
        client = get_client()
        records = client.get_kline(
            stock_code, period=period, count=count,
            start_time=start_time, end_time=end_time
        )
        if not records:
            return f"未获取到 {stock_code} 的K线数据。"

        lines = [f"{'时间':>20} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'成交量':>12} {'成交额':>14}"]
        lines.append("-" * 90)

        # 只显示最后 count 条（可能返回更多）
        display = records[-count:] if len(records) > count else records
        for r in display:
            ts = r.get("time_str", str(r.get("time", "")))
            lines.append(
                f"{ts:>20} {r.get('open', 0):>10.3f} {r.get('high', 0):>10.3f} "
                f"{r.get('low', 0):>10.3f} {r.get('close', 0):>10.3f} "
                f"{r.get('volume', 0):>12.0f} {r.get('amount', 0):>14.0f}"
            )
        return f"【{stock_code}】 {period} K线 ({len(display)} 条)\n" + "\n".join(lines)
    except Exception as e:
        return f"获取K线失败: {e}"


@tool()
def get_instrument_detail(stock_code: str) -> str:
    """获取证券详细信息，包括名称、涨跌停价、交易状态等。

    Args:
        stock_code: 股票代码，如 "600519.SH"
    """
    try:
        client = get_client()
        detail = client.get_instrument_detail(stock_code)
        if not detail:
            return f"未找到 {stock_code} 的证券信息。"

        return (
            f"【{stock_code}】{detail.get('InstrumentName', '')}\n"
            f"  交易所: {detail.get('ExchangeID', '')}\n"
            f"  证券类型: {detail.get('InstrumentType', '')}\n"
            f"  涨跌停板: {detail.get('UpDownLimit', '')}\n"
            f"  每手数量: {detail.get('VolumeMultiple', 1)}\n"
            f"  最小变动价位: {detail.get('PriceTick', 0.01)}\n"
            f"  上市日期: {detail.get('OpenDate', '')}\n"
            f"  所属行业: {detail.get('Industry', '')}"
        )
    except Exception as e:
        return f"获取证券详情失败: {e}"


@tool()
def get_stock_list(market: int = -1) -> str:
    """获取沪深A股股票列表。

    Args:
        market: 市场筛选 (-1=全部, 0=深圳, 1=上海)，默认 -1
    """
    try:
        client = get_client()
        stocks = client.get_stock_list(market)
        if not stocks:
            return "未获取到股票列表。"

        market_names = {-1: "全部", 0: "深圳", 1: "上海"}
        lines = [f"沪深A股列表 ({market_names.get(market, '全部')})，共 {len(stocks)} 只"]

        # 显示前 50 只
        for s in stocks[:50]:
            code = s.get("code", s) if isinstance(s, dict) else str(s)
            lines.append(f"  {code}")
        if len(stocks) > 50:
            lines.append(f"  ... 省略 {len(stocks) - 50} 只")
        return "\n".join(lines)
    except Exception as e:
        return f"获取股票列表失败: {e}"


# ══════════════════════════════════════════════════
#                  账户查询工具
# ══════════════════════════════════════════════════

@tool()
def get_account_asset() -> str:
    """查询 QMT 账户资产信息，包括总资产、可用资金、持仓市值、冻结资金。

    需要确认 miniQMT 客户端已启动并登录。
    """
    try:
        client = get_client()
        asset = client.get_asset()
        if "error" in asset:
            return asset["error"]

        profit = asset["total_asset"] - asset["market_value"] - (asset["total_asset"] - asset["market_value"] - asset["cash"])
        return (
            f"【账户 {asset['account_id']}】{asset['fetch_time']}\n"
            f"  总资产: {asset['total_asset']:,.2f}\n"
            f"  持仓市值: {asset['market_value']:,.2f}\n"
            f"  可用资金: {asset['cash']:,.2f}\n"
            f"  冻结资金: {asset['frozen_cash']:,.2f}"
        )
    except Exception as e:
        return f"查询资产失败: {e}"


@tool()
def get_positions() -> str:
    """查询 QMT 账户当前持仓列表，包括股票代码、持仓数量、成本价、市值、盈亏等。

    需要确认 miniQMT 客户端已启动并登录。
    """
    try:
        client = get_client()
        positions = client.get_positions()
        if not positions:
            return "当前无持仓。"

        lines = [f"{'代码':>12} {'名称':>8} {'持仓':>8} {'可用':>8} {'成本价':>10} {'市值':>14} {'盈亏':>12}"]
        lines.append("-" * 76)
        total_profit = 0
        for p in positions:
            profit = p.get("profit", 0)
            total_profit += profit
            lines.append(
                f"{p['stock_code']:>12} {p['stock_name']:>8} {p['volume']:>8} "
                f"{p['can_use_volume']:>8} {p['open_price']:>10.3f} "
                f"{p['market_value']:>14,.2f} {profit:>+12,.2f}"
            )
        lines.append("-" * 76)
        lines.append(f"持仓 {len(positions)} 只，总盈亏: {total_profit:>+,.2f}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询持仓失败: {e}"


@tool()
def get_orders() -> str:
    """查询 QMT 当日委托列表，包括委托代码、价格、数量、状态等。

    需要确认 miniQMT 客户端已启动并登录。
    """
    try:
        client = get_client()
        orders = client.get_orders()
        if not orders:
            return "当日无委托记录。"

        lines = [f"{'代码':>12} {'名称':>8} {'方向':>4} {'价格':>10} {'数量':>8} {'已成':>8} {'状态':>8} {'时间':>8}"]
        lines.append("-" * 82)
        for o in orders:
            direction = "买入" if o.get("direction", 0) == xtconstant.DIRECTION_FLAG_BUY else "卖出"
            lines.append(
                f"{o['stock_code']:>12} {o['stock_name']:>8} {direction:>4} "
                f"{o['price']:>10.3f} {o['order_volume']:>8} "
                f"{o['traded_volume']:>8} {o.get('status_msg', ''):>8} {o.get('order_time', ''):>8}"
            )
        return f"当日委托 {len(orders)} 笔\n" + "\n".join(lines)
    except Exception as e:
        return f"查询委托失败: {e}"


@tool()
def get_trades() -> str:
    """查询 QMT 当日成交列表，包括成交代码、价格、数量、金额等。

    需要确认 miniQMT 客户端已启动并登录。
    """
    try:
        client = get_client()
        trades = client.get_trades()
        if not trades:
            return "当日无成交记录。"

        lines = [f"{'代码':>12} {'名称':>8} {'方向':>4} {'成交价':>10} {'数量':>8} {'金额':>14} {'时间':>8}"]
        lines.append("-" * 80)
        total_amount = 0
        for t in trades:
            direction = "买入" if t.get("direction", 0) == xtconstant.DIRECTION_FLAG_BUY else "卖出"
            total_amount += t.get("traded_amount", 0)
            lines.append(
                f"{t['stock_code']:>12} {t['stock_name']:>8} {direction:>4} "
                f"{t['traded_price']:>10.3f} {t['traded_volume']:>8} "
                f"{t['traded_amount']:>14,.2f} {t.get('traded_time', ''):>8}"
            )
        lines.append("-" * 80)
        lines.append(f"当日成交 {len(trades)} 笔，总金额: {total_amount:,.2f}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询成交失败: {e}"


# ══════════════════════════════════════════════════
#                  交易操作工具
# ══════════════════════════════════════════════════

@tool()
def place_order(stock_code: str, direction: str, price: float, volume: int,
                strategy_name: str = "") -> str:
    """提交买卖委托订单。

    Args:
        stock_code: 股票代码，如 "600519.SH"
        direction: 交易方向 "buy" 买入 或 "sell" 卖出
        price: 委托价格
        volume: 委托数量（股，必须为100的整数倍）
        strategy_name: 策略名称备注（可选）

    ⚠️ 此操作会真实下单，请确认参数无误后再调用。
    """
    try:
        if direction.lower() in ("buy", "买入", "b"):
            dir_flag = xtconstant.STOCK_BUY
            dir_name = "买入"
        elif direction.lower() in ("sell", "卖出", "s"):
            dir_flag = xtconstant.STOCK_SELL
            dir_name = "卖出"
        else:
            return f"无效的交易方向: {direction}，请使用 buy/sell"

        if volume <= 0:
            return "委托数量必须大于0。"
        if volume % 100 != 0:
            return f"警告: 委托数量 {volume} 不是100的整数倍，A股通常要求整手(100股)。"

        if price <= 0:
            return "委托价格必须大于0。"

        client = get_client()
        result = client.place_order(
            stock_code=stock_code,
            price=price,
            volume=volume,
            direction=dir_flag,
            strategy_name=strategy_name
        )

        if "error" in result:
            return result["error"]

        return (
            f"【下单成功】\n"
            f"  操作: {dir_name} {stock_code}\n"
            f"  价格: {price:.3f}  数量: {volume} 股\n"
            f"  订单号: {result['order_id']}\n"
            f"  系统编号: {result.get('order_sysid', '')}\n"
            f"  状态: {result.get('status_msg', '')}"
        )
    except Exception as e:
        return f"下单失败: {e}"


@tool()
def cancel_order(order_id: int) -> str:
    """撤销指定委托订单。

    Args:
        order_id: 要撤销的委托订单号（从 get_orders 查询获取）

    ⚠️ 此操作会真实撤单。
    """
    try:
        if order_id <= 0:
            return "请提供有效的订单号。"

        client = get_client()
        result = client.cancel_order(order_id)

        if "error" in result:
            return result["error"]

        return (
            f"【撤单请求已发送】\n"
            f"  订单号: {result['order_id']}\n"
            f"  结果: {result.get('cancel_result', '')}"
        )
    except Exception as e:
        return f"撤单失败: {e}"


# ══════════════════════════════════════════════════
#                  辅助资源
# ══════════════════════════════════════════════════

@resource("qmt://info")
def qmt_info() -> str:
    """QMT 服务基本信息和连接状态"""
    import config
    try:
        client = get_client()
        status = client.get_account_status()
        status_str = json.dumps(status, ensure_ascii=False, indent=2) if status else "未连接"
    except Exception as e:
        status_str = f"连接失败: {e}"

    return json.dumps({
        "qmt_path": config.QMT_PATH,
        "account_id": config.ACCOUNT_ID,
        "account_type": config.ACCOUNT_TYPE,
        "account_status": status_str,
    }, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════
#                    主入口
# ══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="QMT MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="传输协议 (默认 stdio)")
    parser.add_argument("--host", default=MCP_HOST, help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="HTTP 监听端口")
    args = parser.parse_args()

    if args.transport == "http":
        run_http(args.host, args.port, api_token=MCP_API_TOKEN)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
