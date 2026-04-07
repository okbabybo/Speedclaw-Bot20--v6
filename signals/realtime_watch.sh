#!/bin/bash
# 混沌龙虾实时盯盘 - 后台运行
# 只推送大机会，不推小波动

API_KEY="be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET="508989F295B579CA787D85F500B9C02E"
PASSPHRASE="Fjh872330@"
FEISHU_ID="ou_ce5a94cfca07b266414b003138b8f1f8"

send_alert() {
    local title="$1"
    local msg="$2"
    curl -s "https://open.feishu.cn/open-apis/bot/v2/hook/$FEISHU_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"🦞 $title\n\n$msg\"}}" 2>/dev/null
}

get_btc_price() {
    curl -s "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP" | python3 -c "
import sys,json
d=json.load(sys.stdin)['data'][0]
print(d['last'])
"
}

get_eth_price() {
    curl -s "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP" | python3 -c "
import sys,json
d=json.load(sys.stdin)['data'][0]
print(d['last'])
"
}

LAST_BTC=""
LAST_ETH=""
COOLDOWN=300  # 5分钟冷却，避免重复推送

echo "[实时盯盘] 启动..."

while true; do
    BTC=$(get_btc_price 2>/dev/null)
    ETH=$(get_eth_price 2>/dev/null)
    
    if [[ -z "$BTC" || -z "$ETH" ]]; then
        sleep 10
        continue
    fi
    
    echo "[$(date '+%H:%M:%S')] BTC: $BTC | ETH: $ETH"
    
    # ========== 大机会判断 ==========
    
    # BTC 大机会1: 突破70,000 = 多头信号
    if [[ ! -z "$LAST_BTC" ]]; then
        BTC_INT=$(echo "$BTC" | cut -d'.' -f1)
        LAST_BTC_INT=$(echo "$LAST_BTC" | cut -d'.' -f1)
        
        # BTC突破70,000
        if [[ $LAST_BTC_INT -lt 70000 ]] && [[ $BTC_INT -ge 70000 ]]; then
            echo "🚨 BTC突破70,000!"
            send_alert "BTC突破70000 ⚠️" "BTC刚刚突破70000，多头信号！\n当前价: $BTC\n目标: 70500-71000\n止损: 69500"
        fi
        
        # BTC跌破68,500 = 空头信号
        if [[ $LAST_BTC_INT -gt 68500 ]] && [[ $BTC_INT -le 68500 ]]; then
            echo "🚨 BTC跌破68,500!"
            send_alert "BTC跌破68500 ⚠️" "BTC跌破68500，空头信号！\n当前价: $BTC\n观察: 是否企稳"
        fi
        
        # BTC回到69,000-69200 = 入场做多位
        if [[ $BTC_INT -ge 69000 ]] && [[ $BTC_INT -le 69200 ]]; then
            echo "🍖 BTC入场区间!"
            send_alert "BTC入场区间 🍖" "BTC回到69000-69200，可入场做多\n当前价: $BTC\n止损: 68800\n目标: 70000"
        fi
    fi
    
    # ETH 大机会
    if [[ ! -z "$LAST_ETH" ]]; then
        ETH_INT=$(echo "$ETH" | cut -d'.' -f1)
        LAST_ETH_INT=$(echo "$LAST_ETH" | cut -d'.' -f1)
        
        # ETH突破2150
        if [[ $LAST_ETH_INT -lt 2150 ]] && [[ $ETH_INT -ge 2150 ]]; then
            echo "🚨 ETH突破2150!"
            send_alert "ETH突破2150 ⚠️" "ETH突破2150，多头信号！\n当前价: $ETH\n目标: 2180-2200"
        fi
        
        # ETH跌破2080
        if [[ $LAST_ETH_INT -gt 2080 ]] && [[ $ETH_INT -le 2080 ]]; then
            echo "🚨 ETH跌破2080!"
            send_alert "ETH跌破2080 ⚠️" "ETH跌破2080，空头信号！\n当前价: $ETH"
        fi
        
        # ETH回到2100-2110
        if [[ $ETH_INT -ge 2100 ]] && [[ $ETH_INT -le 2110 ]]; then
            echo "🍖 ETH入场区间!"
            send_alert "ETH入场区间 🍖" "ETH回到2100-2110，可入场做多\n当前价: $ETH\n止损: 2080\n目标: 2150"
        fi
    fi
    
    LAST_BTC="$BTC"
    LAST_ETH="$ETH"
    
    sleep 15  # 每15秒检查一次
done
