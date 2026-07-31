# PyInstaller one-file sidecar 退出后悬挂

## 现场证据

- 用户退出主应用后，8844–8849 各有一个监听进程残留。
- 6 个残留进程 PPID 均为 1；其中 5 个是 bundle 内 `codex-cockpit-backend`，另一个是更早的 Python 源码后端。
- `cockpit.log` 每次均记录 `Stopping backend sidecar`，说明 Tauri 的退出回调确实执行，不是 Command-Q 事件遗漏。

## 根因

PyInstaller one-file 是两层进程：外层 bootloader 解包并等待，内层进程加载 Python 并运行 Uvicorn。Tauri shell 2.3.5 的 `CommandChild` 只持有外层 PID。

当前 `stop_backend()` 调用 `CommandChild.kill()`。插件内部转到 `shared_child::SharedChild::kill()`；该实现明确说明 Unix 使用 `SIGKILL`，且标准库 `Child.kill()` 只杀直接子进程。外层 bootloader 无法在 SIGKILL 下转发信号或清理内层进程。

## 可重复实验

最终 bundle 的临时 sidecar 进程树：

```text
outer 4345 -> inner 4346
```

向 outer 发送 SIGKILL 后：

- inner 4346 的 PPID 变为 1；
- 测试端口继续监听；
- 行为与用户现场完全一致。

对另一个临时 sidecar 的 outer 发送 SIGTERM 后：

- outer 退出码为 -15；
- inner 同步退出；
- 监听端口释放。

所有实验进程已单独清理。

## 修复建议

优先实现显式的优雅退出协议：Tauri 向当前 backend 发送仅内部可用的 shutdown 指令，让 Uvicorn 自己退出，PyInstaller bootloader 随后完成清理并退出；等待有限时间后再执行精确 PID fallback。该方案跨 macOS/Windows，且不依赖 `pkill` 或进程名猜测。

macOS/Linux 的较小修复是对 Tauri 保存的直接 sidecar PID 发送 SIGTERM并等待，实验已验证有效；但 Windows 信号语义不同，不能把它作为完整跨平台方案。

## 当前状态

- 历史 PID 27476、64922、65464、72637、82823、94429 已逐个验证路径后发送 TERM，全部正常退出。
- 8844–8849 已全部释放。
- 后端在 Uvicorn listener 就绪后输出随机 `CONTROL` 令牌与实际 `PORT`，隐藏关闭端点只接受精确令牌。
- Rust 等待两项协议数据就绪，退出时先请求 Uvicorn 自行结束，并等待 Tauri shell 的 `Terminated` 事件；Unix 超时 fallback 对保存的精确外层 PID 发送 SIGTERM，Windows 使用保存的精确子进程句柄。
- 未使用 PID 文件、进程名扫描或 `pkill`。

## 修复结论

根因可由一个内部优雅关闭协议直接消除：后端在 listener 就绪后输出随机控制令牌和实际端口，Tauri 使用该令牌请求隐藏关闭端点，Uvicorn 自行退出后 PyInstaller 内外两层会按正常路径一起结束。只有协议不可用或超时时，才对保存的精确子进程执行有限 fallback；不按名称扫描或清理进程。

## 验证结果

- `python3 scripts/check.py`：Python 31、前端 50、Rust 6 项测试全部通过；Ruff、ESLint、svelte-check、Clippy 均通过。
- `scripts/smoke_sidecar.py` 现在读取 `CONTROL`/`PORT`，记录 PyInstaller 内层 PID，调用隐藏关闭端点后断言外层退出码为 0、内层 PID 消失且端口释放。
- 构建目录 sidecar 连续通过动态端口冒烟；最终 `.app/Contents/MacOS/codex-cockpit-backend` 再次通过。
- `codesign --verify --deep --strict --verbose=2` 验证最终 `.app` 有效。
- 验证期间用户当前运行的 PID 2242/2253/2254 未被终止。
