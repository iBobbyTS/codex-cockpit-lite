# PORT 就绪协议早于 Uvicorn 监听

## 用户现象

打开应用时前端显示：`请求失败: io: Connection refused`。

## 已确认根因

`PORT=<port>` 在 FastAPI lifespan startup 中输出。Uvicorn 的启动顺序是先等待 lifespan，然后才调用 `loop.create_server()` 建立监听 socket。因此 Tauri 收到 PORT 并放行首次 API 请求时，端口尚未监听。

最终 bundle 连续 5 次在读取 PORT 后立即连接，结果均为 macOS errno 61：

```text
immediate-connect-results [61, 61, 61, 61, 61]
```

日志也显示 lifespan 的 started 消息和 Tauri 的 Backend ready 均早于 `Uvicorn running on ...`。

## 修复

用 `CockpitServer` 覆盖 Uvicorn startup，在父类完成 listener 创建且 `server.started` 为真后才输出 PORT。Tauri 继续使用原 PORT 协议，但其语义恢复为真实可连接状态。

## 验证

- 修复前最终 bundle 连续 5 次即时连接均返回 errno 61。
- 修复后 sidecar 和最终 bundle 各连续 5 次即时连接均返回 0。
- `python3 scripts/check.py` 全部通过：Python 29 项、前端 50 项、Rust 3 项测试通过。
- 最终 bundle sidecar 冒烟和 macOS 深度签名验证通过。

## 额外观察

本机仍有历史 sidecar 占用 8844–8848。它们不是本次 Connection refused 的直接原因，本轮未手动终止或清理。
