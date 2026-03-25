# 安全说明 - API密钥管理

⚠️ **混沌龙虾机密文件** ⚠️

## API密钥存储原则

1. **配置文件加密**：
   - API密钥存储在 `config/trading_config.json`
   - 该文件已在 `.gitignore` 中排除
   - 仅限当前服务器访问

2. **密钥安全等级**：
   - **Level 1（已存储）**：OKX API Key（只读权限）
   - **Level 2（待提供）**：OKX API Secret + Passphrase（交易权限）

3. **推荐安全实践**：
   - Boss应在OKX创建"只读"API用于监控
   - 交易执行使用独立API，开启IP限制
   - 仅限TRADE权限，不开启WITHDRAW
   - 定期更换API密钥

## OKX API集成状态

### 已配置：
- ✅ API Key：`1dcf40d7-e2d7-4470-a9d6-b55806425dc5`
- ⏳ API Secret：待Boss提供
- ⏳ Passphrase：待Boss提供
- ✅ 目标：主网（非测试网）
- ✅ 状态：已启用（enabled: true）

### 需要Boss提供：
- [ ] OKX API Secret（用于签名）
- [ ] Passphrase（创建API时设置）
- [ ] 权限确认（只读？可交易？）

---

🦞 混沌龙虾已安全记录OKX API密钥，等待完整配置后即可开始交易！