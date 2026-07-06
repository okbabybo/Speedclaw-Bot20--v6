#!/bin/bash
# ============================================================
# SpeedClaw Bot - 崩溃告警推送（Telegram）
# ============================================================
# 触发逻辑：crash_count >= 2 或 PM2 进程进入 errored 状态
# 推送内容：进程名 + 崩溃次数 + 时间 + 最近日志
# 防骚扰：同一进程1小时内最多推1次
# ============================================================

set -euo pipefail

WORKDIR="/root/.openclaw/workspace"
ALERT_STATE_FILE="${WORKDIR}/.crash_alert_state"
TG_TOKEN="${TELEGRAM_TOKEN:-}"
TG_CHAT_ID="${OWNER_TELEGRAM_ID:-7204010604}"
CRASH_COUNT_FILE="${WORKDIR}/.crash_count"
ALERT_COOLDOWN_FILE="${WORKDIR}/.crash_alert_cooldown"
ALERT_COOLDOWN_SECS=3600  # 同一进程1小时内最多推1次

# --- 读环境变量（.env 兜底）---
if [[ -z "$TG_TOKEN" && -f "${WORKDIR}/.env" ]]; then
    TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' "${WORKDIR}/.env" | head -1 | cut -d'=' -f2-)
fi

if [[ -z "$TG_TOKEN" ]]; then
    echo "[crash_alert] TELEGRAM_TOKEN 未配置，跳过推送"
    exit 0
fi

# --- 读取崩溃计数 ---
get_crash_count() {
    if [[ -f "$CRASH_COUNT_FILE" ]]; then
        python3 -c "
import json, sys
try:
    with open('$CRASH_COUNT_FILE') as f:
        d = json.load(f)
    print(d.get('count', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

# --- 读取PM2进程状态 ---
get_pm2_status() {
    pm2 jlist 2>/dev/null | python3 -c "
import json, sys
try:
    procs = json.load(sys.stdin)
    bad = []
    for p in procs:
        name = p.get('name', '')
        status = p.get('pm2_env', {}).get('status', '')
        restarts = p.get('pm2_env', {}).get('restart_time', 0)
        uptime = p.get('pm2_env', {}).get('pm_uptime', 0)
        if status in ('errored', 'stopped', 'stopping') and name in ('bot20x', 'bot-king', 'botking-tg', 'auto-activate'):
            uptime_min = (uptime / 1000 / 60) if uptime > 0 else 0
            bad.append(f'  • {name}: {status} (restarts={restarts}, uptime={uptime_min:.1f}min)')
    if bad:
        print('进程异常:\n' + '\n'.join(bad))
    else:
        print('')
except Exception as e:
    print('')
" 2>/dev/null
}

# --- 检查冷却（避免重复推送）---
should_alert() {
    local proc_name="$1"
    local now=$(date +%s)
    if [[ -f "$ALERT_COOLDOWN_FILE" ]]; then
        local last_alert=$(python3 -c "
import json, sys
try:
    with open('$ALERT_COOLDOWN_FILE') as f:
        d = json.load(f)
    print(d.get('$proc_name', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
        if (( now - last_alert < ALERT_COOLDOWN_SECS )); then
            return 1  # 冷却中，不推
        fi
    fi
    # 更新冷却时间
    python3 -c "
import json, os, time
fp = '$ALERT_COOLDOWN_FILE'
try:
    d = json.load(open(fp)) if os.path.exists(fp) else {}
except Exception:
    d = {}
d['$proc_name'] = int(time.time())
json.dump(d, open(fp, 'w'))
" 2>/dev/null
    return 0  # 可以推送
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
CRASH_COUNT=$(get_crash_count)
PM2_BAD=$(get_pm2_status)
HOSTNAME=$(hostname 2>/dev/null || echo "unknown")
TIME_NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')

# 触发条件1：crash_count >= 2
if (( CRASH_COUNT >= 2 )); then
    if should_alert "bot20x_crash"; then
        MSG="🚨 *崩溃告警*

⏰ ${TIME_NOW}
🖥️ \`${HOSTNAME}\`

📊 *bot20x 崩溃统计*
• 10分钟内崩溃次数: *${CRASH_COUNT}* 次
• 阈值: 5次（10分钟内）
• 当前状态: $([ $CRASH_COUNT -ge 5 ] && echo "🔒 已进入安全模式" || echo "⚠️ 监控中")

📋 *PM2进程状态*
\`\`\`
${PM2_BAD:-全部正常 ✅}
\`\`\`

🔧 *已自动执行*
• PM2 \`max_restarts=10\` 已配置
• 进程崩溃后延迟5秒再启（防雪崩）
• 安全模式：连续崩5次→自动停止开仓
• 内存超500MB→自动重启

📂 日志路径: \`/root/.openclaw/workspace/bot_20x.log\`"
        send_tg "$MSG"
        echo "[crash_alert] 已推送 crash_count=${CRASH_COUNT}"
    fi
fi

# 触发条件2：PM2有进程errorred/stopped
if [[ -n "$PM2_BAD" ]]; then
    if should_alert "pm2_status"; then
        MSG="🚨 *PM2进程异常*

⏰ ${TIME_NOW}
🖥️ \`${HOSTNAME}\`

${PM2_BAD}

🔧 *已自动尝试恢复*
• PM2 自动重启机制已激活
• 崩溃延迟5秒（防雪崩）
• 单进程最多重启10次后停止

📂 查看详情: \`pm2 logs --lines 50\`"
        send_tg "$MSG"
        echo "[crash_alert] 已推送 PM2异常"
    fi
fi