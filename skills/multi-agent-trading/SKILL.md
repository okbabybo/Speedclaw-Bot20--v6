---
name: multi-agent-trading
description: "混沌龙虾多Agent交易系统 | 多Agent协作分析框架。分工：技术Agent、资金Agent、多头Agent、空头Agent、裁判Agent。触发词：分析行情、多Agent报告、实时汇报、团队协作分析。"
---

# 混沌龙虾多Agent交易系统

## 核心功能

多Agent协作分析，比单一判断更全面。

## Agent架构

| Agent | 职责 |
|-------|------|
| 技术Agent | K线结构、RSI、趋势判断 |
| 资金Agent | OI、资金费率、盘口分析 |
| 多头Agent | 收集做多理由 |
| 空头Agent | 收集做空理由 |
| 裁判Agent | 综合辩论，最终决策 |

## 执行命令

```bash
cd /root/.openclaw/workspace/signals && python3 multi_agent_trading.py
```

## 触发时机

- 每30分钟自动执行
- 价格触及关键位时实时执行
- 用户要求"分析行情"时执行

## 输出格式

1. BTC + ETH 双盘分析
2. 各Agent观点
3. 裁判决策 + 置信度
4. 入场/止损/目标价位

## 推送方式

分析完成后，通过飞书主动推送报告给用户。
