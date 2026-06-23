#!/usr/bin/env python3
"""币安现货交易机器人 v1.0
多币种趋势跟随策略：BTC, ETH, BNB, SOL, SUI 等
"""
import requests, time, json, yaml
from datetime import datetime
from spot_adapter import BinanceSpotAdapter

# ===================== 配置 =====================
CONFIG_FILE = "/root/.openclaw/workspace/spot_config.yaml"

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

config = load_config()
LOG_FILE = config.get('log_file', '/root/.openclaw/workspace/spot_bot.log')
STATE_DIR = config.get('state_dir', '/root/.openclaw/workspace/')

# 全局参数
COINS = config.get('coins', ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'SUIUSDT'])
MAX_HOLD = config.get('max_hold', 3)  # 最多同时持有几个币
ALLOC_PCT = config.get('alloc_pct', 0.15)  # 每个币分配_USDT的_15%
MIN_INVEST = config.get('min_invest', 11)  # 最小投资金额（USDT）
SL_PCT = config.get('sl_pct', 0.05)  # 止损5%
TP_PCT = config.get('tp_pct', 0.08)  # 止盈8%
RSI_PERIOD = config.get('rsi_period', 14)
MACD_FAST = config.get('macd_fast', 12)
MACD_SLOW = config.get('macd_slow', 26)
MACD_SIGNAL = config.get('macd_signal', 9)
BB_PERIOD = config.get('bb_period', 20)
BB_MULT = config.get('bb_mult', 2.0)
SIGNAL_INTERVAL = config.get('signal_interval', 60)  # 信号检查间隔（秒）
STATE_FILE = STATE_DIR + "spot_holdings.json"

# ===================== 工具函数 =====================
def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

# ===================== 技术指标 =====================
def calc_rsi(prices, period=14):
    if len(prices) < period+1:
        return 50
    gains = [max(0, prices[i]-prices[i-1]) for i in range(1,len(prices))]
    losses = [max(0, prices[i-1]-prices[i]) for i in range(1,len(prices))]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0:
        return 100
    return 100 - 100/(1 + avg_gain/avg_loss)

def calc_ema(prices, n):
    if len(prices) < n:
        return None
    k = 2/(n+1)
    ema = sum(prices[:n])/n
    for p in prices[n:]:
        ema = p*k + ema*(1-k)
    return ema

def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow+1:
        return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return 0, 0, 0
    macd = ema_fast - ema_slow
    # Signal line approximation
    macd_hist = [macd]
    for _ in range(signal-1):
        macd_hist.append(macd_hist[-1] * 0.9)  # 简化
    signal_line = sum(macd_hist[-signal:])/signal if len(macd_hist) >= signal else macd
    hist = macd - signal_line
    return macd, signal_line, hist

def calc_bollinger(prices, period=20, mult=2):
    if len(prices) < period:
        return None, None, None
    sma = sum(prices[-period:]) / period
    std = (sum((p - sma) ** 2 for p in prices[-period:]) / period) ** 0.5
    upper = sma + mult * std
    lower = sma - mult * std
    return upper, sma, lower

# ===================== 信号计算 =====================
def get_signal(ex, symbol):
    """
    返回信号: 'BUY', 'SELL', None
    策略：
    - RSI < 30 超卖 + MACD 金叉 → BUY
    - RSI > 70 超买 + MACD 死叉 → SELL
    - BB下轨支撑 + MACD 多头 → BUY
    - BB上轨压力 + MACD 空头 → SELL
    """
    klines = ex.get_klines(symbol, "1h", 100)
    if len(klines) < BB_PERIOD + 5:
        return None, {}
    
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    
    cur = closes[-1]
    rsi = calc_rsi(closes, RSI_PERIOD)
    macd, signal_line, hist = calc_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes, BB_PERIOD, BB_MULT)
    
    bb_pos = (cur - bb_lower) / (bb_upper - bb_lower) if bb_upper and bb_lower and bb_upper != bb_lower else 0.5
    bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid else 0
    
    # 成交量确认
    vol_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
    vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 1
    
    # MACD趋势
    macd_bull = hist > 0 and macd > signal_line
    macd_bear = hist < 0 and macd < signal_line
    
    # Signal scoring
    buy_score = 0
    sell_score = 0
    reasons = []
    
    # RSI 超卖/超买
    if rsi < 35:
        buy_score += 2
        reasons.append(f"RSI={rsi:.0f}<35")
    elif rsi < 40:
        buy_score += 1
        reasons.append(f"RSI={rsi:.0f}<40")
    
    if rsi > 65:
        sell_score += 2
        reasons.append(f"RSI={rsi:.0f}>65")
    elif rsi > 60:
        sell_score += 1
        reasons.append(f"RSI={rsi:.0f}>60")
    
    # MACD 金叉/死叉
    if macd_bull:
        buy_score += 2
        reasons.append("MACD多头")
    if macd_bear:
        sell_score += 2
        reasons.append("MACD空头")
    
    # BB 支撑/压力
    if bb_pos < 0.2:
        buy_score += 1.5
        reasons.append(f"BB下轨={bb_pos:.0%}")
    elif bb_pos < 0.3:
        buy_score += 1
        reasons.append(f"BB偏低={bb_pos:.0%}")
    
    if bb_pos > 0.8:
        sell_score += 1.5
        reasons.append(f"BB上轨={bb_pos:.0%}")
    elif bb_pos > 0.7:
        sell_score += 1
        reasons.append(f"BB偏高={bb_pos:.0%}")
    
    # 成交量放大
    if vol_ratio > 1.5:
        buy_score += 1
        reasons.append(f"V={vol_ratio:.1f}x")
    
    # 趋势过滤：ema20 方向
    ema20 = calc_ema(closes, 20)
    ema20_prev = calc_ema(closes[:-1], 20)
    if ema20 and ema20_prev:
        if ema20 > ema20_prev:
            buy_score += 0.5
            reasons.append("EMA20↑")
        else:
            sell_score += 0.5
            reasons.append("EMA20↓")
    
    sig = None
    if buy_score >= 4.0:
        sig = "BUY"
    elif sell_score >= 4.0:
        sig = "SELL"
    
    return sig, {
        'price': cur, 'rsi': rsi, 'macd': macd, 'hist': hist,
        'bb_upper': bb_upper, 'bb_lower': bb_lower, 'bb_pos': bb_pos,
        'vol_ratio': vol_ratio, 'buy_score': buy_score, 'sell_score': sell_score,
        'reasons': reasons, 'ema20': ema20, 'ema20_prev': ema20_prev
    }

# ===================== 现货机器人核心 =====================
class SpotBot:
    def __init__(self, adapter):
        self.ex = adapter
        self.holdings = self._load_holdings()
    
    def _load_holdings(self) -> dict:
        """加载持仓状态"""
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            return {}  # {symbol: {qty, entry, entry_time, sl, tp}}
    
    def _save_holdings(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.holdings, f, indent=2)
    
    def get_holdings_list(self) -> list:
        """当前持仓的币种列表"""
        return [sym for sym, h in self.holdings.items() if h.get('qty', 0) > 0]
    
    def has_position(self, symbol: str) -> bool:
        return symbol in self.holdings and self.holdings[symbol].get('qty', 0) > 0
    
    def buy(self, symbol: str, usdt_amount: float):
        """买入 signal"""
        price = self.ex.get_price(symbol)
        if price <= 0:
            log(f"[买入失败] {symbol} 价格获取失败")
            return False
        
        qty = usdt_amount / price
        # 精度处理
        info = self.ex._get_symbol_info(symbol)
        step_size = 0.001
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break
        qty = float(qty) // step_size * step_size
        
        if qty * price < self.ex.get_min_notional(symbol):
            log(f"[跳过] {symbol} 金额{qty*price:.2f}低于最小{self.ex.get_min_notional(symbol)}")
            return False
        
        log(f"[买入信号] {symbol} qty={qty:.4f} @ ${price:.2f} (投入${usdt_amount:.2f})")
        try:
            ok = self.ex.market_buy(symbol, qty)
            if ok:
                self.holdings[symbol] = {
                    "qty": qty,
                    "entry": price,
                    "entry_time": time.time(),
                    "sl": price * (1 - SL_PCT),
                    "tp": price * (1 + TP_PCT),
                }
                self._save_holdings()
                log(f"[持仓] {symbol} 入场 ${price:.4f} qty={qty:.4f} SL=${price*(1-SL_PCT):.4f} TP=${price*(1+TP_PCT):.4f}")
                return True
            else:
                log(f"[买入失败] {symbol}")
                return False
        except Exception as e:
            log(f"[买入异常] {symbol}: {e}")
            return False
    
    def sell(self, symbol: str, reason: str = ""):
        """卖出"""
        if not self.has_position(symbol):
            return False
        
        qty = self.holdings[symbol]['qty']
        price = self.ex.get_price(symbol)
        
        log(f"[卖出信号] {symbol} qty={qty:.4f} @ ${price:.4f} ({reason})")
        try:
            ok = self.ex.market_sell(symbol, qty)
            if ok:
                entry = self.holdings[symbol]['entry']
                pnl = (price - entry) / entry * 100
                log(f"[平仓] {symbol} {pnl:+.2f}% ({reason})")
                self.holdings[symbol] = {"qty": 0, "sold": True}
                self._save_holdings()
                return True
        except Exception as e:
            log(f"[卖出异常] {symbol}: {e}")
            return False
    
    def check_holdings(self, price_map: dict):
        """检查持仓，检查止损/止盈"""
        for symbol, h in list(self.holdings.items()):
            qty = h.get('qty', 0)
            if qty <= 0:
                continue
            
            cur = price_map.get(symbol, self.ex.get_price(symbol))
            if cur <= 0:
                continue
            
            entry = h['entry']
            sl = h.get('sl', entry * (1 - SL_PCT))
            tp = h.get('tp', entry * (1 + TP_PCT))
            pnl = (cur - entry) / entry * 100
            
            # 止盈
            if cur >= tp:
                self.sell(symbol, f"TP触发 {pnl:+.2f}%")
                continue
            
            # 止损
            if cur <= sl:
                self.sell(symbol, f"SL触发 {pnl:+.2f}%")
                continue
            
            # 跟踪止损：价格下跌8%自动出
            if cur < entry * 0.92:
                self.sell(symbol, f"回撤保护 {pnl:+.2f}%")
                continue
            
            # 趋势走坏：价格跌破EMA20 强平警告
            klines = self.ex.get_klines(symbol, "1h", 25)
            if len(klines) >= 25:
                closes = [float(k[4]) for k in klines]
                ema20 = calc_ema(closes, 20)
                if ema20 and cur < ema20 * 0.97:
                    log(f"[警告] {symbol} 跌破EMA20 ${cur:.4f}<${ema20*0.97:.4f} pnl:{pnl:+.2f}%")
    
    def rebalance(self, signals: dict, usdt_balance: float):
        """
        根据信号调仓
        逻辑：
        1. 信号BUY的币，如果没持仓且有足够余额 → 买入
        2. 有持仓但信号SELL → 卖出
        3. 持仓中但趋势变差(EMA20向下) → 减仓或卖出
        """
        # 计算目标持仓
        current_holdings = self.get_holdings_list()
        
        # 找出要买的
        buy_candidates = []
        for symbol, (sig, info) in signals.items():
            if sig == "BUY" and not self.has_position(symbol) and symbol not in current_holdings:
                buy_candidates.append((symbol, info['buy_score'], info['price']))
        
        # 按分数排序，买最强的
        buy_candidates.sort(key=lambda x: -x[1])
        
        slots = MAX_HOLD - len(current_holdings)
        allocate = min(usdt_balance * ALLOC_PCT, usdt_balance / max(MAX_HOLD, 1))
        
        for symbol, score, price in buy_candidates[:slots]:
            if allocate < MIN_INVEST:
                break
            log(f"[调仓] 买入 {symbol} 评分{score} @ ${price:.2f} 分配${allocate:.2f}")
            self.buy(symbol, allocate)
            time.sleep(1)
        
        # 卖出信号：强SELL
        for symbol, (sig, info) in signals.items():
            if sig == "SELL" and self.has_position(symbol):
                log(f"[调仓] 卖出 {symbol} ({info['reasons']})")
                self.sell(symbol, f"SELL信号 {info.get('reasons', [])}")
                time.sleep(1)

# ===================== 主循环 =====================
def main():
    cfg = load_config()
    
    # 创建现货adapter
    ex = BinanceSpotAdapter(
        api_key=cfg['api_key'],
        secret=cfg['secret']
    )
    
    bot = SpotBot(ex)
    
    log(f"=" * 60)
    log(f"现货Bot启动 | 监控: {COINS} | 最多持有:{MAX_HOLD}个 | 每币分配:{ALLOC_PCT*100}%")
    log(f"=" * 60)
    
    # 启动检查
    try:
        balance = ex.get_balance("USDT")
        log(f"USDT余额: ${balance:.2f}")
    except Exception as e:
        log(f"余额获取失败: {e}")
        balance = 0
    
    last_signal_time = 0
    
    while True:
        try:
            now = time.time()
            price_map = {}
            
            # 获取所有币当前价格
            for sym in COINS:
                try:
                    price_map[sym] = ex.get_price(sym)
                except:
                    pass
            
            # 检查持仓（止损/止盈/跟踪）
            bot.check_holdings(price_map)
            
            # 每60秒检查一次信号
            if now - last_signal_time >= SIGNAL_INTERVAL:
                last_signal_time = now
                signals = {}
                
                for sym in COINS:
                    try:
                        sig, info = get_signal(ex, sym)
                        signals[sym] = (sig, info)
                        if sig:
                            log(f"[信号] {sym} {sig} {info.get('reasons', [])} "
                                f"Rsi={info['rsi']:.0f} MACD={info['macd']:.2f} BB={info['bb_pos']:.0%} "
                                f"Bscore={info['buy_score']:.1f} Sscore={info['sell_score']:.1f}")
                        else:
                            log(f"[观察] {sym} ${info['price']:.2f} "
                                f"Rsi={info['rsi']:.0f} BB={info['bb_pos']:.0%} "
                                f"Bscore={info['buy_score']:.1f} Sscore={info['sell_score']:.1f}")
                    except Exception as e:
                        log(f"[信号错误] {sym}: {e}")
                
                # 调仓
                try:
                    balance = ex.get_balance("USDT")
                except:
                    balance = 0
                
                if balance >= MIN_INVEST:
                    bot.rebalance(signals, balance)
                else:
                    log(f"余额${balance:.2f}低于最小${MIN_INVEST}，跳过买入")
            
            time.sleep(15)
            
        except KeyboardInterrupt:
            log("STOPPED")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(15)

if __name__ == "__main__":
    main()
