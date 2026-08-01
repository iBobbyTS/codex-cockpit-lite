## Active

| file | summary |
| --- | --- |
| active/scsc-refresh-token-invalidated.md | Adding persistent reauth state, blank unavailable quota, and identity-aware PKCE browser login after confirmed access and refresh token invalidation. |

## Unresolved

| file | summary |
| --- | --- |

## Resolved

| file | summary |
| --- | --- |
| resolved/live-api-compatibility.md | Cloudflare 400, managed Codex routing, streaming/encoding, search, Images, compaction, and Rosetta live-agent compatibility verified through Cockpit. |
| resolved/port-readiness-race.md | PORT 协议已移到 Uvicorn listener 建立后，最终 bundle 连续即时连接验证通过。 |
| resolved/duplicate-import-empty-body.md | 空 body 的 ~/.codex 导入已视为默认选项，重复账号弹窗取消和覆盖流程均有回归测试。 |
| resolved/startup-loading-state.md | 启动门控和账号骨架加载态已验证，避免空账号闪烁及 effect 隐式依赖回归。 |
| resolved/account-import-refresh-state.md | 导入与每账号刷新已拆成独立生命周期，真实组件时序测试通过。 |
| resolved/pyinstaller-hardened-runtime.md | 关闭 ad-hoc bundle 的 hardened runtime，并直接冒烟最终 bundle sidecar，修复 libpython Team ID 拒载。 |
| resolved/pyinstaller-sidecar-orphans.md | Tauri 现通过内部令牌优雅关闭 Uvicorn；最终 bundle 验证 PyInstaller 内外进程均退出且端口释放。 |
| resolved/shutdown-main-thread-block.md | ExitRequested 后台等待、macOS Exit 无等待保障均经最终 bundle 验证，退出不再阻塞事件线程且无残留。 |
| resolved/account-card-drag-drop.md | 已关闭 Tauri 原生拖放拦截，并扩展列表空白区域为末尾插入目标；最终 bundle 手动验证通过。 |
