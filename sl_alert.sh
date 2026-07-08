#!/bin/bash
# ============================================================
# SpeedClaw Bot - 止损击穿告警推送（Telegram）
# ============================================================
# 触发逻辑：SL 被击穿，主动推老板
# 推送内容：标的+方向+现价+止损价+仓位+剩余时间
# 防骚扰：同一次预警只推1次（主代码控制）
# ============================================================

set -euo pipefail

WORKDIR="/root/.openclaw/workspace"
TG_TOKEN="${TELEGRAM_TOKEN:-}"
TG_CHAT_ID="${OWNER_TELEGRAM_ID:-7204010604}"

SYMBOL="${1:-?}"
DIRECTION="${2:-?}"
SL_PRICE="${3:-0}"
CUR_PRICE="${4:-0}"
QTY="${5:-0}"

if [[ -z "$TG_TOKEN" && -f "${WORKDIR}/.env" ]]; then
    TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' "${WORKDIR}/.env" | head -1 | cut -d'=' -f2-)
fi

if [[ -z "$TG_TOKEN" ]]; then
    echo "[sl_alert] TELEGRAM_TOKEN 未配置，跳过推送"
    exit 0
fi

send_tg() {
    local msg="$1"
    local url="https://api.telegram.org/bot${TG_TOKEN}/sendMessage"
    curl -s -m 8 -X POST "$url" \
        -d "chat_id=${TG_CHAT_ID}" \
        -d "parse_mode=Markdown" \
        -d "disable_web_page_preview=true" \
        --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
}

TIME_NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')

MSG="🚨 *SL 击穿预警*

*标的*: ${SYMBOL}
*方向*: ${DIRECTION}
*现价*: \$${CUR_PRICE}
*止损价*: \$${SL_PRICE}
*仓位*: ${QTY}

*30秒后自动平仓*（如需延后请回复 /hold）

_触发时间_: ${TIME_NOW}"

send_tg "$MSG"
echo "[sl_alert] 已推送：${SYMBOL} ${DIRECTION} \$${CUR_PRICE} <击穿> \$${SL_PRICE}"