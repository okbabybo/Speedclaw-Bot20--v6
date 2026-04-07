"""
混沌龙虾信号系统 v1.0
信号引擎：多指标共振 + 量化评分
"""

import json
import time
import requests
import numpy as np
from datetime import datetime, timezone, timedelta

# ========== OKX API ==========
OKX_BASE = "https://www.okx.com/api/v5"

def api_get(endpoint, params=None):
    r = requests.get(f"{OKX_BASE}{endpoint}", params=params, timeout=10)
    return r.json()

def get_ticker(inst):
    d = api_get("/market/ticker", {"instId": inst})["data"][0]
    return {
        "last": float(d["last"]),
        "mark": float(d.get("markPx", d["last"])),
        "bid": float(d["bidPx"]),
        "ask": float(d["askPx"]),
        "high24h": float(d["high24h"]),
        "low24h": float(d["low24h"]),
        "vol24h": float(d["vol24h"]),
        "open24h": float(d["open24h"]),
        "sodUtc0": float(d["sodUtc0"]),
    }

def get_candles(inst, bar="1H", limit=100):
    d = api_get("/market/candles", {"instId": inst, "bar": bar, "limit": limit})
    if d["code"] != "0":
        return []
    # 返回列表: [ts, open, high, low, close, vol, ...]
    return [[float(x) for x in b[:6]] for b in d["data"]]

def get_funding(inst):
    d = api_get("/public/funding-rate", {"instId": inst})
    if d["code"] != "0":
        return 0.0
    return float(d["data"][0]["fundingRate"])

def get_oi(inst):
    d = api_get("/public/open-interest", {"instId": inst})
    if d["code"] != "0":
        return 0.0
    return float(d["data"][0]["oiUsd"])

# ========== 技术指标 ==========
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow:
        return None, None, None
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow
    # signal line: build macd series skipping warmup
    macd_series = []
    for i in range(slow, len(closes)):
        ef = ema(closes[:i+1], fast)
        es = ema(closes[:i+1], slow)
        if ef is not None and es is not None:
            macd_series.append(ef - es)
    if len(macd_series) < signal:
        return macd_line, None, macd_line
    sig = ema(macd_series, signal)
    hist = macd_line - sig if sig else macd_line
    return macd_line, sig, hist

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, c_prev = candles[i][2], candles[i][3], candles[i-1][4]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return np.mean(trs[-period:])

def volume_ratio(candles, period=20):
    if len(candles) < period:
        return 1.0
    vols = [c[5] for c in candles[-period:]]
    return vols[-1] / np.mean(vols[:-1])

# ========== 信号生成 ==========
def generate_signal(inst_id, name):
    ticker = get_ticker(inst_id)
    funding = get_funding(inst_id)
    oi_usd = get_oi(inst_id)

    c1h = get_candles(inst_id, "1H", 60)
    c4h = get_candles(inst_id, "4H", 60)
    c1d = get_candles(inst_id, "1D", 30)

    if not c1h or not c4h:
        return None

    closes_1h = [c[4] for c in c1h]
    closes_4h = [c[4] for c in c4h]
    closes_1d = [c[4] for c in c1d]

    # 指标计算
    rsi_14 = rsi(closes_1h, 14)
    rsi_4h = rsi(closes_4h, 14) if len(closes_4h) >= 15 else None
    ema7 = ema(closes_1h, 7)
    ema21 = ema(closes_1h, 21)
    ema200_4h = ema(closes_4h, 200) if len(closes_4h) >= 200 else None
    atr_14 = atr(c1h, 14)
    vol_r = volume_ratio(c1h, 20)
    macd_line, macd_sig, macd_hist = macd(closes_1h)

    last = ticker["last"]
    mark = ticker["mark"]
    funding_pct = funding * 100
    oi_change_pct = 0  # 简化

    score = 0
    signals = []
    direction = None
    entry = last
    stop = None
    tp1 = None
    tp2 = None

    # ---- 趋势判断 ----
    above_ema7 = last > ema7 if ema7 else False
    above_ema21 = last > ema21 if ema21 else False
    ema7_above_ema21 = ema7 > ema21 if (ema7 and ema21) else False
    ema_cross_up = len(closes_1h) >= 2 and closes_1h[-2] < (ema(closes_1h[:-1], 7) or 0) and above_ema7
    ema_cross_down = len(closes_1h) >= 2 and closes_1h[-2] > (ema(closes_1h[:-1], 7) or float('inf')) and not above_ema7

    # ---- RSI ----
    rsi_overbought = rsi_14 and rsi_14 > 70
    rsi_oversold = rsi_14 and rsi_14 < 30
    rsi_neutral_high = rsi_14 and rsi_14 > 60
    rsi_neutral_low = rsi_14 and rsi_14 < 40

    # ---- MACD ----
    macd_bull = macd_hist and macd_hist > 0
    macd_bear = macd_hist and macd_hist < 0
    macd_cross_up = len(closes_1h) >= 3 and macd(closes_1h[:-1])[2] and macd(closes_1h[:-1])[2] < 0 < macd_hist
    macd_cross_down = len(closes_1h) >= 3 and macd(closes_1h[:-1])[2] and macd(closes_1h[:-1])[2] > 0 > macd_hist

    # ---- 4H 趋势确认 ----
    h4_above_ema200 = closes_4h[-1] > ema200_4h if ema200_4h else True
    h4_rsi_high = rsi_4h and rsi_4h > 55
    h4_rsi_low = rsi_4h and rsi_4h < 45

    # ---- 资金费率信号 ----
    funding_danger = funding_pct > 0.03  # 多头拥挤
    funding_bearish = funding_pct < -0.03  # 空头拥挤

    # ---- 突破信号 ----
    high_20 = max(closes_1h[-20:])
    low_20 = min(closes_1h[-20:])
    high_50 = max(closes_1h[-50:])
    low_50 = min(closes_1h[-50:])
    range_20 = high_20 - low_20
    near_high = last > high_20 * 0.995
    near_low = last < low_20 * 1.005

    # ---- 量能信号 ----
    vol_surge = vol_r > 1.5
    vol_dry = vol_r < 0.7

    # =====================
    # 多头信号 (LONG)
    # =====================
    long_score = 0
    long_reasons = []

    if above_ema7 and above_ema21 and ema7_above_ema21:
        long_score += 30
        long_reasons.append("EMA7>EMA21多头排列")
    if ema_cross_up:
        long_score += 25
        long_reasons.append("EMA7金叉")
    if rsi_oversold:
        long_score += 20
        long_reasons.append("RSI超卖")
    if rsi_neutral_low and rsi_14 and rsi_14 > 25:
        long_score += 10
        long_reasons.append("RSI偏低支撑")
    if macd_bull or macd_cross_up:
        long_score += 20
        long_reasons.append("MACD多头")
    if h4_above_ema200 and h4_rsi_low:
        long_score += 15
        long_reasons.append("4H超卖共振")
    if near_low and vol_surge:
        long_score += 20
        long_reasons.append("低位放量反弹")
    if vol_dry:
        long_score += 5
        long_reasons.append("地量磨底")
    if funding_danger:
        long_score -= 10
        long_reasons.append("资金费率偏高(多头拥挤)")
    if not funding_danger and funding_pct < 0.01:
        long_score += 10
        long_reasons.append("资金费率健康")

    # =====================
    # 空头信号 (SHORT)
    # =====================
    short_score = 0
    short_reasons = []

    if not above_ema7 and not above_ema21 and not ema7_above_ema21:
        short_score += 30
        short_reasons.append("EMA空头排列")
    if ema_cross_down:
        short_score += 25
        short_reasons.append("EMA死叉")
    if rsi_overbought:
        short_score += 20
        short_reasons.append("RSI超买")
    if rsi_neutral_high and rsi_14 and rsi_14 < 80:
        short_score += 10
        short_reasons.append("RSI偏高压力")
    if macd_bear or macd_cross_down:
        short_score += 20
        short_reasons.append("MACD空头")
    if not h4_above_ema200 and h4_rsi_high:
        short_score += 15
        short_reasons.append("4H超买共振")
    if near_high and vol_surge:
        short_score += 20
        short_reasons.append("高位放量滞涨")
    if funding_bearish:
        short_score += 15
        short_reasons.append("资金费率偏空")
    if vol_dry:
        short_score += 5
        short_reasons.append("高位地量")

    # =====================
    # 决策
    # =====================
    confidence = 0
    signal_type = "WAIT"

    if long_score > short_score and long_score >= 40:
        direction = "LONG"
        confidence = min(long_score, 95)
        stop = last - (atr_14 * 1.5 if atr_14 else last * 0.008)
        tp1 = last + (atr_14 * 3 if atr_14 else last * 0.015)
        tp2 = last + (atr_14 * 5 if atr_14 else last * 0.025)
        signals = long_reasons
    elif short_score > long_score and short_score >= 40:
        direction = "SHORT"
        confidence = min(short_score, 95)
        stop = last + (atr_14 * 1.5 if atr_14 else last * 0.008)
        tp1 = last - (atr_14 * 3 if atr_14 else last * 0.015)
        tp2 = last - (atr_14 * 5 if atr_14 else last * 0.025)
        signals = short_reasons
    elif abs(long_score - short_score) <= 10 and max(long_score, short_score) >= 30:
        # 震荡信号
        if near_high:
            direction = "SHORT"
            confidence = 35
            stop = last + (atr_14 * 1 if atr_14 else last * 0.005)
            tp1 = last - (atr_14 * 2 if atr_14 else last * 0.01)
            signals = ["震荡高位+偏空"]
        elif near_low:
            direction = "LONG"
            confidence = 35
            stop = last - (atr_14 * 1 if atr_14 else last * 0.005)
            tp1 = last + (atr_14 * 2 if atr_14 else last * 0.01)
            signals = ["震荡低位+偏多"]

    # 过滤低波动
    if range_20 < last * 0.003:
        direction = "WAIT"
        confidence = 0
        signals = ["波动率过低，等待"]

    basis = ((mark - ticker["sodUtc0"]) / ticker["sodUtc0"]) * 100

    return {
        "inst_id": inst_id,
        "name": name,
        "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "last": last,
        "mark": mark,
        "basis": round(basis, 4),
        "funding_pct": round(funding_pct, 4),
        "oi_usd": round(oi_usd / 1e8, 2),  # 亿
        "rsi_14": round(rsi_14, 1) if rsi_14 else None,
        "ema7": round(ema7, 2) if ema7 else None,
        "ema21": round(ema21, 2) if ema21 else None,
        "atr": round(atr_14, 2) if atr_14 else None,
        "vol_ratio": round(vol_r, 2),
        "direction": direction,
        "confidence": confidence,
        "entry": round(entry, 2),
        "stop": round(stop, 2) if stop else None,
        "tp1": round(tp1, 2) if tp1 else None,
        "tp2": round(tp2, 2) if tp2 else None,
        "signals": signals,
        "long_score": long_score,
        "short_score": short_score,
    }

def format_signal(sig):
    if sig is None:
        return "❌ 数据获取失败"
    if sig["confidence"] == 0:
        return f"⏸ [{sig['name']}] 观望\n信号分: 0\n理由: {sig['signals']}"

    dir_emoji = "🟢" if sig["direction"] == "LONG" else ("🔴" if sig["direction"] == "SHORT" else "⚪")
    conf_bar = "█" * int(sig["confidence"] / 10) + "░" * (10 - int(sig["confidence"] / 10))

    lines = [
        f"{dir_emoji} **{sig['direction']}** | 置信 {sig['confidence']}% | `{conf_bar}`",
        f"─── {sig['name']} {sig['timestamp']} ───",
        f"价格: **{sig['last']}** | 标记价: {sig['mark']} | 溢价: {sig['basis']:+.3f}%",
        f"资金费率: {sig['funding_pct']:+.4f}% | OI: {sig['oi_usd']}亿",
        f"RSI(14): {sig['rsi_14']} | EMA7: {sig['ema7']} | EMA21: {sig['ema21']}",
        f"ATR: {sig['atr']} | 量比: {sig['vol_ratio']}",
        f"─── 信号逻辑 ───",
    ]
    for r in sig["signals"]:
        lines.append(f"  • {r}")
    if sig["confidence"] > 0:
        lines.extend([
            f"─── 交易计划 ───",
            f"入场: **{sig['entry']}**",
        ])
        if sig["stop"]:
            lines.append(f"止损: {sig['stop']} ({'+' if sig['direction']=='LONG' else ''}{round((sig['stop']-sig['entry'])/sig['entry']*100,2) if sig['direction']=='LONG' else round((sig['entry']-sig['stop'])/sig['entry']*100,2)}%)")
        if sig["tp1"]:
            lines.append(f"目标1: {sig['tp1']} ({'+' if sig['direction']=='LONG' else ''}{round((sig['tp1']-sig['entry'])/sig['entry']*100,2) if sig['direction']=='LONG' else round((sig['entry']-sig['tp1'])/sig['entry']*100,2)}%)")
        if sig["tp2"]:
            lines.append(f"目标2: {sig['tp2']} ({'+' if sig['direction']=='LONG' else ''}{round((sig['tp2']-sig['entry'])/sig['entry']*100,2) if sig['direction']=='LONG' else round((sig['entry']-sig['tp2'])/sig['entry']*100,2)}%)")

    return "\n".join(lines)

if __name__ == "__main__":
    print("=== 混沌龙虾信号扫描 ===")
    results = []
    for inst, name in [("BTC-USDT-SWAP","BTC"), ("ETH-USDT-SWAP","ETH")]:
        try:
            sig = generate_signal(inst, name)
            results.append(sig)
            print(format_signal(sig))
            print()
        except Exception as e:
            print(f"ERROR {inst}: {e}")

    # 保存信号
    with open("/root/.openclaw/workspace/signals/logs/latest.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 记录历史
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M")
    with open(f"/root/.openclaw/workspace/signals/logs/{ts}.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
