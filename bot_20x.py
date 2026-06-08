#!/usr/bin/env python3
"""20x杠杆 精准信号策略 v5.4
v5.4优化：双模式信号 - 强趋势中(4H+1H共振)自动切换到趋势跟随模式(RSI<50做多/>50做空)，避免踏空
v5.3优化：持仓中趋势反转保护 - 检测到持仓方向与4H趋势矛盾时预警（用户控制SL，AI只报不操作）
v5.2优化：API重试机制 + 趋势冲突过滤 + 趋势反转预警
修复：网络抖动时频繁崩溃 + 4H/1H趋势矛盾时逆势开仓 + 趋势反转时无预警
"""
import requests, time, json, hmac, hashlib
from datetime import datetime

API_KEY = "QccKkNLbtV61rJpOms4h2E0RWoZMfMhG2ar3v9tueF5kbQ6KkN4sUf5CFLLkMhzx"
SECRET  = "Q549z4g3QlOnVs0PDSCzW6Xy2nVt9763DMqWo64MLLDoUeV8MigrUGUQn2nZTDuU"
LOG_FILE = "/root/.openclaw/workspace/bot_20x.log"

# === 新增优化模块 ===
ADX_PERIOD = 14
ADX_TREND_THRESH = 25
ADX_WEAK_THRESH = 20
LOSS_STREAK_LIMIT = 3
LOSS_STREAK_PAUSE = 15*60
ATR_BREAKOUT_MULT = 2.0
ATR_TIGHT_MULT = 1.0
LOW_LIQ_START = 3
LOW_LIQ_END = 5

# === v5.2 新增：稳定性优化 ===
TREND_CONFLICT_FILTER = True  # 趋势冲突过滤（4H和1H矛盾时跳过信号）
API_RETRY_MAX = 3              # API重试次数
API_RETRY_DELAY = 2           # 重试延迟（秒，指数退避）
API_TIMEOUT = 15             # API超时时间

loss_streak_count = 0
last_loss_time = 0

LEVER = 20
RISK_PCT = 0.10
MIN_BAL = 3
OPEN_COOLDOWN = 0

SL_ATR_MULT = 0.02   # 改为固定2% SL（原1.5×ATR太紧）
TP1_PCT = 0.02       # 优化：3%→2%，更灵敏止盈，积小胜为大胜
TP2_TRIGGER = 0.04   # TP2从6%→4%，跟上TP1节奏
TP2_BUFFER = 0.008    # 追踪回撤1%→0.8%，更快保护利润
WIN_STREAK_ACCEL = 2   # 连赢2次TP1后激活加速模式
WIN_STREAK_THRESH = 0.05  # 加速模式下RSI门槛临时降5%
ACCEL_SCORE_BOOST = 2  # 加速模式下SHORT信号评分额外加分

# === 复利风控参数 ===
MAX_POS_PCT = 0.30      # 单标仓位上限：不超过余额的30%
MAX_TOTAL_EXPOSURE = 1.50  # 总仓位上限：所有仓位不超过余额的150%
DRAWDOWN_PROTECT = 0.15  # 利润保护：账户从高点回撤15%则减半仓
DRAWDOWN_COOLDOWN = 1800   # 回撤保护冷却期：30分钟内不重复触发
DRAWDOWN_COOLDOWN_FILE = "/root/.openclaw/workspace/.drawdown_cooldown"  # 冷却期记录
HIGH_WATER_FILE = "/root/.openclaw/workspace/.high_water"  # 历史最高余额记录
RISK_DANGER = 20       # 危险区余额阈值（低于此值风险减半）
RISK_DANGER_PCT = 0.05  # 危险区风控：风险从10%降到5%
RISK_RICH_PCT = 0.08   # 富裕区风控：余额>80时风险降到8%

# === v5.2 新增：趋势反转预警 ===
TREND_STATE_FILE = "/root/.openclaw/workspace/.trend_state"
TREND_WARN_COOLDOWN = 300  # 冷却5分钟
WARN_FILE = "/root/.openclaw/workspace/.trend_warn"  # 待发送预警文件

def load_trend_state():
    try:
        with open(TREND_STATE_FILE) as f:
            return json.load(f)
    except:
        return {"btc_trend": None, "eth_trend": None, "last_warn": 0}

def save_trend_state(state):
    with open(TREND_STATE_FILE, "w") as f:
        json.dump(state, f)

def check_trend_reversal_warning(symbol, current_trend_up, positions):
    now = time.time()
    state = load_trend_state()
    key = symbol.replace("USDT", "").lower() + "_trend"
    prev_trend = state.get(key)
    last_warn = state.get("last_warn", 0)
    
    if prev_trend is not None and prev_trend != current_trend_up:
        if now - last_warn < TREND_WARN_COOLDOWN:
            return
        for direction in ["LONG", "SHORT"]:
            pos = positions.get(direction)
            if not pos:
                continue
            if (current_trend_up and direction == "SHORT") or (not current_trend_up and direction == "LONG"):
                old_str = "下降" if not prev_trend else "上升"
                new_str = "上升" if current_trend_up else "下降"
                msg = f"⚠️ 【趋势反转预警】\n\n{symbol} 1H趋势：{old_str} → {new_str}\n\n当前持仓：{direction} {pos['qty']} @ ${round(pos['entry'], 2)}\n\n建议：考虑手动平仓，避免逆势持仓\n\n—— bot20x v5.2"
                log(f"🚨 趋势反转预警：{symbol} {direction} 逆势持仓中！")
                state["pending_warn"] = msg
                state["last_warn"] = now
                save_trend_state(state)
                with open(WARN_FILE, "w") as f:
                    f.write(msg)
                return
    state[key] = current_trend_up
    save_trend_state(state)

def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f: f.write(f"[{ts}] {msg}\n")

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = [max(0, prices[i]-prices[i-1]) for i in range(1,len(prices))]
    losses = [max(0, prices[i-1]-prices[i]) for i in range(1,len(prices))]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    return 100 - 100/(1 + avg_gain/avg_loss)

def calc_stoch_rsi(prices, period=14, smooth_k=3, smooth_d=3):
    """StochRSI = 随机RSI，捕捉中性区超买超卖"""
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

def calc_ma(prices, n):
    return sum(prices[-n:])/n if len(prices) >= n else None

def calc_ema(prices, n):
    """"EMA计算，比MA更灵敏"""
    if len(prices) < n: return None
    k = 2/(n+1)
    ema = sum(prices[:n])/n
    for p in prices[n:]:
        ema = p*k + ema*(1-k)
    return ema

def calc_adx(klines, period=14):
    """ADX趋势强度指标"""
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

def api_retry_call(func, *args, **kwargs):
    """带指数退避的API重试机制"""
    delay = API_RETRY_DELAY
    for attempt in range(API_RETRY_MAX):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < API_RETRY_MAX - 1:
                time.sleep(delay)
                delay *= 2  # 指数退避
            else:
                log(f"API重试{API_RETRY_MAX}次失败: {e}")
                raise

def bn_get(endpoint, params=""):
    ts = str(int(time.time()*1000))
    p = f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
    sig = hmac.new(SECRET.encode(), p.encode(), hashlib.sha256).hexdigest()
    return api_retry_call(requests.get, f"https://fapi.binance.com{endpoint}?{p}&signature={sig}",
                       headers={"X-MBX-APIKEY": API_KEY}, timeout=API_TIMEOUT).json()

def bn_post(endpoint, params):
    ts = str(int(time.time()*1000))
    p = f"{params}&timestamp={ts}"
    sig = hmac.new(SECRET.encode(), p.encode(), hashlib.sha256).hexdigest()
    return api_retry_call(requests.post, f"https://fapi.binance.com{endpoint}?{p}&signature={sig}",
                        headers={"X-MBX-APIKEY": API_KEY}, timeout=API_TIMEOUT).json()

def get_balance():
    try: return float(bn_get("/fapi/v2/account").get('availableBalance', 0))
    except: return 0

def get_all_positions(symbol):
    positions = {}
    for p in bn_get("/fapi/v2/positionRisk", f"symbol={symbol}"):
        amt = float(p.get('positionAmt', 0))
        if amt != 0:
            side = p['positionSide']
            positions[side] = {"dir": "LONG" if amt > 0 else "SHORT",
                                "qty": abs(amt), "entry": abs(float(p['entryPrice']))}
    return positions

def do_order(symbol, side, posSide, qty):
    params = f"symbol={symbol}&side={side}&positionSide={posSide}&type=MARKET&quantity={qty:.3f}"
    resp = bn_post("/fapi/v1/order", params)
    return resp.get("orderId") is not None

def get_klines(symbol, interval, limit=100):
    def _fetch():
        r = requests.get(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}', timeout=API_TIMEOUT)
        return r.json()
    return api_retry_call(_fetch)

def get_signal(symbol):
    k4h = get_klines(symbol, "4h", 60)
    k1h = get_klines(symbol, "1h", 100)
    k15m = get_klines(symbol, "15m", 100)
    
    c4h = [float(k[4]) for k in k4h]
    c1h = [float(k[4]) for k in k1h]
    c15m = [float(k[4]) for k in k15m]
    v15m = [float(k[5]) for k in k15m]
    
    cur = c1h[-1]
    r4 = calc_rsi(c4h, 14)
    r1 = calc_rsi(c1h, 14)
    r15 = calc_rsi(c15m, 14)
    
    # StochRSI（捕捉中性区机会）
    sk15, sd15 = calc_stoch_rsi(c15m, 14, 3, 3)
    sk1, sd1 = calc_stoch_rsi(c1h, 14, 3, 3)
    
    atr = calc_atr(k15m, 14)
    vr = v15m[-1] / (sum(v15m[-20:])/20) if len(v15m) >= 20 else 1
    
    # ===== ADX市场环境检测 =====
    adx_val, adx_bullish = calc_adx(k1h, ADX_PERIOD)
    market_trending = adx_val >= ADX_TREND_THRESH
    market_weak = adx_val < ADX_WEAK_THRESH

    # ===== 多周期趋势确认 v3：EMA20 + 成交量确认 =====
    # EMA20 趋势判断（替代原 MA20）
    ema4h_20 = calc_ema(c4h, 20)
    ema4h_20_prev = calc_ema(c4h[:-4], 20)
    ema1h_20 = calc_ema(c1h, 20)
    ema1h_20_prev = calc_ema(c1h[:-1], 20)
    ema15m_20 = calc_ema(c15m, 20)
    ema15m_20_prev = calc_ema(c15m[:-1], 20)
    
    # 4H趋势：价格>EMA20 且 EMA20向上
    trend4h_price = cur > ema4h_20 and ema4h_20 > ema4h_20_prev
    trend4h_rsi = r4 > calc_rsi(c4h[:-4], 14)
    
    # 1H趋势
    trend1h_price = cur > ema1h_20 and ema1h_20 > ema1h_20_prev
    trend1h_rsi = r1 > calc_rsi(c1h[:-1], 14)
    
    # 15M趋势（入场点精确判断）
    trend15m_price = c15m[-1] > ema15m_20 and ema15m_20 > ema15m_20_prev
    
    # 成交量确认（放量突破EMA20）
    vol_avg = sum(v15m[-20:])/20 if len(v15m) >= 20 else v15m[-1]
    vol_confirm = vr > 1.5  # 放量1.5倍确认趋势真实性
    
    # ===== 做多条件（全部满足才做多）=====
    long_ready = (cur > ema1h_20 and trend1h_price and r1 < 50 and trend4h_price and r4 < 60 and (market_trending or r1 < 40)) and not market_weak
    
    # ===== 做空条件（全部满足才做空）=====
    # r4<15时为超卖警戒，不允许做空（价格可能瞬间反弹）
    oversold_guard = r4 < 15
    short_ready = (cur < ema1h_20 and r1 > 35 and r4 >= 15 and r4 < 60 and (market_trending or r1 > 40)) and not market_weak
    
    # 趋势评分（用于日志显示）
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
    
    # RSI背离
    r15_prev = calc_rsi(c15m[:-1], 14)
    div_bull = r15 < 50 and r15 > r15_prev and r15_prev < 52
    div_bear = r15 > 50 and r15 < r15_prev and r15_prev > 48
    
    sig = None; reasons = []
    counter_trend_sig = None; counter_trend_reasons = []
    
    # ===== 逆势/震荡模式检测 ====
    # 当价格在均线附近徘徊，未形成明确趋势时，激活逆势模式
    ema_deviation = abs(cur - ema1h_20) / ema1h_20 * 100
    
    # 逆势做多：RSI极端超卖 + 价格偏离均线
    if r1 < 40 and ema_deviation > 0.5 and not market_weak:
        ct_score = 0; ct_reasons = []
        # RSI极端
        if r1 < 30: ct_score += 2; ct_reasons.append(f"R1={r1:.0f}<30极端")
        elif r1 < 35: ct_score += 1.5; ct_reasons.append(f"R1={r1:.0f}<35")
        else: ct_score += 1; ct_reasons.append(f"R1={r1:.0f}<40")
        # 价格偏离（逆势核心：价格必须远离均线才给信号）
        # 价格偏离（放宽到0.5%以上即可）
        if cur < ema1h_20 * 0.995: ct_score += 1.5; ct_reasons.append(f"偏离EMA>{0.5:.1f}%")
        elif cur < ema1h_20 * 0.99: ct_score += 1; ct_reasons.append(f"偏离EMA>{1.0:.1f}%")
        # StochRSI极端
        if sk15 < 20: ct_score += 2; ct_reasons.append(f"Stoch15={sk15:.0f}<20")
        if sk1 < 20: ct_score += 1; ct_reasons.append(f"Stoch1={sk1:.0f}<20")
        # 底背加分
        if div_bull: ct_score += 1.5; ct_reasons.append("底背")
        if ct_score >= 6.5:
            counter_trend_sig = "LONG"; counter_trend_reasons = ct_reasons
    
    # 逆势做空：RSI极端超买 + 价格偏离均线
    if r1 > 60 and ema_deviation > 0.5 and r4 >= 15 and not market_weak:
        ct_score = 0; ct_reasons = []
        if r1 > 70: ct_score += 2; ct_reasons.append(f"R1={r1:.0f}>70极端")
        elif r1 > 65: ct_score += 1.5; ct_reasons.append(f"R1={r1:.0f}>65")
        else: ct_score += 1; ct_reasons.append(f"R1={r1:.0f}>60")
        # 价格偏离（放宽到0.5%以上即可）
        if cur > ema1h_20 * 1.005: ct_score += 1.5; ct_reasons.append(f"偏离EMA>{0.5:.1f}%")
        elif cur > ema1h_20 * 1.01: ct_score += 1; ct_reasons.append(f"偏离EMA>{1.0:.1f}%")
        if sk15 > 80: ct_score += 2; ct_reasons.append(f"Stoch15={sk15:.0f}>80")
        if sk1 > 80: ct_score += 1; ct_reasons.append(f"Stoch1={sk1:.0f}>80")
        if div_bear: ct_score += 1.5; ct_reasons.append("顶背")
        if ct_score >= 6.5:
            counter_trend_sig = "SHORT"; counter_trend_reasons = ct_reasons
    
    # ===== v5.4 双模式：判断当前属于强趋势还是震荡 =====
    # 强趋势模式：4H+1H EMA共振（趋势跟随，RSI门槛放宽到50）
    # 震荡/逆势模式：趋势不明确或EMA矛盾（原有RSI门槛45/55）
    STRONG_TREND_MODE = long_ready  # 4H+1H同时确认上升
    
    # ===== 做多 =====
    long_score = 0; long_reasons = []
    
    # 核心条件（强趋势模式：RSI<50即可；震荡模式：RSI<45）
    long_rsi_thresh = 50 if STRONG_TREND_MODE else 45
    if r1 < 40: long_score += 1; long_reasons.append(f"R1={r1:.0f}<40")
    elif r1 < long_rsi_thresh: long_score += 0.5; long_reasons.append(f"R1={r1:.0f}<{long_rsi_thresh}" + (" [趋势跟随]" if STRONG_TREND_MODE else ""))  # 放宽区
    if r4 < 50: long_score += 1; long_reasons.append(f"R4={r4:.0f}<50")
    if r15 < 40: long_score += 1; long_reasons.append(f"R15={r15:.0f}<40")
    if trend_up: long_score += 1; long_reasons.append("趋势↑" + (" [共振]" if STRONG_TREND_MODE else ""))
    
    # StochRSI EMA平滑（减少噪音）
    if sk15 < 20: long_score += 2; long_reasons.append(f"StochK15={sk15:.0f}<20")
    if sk1 < 20: long_score += 1; long_reasons.append(f"StochK1={sk1:.0f}<20")
    
    # 放宽区(40-{long_rsi_thresh})必须有StochRSI极端值才能触发
    stoich_extreme = sk15 < 20 or sk1 < 20
    if 40 <= r1 < long_rsi_thresh and not stoich_extreme:
        long_score -= 0.5; long_reasons.append("放宽区无Stoch极端-0.5")
    
    # 加分项
    if div_bull: long_score += 2; long_reasons.append("底背")
    if vr > 1.5: long_score += 1; long_reasons.append(f"V={vr:.1f}x")
    
    # 趋势确认（多周期一致性）
    if long_ready: long_score += 1.5; long_reasons.append(f"EMA确认({trend_score:.1f})")
    
    if long_score >= 6.5:
        sig = "LONG"; reasons = long_reasons
    elif counter_trend_sig:
        sig = counter_trend_sig; reasons = counter_trend_reasons
    
    # ===== 做空（双模式）=====
    short_score = 0; short_reasons = []
    short_rsi_thresh = 50 if short_ready else 55  # 强趋势模式RSI>50即可；震荡模式RSI>55
    
    if r1 > 35: short_score += 1; short_reasons.append(f"R1={r1:.0f}>35")  # 优化：40→35，下降趋势RSI35已是高处
    elif r1 > 30: short_score += 0.5; short_reasons.append(f"R1={r1:.0f}>30")  # 放宽区
    if r4 > 50: short_score += 1; short_reasons.append(f"R4={r4:.0f}>50")
    if r4 < 40: short_score += 0.5; short_reasons.append(f"R4={r4:.0f}<40强势")  # 新增：4H超卖强势确认做空
    if r15 > 55: short_score += 1; short_reasons.append(f"R15={r15:.0f}>55")  # 优化：60→55，更灵敏
    if not trend_up: short_score += 1; short_reasons.append("趋势↓" + (" [共振]" if short_ready else ""))
    
    # v5.4新增：强趋势模式下RSI>50即给分（不做空等待极端值）
    if short_ready and r1 > short_rsi_thresh:
        short_score += 0.5; short_reasons.append(f"R1={r1:.0f} [趋势跟随]")
    
    # StochRSI EMA平滑（减少噪音）
    if sk15 > 80: short_score += 2; short_reasons.append(f"StochK15={sk15:.0f}>80")
    if sk1 > 80: short_score += 1; short_reasons.append(f"StochK1={sk1:.0f}>80")
    
    # 放宽区必须有StochRSI极端值才能触发
    stoich_extreme_short = sk15 > 80 or sk1 > 80
    if 30 < r1 <= 40 and not stoich_extreme_short:
        short_score -= 0.5; short_reasons.append("放宽区无Stoch极端-0.5")
    
    # 趋势确认（多周期一致性）
    if short_ready: short_score += 1.5; short_reasons.append(f"EMA确认({trend_score:.1f})")
    
    if div_bear: short_score += 2; short_reasons.append("顶背")
    if vr > 1.5: short_score += 1; short_reasons.append(f"V={vr:.1f}x")
    
    if short_score >= 6.5:
        sig = "SHORT"; reasons = short_reasons
    elif counter_trend_sig:
        sig = counter_trend_sig; reasons = counter_trend_reasons
    
    # === v5.2 新增：趋势冲突过滤 ===
    # 当4H和1H趋势方向矛盾时，拒绝信号（避免逆势开仓）
    trend_conflict = TREND_CONFLICT_FILTER and (trend4h_price != trend1h_price)
    if trend_conflict:
        sig = None; reasons = ["趋势冲突:4H↓EMA,1H↑EMA" if not trend4h_price else "趋势冲突:4H↑EMA,1H↓EMA"]
    
    return {
        'cur': cur, 'r4': r4, 'r1': r1, 'r15': r15,
        'sk15': sk15, 'sk1': sk1,
        'atr': atr, 'vr': vr,
        'trend_up': trend_up, 'trend_score': trend_score,
        'long_ready': long_ready, 'short_ready': short_ready,
        'trend_reasons': trend_reasons,
        'div': 'bull' if div_bull else ('bear' if div_bear else None),
        'sig': sig, 'reasons': reasons,
        'counter_trend': counter_trend_sig is not None,
        'trend_conflict': trend_conflict
    }

def calc_sl(entry, atr, direction, rsi=None):
    """固定2%止损（替代原ATR×1.5）"""
    sl_dist = entry * SL_ATR_MULT
    return entry - sl_dist if direction == "LONG" else entry + sl_dist

def get_risk_pct(balance):
    """根据余额动态调整风险比例"""
    if balance < RISK_DANGER:
        return RISK_DANGER_PCT
    elif balance > 80:
        return RISK_RICH_PCT
    else:
        return RISK_PCT

def get_max_pos_qty(balance, price):
    """单标最大仓位（不超过余额的30%）"""
    return round((balance * MAX_POS_PCT) / price, 3)


def get_high_water():
    """获取历史最高余额"""
    try:
        with open(HIGH_WATER_FILE) as f:
            return float(f.read().strip())
    except:
        return 0

def save_high_water(bal):
    """保存历史最高余额"""
    with open(HIGH_WATER_FILE, "w") as f:
        f.write(str(bal))

def check_drawdown_protection(balance):
    """检查回撤保护：高点回撤15%则触发减半仓"""
    high = get_high_water()
    if high > 0 and balance < high * (1 - DRAWDOWN_PROTECT):
        return True, high
    return False, high

def calc_qty(balance, atr, price):
    risk_pct = get_risk_pct(balance)
    risk_amount = balance * risk_pct
    sl_dist = price * SL_ATR_MULT   # 2%固定止损
    if sl_dist == 0: return 0
    qty = risk_amount / sl_dist
    min_qty = max(0.001, round(risk_amount / price, 3))
    # 应用单标仓位上限（不超过余额的30%）
    max_qty = get_max_pos_qty(balance, price)
    return max(min_qty, min(round(qty, 3), max_qty))

def main():
    log("="*60)
    log("20x 精准信号v5.4 | 双模式趋势跟随 | 分批止盈")
    log("="*60)
    
    state_files = {
        "BTCUSDT": {"LONG": "/root/.openclaw/workspace/st_btc_long.json", "SHORT": "/root/.openclaw/workspace/st_btc_short.json"},
        "ETHUSDT": {"LONG": "/root/.openclaw/workspace/st_eth_long.json", "SHORT": "/root/.openclaw/workspace/st_eth_short.json"},
    }
    
    while True:
        try:
            bal = get_balance()
            now = time.time()
            hour_utc = int(datetime.utcnow().strftime('%H'))
            
            # === 复利风控：更新历史最高 & 检查回撤 ===
            high_water = get_high_water()
            if bal > high_water:
                save_high_water(bal)
                high_water = bal
            # 回撤保护冷却期检查
            try:
                with open(DRAWDOWN_COOLDOWN_FILE) as f:
                    last_drawdown = float(f.read().strip())
            except:
                last_drawdown = 0
            
            drawback_triggered, high = check_drawdown_protection(bal)
            if drawback_triggered and (now - last_drawdown) > DRAWDOWN_COOLDOWN:
                log(f"⚠️ 回撤保护触发：高点${high:.2f} → 当前${bal:.2f}，减半仓")
                with open(DRAWDOWN_COOLDOWN_FILE, "w") as f:
                    f.write(str(now))
                # 遍历所有状态文件，减半所有持仓
                for sym, sf in state_files.items():
                    for direction in ["LONG", "SHORT"]:
                        try:
                            with open(sf[direction]) as f: s = json.load(f)
                        except: continue
                        if s.get("pos") and s.get("qty"):
                            half_qty = round(s["qty"] / 2, 3)
                            if half_qty >= 0.001:
                                do_order(sym, "SELL" if s["pos"]=="LONG" else "BUY", s["pos"], half_qty)
                                log(f"{sym} {s['pos']} 回撤保护减半：出{half_qty}")
                                s["qty"] = round(s["qty"] - half_qty, 3)
                                if s["qty"] < 0.001:
                                    s.clear()
                                with open(sf[direction], "w") as f: json.dump(s, f)
                        time.sleep(2)
                time.sleep(3); continue  # 减仓后跳过本次循环
            
            # === 复利风控：总仓位上限检查（按实际保证金算）===
            # 总暴露 = Σ(持仓数量 × 当前价格 ÷ 杠杆) = 实际占用保证金
            total_exposure = 0
            for sym in ["BTCUSDT", "ETHUSDT"]:
                try:
                    cur_price = float(get_klines(sym, "1m", 1)[0][4])
                except:
                    cur_price = 0
                for p in bn_get("/fapi/v2/positionRisk", f"symbol={sym}"):
                    amt = abs(float(p.get('positionAmt', 0)))
                    if amt > 0:
                        entry = abs(float(p.get('entryPrice', 0)))
                        price_used = cur_price if cur_price > 0 else entry
                        # 按实际保证金算：名义值 ÷ 杠杆
                        total_exposure += (amt * price_used) / LEVER
            if total_exposure > bal * MAX_TOTAL_EXPOSURE:
                log(f"⚠️ 总仓位超限：${total_exposure:.2f} > ${bal:.2f}×{MAX_TOTAL_EXPOSURE}，暂停新开仓")
                time.sleep(15); continue

            global loss_streak_count, last_loss_time
            if loss_streak_count >= LOSS_STREAK_LIMIT and (now - last_loss_time) < LOSS_STREAK_PAUSE:
                log(f"熔断中：连续{loss_streak_count}亏，剩余{int(LOSS_STREAK_PAUSE-(now-last_loss_time))/60:.0f}分钟")
                time.sleep(15); continue
            elif loss_streak_count >= LOSS_STREAK_LIMIT:
                loss_streak_count = 0; log("熔断恢复")

            for symbol in ["BTCUSDT", "ETHUSDT"]:
                sf = state_files[symbol]
                info = get_signal(symbol)
                positions = get_all_positions(symbol)
                
                # === v5.2 新增：趋势反转预警 ===
                check_trend_reversal_warning(symbol, info.get('trend_up', False), positions)
                
                for direction in ["LONG", "SHORT"]:
                    sf_file = sf[direction]
                    try:
                        with open(sf_file) as f: s = json.load(f)
                    except: s = {}
                    
                    pos = positions.get(direction)
                    
                    if s.get("pos") and not pos:
                        log(f"{symbol} {direction} 手动平仓已同步 | 上次:{s.get('last','?')}")
                        s["closed"] = now
                        s["last"] = s.get("last", "closed")
                        s.pop("pos", None)
                        with open(sf_file, "w") as f: json.dump(s, f)
                        continue
                    
                    if not pos:
                        sig = info['sig']
                        closed_time = s.get("closed", now - OPEN_COOLDOWN - 1)
                        win_streak = s.get("win_streak", 0)  # 继承上次的连赢记录
                        accel_active = win_streak >= WIN_STREAK_ACCEL
                        reverse_target = None  # 反向信号标志：需要反向开仓时设置
                        
                        # ===== 加速模式：连赢后信号更灵敏（双向，必须符合趋势）=====
                        if accel_active:
                            if direction == "SHORT":
                                # 下降趋势中，连赢2次后RSI门槛临时降低，不放过做空机会
                                if info.get('r1', 99) > 33 or (info.get('r4', 99) > 50 and not info.get('trend_up')):
                                    sig = "SHORT"
                                    log(f"{symbol} SHORT 加速模式激活(R1={info['r1']:.0f}，连赢{win_streak}次)")
                            elif direction == "LONG":
                                # 上升趋势中，连赢2次后RSI门槛临时降低（必须趋势向上！）
                                if info.get('trend_up') and info.get('r1', 99) < 47:
                                    sig = "LONG"
                                    log(f"{symbol} LONG 加速模式激活(R1={info['r1']:.0f}，连赢{win_streak}次)")
                        
                        # ===== 反向持仓屏蔽已移除 =====
                        # opp_dir = "SHORT" if direction == "LONG" else "LONG"
                        # opp_file = sf[opp_dir]
                        # if opp_s.get("pos"):
                        #     log(f"{symbol} {direction} 屏蔽 — 反向{opp_dir}持仓中，不逆向开仓")
                        #     continue
                        
                        # ===== 反向机会（优先于趋势检查，修复漏洞5）=====
                        # 超卖时：RSI4H<15 + 走SHORT方向 → 检查是否反向做多
                        if info.get('r4', 99) < 15 and direction == "SHORT":
                            if info.get('r1', 99) < 35 and info.get('sk15', 99) < 20:
                                reverse_target = "LONG"
                                log(f"{symbol} 超卖→触发反向LONG(R1={info['r1']:.0f},Stoch15={info['sk15']:.0f})")
                            else:
                                log(f"{symbol} {direction} 超卖保护(r4={info['r4']:.1f}<15) 跳过")
                                continue  # 条件不满足才跳过
                        
                        # 超买时：RSI4H>85 + 走LONG方向 → 检查是否反向做空
                        if info.get('r4', 99) > 85 and direction == "LONG":
                            if info.get('r1', 99) > 65 and info.get('sk15', 99) > 80:
                                reverse_target = "SHORT"
                                log(f"{symbol} 超买→触发反向SHORT(R1={info['r1']:.0f},Stoch15={info['sk15']:.0f})")
                            else:
                                log(f"{symbol} {direction} 超买保护(r4={info['r4']:.1f}>85) 跳过")
                                continue  # 条件不满足才跳过
                        
                        # 【关键修复】：有反向信号时直接走反向流程，否则走正常趋势确认
                        if not reverse_target:
                            trend_ok = info['long_ready'] if direction == "LONG" else info['short_ready']
                            if not trend_ok:
                                log(f"{symbol} {direction} 趋势不符 {info['trend_reasons']} 跳过")
                                continue
                        
                        # 检查是否满足下单条件（正常信号或反向信号）
                        sig_ok = (sig == direction) or (reverse_target is not None)
                        reasons = info['reasons'] if sig == direction else [f"反向:{reverse_target}", f"R4={info['r4']:.0f},R1={info['r1']:.0f}"]
                        
                        if sig_ok and reasons and bal > MIN_BAL and (now - closed_time) > OPEN_COOLDOWN:
                            actual_dir = reverse_target if reverse_target else direction
                            qty = calc_qty(bal, info['atr'], info['cur'])
                            log(f"{symbol} -> {actual_dir} {reasons} @{info['cur']:.0f} qty:{qty}")
                            if do_order(symbol, "BUY" if actual_dir=="LONG" else "SELL", actual_dir, qty):
                                entry = info['cur']
                                atr = info['atr']
                                # 反向订单时，需要写入反向的状态文件
                                actual_sf_file = sf[actual_dir]
                                s.clear()
                                s.update({
                                    "pos": actual_dir, "entry": entry, "qty": qty,
                                    "sl": calc_sl(entry, atr, actual_dir),
                                    "atr": atr,
                                    "best": entry, "opened": now,
                                    "tp1_done": False, "tp2_done": False,
                                    "last": None, "win_streak": 0
                                })
                                with open(actual_sf_file, "w") as f: json.dump(s, f)
                                time.sleep(3)
                        else:
                            sig_str = sig if sig else "无信号"
                            log(f"{symbol} {direction} {info['cur']:.0f} R4={info['r4']:.0f}/R1={info['r1']:.0f}/R15={info['r15']:.0f} Sk15={info['sk15']:.0f} V={info['vr']:.1f}x {sig_str}")
                    else:
                        d = pos["dir"]; entry = pos["entry"]; cur = info['cur']
                        atr = s.get("atr", info['atr'])
                        
                        if "sl" not in s: s["sl"] = calc_sl(entry, atr, d)
                        if "best" not in s: s["best"] = entry
                        
                        if d == "LONG":
                            pnl = (cur - entry) / entry * 100
                            best_high = max(s.get("best", entry), cur)
                            s["best"] = best_high
                            
                            tp1_price = entry * (1 + TP1_PCT)
                            if not s.get("tp1_done") and cur >= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(symbol, "SELL", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_high * (1 - TP2_BUFFER)
                                if cur <= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(symbol, "SELL", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 剩余出清")
                                    s["tp2_done"] = True
                                    s["last"] = "win"
                                    s.clear()
                                    loss_streak_count = max(0, loss_streak_count-1)
                                    with open(sf_file, "w") as f: json.dump(s, f)
                                    continue
                            
                        else:
                            pnl = (entry - cur) / entry * 100
                            best_low = min(s.get("best", entry), cur)
                            s["best"] = best_low
                            
                            tp1_price = entry * (1 - TP1_PCT)
                            if not s.get("tp1_done") and cur <= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(symbol, "BUY", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_low * (1 + TP2_BUFFER)
                                if cur >= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(symbol, "BUY", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 剩余出清")
                                    s["tp2_done"] = True
                                    s["last"] = "win"
                                    s["win_streak"] = s.get("win_streak", 0) + 1
                                    s.clear()
                                    loss_streak_count = max(0, loss_streak_count-1)
                                    with open(sf_file, "w") as f: json.dump(s, f)
                                    continue
                        
                        markers = []
                        if s.get("tp1_done"): markers.append("TP1[OK]")
                        if s.get("tp2_done"): markers.append("TP2[OK]")
                        fire = " *" if pnl > 1.0 else ""
                        m = " " + ",".join(markers) if markers else ""
                        log(f"{symbol} {d} {pnl:+.1f}%{fire}{m}")
                        
                        # ===== v5.3 新增：趋势反转保护 =====
                        # 如果持仓方向与当前4H趋势矛盾 → 预警（用户控制SL，AI只报不操作）
                        trend_reversed = (d == "LONG" and not info['trend_up']) or (d == "SHORT" and info['trend_up'])
                        if trend_reversed and not s.get("reversal_alert_sent"):
                            log(f"⚠️ 【趋势反转预警】{symbol} {d} 趋势反转！pnl:{pnl:+.1f}% 建议手动检查SL | 入口:${entry:.0f} 现价:${cur:.0f}")
                            s["reversal_alert_sent"] = True
                        
                        with open(sf_file, "w") as f: json.dump(s, f)
            
            time.sleep(15)
        except KeyboardInterrupt:
            log("STOPPED"); break
        except Exception as e:
            log(f"ERROR: {e}"); import traceback; traceback.print_exc(); time.sleep(15)

if __name__ == "__main__":
    main()
