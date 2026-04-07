"""
混沌龙虾信号系统 v2.0
===================
核心改进：
- 自适应趋势/震荡判断（regime detection）
- 盘口结构分析（order flow imbalance）
- OI变化率追踪
- 资金费率拐点检测
- 量能潮汐分析
- 多时间共振确认
- 动态止损（基于结构+ATR混合）
"""

import requests
import numpy as np
import json
import time
from datetime import datetime, timezone, timedelta
from collections import deque

OKX_BASE = "https://www.okx.com/api/v5"
CACHE = {}  # 简单缓存，避免重复请求

def api_get(endpoint, params=None, cache_ttl=5):
    key = (endpoint, tuple(sorted((params or {}).items())))
    if key in CACHE and time.time() - CACHE[key][1] < cache_ttl:
        return CACHE[key][0]
    r = requests.get(f"{OKX_BASE}{endpoint}", params=params, timeout=10)
    d = r.json()
    CACHE[key] = (d, time.time())
    return d

# ---------- 数据获取 ----------
def get_ticker(inst):
    d = api_get("/market/ticker", {"instId": inst})["data"][0]
    return {
        "last": float(d["last"]),
        "mark": float(d.get("markPx", d["last"])),
        "bid": float(d["bidPx"]),
        "ask": float(d["askPx"]),
        "bid_sz": float(d["bidSz"]),
        "ask_sz": float(d["askSz"]),
        "high24h": float(d["high24h"]),
        "low24h": float(d["low24h"]),
        "vol24h": float(d["vol24h"]),
        "open24h": float(d["open24h"]),
        "sodUtc0": float(d["sodUtc0"]),
        "sodUtc8": float(d.get("sodUtc8", d["sodUtc0"])),
    }

def get_candles(inst, bar="1H", limit=100):
    d = api_get("/market/candles", {"instId": inst, "bar": bar, "limit": limit}, cache_ttl=10)
    if d["code"] != "0":
        return []
    # 返回 [ts, open, high, low, close, vol]
    return [[float(x) for x in b[:6]] for b in d["data"]]

def get_funding(inst):
    d = api_get("/public/funding-rate", {"instId": inst}, cache_ttl=30)
    if d["code"] != "0":
        return 0.0, 0.0, 0.0
    fd = d["data"][0]
    def safe_float(val, default=0.0):
        try:
            return float(val) if val and val != '' else default
        except:
            return default
    return safe_float(fd["fundingRate"]), safe_float(fd.get("prevFundingRate", 0)), safe_float(fd.get("nextFundingRate", 0))

def get_oi(inst):
    d = api_get("/public/open-interest", {"instId": inst}, cache_ttl=10)
    if d["code"] != "0":
        return 0.0, 0.0
    oi = float(d["data"][0]["oiUsd"])
    return oi, CACHE.get(("open-interest", inst), (0, 0))[0]

def get_books(inst, sz=20):
    d = api_get("/market/books", {"instId": inst, "sz": str(sz)}, cache_ttl=3)
    if d["code"] != "0":
        return None
    asks = [[float(p), float(s)] for p, s, *_ in d["data"][0]["asks"][:10]]
    bids = [[float(p), float(s)] for p, s, *_ in d["data"][0]["bids"][:10]]
    return asks, bids

# ---------- 指标计算 ----------
def ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema_val = closes[0]
    for c in closes[1:]:
        ema_val = c * k + ema_val * (1 - k)
    return ema_val

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, c_prev = candles[i][2], candles[i][3], candles[i-1][4]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return np.mean(trs[-period:])

def adx(closes, highs, lows, period=14):
    """简化ADX：衡量趋势强度"""
    if len(closes) < period * 2:
        return None
    # 先算 +DI 和 -DI
    plus_dm, minus_dm = [], []
    for i in range(1, len(closes)):
        h_curr, h_prev = highs[i], highs[i-1]
        l_curr, l_prev = lows[i], lows[i-1]
        dm_plus = max(h_curr - h_prev, 0) if (h_curr - h_prev) > (l_prev - l_curr) else 0
        dm_minus = max(l_prev - l_curr, 0) if (l_prev - l_curr) > (h_curr - h_prev) else 0
        plus_dm.append(dm_plus)
        minus_dm.append(dm_minus)
    if len(plus_dm) < period:
        return None
    # ATR
    trs = []
    for i in range(1, len(closes)):
        h, l, c_prev = highs[i], lows[i], closes[i-1]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    atr_val = np.mean(trs[-period:])
    if atr_val == 0:
        return 0
    di_plus = np.mean(plus_dm[-period:]) / atr_val * 100
    di_minus = np.mean(minus_dm[-period:]) / atr_val * 100
    dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100
    return dx  # 简化版，返回DX而非完整ADX

def volume_profile(candles, period=20):
    """量能分析：当前量能在近期属于什么水平"""
    if len(candles) < period:
        return 1.0, 1.0
    vols = np.array([c[5] for c in candles[-period:]])
    current = vols[-1]
    avg = np.mean(vols[:-1])
    std = np.std(vols[:-1])
    z_score = (current - avg) / std if std > 0 else 0
    ratio = current / avg if avg > 0 else 1.0
    return ratio, z_score

# ---------- 核心信号生成 ----------
def regime(candles):
    """判断当前是趋势还是震荡"""
    if len(candles) < 30:
        return "unknown"
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    dx = adx(closes, highs, lows, 14)
    if dx is None:
        return "unknown"
    # 简化：DX > 25 表示趋势市场，< 20 表示震荡
    if dx > 25:
        return "trend"
    elif dx < 20:
        return "range"
    return "transition"

def book_imbalance(books):
    """盘口多空力量对比"""
    if books is None:
        return 0
    asks, bids = books
    bid_total_vol = sum(s for _, s in bids)
    ask_total_vol = sum(s for _, s in asks)
    total = bid_total_vol + ask_total_vol
    if total == 0:
        return 0
    return (bid_total_vol - ask_total_vol) / total * 100  # 正=买盘强

def funding_momentum(inst):
    """资金费率动量：当前vs上一个vs预测"""
    funding, prev_funding, next_funding = get_funding(inst)
    if prev_funding == 0:
        return 0
    # 动量：当前相比上一个的变化
    mom = (funding - prev_funding) * 10000  # 转换为bp
    return mom

def oi_change_rate(inst):
    """OI变化率（简化版，需要连续两次查询对比）"""
    current_oi, prev_oi = get_oi(inst)
    if prev_oi == 0:
        return 0
    return (current_oi - prev_oi) / prev_oi * 100

# ---------- 信号生成 ----------
def generate_signal_v2(inst_id, name):
    ticker = get_ticker(inst_id)
    try:
        funding, prev_funding, next_funding = get_funding(inst_id)
    except:
        funding, prev_funding, next_funding = 0.0, 0.0, 0.0
    current_oi, prev_oi = get_oi(inst_id)
    books = get_books(inst_id)

    c1h = get_candles(inst_id, "1H", 80)
    c4h = get_candles(inst_id, "4H", 60)
    c1d = get_candles(inst_id, "1D", 30)

    if not c1h or not c4h:
        return None

    last = ticker["last"]
    mark = ticker["mark"]
    spread = ticker["ask"] - ticker["bid"]

    opens_1h = [c[1] for c in c1h]
    closes_1h = [c[4] for c in c1h]
    highs_1h = [c[2] for c in c1h]
    lows_1h = [c[3] for c in c1h]
    closes_4h = [c[4] for c in c4h]
    highs_4h = [c[2] for c in c4h]
    lows_4h = [c[3] for c in c4h]
    closes_1d = [c[4] for c in c1d]

    # 基础指标
    rsi_14 = rsi(closes_1h, 14)
    rsi_4h = rsi(closes_4h, 14)
    ema7 = ema(closes_1h, 7)
    ema21 = ema(closes_1h, 21)
    ema50 = ema(closes_1h, 50)
    ema200_4h = ema(closes_4h, 200)
    atr14 = atr(c1h, 14)
    vol_ratio, vol_z = volume_profile(c1h, 20)

    # 趋势判断
    above_ema7 = last > ema7 if ema7 else False
    above_ema21 = last > ema21 if ema21 else False
    above_ema50 = last > ema50 if ema50 else False
    ema7_above_21 = ema7 > ema21 if (ema7 and ema21) else False
    h4_above_ema200 = closes_4h[-1] > ema200_4h if ema200_4h else True

    # K线结构
    c1h_bullish_engulfing = (
        closes_1h[-1] > opens_1h[-1] and
        closes_1h[-2] < opens_1h[-2] and
        closes_1h[-1] > opens_1h[-2] and
        closes_1h[-2] < opens_1h[-1]
    ) if len(closes_1h) >= 2 else False

    # 动态支撑阻力
    highs_20 = highs_1h[-20:]
    lows_20 = lows_1h[-20:]
    resistance = max(highs_20)
    support = min(lows_20)
    for i in range(len(highs_1h)-1, len(highs_1h)-5, -1):
        if highs_1h[i] == max(highs_1h[max(0,i-5):i+1]):
            resistance = highs_1h[i]
        if lows_1h[i] == min(lows_1h[max(0,i-5):i+1]):
            support = lows_1h[i]

    recent_high = max(closes_1h[-10:])
    recent_low = min(closes_1h[-10:])
    range_20 = max(highs_1h[-20:]) - min(lows_1h[-20:])
    range_ratio = range_20 / last  # 波动率相对值

    # 市场状态
    current_regime = regime(c1h)
    book_bal = book_imbalance(books)
    funding_mom = funding_momentum(inst_id)
    oi_change = oi_change_rate(inst_id)
    funding_pct = funding * 100

    # ======================
    # 信号评分
    # ======================
    long_score = 0
    short_score = 0
    signals_long = []
    signals_short = []

    # === 趋势市场策略 ===
    if current_regime == "trend":
        if above_ema7 and above_ema21 and above_ema50 and ema7_above_21:
            long_score += 25
            signals_long.append("多头排列(EMA7>21>50)")
        if not above_ema7 and not above_ema21 and not above_ema50:
            short_score += 25
            signals_short.append("空头排列")
        if above_ema7 and not above_ema21:
            short_score += 20
            signals_short.append("EMA7下穿EMA21死叉")
        if not above_ema7 and above_ema21:
            long_score += 20
            signals_long.append("EMA7上穿EMA21金叉")

        # ADX趋势强度
        if rsi_14 and rsi_14 > 55:
            long_score += 10
            signals_long.append("RSI偏多确认趋势")
        if rsi_14 and rsi_14 < 45:
            short_score += 10
            signals_short.append("RSI偏空确认趋势")

        # OI与价格背离
        if oi_change > 5:
            if above_ema21:
                long_score += 10
                signals_long.append("OI增长+多头趋势")
            else:
                short_score += 10
                signals_short.append("OI增长+空头趋势")
        elif oi_change < -5:
            if not above_ema21:
                short_score += 10
                signals_short.append("OI收缩+空头")
            else:
                long_score += 10
                signals_long.append("OI收缩+多头")

    # === 震荡市场策略 ===
    elif current_regime == "range":
        # 震荡行情：高RSI做空，低RSI做多
        if rsi_14 and rsi_14 > 70:
            short_score += 30
            signals_short.append("震荡超买→卖")
        if rsi_14 and rsi_14 < 30:
            long_score += 30
            signals_long.append("震荡超卖→买")
        if rsi_14 and rsi_14 > 60:
            short_score += 15
            signals_short.append("RSI偏高+高空")
        if rsi_14 and rsi_14 < 40:
            long_score += 15
            signals_long.append("RSI偏低+低多")

        # 盘口失衡
        if book_bal > 20:
            long_score += 15
            signals_long.append(f"买盘堆积(+{book_bal:.0f}%)")
        if book_bal < -20:
            short_score += 15
            signals_short.append(f"卖盘堆积({book_bal:.0f}%)")

        # 区间支撑阻力
        if last < support + (resistance - support) * 0.1:
            long_score += 15
            signals_long.append("贴近支撑→短多")
        if last > resistance - (resistance - support) * 0.1:
            short_score += 15
            signals_short.append("贴近阻力→短空")

        # 地量见底
        if vol_ratio < 0.6:
            if rsi_14 and rsi_14 < 45:
                long_score += 10
                signals_long.append("地量+RSI支撑")
        if vol_ratio > 1.8:
            if rsi_14 and rsi_14 > 55:
                short_score += 10
                signals_short.append("放量+RSI压力")

    # === 通用信号（两种市场都适用）===
    # RSI极端
    if rsi_14 and rsi_14 > 80:
        short_score += 20
        signals_short.append("RSI严重超买")
    if rsi_14 and rsi_14 < 20:
        long_score += 20
        signals_long.append("RSI严重超卖")

    # 资金费率动量
    if funding_mom > 2:  # 资金费率快速上升
        short_score += 15
        signals_short.append("资金费率急剧上升→空头拥挤")
    if funding_mom < -2:
        long_score += 15
        signals_long.append("资金费率急剧下降→空头平仓")

    # 盘口spread异常
    if spread / last > 0.001:  # spread > 0.1%
        # 市场紧张
        if above_ema21:
            short_score += 10
            signals_short.append("高波动+偏空")
        else:
            long_score += 10
            signals_long.append("高波动+偏多")

    # 4H共振
    if rsi_4h and rsi_4h > 65:
        short_score += 10
        signals_short.append("4H RSI超买共振")
    if rsi_4h and rsi_4h < 35:
        long_score += 10
        signals_long.append("4H RSI超卖共振")

    # === 多时间框架冲突检测 ===
    # 1H超卖 + 4H超买 → 反弹可能失败，惩罚LONG
    if rsi_14 and rsi_14 < 40 and rsi_4h and rsi_4h > 75:
        long_score -= 25
        signals_long.append("⚠️ 1H超卖+4H严重超买→反弹受限")
    # 1H超买 + 4H超卖 → 回撤可能失败，惩罚SHORT
    if rsi_14 and rsi_14 > 60 and rsi_4h and rsi_4h < 35:
        short_score -= 25
        signals_short.append("⚠️ 1H超买+4H严重超卖→回调受限")

    # 波动率过滤
    if range_ratio < 0.003:  # 波动率异常低
        if abs(long_score - short_score) < 15:
            return {
                "inst_id": inst_id, "name": name,
                "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
                "last": last, "mark": mark,
                "basis": round((mark - ticker["sodUtc0"]) / ticker["sodUtc0"] * 100, 4),
                "funding_pct": round(funding_pct, 4),
                "oi_usd": round(current_oi / 1e8, 2),
                "rsi_14": round(rsi_14, 1),
                "regime": current_regime,
                "direction": "WAIT", "confidence": 0,
                "entry": last, "stop": None, "tp1": None, "tp2": None,
                "signals": [f"波动率极低({range_ratio:.3f})，等待突破"],
                "long_score": long_score, "short_score": short_score,
            }

    # ======================
    # 决策
    # ======================
    direction = "WAIT"
    confidence = 0
    entry = last
    stop, tp1, tp2 = None, None, None

    if long_score > short_score and long_score >= 35:
        direction = "LONG"
        confidence = min(long_score, 92)
        # 动态止损：结合ATR和结构
        sl = last - (atr14 * 1.5 if atr14 else last * 0.008)
        tp1 = last + (atr14 * 2.5 if atr14 else last * 0.012)
        tp2 = last + (atr14 * 4 if atr14 else last * 0.02)
        stop = round(sl, 2)
    elif short_score > long_score and short_score >= 35:
        direction = "SHORT"
        confidence = min(short_score, 92)
        sl = last + (atr14 * 1.5 if atr14 else last * 0.008)
        tp1 = last - (atr14 * 2.5 if atr14 else last * 0.012)
        tp2 = last - (atr14 * 4 if atr14 else last * 0.02)
        stop = round(sl, 2)
    elif abs(long_score - short_score) <= 10 and max(long_score, short_score) >= 25:
        # 中性震荡行情
        if last < support + (resistance - support) * 0.35:
            direction = "LONG"
            confidence = max(25, min(35, 40 - (last - support) / (resistance - support) * 20))
            sl = support - (atr14 * 0.5 if atr14 else last * 0.003)
            tp1 = resistance - (resistance - support) * 0.3
            tp2 = resistance
            stop = round(sl, 2)
        elif last > resistance - (resistance - support) * 0.35:
            direction = "SHORT"
            confidence = max(25, min(35, 40 - (resistance - last) / (resistance - support) * 20))
            sl = resistance + (atr14 * 0.5 if atr14 else last * 0.003)
            tp1 = support + (resistance - support) * 0.3
            tp2 = support
            stop = round(sl, 2)

    return {
        "inst_id": inst_id,
        "name": name,
        "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "last": round(last, 2),
        "mark": round(mark, 2),
        "bid": ticker["bid"],
        "ask": ticker["ask"],
        "spread": round(spread, 4),
        "basis": round((mark - ticker["sodUtc0"]) / ticker["sodUtc0"] * 100, 4),
        "funding_pct": round(funding_pct, 4),
        "funding_mom": round(funding_mom, 2),
        "oi_usd": round(current_oi / 1e8, 2),
        "oi_change": round(oi_change, 2),
        "rsi_14": round(rsi_14, 1) if rsi_14 else None,
        "rsi_4h": round(rsi_4h, 1) if rsi_4h else None,
        "ema7": round(ema7, 2) if ema7 else None,
        "ema21": round(ema21, 2) if ema21 else None,
        "atr": round(atr14, 2) if atr14 else None,
        "vol_ratio": round(vol_ratio, 2),
        "book_bal": round(book_bal, 1) if book_bal else 0,
        "regime": current_regime,
        "range_ratio": round(range_ratio * 100, 2),
        "resistance": round(resistance, 2),
        "support": round(support, 2),
        "direction": direction,
        "confidence": confidence,
        "entry": round(entry, 2),
        "stop": round(stop, 2) if stop and not isinstance(stop, str) else None,
        "tp1": round(tp1, 2) if direction != "WAIT" else None,
        "tp2": round(tp2, 2) if direction != "WAIT" else None,
        "signals_long": signals_long,
        "signals_short": signals_short,
        "signals": signals_long + signals_short,
        "long_score": long_score,
        "short_score": short_score,
    }

def format_signal_v2(sig):
    if sig is None:
        return "数据获取失败"
    if sig["confidence"] == 0:
        dir_icon = "⚪"
        return (
            f"{dir_icon} [{sig['name']}] **WAIT** | {sig['timestamp']}\n"
            f"现价: **{sig['last']}** | 标记: {sig['mark']} | 溢价: {sig['basis']:+.3f}%\n"
            f"市场状态: {sig['regime']} | RSI: {sig['rsi_14']} | ATR: {sig['atr']} | 量比: {sig['vol_ratio']}\n"
            f"OI: {sig['oi_usd']}亿({sig['oi_change']:+.1f}%) | 资金费率: {sig['funding_pct']:+.4f}%({sig['funding_mom']:+.1f}bp动量)\n"
            f"盘口失衡: {sig['book_bal']:+.0f}% | 波动率: {sig['range_ratio']:.2f}%\n"
            f"支撑: {sig['support']} | 阻力: {sig['resistance']}\n"
            f"→ {sig['signals'][0]}"
        )
    dir_icon = "🟢" if sig["direction"] == "LONG" else "🔴"
    conf_bar = "█" * int(sig["confidence"] / 10) + "░" * (10 - int(sig["confidence"] / 10))
    sig_list = sig["signals_long"] if sig["direction"] == "LONG" else sig["signals_short"]
    sl_pct = tp1_pct = ""
    if sig.get("stop") and sig.get("entry"):
        diff = abs(sig["entry"] - sig["stop"])
        sl_pct = f"({diff/sig['entry']*100:+.2f}%)"
    if sig.get("tp1") and sig.get("entry"):
        diff = abs(sig["tp1"] - sig["entry"])
        tp1_pct = f"({diff/sig['entry']*100:+.2f}%)"
    lines = [
        f"{dir_icon} **{sig['direction']}** | 置信 {sig['confidence']}% | `{conf_bar}`",
        f"[{sig['name']}] {sig['timestamp']} | 市场:{sig['regime']}",
        f"价格: **{sig['last']}** | 标记: {sig['mark']} | 溢价: {sig['basis']:+.3f}%",
        f"资金费率: {sig['funding_pct']:+.4f}% | 费率动量: {sig['funding_mom']:+.1f}bp",
        f"OI: {sig['oi_usd']}亿({sig['oi_change']:+.1f}%) | RSI: {sig['rsi_14']}(4H:{sig['rsi_4h']})",
        f"ATR: {sig['atr']} | 量比: {sig['vol_ratio']} | 盘口失衡: {sig['book_bal']:+.0f}%",
        f"波动率: {sig['range_ratio']:.2f}% | 支撑: {sig['support']} | 阻力: {sig['resistance']}",
        f"─── 信号逻辑 ───",
    ]
    for r in sig_list:
        lines.append(f"  • {r}")
    if sig["confidence"] > 0:
        lines.extend([
            f"─── 交易计划 ───",
            f"入场: **{sig['entry']}**",
        ])
        if sig.get("stop"):
            lines.append(f"止损: {sig['stop']} {sl_pct}")
        if sig.get("tp1"):
            lines.append(f"目标1: {sig['tp1']} {tp1_pct}")
        if sig.get("tp2"):
            tp2_diff = abs(sig['tp2'] - sig['entry'])
            lines.append(f"目标2: {sig['tp2']} ({tp2_diff/sig['entry']*100:+.2f}%)")
    return "\n".join(lines)

if __name__ == "__main__":
    print("=== 混沌龙虾信号系统 v2 ===")
    results = []
    for inst, name in [("BTC-USDT-SWAP","BTC"), ("ETH-USDT-SWAP","ETH")]:
        try:
            sig = generate_signal_v2(inst, name)
            results.append(sig)
            print(format_signal_v2(sig))
            print()
        except Exception as e:
            import traceback; traceback.print_exc()
    # 保存
    with open("/root/.openclaw/workspace/signals/logs/latest_v2.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
