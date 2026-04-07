#!/usr/bin/env python3
"""
🦞 混沌龙虾 - 综合盯盘分析脚本
结合：OKX市场数据 + BBC/CoinDesk新闻 + 主观判断 + 进化日志
"""

import urllib.request
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ========== 基础配置 ==========
WORKSPACE = Path("/root/.openclaw/workspace")
NEWS_LOG = WORKSPACE / "memory" / "trading-journal.md"

OKX_BTC_URL = "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP"
OKX_ETH_URL = "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP"

BBC_RSS = "https://feeds.bbci.co.uk/news/business/rss.xml"
COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"

# ========== 数据抓取 ==========

def fetch_json(url):
    """抓取JSON数据"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None

def fetch_text(url, max_chars=3000):
    """抓取文本/HTML"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            if isinstance(data, bytes):
                data = data.decode('utf-8', errors='ignore')
            return data[:max_chars]
    except Exception as e:
        return ""

def extract_text_from_html(html):
    """从HTML提取纯文本"""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_rss_items(xml_text, max_items=3):
    """解析RSS XML，提取标题和描述"""
    items = []
    item_pattern = r'<item>(.*?)</item>'
    title_pattern = r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>'
    desc_pattern = r'<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>'
    
    for match in re.finditer(item_pattern, xml_text, re.DOTALL):
        item_xml = match.group(1)
        title_match = re.search(title_pattern, item_xml, re.DOTALL)
        desc_match = re.search(desc_pattern, item_xml, re.DOTALL)
        
        title = ""
        if title_match:
            title = title_match.group(1) or title_match.group(2) or ""
        
        desc = ""
        if desc_match:
            desc_text = desc_match.group(1) or desc_match.group(2) or ""
            desc = extract_text_from_html(desc_text)[:150]
        
        if title:
            items.append({"title": title.strip(), "desc": desc.strip()})
        if len(items) >= max_items:
            break
    
    return items

# ========== 主观判断逻辑 ==========

def analyze_market(btc_data, eth_data):
    """综合分析，给出主观判断"""
    
    btc_last = float(btc_data['last'])
    btc_high24h = float(btc_data['high24h'])
    btc_low24h = float(btc_data['low24h'])
    btc_open24h = float(btc_data['open24h'])
    
    eth_last = float(eth_data['last'])
    eth_high24h = float(eth_data['high24h'])
    eth_low24h = float(eth_data['low24h'])
    eth_open24h = float(eth_data['open24h'])
    
    # BTC 分析
    btc_pct = (btc_last - btc_open24h) / btc_open24h * 100
    btc_range_pct = (btc_high24h - btc_low24h) / btc_low24h * 100
    
    # ETH 分析
    eth_pct = (eth_last - eth_open24h) / eth_open24h * 100
    eth_range_pct = (eth_high24h - eth_low24h) / eth_low24h * 100
    
    # 主观判断
    signals = {
        "btc": {
            "price": btc_last,
            "change_pct": btc_pct,
            "signal": "neutral",
            "direction": "观望",
            "reason": []
        },
        "eth": {
            "price": eth_last,
            "change_pct": eth_pct,
            "signal": "neutral", 
            "direction": "观望",
            "reason": []
        }
    }
    
    # BTC 判断
    if btc_pct > 0.5:
        signals["btc"]["signal"] = "bullish"
        signals["btc"]["direction"] = "偏多"
        signals["btc"]["reason"].append("24h上涨正向")
    elif btc_pct < -0.5:
        signals["btc"]["signal"] = "bearish"
        signals["btc"]["direction"] = "偏空"
        signals["btc"]["reason"].append("24h下跌负向")
    
    if btc_range_pct > 2.5:
        signals["btc"]["reason"].append(f"振幅{btc_range_pct:.1f}%较大，波动剧烈")
    
    # ETH 判断
    if eth_pct > 0.5:
        signals["eth"]["signal"] = "bullish"
        signals["eth"]["direction"] = "偏多"
        signals["eth"]["reason"].append("24h上涨正向")
    elif eth_pct < -0.5:
        signals["eth"]["signal"] = "bearish"
        signals["eth"]["direction"] = "偏空"
        signals["eth"]["reason"].append("24h下跌负向")
    
    # 我的主观逻辑：BTC和ETH的相对强弱
    if btc_pct > eth_pct + 0.3:
        signals["eth"]["reason"].append("BTC比ETH强，资金轮动偏BTC")
    elif eth_pct > btc_pct + 0.3:
        signals["btc"]["reason"].append("ETH比BTC强，资金轮动偏ETH")
    
    return signals

# ========== 格式化输出 ==========

def format_report(btc_data, eth_data, bbc_news, coindesk_news, signals):
    """生成完整报告"""
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC+8")
    
    btc_last = signals["btc"]["price"]
    eth_last = signals["eth"]["price"]
    
    report = f"""
🦞 混沌龙虾综合分析报告 | {now}
{'='*50}

📊 OKX实时数据

BTC-USDT-SWAP
• 最新价: ${signals["btc"]["price"]:,.1f} USDT
• 24h涨跌: {signals["btc"]["change_pct"]:+.2f}%
• 24h区间: ${float(btc_data['low24h']):,.1f} - ${float(btc_data['high24h']):,.1f}

ETH-USDT-SWAP  
• 最新价: ${signals["eth"]["price"]:,.1f} USDT
• 24h涨跌: {signals["eth"]["change_pct"]:+.2f}%
• 24h区间: ${float(eth_data['low24h']):,.1f} - ${float(eth_data['high24h']):,.1f}

📰 国际宏观 (BBC Business)
"""
    
    for i, item in enumerate(bbc_news[:2]):
        report += f"\n{i+1}. {item['title']}"
        if item['desc']:
            report += f"\n   {item['desc'][:80]}..."
    
    report += f"""

📈 加密市场动态 (CoinDesk)
"""
    
    for i, item in enumerate(coindesk_news[:3]):
        report += f"\n{i+1}. {item['title']}"
        if item['desc']:
            report += f"\n   {item['desc'][:80]}..."
    
    report += f"""

🧠 主观判断

BTC: {signals["btc"]["direction"]} | 信号强度: {signals["btc"]["signal"]}
"""
    for r in signals["btc"]["reason"]:
        report += f"  • {r}\n"
    
    report += f"""
ETH: {signals["eth"]["direction"]} | 信号强度: {signals["eth"]["signal"]}
"""
    for r in signals["eth"]["reason"]:
        report += f"  • {r}\n"
    
    report += f"""
{'='*50}
"""
    
    return report

# ========== 主流程 ==========

def main():
    print("🦞 混沌龙虾综合分析...", flush=True)
    
    # 1. 抓取市场数据
    print("  → 获取OKX数据...", flush=True)
    btc_data = fetch_json(OKX_BTC_URL)
    eth_data = fetch_json(OKX_ETH_URL)
    
    if not btc_data or not eth_data:
        print("  ❌ OKX数据获取失败", flush=True)
        sys.exit(1)
    
    # 2. 抓取新闻
    print("  → 获取BBC新闻...", flush=True)
    bbc_xml = fetch_text(BBC_RSS)
    bbc_news = parse_rss_items(bbc_xml, max_items=3) if bbc_xml else []
    
    print("  → 获取CoinDesk新闻...", flush=True)
    coindesk_xml = fetch_text(COINDESK_RSS)
    coindesk_news = parse_rss_items(coindesk_xml, max_items=3) if coindesk_xml else []
    
    # 3. 主观分析
    print("  → 主观判断分析...", flush=True)
    signals = analyze_market(
        btc_data['data'][0],
        eth_data['data'][0]
    )
    
    # 4. 生成报告
    report = format_report(
        btc_data['data'][0],
        eth_data['data'][0],
        bbc_news,
        coindesk_news,
        signals
    )
    
    print(report)
    
    # 5. 保存到日志
    NEWS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(NEWS_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n\n## 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M UTC+8')}\n")
        f.write(report)
    
    print(f"\n✅ 报告已保存到: {NEWS_LOG}", flush=True)

if __name__ == "__main__":
    main()
