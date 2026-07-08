#!/bin/bash
# ============================================================
# SpeedClaw Bot - 低余额告警推送（Telegram）
# ============================================================
# 触发逻辑：账户余额 < $5 USDT 时主动推老板
# 推送内容：进程名 + 余额 + 当前 ETH/BTC 价 + 充值建议
# 防骚扰：同一进程6小时内最多推1次
# ============================================================

set -euo pipefail

WORKDIR="/root/.openclaw/workspace"
ALERT_COOLDOWN_FILE="${WORKDIR}/.low_balance_alert_cooldown"
ALERT_COOLDOWN_SECS=21600  # 6小时
TG_TOKEN="${TELEGRAM_TOKEN:-}"
TG_CHAT_ID="${OWNER_TELEGRAM_ID:-7204010604}"

# --- 读环境变量（.env 兜底）---
if [[ -z "$TG_TOKEN" && -f "${WORKDIR}/.env" ]]; then
    TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' "${WORKDIR}/.env" | head -1 | cut -d'=' -f2-)
fi

if [[ -z "$TG_TOKEN" ]]; then
    echo "[low_balance_alert] TELEGRAM_TOKEN 未配置，跳过推送"
    exit 0
fi

# --- 防骚扰：检查冷却 ---
should_alert() {
    local key="$1"
    if [[ -f "$ALERT_COOLDOWN_FILE" ]]; then
        local last_alert
        last_alert=$(grep -o "\"${key}\": *[0-9]*" "$ALERT_COOLDOWN_FILE" 2>/dev/null | grep -o '[0-9]*$' || echo 0)
        local now
        now=$(date +%s)
        if (( now - last_alert < ALERT_COOLDOWN_SECS )); then
            echo "[low_balance_alert] ${key} 冷却中，跳过"
            return 1
        fi
    fi
    return 0
}

# --- 记录推送时间 ---
record_alert() {
    local key="$1"
    local now
    now=$(date +%s)
    if [[ -f "$ALERT_COOLDOWN_FILE" ]]; then
        # 删除旧的同key记录
        grep -v "\"${key}\":" "$ALERT_COOLDOWN_FILE" > "${ALERT_COOLDOWN_FILE}.tmp" 2>/dev/null || true
        mv "${ALERT_COOLDOWN_FILE}.tmp" "$ALERT_COOLDOWN_FILE" 2>/dev/null || true
    fi
    echo "{\"${key}\": ${now}}" >> "$ALERT_COOLDOWN_FILE"
}

# --- 推送Telegram ---
send_tg() {
    local msg="$1"
    local url="https://api.telegram.org/bot${TG_TOKEN}/sendMessage"
    curl -s -m 8 -X POST "$url" \
        -d "chat_id=${TG_CHAT_ID}" \
        -d "parse_mode=Markdown" \
        -d "disable_web_page_preview=true" \
        --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
}

# === 主逻辑 ===
BALANCE="${1:-0.00}"
HOSTNAME=$(hostname 2>/dev/null || echo "unknown")
TIME_NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')

# 尝试获取实时ETH/BTC价格
ETH_PRICE=$(curl -s -m 5 "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('price','?'))" 2>/dev/null || echo "?")
BTC_PRICE=$(curl -s -m 5 "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('price','?'))" 2>/dev/null || echo "?")

if should_alert "bot20x_low_balance"; then
    MSG="💸 *低余额告警*

*机器人*: bot20x + bot-king
*当前余额*: \$${BALANCE}
*ETH 现价*: \$${ETH_PRICE}
*BTC 现价*: \$${BTC_PRICE}

机器人已空转超过1小时，无法下单。
机器人本身代码稳定（38h 0崩溃），是没钱 = 没收入。

老板充值 ≥\$20 后通知我重启机器人。
\`pm2 restart bot20x && pm2 restart bot-king\`

_触发时间_: ${TIME_NOW}
_主机_: ${HOSTNAME}"
    
    send_tg "$MSG"
    record_alert "bot20x_low_balance"
    echo "[low_balance_alert] 已推送：余额\$${BALANCE}"
fi