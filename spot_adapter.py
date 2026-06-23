#!/usr/bin/env python3
"""币安现货适配层 - Spot Trading Bot
注意：现货API与合约API完全分离
"""
import requests, time, hmac, hashlib, json
from typing import Optional

class BinanceSpotAdapter:
    name = "binance_spot"
    
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self.base = "https://api.binance.com"
        self.timeout = 15
    
    def _sign(self, params: str = "") -> str:
        if params:
            p = f"{params}&timestamp={int(time.time() * 1000)}"
        else:
            p = f"timestamp={int(time.time() * 1000)}"
        return hmac.new(self.secret.encode(), p.encode(), hashlib.sha256).hexdigest()
    
    def _request(self, method: str, endpoint: str, params: dict = None):
        for attempt in range(3):
            try:
                if params:
                    p = "&".join(f"{k}={v}" for k, v in params.items())
                    sig = self._sign(p)
                    url = f"{self.base}{endpoint}?{p}&signature={sig}"
                else:
                    sig = self._sign("")
                    url = f"{self.base}{endpoint}?signature={sig}"
                
                resp = requests.request(method, url,
                    headers={"X-MBX-APIKEY": self.api_key},
                    timeout=self.timeout)
                
                result = resp.json()
                if isinstance(result, dict) and result.get('code') == '-1021':
                    # 时间戳问题，用Binance服务器时间校准
                    raise Exception("Timestamp mismatch")
                return result
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
    
    # ===================== 账户 & 余额 =====================
    def get_balance(self, asset: str = "USDT") -> float:
        """获取指定资产余额"""
        data = self._request("GET", "/api/v3/account")
        if isinstance(data, dict):
            for bal in data.get('balances', []):
                if bal['asset'] == asset:
                    return float(bal['free'])
        return 0.0
    
    def get_all_spot_balances(self) -> dict:
        """获取所有现货余额，返回 {asset: free_amount}"""
        data = self._request("GET", "/api/v3/account")
        result = {}
        if isinstance(data, dict):
            for bal in data.get('balances', []):
                free = float(bal.get('free', 0))
                if free > 0:
                    result[bal['asset']] = free
        return result
    
    # ===================== K线数据 =====================
    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        """
        返回标准化K线: [[open_time, open, high, low, close, volume], ...]
        symbol格式: BTCUSDT (不用点号)
        """
        r = requests.get(
            f"{self.base}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=self.timeout
        )
        return r.json()
    
    # ===================== 订单 =====================
    def market_buy(self, symbol: str, quantity: float) -> bool:
        """市价买入"""
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": self._round_qty(symbol, quantity, "BUY")
        }
        data = self._request("POST", "/api/v3/order", params)
        return bool(data.get('orderId'))
    
    def market_sell(self, symbol: str, quantity: float) -> bool:
        """市价卖出"""
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": self._round_qty(symbol, quantity, "SELL")
        }
        data = self._request("POST", "/api/v3/order", params)
        return bool(data.get('orderId'))
    
    def get_open_orders(self, symbol: str = None) -> list:
        """获取当前挂单"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._request("GET", "/api/v3/openOrders", params)
        return data if isinstance(data, list) else []
    
    def cancel_order(self, symbol: str, order_id: int) -> bool:
        data = self._request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})
        return data.get('status') == 'CANCELED'
    
    def get_order(self, symbol: str, order_id: int) -> dict:
        return self._request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id})
    
    # ===================== 持仓查询 =====================
    def get_my_trades(self, symbol: str, limit: int = 20) -> list:
        """成交历史，用于计算持仓成本"""
        data = self._request("GET", "/api/v3/myTrades", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []
    
    # ===================== 辅助 =====================
    def _round_qty(self, symbol: str, qty: float, side: str) -> str:
        """根据交易对精度格式化数量"""
        # 现货精度查询
        info = self._get_symbol_info(symbol)
        step_size = 0.0
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break
        
        if step_size == 0:
            return f"{qty:.6f}"
        
        qty_str = f"{float(qty) // step_size * step_size:.8f}"
        # 去掉尾部0
        qty_str = qty_str.rstrip('0').rstrip('.')
        return qty_str
    
    def _get_symbol_info(self, symbol: str) -> dict:
        """缓存交易对精度信息"""
        if not hasattr(self, '_symbol_cache'):
            self._symbol_cache = {}
        
        if symbol not in self._symbol_cache:
            r = requests.get(f"{self.base}/api/v3/exchangeInfo", timeout=self.timeout)
            for s in r.json().get('symbols', []):
                if s['symbol'] == symbol:
                    self._symbol_cache[symbol] = s
                    break
        
        return self._symbol_cache.get(symbol, {'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '0.001'}]})
    
    def get_min_notional(self, symbol: str) -> float:
        """最小下单金额 (USDT)"""
        info = self._get_symbol_info(symbol)
        for f in info.get('filters', []):
            if f['filterType'] == 'MIN_NOTIONAL':
                return float(f['minNotional'])
        return 10.0  # 默认$10
    
    # ===================== 行情 =====================
    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        r = requests.get(
            f"{self.base}/api/v3/ticker/bookTicker",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        data = r.json()
        return {
            "bid": [(float(data['bidPrice']), float(data['bidQty']))],
            "ask": [(float(data['askPrice']), float(data['askQty']))]
        }
    
    def get_price(self, symbol: str) -> float:
        """当前价格"""
        r = requests.get(
            f"{self.base}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        return float(r.json().get('price', 0))
    
    def get_24h_ticker(self, symbol: str) -> dict:
        """24h行情统计"""
        r = requests.get(
            f"{self.base}/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        data = r.json()
        return {
            "price": float(data.get('lastPrice', 0)),
            "change": float(data.get('priceChangePercent', 0)),
            "high": float(data.get('highPrice', 0)),
            "low": float(data.get('lowPrice', 0)),
            "volume": float(data.get('quoteVolume', 0)),  # USDT成交量
        }
