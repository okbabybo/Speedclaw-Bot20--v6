#!/usr/bin/env python3
"""
SpeedClaw BotKing 现货机器人 v1.0
混沌龙虾 🦞 — 独立部署版

名称：SpeedClaw BotKing
类型：现货智能网格+趋势双引擎
交易所：币安现货 USDT-M
"""

import requests, time, json, yaml, math
from datetime import datetime
from spot_adapter import BinanceSpotAdapter as SpotAdapter

# ===================== 配置 =====================
CONFIG_FILE = "/root/.openclaw/workspace/spot_config.yaml"

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

cfg = load_config()
LOG_FILE  = cfg.get('log_file', '/root/.openclaw/workspace/bot_king.log')
STATE_DIR = cfg.get('state_dir', '/root/.openclaw/workspace/')
STATE_FILE = STATE_DIR + "bot_king_state.json"

COINS = cfg.get('coins', ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'AVAXUSDT', 'XRPUSDT'])

# === BotKing 核心参数 ===
GRID_PROFIT     = 0.006    # 每格0.6%
GRID_VOL_PROFIT = 0.010    # 高波动每格1%
SL_PCT          = 0.12     # 止损12%
TS_PCT          = 0.03     # 追踪回撤3%
TP_TREND1       = 0.15     # 趋势第一目标+15%
TP_TREND2       = 0.25     # 趋势第二目标+25%
TS_TREND_PCT    = 0.05     # 趋势追踪回撤5%

# 资金分级（现货无杠杆）
TIER1 = 50
TIER2 = 150
TIER3 = 500
TIER4 = 1500

# 风控
DRAWDOWN_PROTECT = 0.20
MAX_DAILY_LOSS   = 0.08
CRASH_LIMIT      = 3
CRASH_PAUSE      = 900
PROFIT_LOCK      = 0.50
PHASE2_DELAY     = 300

# ATR自适应
ATR_GRID_MAP = {
    'high':   (2, 0.010),
    'medium': (4, 0.006),
    'low':    (6, 0.004),
}

# 运行
CHECK_INTERVAL = 20
SCAN_INTERVAL  = 180
SAVE_INTERVAL  = 60
MAX_POSITIONS  = 3

# === 市场宏观过滤 ===
FEAR_GREED_URL = "https://api.alternative.me/fng/"
FEAR_GREED_COOLDOWN = 3600  # Fear/Greed数据每小时更新，缓存1小时
_last_fear_greed = 75  # 默认中立
_last_fg_fetch = 0

# 指标
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_MULT = 20, 2.0
ATR_PERIOD = 14

# ===================== 工具函数 =====================
def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = [max(0, prices[i]-prices[i-1]) for i in range(1,len(prices))]
    losses = [max(0, prices[i-1]-prices[i]) for i in range(1,len(prices))]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    return 100 - 100/(1 + avg_gain/avg_loss)

def calc_ema(prices, n):
    if len(prices) < n: return None
    k = 2/(n+1)
    ema = sum(prices[:n])/n
    for p in prices[n:]: ema = p*k + ema*(1-k)
    return ema

def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow+signal: return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    if ema_fast is None or ema_slow is None: return 0, 0, 0
    macd = ema_fast - ema_slow
    return macd, 0, 0

def calc_atr(klines, period=14):
    if not klines or len(klines) < period+1: return 0
    trs = []
    for i in range(1, len(klines)):
        h, l, c = float(klines[i][2]), float(klines[i][3]), float(klines[i][4])
        prev_c = float(klines[i-1][4])
        tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
        trs.append(tr)
    if len(trs) < period: return 0
    return sum(trs[-period:]) / period

def calc_adx(klines, period=14):
    """计算ADX趋势强度指标（ADX>25=强趋势，<20=震荡）"""
    if not klines or len(klines) < period+2: return 20
    pdm, mdm, trs = [], [], []
    for i in range(1, len(klines)):
        h, l, c = float(klines[i][2]), float(klines[i][3]), float(klines[i][4])
        ph, pl = float(klines[i-1][2]), float(klines[i-1][3])
        up = h - ph; dn = pl - l
        pdm.append(max(up, dn) if up > dn else 0)
        mdm.append(max(dn, up) if dn > up else 0)
        tr = max(h-l, abs(h-c), abs(l-c))
        trs.append(tr)
    if len(trs) < period: return 20
    pdi = sum(pdm[-period:]) / sum(trs[-period:]) * 100 if sum(trs[-period:]) > 0 else 0
    mdi = sum(mdm[-period:]) / sum(trs[-period:]) * 100 if sum(trs[-period:]) > 0 else 0
    dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
    return min(dx, 100)  # ADX范围0-100

def get_phase1_grids(balance):
    if balance < 100:  return 1
    if balance < 300:  return 2
    return 2

def get_fear_greed():
    """获取Fear & Greed指数（0-100），0=极度恐慌，100=极度贪婪"""
    global _last_fear_greed, _last_fg_fetch
    now = time.time()
    if now - _last_fg_fetch < FEAR_GREED_COOLDOWN:
        return _last_fear_greed
    try:
        r = requests.get(FEAR_GREED_URL, timeout=5)
        data = r.json().get('data', [{}])[0]
        _last_fear_greed = int(data.get('value', 50))
        _last_fg_fetch = now
        log(f"[宏观] Fear & Greed: {_last_fear_greed} ({data.get('value_classification','')})")
    except:
        log(f"[宏观] Fear & Greed获取失败，使用默认值50")
    return _last_fear_greed

def is_extreme_hour():
    """北京时间22:00-02:00为流动性极差的极端时段，禁止开仓"""
    from datetime import datetime
    h = datetime.utcfromtimestamp(time.time()).hour  # UTC小时
    # 北京时间 = UTC+8，所以北京时间22:00-02:00 = UTC14:00-18:00
    return 14 <= h <= 18

# ===================== 市场模式检测 =====================
def detect_market_mode(symbol, ex):
    """检测6种市场模式（多周期确认 + ADX趋势强度），返回(mode, info字典)"""
    c15m = ex.get_klines(symbol, "15m", 60)   # 15分钟辅助确认
    c1h  = ex.get_klines(symbol, "1h",  60)
    c4h  = ex.get_klines(symbol, "4h",  100)
    c1d  = ex.get_klines(symbol, "1d",  50)

    if not (c1h and c4h and c1d):
        return "RANGE_BOUND", {'price': 0, 'rsi': 50, 'mode': 'RANGE_BOUND',
                               'grids': 4, 'grid_profit': GRID_PROFIT, 'atr': 0,
                               'trend_bias': 0.3, 'confidence': 0}

    closes_15m = [float(k[4]) for k in c15m] if c15m else []
    closes_1h = [float(k[4]) for k in c1h]
    closes_4h = [float(k[4]) for k in c4h]
    closes_1d = [float(k[4]) for k in c1d]
    cur = closes_1h[-1]
    if cur <= 0:
        return "RANGE_BOUND", {'price': 0, 'rsi': 50, 'mode': 'RANGE_BOUND',
                               'grids': 4, 'grid_profit': GRID_PROFIT, 'atr': 0,
                               'trend_bias': 0.3, 'confidence': 0}

    rsi_15m = calc_rsi(closes_15m) if closes_15m else 50
    rsi_1h  = calc_rsi(closes_1h)
    rsi_d1  = calc_rsi(closes_1d)

    ema20_15m = calc_ema(closes_15m, 20) if closes_15m else None
    ema20_1h  = calc_ema(closes_1h, 20)
    ema20_4h  = calc_ema(closes_4h, 20)
    ema20_1d  = calc_ema(closes_1d, 20)

    # ADX趋势强度
    adx_1h = calc_adx(c1h)
    adx_4h = calc_adx(c4h)
    adx_avg = (adx_1h + adx_4h) / 2

    # 趋势方向（明确：价格必须在EMA以上才算向上）
    trend_up_15m = ema20_15m is not None and closes_15m[-1] > ema20_15m
    trend_up_1h  = ema20_1h  is not None and cur > ema20_1h
    trend_up_4h  = ema20_4h  is not None and closes_4h[-1] > ema20_4h
    trend_up_d1  = ema20_1d  is not None and closes_1d[-1] > ema20_1d

    trend_down_15m = ema20_15m is not None and closes_15m[-1] < ema20_15m
    trend_down_1h  = ema20_1h  is not None and cur < ema20_1h
    trend_down_4h  = ema20_4h  is not None and closes_4h[-1] < ema20_4h
    trend_down_d1  = ema20_1d  is not None and closes_1d[-1] < ema20_1d

    macd, _, macd_sig = calc_macd(closes_1h)

    # RSI背离检测（价格创新低但RSI没创新低 = 底背离 = 反弹信号）
    rsi_bullish_div = False
    if len(closes_1h) >= 20:
        price_slope = closes_1h[-1] - closes_1h[-20]
        rsi_slope   = rsi_1h - calc_rsi(closes_1h[:-10]) if len(closes_1h) >= 30 else 0
        rsi_bullish_div = price_slope < -0.02 * cur and rsi_slope > 2  # 价格跌但RSI没跟随大跌

    # 成交量爆发
    vol_cur = float(c1h[-1][5]) if c1h else 0
    vol_avg = sum(float(k[5]) for k in c1h[-30:]) / 30 if c1h else 1
    vol_ratio = vol_cur / vol_avg if vol_avg > 0 else 1
    vol_surge = vol_ratio > 1.3

    # 波幅
    price_range = (max(closes_1h) - min(closes_1h)) / cur
    is_volatile = price_range > 0.08

    # ATR
    atr = calc_atr(c1h)
    atr_pct = atr / cur
    if atr_pct > 0.05:   atr_grids, atr_gp = ATR_GRID_MAP['high']
    elif atr_pct > 0.02: atr_grids, atr_gp = ATR_GRID_MAP['medium']
    else:                   atr_grids, atr_gp = ATR_GRID_MAP['low']

    # Fear & Greed宏观过滤
    fg = get_fear_greed()

    # 交易量确认（当前成交量应>均量70%以上才有意义）
    vol_valid = vol_ratio > 0.7

    # === 模式判断（带置信度）===
    mode = "RANGE_BOUND"
    confidence = 0.5

    # 1. 极端行情（最高优先级）
    if rsi_d1 > 80 or rsi_d1 < 20:
        mode = "CRISIS"
        confidence = 0.95
    # 2. 强上升趋势（ADX>25 + 三周期同向上 + MACD正向）
    elif adx_avg > 25 and trend_up_d1 and trend_up_4h and trend_up_1h:
        mode = "TREND_UP"
        confidence = min(0.95, 0.6 + (adx_avg - 25) / 75 + 0.1 * int(macd > macd_sig))
    # 3. 强下跌趋势（ADX>25 + 4H+1H向下 + RSI<50）
    elif adx_avg > 25 and trend_down_4h and trend_down_1h and rsi_1h < 50:
        mode = "TREND_DOWN"
        confidence = min(0.95, 0.6 + (adx_avg - 25) / 75)
    # 4. 趋势中继整理（在强趋势中回调，视为机会而非风险）
    #    条件：ADX>20 + 任意EMA同向 + RSI极端回调（<40或>60）
    #    例：上涨趋势中RSI回调到35 = 买入机会，不是转势
    elif adx_avg > 20 and (trend_up_4h or trend_up_d1):
        if rsi_1h < 35 and rsi_bullish_div:
            mode = "TREND_UP_RECALL"  # 趋势回调，是买入机会
            confidence = 0.75
        elif rsi_1h < 40 and vol_valid:
            mode = "TREND_UP_RECALL"
            confidence = 0.65
    # 5. 高波动超卖（RSI背离加分）
    elif is_volatile and rsi_1h < 35 and (vol_surge or rsi_bullish_div):
        mode = "VOLATILE_OVERSOLD"
        confidence = 0.7 + 0.1 * int(rsi_bullish_div) + 0.1 * int(vol_surge)
    # 6. 高波动超买
    elif is_volatile and rsi_1h > 65 and vol_surge:
        mode = "VOLATILE_OVERBOUGHT"
        confidence = 0.8
    # 7. 弱趋势/震荡（ADX<20时更确认震荡）
    elif adx_avg < 20:
        mode = "RANGE_BOUND"
        confidence = 0.8
    # 8. 普通震荡
    else:
        mode = "RANGE_BOUND"
        confidence = 0.6

    # Fear & Greed调整置信度（极度恐慌时超卖信号更可信）
    if fg < 25 and mode in ("VOLATILE_OVERSOLD", "TREND_UP_RECALL"):
        confidence = min(0.98, confidence + 0.15)
        log(f"[Fear & Greed] 极度恐慌{fg}，超卖信号置信度提升")
    elif fg > 75 and mode in ("VOLATILE_OVERBOUGHT", "CRISIS"):
        confidence = min(0.98, confidence + 0.1)

    # 交易量无效时降低置信度
    if not vol_valid and mode in ("VOLATILE_OVERSOLD", "VOLATILE_OVERBOUGHT"):
        confidence *= 0.7

    # === 模式参数（趋势回调等同于TREND_UP）===
    _mode_params = {
        "TREND_UP":            {"pos_pct": 1.0,  "grids": 2, "grid_profit": GRID_PROFIT,      "trend_bias": 1.0},
        "TREND_UP_RECALL":     {"pos_pct": 1.0,  "grids": 2, "grid_profit": GRID_PROFIT,      "trend_bias": 0.9},  # 回调买入按趋势做
        "TREND_DOWN":          {"pos_pct": 0.3,  "grids": 2, "grid_profit": GRID_PROFIT,      "trend_bias": 0.0},
        "RANGE_BOUND":         {"pos_pct": 0.8,  "grids": atr_grids, "grid_profit": atr_gp,  "trend_bias": 0.3},
        "VOLATILE_OVERSOLD":   {"pos_pct": 1.2,  "grids": min(atr_grids, 4), "grid_profit": atr_gp, "trend_bias": 0.7},
        "VOLATILE_OVERBOUGHT": {"pos_pct": 0.0,  "grids": 0, "grid_profit": atr_gp,          "trend_bias": 0.0},
        "CRISIS":              {"pos_pct": 0.0,  "grids": 0, "grid_profit": atr_gp,          "trend_bias": 0.0},
    }
    base_params = _mode_params[mode]

    # === 综合评分（决定买入优先级，置信度加权）===
    grid_score   = max(0, (60 - rsi_1h) / 60)
    trend_score  = max(0, (rsi_1h - 40) / 40)
    total_score  = (grid_score + trend_score * base_params['trend_bias']) * confidence
    if macd > macd_sig: total_score *= 1.1  # MACD柱在信号线上方

    return mode, {
        'price': cur,
        'rsi': rsi_1h,
        'rsi_15m': rsi_15m,
        'adx': adx_avg,
        'mode': mode,
        'confidence': confidence,
        'grids': base_params['grids'],
        'grid_profit': base_params['grid_profit'],
        'atr': atr,
        'trend_bias': base_params['trend_bias'],
        'total_score': total_score,
        'pos_pct': base_params['pos_pct'],
        'rsi_bullish_div': rsi_bullish_div,
        'vol_ratio': vol_ratio,
        'fear_greed': fg,
    }

class GridEngine:
    def __init__(self, symbol, entry_price, grids, grid_profit, atr, ex, capital, phase1_limit=2, sm=None):
        self.symbol = symbol
        self.entry_price = entry_price
        self.max_grids = grids
        self.grid_profit = grid_profit
        self.atr = atr
        self.ex = ex
        self.capital = capital
        self.phase1_limit = phase1_limit
        self.sm = sm           # StateManager引用（用于record_loss）
        self.pending_profit = 0
        self.last_tp_time = 0
        self._open_count = 0

        grid_range = max(atr * 3, entry_price * 0.12)
        self.upper = entry_price + grid_range / 2
        self.lower = entry_price - grid_range / 2
        self.grid_width = (self.upper - self.lower) / self.max_grids if self.max_grids > 0 else grid_range
        self.positions = {}
        self.position = {'symbol': symbol, 'qty': 0, 'entry': entry_price}
        self._all_sold = False  # 标记所有格都已平仓（重启后恢复）

    def get_grid_index(self, price):
        if price <= self.lower: return 0
        if price >= self.upper: return self.max_grids
        return int((price - self.lower) / self.grid_width)

    def invest_per_grid(self, locked_profit=0):
        active = len([p for p in self.positions.values() if not p.get('sold')])
        if active >= self.max_grids: return 0
        base = self.capital + locked_profit
        per_grid_max = base * 0.35
        return min(per_grid_max, base / (self.max_grids - active))

    def buy_grid(self, idx, price, locked_profit=0):
        # 防止重复开仓（race condition保护）
        if idx in self.positions and not self.positions[idx].get('sold'):
            return False
        invest = self.invest_per_grid(locked_profit)
        if invest < 11: return False
        qty = self._round_qty(invest / price)
        if qty <= 0: return False
        try:
            if self.ex.market_buy(self.symbol, qty):
                self.positions[idx] = {
                    'buy_price': price, 'qty': qty, 'sold': False,
                    'target': price * (1 + self.grid_profit),
                    'sl': price * (1 - SL_PCT),
                    'ts_triggered': False, 'ts_price': 0, 'ts_high': 0,
                    'bought_at': time.time(),
                    'profit_locked': invest * self.grid_profit * PROFIT_LOCK,
                }
                self._open_count += 1
                self.position['qty'] += qty
                log(f"[格买入] {self.symbol}格{idx}@{price:.4f} qty={qty:.4f} "
                    f"(已开{self._open_count}/{self.max_grids}格)")
                return True
        except Exception as e:
            log(f"[格买入失败] {self.symbol}格{idx}: {e}")
        return False

    def check_phased_open(self, cur_price):
        """分批开仓：Phase1→Phase2，Phase2由止盈利润触发"""
        now = time.time()
        if self._open_count >= self.max_grids: return
        # Phase2必须有止盈利润才能开
        if self.pending_profit <= 0: return
        # 止盈后必须等PHASE2_DELAY秒
        if now - self.last_tp_time < PHASE2_DELAY: return
        # Phase1未满时继续开Phase1（不用pending_profit）
        if self._open_count < self.phase1_limit: return
        # Phase1已满，开Phase2（用止盈利润开）
        for idx in range(self.max_grids):
            if idx not in self.positions:
                self.buy_grid(idx, cur_price, locked_profit=self.pending_profit)
                self.pending_profit = 0
                break

    def _round_qty(self, qty):
        rules = {'BTCUSDT':4,'ETHUSDT':4,'BNBUSDT':2,'SOLUSDT':1,'AVAXUSDT':2,'XRPUSDT':1}
        d = rules.get(self.symbol, 4)
        return math.floor(qty * 10**d) / 10**d

    def check(self, cur_price):
        for idx in list(self.positions.keys()):
            pos = self.positions[idx]
            if pos.get('sold') or pos['qty'] <= 0: continue
            bp = pos['buy_price']
            profit = (cur_price - bp) / bp

            # 追踪止损（动态上调）
            if profit > 0.06:
                if not pos.get('ts_triggered'):
                    pos['ts_triggered'] = True
                    pos['ts_price'] = cur_price * (1 - TS_PCT)
                    pos['ts_high'] = cur_price
                    log(f"[TS激活] {self.symbol}格{idx}@{cur_price:.4f} 触发={pos['ts_price']:.4f}")
                elif cur_price > pos.get('ts_high', 0):
                    pos['ts_high'] = cur_price
                    pos['ts_price'] = cur_price * (1 - TS_PCT)

            if pos.get('ts_triggered') and cur_price <= pos['ts_price']:
                self._sell_grid(idx, cur_price, "TS")
                continue

            # 止盈
            if cur_price >= pos['target']:
                self._sell_grid(idx, cur_price, "TP")
                continue

            # 止损
            if cur_price <= pos['sl']:
                self._sell_grid(idx, cur_price, "SL")
                continue

    def _sell_grid(self, idx, cur_price, reason):
        pos = self.positions.get(idx)
        if not pos or pos.get('sold'): return
        qty = pos['qty']
        if qty <= 0: return
        try:
            if self.ex.market_sell(self.symbol, qty):
                pnl = (cur_price - pos['buy_price']) / pos['buy_price'] * 100
                invest = pos['buy_price'] * qty
                profit = cur_price * qty - invest
                log(f"[格卖出] {self.symbol}格{idx}@{cur_price:.4f}({pnl:+.2f}%) {reason}")
                pos['sold'] = True
                pos['sold_price'] = cur_price
                pos['sold_at'] = time.time()
                self.position['qty'] = max(0, self.position['qty'] - qty)

                # 检查是否所有格都平了
                if all(p.get('sold') for p in self.positions.values()):
                    self._all_sold = True

                if reason in ('SL', 'TS'):
                    # 止损/追踪止损 → 记录亏损、触发冷静期
                    if self.sm: self.sm.record_loss()
                elif reason.startswith('TP'):
                    # 止盈 → 记录胜利、分配利润
                    if self.sm: self.sm.record_win()
                    locked = profit * PROFIT_LOCK
                    reinvest = profit * (1 - PROFIT_LOCK)
                    self.pending_profit += reinvest
                    self.last_tp_time = time.time()
                    log(f"  → 利润${profit:.2f} | 锁定50%=${locked:.2f} | 复利30%=${reinvest:.2f}")
        except Exception as e:
            log(f"[格卖出失败] {self.symbol}格{idx}: {e}")

    def adjust_center(self, cur_price):
        """网格区间动态调整：当价格持续偏离中心>20%时，重新居中"""
        center = (self.upper + self.lower) / 2
        drift = (cur_price - center) / center if center > 0 else 0
        # 价格偏离网格中心>25%时，重新计算区间
        if abs(drift) > 0.25:
            new_range = max(self.atr * 3, cur_price * 0.12)
            self.upper = cur_price + new_range / 2
            self.lower = cur_price - new_range / 2
            self.grid_width = (self.upper - self.lower) / self.max_grids
            log(f"[网格重置] {self.symbol} @{cur_price:.4f} 偏离{drift*100:+.0f}%，重新居中 "
                f"区间[{self.lower:.4f}, {self.upper:.4f}]")

    def has_position(self):
        return any(not p.get('sold') and p['qty'] > 0 for p in self.positions.values())

    def detect_manual_close(self, api_qty):
        """检测手动平仓（支持部分平仓检测）"""
        # 计算状态文件中该币的总持仓
        total_state_qty = sum(pos['qty'] for pos in self.positions.values()
                             if not pos.get('sold') and pos['qty'] > 0)
        # 如果API持仓 < 状态持仓，说明用户手动卖出了一部分
        if api_qty < total_state_qty - 0.00001:  # 允许微小误差
            for idx, pos in list(self.positions.items()):
                if pos.get('sold') or pos['qty'] <= 0: continue
                log(f"[⚠️ 手动平仓] {self.symbol}格{idx}@{pos['buy_price']:.4f} "
                    f"(API仅剩{api_qty})")
                pos['sold'] = True
                pos['sold_at'] = time.time()

# ===================== 趋势引擎 =====================
class TrendEngine:
    def __init__(self, symbol, ex, sm=None):
        self.symbol = symbol
        self.ex = ex
        self.sm = sm
        self.position = None
        self.entry_price = 0
        self.ts_triggered = False
        self.ts_price = 0
        self.peak_price = 0  # 持仓期间最高价（用于趋势破坏判断）

    def buy(self, price, qty):
        try:
            if self.ex.market_buy(self.symbol, qty):
                self.position = {'qty': qty, 'entry': price, 'tp1_done': False}
                self.entry_price = price
                self.peak_price = price
                log(f"[趋势买入] {self.symbol}@{price:.4f} qty={qty:.4f}")
                return True
        except: pass
        return False

    def check(self, cur_price):
        if not self.position: return
        entry = self.position['entry']
        qty = self.position['qty']
        profit = (cur_price - entry) / entry

        # 追踪止损（动态上调）
        if profit > 0.15 and not self.ts_triggered:
            self.ts_triggered = True
            self.ts_price = cur_price * (1 - TS_TREND_PCT)
        elif self.ts_triggered and cur_price > entry * 1.15:
            new_ts = cur_price * (1 - TS_TREND_PCT)
            if new_ts > self.ts_price: self.ts_price = new_ts

        if self.ts_triggered and cur_price <= self.ts_price:
            self._sell(cur_price, "TS")
            return

        # 趋势破坏退出：从峰值回落>8%且趋势已确认破坏
        if cur_price > self.peak_price:
            self.peak_price = cur_price
        drawdown_from_peak = (self.peak_price - cur_price) / self.peak_price if self.peak_price > 0 else 0
        if drawdown_from_peak > 0.08 and self.peak_price > entry * 1.10:
            log(f"[趋势破坏] {self.symbol}@{cur_price:.4f} 从峰值{self.peak_price:.4f}回落{drawdown_from_peak*100:.1f}%，趋势破坏止损")
            self._sell(cur_price, "TREND_BREAK")
            return

        # TP1: +15% 止盈50%
        if profit >= 0.15 and not self.position.get('tp1_done'):
            sell_qty = math.floor(qty * 0.5 * 10**4) / 10**4
            if sell_qty > 0:
                try:
                    self.ex.market_sell(self.symbol, sell_qty)
                    log(f"[TP1] {self.symbol}@{cur_price:.4f} 卖50%qty={sell_qty:.4f}")
                    self.position['qty'] -= sell_qty
                    self.position['tp1_done'] = True
                except: pass

        # TP2: +25% 止盈剩余
        if profit >= 0.25 and self.position['qty'] > 0:
            self._sell(cur_price, "TP2")

        # 止损
        if cur_price <= entry * (1 - SL_PCT):
            self._sell(cur_price, "SL")

    def _sell(self, price, reason):
        if not self.position or self.position['qty'] <= 0: return
        qty = self.position['qty']
        try:
            self.ex.market_sell(self.symbol, qty)
            pnl = (price - self.entry_price) / self.entry_price * 100
            log(f"[趋势卖出] {self.symbol}@{price:.4f}({pnl:+.2f}%) {reason}")
            if reason in ('SL', 'TS', 'TREND_BREAK') and self.sm:
                self.sm.record_loss()
            elif reason.startswith('TP') and self.sm:
                self.sm.record_win()
            self.position = None
        except: pass

# ===================== 状态管理 =====================
class StateManager:
    def __init__(self, ex, fpath):
        self.ex = ex
        self.fpath = fpath
        self.data = self._load()
        self.high_water = self.data.get('high_water', 0)
        self.total_profit_taken = self.data.get('total_profit_taken', 0)
        self.loss_streak = self.data.get('loss_streak', 0)
        self.last_loss_time = self.data.get('last_loss_time', 0)
        self.loss_cooldown = self.data.get('loss_cooldown', 0)
        self.lock_until = self.data.get('lock_until', 0)
        self.daily_loss = self.data.get('daily_loss', 0)
        self.daily_reset_time = self.data.get('daily_reset_time', 0)
        self.market_mode = "RANGE_BOUND"

    def _load(self):
        try:
            with open(self.fpath) as f: return json.load(f)
        except: return {}

    def save(self):
        self.data.update({
            'high_water': self.high_water,
            'total_profit_taken': self.total_profit_taken,
            'loss_streak': self.loss_streak,
            'last_loss_time': self.last_loss_time,
            'loss_cooldown': self.loss_cooldown,
            'lock_until': self.lock_until,
            'daily_loss': self.daily_loss,
            'daily_reset_time': self.daily_reset_time,
            'saved_at': time.time(),
        })
        with open(self.fpath, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def get_balance(self):
        try: return self.ex.get_balance()
        except: return 0.0

    def record_loss(self):
        self.loss_streak += 1
        self.last_loss_time = time.time()
        self.loss_cooldown = min(self.loss_streak * 300, CRASH_PAUSE)
        self.save()

    def record_win(self):
        if self.loss_streak > 0:
            self.loss_streak = 0
            self.loss_cooldown = 0
            self.save()

    def check_loss_cooldown(self):
        if self.loss_streak >= 1 and self.loss_cooldown > 0:
            elapsed = time.time() - self.last_loss_time
            if elapsed < self.loss_cooldown:
                remaining = int(self.loss_cooldown - elapsed)
                log(f"[亏损冷静期] {self.loss_streak}连亏，还需等待{remaining//60}分钟")
                return False
            else:
                self.loss_cooldown = 0
        return True

    def check_crash_protection(self):
        if self.loss_streak >= CRASH_LIMIT:
            elapsed = time.time() - self.last_loss_time
            if elapsed < CRASH_PAUSE:
                remaining = int(CRASH_PAUSE - elapsed)
                log(f"[熔断] 连亏{CRASH_LIMIT}次，暂停{remaining//60}分钟")
                return False
            else:
                self.loss_streak = 0
                self.last_loss_time = 0
                self.loss_cooldown = 0
        return True

    def check_drawdown_protection(self, balance):
        if self.high_water > 0 and balance < self.high_water * (1 - DRAWDOWN_PROTECT):
            log(f"[⚠️ 回撤保护] ${self.high_water:.2f}→${balance:.2f}，清仓止损")
            self.lock_until = time.time() + 1800
            self.save()
            return False
        return True

    def check_daily_loss(self, balance):
        """日亏保护：单日亏损>8%暂停1小时，UTC0点重置"""
        now = time.time()
        # UTC0点重置
        if self.daily_reset_time == 0 or (now - self.daily_reset_time) >= 86400:
            self.daily_loss = 0
            self.daily_reset_time = now
            self.save()
            return True
        if self.high_water > 0:
            daily_pnl = (balance - self.high_water) / self.high_water
            if daily_pnl < -MAX_DAILY_LOSS:
                log(f"[⚠️ 日亏保护] 单日亏损{abs(daily_pnl)*100:.1f}% > {MAX_DAILY_LOSS*100:.0f}%，暂停1小时")
                self.lock_until = time.time() + 3600
                self.save()
                return False
        return True

    def check_take_profit(self, balance):
        if self.high_water > 0 and balance >= self.high_water * 1.20:
            profit = balance - self.high_water
            if profit >= 5:
                take = profit * 0.5
                log(f"[💰 提盈] 利润${profit:.2f} → 提取${take:.2f} | 新高点${balance:.2f}")
                self.total_profit_taken += take
                self.high_water = balance * 0.9
                self.save()
        if balance > self.high_water:
            self.high_water = balance
            self.save()

    def is_locked(self):
        return time.time() < self.lock_until

# ===================== 主程序 =====================
def main():
    log("=" * 70)
    log("  SpeedClaw BotKing 现货机器人 v1.0 🦞")
    log(f"  币种: {COINS}")
    log(f"  网格: 2-6格/0.4%-1.0% | 趋势:TP15%/25% | SL:12%")
    log(f"  熔断: 连亏3次暂停 | 回撤:>20%清仓 | 日亏:>8%暂停")
    log("=" * 70)

    # 加载API密钥
    try:
        with open('/root/.openclaw/workspace/config_exchange.yaml') as f:
            creds = yaml.safe_load(f)
        # config_exchange.yaml 结构: exchanges:[{name:binance, api_key, secret}, ...]
        for ex_cfg in creds.get('exchanges', []):
            if ex_cfg.get('name') == 'binance':
                api_key = ex_cfg['api_key']
                secret  = ex_cfg['secret']
                break
        else:
            raise ValueError("Binance not found in exchanges list")
    except:
        log("[错误] 读取config_exchange.yaml失败")
        return

    ex = SpotAdapter(api_key, secret)
    sm = StateManager(ex, STATE_FILE)

    balance = sm.get_balance()
    log(f"USDT余额: ${balance:.2f}")

    grid_engines = {}
    trend_engines = {}
    last_scan = last_save = 0
    last_manual_check = 0

    mode_emoji = {
        "TREND_UP": "🟢", "TREND_DOWN": "📉",
        "VOLATILE_OVERSOLD": "🔴", "VOLATILE_OVERBOUGHT": "🟠",
        "RANGE_BOUND": "📊", "CRISIS": "💥"
    }
    sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}

    while True:
        now = time.time()

        # === 余额更新 ===
        balance = sm.get_balance()

        # === 锁定检查 ===
        if sm.is_locked():
            time.sleep(30)
            continue

        if not sm.check_crash_protection():
            time.sleep(30)
            continue

        # === 熊市加强熔断 ===
        if sm.loss_streak >= CRASH_LIMIT and sm.market_mode in ("TREND_DOWN", "CRISIS"):
            log(f"[熊市锁定] 熔断+熊市，等待转势")
            time.sleep(60)
            continue

        # === 止损冷静期 ===
        if not sm.check_loss_cooldown():
            time.sleep(30)
            continue

        # === 回撤保护 ===
        if not sm.check_drawdown_protection(balance):
            for eng in list(grid_engines.values()):
                try:
                    cur = ex.get_price(eng.symbol)
                    for idx in list(eng.positions.keys()):
                        eng._sell_grid(idx, cur, "回撤保护")
                except: pass
            for eng in list(trend_engines.values()):
                try:
                    cur = ex.get_price(eng.symbol)
                    if eng.position: eng._sell(cur, "回撤保护")
                except: pass
            time.sleep(30)
            continue

        # === 日亏保护 ===
        if not sm.check_daily_loss(balance):
            time.sleep(30)
            continue

        # === 市场扫描（每3分钟）===
        if now - last_scan >= SCAN_INTERVAL:
            last_scan = now
            sm.check_take_profit(balance)
            sm.save()

            signals = {}
            for sym in COINS:
                try:
                    mode, info = detect_market_mode(sym, ex)
                    signals[sym] = info
                    me = mode_emoji.get(mode, "⚪")
                    se = sig_emoji.get("HOLD", "⚪")
                    if mode in ("VOLATILE_OVERBOUGHT", "CRISIS"): se = sig_emoji["SELL"]
                    elif mode in ("TREND_UP", "VOLATILE_OVERSOLD"): se = sig_emoji["BUY"]
                    conf = info.get('confidence', 0)
                    adx  = info.get('adx', 0)
                    fg   = info.get('fear_greed', 50)
                    div  = '📈' if info.get('rsi_bullish_div') else ''
                    conf_str = f"{conf:.0%}"
                    fg_str = f"FG{fg:3.0f}"
                    log(f"  {me}{se} {sym:10s} ${info.get('price',0):12.4f} | RSI={info.get('rsi',0):5.1f} ADX={adx:4.0f} | {fg_str} | {conf_str} | {mode:20s} | G={info.get('grids',0):1.0f} {div}")
                except Exception as e:
                    log(f"[扫描异常] {sym}: {e} ({type(e).__name__})")
                    signals[sym] = {'price': 0, 'rsi': 50, 'mode': 'RANGE_BOUND', 'grids': 0, 'trend_bias': 0, 'confidence': 0, 'adx': 0, 'fear_greed': 50, 'vol_ratio': 1}

            # 更新全局市场模式
            btc_mode = signals.get('BTCUSDT', {}).get('mode', 'RANGE_BOUND')
            sm.market_mode = btc_mode

            # === 清理死引擎（所有格都平了）=== Bug #2 fix
            for sym in list(grid_engines.keys()):
                if grid_engines[sym]._all_sold:
                    log(f"[引擎清理] {sym} 所有格已平，移除引擎")
                    del grid_engines[sym]
            for sym in list(trend_engines.keys()):
                if trend_engines[sym].position is None:
                    del trend_engines[sym]

            # === 极端时段过滤（北京时间22:00-02:00禁止开仓）===
            if is_extreme_hour():
                if buy_list:
                    log(f"[⏰极端时段] 北京时间22:00-02:00，暂停开仓，等待流动性恢复")
                buy_list = []

            buy_list = sorted(
                [(s, i) for s, i in signals.items()
                 if (s not in grid_engines or grid_engines.get(s, {})._all_sold)
                 and (s not in trend_engines or trend_engines.get(s, {}).position is None)
                 and i.get('confidence', 0) >= 0.6
                 and (i['mode'] in ("TREND_UP", "TREND_UP_RECALL", "VOLATILE_OVERSOLD")
                      or (i['mode'] == "RANGE_BOUND" and i.get('total_score', 0) > 0.5))
                 and i.get('price', 0) > 0],
                key=lambda x: x[1].get('total_score', 0),
                reverse=True
            )

            def calc_position_size(bal, active, info):
                tier = TIER4 if bal > 1000 else TIER3 if bal > 200 else TIER2 if bal > 50 else TIER1
                return min(tier * info.get('pos_pct', 1.0), bal * 0.35)

            for sym, info in buy_list:
                if active_total >= MAX_POSITIONS: break
                if investable < 15: break

                per_coin = calc_position_size(investable, max(active_total, 1), info)
                if info['trend_bias'] >= 0.7:
                    eng = TrendEngine(sym, ex, sm=sm)
                    if eng.buy(info['price'], per_coin / info['price']):
                        trend_engines[sym] = eng
                        investable -= per_coin
                        active_total += 1
                        log(f"[趋势开仓] {sym}@{info['price']:.2f} 模式:{info['mode']}")
                elif info['grids'] > 0:
                    phase1 = get_phase1_grids(balance)
                    eng = GridEngine(sym, info['price'], info['grids'],
                                    info['grid_profit'], info.get('atr', 0), ex, per_coin,
                                    phase1_limit=phase1, sm=sm)
                    grid_engines[sym] = eng
                    investable -= per_coin
                    active_total += 1
                    log(f"[网格开仓] {sym}@{info['price']:.2f} {info['grids']}格 模式:{info['mode']}")

            # === SELL信号平仓 ===
            for sym, info in signals.items():
                if info['mode'] in ("VOLATILE_OVERBOUGHT", "CRISIS", "TREND_DOWN"):
                    if sym in grid_engines:
                        try:
                            cur = info['price']
                            for idx in list(grid_engines[sym].positions.keys()):
                                grid_engines[sym]._sell_grid(idx, cur, f"市场信号-{info['mode']}")
                            sm.record_loss()
                        except: pass
                    if sym in trend_engines and trend_engines[sym].position:
                        try:
                            cur = info['price']
                            trend_engines[sym]._sell(cur, f"市场信号-{info['mode']}")
                            sm.record_loss()
                        except: pass

            # === 手动平仓检测（每5分钟）===
            if now - last_manual_check >= 300:
                last_manual_check = now
                for sym, eng in list(grid_engines.items()):
                    try:
                        api_qty = ex.get_spot_holdings(sym)
                        if api_qty <= 0 and eng.has_position():
                            eng.detect_manual_close(api_qty)
                    except: pass

            # === 状态汇报 ===
            active_g = len([e for e in grid_engines.values() if e.has_position()])
            active_t = len([e for e in trend_engines.values() if e.position])
            total_inv = sum(e.capital for e in grid_engines.values())
            log(f"[{len(COINS)}] 网格{active_g}格 | 趋势{active_t}仓 | "
                f"总投入${total_inv:.2f} | 盈亏${balance-total_inv:.2f} | "
                f"余额${balance:.2f} | 提取${sm.total_profit_taken:.2f} | 连亏{sm.loss_streak}次")

        # === 实时检查（每20秒）===
        for sym, eng in list(grid_engines.items()):
            try:
                cur = ex.get_price(sym)
                eng.check(cur)
                eng.check_phased_open(cur)
            except: pass
        for sym, eng in list(trend_engines.items()):
            try:
                cur = ex.get_price(sym)
                eng.check(cur)
                if eng.position is None:
                    del trend_engines[sym]
            except: pass

        # === 状态保存 ===
        if now - last_save >= SAVE_INTERVAL:
            last_save = now
            sm.save()

        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
