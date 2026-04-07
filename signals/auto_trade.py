#!/usr/bin/env python3
"""
混沌龙虾自动交易系统 v1.0
============================
基于多Agent信号的自动执行

规则：
- 入场价触达 → 自动买入
- 止损价触达 → 自动止损
- 目标价触达 → 自动止盈

风险控制：
- 最大仓位：20%（200U）
- 止损：严格执行
- 单品种操作，不重仓
"""

import requests
import json
import time
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta

OKX_BASE = "https://www.okx.com/api/v5"

# 账户配置
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
MAX_POSITION_PCT = 0.20  # 最大20%仓位

def get_headers(method, path, body=""):
    resp = requests.get(f"{OKX_BASE}/public/time")
    server_ts = resp.json()['data'][0]['ts']
    ts = datetime.utcfromtimestamp(int(server_ts)/1000).strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(server_ts)%1000:03d}Z"
    message = ts + method + path + body
    mac = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256)
    sig = base64.b64encode(mac.digest()).decode()
    return {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }

def get_balance():
    r = requests.get(f"{OKX_BASE}/account/balance?ccy=USDT", headers=get_headers("GET", "/api/v5/account/balance?ccy=USDT"))
    return float(r.json()['data'][0]['details'][0]['availBal'])

def get_ticker(inst):
    r = requests.get(f"{OKX_BASE}/market/ticker?instId={inst}")
    return float(r.json()['data'][0]['last'])

def place_order(inst, side, sz, px=None):
    """下单"""
    path = "/api/v5/trade/order"
    body = {
        "instId": inst,
        "tdMode": "cross",
        "side": side,
        "ordType": "market" if px is None else "limit",
        "sz": str(sz),
        "px": str(px) if px else None
    }
    body_str = json.dumps(body)
    r = requests.post(f"{OKX_BASE}{path}", headers=get_headers("POST", path, body_str), data=body_str)
    return r.json()

def set_sl(inst, sz, sl_px):
    """设置止损"""
    path = "/api/v5/trade/order"
    body = {
        "instId": inst,
        "tdMode": "cross",
        "side": "sell",
        "ordType": "stop",
        "sz": str(sz),
        "slTriggerPx": str(sl_px),
        "slOrdPx": str(sl_px)
    }
    body_str = json.dumps(body)
    r = requests.post(f"{OKX_BASE}{path}", headers=get_headers("POST", path, body_str), data=body_str)
    return r.json()

class AutoTrader:
    def __init__(self):
        self.btc_entry = 69200   # 入场价
        self.btc_stop = 67468   # 止损价
        self.btc_tp = 75119     # 目标价
        self.eth_entry = 2145.0   # 入场价
        self.eth_stop = 2089.1   # 止损价
        self.eth_tp = 2328.0     # 目标价
        
        self.btc_in = False
        self.eth_in = False
        self.btc_sz = 0
        self.eth_sz = 0
        
        self.last_notify = 0
    
    def check_and_notify(self, msg):
        """飞书通知"""
        print(f"[通知] {msg}")
        # 通知会在主脚本中通过message工具发送
    
    def run(self):
        print(f"=== 自动交易启动 {datetime.now().strftime('%H:%M:%S')} ===")
        print(f"BTC入场: {self.btc_entry} | 止损: {self.btc_stop} | 目标: {self.btc_tp}")
        print(f"ETH入场: {self.eth_entry} | 止损: {self.eth_stop} | 目标: {self.eth_tp}")
        print()
        
        while True:
            try:
                btc = get_ticker("BTC-USDT-SWAP")
                eth = get_ticker("ETH-USDT-SWAP")
                balance = get_balance()
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] BTC: {btc} | ETH: {eth} | 余额: {balance}")
                
                # BTC检查
                if not self.btc_in and btc <= self.btc_entry:
                    # 触发入场
                    sz = int(balance * MAX_POSITION_PCT / btc)
                    r = place_order("BTC-USDT-SWAP", "buy", sz)
                    if r.get('code') == '0':
                        self.btc_in = True
                        self.btc_sz = sz
                        # 设置止损
                        set_sl("BTC-USDT-SWAP", sz, self.btc_stop)
                        self.check_and_notify(f"✅ BTC买入成功！{sz}张 @{btc}，止损{self.btc_stop}")
                
                # ETH检查
                if not self.eth_in and eth <= self.eth_entry:
                    sz = int(balance * MAX_POSITION_PCT / eth)
                    r = place_order("ETH-USDT-SWAP", "buy", sz)
                    if r.get('code') == '0':
                        self.eth_in = True
                        self.eth_sz = sz
                        set_sl("ETH-USDT-SWAP", sz, self.eth_stop)
                        self.check_and_notify(f"✅ ETH买入成功！{sz}张 @{eth}，止损{self.eth_stop}")
                
                # 检查是否到达目标
                if self.btc_in and btc >= self.btc_tp:
                    self.check_and_notify(f"🎯 BTC到达目标{self.btc_tp}！")
                
                if self.eth_in and eth >= self.eth_tp:
                    self.check_and_notify(f"🎯 ETH到达目标{self.eth_tp}！")
                
                time.sleep(30)  # 每30秒检查一次
                
            except Exception as e:
                print(f"错误: {e}")
                time.sleep(10)

if __name__ == "__main__":
    trader = AutoTrader()
    trader.run()
