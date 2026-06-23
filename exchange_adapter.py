#!/usr/bin/env python3
"""交易所适配层 - 统一币安/OKX接口
所有交易所调用必须通过本模块，不允许直接写requests
"""
import requests, time, hmac, hashlib, json
from typing import Optional

# ===================== 币安 =====================
class BinanceAdapter:
    name = "binance"
    
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self.base = "https://fapi.binance.com"
        self.timeout = 15
    
    def _sign(self, params: str) -> str:
        ts = str(int(time.time() * 1000))
        p = f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
        return hmac.new(self.secret.encode(), p.encode(), hashlib.sha256).hexdigest()
    
    def _request(self, method, endpoint, params=""):
        def _call():
            sig = self._sign(params)
            url = f"{self.base}{endpoint}?{params}&signature={sig}" if params else f"{self.base}{endpoint}?signature={sig}"
            resp = requests.request(method, url, headers={"X-MBX-APIKEY": self.api_key}, timeout=self.timeout)
            return resp.json()
        
        for attempt in range(3):
            try:
                return _call()
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
    
    def get_balance(self) -> float:
        data = self._request("GET", "/fapi/v2/account")
        return float(data.get('availableBalance', 0))
    
    def get_positions(self, symbol: str) -> dict:
        """返回 {side: {"dir": "LONG"/"SHORT", "qty": float, "entry": float}}"""
        positions = {}
        data = self._request("GET", "/fapi/v2/positionRisk", f"symbol={symbol}")
        if isinstance(data, list):
            for p in data:
                amt = float(p.get('positionAmt', 0))
                if amt != 0:
                    side = p['positionSide']
                    positions[side] = {
                        "dir": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry": abs(float(p['entryPrice']))
                    }
        return positions
    
    def get_klines(self, symbol: str, interval: str, limit: int = 100):
        """返回标准化K线: [[open_time, open, high, low, close, volume, ...], ...]"""
        r = requests.get(
            f"{self.base}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=self.timeout
        )
        return r.json()  # Binance格式: [ [open_time, o, h, l, c, v, ...], ... ]
    
    def market_order(self, symbol: str, side: str, position_side: str, quantity: float) -> bool:
        """返回是否成功"""
        params = f"symbol={symbol}&side={side}&positionSide={position_side}&type=MARKET&quantity={quantity:.3f}"
        data = self._request("POST", "/fapi/v1/order", params)
        order_id = data.get("orderId")
        return bool(order_id)
    
    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        """返回买卖盘口 {bid: [(price, qty), ...], ask: [(price, qty), ...]}"""
        r = requests.get(
            f"{self.base}/fapi/v1/ticker/bookTicker",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        data = r.json()
        return {
            "bid": [(float(data['bidPrice']), float(data['bidQty']))],
            "ask": [(float(data['askPrice']), float(data['askQty']))]
        }
    
    def get_funding_rate(self, symbol: str) -> float:
        r = requests.get(
            f"{self.base}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        data = r.json()
        return float(data.get('lastFundingRate', 0))
    
    def get_open_interest(self, symbol: str) -> float:
        r = requests.get(
            f"{self.base}/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=self.timeout
        )
        data = r.json()
        return float(data.get('openInterest', 0))


# ===================== OKX =====================
class OKXAdapter:
    name = "okx"
    
    # OKX时间区间映射
    INTERVAL_MAP = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
        "6h": "6H", "12h": "12H", "1d": "1D", "2d": "2D",
        "3d": "3D", "5d": "5D", "1w": "1W", "2w": "2W"
    }
    
    def __init__(self, api_key: str, secret: str, passphrase: str):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.base = "https://www.okx.com"
        self.timeout = 15
    
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = timestamp + method + path + body
        mac = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256)
        return mac.hexdigest()
    
    def _headers(self, method: str, path: str, body: str = ""):
        timestamp = str(int(time.time()))
        sign = self._sign(timestamp, method, path, body)
        return {
            "OKX-APIKEY": self.api_key,
            "OKX-TIMESTAMP": timestamp,
            "OKX-SIGN": sign,
            "OKX-PASSPHRASE": self.passphrase,
            "OKX-COUNTRY": "XX",
        }
    
    def _request(self, method: str, endpoint: str, params: dict = None):
        def _call():
            query = ""
            if params and method == "GET":
                query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
            elif params:
                body = json.dumps(params)
            else:
                body = ""
            
            path = endpoint + query
            headers = self._headers(method, path, body if method != "GET" else "")
            
            url = f"{self.base}{path}"
            resp = requests.request(method, url, headers=headers, data=body if method != "GET" else None, timeout=self.timeout)
            result = resp.json()
            
            if result.get('code') != '0':
                raise Exception(f"OKX API error: {result.get('msg')}")
            return result.get('data', [])
        
        for attempt in range(3):
            try:
                return _call()
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
    
    def get_balance(self) -> float:
        """获取交易账户 USDT 余额"""
        data = self._request("GET", "/api/v5/account/balance", {"ccy": "USDT"})
        if data:
            for inst in data[0].get('details', []):
                if inst.get('ccy') == 'USDT':
                    return float(inst.get('availBal', 0))
        return 0.0
    
    def get_positions(self, symbol: str) -> dict:
        """返回 {side: {"dir": "LONG"/"SHORT", "qty": float, "entry": float}}"""
        # OKX symbol format: BTC-USDT-SWAP
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        positions = {}
        data = self._request("GET", "/api/v5/account/positions", {"instId": inst_id})
        
        if isinstance(data, list):
            for p in data:
                if not isinstance(p, dict):
                    continue
                amt = float(p.get('availPos', 0))
                if amt == 0:
                    # 检查总持仓（包含不可用）
                    total = float(p.get('pos', 0))
                    if total == 0:
                        continue
                    amt = total
                
                side = p.get('posSide', '').upper()
                if side not in ('LONG', 'SHORT'):
                    # 判断方向
                    if p.get('imr') and float(p.get('imr', 0)) > 0:
                        side = 'LONG'
                    else:
                        side = 'SHORT'
                
                positions[side] = {
                    "dir": side,
                    "qty": abs(amt),
                    "entry": abs(float(p.get('avgPx', 0)))
                }
        return positions
    
    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        """返回标准化K线: [[open_time, open, high, low, close, volume, ...], ...]"""
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        okx_interval = self.INTERVAL_MAP.get(interval, interval)
        
        data = self._request("GET", "/api/v5/market/history-candles", {
            "instId": inst_id,
            "bar": okx_interval,
            "limit": limit
        })
        
        # OKX格式: [{ts, o, h, l, c, vol, ...}, ...] → 转为 Binance 格式
        result = []
        for k in reversed(data):
            result.append([
                int(k['ts']),           # open_time
                float(k['o']),          # open
                float(k['h']),          # high
                float(k['l']),          # low
                float(k['c']),          # close
                float(k['vol']),        # volume
            ])
        return result
    
    def market_order(self, symbol: str, side: str, position_side: str, quantity: float) -> bool:
        """OKX 市价下单"""
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        # OKX: side = buy/sell (always relative to USDT), posSide = long/short/net
        # OKX永续合约：市价单用市价单参数
        params = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": "buy" if side == "BUY" else "sell",
            "posSide": position_side.lower(),
            "ordType": "market",
            "sz": str(int(quantity)),  # OKX使用整数数量
        }
        
        data = self._request("POST", "/api/v5/trade/order", params)
        if data:
            return bool(data[0].get('ordId'))
        return False
    
    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        """返回买卖盘口"""
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        # OKX books-lite 只接受 instId（无sz参数），返回默认深度
        data = self._request("GET", "/api/v5/market/books-lite", {
            "instId": inst_id,
        })
        if data and len(data) > 0:
            books = data[0]
            bids = [(float(b[0]), float(b[1])) for b in books.get('bids', [])[:limit]]
            asks = [(float(a[0]), float(a[1])) for a in books.get('asks', [])[:limit]]
            return {"bid": bids, "ask": asks}
        return {"bid": [], "ask": []}
    
    def get_funding_rate(self, symbol: str) -> float:
        """获取资金费率"""
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        data = self._request("GET", "/api/v5/public/funding-rate", {"instId": inst_id})
        if data and len(data) > 0:
            return float(data[0].get('fundingRate', 0))
        return 0.0
    
    def get_open_interest(self, symbol: str) -> float:
        """获取未平仓合约数"""
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        data = self._request("GET", "/api/v5/public/open-interest", {"instId": inst_id})
        if data and len(data) > 0:
            return float(data[0].get('oi', 0))
        return 0.0


# ===================== 工厂函数 =====================
def create_adapter(exchange: str, api_key: str, secret: str, passphrase: str = "") -> object:
    exchange = exchange.lower()
    if exchange == "binance":
        return BinanceAdapter(api_key, secret)
    elif exchange == "okx":
        if not passphrase:
            raise ValueError("OKX需要passphrase参数")
        return OKXAdapter(api_key, secret, passphrase)
    else:
        raise ValueError(f"不支持的交易所: {exchange}")
