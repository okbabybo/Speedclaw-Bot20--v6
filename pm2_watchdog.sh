#!/bin/bash
# PM2 watchdog v2: 三层保护
# 1. PM2 daemon 不在 → 拉起 PM2
# 2. PM2 daemon 在但进程列表空 → resurrect
# 3. PM2 在且进程在 → 健康检查 (online 状态, uptime 合理)

LOG=/root/.openclaw/workspace/pm2_resurrect.log

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
}

# 1) PM2 daemon 状态
if ! pm2 ping >/dev/null 2>&1; then
    log "WARN PM2 daemon 不在, 启动 PM2"
    pm2 kill >/dev/null 2>&1
    sleep 2
fi

# 2) 进程列表是否完整 (bot20x + bot-king 都在)
_pm2_out=$(pm2 list 2>/dev/null)
_has_bot20x=$(echo "$_pm2_out" | grep -c "bot20x" || true)
_has_botking=$(echo "$_pm2_out" | grep -c "bot-king" || true)
_has_guard=$(echo "$_pm2_out" | grep -c "pm2-guard" || true)

if [ "$_has_bot20x" -lt 1 ] || [ "$_has_botking" -lt 1 ]; then
    log "WARN PM2 进程表不完整 (bot20x=$_has_bot20x bot-king=$_has_botking), resurrecting"
    pm2 resurrect >> "$LOG" 2>&1
    sleep 3
    _pm2_out=$(pm2 list 2>/dev/null)
fi

# 3) 健康检查 - bot20x 必须 online
_bot20x_status=$(echo "$_pm2_out" | awk '/bot20x/ {for(i=1;i<=NF;i++) if($i=="online"||$i=="stopped"||$i=="errored"||$i=="launching") print $i}' | head -1)
if [ "$_bot20x_status" != "online" ]; then
    log "WARN bot20x status=$_bot20x_status, restart"
    pm2 restart bot20x >> "$LOG" 2>&1
fi

# 4) bot-king 健康
_botking_status=$(echo "$_pm2_out" | awk '/bot-king/ {for(i=1;i<=NF;i++) if($i=="online"||$i=="stopped"||$i=="errored"||$i=="launching") print $i}' | head -1)
if [ "$_botking_status" != "online" ]; then
    log "WARN bot-king status=$_botking_status, restart"
    pm2 restart bot-king >> "$LOG" 2>&1
fi

# 5) pm2-guard 必须存在并 online (它是60秒级抢救)
_guard_status=$(echo "$_pm2_out" | awk '/pm2-guard/ {for(i=1;i<=NF;i++) if($i=="online"||$i=="stopped"||$i=="errored") print $i}' | head -1)
if [ "$_guard_status" != "online" ]; then
    log "WARN pm2-guard status=$_guard_status, restart"
    pm2 restart pm2-guard >> "$LOG" 2>&1
fi

log "OK PM2 健康 (bot20x=$_bot20x_status bot-king=$_botking_status guard=$_guard_status)"