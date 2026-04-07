"""
信号记录与准确率追踪
每次信号后记录，结果由人工或脚本更新
"""
import json, os
from datetime import datetime, timezone, timedelta

LOG = "/root/.openclaw/workspace/signals/logs/signals_log.json"

def load():
    if os.path.exists(LOG):
        with open(LOG) as f:
            return json.load(f)
    return []

def record(signal):
    """记录一个新信号"""
    data = load()
    entry = {
        "id": len(data) + 1,
        "ts": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "inst": signal["inst_id"],
        "name": signal["name"],
        "direction": signal["direction"],
        "confidence": signal["confidence"],
        "entry": signal["entry"],
        "stop": signal["stop"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "result": None,  # PENDING / WIN / LOSS
        "exit_price": None,
        "pnl_pct": None,
        "notes": ""
    }
    data.append(entry)
    with open(LOG, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return entry

def update(id, result, exit_price, notes=""):
    """更新信号结果"""
    data = load()
    for e in data:
        if e["id"] == id:
            e["result"] = result
            e["exit_price"] = exit_price
            if e["entry"]:
                diff = exit_price - e["entry"] if e["direction"] == "LONG" else e["entry"] - exit_price
                e["pnl_pct"] = round(diff / e["entry"] * 100, 3)
            e["notes"] = notes
            break
    with open(LOG, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def stats():
    """统计准确率"""
    data = load()
    closed = [x for x in data if x["result"] in ("WIN", "LOSS")]
    if not closed:
        return "暂无已结束信号"
    wins = [x for x in closed if x["result"] == "WIN"]
    win_rate = len(wins) / len(closed) * 100
    longs = [x for x in closed if x["direction"] == "LONG"]
    shorts = [x for x in closed if x["direction"] == "SHORT"]
    long_wins = [x for x in longs if x["result"] == "WIN"]
    short_wins = [x for x in shorts if x["result"] == "WIN"]
    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(win_rate, 1),
        "long_wins": len(long_wins),
        "long_total": len(longs),
        "short_wins": len(short_wins),
        "short_total": len(shorts),
        "pending": len([x for x in data if x["result"] is None]),
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "stats":
        print(stats())
    elif len(sys.argv) >= 3:
        # update <id> <WIN|LOSS> <exit_price> [notes]
        _, sid, result, exit_price = sys.argv[:4]
        notes = sys.argv[4] if len(sys.argv) > 4 else ""
        update(int(sid), result, float(exit_price), notes)
        print("Updated:", stats())
