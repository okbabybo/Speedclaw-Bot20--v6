#!/usr/bin/env python3
"""
混沌龙虾盯盘系统 v7.0
多Agent详细分析报告格式
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

def main():
    # 获取数据
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

    # Agent2 信号评分
    btc_long = max(0, (50 - btc_rsi_4h) / 10)
    btc_short = max(0, (btc_rsi_4h - 50) / 10)
    eth_long = max(0, (50 - eth_rsi_4h) / 10)
    eth_short = max(0, (eth_rsi_4h - 50) / 10)
    signal_score = (btc_long + eth_long) - (btc_short + eth_short)

    # Agent3 决策
    long_count = (btc_4h=="🟢") + (eth_4h=="🟢")
    if long_count == 2 and btc_rsi_4h < 60:
        decision = "🟢强烈买入"
        confidence = 75
    elif long_count == 2:
        decision = "🟢买入"
        confidence = 60
    elif long_count == 0:
        decision = "🔴卖出"
        confidence = 70
    else:
        decision = "⚪观望"
        confidence = 50

    # Agent4 风控
    if btc_pos:
        stop_loss = btc_pos['avgPx'] * 0.95
        take_profit = btc_pos['avgPx'] * 1.02
    else:
        stop_loss = take_profit = 0

    # 操作建议
    if btc_pos:
        if btc_pos['uplRatio'] < -0.05:
            action = "⚠️ 止损风险！建议设置自动止损"
        elif btc_pos['uplRatio'] > 0.20:
            action = "💰 已达20%盈利，考虑部分止盈"
        else:
            action = "✅ 持有，等待行情演绎"
    else:
        if decision == "🟢强烈买入":
            action = "🔥 买入信号，可轻仓介入"
        elif decision == "🟢买入":
            action = "✅ 可少量买入，止损放68,000"
        else:
            action = "⏸️ 等待更明确信号"

    # 输出
    print(f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🦞 多Agent量化分析报告 | {datetime.now().strftime('%H:%M')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【Agent1 - 技术分析】
| 指标 | BTC | ETH |
|------|-----|-----|
| 最新价 | {btc:,.0f} USDT | {eth:,.0f} USDT |
| RSI(1H) | {btc_rsi_1h:.0f} | {eth_rsi_1h:.0f} |
| RSI(4H) | {btc_rsi_4h:.0f} | {eth_rsi_4h:.0f} |
| 趋势 | {btc_4h} | {eth_4h} |
| 资金费率 | {btc_funding:.4f}% | {eth_funding:.4f}% |

【Agent2 - 信号评分】
| 信号 | BTC | ETH |
|------|-----|-----|
| 多头评分 | {btc_long:.1f} | {eth_long:.1f} |
| 空头评分 | {btc_short:.1f} | {eth_short:.1f} |
| 综合 | {signal_score:+.1f} | {'偏多' if signal_score>0 else '偏空'} |

【Agent3 - 综合决策】
| 项目 | 数值 |
|------|------|
| 决策 | {decision} |
| 置信度 | {confidence}% |
| 理由 | {'4H上涨，RSI未超买' if long_count==2 else '4H下跌' if long_count==0 else '震荡整理'} |

【Agent4 - 风控】
| 项目 | 数值 |
|------|------|
| 余额 | {balance:.2f} U |""")

    if btc_pos:
        print(f"| 持仓 | {btc_pos['side'].upper()} {btc_pos['pos']:.2f}张 |")
        print(f"| 均价 | {btc_pos['avgPx']:,.0f} |")
        print(f"| 当前价 | {btc:,.0f} |")
        print(f"| 浮盈 | {btc_pos['upl']:.2f} U ({btc_pos['uplRatio']*100:.1f}%) |")
        print(f"| 止损 | {stop_loss:,.0f} |")
        print(f"| 止盈 | {take_profit:,.0f} |")
    else:
        print(f"| 建议方向 | {'做多' if btc_4h=='🟢' else '做空' if btc_4h=='🔴' else '观望'} |")

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【综合分析报告】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 趋势判断：
   BTC 4H {btc_4h} | ETH 4H {eth_4h}
   → {'上涨趋势' if long_count==2 else '下跌趋势' if long_count==0 else '震荡整理'}

2. RSI分析：
   BTC RSI {btc_rsi_4h:.0f} {'（偏多）' if btc_rsi_4h<50 else '（偏空）'}
   ETH RSI {eth_rsi_4h:.0f} {'（超卖，可能反弹）' if eth_rsi_4h<30 else '（偏多）' if eth_rsi_4h<50 else '（偏空）'}

3. 仓位状态：
   {'持仓浮亏 ' + f"{btc_pos['uplRatio']*100:.1f}%" if btc_pos and btc_pos['uplRatio']<0 else '持仓浮盈 ' + f"{btc_pos['uplRatio']*100:.1f}%" if btc_pos else '空仓'}

4. 操作建议：
   {action}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""")

if __name__ == "__main__":
    main()
