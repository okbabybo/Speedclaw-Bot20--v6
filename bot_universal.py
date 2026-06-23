#!/usr/bin/env python3
"""20x杠杆 精准信号策略 v6.0 - 双交易所版
同时支持币安 + OKX
v6.0: 从 bot_20x.py v5.6 移植，策略逻辑完全保留
"""
import requests, time, json, hmac, hashlib, yaml
from datetime import datetime
from exchange_adapter import create_adapter

# ===================== 配置加载 =====================
CONFIG_FILE = "/root/.openclaw/workspace/config_exchange.yaml"

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

config = load_config()

# 全局参数（从config覆盖）
SYMBOLS = config.get('symbols', ['BTCUSDT', 'ETHUSDT'])
LEVER = config.get('lever', 20)
LOG_FILE = config.get('log_file', '/root/.openclaw/workspace/bot_universal.log')
STATE_DIR = config.get('state_dir', '/root/.openclaw/workspace/')

# ===================== 策略参数（与v5.6完全一致）=====================
ADX_PERIOD = 14
ADX_TREND_THRESH = 25
ADX_WEAK_THRESH = 20
LOSS_STREAK_LIMIT = 3
LOSS_STREAK_PAUSE = 15*60

SL_ATR_MULT = 0.025
TP1_PCT = 0.02
TP2_TRIGGER = 0.03
TP2_BUFFER = 0.01
WIN_STREAK_ACCEL = 2
WIN_STREAK_THRESH = 0.05
ACCEL_SCORE_BOOST = 2

MAX_POS_PCT = 0.30
MAX_TOTAL_EXPOSURE = 1.50
MIN_BAL = 3
OPEN_COOLDOWN = 300
RISK_PCT = 0.10
RISK_DANGER = 20
RISK_DANGER_PCT = 0.05
RISK_RICH_PCT = 0.08
DRAWDOWN_PROTECT = 0.30
DRAWDOWN_COOLDOWN = 1800
DRAWDOWN_LOCK_SECS = 600
CRASH_WINDOW_SECS = 600
CRASH_LIMIT = 5
TREND_CONFLICT_FILTER = True
API_RETRY_MAX = 3
API_RETRY_DELAY = 2
API_TIMEOUT = 15
MIN_TRADE_INTERVAL = 30
MANUAL_CLOSE_COOLDOWN = 60

# ===================== 全局状态 =====================
loss_streak_count = 0
last_loss_time = 0
last_trade_time = 0

# ===================== 文件路径 =====================
CRASH_COUNT_FILE = STATE_DIR + ".crash_count"
HIGH_WATER_FILE = STATE_DIR + ".high_water"
DRAWDOWN_COOLDOWN_FILE = STATE_DIR + ".drawdown_cooldown"
DRAWDOWN_LOCK_FILE = STATE_DIR + ".drawdown_lock"
TREND_STATE_FILE = STATE_DIR + ".trend_state"
WARN_FILE = STATE_DIR + ".trend_warn"

# ===================== 工具函数 =====================
def get_state_file(exchange, symbol, direction):
    safe_name = symbol.replace("USDT", "").lower()
    return f"{STATE_DIR}st_{safe_name}_{exchange}_{direction.lower()}.json"

def get_crash_count():
    try:
        with open(CRASH_COUNT_FILE) as f:
            data = json.load(f)
        return data.get("count", 0), data.get("first_time", 0)
    except:
        return 0, 0

def increment_crash():
    count, first = get_crash_count()
    now = time.time()
    if first == 0 or (now - first) > CRASH_WINDOW_SECS:
        count, first = 0, now
    count += 1
    with open(CRASH_COUNT_FILE, "w") as f:
        json.dump({"count": count, "first_time": first}, f)
    return count

def check_crash_safety():
    count, first = get_crash_count()
    now = time.time()
    if first > 0 and (now - first) <= CRASH_WINDOW_SECS and count >= CRASH_LIMIT:
        log(f"⚠️ 安全模式：{CRASH_WINDOW_SECS//60}分钟内重启{count}次，等待冷静期...")
        return False
    return True

def get_high_water():
    try:
        with open(HIGH_WATER_FILE) as f:
            return float(f.read().strip())
    except:
        return 0

def save_high_water(bal):
    with open(HIGH_WATER_FILE, "w") as f:
        f.write(str(bal))

def check_drawdown_protection(balance):
    high = get_high_water()
    if high > 0 and balance < high * (1 - DRAWDOWN_PROTECT):
        return True, high
    return False, high

def is_drawdown_locked():
    try:
        with open(DRAWDOWN_LOCK_FILE) as f:
            return time.time() < float(f.read().strip())
    except:
        return False

def trigger_drawdown_lock():
    with open(DRAWDOWN_LOCK_FILE, "w") as f:
        f.write(str(time.time() + DRAWDOWN_LOCK_SECS))
    log(f"回撤冷静期锁定：{DRAWDOWN_LOCK_SECS//60}分钟")

def load_trend_state():
    try:
        with open(TREND_STATE_FILE) as f:
            return json.load(f)
    except:
        return {"btc_trend": None, "eth_trend": None, "last_warn": 0}

def save_trend_state(state):
    with open(TREND_STATE_FILE, "w") as f:
        json.dump(state, f)

def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

# ===================== 技术指标（与v5.6一致）=====================
def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = [max(0, prices[i]-prices[i-1]) for i in range(1,len(prices))]
    losses = [max(0, prices[i-1]-prices[i]) for i in range(1,len(prices))]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    return 100 - 100/(1 + avg_gain/avg_loss)

def calc_stoch_rsi(prices, period=14, smooth_k=3, smooth_d=3):
    if len(prices) < period+1: return 50, 50
    rsi_values = []
    for i in range(period, len(prices)+1):
        rsi = calc_rsi(prices[:i], period)
        rsi_values.append(rsi)
    if len(rsi_values) < 3: return 50, 50
    rsi_arr = rsi_values[-smooth_k:]
    lowest = min(rsi_arr); highest = max(rsi_arr)
    if highest == lowest: return 50, 50
    k = (rsi_values[-1] - lowest) / (highest - lowest) * 100
    d = sum(rsi_arr[-smooth_d:]) / smooth_d if len(rsi_arr) >= smooth_d else k
    return k, d

def calc_ema(prices, n):
    if len(prices) < n: return None
    k = 2/(n+1)
    ema = sum(prices[:n])/n
    for p in prices[n:]:
        ema = p*k + ema*(1-k)
    return ema

def calc_adx(klines, period=14):
    if len(klines) < period*2+1: return 20, False
    trs, pos_dm, neg_dm = [], [], []
    for i in range(1, len(klines)):
        high, low = float(klines[i][2]), float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        dm_plus = max(high - float(klines[i-1][2]), 0) if (high - float(klines[i-1][2])) > (float(klines[i-1][3]) - low) else 0
        dm_minus = max(float(klines[i-1][3]) - low, 0) if (float(klines[i-1][3]) - low) > (high - float(klines[i-1][2])) else 0
        trs.append(tr); pos_dm.append(dm_plus); neg_dm.append(dm_minus)
    adx_vals = []
    for i in range(period, len(trs)+1):
        tr_s = trs[i-period:i]; pdm_s = pos_dm[i-period:i]; ndm_s = neg_dm[i-period:i]
        atr_i = sum(tr_s)/period if sum(tr_s) > 0 else 1
        dp = sum(pdm_s)/period/atr_i*100 if atr_i > 0 else 0
        dn = sum(ndm_s)/period/atr_i*100 if atr_i > 0 else 0
        dx = abs(dp-dn)/(dp+dn)*100 if (dp+dn) > 0 else 0
        adx_vals.append(dx)
    adx = sum(adx_vals[-period:])/period if adx_vals else 20
    di_plus = sum(pos_dm[-period:])/period/trs[-1]*100 if trs[-1] > 0 else 0
    return min(adx, 60), di_plus > 0

def calc_atr(klines, period=14):
    if len(klines) < period+1: return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2]); low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period if trs else 0

def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow+1: return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    macd_history = [macd_line] * signal
    signal_line = calc_ema(macd_history, signal) if len(macd_history) >= signal else macd_line
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_bollinger(prices, period=20, mult=2):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:]) / period
    std = (sum((p - sma) ** 2 for p in prices[-period:]) / period) ** 0.5
    upper = sma + mult * std
    lower = sma - mult * std
    return upper, sma, lower

# ===================== 信号计算（与v5.6完全一致）=====================
def get_signal(exchange, symbol):
    ex = exchange  # exchange adapter
    k4h = ex.get_klines(symbol, "4h", 60)
    k1h = ex.get_klines(symbol, "1h", 100)
    k15m = ex.get_klines(symbol, "15m", 100)
    
    if len(k4h) < 25 or len(k1h) < 25 or len(k15m) < 25:
        log(f"{exchange.name.upper()} {symbol} K线数据不足，跳过")
        return None
    
    c4h = [float(k[4]) for k in k4h]
    c1h = [float(k[4]) for k in k1h]
    c15m = [float(k[4]) for k in k15m]
    v15m = [float(k[5]) for k in k15m]
    
    cur = c1h[-1]
    r4 = calc_rsi(c4h, 14)
    r1 = calc_rsi(c1h, 14)
    r15 = calc_rsi(c15m, 14)
    
    sk15, sd15 = calc_stoch_rsi(c15m, 14, 3, 3)
    sk1, sd1 = calc_stoch_rsi(c1h, 14, 3, 3)
    sk15_v = sk15 if sk15 is not None else 50
    sk1_v = sk1 if sk1 is not None else 50
    
    atr = calc_atr(k15m, 14)
    vr = v15m[-1] / (sum(v15m[-20:])/20) if len(v15m) >= 20 else 1
    
    adx_val, adx_bullish = calc_adx(k1h, ADX_PERIOD)
    market_trending = adx_val >= ADX_TREND_THRESH
    market_weak = adx_val < ADX_WEAK_THRESH
    
    macd_line, macd_signal, macd_hist = calc_macd(c1h)
    macd_bullish = macd_hist > 0
    macd_bearish = macd_hist < 0
    
    bb_upper, bb_mid, bb_lower = calc_bollinger(c15m, 20, 2)
    bb_position = (cur - bb_lower) / (bb_upper - bb_lower) if bb_upper and bb_lower and bb_upper != bb_lower else 0.5
    
    ema4h_20 = calc_ema(c4h, 20)
    ema4h_20_prev = calc_ema(c4h[:-4], 20)
    ema1h_20 = calc_ema(c1h, 20)
    ema1h_20_prev = calc_ema(c1h[:-1], 20)
    ema15m_20 = calc_ema(c15m, 20)
    ema15m_20_prev = calc_ema(c15m[:-1], 20)
    
    trend4h_price = cur > ema4h_20 and ema4h_20 > ema4h_20_prev
    trend4h_rsi = r4 > calc_rsi(c4h[:-4], 14)
    trend1h_price = cur > ema1h_20 and ema1h_20 > ema1h_20_prev
    trend1h_rsi = r1 > calc_rsi(c1h[:-1], 14)
    trend15m_price = c15m[-1] > ema15m_20 and ema15m_20 > ema15m_20_prev
    
    vol_avg = sum(v15m[-20:])/20 if len(v15m) >= 20 else v15m[-1]
    vol_confirm = vr > 1.5
    
    long_ready = (cur > ema1h_20 and trend1h_price and r1 < 50 and trend4h_price and r4 < 60 and (market_trending or r1 < 40)) and not market_weak
    oversold_guard = r4 < 15
    short_ready = (cur < ema1h_20 and r1 > 50 and r4 >= 15 and r4 < 60 and (market_trending or r1 > 55)) and not market_weak
    
    trend_score = 0
    trend_reasons = []
    if trend4h_price: trend_score += 2; trend_reasons.append("4H↑EMA")
    else: trend_reasons.append("4H↓EMA")
    if trend1h_price: trend_score += 1; trend_reasons.append("1H↑EMA")
    else: trend_reasons.append("1H↓EMA")
    if trend15m_price: trend_score += 0.5; trend_reasons.append("15m顺")
    if vol_confirm: trend_score += 1; trend_reasons.append(f"V={vr:.1f}x")
    if trend4h_rsi: trend_reasons.append("R4动↑")
    trend_up = trend1h_price and trend4h_price
    
    r15_prev = calc_rsi(c15m[:-1], 14)
    div_bull = r15 < 50 and r15 > r15_prev and r15_prev < 52
    div_bear = r15 > 50 and r15 < r15_prev and r15_prev > 48
    
    sig = None; reasons = []
    counter_trend_sig = None; counter_trend_reasons = []
    
    ema_deviation = abs(cur - ema1h_20) / ema1h_20 * 100
    
    # 逆势做多
    if r1 < 40 and ema_deviation > 0.5 and not market_weak:
        ct_score = 0; ct_reasons = []
        if r1 < 30: ct_score += 2; ct_reasons.append(f"R1={r1:.0f}<30极端")
        elif r1 < 35: ct_score += 1.5; ct_reasons.append(f"R1={r1:.0f}<35")
        else: ct_score += 1; ct_reasons.append(f"R1={r1:.0f}<40")
        if cur < ema1h_20 * 0.995: ct_score += 1.5; ct_reasons.append(f"偏离EMA>{0.5:.1f}%")
        elif cur < ema1h_20 * 0.99: ct_score += 1; ct_reasons.append(f"偏离EMA>{1.0:.1f}%")
        if sk15_v < 20: ct_score += 2; ct_reasons.append(f"Stoch15={sk15_v:.0f}<20")
        if sk1_v < 20: ct_score += 1; ct_reasons.append(f"Stoch1={sk1_v:.0f}<20")
        if div_bull: ct_score += 1.5; ct_reasons.append("底背")
        if ct_score >= 6.5:
            counter_trend_sig = "LONG"; counter_trend_reasons = ct_reasons
    
    # 逆势做空
    if r1 > 60 and ema_deviation > 0.5 and r4 >= 15 and not market_weak:
        ct_score = 0; ct_reasons = []
        if r1 > 70: ct_score += 2; ct_reasons.append(f"R1={r1:.0f}>70极端")
        elif r1 > 65: ct_score += 1.5; ct_reasons.append(f"R1={r1:.0f}>65")
        else: ct_score += 1; ct_reasons.append(f"R1={r1:.0f}>60")
        if cur > ema1h_20 * 1.005: ct_score += 1.5; ct_reasons.append(f"偏离EMA>{0.5:.1f}%")
        elif cur > ema1h_20 * 1.01: ct_score += 1; ct_reasons.append(f"偏离EMA>{1.0:.1f}%")
        if sk15_v > 80: ct_score += 2; ct_reasons.append(f"Stoch15={sk15_v:.0f}>80")
        if sk1_v > 80: ct_score += 1; ct_reasons.append(f"Stoch1={sk1_v:.0f}>80")
        if div_bear: ct_score += 1.5; ct_reasons.append("顶背")
        if ct_score >= 6.5:
            counter_trend_sig = "SHORT"; counter_trend_reasons = ct_reasons
    
    STRONG_TREND_MODE = trend_up
    
    # 做多评分
    long_score = 0; long_reasons = []
    long_rsi_thresh = 55 if STRONG_TREND_MODE else 45
    if r1 < 40: long_score += 1; long_reasons.append(f"R1={r1:.0f}<40")
    elif r1 < long_rsi_thresh: long_score += (1.0 if STRONG_TREND_MODE else 0.5); long_reasons.append(f"R1={r1:.0f}<{long_rsi_thresh}" + (" [趋势]" if STRONG_TREND_MODE else ""))
    if r4 < 50: long_score += 1; long_reasons.append(f"R4={r4:.0f}<50")
    if r15 < 40: long_score += 1; long_reasons.append(f"R15={r15:.0f}<40")
    if trend_up: long_score += 1; long_reasons.append("趋势↑")
    if sk15_v < 20: long_score += 2; long_reasons.append(f"StochK15={sk15_v:.0f}<20")
    if sk1_v < 20: long_score += 1; long_reasons.append(f"StochK1={sk1_v:.0f}<20")
    stoich_extreme = sk15_v < 20 or sk1_v < 20
    if 40 <= r1 < long_rsi_thresh and not stoich_extreme:
        long_score -= 0.5; long_reasons.append("放宽区无Stoch极端-0.5")
    if div_bull: long_score += 2; long_reasons.append("底背")
    if vr > (1.0 if STRONG_TREND_MODE else 1.5): long_score += 1; long_reasons.append(f"V={vr:.1f}x")
    if macd_bullish: long_score += 1; long_reasons.append("MACD多头")
    if bb_position < 0.2: long_score += 1.5; long_reasons.append(f"BB下轨={bb_position:.0%}")
    elif bb_position < 0.3: long_score += 1; long_reasons.append(f"BB偏低={bb_position:.0%}")
    if long_ready: long_score += 1.5; long_reasons.append(f"EMA确认({trend_score:.1f})")
    if trend_up and not long_ready: long_score += 0.5; long_reasons.append(f"EMA向上({trend_score:.1f})")
    
    if long_score >= (6.5 if not STRONG_TREND_MODE else 2.5):
        sig = "LONG"; reasons = long_reasons
    elif counter_trend_sig:
        sig = counter_trend_sig; reasons = counter_trend_reasons
    
    # 做空评分
    short_score = 0; short_reasons = []
    short_rsi_thresh = 45 if short_ready else 55
    if r1 > 35: short_score += 1; short_reasons.append(f"R1={r1:.0f}>35")
    elif r1 > 30: short_score += 0.5; short_reasons.append(f"R1={r1:.0f}>30")
    if r4 > 50: short_score += 1; short_reasons.append(f"R4={r4:.0f}>50")
    if r4 < 40: short_score += 0.5; short_reasons.append(f"R4={r4:.0f}<40强势")
    if r15 > 55: short_score += 1; short_reasons.append(f"R15={r15:.0f}>55")
    if not trend_up: short_score += 1; short_reasons.append("趋势↓")
    if short_ready and r1 > short_rsi_thresh:
        short_score += 0.5; short_reasons.append(f"R1={r1:.0f} [趋势跟随]")
    if sk15_v > 80: short_score += 2; short_reasons.append(f"StochK15={sk15_v:.0f}>80")
    if sk1_v > 80: short_score += 1; short_reasons.append(f"StochK1={sk1_v:.0f}>80")
    stoich_extreme_short = sk15_v > 80 or sk1_v > 80
    if 30 < r1 <= 40 and not stoich_extreme_short:
        short_score -= 0.5; short_reasons.append("放宽区无Stoch极端-0.5")
    if short_ready: short_score += 1.5; short_reasons.append(f"EMA确认({trend_score:.1f})")
    if div_bear: short_score += 2; short_reasons.append("顶背")
    if vr > 1.5: short_score += 1; short_reasons.append(f"V={vr:.1f}x")
    if macd_bearish: short_score += 1; short_reasons.append("MACD空头")
    if bb_position > 0.8: short_score += 1.5; short_reasons.append(f"BB上轨={bb_position:.0%}")
    elif bb_position > 0.7: short_score += 1; short_reasons.append(f"BB偏高={bb_position:.0%}")
    
    if short_score >= (6.5 if not short_ready else 5.0):
        sig = "SHORT"; reasons = short_reasons
    elif counter_trend_sig:
        sig = counter_trend_sig; reasons = counter_trend_reasons
    
    # 趋势冲突过滤
    trend_conflict = TREND_CONFLICT_FILTER and (trend4h_price != trend1h_price)
    if trend_conflict:
        sig = None; reasons = ["趋势冲突"]
    
    return {
        'cur': cur, 'r4': r4, 'r1': r1, 'r15': r15,
        'sk15': sk15, 'sk1': sk1,
        'atr': atr, 'vr': vr,
        'trend_up': trend_up, 'trend_score': trend_score,
        'long_ready': long_ready, 'short_ready': short_ready,
        'trend_reasons': trend_reasons,
        'trend4h_price': trend4h_price,
        'div': 'bull' if div_bull else ('bear' if div_bear else None),
        'macd_bullish': macd_bullish, 'macd_bearish': macd_bearish,
        'bb_position': bb_position,
        'sig': sig, 'reasons': reasons,
        'counter_trend': counter_trend_sig is not None,
        'trend_conflict': trend_conflict
    }

# ===================== 订单与风控 =====================
def calc_sl(entry, direction):
    sl_dist = entry * SL_ATR_MULT
    return entry - sl_dist if direction == "LONG" else entry + sl_dist

def get_risk_pct(balance):
    if balance < RISK_DANGER:
        return RISK_DANGER_PCT
    elif balance > 80:
        return RISK_RICH_PCT
    return RISK_PCT

def get_max_pos_qty(balance, price):
    return round((balance * MAX_POS_PCT) / price, 3)

def calc_qty(balance, price):
    risk_pct = get_risk_pct(balance)
    risk_amount = balance * risk_pct
    sl_dist = price * SL_ATR_MULT
    if sl_dist == 0: return 0
    qty = risk_amount / sl_dist
    min_qty = max(0.001, round(risk_amount / price, 3))
    max_qty = get_max_pos_qty(balance, price)
    return max(min_qty, min(round(qty, 3), max_qty))

def do_order(exchange, symbol, side, posSide, qty):
    log(f"[下单] {exchange.name.upper()} {symbol} {side} {posSide} qty={qty:.3f}")
    try:
        ok = exchange.market_order(symbol, side, posSide, qty)
        if ok:
            log(f"[成功] {exchange.name.upper()} {symbol} {side} {posSide}")
            return True
        else:
            log(f"[失败] {exchange.name.upper()} {symbol} {side} {posSide}")
            return False
    except Exception as e:
        log(f"[异常] {exchange.name.upper()} {symbol} {side} {posSide} {e}")
        return False

def check_trend_reversal_warning(exchange, symbol, current_trend_up, positions):
    now = time.time()
    state = load_trend_state()
    key = symbol.replace("USDT", "").lower() + "_" + exchange.name + "_trend"
    prev_trend = state.get(key)
    last_warn = state.get("last_warn", 0)
    
    if prev_trend is not None and prev_trend != current_trend_up:
        if now - last_warn < 300:
            return
        for direction in ["LONG", "SHORT"]:
            pos = positions.get(direction)
            if not pos:
                continue
            if (current_trend_up and direction == "SHORT") or (not current_trend_up and direction == "LONG"):
                old_str = "下降" if not prev_trend else "上升"
                new_str = "上升" if current_trend_up else "下降"
                msg = f"⚠️ 【趋势反转预警】\n\n{exchange.name.upper()} {symbol}\n1H趋势：{old_str} → {new_str}\n持仓：{direction} {pos['qty']} @ ${round(pos['entry'], 2)}"
                log(f"🚨 趋势反转预警：{exchange.name.upper()} {symbol} {direction}")
                state["last_warn"] = now
                save_trend_state(state)
                with open(WARN_FILE, "w") as f:
                    f.write(msg)
                return
    state[key] = current_trend_up
    save_trend_state(state)

# ===================== 主循环（单交易所）=====================
def run_exchange(exchange, symbols):
    global loss_streak_count, last_loss_time, last_trade_time
    
    log(f"="*60)
    log(f"{exchange.name.upper()} 交易所启动 | 标的: {symbols}")
    log(f"="*60)
    
    state_files_tpl = {
        sym: {
            "LONG": get_state_file(exchange.name, sym, "LONG"),
            "SHORT": get_state_file(exchange.name, sym, "SHORT")
        }
        for sym in symbols
    }
    
    while True:
        try:
            bal = 0
            try:
                bal = exchange.get_balance() or 0
            except Exception as e:
                log(f"{exchange.name.upper()} 余额获取失败: {e}")
            
            now = time.time()
            
            if not check_crash_safety():
                time.sleep(30)
                continue
            
            if is_drawdown_locked():
                time.sleep(15)
                continue
            
            high_water = get_high_water()
            if bal > high_water:
                save_high_water(bal)
                high_water = bal
            
            try:
                with open(DRAWDOWN_COOLDOWN_FILE) as f:
                    last_drawdown = float(f.read().strip())
            except:
                last_drawdown = 0
            
            drawback_triggered, high = check_drawdown_protection(bal)
            if drawback_triggered and (now - last_drawdown) > DRAWDOWN_COOLDOWN:
                log(f"⚠️ 回撤保护：${high:.2f} → ${bal:.2f}，减半仓")
                trigger_drawdown_lock()
                with open(DRAWDOWN_COOLDOWN_FILE, "w") as f:
                    f.write(str(now))
                for sym in symbols:
                    sf = state_files_tpl[sym]
                    for direction in ["LONG", "SHORT"]:
                        try:
                            with open(sf[direction]) as f:
                                s = json.load(f)
                        except:
                            continue
                        if s.get("pos") and s.get("qty"):
                            half_qty = round(s["qty"] / 2, 3)
                            if half_qty >= 0.001:
                                exchange.market_order(sym, "SELL" if s["pos"]=="LONG" else "BUY", s["pos"], half_qty)
                                log(f"{sym} {s['pos']} 回撤减半：出{half_qty}")
                                s["qty"] = round(s["qty"] - half_qty, 3)
                                if s["qty"] < 0.001:
                                    s.clear()
                                with open(sf[direction], "w") as f:
                                    json.dump(s, f)
                        time.sleep(2)
                time.sleep(3)
                continue
            
            # 总仓位检查
            total_exposure = 0
            for sym in symbols:
                try:
                    cur_price = exchange.get_klines(sym, "1m", 1)
                    if cur_price:
                        cur_price = float(cur_price[0][4])
                    else:
                        cur_price = 0
                    positions = exchange.get_positions(sym)
                    for side, pos in positions.items():
                        if pos['qty'] > 0:
                            total_exposure += (pos['qty'] * (cur_price or pos['entry'])) / LEVER
                except Exception as e:
                    pass
            
            if total_exposure > bal * MAX_TOTAL_EXPOSURE:
                log(f"⚠️ 总仓位超限：${total_exposure:.2f} > ${bal:.2f}×{MAX_TOTAL_EXPOSURE}")
                time.sleep(15)
                continue
            
            if loss_streak_count >= LOSS_STREAK_LIMIT and (now - last_loss_time) < LOSS_STREAK_PAUSE:
                log(f"熔断中：连续{loss_streak_count}亏，剩余{(LOSS_STREAK_PAUSE-(now-last_loss_time))//60:.0f}分钟")
                time.sleep(15)
                continue
            elif loss_streak_count >= LOSS_STREAK_LIMIT:
                loss_streak_count = 0
                log("熔断恢复")
            
            for symbol in symbols:
                sf = state_files_tpl[symbol]
                info = get_signal(exchange, symbol)
                if info is None:
                    time.sleep(5)
                    continue
                
                try:
                    positions = exchange.get_positions(symbol)
                except Exception as e:
                    log(f"持仓获取失败: {e}")
                    positions = {}
                
                check_trend_reversal_warning(exchange, symbol, info.get('trend_up', False), positions)
                
                for direction in ["LONG", "SHORT"]:
                    sf_file = sf[direction]
                    try:
                        with open(sf_file) as f:
                            s = json.load(f)
                    except:
                        s = {}
                    
                    pos = positions.get(direction)
                    
                    if s.get("pos") and not pos:
                        log(f"{exchange.name.upper()} {symbol} {direction} 手动平仓已同步")
                        s["closed"] = now
                        s["manual_close_dir"] = direction
                        s["manual_close_time"] = now
                        s["last"] = s.get("last", "closed")
                        s.pop("pos", None)
                        with open(sf_file, "w") as f:
                            json.dump(s, f)
                        continue
                    
                    if not pos:
                        sig = info['sig']
                        closed_time = s.get("closed") or (now - OPEN_COOLDOWN - 1)
                        win_streak = s.get("win_streak", 0)
                        accel_active = win_streak >= WIN_STREAK_ACCEL
                        reverse_target = None
                        
                        if accel_active:
                            if direction == "SHORT":
                                if info.get('r1', 99) > 33 or (info.get('r4', 99) > 50 and not info.get('trend_up')):
                                    sig = "SHORT"
                                    log(f"{symbol} SHORT 加速模式(R1={info['r1']:.0f}，连赢{win_streak}次)")
                            elif direction == "LONG":
                                if info.get('trend_up') and info.get('r1', 99) < 47:
                                    sig = "LONG"
                                    log(f"{symbol} LONG 加速模式(R1={info['r1']:.0f}，连赢{win_streak}次)")
                        
                        # 超卖保护
                        if info.get('r4', 99) < 15 and direction == "SHORT":
                            if info.get('r1', 99) < 35 and info.get('sk15', 99) < 20:
                                reverse_target = "LONG"
                                log(f"{symbol} 超卖→反向LONG(R1={info['r1']:.0f})")
                            else:
                                log(f"{symbol} 超卖保护跳过")
                                continue
                        
                        # 超买保护
                        if info.get('r4', 99) > 85 and direction == "LONG":
                            if info.get('r1', 99) > 65 and info.get('sk15', 99) > 80:
                                reverse_target = "SHORT"
                                log(f"{symbol} 超买→反向SHORT(R1={info['r1']:.0f})")
                            else:
                                log(f"{symbol} 超买保护跳过")
                                continue
                        
                        if not reverse_target:
                            trend_ok = info['long_ready'] if direction == "LONG" else info['short_ready']
                            if not trend_ok:
                                if direction == "LONG" and sig == "LONG" and info['r1'] < 55 and info['trend_up']:
                                    trend_ok = True
                                elif direction == "SHORT" and sig == "SHORT" and info['r1'] > 50 and not info['trend_up'] and not info.get('trend4h_price', True):
                                    trend_ok = True
                            if not trend_ok:
                                log(f"{symbol} {direction} 趋势不符 {info['trend_reasons']}")
                                continue
                        
                        sig_ok = (sig == direction) or (reverse_target is not None)
                        reasons = info['reasons'] if sig == direction else [f"反向:{reverse_target}"]
                        
                        if last_trade_time and (now - last_trade_time) < MIN_TRADE_INTERVAL:
                            log(f"{symbol} 防过度交易跳过")
                        elif sig_ok and s.get("manual_close_time") and s.get("manual_close_dir") == direction and (now - s["manual_close_time"]) < MANUAL_CLOSE_COOLDOWN:
                            remaining = int(MANUAL_CLOSE_COOLDOWN - (now - s["manual_close_time"]))
                            log(f"{symbol} 手动平仓冷静期：剩{remaining}秒")
                        elif sig_ok and reasons and bal > MIN_BAL and (now - closed_time) > OPEN_COOLDOWN:
                            actual_dir = reverse_target if reverse_target else direction
                            qty = calc_qty(bal, info['cur'])
                            log(f"{symbol} -> {actual_dir} {reasons} @{info['cur']:.0f} qty:{qty}")
                            if do_order(exchange, symbol, "BUY" if actual_dir=="LONG" else "SELL", actual_dir, qty):
                                entry = info['cur']
                                atr = info['atr']
                                actual_sf_file = sf[actual_dir]
                                s.clear()
                                s.update({
                                    "pos": actual_dir, "entry": entry, "qty": qty,
                                    "sl": calc_sl(entry, actual_dir),
                                    "atr": atr,
                                    "best": entry, "opened": now,
                                    "tp1_done": False, "tp2_done": False,
                                    "last": None, "win_streak": 0
                                })
                                with open(actual_sf_file, "w") as f:
                                    json.dump(s, f)
                                last_trade_time = now
                                time.sleep(3)
                        else:
                            sig_str = sig if sig else "无信号"
                            log(f"{symbol} {direction} {info['cur']:.0f} R4={info['r4']:.0f}/R1={info['r1']:.0f}/R15={info['r15']:.0f} Sk15={info['sk15']:.0f} V={info['vr']:.1f}x {sig_str}")
                    else:
                        d = pos["dir"]
                        entry = pos["entry"]
                        cur = info['cur']
                        atr = s.get("atr", info['atr'])
                        
                        if "sl" not in s:
                            s["sl"] = calc_sl(entry, d)
                        if "best" not in s:
                            s["best"] = entry
                        
                        if d == "LONG":
                            pnl = (cur - entry) / entry * 100
                            best_high = max(s.get("best", entry), cur)
                            s["best"] = best_high
                            
                            tp1_price = entry * (1 + TP1_PCT)
                            if not s.get("tp1_done") and cur >= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(exchange, symbol, "SELL", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_high * (1 - TP2_BUFFER)
                                if cur <= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(exchange, symbol, "SELL", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 出清")
                                    s["tp2_done"] = True
                                    s["last"] = "win"
                                    s.clear()
                                    loss_streak_count = max(0, loss_streak_count - 1)
                                    with open(sf_file, "w") as f:
                                        json.dump(s, f)
                                    continue
                        else:
                            pnl = (entry - cur) / entry * 100
                            best_low = min(s.get("best", entry), cur)
                            s["best"] = best_low
                            
                            tp1_price = entry * (1 - TP1_PCT)
                            if not s.get("tp1_done") and cur <= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(exchange, symbol, "BUY", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_low * (1 + TP2_BUFFER)
                                if cur >= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(exchange, symbol, "BUY", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 出清")
                                    s["tp2_done"] = True
                                    s["last"] = "win"
                                    s["win_streak"] = s.get("win_streak", 0) + 1
                                    s.clear()
                                    loss_streak_count = max(0, loss_streak_count - 1)
                                    with open(sf_file, "w") as f:
                                        json.dump(s, f)
                                    continue
                        
                        markers = []
                        if s.get("tp1_done"):
                            markers.append("TP1[OK]")
                        if s.get("tp2_done"):
                            markers.append("TP2[OK]")
                        fire = " *" if pnl > 1.0 else ""
                        m = " " + ",".join(markers) if markers else ""
                        log(f"{symbol} {d} {pnl:+.1f}%{fire}{m}")
                        
                        trend_reversed = (d == "LONG" and not info['trend_up']) or (d == "SHORT" and info['trend_up'])
                        if trend_reversed and not s.get("reversal_alert_sent"):
                            log(f"⚠️ 趋势反转预警 {symbol} {d} pnl:{pnl:+.1f}% 建议检查SL")
                            s["reversal_alert_sent"] = True
                        
                        with open(sf_file, "w") as f:
                            json.dump(s, f)
            
            time.sleep(15)
        except KeyboardInterrupt:
            log(f"{exchange.name.upper()} STOPPED")
            break
        except Exception as e:
            log(f"ERROR [{exchange.name.upper()}]: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(15)

# ===================== 主入口 =====================
def main():
    cfg = load_config()
    
    adapters = []
    for ex_cfg in cfg.get('exchanges', []):
        ex_name = ex_cfg['name']
        try:
            adapter = create_adapter(
                ex_name,
                ex_cfg['api_key'],
                ex_cfg['secret'],
                ex_cfg.get('passphrase', '')
            )
            # 测试连接
            bal = adapter.get_balance()
            log(f"✅ {ex_name.upper()} 连接成功，余额: ${bal:.2f}")
            adapters.append(adapter)
        except Exception as e:
            log(f"❌ {ex_name.upper()} 连接失败: {e}")
    
    if not adapters:
        log("没有可用的交易所，退出")
        return
    
    # 每个交易所独立运行
    # 简单方案：顺序跑（后续可改线程/多进程）
    for adapter in adapters:
        run_exchange(adapter, SYMBOLS)

if __name__ == "__main__":
    main()
