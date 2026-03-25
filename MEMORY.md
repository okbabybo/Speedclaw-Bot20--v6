# MEMORY.md

> 此为索引文件，详细记忆见 `memory/` 目录

## 核心信息
- 交易品种: BTC、ETH 永续合约
- 语言: 简体中文
- 时区: GMT+8

## OKX 账户配置
- API Key: be046210-77bd-47e6-8524-dee2f2acebd9
- Secret: 508989F295B579CA787D85F500B9C02E（已加密存储）
- Passphrase: Fjh872330@
- 用途: 盯盘实时数据（账户级盘口、持仓、挂单）

## 盯盘汇报格式（用户要求）
1. 现货价格 + 合约溢价/折价 (Basis)
2. 资金费率 (Funding Rate)
3. 持仓量变化 (Open Interest)
4. 多空比 (Long/Short Ratio)
5. 爆仓数据 (Liquidations)
6. 深度和流动性分析
7. 多空方向建议（Long/Short 信号与逻辑）
8. 时效周期（分析有效期）

- **数据源**: OKX 账户实时数据
- **格式**: 严格列表格式，清晰整洁
- **更新**: 2026-03-26

## 记忆系统
- P0 长期记忆: `memory/p0-longterm/`
- P1 经验总结: `memory/p1-summary/`
- P2 短期记录: `memory/p2-shortterm/`
- 共享索引: `memory/shared/`
- 工作日志: `memory/workspace/`
