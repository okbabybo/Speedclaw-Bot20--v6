"""
自动交易执行器 v1.0
接收AI信号 → 调用OKX API执行 → 推送结果

用法：
  python3 executor.py SHORT 72000 72500 1   # 做空
  python3 executor.py LONG  68000 67500 1   # 做多
  python3 executor.py CLOSE                   # 平仓
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from okx_trader import (
    place_order, close_position, get_position, 
    get_ticker, get_account
)

SYMBOL_BTC = "BTC-USDT-SWAP"
SYMBOL_ETH = "ETH-USDT-SWAP"

def exec_short(inst_id, entry_price, stop_loss, size=1):
    """执行做空"""
    print(f"\n🚨 执行做空 | {inst_id}")
    print(f"   入场: {entry_price} | 止损: {stop_loss} | 数量: {size}")
    
    # 开空单
    order_id = place_order(inst_id, "sell", "short", entry_price, size)
    if order_id:
        print(f"   ✅ 做空单已挂 | 订单ID: {order_id}")
        
        # 挂止损单（止损价格略高于止损价，确保触发）
        sl_price = stop_loss + 50  # 留50点缓冲
        sl_order = place_order(inst_id, "buy", "short", sl_price, size)
        if sl_order:
            print(f"   ✅ 止损单已挂 | 价格: {sl_price}")
        return order_id
    return None

def exec_long(inst_id, entry_price, stop_loss, size=1):
    """执行做多"""
    print(f"\n🚀 执行做多 | {inst_id}")
    print(f"   入场: {entry_price} | 止损: {stop_loss} | 数量: {size}")
    
    order_id = place_order(inst_id, "buy", "long", entry_price, size)
    if order_id:
        print(f"   ✅ 做多单已挂 | 订单ID: {order_id}")
        
        sl_price = stop_loss - 50
        sl_order = place_order(inst_id, "sell", "long", sl_price, size)
        if sl_order:
            print(f"   ✅ 止损单已挂 | 价格: {sl_price}")
        return order_id
    return None

def exec_close(inst_id):
    """平仓"""
    print(f"\n🔄 平仓 | {inst_id}")
    positions = get_position(inst_id)
    for p in positions:
        if float(p.get('pos', 0)) > 0:
            ok = close_position(inst_id, p['posSide'])
            if ok:
                print(f"   ✅ {p['posSide']} 已平")
        else:
            print(f"   ⏸ 无持仓")

def show_status():
    """显示状态"""
    print("\n" + "=" * 40)
    print("【账户状态】")
    get_account()
    print("\n【持仓状态】")
    get_position(SYMBOL_BTC)
    get_position(SYMBOL_ETH)
    print("\n【实时行情】")
    get_ticker(SYMBOL_BTC)
    get_ticker(SYMBOL_ETH)

# ============ 主入口 ============
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 executor.py status              # 查看状态")
        print("  python3 executor.py SHORT 72000 72500 1 # BTC做空")
        print("  python3 executor.py LONG  68000 67500 1 # BTC做多")
        print("  python3 executor.py CLOSE BTC            # 平仓BTC")
        print("  python3 executor.py CLOSE ALL            # 平仓全部")
        sys.exit(0)
    
    cmd = sys.argv[1].upper()
    
    if cmd == "STATUS":
        show_status()
    
    elif cmd == "SHORT":
        if len(sys.argv) < 5:
            print("用法: SHORT <symbol> <entry_price> <stop_loss> <size>")
            sys.exit(1)
        symbol = sys.argv[2] if sys.argv[2] != "BTC" else SYMBOL_BTC
        entry = float(sys.argv[2] if sys.argv[2] == SYMBOL_BTC else sys.argv[2])
        if len(sys.argv) > 2 and sys.argv[2] == "BTC":
            entry = float(sys.argv[3])
            sl = float(sys.argv[4])
            size = int(sys.argv[5])
            symbol = SYMBOL_BTC
        elif sys.argv[2] == SYMBOL_BTC:
            entry = float(sys.argv[2+1])
            sl = float(sys.argv[3+1])
            size = int(sys.argv[4+1])
        exec_short(symbol, entry, sl, size)
    
    elif cmd == "LONG":
        if len(sys.argv) < 5:
            print("用法: LONG <symbol> <entry_price> <stop_loss> <size>")
            sys.exit(1)
        symbol = sys.argv[2] if sys.argv[2] == SYMBOL_BTC else SYMBOL_BTC
        entry = float(sys.argv[3])
        sl = float(sys.argv[4])
        size = int(sys.argv[5])
        exec_long(symbol, entry, sl, size)
    
    elif cmd == "CLOSE":
        target = sys.argv[2].upper() if len(sys.argv) > 2 else "BTC"
        if target == "ALL":
            exec_close(SYMBOL_BTC)
            exec_close(SYMBOL_ETH)
        elif target == "BTC":
            exec_close(SYMBOL_BTC)
        elif target == "ETH":
            exec_close(SYMBOL_ETH)
        else:
            exec_close(SYMBOL_BTC)
    
    else:
        print(f"未知命令: {cmd}")
