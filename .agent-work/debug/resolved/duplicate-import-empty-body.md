# 从 ~/.codex 重复导入返回 500

## 用户现象

点击“从 ~/.codex 导入”后显示：`从 ~/.codex 导入失败: HTTP 500: Internal Server Error`。

## 根因

前端调用 `apiClient('POST', '/api/accounts/import-from-codex')` 时不传 body。Tauri Rust 代理仍设置 `Content-Type: application/json` 并发送空字符串。后端仅根据 Content-Type 决定执行 `await req.json()`，空字符串触发未处理的 `JSONDecodeError`，请求未进入 `_dedup_account()` 就返回 500。

## 修复

- 后端用独立请求边界函数读取可选 JSON object，空 body 解释为 `{}`。
- 非空的畸形 JSON 或非 object JSON 继续返回可读的 400，不隐藏调用错误。
- 现有去重规则不变：同 email 和同 `tokens.account_id` 返回 409；确认覆盖时复用原账号 ID。

## 验证

- 后端测试覆盖空 body 返回 `DUPLICATE:`、`force: true` 复用 ID、畸形 JSON 返回 400。
- 前端真实组件测试覆盖重复弹窗取消不写入，以及覆盖发送 `{ force: true }` 并刷新原账号。
- `python3 scripts/check.py` 全部通过：Python 25 项、前端 49 项、Rust 3 项测试通过。
- 最终 macOS bundle sidecar 冒烟和应用签名验证通过。
