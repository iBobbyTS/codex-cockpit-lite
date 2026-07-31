# 退出时 macOS 主线程阻塞

## 现场现象

- 用户关闭应用时约 1 秒出现 macOS 彩色转轮。
- 修复后的 sidecar 能正常优雅退出，连续两次关闭均无残留进程。

## 已确认根因

- `stop_backend()` 同步发送 HTTP shutdown 请求，并在条件变量上等待 PyInstaller 外层进程的 `Terminated` 事件。
- 该函数直接从 `WindowEvent::Destroyed` 和 `RunEvent::ExitRequested` 事件回调调用，阻塞了 macOS/Tauri 主事件线程。
- 后端退出本身成功；问题是等待发生在错误的线程，而不是 graceful shutdown 太慢或失败。

## 修复假设

首次 `ExitRequested` 应同步调用官方 `api.prevent_exit()` 后立即返回，将阻塞式清理交给 `tauri::async_runtime::spawn_blocking()`；清理完成后从后台调用 `AppHandle::exit()`。状态机必须阻止重复清理，并在第二次 `ExitRequested` 时放行正式退出。

## macOS 官方机制缺口

- 最终 bundle 在后端完全 ready 后通过正常 Quit Apple Event 退出时，没有触发 `ExitRequested`；日志中没有后台清理入口，Tauri 主进程退出后 PyInstaller PID 28231/28242 变成残留。
- 该行为与 Tauri issue #13778 / #12978 一致：macOS Command-Q 和 Dock Quit 可能跳过 `ExitRequested`，上游目前没有完整官方解决方案。
- 本次测试产生的两个进程已核验路径后，通过外层精确 PID 的 SIGTERM 清理，8851 已释放。
- 因此必须同时处理官方 `RunEvent::Exit`：只发起最长 250ms 的本地 shutdown 请求，不在主线程等待进程结束；请求不可用时对保存的精确外层 PID 发送 SIGTERM。

## 验证目标

- 退出事件回调不再执行 HTTP 或条件变量等待。
- 同时发生的多个退出请求只启动一次清理。
- 后端启动中、运行中和已停止三种状态均能正确退出。
- 最终 bundle 关闭后仍无 PyInstaller 内层残留或监听端口。

## 验证结果

- `python3 scripts/check.py` 全部通过：Python 31、前端 50、Rust 9 项测试；Ruff、ESLint、svelte-check、Clippy 均通过。
- 最终 `.app` sidecar 冒烟及 `codesign --verify --deep --strict` 通过。
- 后端 ready 后发送 macOS Quit Apple Event：事件调用 0.165 秒返回，主进程与 PyInstaller 两层在 0.420 秒内全部退出；日志命中 `Final app exit` 无等待保障，8844–8855 无监听。
- 通过 Computer Use 点击最终 bundle 的原生红色关闭按钮：日志完整命中 `App exit requested`、后台 `Stopping backend sidecar`、`Backend sidecar stopped gracefully`、`Backend cleanup finished`；随后全部进程消失且 8844–8855 无监听。
- AppleScript `close front window` 不受该 Tauri 窗口支持，返回 -1708；它没有被作为产品验证证据，遗留的测试实例随后由 Computer Use 原生关闭按钮正常关闭。

## 修复结论

- Windows/可触发 `ExitRequested` 的 macOS 窗口关闭使用 Tauri 官方 `prevent_exit()`、`spawn_blocking()` 和 `AppHandle::exit()`，事件线程不执行 HTTP 或条件变量等待。
- macOS 原生 Quit 可能跳过 `ExitRequested`，因此 `RunEvent::Exit` 只发起最长 250ms 的 localhost shutdown；不等待进程，失败时精确 SIGTERM 外层 PID。
- 两条路径共享同一状态，重复退出请求不会重复启动清理，后端启动中的退出也有精确终止保障。
