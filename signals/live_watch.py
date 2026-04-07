#!/usr/bin/env python3
"""
混沌龙虾秒级盯盘系统 v1.0
============================
每秒检查价格，关键位触发时写入信号文件
"""
import requests
import json
import time
from datetime import datetime

# 关键价位
KEY_LEVELS = {
    "BTC": {
        "break_high": 70000,    # 突破做多信号
        "enter_long": 69200,     # 入场做多区间
        "enter_long_max": 69500,
        "stop_loss": 68500,      # 止损
        "break_low": 68000,      # 跌破空头信号
    },
    "ETH": {
        "break_high": 2160,      # 突破做多
        "enter_long": 2145,       # 入场做多
        "enter_long_max": 2150,
        "stop_loss": 2100,       # 止损
        "break_low": 2090,       # 跌破空头
    }
}

SIGNAL_FILE = "/tmp/lobster_signal.json"
CHECK_INTERVAL = 3  # 每3秒检查一次

def get_price(inst):
    r = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={inst}", timeout=5)
    return float(r.json()['data'][0]['last'])

def check_signals(btc, eth):
    signals = []
    now = datetime.now().strftime("%H:%M:%S")
    
    # BTC检查
    if btc >= KEY_LEVELS["BTC"]["break_high"]:
        signals.append({"time": now, "symbol": "BTC", "type": "BREAK_HIGH", "price": btc, "action": "突破信号！可追多！"})
    elif btc >= KEY_LEVELS["BTC"]["enter_long"] and btc <= KEY_LEVELS["BTC"]["enter_long_max"]:
        signals.append({"time": now, "symbol": "BTC", "type": "ENTER_LONG", "price": btc, "action": "入场做多位！止损68500"})
    elif btc <= KEY_LEVELS["BTC"]["stop_loss"]:
        signals.append({"time": now, "symbol": "BTC", "type": "STOP_LOSS", "price": btc, "action": "触及止损！"})
    elif btc <= KEY_LEVELS["BTC"]["break_low"]:
        signals.append({"time": now, "symbol": "BTC", "type": "BREAK_LOW", "price": btc, "action": "跌破空头信号！"})
    
    # ETH检查
    if eth >= KEY_LEVELS["ETH"]["break_high"]:
        signals.append({"time": now, "symbol": "ETH", "type": "BREAK_HIGH", "price": eth, "action": "突破信号！可追多！"})
    elif eth >= KEY_LEVELS["ETH"]["enter_long"] and eth <= KEY_LEVELS["ETH"]["enter_long_max"]:
        signals.append({"time": now, "symbol": "ETH", "type": "ENTER_LONG", "price": eth, "action": "入场做多位！止损2100"})
    elif eth <= KEY_LEVELS["ETH"]["stop_loss"]:
        signals.append({"time": now, "symbol": "ETH", "type": "STOP_LOSS", "price": eth, "action": "触及止损！"})
    elif eth <= KEY_LEVELS["ETH"]["break_low"]:
        signals.append({"time": now, "symbol": "ETH", "type": "BREAK_LOW", "price": eth, "action": "跌破空头信号！"})
    
    return signals

def main():
    print(f"[秒级盯盘] 启动 {datetime.now().strftime('%H:%M:%S')}")
    print(f"[关键价位]")
    print(f"  BTC: 入场{KEY_LEVELS['BTC']['enter_long']}-{KEY_LEVELS['BTC']['enter_long_max']} | 止损{KEY_LEVELS['BTC']['stop_loss']} | 突破{KEY_LEVELS['BTC']['break_high']}")
    print(f"  ETH: 入场{KEY_LEVELS['ETH']['enter_long']}-{KEY_LEVELS['ETH']['enter_long_max']} | 止损{KEY_LEVELS['ETH']['stop_loss']} | 突破{KEY_LEVELS['ETH']['break_high']}")
    
    last_signal_time = 0
    last_prices = {"BTC": 0, "ETH": 0}
    
    while True:
        try:
            btc = get_price("BTC-USDT-SWAP")
            eth = get_price("ETH-USDT-SWAP")
            
            # 价格变化时打印
            if btc != last_prices["BTC"] or eth != last_prices["ETH"]:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] BTC: {btc} | ETH: {eth}")
                last_prices = {"BTC": btc, "ETH": eth}
            
            # 检查信号
            signals = check_signals(btc, eth)
            for sig in signals:
                # 写入信号文件
                with open(SIGNAL_FILE, 'w') as f:
                    json.dump(sig, f, ensure_ascii=False)
                print(f"🚨 信号触发: {sig}")
                last_signal_time = time.time()
            
            # 30秒后清除旧信号
            if last_signal_time > 0 and time.time() - last_signal_time > 30:
                try:
                    import os
                    os.remove(SIGNAL_FILE)
                except:
                    pass
                last_signal_time = 0
            
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
