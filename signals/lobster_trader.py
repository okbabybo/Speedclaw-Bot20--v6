#!/usr/bin/env python3
"""
混沌龙虾盯盘系统 v5.0
动态变化对比汇报
"""
import requests
import numpy as np
import hmac
import base64
import hashlib
import json
from datetime import datetime, timezone

API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
CACHE_FILE = "/tmp/lobster_last.json"

def sign(message, secret):
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def api_get(path):
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    message = timestamp + "GET" + path
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(message, SECRET), "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": PASSPHRASE}
    return requests.get(f"https://www.okx.com{path}", headers=headers, timeout=10).json()

def get_price(symbol):
    return float(requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}", timeout=10).json()['data'][0]['last'])

def get_rsi(symbol, period):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={period}&limit=50"
    closes = [float(c[4]) for c in requests.get(url, timeout=10).json()['data']]
    deltas = np.diff(closes[-15:])
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    rs = np.mean(gains) / np.mean(losses) if np.mean(losses) > 0 else 100
    return 100 - (100 / (1 + rs))

def get_trend(symbol, period):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={period}&limit=50"
    closes = [float(c[4]) for c in requests.get(url, timeout=10).json()['data']]
    return "🟢" if closes[0] > np.mean(closes[-20:]) else "🔴"

def get_funding(symbol):
    url = f"https://www.okx.com/api/v5/market/funding-rate?instId={symbol}"
    try:
        data = requests.get(url, timeout=10).json()['data'][0]
        return float(data.get('fundingRate', 0)) * 100
    except:
        return 0

def get_balance():
    result = api_get("/api/v5/account/balance")
    if result.get('code') == '0':
        for item in result['data'][0]['details']:
            if item['ccy'] == 'USDT':
                return float(item['eq'])
    return 0

def get_positions():
    result = api_get("/api/v5/account/positions?instType=SWAP")
    positions = []
    if result.get('code') == '0':
        for p in result.get('data', []):
            pos = float(p.get('pos', '0') or '0')
            if pos != 0:
                positions.append({
                    'symbol': p['instId'],
                    'side': p['posSide'],
                    'pos': pos,
                    'avgPx': float(p['avgPx']),
                    'upl': float(p.get('upl', 0) or 0),
                    'uplRatio': float(p.get('uplRatio', 0) or 0)
                })
    return positions

def load_last():
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except:
        return None

def save_last(data):
    with open(CACHE_FILE, 'w') as f:
        json.dump(data, f)

def arrow(val, ref, threshold=0.5):
    if ref is None:
        return ""
    diff = val - ref
    if abs(diff) < threshold:
        return "→"
    return "↑" if diff > 0 else "↓"

def main():
    # 获取当前数据
    btc = get_price("BTC-USDT-SWAP")
    eth = get_price("ETH-USDT-SWAP")
    btc_rsi_1h = get_rsi("BTC-USDT-SWAP", "1H")
    btc_rsi_4h = get_rsi("BTC-USDT-SWAP", "4H")
    eth_rsi_1h = get_rsi("ETH-USDT-SWAP", "1H")
    eth_rsi_4h = get_rsi("ETH-USDT-SWAP", "4H")
    btc_4h = get_trend("BTC-USDT-SWAP", "4H")
    eth_4h = get_trend("ETH-USDT-SWAP", "4H")
    btc_funding = get_funding("BTC-USDT-SWAP")
    eth_funding = get_funding("ETH-USDT-SWAP")
    balance = get_balance()
    positions = get_positions()
    
    btc_pos = None
    for pos in positions:
        if "BTC" in pos['symbol']:
            btc_pos = pos
            break
    
    # 加载上次数据
    last = load_last()
    
    # 构建当前数据
    current = {
        'btc': btc, 'eth': eth,
        'btc_rsi_1h': btc_rsi_1h, 'btc_rsi_4h': btc_rsi_4h,
        'eth_rsi_1h': eth_rsi_1h, 'eth_rsi_4h': eth_rsi_4h,
        'btc_4h': btc_4h, 'eth_4h': eth_4h,
        'balance': balance
    }
    
    # 对比变化
    if last:
        btc_chg = arrow(btc, last['btc'], 50)
        eth_chg = arrow(eth, last['eth'], 10)
        btc_rsi_1h_chg = arrow(btc_rsi_1h, last['btc_rsi_1h'], 3)
        btc_rsi_4h_chg = arrow(btc_rsi_4h, last['btc_rsi_4h'], 3)
        eth_rsi_1h_chg = arrow(eth_rsi_1h, last['eth_rsi_1h'], 3)
        eth_rsi_4h_chg = arrow(eth_rsi_4h, last['eth_rsi_4h'], 3)
        balance_chg = arrow(balance, last['balance'], 1)
    else:
        btc_chg = eth_chg = ""
        btc_rsi_1h_chg = btc_rsi_4h_chg = ""
        eth_rsi_1h_chg = eth_rsi_4h_chg = ""
        balance_chg = ""
    
    # 保存当前
    save_last(current)
    
    # 判断
    long_count = (btc_4h=="🟢") + (eth_4h=="🟢")
    if long_count == 2:
        judge = "🟢上涨"
        suggest = "持有做多"
    elif long_count == 0:
        judge = "🔴下跌"
        suggest = "做空/观望"
    else:
        judge = "⚪震荡"
        suggest = "观望"
    
    # 输出
    print(f"""━━━━━━━━━━━━━━━━━━━━━━
🦞 盯盘报告 | {datetime.now().strftime('%H:%M')}
━━━━━━━━━━━━━━━━━━━━━━

【价格变化】
BTC: {btc:,.0f} {btc_chg}
ETH: {eth:,.0f} {eth_chg}

【技术指标】
| 周期 | BTC RSI | ETH RSI |
|------|---------|---------|
| 1H | {btc_rsi_1h:.0f} {btc_rsi_1h_chg} | {eth_rsi_1h:.0f} {eth_rsi_1h_chg} |
| 4H | {btc_rsi_4h:.0f} {btc_rsi_4h_chg} | {eth_rsi_4h:.0f} {eth_rsi_4h_chg} |

【趋势信号】
BTC 4H: {btc_4h} | ETH 4H: {eth_4h}

【账户状态】
余额: {balance:.2f} U {balance_chg}""")
    
    if btc_pos:
        upl_chg = ""
        if last and 'btc_upl' in last:
            upl_chg = arrow(btc_pos['upl'], last.get('btc_upl'), 1)
        print(f"""持仓: {btc_pos['side'].upper()} {btc_pos['pos']:.2f}张
浮盈: {btc_pos['upl']:.2f} U ({btc_pos['uplRatio']*100:.1f}%) {upl_chg}""")
        # 保存持仓浮盈
        current['btc_upl'] = btc_pos['upl']
    
    print(f"""
【综合分析】
判断: {judge}
建议: {suggest}

━━━━━━━━━━━━━━━━━━━━━━""")

if __name__ == "__main__":
    main()
