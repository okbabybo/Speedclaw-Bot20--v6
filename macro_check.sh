#!/bin/bash
# 宏观新闻速查脚本 - 每次行情分析前必运行
# 用法: bash macro_check.sh

echo "🌍 宏观新闻速查 - $(date '+%Y-%m-%d %H:%M')"
echo "========================================"

echo ""
echo "📰 Yahoo Finance 宏观要闻:"
curl -s -A "Mozilla/5.0" "https://finance.yahoo.com/news/rssindex" 2>/dev/null | grep -o '<title>[^<]*</title>' | head -8 | sed 's/<title>//g; s/<\/title>//g'

echo ""
echo "📰 Cointelegraph 加密新闻:"
curl -s -A "Mozilla/5.0" "https://cointelegraph.com/rss" 2>/dev/null | grep -o '<title>[^<]*</title>' | head -8 | sed 's/<title>//g; s/<\/title>//g'

echo ""
echo "📊 市场情绪 (market-sentiment skill):"
cd ~/.agents/skills/market-sentiment/scripts 2>/dev/null && python3 sentiment_analyzer.py 2>/dev/null | tail -6

echo ""
echo "========================================"
echo "检查完成"
