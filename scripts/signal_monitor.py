#!/usr/bin/env python3
"""
开仓信号监测脚本
触发条件（满足任一即发警报）：
- BTC突破 $67,400 → LONG信号
- BTC跌破 $65,653 → SHORT信号
- ETH突破 $2,082 → LONG信号  
- ETH跌破 $2,015 → SHORT信号
- BTC资金费率 > 0.03%/周期 → 多头拥挤预警
"""

import requests
import json
import time

TELEGRAM_CHAT_ID = "ou_ce5a94cfca07b266414b003138b8f1f8"
FEISHUWebhook = ""  # 先用飞书直发

def get_btc_data():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP", timeout=5)
        d = r.json()['data'][0]
        return {
            'last': float(d['last']),
            'high24h': float(d['high24h']),
            'low24h': float(d['low24h']),
        }
    except:
        return None

def get_eth_data():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP", timeout=5)
        d = r.json()['data'][0]
        return {
            'last': float(d['last']),
            'high24h': float(d['high24h']),
            'low24h': float(d['low24h']),
        }
    except:
        return None

def get_funding_rate(instId):
    try:
        r = requests.get(f"https://www.okx.com/api/v5/public/funding-rate?instId={instId}", timeout=5)
        d = r.json()['data'][0]
        return float(d['fundingRate'])
    except:
        return None

def check_signals():
    signals = []
    
    btc = get_btc_data()
    eth = get_eth_data()
    btc_funding = get_funding_rate("BTC-USDT-SWAP")
    
    if not btc or not eth:
        return None, "数据获取失败"
    
    # LONG信号
    if btc['last'] > 67400:
        signals.append(f"✅ BTC LONG信号\n价格: ${btc['last']}\n突破 $67,400 压力位")
    
    if eth['last'] > 2082:
        signals.append(f"✅ ETH LONG信号\n价格: ${eth['last']}\n突破 $2,082 压力位")
    
    # SHORT信号
    if btc['last'] < 65653:
        signals.append(f"🔴 BTC SHORT信号\n价格: ${btc['last']}\n跌破 $65,653 支撑位")
    
    if eth['last'] < 2015:
        signals.append(f"🔴 ETH SHORT信号\n价格: ${eth['last']}\n跌破 $2,015 支撑位")
    
    # 多头拥挤预警
    if btc_funding and btc_funding > 0.0003:
        signals.append(f"⚠️ BTC资金费率预警\n费率: {btc_funding*100:.4f}%\n多头拥挤，注意对冲")
    
    return signals, f"BTC={btc['last']} ETH={eth['last']}"

def format_alert(signals, price_info):
    header = f"🚨 **开仓信号** | {price_info}\n{'='*20}\n"
    return header + "\n".join(signals)

if __name__ == "__main__":
    signals, price_info = check_signals()
    if signals:
        msg = format_alert(signals, price_info)
        print("SIGNAL_DETECTED:" + msg)
    else:
        print(f"NO_SIGNAL:{price_info}")
