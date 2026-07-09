#!/usr/bin/env python3
"""20x杠杆 精准信号策略 v_smart_v3（2026-07-09 老板拍板最终版）
v_smart_v3 老板要求完整机制盘点 + 不留漏洞:
- 杠杆: 20x (老板偏好, 不动)
- 仓位上限: MAX_POS_PCT=10% (老板要求, 名义仓位不超过账户×10%)
- 总仓位: MAX_TOTAL_EXPOSURE=1.0 (防止重仓)
- 风险阶梯(复利滚仓核心):
  * <30U: 2% (危险区, 优先保本)
  * 30-100U: 5% (正常交易)
  * 100-500U: 8% (富余区, 复利)
  * >500U: 10% (鲸鱼区, 满滚)
- SL: ATR动态跟随波动 (不是固定1%了)
  * BTC: SL=ATR×1.8 (约0.8-1.2%) + TP=2.2% (盈亏比1.5:1)
  * ETH: SL=ATR×2.2 (约1.0-1.5%) + TP=2.5% (盈亏比1.7:1)
- DCA补仓: v_smart_v3 不启用 (老板决定: 20x杠杆下补仓=加倍错误风险, 首仓10%内满仓)
- 追踪止盈: 阶梯式SL锁利 (浮盈1.5%保本锁, 2.5%+1.2%锁, TP1后追0.8%, 5%+追1.2%)
- 手动平仓检测: manual_close_dir + 冷静期60秒
- /hold命令: _check_telegram_commands() 支持
- BTC/ETH双向 + 独立参数
- 保留: v6.0全部信号(RSI+EMA+ADX+MACD+布林+支撑阻力+K线+资金费率+趋势反转预警+回撤保护+追踪止损+熔断)
- 简化: 不使用动态杠杆/周末降挡/爆仓检测(老板意见:10%仓位+20x+SL足够防爆,动态杠杆API繁琐)

v6.0 全面升级（2026-07-08）：
- P0：预警式自动止损（SL击穿30秒后老板未干预自动平仓）
- P1：胜率统计三分类（wins/losses/neutral，win_rate排除中性笔）
- P1：信号门槛调整（震荡6.5→5.0，强趋势2.5→3.5，逆势独立4.5）
- P2：API凭证从环境变量读取（代码本身不存key）
- P2：恢复反向持仓屏蔽（避免双倍杠杆风险）
- P2：SHORT触发平衡（两路径：严格趋势+轻度弱势）
- P3：Pnl归档（v5.12保留）
v5.11优化：TP2=3.0%→3.5%（5000次蒙特卡洛验证 EV提升+0.059%/笔）
v5.10优化：RSI超买/超卖软门槛 - 1H RSI>75不许做多，<25不许做空（防追高杀跌）
v5.9优化：集成trading-knowledge skill - K线形态识别+信号K检测+流动性猎杀+支撑阻力强度
v5.8修复：账户清零状态自愈 - high_water+lock+cooldown自动重置，避免永久回撤循环
v5.6优化：熔断间隔15分钟→30分钟，冷静期0秒→5分钟
v5.5优化：新增MACD+布林带确认信号，精准度提升
v5.4优化：双模式信号 - 强趋势中(4H+1H共振)自动切换到趋势跟随模式(RSI<50做多/>50做空)，避免踏空
v5.3优化：持仓中趋势反转保护 - 检测到持仓方向与4H趋势矛盾时预警（用户控制SL，AI只报不操作）
v5.2优化：API重试机制 + 趋势冲突过滤 + 趋势反转预警
修复：网络抖动时频繁崩溃 + 4H/1H趋势矛盾时逆势开仓 + 趋势反转时无预警
"""
import requests, time, json, hmac, hashlib, os
from datetime import datetime

# === v6.0：凭证从环境变量读取（代码本身不存 key）===
# 启动时如果环境变量未设置，从 .env 加载，最后还无则报错退出
def _load_env_file():
    env_path = "/root/.openclaw/workspace/.env"
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('export '):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

_load_env_file()
if not os.environ.get("BINANCE_API_KEY") or not os.environ.get("BINANCE_API_SECRET"):
    raise SystemExit(
        "❌ 缺少凭证环境变量。\n"
        "   在 .env 里加：\n"
        "     BINANCE_API_KEY=你的key\n"
        "     BINANCE_API_SECRET=你的secret\n"
        "   或在 PM2 启动时手动 export。"
    )
API_KEY = os.environ.get("BINANCE_API_KEY")
SECRET  = os.environ.get("BINANCE_API_SECRET")
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

# === v5.10 优化：余额不足静默降频 ===
_LAST_LOW_BAL_WARN = 0.0

# === v5.2 新增：稳定性优化 ===
TREND_CONFLICT_FILTER = True  # 趋势冲突过滤（4H和1H矛盾时跳过信号）
API_RETRY_MAX = 3              # API重试次数
API_RETRY_DELAY = 2           # 重试延迟（秒，指数退避）
API_TIMEOUT = 15             # API超时时间

loss_streak_count = 0
last_loss_time = 0
last_trade_time = 0

# === v_final_smart（2026-07-09）：老板拍板 - 20x杠杆不动 + BTC/ETH独立 ===
# 改动: 杠杆维持20x(老板偏好), 风险10%→3%(提高扛连亏能力), 
#       最低余额10→30, TP1 2%→2.5%(20x下需要更宽TP才能赢)
#       ETH重新加入(BTC/ETH分开策略)
# 核心思路: 20x杠杆要赢 = 必须高胜率信号 + 严格入场条件
#           BTC: 趋势跟踪为主(3年+107%, 只做多盈利高)
#           ETH: 双向反转为主(3年震荡, 反转胜率更高)
#           风险3%/笔 = 20x下单名义0.15%承受位, 抗连亏15次
LEVER = 20          # 老板要求维持20x
RISK_PCT = 0.03     # v_smart: 10%→3%, 提高抗连亏能力(可抗15次连亏)
MIN_BAL = 30        # v_smart: 50→30, 配合老板充值情况
OPEN_COOLDOWN = 300  # 冷静期5分钟

# === v_smart：BTC和ETH独立策略 ===
ENABLED_SYMBOLS = ['BTCUSDT', 'ETHUSDT']  # v_smart: 两种合约都跑, 但分开策略

# BTC参数 (双向跟踪型 - 老板要求BTC也做空)
# 20x杠杆下策略要稳 = SL必须用ATR(跟随波动)不用固定1%
# TP/SL≥1.5:1 (BTC 1h波动0.32%, 1%止损每天被扫4-5次, 改成ATR×1.8=约0.9-1.2% SL)
BTC_PARAMS = {
    'sl_atr_mult': 1.8,      # v_smart_v2: 固定%→ATR×1.8, 跟随波动
    'tp1_pct': 0.022,        # 2.2% TP1 (让TP跟SL同比例波动, 盈亏比1.5:1)
    'tp2_trigger': 0.038,    # 3.8%触发TP2
    'tp2_buffer': 0.012,
    'rsi_long_max': 65,      # 双向都要有, 放宽超买门槛
    'rsi_long_min': 25,   # v3.1: 32→25, 让他接住深度回调
    'rsi_short_min': 35,
    'rsi_short_max': 68,
    'adx_min': 20,
    'allow_short': True,     # v_smart_v2: BTC也要双向(BOSS要求)
    'require_trend': False,  # v_smart_v2: 不强制趋势, 让信号决定
    'score_thresh_normal': 5.0,   # 双向后门槛微调(太严会错过机会)
    'score_thresh_trending': 3.8,
    'counter_trend_thresh': 5.0,
}

# ETH参数 (双向反转 + ATR动态止损 - 老板要求策略稳)
# ETH 1h波动0.43% > BTC 0.32%, SL要更宽才能不被噪音扫掉
# SL=ATR×2.2 约1.2-1.5%, TP=2.5% = 盈亏比≥1.7:1
ETH_PARAMS = {
    'sl_atr_mult': 2.2,      # v_smart_v2: 固定3.5%→ATR×2.2, 跟随波动但更宽
    'tp1_pct': 0.025,        # 2.5% TP1 (维持, 让策略稳)
    'tp2_trigger': 0.045,
    'tp2_buffer': 0.015,
    'rsi_long_max': 38,      # ETH反转: 超卖做多(微调)
    'rsi_long_min': 15,
    'rsi_short_min': 62,     # 超买卖空(微调)
    'rsi_short_max': 85,
    'adx_min': 18,           # ETH反转在震荡市, ADX要求保持低
    'allow_short': True,
    'require_trend': False,
    'score_thresh_normal': 4.5,
    'score_thresh_trending': 3.0,
    'counter_trend_thresh': 4.0,
}

SL_ATR_MULT = 0.025  # 默认用BTC参数
TP1_PCT = 0.025      # v_smart：2%→2.5%，20x杠杆下让TP稍宽一点能覆盖摩擦成本
TP2_TRIGGER = 0.035  # v5.11优化：3%→3.5% 让强趋势多走一截 (5000次蒙特卡洛验证+EV提升)
TP2_BUFFER = 0.01    # 追踪回撤1%，增加呼吸空间
WIN_STREAK_ACCEL = 2   # 连赢2次TP1后激活加速模式
WIN_STREAK_THRESH = 0.05  # 加速模式下RSI门槛临时降5%
ACCEL_SCORE_BOOST = 2  # 加速模式下SHORT信号评分额外加分

# === v6.0：信号门槛调整 ===
SCORE_THRESH_NORMAL = 5.0      # 震荡市门槛 6.5→5.0（多指标仍需共振）
SCORE_THRESH_TRENDING = 3.5    # 强趋势门槛 2.5→3.5（避免频繁开仓）
COUNTER_TREND_THRESH = 4.5     # 逆势信号独立门槛（v6.0新独立路径）

# === v_smart_v3: SL自动保护(激活) ===
SL_AUTO_ALERT_FILE = "/root/.openclaw/workspace/.sl_alert_pending"
SL_AUTO_DELAY = 30              # 预警后30秒老板未干预则自动平
SL_AUTO_ENABLED = True          # v_smart_v3: 老板明确要防爆仓+防回吐, 激活自动平
                                # /hold命令可以取消本次自动平
                                # SL不是动态调整而是动态计算(ATR跟随)

# === 复利风控参数 ===
MAX_POS_PCT = 0.10       # v_smart_v3: 30%→10%（老板要求：仓位上限 = 余额10%）
MAX_TOTAL_EXPOSURE = 1.0  # v_smart_v3: 150%→100%（总名义仓位不超过1倍余额，防止重仓）

# === v_smart_v3: 补仓（DCA）机制 ===
# 老板明确要求: 仓位≤本金10%, 补仓后不能超
# 在20x杠杆下首仓$10名义价值已达$200 = 1.67倍本金, 补仓=加倍错误风险
# 结论: v_smart_v3不要DCA机制, 首仓10%内满仓, 错了认错不补仓
DCA_ENABLED = False              # v_smart_v3: 老板决定不启用补仓
DCA_MAX_TIMES = 0                # 补仓次数 = 0 (禁用)
DCA_TRIGGER_PCT = 0.020          # 以下参数保留但不会触发
DCA_ADD_RATIO = 0.0              # 补仓比例 = 0
DCA_MIN_HOLD_BARS = 12
DCA_SL_RESET = False
DCA_REQUIRE_TREND_AGREE = False

# === v_smart_v3: 复利滚仓阈值(5档平滑) ===
RISK_DANGER = 30                  # 危险区：余额<30, 风险降到2%防强平
RISK_NORMAL_MIN = 30
RISK_NORMAL_MAX = 80
RISK_RICH_MIN = 80                # v3.1平滑：80-100过渡(风险5%→7%线生)
RISK_RICH_MAX = 250
RISK_WHALE_MIN = 250
RISK_WHALE_MAX = 500
RISK_MEGA_THRESHOLD = 500

RISK_DANGER_PCT = 0.02            # <30: 2%
RISK_NORMAL_PCT = 0.05            # 30-80: 5%
RISK_RICH_PCT = 0.07              # 80-250: 7%
RISK_WHALE_PCT = 0.09             # 250-500: 9%
RISK_MEGA_PCT = 0.10              # >500: 10%（复利满滚）

# === v_smart_v3: 追踪止盈（趋势单抓取）===
TRAIL_AFTER_TP1 = 0.015           # TP1出后启动追踪
TRAIL_DISTANCE = 0.008            # 追踪距离0.8%（ATR动态SL补充）

# === v_smart_v3: 手续费/滑点补偿 ===
EXPECTED_FEE_PCT = 0.0016         # 20x下单双边手续费+滑点 总名义成本约0.16%

# === 安全保卫 ===
CRASH_COUNT_FILE = "/root/.openclaw/workspace/.crash_count"
CRASH_WINDOW_SECS = 600      # 10分钟内
CRASH_LIMIT = 5              # 超过5次重启则进入安全模式

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
    # 如果窗口期已过，重置计数
    if first == 0 or (now - first) > CRASH_WINDOW_SECS:
        count, first = 0, now
    count += 1
    with open(CRASH_COUNT_FILE, "w") as f:
        json.dump({"count": count, "first_time": first}, f)
    return count

def check_crash_safety():
    """检查是否在安全模式下，拒绝交易"""
    count, first = get_crash_count()
    now = time.time()
    if first > 0 and (now - first) <= CRASH_WINDOW_SECS and count >= CRASH_LIMIT:
        log(f"⚠️ 安全模式：10分钟内重启{count}次，等待冷静期...")
        return False  # False = 拒绝交易
    return True

# === v5.11 新增：崩溃自愈 + Telegram告警 ===
CRASH_ALERT_THRESHOLD = 2   # crash_count>=2 触发告警
CRASH_AUTO_RESET_SECS = 86400  # 24小时无重启自动清零
CRASH_ALERT_TRIGGERED_FILE = "/root/.openclaw/workspace/.crash_alert_triggered"

def auto_reset_crash_if_stale():
    """24小时无重启则清零计数（防止历史crash污染）"""
    try:
        count, first = get_crash_count()
        now = time.time()
        if first > 0 and (now - first) > CRASH_AUTO_RESET_SECS and count > 0:
            with open(CRASH_COUNT_FILE, "w") as f:
                json.dump({"count": 0, "first_time": 0}, f)
            log(f"🔄 crash_count 自动重置（原count={count}，超过24h无重启）")
    except Exception as e:
        pass

def maybe_alert_crash(count):
    """crash_count>=阈值时推Telegram（每窗口只推1次）"""
    if count < CRASH_ALERT_THRESHOLD:
        return
    try:
        triggered = False
        if os.path.exists(CRASH_ALERT_TRIGGERED_FILE):
            with open(CRASH_ALERT_TRIGGERED_FILE) as f:
                triggered_data = json.load(f)
                triggered = triggered_data.get("current_window", False)
        if triggered:
            return  # 当前窗口已推过，不重复
        # 触发告警（异步执行shell脚本，不阻塞主循环）
        import subprocess
        subprocess.Popen(
            ["bash", "/root/.openclaw/workspace/crash_alert.sh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        with open(CRASH_ALERT_TRIGGERED_FILE, "w") as f:
            json.dump({"current_window": True, "at": time.time()}, f)
        log(f"🚨 已触发Telegram崩溃告警（count={count}）")
    except Exception as e:
        log(f"⚠️ 告警触发失败: {e}")

# 启动时立即自愈 + 检查
auto_reset_crash_if_stale()
DRAWDOWN_PROTECT = 0.30  # 小账户回撤30%才触发（原15%太敏感）
DRAWDOWN_COOLDOWN = 1800   # 回撤保护冷却期：30分钟内不重复触发
DRAWDOWN_COOLDOWN_FILE = "/root/.openclaw/workspace/.drawdown_cooldown"  # 冷却期记录
DRAWDOWN_LOCK_FILE = "/root/.openclaw/workspace/.drawdown_lock"  # 回撤后冷静期锁
DRAWDOWN_LOCK_SECS = 600   # 冷静期10分钟（原15分钟），加快反手机会
HIGH_WATER_FILE = "/root/.openclaw/workspace/.high_water"  # 历史最高余额记录
RISK_DANGER = 30       # 危险区余额阈值 v6.0: 20→30（更保守）
RISK_DANGER_PCT = 0.05  # 危险区风控：风险从10%降到5%
RISK_RICH_PCT = 0.08   # 富裕区风控：余额>80时风险降到8%

# === v5.2 新增：趋势反转预警 ===
TREND_STATE_FILE = "/root/.openclaw/workspace/.trend_state"
TREND_WARN_COOLDOWN = 300  # 冷却5分钟
WARN_FILE = "/root/.openclaw/workspace/.trend_warn"  # 待发送预警文件
MIN_TRADE_INTERVAL = 30  # 最小下单间隔（秒），防止过度交易
MANUAL_CLOSE_COOLDOWN = 60  # 手动平仓后冷静期（秒），1分钟内禁止同方向新开仓

# === v5.12 新增：交易历史 PnL 归档 ===
TRADE_HISTORY_FILE = "/root/.openclaw/workspace/trades_history.json"
TRADE_HISTORY_MAX = 500  # 最多保留 500 条

def record_trade(symbol, direction, entry, exit_price, qty, reason, pnl_pct=None, leverage=20):
    """归档一笔交易到 trades_history.json
    
    reason: TP1 / TP2 / SL / MANUAL / REVERSE / DRAWDON_PROTECT
    pnl_pct: 胜率盈亏百分比（如 +2.0 表示 +2%）
    """
    try:
        # 读现有历史
        try:
            with open(TRADE_HISTORY_FILE) as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
        
        # 算名义盈亏 = qty × 价差 × leverage（U本位，含手续费估）
        # MANUAL/REVERSE/DRAWDOWN_PROTECT 未知真实平仓价，一律记为0
        if reason in ("MANUAL", "REVERSE", "DRAWDOWN_PROTECT"):
            notional_pnl = 0.0
            pnl_pct = pnl_pct if pnl_pct is not None else 0.0
        elif entry and exit_price and qty:
            if direction == "LONG":
                price_diff = exit_price - entry
            else:
                price_diff = entry - exit_price
            notional_pnl = price_diff * qty * leverage
            if pnl_pct is None:
                pnl_pct = (price_diff / entry) * 100 * leverage
        else:
            notional_pnl = 0.0
            pnl_pct = pnl_pct or 0.0
        
        record = {
            "ts": time.time(),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry, 4) if entry else None,
            "exit": round(exit_price, 4) if exit_price else None,
            "qty": qty,
            "leverage": leverage,
            "pnl_pct": round(pnl_pct, 3),
            "pnl_usdt": round(notional_pnl, 4),
            "reason": reason,
        }
        history.append(record)
        # 限长度
        if len(history) > TRADE_HISTORY_MAX:
            history = history[-TRADE_HISTORY_MAX:]
        with open(TRADE_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
        log(f"📝 归档交易: {symbol} {direction} {reason} 盈亏{pnl_pct:+.2f}% (${notional_pnl:+.4f})")
        return record
    except Exception as e:
        log(f"⚠️ 交易归档失败: {e}")
        return None

def check_stop_loss(symbol, direction, entry, sl, qty, cur):
    """v6.0：预警式自动止损检查
    
    逻辑：
    1. 价格未击穿 SL → 返回 False
    2. 价格击穿 SL 且 SL_AUTO_ENABLED=True：
       - 第一次击穿：推 Telegram 预警，记时间戳，等 SL_AUTO_DELAY 秒
       - 等待期间老板可推送 "/hold" 命令延后（需 Telegram bot 集成）
       - 超过 SL_AUTO_DELAY 未干预 → 自动市价平仓
    """
    if not sl or not entry or not qty:
        return False
    if direction == "LONG" and cur > sl:
        return False
    if direction == "SHORT" and cur < sl:
        return False
    # SL 被击穿
    # 优先级: 环境变量 override > 常量 SL_AUTO_ENABLED
    _auto_enabled = SL_AUTO_ENABLED
    if "SL_AUTO_ENABLED_OVERRIDE" in os.environ:
        _auto_enabled = os.environ["SL_AUTO_ENABLED_OVERRIDE"] == "1"
    if not _auto_enabled:
        # 仅预警模式
        log(f"🚨 SL预警: {symbol} {direction} 现价${cur:.0f} <击穿> SL=${sl:.0f}（仅预警，不自动平）")
        try:
            import subprocess
            subprocess.Popen(
                ["bash", "/root/.openclaw/workspace/sl_alert.sh", symbol, direction, str(sl), str(cur), str(qty)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception:
            pass
        return False
    # 自动止损：检查是否已预警
    pending_file = f"{SL_AUTO_ALERT_FILE}.{symbol}.{direction}"
    now = time.time()
    alert_sent_at = 0.0
    try:
        with open(pending_file) as _f:
            alert_sent_at = float(_f.read().strip() or 0)
    except Exception:
        alert_sent_at = 0.0
    if alert_sent_at == 0.0:
        # 第一次击穿：推预警 + 记时间戳
        log(f"🚨 SL击穿: {symbol} {direction} 现价${cur:.0f} SL=${sl:.0f} 预警{SL_AUTO_DELAY}秒后自动平")
        try:
            with open(pending_file, "w") as _f:
                _f.write(str(now))
        except Exception:
            pass
        try:
            import subprocess
            subprocess.Popen(
                ["bash", "/root/.openclaw/workspace/sl_alert.sh", symbol, direction, str(sl), str(cur), str(qty)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception:
            pass
        return False  # 预警阶段不跳主逻辑
    # 已预警超过 SL_AUTO_DELAY 秒 → 自动平仓
    if (now - alert_sent_at) >= SL_AUTO_DELAY:
        log(f"💀 SL超时自动平仓: {symbol} {direction} 现价${cur:.0f}")
        # 归档 SL 平仓（仍按击穿价计，名义亏损）
        try:
            sl_pct = abs(cur - entry) / entry * 100
            if direction == "SHORT":
                sl_pct = -sl_pct
            record_trade(symbol, direction, entry, cur, qty, "SL", pnl_pct=sl_pct)
        except Exception as _e:
            log(f"⚠️ SL归档失败: {_e}")
        # 清预警文件
        try:
            os.remove(pending_file)
        except Exception:
            pass
        return True
    return False

# === v6.0 增强：Telegram 命令轮询（老板可远程控制机器人）===
_LAST_TG_POLL = [0.0]  # 上次轮询时间（用 list 避免 global）
_TG_OFFSET = [0]       # 增量拉取 offset

def _check_telegram_commands():
    """轮询 Telegram，老板可发送:
        /hold     — 取消本次 SL 预警（保持持仓）
        /enable_sl — 开启 SL 自动执行
        /disable_sl — 关闭 SL 自动执行（仅预警）
        /freeze   — v3.1 全局冻结(不开新仓, 现有持仓仍受SL/TP保护)
        /unfreeze — 解除全局冻结
        /status   — 查询机器人状态
        /stats    — 查看交易统计
        /status   — 查看机器人状态
        /stats    — 查看交易统计
    """
    now = time.time()
    if now - _LAST_TG_POLL[0] < 30:  # 每 30 秒轮询一次
        return
    _LAST_TG_POLL[0] = now
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = str(os.environ.get("OWNER_TELEGRAM_ID", ""))
    if not token or not chat_id:
        return
    try:
        # 30 秒超时 + 长轮询 0 秒
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": _TG_OFFSET[0], "timeout": 0, "allowed_updates": '["message"]'},
            timeout=8
        )
        if not r.ok:
            return
        data = r.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            _TG_OFFSET[0] = max(_TG_OFFSET[0], update["update_id"] + 1)
            msg = update.get("message") or {}
            text = (msg.get("text") or "").strip()
            from_id = str(msg.get("from", {}).get("id", ""))
            if from_id != chat_id:
                continue  # 只接受老板的命令
            if not text.startswith("/"):
                continue
            cmd = text.split()[0].lower().split("@")[0]
            if cmd == "/hold":
                # 删除所有待平仓预警文件（取消所有 SL 预警）
                cleared = 0
                for f in os.listdir("/root/.openclaw/workspace"):
                    if f.startswith(".sl_alert_pending."):
                        try:
                            os.remove(f"/root/.openclaw/workspace/{f}")
                            cleared += 1
                        except Exception:
                            pass
                log(f"📲 老板 /hold：已取消 {cleared} 个 SL 预警")
                # 回复老板
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"✅ /hold 已执行：取消 {cleared} 个 SL 预警，持仓保留"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/enable_sl":
                os.environ["SL_AUTO_ENABLED_OVERRIDE"] = "1"
                log("📲 老板 /enable_sl：SL 自动执行已开启")
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "✅ SL 自动执行已开启（30s 预警后自动平）"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/disable_sl":
                os.environ["SL_AUTO_ENABLED_OVERRIDE"] = "0"
                log("📲 老板 /disable_sl：SL 自动执行已关闭")
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "✅ SL 仅预警（不自动平）"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/freeze":
                # v3.1: 全局冻结命令 - 不开新仓, 不平现有仓
                os.environ["BOT20X_FROZEN"] = "1"
                log("🧊 老板 /freeze：全局冻结(不开新仓, 现有持仓仍受SL保护)")
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "🧊 全局冻结已启动\n不开新仓\n现有持仓保留\nSL/TP仍生效\n/unfreeze 解除冻结"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/unfreeze":
                os.environ.pop("BOT20X_FROZEN", None)
                log("🔥 老板 /unfreeze：解除冻结")
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "🔥 全局冻结已解除，恢复交易"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/status":
                bal = get_balance() or 0
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"🦞 bot20x v6.0\n余额: ${bal:.2f}\n运行正常"},
                        timeout=5
                    )
                except Exception:
                    pass
            elif cmd == "/stats":
                stats = get_trade_stats()
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"📊 交易统计\n{json.dumps(stats, ensure_ascii=False, indent=2)}"},
                        timeout=5
                    )
                except Exception:
                    pass
    except Exception as e:
        if int(time.time()) % 300 < 2:  # 5 分钟才记一次错误
            log(f"⚠️ Telegram轮询错误: {e}")

def get_trade_stats():
    """从归档文件读统计：总交易/胜率/总盈亏/最大单盈/最大单亏
    
    v6.0 优化：胜率 = wins / (wins + losses)，中性笔(MANUAL/REVERSE)不计分母
    """
    try:
        with open(TRADE_HISTORY_FILE) as f:
            history = json.load(f)
        if not history:
            return {"total": 0}
        wins = [r for r in history if r.get("pnl_usdt", 0) > 0]
        losses = [r for r in history if r.get("pnl_usdt", 0) < 0]
        neutral = [r for r in history if r.get("pnl_usdt", 0) == 0]
        total_pnl = sum(r.get("pnl_usdt", 0) for r in history)
        # 真实胜率：仅以有盈亏记录的笔为准
        decisive = len(wins) + len(losses)
        real_win_rate = round(len(wins) / decisive * 100, 2) if decisive > 0 else 0
        return {
            "total": len(history),
            "wins": len(wins),
            "losses": len(losses),
            "neutral": len(neutral),
            "win_rate": real_win_rate,  # 修正后的真实胜率
            "total_pnl": round(total_pnl, 4),
            "avg_win": round(sum(r["pnl_usdt"] for r in wins) / len(wins), 4) if wins else 0,
            "avg_loss": round(sum(r["pnl_usdt"] for r in losses) / len(losses), 4) if losses else 0,
            "max_win": round(max((r["pnl_usdt"] for r in history), default=0), 4),
            "max_loss": round(min((r["pnl_usdt"] for r in history), default=0), 4),
            "first_trade": history[0].get("datetime"),
            "last_trade": history[-1].get("datetime"),
        }
    except Exception as e:
        return {"total": 0, "error": str(e)}

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

def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD指标：趋势动量确认"""
    if len(prices) < slow+1: return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    # Signal line (简化：用MACD的EMA)
    macd_history = [macd_line] * signal
    signal_line = calc_ema(macd_history, signal) if len(macd_history) >= signal else macd_line
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_bollinger(prices, period=20, mult=2):
    """布林带：极端值+支撑阻力"""
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:]) / period
    std = (sum((p - sma) ** 2 for p in prices[-period:]) / period) ** 0.5
    upper = sma + mult * std
    lower = sma - mult * std
    return upper, sma, lower

# ===== v5.9 trading-knowledge 集成模块 =====
# 来源：ClawHub trading-knowledge skill
# 补充能力：K线形态识别 + 信号K检测 + 流动性猎杀 + 支撑阻力强度

def detect_candle_pattern(klines):
    """K线形态识别 (trading-knowledge: 单/多K线)
    klines: [[time, open, high, low, close, vol, ...], ...]
    返回: (pattern_name, score)  正分=看多, 负分=看空
    """
    if len(klines) < 3: return ("none", 0)
    o1, h1, l1, c1 = float(klines[-1][1]), float(klines[-1][2]), float(klines[-1][3]), float(klines[-1][4])
    o2, c2 = float(klines[-2][1]), float(klines[-2][4])
    o3, c3 = float(klines[-3][1]), float(klines[-3][4])
    body = abs(c1 - o1); rng = h1 - l1 if h1 > l1 else 0.0001
    upper_wick = h1 - max(o1, c1); lower_wick = min(o1, c1) - l1

    # Doji (不决/反转前兆)
    if body < rng * 0.1:
        return ("doji", 0)
    # Hammer (看多反转)
    if lower_wick > body * 2 and upper_wick < body * 0.5 and c1 > o1:
        return ("hammer", 1.5)
    # Shooting Star (看空反转)
    if upper_wick > body * 2 and lower_wick < body * 0.5 and c1 < o1:
        return ("shooting_star", -1.5)
    # Bullish Engulfing
    if c1 > o1 and c2 < o2 and c1 > o2 and o1 < c2:
        return ("bull_engulf", 1.5)
    # Bearish Engulfing
    if c1 < o1 and c2 > o2 and c1 < o2 and o1 > c2:
        return ("bear_engulf", -1.5)
    # Morning Star (三K线: 大阴+小实体+大阳)
    if c3 < o3 and abs(c2 - o2) < abs(c3 - o3) * 0.3 and c1 > o1 and c1 > (o3 + c3) / 2:
        return ("morning_star", 2.0)
    # Evening Star (三K线: 大阳+小实体+大阴)
    if c3 > o3 and abs(c2 - o2) < abs(c3 - o3) * 0.3 and c1 < o1 and c1 < (o3 + c3) / 2:
        return ("evening_star", -2.0)
    return ("none", 0)

def detect_volume_anomaly(klines, period=20, threshold=2.5):
    """v_smart_v3.1: 成交量异动检测
    
    当根成交量 > 20期均量×2.5 = 异动(可能是主力进出/事件驱动)
    返回: (is_anomaly, direction)  direction='up'=大量阳烛, 'down'=大量阴烛, None=中性
    """
    if len(klines) < period + 1:
        return (False, None)
    vols = [float(k[5]) for k in klines[-period-1:]]
    avg_vol = sum(vols[:-1]) / period
    cur_vol = vols[-1]
    if cur_vol > avg_vol * threshold:
        # 判断方向
        o = float(klines[-1][1])
        c = float(klines[-1][4])
        if c > o:
            return (True, 'up')  # 大量上涨
        elif c < o:
            return (True, 'down')  # 大量下跌
    return (False, None)


def detect_liquidity_hunt(klines, atr_val):
    """流动性猎杀识别 (trading-knowledge: 流动性猎杀)
    长上下影线 + 快速回归 = 假突破, 应过滤
    返回: (is_hunt, direction)  direction='up'=上影猎杀, 'down'=下影猎杀
    """
    if len(klines) < 2 or atr_val <= 0: return (False, None)
    o, h, l, c = float(klines[-1][1]), float(klines[-1][2]), float(klines[-1][3]), float(klines[-1][4])
    body = abs(c - o); rng = h - l
    upper_wick = h - max(o, c); lower_wick = min(o, c) - l
    # 上影线 > 2倍 ATR 且 实体重小 = 上方假突破
    if upper_wick > atr_val * 1.5 and body < atr_val * 0.3:
        return (True, "up")
    # 下影线 > 2倍 ATR 且 实体重小 = 下方假突破
    if lower_wick > atr_val * 1.5 and body < atr_val * 0.3:
        return (True, "down")
    return (False, None)

def find_support_resistance(prices, window=5):
    """支撑阻力位识别 (trading-knowledge: 支撑阻力)
    找近期价格碰触多次的关键位
    返回: (support, resistance)
    """
    if len(prices) < window * 2 + 1: return (None, None)
    supps, resis = [], []
    for i in range(window, len(prices) - window):
        p = prices[i]
        # 局部低点 = 支撑
        if all(p <= prices[i-j] for j in range(1, window+1)) and all(p <= prices[i+j] for j in range(1, window+1)):
            supps.append(p)
        # 局部高点 = 阻力
        if all(p >= prices[i-j] for j in range(1, window+1)) and all(p >= prices[i+j] for j in range(1, window+1)):
            resis.append(p)
    support = max(supps) if supps else None
    resistance = min(resis) if resis else None
    return (support, resistance)

def sr_test_count(prices, level, tolerance_pct=0.005):
    """支撑阻力位测试次数 (trading-knowledge: 规则3: 测试次数=强度)
    返回: 测试次数 ≥3 = 强位
    """
    if level is None: return 0
    tol = level * tolerance_pct
    count = 0
    for p in prices[-30:]:  # 看近30根
        if abs(p - level) < tol:
            count += 1
    return count

def api_retry_call(func, *args, **kwargs):
    """"带指数退避的API重试机制"""
    delay = API_RETRY_DELAY
    for attempt in range(API_RETRY_MAX):
        try:
            resp = func(*args, **kwargs)
            if isinstance(resp, list):
                return resp
            if not hasattr(resp, 'json'):
                raise ValueError(f"resp不是Response对象: {type(resp).__name__}")
            return resp.json()
        except ValueError:
            raise
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
                       headers={"X-MBX-APIKEY": API_KEY}, timeout=API_TIMEOUT)

def bn_post(endpoint, params):
    ts = str(int(time.time()*1000))
    p = f"{params}&timestamp={ts}"
    sig = hmac.new(SECRET.encode(), p.encode(), hashlib.sha256).hexdigest()
    return api_retry_call(requests.post, f"https://fapi.binance.com{endpoint}?{p}&signature={sig}",
                        headers={"X-MBX-APIKEY": API_KEY}, timeout=API_TIMEOUT)

def get_balance():
    try: return float(bn_get("/fapi/v2/account").get('availableBalance', 0))
    except: return 0

def get_all_positions(symbol):
    positions = {}
    try:
        data = bn_get("/fapi/v2/positionRisk", f"symbol={symbol}")
        if not isinstance(data, list):
            log(f"get_all_positions异常: 返回{type(data).__name__}")
            return positions
        for p in data:
            if not isinstance(p, dict): continue
            amt = float(p.get('positionAmt', 0))
            if amt != 0:
                side = p['positionSide']
                positions[side] = {"dir": "LONG" if amt > 0 else "SHORT",
                                    "qty": abs(amt), "entry": abs(float(p['entryPrice']))}
    except Exception as e:
        log(f"get_all_positions错误: {e}")
    return positions

def startup_self_check():
    """启动自检：验证所有API返回类型正确，不正确则拒绝启动"""
    log("启动自检：验证API响应类型...")
    errors = []
    try:
        bal = get_balance()
        if not isinstance(bal, (int, float)):
            errors.append(f"get_balance返回{type(bal).__name__}")
        else:
            log(f"  ✅ get_balance OK: ${bal:.2f}")
    except Exception as e:
        errors.append(f"get_balance失败: {e}")
    
    try:
        klines = get_klines("BTCUSDT", "1m", 1)
        if not isinstance(klines, list):
            errors.append(f"get_klines返回{type(klines).__name__}")
        else:
            log(f"  ✅ get_klines OK: {len(klines)}条")
    except Exception as e:
        errors.append(f"get_klines失败: {e}")
    
    try:
        pos = get_all_positions("BTCUSDT")
        if not isinstance(pos, dict):
            errors.append(f"get_all_positions返回{type(pos).__name__}")
        else:
            log(f"  ✅ get_all_positions OK")
    except Exception as e:
        errors.append(f"get_all_positions失败: {e}")
    
    if errors:
        log(f"自检失败: {', '.join(errors)}，拒绝启动以防止事故")
        log("请检查网络和API状态后手动重启")
        exit(1)
    log("自检全部通过")

def do_order(symbol, side, posSide, qty):
    # 下单前先记录日志（防止crash后无法追溯）
    log(f"[下单] {symbol} {side} {posSide} qty={qty:.3f} -> 发送中...")
    try:
        params = f"symbol={symbol}&side={side}&positionSide={posSide}&type=MARKET&quantity={qty:.3f}"
        resp = bn_post("/fapi/v1/order", params)
        if not isinstance(resp, dict):
            log(f"[下单失败] {symbol} {side} {posSide} resp类型错误: {type(resp).__name__}")
            return False
        order_id = resp.get("orderId")
        if order_id:
            log(f"[下单成功] {symbol} {side} {posSide} qty={qty:.3f} orderId={order_id}")
            return True
        else:
            log(f"[下单失败] {symbol} {side} {posSide} qty={qty:.3f} resp={resp}")
            return False
    except Exception as e:
        log(f"[下单异常] {symbol} {side} {posSide} qty={qty:.3f} error={e}")
        return False

def get_klines(symbol, interval, limit=100):
    def _fetch():
        r = requests.get(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}', timeout=API_TIMEOUT)
        return r.json()
    return api_retry_call(_fetch)

def get_signal(symbol):
    k4h = get_klines(symbol, "4h", 60)
    k1h = get_klines(symbol, "1h", 100)
    k15m = get_klines(symbol, "15m", 100)
    
    # 数据不足时直接返回None，防止None比较崩溃
    if len(k4h) < 25 or len(k1h) < 25 or len(k15m) < 25:
        log(f"{symbol} K线数据不足，跳过本次信号")
        return None
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
    # None保护：所有sk15/sk1比较统一用sk15_v/sk1_v
    sk15_v = sk15 if sk15 is not None else 50
    sk1_v = sk1 if sk1 is not None else 50
    
    atr = calc_atr(k15m, 14)
    vr = v15m[-1] / (sum(v15m[-20:])/20) if len(v15m) >= 20 else 1
    
    # ===== v_smart: BTC/ETH独立参数 =====
    # 在函数顶部根据symbol动态选择参数，原有逻辑零改动继续走
    global SL_ATR_MULT, TP1_PCT
    if symbol == 'ETHUSDT' and 'ETH_PARAMS' in dir():
        _P = ETH_PARAMS
        SL_ATR_MULT = _P['sl_atr_mult']
        TP1_PCT = _P['tp1_pct']
        _score_normal = _P['score_thresh_normal']
        _score_trending = _P['score_thresh_trending']
        _rsi_long_max = _P['rsi_long_max']
        _rsi_long_min = _P['rsi_long_min']
        _rsi_short_min = _P['rsi_short_min']
        _rsi_short_max = _P['rsi_short_max']
        _adx_min = _P['adx_min']
        _allow_short = _P['allow_short']
        _require_trend = _P['require_trend']
    elif 'BTC_PARAMS' in dir():
        _P = BTC_PARAMS
        SL_ATR_MULT = _P['sl_atr_mult']
        TP1_PCT = _P['tp1_pct']
        _score_normal = _P['score_thresh_normal']
        _score_trending = _P['score_thresh_trending']
        _rsi_long_max = _P['rsi_long_max']
        _rsi_long_min = _P['rsi_long_min']
        _rsi_short_min = _P['rsi_short_min']
        _rsi_short_max = _P['rsi_short_max']
        _adx_min = _P['adx_min']
        _allow_short = _P['allow_short']
        _require_trend = _P['require_trend']
    else:
        _score_normal = SCORE_THRESH_NORMAL
        _score_trending = SCORE_THRESH_TRENDING
        _rsi_long_max = 60
        _rsi_long_min = 35
        _rsi_short_min = 40
        _rsi_short_max = 65
        _adx_min = 20
        _allow_short = True
        _require_trend = True

    # ===== ADX市场环境检测 =====
    adx_val, adx_bullish = calc_adx(k1h, ADX_PERIOD)
    market_trending = adx_val >= ADX_TREND_THRESH
    market_weak = adx_val < ADX_WEAK_THRESH

    # ===== MACD趋势动量检测 =====
    macd_line, macd_signal, macd_hist = calc_macd(c1h)
    macd_bullish = macd_hist > 0  # MACD柱在零轴上方
    macd_bearish = macd_hist < 0  # MACD柱在零轴下方

    # ===== 布林带极端值检测 =====
    bb_upper, bb_mid, bb_lower = calc_bollinger(c15m, 20, 2)
    bb_position = (cur - bb_lower) / (bb_upper - bb_lower) if bb_upper and bb_lower and bb_upper != bb_lower else 0.5  # 价格在布林带位置(0=下轨,1=上轨)

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
    
    # ===== 做空条件（v6.0 平衡）=====
    # r4<15时为超卖警戒，不允许做空（价格可能瞬间反弹）
    oversold_guard = r4 < 15
    # v6.0：放宽门槛、平衡 LONG。允许两种状态：
    #   A) 价格明确在 EMA 下方 + RSI>50 + 趋势市
    #   B) 价格刚跌破 EMA + RSI>45（衡平LONG的<50严格性）
    short_ready_a = (cur < ema1h_20 and r1 > 50 and r4 >= 15 and r4 < 60 and market_trending) and not market_weak
    short_ready_b = (cur < ema1h_20 * 1.005 and r1 > 45 and r4 >= 15 and r4 < 65 and not market_weak)
    short_ready = short_ready_a or short_ready_b
    
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
        if sk15_v < 20: ct_score += 2; ct_reasons.append(f"Stoch15={sk15_v:.0f}<20")
        if sk1_v < 20: ct_score += 1; ct_reasons.append(f"Stoch1={sk1_v:.0f}<20")
        # 底背加分
        if div_bull: ct_score += 1.5; ct_reasons.append("底背")
        if ct_score >= COUNTER_TREND_THRESH:
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
        if sk15_v > 80: ct_score += 2; ct_reasons.append(f"Stoch15={sk15_v:.0f}>80")
        if sk1_v > 80: ct_score += 1; ct_reasons.append(f"Stoch1={sk1_v:.0f}>80")
        if div_bear: ct_score += 1.5; ct_reasons.append("顶背")
        if ct_score >= COUNTER_TREND_THRESH:
            counter_trend_sig = "SHORT"; counter_trend_reasons = ct_reasons
    
    # ===== v5.4 双模式：判断当前属于强趋势还是震荡 =====
    # 强趋势模式：4H+1H EMA共振（趋势跟随，RSI门槛放宽到55）
    # 震荡/逆势模式：趋势不明确或EMA矛盾（原有RSI门槛45）
    STRONG_TREND_MODE = trend_up  # v5.4：只要EMA趋势确认即可，不强制RSI门槛
    
    # ===== 做多 =====
    long_score = 0; long_reasons = []
    
    # 核心条件（强趋势模式：RSI<55即可；震荡模式：RSI<45）
    long_rsi_thresh = 55 if STRONG_TREND_MODE else 45
    if r1 < 40: long_score += 1; long_reasons.append(f"R1={r1:.0f}<40")
    elif r1 < long_rsi_thresh: long_score += (1.0 if STRONG_TREND_MODE else 0.5); long_reasons.append(f"R1={r1:.0f}<{long_rsi_thresh}" + (" [趋势跟随]" if STRONG_TREND_MODE else ""))  # 强趋势模式RSI权重翻倍
    if r4 < 50: long_score += 1; long_reasons.append(f"R4={r4:.0f}<50")
    if r15 < 40: long_score += 1; long_reasons.append(f"R15={r15:.0f}<40")
    if trend_up: long_score += 1; long_reasons.append("趋势↑" + (" [共振]" if STRONG_TREND_MODE else ""))
    
    # StochRSI EMA平滑（减少噪音）
    if sk15_v < 20: long_score += 2; long_reasons.append(f"StochK15={sk15_v:.0f}<20")
    if sk1_v < 20: long_score += 1; long_reasons.append(f"StochK1={sk1_v:.0f}<20")
    
    # 放宽区(40-{long_rsi_thresh})必须有StochRSI极端值才能触发
    stoich_extreme = sk15_v < 20 or sk1_v < 20
    if 40 <= r1 < long_rsi_thresh and not stoich_extreme:
        long_score -= 0.5; long_reasons.append("放宽区无Stoch极端-0.5")
    
    # 加分项
    if div_bull: long_score += 2; long_reasons.append("底背")
    # v5.4: 强趋势模式下成交量要求放宽（趋势确认优先于量能）
    if vr > (1.0 if STRONG_TREND_MODE else 1.5): long_score += 1; long_reasons.append(f"V={vr:.1f}x")
    # v5.5新增：MACD动量确认（做多需MACD柱>0）
    if macd_bullish: long_score += 1; long_reasons.append("MACD多头")
    # v5.5新增：布林带极端值确认（价格接近下轨=超卖）
    if bb_position < 0.2: long_score += 1.5; long_reasons.append(f"BB下轨={bb_position:.0%}")
    elif bb_position < 0.3: long_score += 1; long_reasons.append(f"BB偏低={bb_position:.0%}")

    # ===== v5.9 trading-knowledge 集成：K线形态 + 信号K + 流动性猎杀 =====
    # K线形态识别 (15m 最新一根K线)
    pat_name, pat_score = detect_candle_pattern(k15m)
    if pat_name in ("hammer", "bull_engulf", "morning_star"):
        long_score += pat_score; long_reasons.append(f"K线={pat_name}+{pat_score}")
    elif pat_name in ("shooting_star", "bear_engulf", "evening_star"):
        # 出现空头形态时给long扣分
        long_score += pat_score; long_reasons.append(f"K线={pat_name}{pat_score}")
    elif pat_name == "doji":
        # 不决信号：弱加分（说明趋势可能在反转）
        long_score += 0.3; long_reasons.append("Doji不决")
    # 流动性猎杀过滤（假突破抑制）
    is_hunt, hunt_dir = detect_liquidity_hunt(k15m, atr)
    if is_hunt and hunt_dir == "down":
        # 下影猎杀 = 多头陷阱，加分
        long_score += 1; long_reasons.append("下影猎杀+1")
    elif is_hunt and hunt_dir == "up":
        # 上影猎杀 = 假突破，扣分
        long_score -= 1; long_reasons.append("上影猎杀-1")
    # v3.1: 成交量异动信号(主力进出/事件驱动)
    is_vol_ano, vol_dir = detect_volume_anomaly(k15m, 20, 2.5)
    if is_vol_ano and vol_dir == 'up':
        long_score += 1.5; long_reasons.append("量异动阳+1.5")
    elif is_vol_ano and vol_dir == 'down':
        long_score -= 1; long_reasons.append("量异动阴-1")
    # 支撑阻力位 + 测试次数（强位加分）
    sup1h, res1h = find_support_resistance(c1h)
    if sup1h is not None and cur < sup1h * 1.01 and cur > sup1h * 0.99:
        test_n = sr_test_count(c1h, sup1h)
        if test_n >= 3:
            long_score += 1.5; long_reasons.append(f"强支撑(${sup1h:.0f},测{test_n}次)")
        else:
            long_score += 0.5; long_reasons.append(f"支撑(${sup1h:.0f},测{test_n}次)")

    # 趋势确认（多周期一致性）
    if long_ready: long_score += 1.5; long_reasons.append(f"EMA确认({trend_score:.1f})")
    # v5.4新增：趋势向上时给EMA确认加分（不要求long_ready）
    if trend_up and not long_ready: long_score += 0.5; long_reasons.append(f"EMA向上({trend_score:.1f})")
    
    if long_score >= (_score_normal if not STRONG_TREND_MODE else _score_trending):
        sig = "LONG"; reasons = long_reasons
    elif counter_trend_sig:
        sig = counter_trend_sig; reasons = counter_trend_reasons
    
    # ===== 做空（双模式）=====
    short_score = 0; short_reasons = []
    short_rsi_thresh = 45 if short_ready else 55  # 强趋势模式RSI>45即可；震荡模式RSI>55
    
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
    if sk15_v > 80: short_score += 2; short_reasons.append(f"StochK15={sk15_v:.0f}>80")
    if sk1_v > 80: short_score += 1; short_reasons.append(f"StochK1={sk1_v:.0f}>80")
    
    # 放宽区必须有StochRSI极端值才能触发
    stoich_extreme_short = sk15_v > 80 or sk1_v > 80
    if 30 < r1 <= 40 and not stoich_extreme_short:
        short_score -= 0.5; short_reasons.append("放宽区无Stoch极端-0.5")
    
    # 趋势确认（多周期一致性）
    if short_ready: short_score += 1.5; short_reasons.append(f"EMA确认({trend_score:.1f})")
    
    if div_bear: short_score += 2; short_reasons.append("顶背")
    if vr > 1.5: short_score += 1; short_reasons.append(f"V={vr:.1f}x")
    # v5.5新增：MACD动量确认（做空需MACD柱<0）
    if macd_bearish: short_score += 1; short_reasons.append("MACD空头")
    # v5.5新增：布林带极端值确认（价格接近上轨=超买）
    if bb_position > 0.8: short_score += 1.5; short_reasons.append(f"BB上轨={bb_position:.0%}")
    elif bb_position > 0.7: short_score += 1; short_reasons.append(f"BB偏高={bb_position:.0%}")

    # ===== v5.9 trading-knowledge 集成：K线形态 + 信号K + 流动性猎杀 =====
    pat_name_s, pat_score_s = detect_candle_pattern(k15m)
    if pat_name_s in ("shooting_star", "bear_engulf", "evening_star"):
        short_score += abs(pat_score_s); short_reasons.append(f"K线={pat_name_s}+{abs(pat_score_s):.1f}")
    elif pat_name_s in ("hammer", "bull_engulf", "morning_star"):
        short_score -= abs(pat_score_s); short_reasons.append(f"K线={pat_name_s}-{abs(pat_score_s):.1f}")
    elif pat_name_s == "doji":
        short_score += 0.3; short_reasons.append("Doji不决")
    # 流动性猎杀（反向计分）
    is_hunt_s, hunt_dir_s = detect_liquidity_hunt(k15m, atr)
    if is_hunt_s and hunt_dir_s == "up":
        short_score += 1; short_reasons.append("上影猎杀+1")
    elif is_hunt_s and hunt_dir_s == "down":
        short_score -= 1; short_reasons.append("下影猎杀-1")
    # v3.1: 成交量异动信号
    is_vol_ano_s, vol_dir_s = detect_volume_anomaly(k15m, 20, 2.5)
    if is_vol_ano_s and vol_dir_s == 'down':
        short_score += 1.5; short_reasons.append("量异动阴+1.5")
    elif is_vol_ano_s and vol_dir_s == 'up':
        short_score -= 1; short_reasons.append("量异动阳-1")
    # 阻力位测试（强阻力加分）
    sup1h_s, res1h_s = find_support_resistance(c1h)
    if res1h_s is not None and cur < res1h_s * 1.01 and cur > res1h_s * 0.99:
        test_n_s = sr_test_count(c1h, res1h_s)
        if test_n_s >= 3:
            short_score += 1.5; short_reasons.append(f"强阻力(${res1h_s:.0f},测{test_n_s}次)")
        else:
            short_score += 0.5; short_reasons.append(f"阻力(${res1h_s:.0f},测{test_n_s}次)")

    if short_score >= (_score_normal if not short_ready else _score_trending):
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
        'trend4h_price': trend4h_price,  # 新增：4H EMA趋势方向
        'div': 'bull' if div_bull else ('bear' if div_bear else None),
        'macd_bullish': macd_bullish, 'macd_bearish': macd_bearish,  # v5.5新增
        'bb_position': bb_position,  # v5.5新增：布林带位置
        'sig': sig, 'reasons': reasons,
        'counter_trend': counter_trend_sig is not None,
        'trend_conflict': trend_conflict
    }

def calc_sl(entry, atr, direction, rsi=None):
    """v_smart_v2: ATR动态止损（老板要求策略稳，不能被噪音扫掉）
    
    SL = ATR × SL_ATR_MULT  (跟随波动, 不固定百分比)
    当ATR过小时(震荡市) 使用最低0.8% 防止SL过近
    """
    if not atr or atr <= 0:
        atr = entry * 0.01  # 默认1%ATR fallback
    sl_dist = atr * SL_ATR_MULT
    # 最低止损距离0.8%(防ATR过小导致SL过近)
    sl_dist = max(sl_dist, entry * 0.008)
    return entry - sl_dist if direction == "LONG" else entry + sl_dist

def get_risk_pct(balance):
    """v_smart_v3.1: 5档平滑复利(避免跳变)
    
    余额阶梯:
    - <30U: 2% (危险区, 优先保本)
    - 30-80U: 5% (正常起步)
    - 80-250U: 7% (富余区, 复利开始)
    - 250-500U: 9% (高复利区)
    - >500U: 10% (鲸鱼区, 满滚)
    """
    if balance < RISK_DANGER:
        return RISK_DANGER_PCT
    elif balance < RISK_RICH_MIN:
        return RISK_NORMAL_PCT
    elif balance < RISK_WHALE_MIN:
        return RISK_RICH_PCT
    elif balance < RISK_MEGA_THRESHOLD:
        return RISK_WHALE_PCT
    else:
        return RISK_MEGA_PCT

def get_max_pos_qty(balance, price):
    """v_smart_v3: 单标最大仓位 = 余额×10%(老板要求)
    
    名义仓位最多 10% 余额, 20x杠杆下 = 名义价值 2倍余额, 名义保证金 5%
    例: 余额$100, 价格$62000, 最多 100*10%/62000 = 0.00016 BTC = $10 名义
    """
    return round((balance * MAX_POS_PCT) / price, 3)


def can_dca(s, info, cur, direction):
    """v_smart_v3: 是否可补仓(DCA)检查
    
    补仓条件 (老板明确要求):
    1. 同方向持仓已存在
    2. 已持仓 >= DCA_MIN_HOLD_BARS (12根K线)
    3. 价格逆反原入场价 DCA_TRIGGER_PCT (默认2%)
    4. 补仓次数 < DCA_MAX_TIMES (2次)
    5. 趋势同意 (补仓不逆势)
    """
    if not DCA_ENABLED:
        return False, "DCA未启用"
    if not s.get('pos') or s['pos'] != direction:
        return False, "无同向持仓"
    if s.get('pos') == direction and s.get('entry') is None:
        return False, "无入场价"
    hold_bars = s.get('hold_bars', 0)
    if hold_bars < DCA_MIN_HOLD_BARS:
        return False, f"持仓不足({hold_bars}<{DCA_MIN_HOLD_BARS})"
    dca_count = s.get('dca_count', 0)
    if dca_count >= DCA_MAX_TIMES:
        return False, f"已补仓{dca_count}次达到上限{DCA_MAX_TIMES}"
    entry = s['entry']
    if direction == 'LONG':
        if cur >= entry * (1 - DCA_TRIGGER_PCT):
            return False, f"价格未逆反到{DCA_TRIGGER_PCT*100}%"
    else:
        if cur <= entry * (1 + DCA_TRIGGER_PCT):
            return False, f"价格未逆反到{DCA_TRIGGER_PCT*100}%"
    if DCA_REQUIRE_TREND_AGREE:
        trend_up = info.get('trend_up')
        if direction == 'LONG' and not trend_up:
            return False, "趋势不一致(补仓须顺势)"
        if direction == 'SHORT' and trend_up is not False:
            return False, "趋势不一致"
    return True, "OK"


def execute_dca(s, info, cur, direction, balance):
    """v_smart_v3: 执行补仓
    
    补仓量 = 原仓位的50%
    补仓后: 重新计算加权平均成本 + 重置SL为新成本±ATR×SL_ATR_MULT
    """
    orig_qty = s['qty']
    orig_entry = s['entry']
    add_qty = orig_qty * DCA_ADD_RATIO
    # 精度
    if 'BTC' in s.get('symbol', ''):
        add_qty = round(add_qty, 3)
    else:
        add_qty = round(add_qty, 2)
    if add_qty < 0.001:
        return False, "补仓量太小"
    # 下单
    if do_order(s['symbol'], "BUY" if direction == 'LONG' else "SELL", direction, add_qty):
        new_qty = orig_qty + add_qty
        # 加权平均成本
        new_entry = (orig_entry * orig_qty + cur * add_qty) / new_qty
        new_cost = orig_entry * orig_qty + cur * add_qty
        s['qty'] = new_qty
        s['entry'] = new_entry
        s['cost'] = new_cost
        s['dca_count'] = s.get('dca_count', 0) + 1
        if DCA_SL_RESET:
            atr = info.get('atr', cur * 0.01)
            s['sl'] = calc_sl(new_entry, atr, direction)
            s['atr_at_entry'] = atr
        # 重置TP1/TP2
        s['tp1_done'] = False
        log(f"🔄 {s['symbol']} {direction} 补仓 #{s['dca_count']}: +{add_qty} @ {cur:.2f}, 新均价 {new_entry:.2f}, 新SL {s['sl']:.2f}")
        return True, f"补仓成功, 新均价{new_entry:.2f}"
    return False, "下单失败"


def get_trailing_stop(s, info, direction):
    """v_smart_v3: 追踪止盈(趋势单抓取)
    
    TP1已出后启动追踪, 以最佳价为基准回调TRAIL_DISTANCE止损
    """
    if not s.get('tp1_done'):
        return None
    best = s.get('best')
    if best is None:
        return None
    if direction == 'LONG':
        return best * (1 - TRAIL_DISTANCE)
    else:
        return best * (1 + TRAIL_DISTANCE)


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
    """v_smart_v3.1: 回撤保护(动态阈值)
    
    小账户<50U用20%阈值(更敏感), 大账户用30%
    避免小账户回撤30%已剩个位数仍不触发
    """
    high = get_high_water()
    # 账户清零→自动重置(防止永久回撤循环)
    if balance < 1.0 and high > 1.0:
        log(f"🛡️ 检测到账户清零(当前${balance:.2f})，重置历史高水位${high:.2f}→$0")
        save_high_water(0)
        return False, 0
    # 动态阈值: 小账户更敏感
    if balance < 50:
        threshold = 0.20  # 小账户20%
    else:
        threshold = 0.30  # 大账户30%
    if high > 0 and balance < high * (1 - threshold):
        return True, high
    return False, high

def is_drawdown_locked():
    """检查是否在回撤冷静期内"""
    try:
        with open(DRAWDOWN_LOCK_FILE) as f:
            unlock_time = float(f.read().strip())
        return time.time() < unlock_time
    except:
        return False

def trigger_drawdown_lock():
    """触发回撤冷静期"""
    with open(DRAWDOWN_LOCK_FILE, "w") as f:
        f.write(str(time.time() + DRAWDOWN_LOCK_SECS))
    log(f"回撤冷静期锁定：{DRAWDOWN_LOCK_SECS//60}分钟内禁止开新仓")

def calc_qty(balance, atr, price):
    """v_smart_v3: 仓位计算(简化版)
    
    计算逻辑:
    1. 根据余额阶梯算风险金额
    2. SL距使用 ATR动态
    3. qty = risk / sl_dist
    4. 上限 = MAX_POS_PCT (10%)
    """
    risk_pct = get_risk_pct(balance)
    risk_amount = balance * risk_pct
    
    if atr and atr > 0:
        sl_dist = atr * SL_ATR_MULT
        sl_dist = max(sl_dist, price * 0.008)
    else:
        sl_dist = price * SL_ATR_MULT
    if sl_dist == 0:
        return 0
    
    qty = risk_amount / sl_dist
    max_qty = get_max_pos_qty(balance, price)
    min_qty = max(0.001, round(risk_amount / price, 3))
    return max(min_qty, min(round(qty, 3), max_qty))


# === v_smart_v3 简化: 不使用动态杠杆 ===
# 老板明确说: 设了10%资金使用+20x杠杆+止损止盈，本来就不容易爆
# 动态杠杆频繁调API会触发风控，删除
# 周末降杠杆同上理由删除
# 爆仓检测同上删除（设了SL就不会爆仓）

def main():
    log("="*60)
    log("v_smart_v3.1 精准信号 | 20x固定 | 仓位≤10% | 阶梯追踪SL锁利 | 5档复利平滑 | 成交量异动 | /freeze全局冻结")
    log("="*60)
    
    # 启动自检：验证API响应类型，不正确则拒绝启动
    startup_self_check()
    
    # 记录本次启动（用于安全模式计数）
    crash_count = increment_crash()
    if crash_count >= 3:
        log(f"⚠️ 重启次数较多({crash_count}次/10分钟内)，进入监控模式")
    
    state_files = {
        "BTCUSDT": {"LONG": "/root/.openclaw/workspace/st_btc_long.json", "SHORT": "/root/.openclaw/workspace/st_btc_short.json"},
        "ETHUSDT": {"LONG": "/root/.openclaw/workspace/st_eth_long.json", "SHORT": "/root/.openclaw/workspace/st_eth_short.json"},
    }
    
    while True:
        try:
            bal = get_balance() or 0
            now = time.time()
            hour_utc = int(datetime.utcnow().strftime('%H'))
            
            # 安全模式检查：频繁重启则停止交易
            if not check_crash_safety():
                time.sleep(30)
                continue

            # v5.11: crash_count>=2 推送Telegram告警（每窗口只推1次）
            try:
                _cc, _ = get_crash_count()
                if _cc >= CRASH_ALERT_THRESHOLD:
                    maybe_alert_crash(_cc)
            except Exception:
                pass
            
            # === v6.0 增强：轮询 Telegram 老板 /hold /enable_sl /disable_sl 等命令 ===
            try:
                _check_telegram_commands()
            except Exception as _e:
                if int(time.time()) % 60 < 2:
                    log(f"⚠️ Telegram命令轮询异常: {_e}")
            
            # 回撤冷静期：回撤保护触发后禁止开新仓
            if is_drawdown_locked():
                time.sleep(15)
                continue
            
            # 🛡️ v5.8: 账户清零状态自愈（先于复利风控检查）
            # 如果余额<1.0但high_water>1.0，重置high_water避免永久100%回撤循环
            if bal < 1.0:
                _hw = get_high_water()
                if _hw > 1.0:
                    log(f"🛡️ 启动自愈：账户${bal:.2f}<1.0，重置历史高水位${_hw:.2f}→$0")
                    save_high_water(0)
                    # 同时清冷却期记录，确保充值后立即可用
                    try:
                        import os
                        if os.path.exists(DRAWDOWN_LOCK_FILE):
                            os.remove(DRAWDOWN_LOCK_FILE)
                            log("🔓 已清空回撤冷静期锁")
                        if os.path.exists(DRAWDOWN_COOLDOWN_FILE):
                            os.remove(DRAWDOWN_COOLDOWN_FILE)
                            log("🔓 已清空回撤冷却期记录")
                    except Exception as e:
                        log(f"⚠️ 清锁定文件失败: {e}")
                    high_water = 0
                else:
                    # 余额+高水位都<1，本就无需触发回撤，直接跳过检查
                    # 优化：每5分钟才提示一次，避免狂刷日志吃CPU
                    global _LAST_LOW_BAL_WARN
                    if time.time() - _LAST_LOW_BAL_WARN > 300:
                        log(f"⏸️ 账户余额不足（${bal:.2f}），跳过复利风控检查")
                        _LAST_LOW_BAL_WARN = time.time()
                        # v5.12 新增：低余额主动告老板（带6h冷却）
                        try:
                            import subprocess
                            subprocess.Popen(
                                ["bash", "/root/.openclaw/workspace/low_balance_alert.sh", f"{bal:.2f}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True
                            )
                        except Exception as _e:
                            log(f"⚠️ 低余额告警触发失败: {_e}")
                    # 余额为0时 sleep 60秒而非主循环间隔，避免反复调用API浪费配额
                    time.sleep(60)
                    continue
            
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
                trigger_drawdown_lock()  # 锁定30分钟冷静期
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
                                    # v5.12 归档回撤保护清仓
                                    try:
                                        cur_price = info.get('cur', s.get('entry'))
                                        record_trade(sym, s['pos'], s.get('entry'), cur_price, half_qty, "DRAWDOWN_PROTECT", pnl_pct=0.0)
                                    except Exception as _e:
                                        log(f"⚠️ 回撤保护归档失败: {_e}")
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
                try:
                    pos_data = bn_get("/fapi/v2/positionRisk", f"symbol={sym}")
                    if isinstance(pos_data, list):
                        for p in pos_data:
                            if not isinstance(p, dict): continue
                            amt = abs(float(p.get('positionAmt', 0)))
                            if amt > 0:
                                entry = abs(float(p.get('entryPrice', 0)))
                                price_used = cur_price if cur_price > 0 else entry
                                total_exposure += (amt * price_used) / LEVER
                except Exception as e:
                    log(f"总仓位检查错误: {e}")
            if total_exposure > bal * MAX_TOTAL_EXPOSURE:
                log(f"⚠️ 总仓位超限：${total_exposure:.2f} > ${bal:.2f}×{MAX_TOTAL_EXPOSURE}，暂停新开仓")
                time.sleep(15); continue

            global loss_streak_count, last_loss_time, last_trade_time
            if loss_streak_count >= LOSS_STREAK_LIMIT and (now - last_loss_time) < LOSS_STREAK_PAUSE:
                log(f"熔断中：连续{loss_streak_count}亏，剩余{int(LOSS_STREAK_PAUSE-(now-last_loss_time))/60:.0f}分钟")
                time.sleep(15); continue
            elif loss_streak_count >= LOSS_STREAK_LIMIT:
                loss_streak_count = 0; log("熔断恢复")

            for symbol in ["BTCUSDT", "ETHUSDT"]:
                sf = state_files[symbol]
                info = get_signal(symbol)
                if info is None:
                    time.sleep(15); continue
                # v5.7 防御性字段处理 - 防止字段为None崩溃 (修复112次重启Bug)
                for _k, _v in {'sig': None, 'long_ready': False, 'short_ready': False,
                                'trend_up': False, 'trend_reasons': 'N/A', 'r1': 99, 'r4': 99,
                                'r15': 50, 'sk15': 50, 'vr': 1.0, 'atr': 0, 'cur': 0,
                                'reasons': [], 'trend4h_price': True}.items():
                    if info.get(_k) is None: info[_k] = _v
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
                        # v5.12 归档手动平仓（损益未知，记为0）
                        try:
                            cur_price = info.get('cur', s.get('entry'))
                            record_trade(symbol, direction, s.get('entry'), cur_price, s.get('qty', 0), "MANUAL", pnl_pct=0.0)
                        except Exception as _e:
                            log(f"⚠️ 手动平仓归档失败: {_e}")
                        s["closed"] = now
                        s["manual_close_dir"] = direction  # 记录被手动平仓的方向
                        s["manual_close_time"] = now  # 冷静期起点
                        s["last"] = s.get("last", "closed")
                        s.pop("pos", None)
                        with open(sf_file, "w") as f: json.dump(s, f)
                        continue
                    
                    # ===== v6.0：SL 预警式自动检查 =====
                    if s.get("pos") and pos:
                        _sl = s.get("sl")
                        _entry = s.get("entry")
                        _qty = pos.get("qty", 0)
                        if check_stop_loss(symbol, direction, _entry, _sl, _qty, info.get('cur', 0)):
                            # 击穿 SL 且超时 → 自动市价平仓
                            do_order(symbol, "SELL" if direction == "LONG" else "BUY", direction, _qty)
                            s.clear()
                            with open(sf_file, "w") as f: json.dump(s, f)
                            loss_streak_count += 1
                            last_loss_time = now
                            continue
                    
                    if not pos:
                        sig = info['sig']
                        closed_time = s.get("closed") or (now - OPEN_COOLDOWN - 1)
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
                        
                        # ===== v6.0：恢复反向持仓屏蔽（避免双倍杠杆风险）=====
                        opp_dir = "SHORT" if direction == "LONG" else "LONG"
                        opp_file = sf[opp_dir]
                        try:
                            with open(opp_file) as _f:
                                opp_s = json.load(_f)
                        except Exception:
                            opp_s = {}
                        if opp_s.get("pos"):
                            log(f"{symbol} {direction} 屏蔽 — 反向{opp_dir}持仓中，不逆向开仓")
                            continue
                        
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
                            # ===== v5.4 趋势跟随模式放宽：如果v5.4信号触发（RSI1H<50+趋势向上 或 RSI1H>50+趋势向下），放宽trend_ok要求
                            if not trend_ok:
    
                                # LONG: v5.4趋势跟随触发，EMA确认但RSI4H>60导致long_ready=False → 允许信号
                                if direction == "LONG" and sig == "LONG" and info['r1'] < 55 and info['trend_up']:
                                    trend_ok = True
                                    log(f"{symbol} {direction} v5.4趋势跟随信号(R1={info['r1']:.0f}<50,趋势↑)放宽trend_ok")
                                # SHORT: v5.4趋势跟随触发，EMA确认但RSI4H<40导致short_ready=False → 允许信号
                                elif direction == "SHORT" and sig == "SHORT" and info['r1'] > 50 and not info['trend_up'] and not info.get('trend4h_price', True):
                                    trend_ok = True
                                    log(f"{symbol} {direction} v5.4趋势跟随信号(R1={info['r1']:.0f}>50,趋势↓)放宽trend_ok")
                            if not trend_ok:
                                log(f"{symbol} {direction} 趋势不符 {info['trend_reasons']} 跳过")
                                continue
                        
                        # 检查是否满足下单条件（正常信号或反向信号）
                        sig_ok = (sig == direction) or (reverse_target is not None)
                        reasons = info['reasons'] if sig == direction else [f"反向:{reverse_target}", f"R4={info['r4']:.0f},R1={info['r1']:.0f}"]
                        
                        # 防过度交易：检查最近一次下单时间
                        if last_trade_time and (now - last_trade_time) < MIN_TRADE_INTERVAL:
                            log(f"{symbol} {direction} 防过度交易：距上次下单{MIN_TRADE_INTERVAL}秒内，跳过")
                        # 手动平仓冷静期：10分钟内禁止同方向新开仓（允许反向开仓）
                        elif sig_ok and s.get("manual_close_time") and s.get("manual_close_dir") == direction and (now - s["manual_close_time"]) < MANUAL_CLOSE_COOLDOWN:
                            remaining = int(MANUAL_CLOSE_COOLDOWN - (now - s["manual_close_time"]))
                            log(f"{symbol} {direction} 手动平仓冷静期：还剩{remaining}秒，跳过")
                        elif os.environ.get("BOT20X_FROZEN") == "1":
                            # v3.1: 全局冻结模式 - 跳过开仓
                            log(f"🧊 {symbol} 跳过开仓: 全局冻结模式")
                        elif sig_ok and reasons and bal > MIN_BAL and (now - closed_time) > OPEN_COOLDOWN:
                            actual_dir = reverse_target if reverse_target else direction
                            # === v5.10 反转预警软门槛：RSI超买区不许做多 (防追高) ===
                            if actual_dir == "LONG" and info.get('r1', 50) > 75:
                                log(f"{symbol} LONG 被拒绝：1H RSI={info['r1']:.0f}>75超买区 (v5.10软门槛)")
                                continue
                            # 同理：RSI超卖区<25不许做空 (防杀跌)
                            if actual_dir == "SHORT" and info.get('r1', 50) < 25:
                                log(f"{symbol} SHORT 被拒绝：1H RSI={info['r1']:.0f}<25超卖区 (v5.10软门槛)")
                                continue
                            qty = calc_qty(bal, info['atr'], info['cur'])
                            log(f"{symbol} -> {actual_dir} {reasons} @{info['cur']:.0f} qty:{qty}")
                            if do_order(symbol, "BUY" if actual_dir=="LONG" else "SELL", actual_dir, qty):
                                entry = info['cur']
                                atr = info['atr']
                                # v5.12 归档反手：先记录旧仓平仓（损益未知，记为0）
                                try:
                                    old_state_file = sf[direction]
                                    with open(old_state_file) as _f:
                                        _old_s = json.load(_f)
                                    if _old_s.get("pos"):
                                        _cur_p = info.get('cur', _old_s.get('entry'))
                                        record_trade(symbol, _old_s['pos'], _old_s.get('entry'), _cur_p, _old_s.get('qty', 0), "REVERSE", pnl_pct=0.0)
                                except Exception as _e:
                                    log(f"⚠️ 反手归档失败: {_e}")
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
                                last_trade_time = now
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
                            best_high = max(s.get("best") if s.get("best") is not None else entry, cur)
                            s["best"] = best_high
                            
                            # v_smart_v3: 追踪SL防利润回吐(阶梯式SL上移)
                            # 浮盈每+1% 锁0.3%利润, 防深回吐
                            if pnl >= 0.015:  # 浮盈≥1.5%
                                new_sl = entry * 1.003  # 移至入场价+0.3%(保本锁)
                                if not s.get('sl') or new_sl > s['sl']:
                                    s['sl'] = new_sl
                                    log(f"📊 {symbol} LONG 锁利SL1: 移至 {new_sl:.2f} (保本锁)")
                            if pnl >= 0.025 and not s.get('tp1_done'):  # 浮盈≥2.5%(到TP1)
                                new_sl = entry * 1.012  # 移至入场价+1.2%
                                if new_sl > s['sl']:
                                    s['sl'] = new_sl
                            if s.get('tp1_done') and pnl >= 0.035:  # TP1后浮盈≥3.5%
                                # 追踪SL: 最佳价回撒0.8%
                                trail_sl = best_high * (1 - 0.008)
                                if trail_sl > s['sl']:
                                    s['sl'] = trail_sl
                            if s.get('tp1_done') and pnl >= 0.05:  # 浮盈≥5%趋势单
                                trail_sl = best_high * (1 - 0.012)
                                if trail_sl > s['sl']:
                                    s['sl'] = trail_sl
                            
                            tp1_price = entry * (1 + TP1_PCT)
                            if not s.get("tp1_done") and cur >= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(symbol, "SELL", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                # v5.12 归档 TP1 出半仓（胜）
                                try:
                                    record_trade(symbol, d, entry, cur, half_qty, "TP1", pnl_pct=pnl)
                                except Exception as _e:
                                    log(f"⚠️ TP1归档失败: {_e}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_high * (1 - TP2_BUFFER)
                                if cur <= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(symbol, "SELL", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 剩余出清")
                                    # v5.12 归档 TP2 全平（胜）
                                    try:
                                        record_trade(symbol, d, entry, cur, remaining, "TP2", pnl_pct=pnl)
                                    except Exception as _e:
                                        log(f"⚠️ TP2归档失败: {_e}")
                                    s["tp2_done"] = True
                                    s["last"] = "win"
                                    s.clear()
                                    loss_streak_count = max(0, loss_streak_count-1)
                                    with open(sf_file, "w") as f: json.dump(s, f)
                                    continue
                            
                        else:
                            pnl = (entry - cur) / entry * 100
                            best_low = min(s.get("best") if s.get("best") is not None else entry, cur)
                            s["best"] = best_low
                            
                            # v_smart_v3: 追踪SL防利润回吐(SHORT对称)
                            if pnl >= 0.015:  # 浮盈≥1.5%
                                new_sl = entry * 0.997
                                if not s.get('sl') or new_sl < s['sl']:
                                    s['sl'] = new_sl
                                    log(f"📊 {symbol} SHORT 锁利SL1: 移至 {new_sl:.2f} (保本锁)")
                            if pnl >= 0.025 and not s.get('tp1_done'):
                                new_sl = entry * 0.988
                                if new_sl < s['sl']:
                                    s['sl'] = new_sl
                            if s.get('tp1_done') and pnl >= 0.035:
                                trail_sl = best_low * (1 + 0.008)
                                if trail_sl < s['sl']:
                                    s['sl'] = trail_sl
                            if s.get('tp1_done') and pnl >= 0.05:
                                trail_sl = best_low * (1 + 0.012)
                                if trail_sl < s['sl']:
                                    s['sl'] = trail_sl
                            
                            tp1_price = entry * (1 - TP1_PCT)
                            if not s.get("tp1_done") and cur <= tp1_price:
                                half_qty = round(pos["qty"] / 2, 3)
                                do_order(symbol, "BUY", d, half_qty)
                                log(f"{symbol} {d} TP1 @{cur:.0f} ({pnl:+.1f}%) 出{half_qty}")
                                # v5.12 归档 TP1 出半仓（胜）
                                try:
                                    record_trade(symbol, d, entry, cur, half_qty, "TP1", pnl_pct=pnl)
                                except Exception as _e:
                                    log(f"⚠️ TP1归档失败: {_e}")
                                s["tp1_done"] = True
                                s["win_streak"] = s.get("win_streak", 0) + 1
                            
                            if pnl >= TP2_TRIGGER * 100 and not s.get("tp2_done"):
                                trail_tp = best_low * (1 + TP2_BUFFER)
                                if cur >= trail_tp:
                                    remaining = round(pos["qty"] * 0.5, 3)
                                    do_order(symbol, "BUY", d, remaining)
                                    log(f"{symbol} {d} TP2 @{cur:.0f} ({pnl:+.1f}%) 剩余出清")
                                    # v5.12 归档 TP2 全平（胜）
                                    try:
                                        record_trade(symbol, d, entry, cur, remaining, "TP2", pnl_pct=pnl)
                                    except Exception as _e:
                                        log(f"⚠️ TP2归档失败: {_e}")
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
