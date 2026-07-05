# -*- coding: utf-8 -*-
"""QMT 连接封装 - xtdata 行情 + xttrader 交易"""

import time
import threading
from datetime import datetime

from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant

from config import QMT_PATH, ACCOUNT_ID, ACCOUNT_TYPE, SESSION_ID


class QMTClient:
    """QMT 客户端封装，懒初始化交易连接"""

    def __init__(self):
        self._trader = None
        self._account = None
        self._connected = False
        self._lock = threading.Lock()

    # ──────────────── 交易连接管理 ────────────────

    def _ensure_trader(self):
        """确保交易连接已建立"""
        if self._trader and self._connected:
            return self._trader, self._account

        with self._lock:
            if self._trader and self._connected:
                return self._trader, self._account

            try:
                self._trader = XtQuantTrader(QMT_PATH, SESSION_ID)
                self._trader.start()
                result = self._trader.connect()
                if result != 0:
                    raise RuntimeError(f"连接 miniQMT 终端失败 (code={result})，请确保 miniQMT 已启动并登录。")

                self._account = StockAccount(ACCOUNT_ID, ACCOUNT_TYPE)
                sub_result = self._trader.subscribe(self._account)
                if sub_result != 0:
                    raise RuntimeError(f"订阅账户失败 (code={sub_result})，请检查账号 {ACCOUNT_ID}。")

                self._connected = True
                return self._trader, self._account
            except Exception:
                self._connected = False
                raise

    def disconnect(self):
        """断开交易连接"""
        if self._trader:
            try:
                self._trader.stop()
            except Exception:
                pass
            self._trader = None
            self._account = None
            self._connected = False

    # ──────────────── 行情接口 (xtdata) ────────────────

    def get_full_tick(self, stock_codes: list) -> dict:
        """获取实时行情快照

        Args:
            stock_codes: 股票代码列表，如 ["600519.SH", "000001.SZ"]

        Returns:
            dict: {code: tick_data}
        """
        result = xtdata.get_full_tick(stock_codes)
        if result is None:
            return {}
        return result

    def get_kline(self, stock_code: str, period: str = "1d", count: int = 100,
                  start_time: str = "", end_time: str = "") -> list:
        """获取K线数据

        Args:
            stock_code: 股票代码，如 "600519.SH"
            period: K线周期，支持 1m/5m/15m/30m/1h/1d/1w/1M
            count: 获取数量
            start_time: 起始时间 "20240101"
            end_time: 结束时间 "20241231"

        Returns:
            list: K线记录列表 (每条为 dict)；通过 xtdata 返回的 DataFrame
                  自身方法 to_dict(orient="records") 转换，无需显式 import pandas。
        """
        if start_time or end_time:
            data = xtdata.get_market_data_ex(
                [stock_code], period=period,
                start_time=start_time, end_time=end_time, count=count, fill_data=True
            )
        else:
            data = xtdata.get_market_data_ex(
                [stock_code], period=period, count=count, fill_data=True
            )

        if stock_code in data and data[stock_code] is not None:
            df = data[stock_code]
            # 重置索引让 time 成为列
            df = df.reset_index()
            # 格式化时间戳
            if "time" in df.columns:
                df["time_str"] = df["time"].apply(
                    lambda t: datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        if t > 1e12 else datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
                )
            return df.to_dict(orient="records")
        return []

    def get_instrument_detail(self, stock_code: str) -> dict:
        """获取证券详细信息

        Args:
            stock_code: 股票代码，如 "600519.SH"
        """
        return xtdata.get_instrument_detail(stock_code)

    def get_stock_list(self, market: int = -1) -> list:
        """获取股票列表

        Args:
            market: 市场代码 (-1=全部, 0=深圳, 1=上海)
        """
        return xtdata.get_stock_list_in_sector("沪深A股")

    def get_index_weight(self, index_code: str) -> dict:
        """获取指数成分股权重"""
        return xtdata.get_index_weight(index_code)

    def download_data(self, stock_codes: list, period: str = "1d", start_time: str = ""):
        """下载历史数据到本地"""
        xtdata.download_history_data(stock_codes, period=period, start_time=start_time)

    # ──────────────── 账户查询接口 ────────────────

    def get_asset(self) -> dict:
        """查询账户资产"""
        trader, account = self._ensure_trader()
        asset = trader.query_stock_asset(account)
        if asset is None:
            return {"error": "查询资产失败，请确认 miniQMT 已登录。"}

        return {
            "account_id": asset.account_id,
            "total_asset": asset.total_asset,        # 总资产
            "cash": asset.cash,                       # 可用资金
            "frozen_cash": asset.frozen_cash,          # 冻结资金
            "market_value": asset.market_value,        # 持仓市值
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_positions(self) -> list:
        """查询持仓列表"""
        trader, account = self._ensure_trader()
        positions = trader.query_stock_positions(account)
        if not positions:
            return []

        result = []
        for pos in positions:
            result.append({
                "stock_code": pos.stock_code,
                "stock_name": pos.instrument_name,
                "volume": pos.volume,                  # 持仓数量
                "can_use_volume": pos.can_use_volume,  # 可用数量
                "frozen_volume": pos.frozen_volume,    # 冻结数量
                "on_road_volume": pos.on_road_volume,  # 在途数量
                "yesterday_volume": pos.yesterday_volume,  # 昨仓
                "open_price": pos.open_price,          # 开仓均价
                "market_value": pos.market_value,      # 市值
                "profit": pos.market_value - pos.open_price * pos.volume if pos.volume else 0,  # 盈亏估算
            })
        return result

    def get_orders(self) -> list:
        """查询当日委托"""
        trader, account = self._ensure_trader()
        orders = trader.query_stock_orders(account)
        if not orders:
            return []

        result = []
        for o in orders:
            result.append({
                "stock_code": o.stock_code,
                "stock_name": o.instrument_name,
                "order_id": o.order_id,
                "order_sysid": o.order_sysid,
                "order_volume": o.order_volume,        # 委托数量
                "traded_volume": o.traded_volume,      # 已成交数量
                "price": o.price,                      # 委托价格
                "traded_price": o.traded_price,        # 成交均价
                "order_type": o.order_type,            # 委托类型
                "order_status": o.order_status,        # 委托状态
                "status_msg": o.status_msg,
                "order_time": datetime.fromtimestamp(o.order_time).strftime("%H:%M:%S") if o.order_time else "",
                "strategy_name": o.strategy_name,
                "direction": o.direction,              # 买卖方向
            })
        return result

    def get_trades(self) -> list:
        """查询当日成交"""
        trader, account = self._ensure_trader()
        trades = trader.query_stock_trades(account)
        if not trades:
            return []

        result = []
        for t in trades:
            result.append({
                "stock_code": t.stock_code,
                "stock_name": t.instrument_name,
                "traded_id": t.traded_id,
                "order_id": t.order_id,
                "traded_volume": t.traded_volume,      # 成交数量
                "traded_price": t.traded_price,        # 成交价格
                "traded_amount": t.traded_amount,      # 成交金额
                "traded_time": datetime.fromtimestamp(t.traded_time).strftime("%H:%M:%S") if t.traded_time else "",
                "direction": t.direction,              # 买卖方向
                "order_sysid": t.order_sysid,
            })
        return result

    def get_account_status(self) -> dict:
        """查询账户连接状态"""
        trader, account = self._ensure_trader()
        status = trader.query_account_status(account)
        if status:
            return {
                "account_id": status.account_id,
                "account_type": status.account_type,
                "status": status.status,
                "status_msg": status.status_msg,
            }
        return {"error": "无法获取账户状态"}

    # ──────────────── 交易接口 ────────────────

    def place_order(self, stock_code: str, price: float, volume: int,
                    direction: int, order_type: int = 0, strategy_name: str = "") -> dict:
        """下单

        Args:
            stock_code: 股票代码 "600519.SH"
            price: 委托价格
            volume: 委托数量 (股)
            direction: 买卖方向 (xtconstant.STOCK_BUY=50 / xtconstant.STOCK_SELL=51)
            order_type: 报价类型 (默认 LATEST=最新价)
            strategy_name: 策略名称备注
        """
        trader, account = self._ensure_trader()
        response = trader.order_stock(
            account, stock_code, order_type, volume, direction,
            price_type=xtconstant.FIX_PRICE, price=price,
            strategy_name=strategy_name
        )
        if response:
            return {
                "order_id": response.order_id,
                "stock_code": response.stock_code,
                "order_sysid": response.order_sysid,
                "status_msg": response.status_msg,
            }
        return {"error": "下单失败"}

    def cancel_order(self, order_id: int) -> dict:
        """撤单

        Args:
            order_id: 委托订单号
        """
        trader, account = self._ensure_trader()
        response = trader.cancel_order_stock(account, order_id)
        if response:
            return {
                "order_id": response.order_id,
                "cancel_result": response.status_msg,
            }
        return {"error": "撤单失败"}


# 全局单例
_client = None


def get_client() -> QMTClient:
    """获取 QMT 客户端单例"""
    global _client
    if _client is None:
        _client = QMTClient()
    return _client
