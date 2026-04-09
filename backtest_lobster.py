"""
龙虾量化 v8.0
EMA50/200金叉死叉 + RSI过滤 + ATR止损止盈
"""
import requests, time, math, numpy as np, matplotlib, csv
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
from typing import List

BINANCE = "https://api.binance.com/api/v3/klines"
INITIAL = 1000
COMMISSION = 0.0004
RISK_PER_TRADE = 20
SL_ATR = 1.5
TP_ATR = 3.0
MIN_CAPITAL = 80

def fetch(symbol, start_ms, end_ms):
    data, cur = [], start_ms
    while cur < end_ms:
        r = requests.get(f"{BINANCE}?symbol={symbol}&interval=1h&startTime={cur}&endTime={end_ms}&limit=1000", timeout=15)
        rows = r.json()
        if not rows: break
        for row in rows:
            data.append({'ts': int(row[0]), 'h': float(row[2]), 'l': float(row[3]),
                         'c': float(row[4]), 'v': float(row[5])})
        cur = rows[-1][0] + 1
        time.sleep(0.05)
    seen, result = set(), []
    for c in data:
        if c['ts'] not in seen:
            seen.add(c['ts'])
            result.append(c)
    result.sort(key=lambda x: x['ts'])
    return result

def ema_calc(vals, n):
    if len(vals) < n:
        return sum(vals) / len(vals) if vals else 0.0
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for p in vals[n:]:
        e = p * k + e * (1 - k)
    return e

def rsi_calc(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    ds = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    g = [d if d > 0 else 0 for d in ds[-n:]]
    l = [-d if d < 0 else 0 for d in ds[-n:]]
    ag = sum(g) / n
    al = sum(l) / n
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def atr_calc(highs, lows, closes, idx, n=14):
    if idx < n:
        return 0.0
    trs = []
    for j in range(idx - n + 1, idx + 1):
        tr = max(highs[j] - lows[j],
                 abs(highs[j] - closes[j-1]),
                 abs(lows[j] - closes[j-1]))
        trs.append(tr)
    return sum(trs) / n

def run():
    print("=" * 60)
    print("🦞 龙虾量化 v8.0 回测")
    print("=" * 60)

    start_ms = int(datetime(2025, 1, 1).timestamp() * 1000)
    end_ms = int(datetime(2026, 4, 9).timestamp() * 1000)

    print("📥 下载数据...")
    eth = fetch("ETHUSDT", start_ms, end_ms)
    btc = fetch("BTCUSDT", start_ms, end_ms)
    print(f"  {len(eth)} / {len(btc)} 根")

    n = len(eth)
    closes_eth = [c['c'] for c in eth]
    highs_eth = [c['h'] for c in eth]
    lows_eth = [c['l'] for c in eth]
    volumes = [c['v'] for c in eth]
    closes_btc = [c['c'] for c in btc]
    timestamps = [datetime.fromtimestamp(c['ts']/1000).strftime('%Y-%m-%d %H:%M') for c in eth]

    print("  预计算指标...")
    ema50 = [0.0] * n
    ema200 = [0.0] * n
    atr_arr = [0.0] * n

    for i in range(n):
        if i >= 49:
            ema50[i] = ema_calc(closes_eth[i-49:i+1], 50)
        else:
            ema50[i] = closes_eth[i]
        if i >= 199:
            ema200[i] = ema_calc(closes_eth[i-199:i+1], 200)
        else:
            ema200[i] = closes_eth[i]
        atr_arr[i] = atr_calc(highs_eth, lows_eth, closes_eth, i)

    print(f"  ATR样: {atr_arr[300]:.2f}, {atr_arr[400]:.2f}, {atr_arr[1000]:.2f}")

    capital = INITIAL
    max_capital = INITIAL
    max_drawdown = 0.0
    equity_curve = []
    trades = []
    wins_count = 0
    losses_count = 0
    total_fees = 0.0
    open_trades = 0
    position = None

    print(f"  开始遍历 {n} 根K线...")

    for i in range(300, n):
        price = closes_eth[i]
        ts = timestamps[i]
        btc_price = closes_btc[i] if i < len(closes_btc) else closes_btc[-1]
        atr_val = atr_arr[i]

        # 更新权益
        cur_equity = capital
        if position:
            d, entry, qty = position['d'], position['e'], position['q']
            pnl_pct = (price - entry) / entry if d == "LONG" else (entry - price) / entry
            cur_equity += qty * entry * pnl_pct

        equity_curve.append({'t': ts, 'e': cur_equity})
        if cur_equity > max_capital:
            max_capital = cur_equity
        dd = (max_capital - cur_equity) / max_capital
        if dd > max_drawdown:
            max_drawdown = dd

        # ===== 有持仓 =====
        if position:
            d, entry, qty, sl_price = position['d'], position['e'], position['q'], position['s']

            # 止损
            hit_sl = (d == "LONG" and price <= sl_price) or (d == "SHORT" and price >= sl_price)
            if hit_sl:
                pnl_pct = (sl_price - entry) / entry if d == "LONG" else (entry - sl_price) / entry
                pnl = qty * entry * pnl_pct
                fee = price * qty * COMMISSION
                net = pnl - fee
                capital += net
                trades.append({'d': d, 'e': entry, 'x': price, 'q': qty, 'p': net, 'r': 'SL', 'ts': ts})
                if net > 0:
                    wins_count += 1
                else:
                    losses_count += 1
                total_fees += fee
                position = None
                continue

            # 止盈
            tp_price = position.get('tp')
            if tp_price:
                hit_tp = (d == "LONG" and price >= tp_price) or (d == "SHORT" and price <= tp_price)
                if hit_tp:
                    pnl_pct = (tp_price - entry) / entry if d == "LONG" else (entry - tp_price) / entry
                    pnl = qty * entry * pnl_pct
                    fee = price * qty * COMMISSION
                    net = pnl - fee
                    capital += net
                    trades.append({'d': d, 'e': entry, 'x': price, 'q': qty, 'p': net, 'r': 'TP', 'ts': ts})
                    if net > 0:
                        wins_count += 1
                    else:
                        losses_count += 1
                    total_fees += fee
                    position = None
                    continue

            # 追踪止损
            best = position.get('b', entry)
            if d == "LONG" and price > best:
                position['b'] = price
            elif d == "SHORT" and price < best:
                position['b'] = price

        # ===== 无持仓 =====
        if not position and capital >= MIN_CAPITAL and atr_val > 0:
            c50 = ema50[i]
            c200 = ema200[i]
            p50 = ema50[i-1] if i > 0 else c50
            p200 = ema200[i-1] if i > 0 else c200
            rsi_val = rsi_calc(closes_eth[max(0, i-13):i+1])
            vol_now = volumes[i]
            vol_avg = sum(volumes[max(0, i-20):i]) / 20 if i >= 20 else vol_now

            # 金叉做多
            golden = (p50 <= p200 and c50 > c200)
            # 死叉做空
            death = (p50 >= p200 and c50 < c200)

            if golden and rsi_val < 65:
                sl_p = price - atr_val * SL_ATR
                tp_p = price + atr_val * TP_ATR
                qty = max(1, int(RISK_PER_TRADE / (atr_val * SL_ATR)))
                position = {'d': 'LONG', 'e': price, 'q': qty, 's': sl_p, 'tp': tp_p, 'b': price}
                open_trades += 1
            elif death and rsi_val > 35:
                sl_p = price + atr_val * SL_ATR
                tp_p = price - atr_val * TP_ATR
                qty = max(1, int(RISK_PER_TRADE / (atr_val * SL_ATR)))
                position = {'d': 'SHORT', 'e': price, 'q': qty, 's': sl_p, 'tp': tp_p, 'b': price}
                open_trades += 1

        if i % 2000 == 0 and i > 0:
            print(f"  {i}/{n} | ${capital:.2f} | {open_trades}笔")

    # 平最后持仓
    if position and n > 0:
        p = closes_eth[-1]
        d, entry, qty = position['d'], position['e'], position['q']
        pnl_pct = (p - entry) / entry if d == "LONG" else (entry - p) / entry
        pnl = qty * entry * pnl_pct
        fee = p * qty * COMMISSION
        capital += pnl - fee
        trades.append({'d': d, 'e': entry, 'x': p, 'q': qty, 'p': pnl - fee, 'r': 'END', 'ts': timestamps[-1]})

    # ===== 报告 =====
    total = wins_count + losses_count
    win_rate = wins_count / total * 100 if total > 0 else 0
    total_return = (capital - INITIAL) / INITIAL * 100

    rets = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i]['e'] - equity_curve[i-1]['e']) / max(equity_curve[i-1]['e'], 1)
        rets.append(r)
    sharpe = (np.mean(rets) / np.std(rets) * math.sqrt(365 * 24)) if rets and np.std(rets) > 0 else 0

    wins_list = [t['p'] for t in trades if t['p'] > 0]
    losses_list = [abs(t['p']) for t in trades if t['p'] < 0]
    avg_win = np.mean(wins_list) if wins_list else 0
    avg_loss = np.mean(losses_list) if losses_list else 0
    profit_factor = (avg_win * len(wins_list)) / (avg_loss * len(losses_list)) if losses_list and losses_list else 0

    max_consec = 0
    consec = 0
    for t in trades:
        if t['p'] < 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    print("\n" + "=" * 60)
    print("🦞 龙虾量化 v8.0 回测报告")
    print("=" * 60)
    print(f"回测周期: 2025-01-01 至 2026-04-08")
    print(f"初始资金: ${INITIAL:.2f}")
    print(f"最终资金: ${capital:.2f}")
    print(f"总收益率: {total_return:+.2f}%")
    print(f"最大回撤: {max_drawdown:.2%}")
    print(f"年化Sharpe: {sharpe:.2f}")
    print("-" * 60)
    print(f"开仓次数: {open_trades}")
    print(f"胜率: {win_rate:.1f}%")
    print(f"平均盈利: ${avg_win:.2f} | 平均亏损: ${avg_loss:.2f}")
    print(f"盈亏比: {profit_factor:.2f}")
    print(f"最大连续亏损: {max_consec}次")
    print(f"总手续费: ${total_fees:.2f}")
    print("=" * 60)

    monthly = {}
    for t in trades:
        m = t['ts'][:7]
        monthly[m] = monthly.get(m, 0) + t['p']
    print("\n【月度盈亏】")
    for m in sorted(monthly.keys()):
        print(f"  {m}: ${monthly[m]:+.2f}")

    reason_stats = {}
    for t in trades:
        r = t['r']
        if r not in reason_stats:
            reason_stats[r] = {'n': 0, 'p': 0}
        reason_stats[r]['n'] += 1
        reason_stats[r]['p'] += t['p']
    print("\n【平仓原因】")
    for r, s in sorted(reason_stats.items()):
        print(f"  {r}: {s['n']}笔 | ${s['p']:+.2f}")

    if trades:
        with open('/root/.openclaw/workspace/backtest_trades.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=trades[0].keys())
            w.writeheader()
            w.writerows(trades)
    if equity_curve:
        with open('/root/.openclaw/workspace/backtest_equity.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=equity_curve[0].keys())
            w.writeheader()
            w.writerows(equity_curve)
        eq_vals = [r['e'] for r in equity_curve]
        plt.figure(figsize=(14, 6))
        plt.plot(eq_vals, linewidth=1, color='#1565C0')
        plt.fill_between(range(len(eq_vals)), eq_vals, alpha=0.15, color='#1565C0')
        plt.title('Lobster Quant v8.0 - Equity Curve', fontsize=13, fontweight='bold')
        plt.ylabel('Equity (USD)')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig('/root/.openclaw/workspace/backtest_equity.png', dpi=150, bbox_inches='tight')
        plt.close()
        print(f"\n📊 backtest_equity.png | 📄 backtest_trades.csv")

if __name__ == '__main__':
    run()
