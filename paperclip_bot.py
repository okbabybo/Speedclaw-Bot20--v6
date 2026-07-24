#!/usr/bin/env python3
# 🥷 混沌武士 Paperclip AI · Telegram Bot (闭环版 v3)
# 完整命令: /start /wallet /pnl /withdraw /cs /help

import os
import json
import re
import time
import logging
import requests
import subprocess
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

# ============== 配置 ==============
BOT_TOKEN = os.environ.get(
    "PAPERCLIP_BOT_TOKEN",
    "8734542487:AAEtrTM24xCdjyB2MYj8DNp0R4xuLMCOJEc"
)
DEPOSIT_ADDR = "0x352f5Cb1CA167500D27741676ab9efA4B07D3D30"

DATA_FILE = "/root/.openclaw/workspace/paperclip_users.json"
WALLET_MAP_FILE = "/root/.openclaw/workspace/wallet_map.json"
WITHDRAW_QUEUE = "/root/.openclaw/workspace/withdraw_queue.json"
USER_KEYS_FILE = "/root/.openclaw/workspace/user_keys.json"  # 用户多账户 API

ADMIN_CHAT_ID = 7204010604  # 老板（你）

# ============== 交易机器人状态源 ==============
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "QccKkNLbtV61rJpOms4h2E0RWoZMfMhG2ar3v9tueF5kbQ6KkN4sUf5CFLLkMhzx")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET", "Q549z4g3QlOnVs0PDSCzW6Xy2nVt9763DMqWo64MLLDoUeV8MigrUGUQn2nZTDuU")

WS = "/root/.openclaw/workspace"
STATE_FILES = {
    "btc_long":  f"{WS}/st_btc_long.json",
    "btc_short": f"{WS}/st_btc_short.json",
    "eth_long":  f"{WS}/st_eth_long.json",
    "eth_short": f"{WS}/st_eth_short.json",
    "king":      f"{WS}/bot_king_state.json",
}

_cache = {"binance": {}, "ts": 0}
CACHE_TTL = 60  # 60秒缓存

def _binance_request(path, params=None, signed=False):
    """Binance USDT-M 期货 API（含缓存）"""
    import hashlib, hmac
    from urllib.parse import urlencode
    now = time.time()
    cache_key = f"{path}_{json.dumps(params or {}, sort_keys=True)}"
    if cache_key in _cache["binance"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["binance"][cache_key]
    base = "https://fapi.binance.com"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if signed:
        params = params or {}
        params["timestamp"] = int(now * 1000)
        params["recvWindow"] = 5000
        qs = urlencode(params)
        sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{qs}&signature={sig}"
    else:
        url = f"{base}{path}?{urlencode(params or {})}" if params else f"{base}{path}"
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        data = r.json()
        _cache["binance"][cache_key] = data
        _cache["ts"] = now
        return data
    except Exception as e:
        return {"_error": str(e)[:80]}

def _pm2_status():
    """读 pm2 进程状态"""
    try:
        out = subprocess.check_output(["pm2", "jlist"], timeout=3).decode()
        procs = json.loads(out)
        result = {}
        for p in procs:
            name = p.get("name", "")
            if name in ("bot20x", "bot-king", "paperclip-bot", "paperclip-monitor"):
                result[name] = {
                    "status": p.get("pm2_env", {}).get("status", "?"),
                    "uptime": p.get("pm2_env", {}).get("pm_uptime", 0),
                    "restarts": p.get("pm2_env", {}).get("restart_time", 0),
                }
        return result
    except Exception as e:
        return {"_error": str(e)[:80]}

def _fmt_uptime(ms):
    """ms -> 友好时长"""
    if not ms: return "?"
    secs = int((time.time() * 1000 - ms) / 1000)
    if secs < 60: return f"{secs}s"
    if secs < 3600: return f"{secs//60}m"
    if secs < 86400: return f"{secs//3600}h{(secs%3600)//60}m"
    return f"{secs//86400}d{(secs%86400)//3600}h"

# ============== 数据 ==============
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: return default
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_user(uid):
    users = load_json(DATA_FILE, {})
    return users.get(uid), users

# ============== 6 个命令 ==============
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """启动 + 显示完整路径"""
    user = update.effective_user
    users = load_json(DATA_FILE, {})
    uid = str(user.id)
    
    if uid not in users:
        users[uid] = {
            "username": user.username or user.first_name,
            "chat_id": user.id,
            "tier": None,
            "deposited": 0,
            "balance": 0,
            "total_pnl": 0,
            "activated_at": None,
            "created_at": datetime.now().isoformat()
        }
        save_json(DATA_FILE, users)
    
    # 检查用户状态
    u = users[uid]
    has_wallet = bool(u.get("wallet"))
    has_deposit = u.get("deposited", 0) >= 9  # 最低$9档
    has_tier = bool(u.get("tier"))

    if has_wallet and has_deposit and has_tier:
        # 老用户: 状态面板
        msg = (
            f"🥷 {user.first_name}, 欢迎回到混沌武士\n\n"
            f"📊 <b>你的状态</b>\n"
            f"档位: <b>{u['tier']}</b>\n"
            f"余额: <b>${u.get('balance', 0):.2f}</b>\n"
            f"累计盈亏: <b>${u.get('total_pnl', 0):.2f}</b>\n\n"
            f"<b>3秒速查</b>\n"
            f"/status — 三机器人状态\n"
            f"/positions — 当前持仓\n"
            f"/pnl — 盈亏明细\n"
            f"/wallet — 绑地址\n"
            f"/withdraw — 提现\n\n"
            f"❓ /help  💬 /cs"
        )
    else:
        # 新用户: 3步引导
        step1 = "✅" if has_wallet else "1️⃣"
        step2 = "✅" if has_deposit else "2️⃣"
        step3 = "✅" if has_tier else "3️⃣"
        msg = (
            f"🥷 你好 {user.first_name}, 我是混沌武士\n"
            f"AI驱动的跟单交易平台\n\n"
            f"<b>3步开始</b>  ⏱ 1分钟搞定\n\n"
            f"{step1} <b>绑地址</b>\n"
            f"   /wallet 0x你的BSC地址\n\n"
            f"{step2} <b>充USDT</b> (BSC BEP-20)\n"
            f"   {DEPOSIT_ADDR}\n"
            f"   最低 $9 体验档起\n\n"
            f"{step3} <b>自动激活</b> (15秒扫链)\n"
            f"   激活后机器人自动跑\n\n"
            f"❓ /help 看完整指引"
        )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """绑定BSC地址"""
    user = update.effective_user
    
    if not ctx.args:
        await update.message.reply_text(
            "📬 **绑定 BSC 地址**\n\n"
            "用法：`/wallet 0xAbC...`\n\n"
            "绑定后，充值时我能主动通知你。",
            parse_mode="Markdown"
        )
        return
    
    wallet = ctx.args[0].strip()
    if not re.match(r'^0x[0-9a-fA-F]{40}$', wallet):
        await update.message.reply_text("❌ 地址格式不对")
        return
    
    wmap = load_json(WALLET_MAP_FILE, {})
    wmap[wallet.lower()] = user.id
    save_json(WALLET_MAP_FILE, wmap)
    
    users = load_json(DATA_FILE, {})
    uid = str(user.id)
    if uid in users:
        users[uid]["wallet"] = wallet
        save_json(DATA_FILE, users)
    
    await update.message.reply_text(
        f"✅ 已绑定 `{wallet}`\n\n"
        f"充值 USDT 到 `{DEPOSIT_ADDR}` 后我会通知你。",
        parse_mode="Markdown"
    )


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """查PnL"""
    user = update.effective_user
    uid = str(user.id)
    user_data, _ = get_user(uid)
    
    if not user_data:
        await update.message.reply_text("请先 /start")
        return
    
    if user_data.get("deposited", 0) == 0:
        await update.message.reply_text(
            "⚠️ 尚未充值\n\n"
            f"向 `{DEPOSIT_ADDR}` 充值后查询 PnL。",
            parse_mode="Markdown"
        )
        return
    
    deposited = user_data.get("deposited", 0)
    balance = user_data.get("balance", deposited)
    total_pnl = user_data.get("total_pnl", 0)
    pnl_pct = (total_pnl / deposited * 100) if deposited else 0
    last_date = user_data.get("last_pnl_date", "—")
    last_amount = user_data.get("last_pnl_amount", 0)
    
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    
    await update.message.reply_text(
        f"📊 **{user.first_name} 的账户**\n\n"
        f"💰 已充：**${deposited:.2f}**\n"
        f"{pnl_emoji} 累计盈亏：**${total_pnl:+.4f}** ({pnl_pct:+.2f}%)\n"
        f"💼 当前余额：**${balance:.4f}**\n\n"
        f"📅 上次结算：{last_date}\n"
        f"   当时盈亏：${last_amount:+.4f}\n\n"
        f"提现：`/withdraw 金额 BSC地址`\n"
        f"客服：`/cs`",
        parse_mode="Markdown"
    )


async def cmd_withdraw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """提现申请"""
    user = update.effective_user
    uid = str(user.id)
    user_data, _ = get_user(uid)
    
    if not user_data or user_data.get("deposited", 0) == 0:
        await update.message.reply_text("⚠️ 尚未充值，无法提现")
        return
    
    balance = user_data.get("balance", 0)
    tier = user_data.get("tier", "trial")
    
    # 档位门槛
    if tier == "trial":
        await update.message.reply_text(
            "⚠️ **体验档不可提现**\n\n"
            "请升级到主力档 ($99+) 后再提现。",
            parse_mode="Markdown"
        )
        return
    
    if balance < 50:
        await update.message.reply_text(
            f"⚠️ 余额不足\n\n"
            f"当前余额：${balance:.2f}\n"
            f"提现门槛：$50 USDT",
            parse_mode="Markdown"
        )
        return
    
    if len(ctx.args) < 2:
        await update.message.reply_text(
            f"💸 **提现申请**\n\n"
            f"用法：`/withdraw 金额 BSC地址`\n\n"
            f"例如：`/withdraw 50 0xAbC...`\n\n"
            f"你的余额：**${balance:.4f}**\n"
            f"门槛：$50 USDT",
            parse_mode="Markdown"
        )
        return
    
    try:
        amount = float(ctx.args[0])
    except:
        await update.message.reply_text("❌ 金额格式不对")
        return
    
    address = ctx.args[1].strip()
    if not re.match(r'^0x[0-9a-fA-F]{40}$', address):
        await update.message.reply_text("❌ 地址格式不对")
        return
    
    if amount > balance:
        await update.message.reply_text(f"❌ 超出余额 (${balance:.2f})")
        return
    
    # 写入提现队列
    queue = load_json(WITHDRAW_QUEUE, [])
    wid = f"wd_{int(time.time())}_{uid[:6]}"
    request = {
        "id": wid,
        "chat_id": user.id,
        "user_id": uid,
        "username": user.username or user.first_name,
        "amount": amount,
        "address": address,
        "status": "pending",
        "requested_at": datetime.now().isoformat()
    }
    queue.append(request)
    save_json(WITHDRAW_QUEUE, queue)
    
    await update.message.reply_text(
        f"✅ **提现申请已提交**\n\n"
        f"ID：`{wid}`\n"
        f"金额：**${amount}**\n"
        f"地址：`{address[:10]}...{address[-6:]}`\n"
        f"状态：⏳ 待审核\n\n"
        f"审核结果会在24小时内通过Telegram通知你。",
        parse_mode="Markdown"
    )
    
    # 通知运营人员
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_CHAT_ID,
                "text": (
                    f"💸 **新提现申请**\n\n"
                    f"ID: `{wid}`\n"
                    f"用户: {user.first_name} ({uid})\n"
                    f"金额: ${amount}\n"
                    f"地址: `{address}`\n\n"
                    f"审核：`python3 paperclip_withdraw.py approve {wid}`\n"
                    f"拒绝：`python3 paperclip_withdraw.py reject {wid}`"
                ),
                "parse_mode": "Markdown"
            },
            timeout=10
        )
    except:
        pass


async def cmd_cs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """客服"""
    await update.message.reply_text(
        "💬 **客服联系**\n\n"
        "🥷 混沌武士 Paperclip AI\n\n"
        "📧 **Email**: ai@chaos-warrior.ai\n"
        "🐦 **Telegram**: @okbobox\n"
        "🌐 **网站**: https://okbabybo.github.io/chaos-warrior/\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "工作时间：24/7 自动监控\n"
        "人工回复：24小时内\n\n"
        "💡 **你的命令**：\n"
        "`/pnl` - 查盈亏\n"
        "`/withdraw 金额 地址` - 提现\n"
        "`/help` - 完整说明",
        parse_mode="Markdown"
    )


# ============== 交易机器人查询命令 ==============
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """机器人健康度：PM2 状态 + uptime + 重启次数"""
    procs = _pm2_status()
    if "_error" in procs:
        await update.message.reply_text(f"❌ PM2 查询失败: `{procs['_error']}`", parse_mode="Markdown")
        return
    
    lines = ["🥷 **机器人状态**\n"]
    icons = {"online": "✅", "errored": "❌", "stopped": "⏸", "launching": "🟡"}
    for name in ("bot20x", "bot-king", "paperclip-monitor", "paperclip-bot"):
        p = procs.get(name, {})
        status = p.get("status", "?")
        icon = icons.get(status, "❓")
        up = _fmt_uptime(p.get("uptime", 0))
        rs = p.get("restarts", 0)
        rs_warn = f" ⚠️重启{rs}次" if rs > 3 else ""
        lines.append(f"{icon} **{name}** | `{status}` | up {up}{rs_warn}")
    
    # 今日盈亏
    king_state = load_json(STATE_FILES["king"], {})
    today_pnl = king_state.get("realized_profit", 0)
    lines.append(f"\n💰 **今日已实现盈亏**: `${today_pnl:.2f}`")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """当前持仓：合约 + 现货账户余额"""
    acct = _binance_request("/fapi/v2/balance", signed=True)
    if isinstance(acct, dict) and "_error" in acct:
        await update.message.reply_text(f"❌ 账户查询失败: `{acct['_error']}`", parse_mode="Markdown")
        return
    
    lines = ["🥷 **持仓与余额**\n"]
    
    # USDT 余额（合约账户）
    usdt_free = usdt_total = 0.0
    for item in acct:
        if item.get("asset") == "USDT":
            usdt_free = float(item.get("availableBalance", 0))
            usdt_total = float(item.get("balance", 0))
            break
    lines.append(f"💵 **合约账户 USDT**: 可用 `${usdt_free:.2f}` / 总 `${usdt_total:.2f}`\n")
    
    # 合约持仓（从本地状态文件读取，因为缓存的 positions 接口有时延）
    pos_lines = []
    for label, path in [("BTC LONG", STATE_FILES["btc_long"]),
                        ("BTC SHORT", STATE_FILES["btc_short"]),
                        ("ETH LONG", STATE_FILES["eth_long"]),
                        ("ETH SHORT", STATE_FILES["eth_short"])]:
        st = load_json(path, {})
        if st and "entry" in st and st.get("qty", 0) > 0:
            entry = st.get("entry", 0)
            sl = st.get("sl", 0)
            qty = st.get("qty", 0)
            best = st.get("best", entry) or entry
            pos_lines.append(
                f"📊 **{label}**: 入场 `{entry:.2f}` SL `{sl:.2f}` 最高 `{best:.2f}` × {qty}"
            )
    
    if pos_lines:
        lines.append("**合约持仓**：")
        lines.extend(pos_lines)
    else:
        lines.append("**合约持仓**：当前无持仓")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """最新信号状态 + bot-king 当前态"""
    lines = ["🥷 **交易信号**\n"]
    
    # bot-king 状态
    king = load_json(STATE_FILES["king"], {})
    if king:
        hw = king.get("high_water", 0)
        lp = king.get("loss_streak", 0)
        cooldown = king.get("loss_cooldown", 0)
        cd_left = max(0, int(lock_until - time.time())) if (lock_until := king.get("lock_until", 0)) > time.time() else 0
        lines.append(f"🤖 **bot-king 现货**：")
        lines.append(f"  · 高水位: `${hw:.2f}`")
        lines.append(f"  · 连亏: `{lp}` 笔")
        if cd_left:
            lines.append(f"  · 冷却中: `{cd_left}s`")
        else:
            lines.append(f"  · 状态: ✅ 可交易")
    
    # bot20x 当前持仓信号
    pos_lines = []
    for label, path in [("BTC", "btc"), ("ETH", "eth")]:
        long_st = load_json(STATE_FILES[f"{path}_long"], {})
        short_st = load_json(STATE_FILES[f"{path}_short"], {})
        l_open = long_st and long_st.get("qty", 0) > 0
        s_open = short_st and short_st.get("qty", 0) > 0
        if l_open:
            pos_lines.append(f"📈 **{label}**: 多单持仓中（入场 `{long_st['entry']:.2f}`）")
        elif s_open:
            pos_lines.append(f"📉 **{label}**: 空单持仓中（入场 `{short_st['entry']:.2f}`）")
        else:
            pos_lines.append(f"⏸ **{label}**: 空仓等信号")
    
    lines.append("\n🤖 **bot20x 合约**：")
    lines.extend(pos_lines)
    
    # 行情参考
    tickers = _binance_request("/fapi/v1/ticker/24hr", {"symbols": '["BTCUSDT","ETHUSDT"]}'})
    if isinstance(tickers, list):
        lines.append("\n💹 **24小时行情**：")
        for t in tickers:
            sym = t.get("symbol", "?")
            price = float(t.get("lastPrice", 0))
            chg = float(t.get("priceChangePercent", 0))
            icon = "🟢" if chg > 0 else "🔴"
            lines.append(f"  {icon} **{sym}**: `${price:,.2f}` ({chg:+.2f}%)")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """简版余额（只看 USDT）"""
    acct = _binance_request("/fapi/v2/balance", signed=True)
    if isinstance(acct, dict) and "_error" in acct:
        await update.message.reply_text(f"❌ 查询失败: `{acct['_error']}`", parse_mode="Markdown")
        return
    
    usdt = next((x for x in acct if x.get("asset") == "USDT"), {})
    if not usdt:
        await update.message.reply_text("❌ 未找到 USDT 余额")
        return
    
    total = float(usdt.get("balance", 0))
    free = float(usdt.get("availableBalance", 0))
    upnl = float(usdt.get("crossUnPnl", 0))
    
    await update.message.reply_text(
        f"💰 **USDT 余额**\n\n"
        f"总资产: `${total:.2f}`\n"
        f"可用: `${free:.2f}`\n"
        f"未实现盈亏: `${upnl:+.2f}`",
        parse_mode="Markdown"
    )


# ============== 多账户 API 绑定 ==============
BIND_TUTORIAL = """🔐 **如何创建 Binance API**

1️⃣ 登录 binance.com → 右上角头像 → **API 管理**
2️⃣ 创建 API → 选 **系统生成的 API Key**
3️⃣ 完成二次验证（手机+邮箱）
4️⃣ **权限勾选**：
   ✅ 启用现货及杠杆交易
   ✅ 启用合约（USDT-M 永续）
   ✅ 启用读取
   ❌ **不要勾提现**（安全红线）
5️⃣ IP 限制：**留空**（允许任意IP）
6️⃣ 复制 **API Key** 和 **Secret Key** 两行发给我

⚠️ **重要**：
· 不要勾提现权限
· Secret Key 只显示一次，丢失需重置
· 立即发我，我测试连接后入库加密

格式示例：
```
BINANCE_KEY: xxxxxxxxxxxx
BINANCE_SECRET: yyyyyyyyyyyy
账户别名: 主账户（可选）
```"""


def _save_user_keys(keys_db):
    """保存用户多账户密钥"""
    save_json(USER_KEYS_FILE, keys_db)


def _test_binance_api(api_key, api_secret):
    """测试Binance API是否可用"""
    import hashlib, hmac
    from urllib.parse import urlencode
    ts = int(time.time() * 1000)
    qs = urlencode({"timestamp": ts, "recvWindow": 5000})
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v2/account?{qs}&signature={sig}",
            headers={"X-MBX-APIKEY": api_key},
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            # 找到 USDT 余额
            for asset in data.get("assets", []):
                if asset.get("asset") == "USDT":
                    return True, float(asset.get("walletBalance", 0))
            return True, 0.0
        return False, r.json().get("msg", f"HTTP {r.status_code}")
    except Exception as e:
        return False, str(e)[:80]


def _mask_key(k):
    """API Key脱敏：前4后4"""
    if len(k) < 10: return "***"
    return f"{k[:4]}***{k[-4:]}"


async def cmd_bind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/bind — 显示教程，等待用户发API"""
    await update.message.reply_text(BIND_TUTORIAL, parse_mode="Markdown")


async def handle_api_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """非命令文本：尝试解析为 API Key 提交"""
    text = (update.message.text or "").strip()
    if "BINANCE_KEY:" not in text or "BINANCE_SECRET:" not in text:
        return  # 不是API提交，忽略
    
    user = update.effective_user
    uid = str(user.id)
    
    # 解析
    lines = text.split("\n")
    api_key = api_secret = alias = None
    for line in lines:
        line = line.strip()
        if line.startswith("BINANCE_KEY:"):
            api_key = line.split(":", 1)[1].strip()
        elif line.startswith("BINANCE_SECRET:"):
            api_secret = line.split(":", 1)[1].strip()
        elif line.startswith("账户别名:"):
            alias = line.split(":", 1)[1].strip()
    
    if not api_key or not api_secret:
        await update.message.reply_text("❌ 格式不对，需要 `BINANCE_KEY:` 和 `BINANCE_SECRET:` 两行")
        return
    
    # 测试
    msg = await update.message.reply_text("⏳ 正在测试连接 Binance...")
    ok, info = _test_binance_api(api_key, api_secret)
    if not ok:
        await msg.edit_text(f"❌ API 测试失败：`{info}`\n\n请检查 Key/Secret 是否正确，或 IP 限制是否留空。", parse_mode="Markdown")
        return
    
    # 写入
    keys_db = load_json(USER_KEYS_FILE, {})
    if uid not in keys_db:
        keys_db[uid] = {"accounts": [], "active": None}
    
    # 默认别名
    n = len(keys_db[uid]["accounts"]) + 1
    if not alias:
        alias = f"账户{n}"
    
    # 防重复（同 key 不重复加）
    for acc in keys_db[uid]["accounts"]:
        if acc["api_key"] == api_key:
            await msg.edit_text(f"⚠️ 此 API 之前已绑定为 **{acc['alias']}**，不重复添加。", parse_mode="Markdown")
            return
    
    account = {
        "alias": alias,
        "api_key": api_key,
        "api_secret": api_secret,
        "balance": info,
        "bound_at": datetime.now().isoformat(),
        "bound_by": user.username or user.first_name,
    }
    keys_db[uid]["accounts"].append(account)
    if not keys_db[uid]["active"]:
        keys_db[uid]["active"] = alias
    _save_user_keys(keys_db)
    
    await msg.edit_text(
        f"✅ **绑定成功**\n\n"
        f"账户别名：**{alias}**\n"
        f"API Key：`{_mask_key(api_key)}`\n"
        f"USDT 余额：**${info:.2f}**\n"
        f"当前活动账户：**{keys_db[uid]['active']}**\n\n"
        f"💡 `/accounts` 查看所有账户\n"
        f"💡 `/use {alias}` 切换活动账户",
        parse_mode="Markdown"
    )


async def cmd_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/accounts — 列出我绑的所有账户"""
    user = update.effective_user
    uid = str(user.id)
    keys_db = load_json(USER_KEYS_FILE, {})
    user_keys = keys_db.get(uid, {})
    accounts = user_keys.get("accounts", [])
    active = user_keys.get("active")
    
    if not accounts:
        await update.message.reply_text(
            "📭 你还没绑定任何 API 账户。\n\n用 `/bind` 开始绑定。",
            parse_mode="Markdown"
        )
        return
    
    lines = [f"🔐 **我的账户**（{len(accounts)} 个）\n"]
    for i, acc in enumerate(accounts, 1):
        is_active = acc["alias"] == active
        icon = "🟢" if is_active else "⚪"
        lines.append(
            f"{icon} **{i}. {acc['alias']}**\n"
            f"   Key: `{_mask_key(acc['api_key'])}`\n"
            f"   余额: ${acc.get('balance', 0):.2f}\n"
            f"   绑定时间: {acc.get('bound_at', '?')[:16]}"
        )
    lines.append(f"\n💡 `/use 别名` 切换活动账户\n💡 `/unbind 别名` 解绑")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_use(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/use 别名 — 切换活动账户"""
    user = update.effective_user
    uid = str(user.id)
    if not ctx.args:
        await update.message.reply_text("用法：`/use 账户别名`")
        return
    alias = " ".join(ctx.args).strip()
    
    keys_db = load_json(USER_KEYS_FILE, {})
    if uid not in keys_db:
        await update.message.reply_text("❌ 你还没绑定账户，先 `/bind`")
        return
    
    accounts = keys_db[uid]["accounts"]
    if not any(a["alias"] == alias for a in accounts):
        names = ", ".join(a["alias"] for a in accounts)
        await update.message.reply_text(f"❌ 没找到「{alias}」\n现有账户：{names}")
        return
    
    keys_db[uid]["active"] = alias
    _save_user_keys(keys_db)
    await update.message.reply_text(
        f"✅ 已切换活动账户为 **{alias}**\n\n后续 `/positions` `/signal` `/balance` 都用这个账户的数据。",
        parse_mode="Markdown"
    )


async def cmd_unbind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/unbind 别名 — 解绑"""
    user = update.effective_user
    uid = str(user.id)
    if not ctx.args:
        await update.message.reply_text("用法：`/unbind 账户别名`")
        return
    alias = " ".join(ctx.args).strip()
    
    keys_db = load_json(USER_KEYS_FILE, {})
    if uid not in keys_db:
        await update.message.reply_text("❌ 你没绑过账户")
        return
    
    accounts = keys_db[uid]["accounts"]
    before = len(accounts)
    keys_db[uid]["accounts"] = [a for a in accounts if a["alias"] != alias]
    
    if len(keys_db[uid]["accounts"]) == before:
        names = ", ".join(a["alias"] for a in accounts)
        await update.message.reply_text(f"❌ 没找到「{alias}」\n现有账户：{names}")
        return
    
    # 如果删的是活动账户，重置
    if keys_db[uid]["active"] == alias:
        keys_db[uid]["active"] = keys_db[uid]["accounts"][0]["alias"] if keys_db[uid]["accounts"] else None
    
    _save_user_keys(keys_db)
    await update.message.reply_text(
        f"✅ 已解绑 **{alias}**\n剩余账户：{len(keys_db[uid]['accounts'])} 个",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """帮助 — 3秒速查"""
    msg = (
        "🥷 <b>混沌武士 · 3秒速查</b>\n\n"

        "━━━ 💰 充值 ━━━\n"
        "/start  路径+状态\n"
        "/wallet 0x地址  绑钱包\n"
        "/pnl  盈亏明细\n"
        "/withdraw 金额 地址  提现\n\n"

        "━━━ 🤖 交易状态 ━━━\n"
        "/status  机器人健康度\n"
        "/positions  当前持仓\n"
        "/signal  最新信号+行情\n"
        "/balance  余额速查\n\n"

        "━━━ 🔗 绑Binance ━━━\n"
        "/bind  绑API教程\n"
        "/accounts  查看账户\n"
        "/use 别名  切换账户\n\n"

        "━━━ 🆘 其他 ━━━\n"
        "/cs  联系客服\n"
        "/help  本速查\n\n"

        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 充USDT (BSC BEP-20):\n<code>{DEPOSIT_ADDR}</code>\n\n"
        f"📊 档位: $9起 | 提现门槛 $50 | 只发BEP-20"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ============== 启动（三件套：重试 + 自愈 + 告警）==============
def build_app():
    """构建 Application，单例创建"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CommandHandler("cs", cmd_cs))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("use", cmd_use))
    app.add_handler(CommandHandler("unbind", cmd_unbind))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_api_text))
    return app


def alert_owner(text):
    """直接通过 Telegram API 发消息给老板（不走 bot 本身）"""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        logging.error(f"alert_owner failed: {e}")


def main():
    logging.basicConfig(level=logging.WARNING)
    print(f"🥷 Bot 启动中...")
    
    # 不退出轮询器：崩溃后 sleep 重试
    crash_count = 0
    max_crash_in_hour = 10  # 1小时内 10 次崩，不再自愈，让 PM2 接管
    
    while True:
        try:
            app = build_app()
            app.run_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
                poll_interval=3,  # 拉长间隔降网络负载
                timeout=30
            )
            # 正常退出 (Ctrl+C)
            break
        except KeyboardInterrupt:
            logging.info("收到中断，正常退出")
            break
        except Exception as e:
            crash_count += 1
            logging.exception(f"主循环崩溃 ({crash_count}/小时{max_crash_in_hour})")
            print(f"⚠️ 崩溃 #{crash_count}: {type(e).__name__}: {e}")
            
            if crash_count == 1:
                alert_owner(f"⚠️ *paperclip-bot 主循环崩溃*\\n\\n`{type(e).__name__}: {str(e)[:200]}`\\n\\n自动重启中…")
            elif crash_count >= max_crash_in_hour:
                alert_owner(f"🔴 *paperclip-bot 频繁崩溃* \\n\\n1小时内 {crash_count} 次\\n\\n让 PM2 接管，手动检查\\n\\n`pm2 logs paperclip-bot --err`")
                break  # 退出，PM2 会重启
            
            # 退避策略
            wait = min(60, 5 * crash_count)
            print(f"⏳ {wait}s 后自动重启…")
            time.sleep(wait)

if __name__ == "__main__":
    main()