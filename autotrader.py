"""
全自动交易系统 v3.0
- 多周期RSI + MA20 + 资金费率 + 24h高低点 实时判断
- 所有信号条件基于实时行情，不写死数据
- 止损3% / 移动止盈（实时跟踪）

【信号条件】（全部基于实时数据动态判断）

做空信号（需同时满足）：
  1. 价格在4H MA20上方（偏弱不做多）
  2. RSI 4H > 60 或 RSI 1H > 65（超买）
  3. 资金费率 > 0.015%（多头拥挤）
  4. 从24h低点反弹 > 4%（累积涨幅，注意回调风险）
  5. 价格在合理做空区间（距离24h高点 < 3%）

做多信号（需同时满足）：
  1. 价格在4H MA20下方（偏强不做空）
  2. RSI 4H < 40 或 RSI 1H < 35（超卖）
  3. 资金费率 < -0.015%（空头拥挤）
  4. 从24h高点回落 > 4%（注意抄底风险）
  5. 价格在合理做多区间（距离24h低点 < 3%）

止盈逻辑：
  做空：价格移动1.5% → 激活移动止盈，等反弹0.5%平仓
       价格移动2.0% → 继续持有，等反弹0.5%平仓
       价格持续下跌 → 等从最低点反弹0.5%平仓
  做多：同上反之
"""

import requests
import json
import time
import hmac
import base64
import hashlib
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

# ============ 配置 ============
LEVERAGE = 100
SL_PCT = 0.03          # 3%止损
TP1_PCT = 0.015        # 1.5%触发移动止盈
TP2_PCT = 0.02         # 2%触发移动止盈
TRAILING_PCT = 0.005   # 0.5%回调止盈
DAILY_LOSS_LIMIT = 0.03  # 3%日亏损上限
TRADE_INTERVAL = 30      # 30秒检查

# 信号阈值（动态参考）
RSI_SHORT_4H = 60
RSI_SHORT_1H = 65
RSI_LONG_4H = 40
RSI_LONG_1H = 35
FR_THRESHOLD = 0.00015   # 0.015%
REBOUND_THRESHOLD = 0.04  # 4%

# 测试迷你仓
TEST_CONTRACTS = 10

# ============ API ============
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET_KEY = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
BASE_URL = "https://www.okx.com"

def sign(msg: str, sk: str) -> str:
    return base64.b64encode(hmac.new(sk.encode(), msg.encode(), hashlib.sha256).digest()).decode()

def headers(path: str, method: str, body: str = '') -> dict:
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    msg = ts + method + path + body
    return {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': sign(msg, SECRET_KEY),
        'OK-ACCESS-TIMESTAMP': ts,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json',
    }

def get(url: str) -> dict:
    p = url.replace(BASE_URL, '')
    return requests.get(url, headers=headers(p, 'GET'), timeout=10).json()

def post(url: str, body: dict) -> dict:
    p = url.replace(BASE_URL, '')
    d = json.dumps(body)
    return requests.post(url, data=d, headers=headers(p, 'POST', d), timeout=10).json()

# ============ 数据获取 ============
def get_candles(inst_id: str, bar: str, limit: int = 100) -> List[List[float]]:
    """获取K线数据，返回[[open, high, low, close, volume], ...]"""
    data = get(f'{BASE_URL}/api/v5/market/history-candles?instId={inst_id}&bar={bar}&limit={limit}')
    result = []
    for row in data.get('data', []):
        try:
            result.append([float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
        except:
            pass
    return result

def calc_rsi(candles: List[List[float]], period: int = 14) -> float:
    """计算RSI"""
    if len(candles) < period + 1:
        return 50.0
    closes = [c[3] for c in candles]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calc_ma(candles: List[List[float]], period: int = 20) -> float:
    """计算MA"""
    closes = [c[3] for c in candles[-period:]]
    return sum(closes) / period

def get_market_data() -> dict:
    """获取完整市场数据"""
    btc_t = get(f'{BASE_URL}/api/v5/market/ticker?instId=BTC-USDT-SWAP')['data'][0]
    btc_fr = get(f'{BASE_URL}/api/v5/public/funding-rate?instId=BTC-USDT-SWAP')['data'][0]

    # K线
    candles_4h = get_candles('BTC-USDT-SWAP', '4H', 100)
    candles_1h = get_candles('BTC-USDT-SWAP', '1H', 100)
    candles_30m = get_candles('BTC-USDT-SWAP', '30m', 100)

    price = float(btc_t['last'])
    high24 = float(btc_t['high24h'])
    low24 = float(btc_t['low24h'])
    fr = float(btc_fr['fundingRate'])

    # RSI
    rsi_4h = calc_rsi(candles_4h, 14)
    rsi_1h = calc_rsi(candles_1h, 14)
    rsi_30m = calc_rsi(candles_30m, 14)

    # MA
    ma20_4h = calc_ma(candles_4h, 20)
    ma20_1h = calc_ma(candles_1h, 20)

    # 动态区间（基于当前价格和24h范围）
    mid_price = (high24 + low24) / 2
    range_size = high24 - low24

    # 做空区间：价格靠近24h高点的30%范围以内
    short_zone_high = high24
    short_zone_low = high24 - range_size * 0.30

    # 做多区间：价格靠近24h低点的30%范围以内
    long_zone_low = low24
    long_zone_high = low24 + range_size * 0.30

    # 从高低点变动
    from_low_pct = (price - low24) / low24 * 100
    from_high_pct = (high24 - price) / high24 * 100

    return {
        'price': price,
        'high24': high24,
        'low24': low24,
        'fr': fr,
        'rsi_4h': rsi_4h,
        'rsi_1h': rsi_1h,
        'rsi_30m': rsi_30m,
        'ma20_4h': ma20_4h,
        'ma20_1h': ma20_1h,
        'from_low_pct': from_low_pct,
        'from_high_pct': from_high_pct,
        'short_zone': (short_zone_low, short_zone_high),
        'long_zone': (long_zone_low, long_zone_high),
        'mid_price': mid_price,
        'range_size': range_size,
        'candles_4h': candles_4h,
        'candles_1h': candles_1h,
    }

def get_balance() -> float:
    resp = get(f'{BASE_URL}/api/v5/account/balance')
    for bal in resp.get('data', [{}])[0].get('details', []):
        if bal['ccy'] == 'USDT':
            return float(bal['cashBal'])
    return 0.0

def get_positions(inst_id: str = 'BTC-USDT-SWAP') -> List[dict]:
    resp = get(f'{BASE_URL}/api/v5/account/positions?instId={inst_id}')
    return [p for p in resp.get('data', []) if float(p.get('pos', 0)) > 0]

# ============ 信号检测 ============
def check_signals(market: dict) -> Tuple[Optional[dict], str]:
    """
    检测交易信号，返回 (signal_dict, reason)
    signal_dict包含: direction, entry, sl, tp, contracts, risk_pct, reason
    """
    p = market['price']
    sz = market['short_zone']
    lz = market['long_zone']

    # ========== 做空信号 ==========
    short_conditions = []

    # 条件1: 价格进入做空区间（靠近24h高点的30%范围）
    in_short_zone = sz[0] <= p <= sz[1]
    short_conditions.append(("价格靠近24h高点", in_short_zone))

    # 条件2: RSI超买
    rsi_ok_short = market["rsi_4h"] > RSI_SHORT_4H or market["rsi_1h"] > RSI_SHORT_1H
    short_conditions.append(("RSI超买", rsi_ok_short))

    # 条件3: 资金费率偏高
    fr_ok_short = market["fr"] > FR_THRESHOLD
    short_conditions.append(("资金费率偏高", fr_ok_short))

    # 条件4: 从低点反弹够多（累积涨幅，注意追高风险）
    rebound_ok = market["from_low_pct"] > (REBOUND_THRESHOLD * 100)
    short_conditions.append(("从24h低反弹够多", rebound_ok))

    # 条件5: 价格在MA20上方
    above_ma = p > market["ma20_4h"]
    short_conditions.append(("价格在MA20上方", above_ma))

    # 全部满足才做空
    if all(c[1] for c in short_conditions):
        sl = round(p * (1 + SL_PCT), 1)
        contracts = TEST_CONTRACTS
        cond_text = " | ".join(["%s: %s" % (c[0], "✅" if c[1] else "❌") for c in short_conditions])
        return {
            "direction": "SHORT",
            "entry": round(p, 1),
            "sl": sl,
            "contracts": contracts,
            "risk_pct": 1.0,
            "reason": cond_text,
        }, "做空5条件全满足"

    # ========== 做多信号 ==========
    long_conditions = []

    # 条件1: 价格靠近24h低点
    in_long_zone = lz[0] <= p <= lz[1]
    long_conditions.append(("价格靠近24h低点", in_long_zone))

    # 条件2: RSI超卖
    rsi_ok_long = market["rsi_4h"] < RSI_LONG_4H or market["rsi_1h"] < RSI_LONG_1H
    long_conditions.append(("RSI超卖", rsi_ok_long))

    # 条件3: 资金费率偏低
    fr_ok_long = market["fr"] < -FR_THRESHOLD
    long_conditions.append(("资金费率偏低", fr_ok_long))

    # 条件4: 从高点回落够多
    pullback_ok = market["from_high_pct"] > (REBOUND_THRESHOLD * 100)
    long_conditions.append(("从24h高回落够多", pullback_ok))

    # 条件5: 价格在MA20下方
    below_ma = p < market["ma20_4h"]
    long_conditions.append(("价格在MA20下方", below_ma))

    if all(c[1] for c in long_conditions):
        sl = round(p * (1 - SL_PCT), 1)
        contracts = TEST_CONTRACTS
        cond_text = " | ".join(["%s: %s" % (c[0], "✅" if c[1] else "❌") for c in long_conditions])
        return {
            "direction": "LONG",
            "entry": round(p, 1),
            "sl": sl,
            "contracts": contracts,
            "risk_pct": 1.0,
            "reason": cond_text,
        }, "做多5条件全满足"

    # 无信号
    zone_info = "做空区间 %.0f-%.0f" % (sz[0], sz[1])
    return None, zone_info

# ============ 订单执行 ============
def close_all_positions(inst_id: str = 'BTC-USDT-SWAP'):
    for p in get_positions(inst_id):
        close = post(f'{BASE_URL}/api/v5/trade/close-position', {
            'instId': inst_id, 'posSide': p['posSide'], 'mgnMode': 'cross',
        })
        if close.get('code') == '0':
            print(f"  ✅ {p['posSide']} 已平仓")

def place_entry(direction: str, price: float, contracts: int, sl: float) -> Optional[str]:
    inst_id = 'BTC-USDT-SWAP'
    if direction == 'SHORT':
        r = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': 'sell', 'posSide': 'short',
            'ordType': 'limit', 'px': str(price), 'sz': str(contracts),
        })
    else:
        r = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': 'buy', 'posSide': 'long',
            'ordType': 'limit', 'px': str(price), 'sz': str(contracts),
        })
    if r.get('code') == '0':
        oid = r['data'][0]['ordId']
        # 挂止损
        sl_side = 'buy' if direction == 'SHORT' else 'sell'
        sl_order = post(f'{BASE_URL}/api/v5/trade/order-algo', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': sl_side, 'posSide': 'short' if direction == 'SHORT' else 'long',
            'ordType': 'conditional', 'sz': str(contracts),
            'slTriggerPx': str(sl), 'slOrdPx': '-1',
        })
        if sl_order.get('code') != '0':
            print(f"  ⚠️ 止损单失败: {sl_order.get('msg')}")
        return oid
    print(f"  ❌ 入场失败: {r.get('msg')}")
    return None

# ============ 持仓监控 ============
@dataclass
class ActivePosition:
    direction: str
    entry: float
    contracts: int
    sl: float
    tp1_triggered: bool = False
    tp2_triggered: bool = False
    trailing_active: bool = False
    best_price: float = 0.0
    trailing_exit: float = 0.0
    status: str = 'open'
    order_id: str = ''

def monitor_position(pos: ActivePosition, current_price: float) -> Optional[str]:
    """检查持仓，返回'EXIT'或'STOP'或None"""
    if pos.direction == 'SHORT':
        # 更新最优价（最低价）
        if current_price < pos.best_price:
            pos.best_price = current_price

        pnl_pct = (pos.entry - current_price) / pos.entry * 100

        # TP1: 移动1.5%
        if not pos.tp1_triggered and pnl_pct >= TP1_PCT * 100:
            pos.tp1_triggered = True
            pos.trailing_active = True
            pos.trailing_exit = round(pos.best_price * (1 + TRAILING_PCT), 1)
            print(f"  🚀 TP1(+1.5%)触发 | 最低{pos.best_price} | 反弹0.5%到{pos.trailing_exit}止盈")

        # TP2: 移动2.0%
        if not pos.tp2_triggered and pnl_pct >= TP2_PCT * 100:
            pos.tp2_triggered = True
            pos.trailing_active = True
            pos.trailing_exit = round(pos.best_price * (1 + TRAILING_PCT), 1)
            print(f"  🚀 TP2(+2.0%)触发 | 最低{pos.best_price} | 反弹0.5%到{pos.trailing_exit}止盈")

        # 移动止盈检查
        if pos.trailing_active and current_price >= pos.trailing_exit:
            return 'EXIT'

        # 止损
        if current_price >= pos.sl:
            return 'STOP'

    else:  # LONG
        if current_price > pos.best_price:
            pos.best_price = current_price

        pnl_pct = (current_price - pos.entry) / pos.entry * 100

        if not pos.tp1_triggered and pnl_pct >= TP1_PCT * 100:
            pos.tp1_triggered = True
            pos.trailing_active = True
            pos.trailing_exit = round(pos.best_price * (1 - TRAILING_PCT), 1)
            print(f"  🚀 TP1(+1.5%)触发 | 最高{pos.best_price} | 回落0.5%到{pos.trailing_exit}止盈")

        if not pos.tp2_triggered and pnl_pct >= TP2_PCT * 100:
            pos.tp2_triggered = True
            pos.trailing_active = True
            pos.trailing_exit = round(pos.best_price * (1 - TRAILING_PCT), 1)
            print(f"  🚀 TP2(+2.0%)触发 | 最高{pos.best_price} | 回落0.5%到{pos.trailing_exit}止盈")

        if pos.trailing_active and current_price <= pos.trailing_exit:
            return 'EXIT'

        if current_price <= pos.sl:
            return 'STOP'

    return None

# ============ 信号展示 ============
def format_signal(sig: dict, zone_desc: str = '') -> str:
    dir_emoji = '🔴' if sig['direction'] == 'SHORT' else '🟢'
    margin = (sig['contracts'] * sig['entry'] / 1000) / LEVERAGE
    return f"""
{'='*50}
{dir_emoji}【自动交易信号】{sig['direction']}
{'='*50}
品种：BTC-USDT-SWAP
方向：{sig['direction']}
入场：${sig['entry']}
止损：${sig['sl']} (+{SL_PCT*100:.0f}%)
止盈：移动止盈（1.5%触发→0.5%回调 / 2%触发→0.5%回调 / 趋势持有→回调0.5%）
张数：{sig['contracts']}张（迷你测试仓）
保证金：约{margin:.2f}U
信号条件：
{sig['reason']}
{'='*50}
回复「确认」执行，其他任意内容取消
"""

def format_status(market: dict, has_position: bool, active_pos: Optional[ActivePosition]) -> str:
    p = market['price']
    b = get_balance()
    sz = market['short_zone']
    lz = market['long_zone']

    in_short_zone = sz[0] <= p <= sz[1]
    in_long_zone = lz[0] <= p <= lz[1]

    status = """[%s]
BTC: $%.1f | 余额: %.2fU
RSI 4H:%.1f | RSI 1H:%.1f | RSI 30m:%.1f
MA20 4H:$%.0f | MA20 1H:$%.0f
资金费率: %.3f%%
从24h低反弹: +%.1f%% | 从24h高回落: -%.1f%%
做空区间: %.0f-%.0f (%s)
做多区间: %.0f-%.0f (%s)
""" % (
        datetime.now().strftime('%H:%M:%S'),
        p, b,
        market['rsi_4h'], market['rsi_1h'], market['rsi_30m'],
        market['ma20_4h'], market['ma20_1h'],
        market['fr']*100,
        market['from_low_pct'], market['from_high_pct'],
        sz[0], sz[1], "✅在区间" if in_short_zone else "❌不在",
        lz[0], lz[1], "✅在区间" if in_long_zone else "❌不在",
    )

    if has_position and active_pos:
        pnl_pct = (active_pos.entry - p) / active_pos.entry * 100 if active_pos.direction == 'SHORT' else (p - active_pos.entry) / active_pos.entry * 100
        status += f"""
持仓: {active_pos.direction} | {active_pos.entry} | {pnl_pct:+.2f}%"""

    return status

# ============ 主循环 ============
def run_monitor():
    print('=' * 60)
    print('🦞 混沌龙虾 自动交易系统 v3.0 启动')
    print('信号条件: 多周期RSI + MA20 + 资金费率 + 24h动态区间')
    print('=' * 60)

    active_pos: Optional[ActivePosition] = None
    pending_signal: Optional[dict] = None
    pending_zone: str = ''
    confirmed_direction: Optional[str] = None  # 等待用户确认

    while True:
        try:
            now = datetime.now()
            market = get_market_data()
            balance = get_balance()
            positions = get_positions()
            has_position = len(positions) > 0
            price = market['price']

            # ========== 有持仓 ==========
            if has_position:
                if active_pos is None or active_pos.status == 'closed':
                    # 初始化
                    p = positions[0]
                    direction = p['posSide'].upper()
                    entry = float(p['avgPx'])
                    active_pos = ActivePosition(
                        direction=direction,
                        entry=entry,
                        contracts=int(float(p['pos'])),
                        sl=round(entry * (1 + SL_PCT if direction == 'SHORT' else 1 - SL_PCT), 1),
                        best_price=price if direction == 'SHORT' else price,
                    )

                pnl_pct = (active_pos.entry - price) / active_pos.entry * 100 if active_pos.direction == 'SHORT' else (price - active_pos.entry) / active_pos.entry * 100
                print(f"[{now.strftime('%H:%M:%S')}] {active_pos.direction} @ {active_pos.entry}→{price} | {pnl_pct:+.2f}%")

                result = monitor_position(active_pos, price)

                if result == 'EXIT':
                    print(f"  🎯 移动止盈触发！平仓")
                    close_all_positions()
                    active_pos.status = 'closed'
                    active_pos = None
                elif result == 'STOP':
                    print(f"  🛑 止损触发！平仓")
                    close_all_positions()
                    active_pos.status = 'closed'
                    active_pos = None

                # 过夜平仓
                if now.hour >= 22 or now.hour < 1:
                    print(f"  🌙 过夜平仓")
                    close_all_positions()
                    active_pos = None

                time.sleep(TRADE_INTERVAL)
                continue

            # ========== 无持仓，检查信号 ==========
            print(format_status(market, has_position, active_pos))

            signal, zone_desc = check_signals(market)

            if signal:
                print(format_signal(signal, zone_desc))
                pending_signal = signal
                confirmed_direction = signal['direction']

            time.sleep(TRADE_INTERVAL)

        except Exception as e:
            print(f'Error: {e}')
            import traceback; traceback.print_exc()
            time.sleep(TRADE_INTERVAL)

if __name__ == '__main__':
    run_monitor()
