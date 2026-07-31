# Codex Cockpit 项目规则

## 完成修改后的打包

- 每次完成功能或界面修改并通过相关检查后，无需等待用户再次授权，直接重新打包对应平台的应用程序并覆盖 bundle 目录中的旧版构建产物。
- 在 macOS 上使用 `cd frontend && npx tauri build --bundles app`，不得只运行 `npm run build` 或 `cargo build --release`。
- `npx tauri build --bundles app` 的 `beforeBuildCommand` 会自动构建 Vite WebUI 和 PyInstaller sidecar；不要手动复制 backend 源码到 app resource。
- 完整本地门禁统一使用 `python3 scripts/check.py`。执行最终打包前应先通过该门禁。
- macOS `.app` 目标路径为 `frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app`。
- 如果打包失败，保留已有可用构建产物并向用户报告失败原因，不得先删除旧版。
