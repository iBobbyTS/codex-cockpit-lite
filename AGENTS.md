# Codex Cockpit 项目规则

## 完成修改后的打包

- 每次完成功能或界面修改并通过相关检查后，无需等待用户再次授权，直接重新打包对应平台的应用程序并覆盖 bundle 目录中的旧版构建产物。
- 在 macOS 上使用 `cd frontend && npx tauri build --bundles app`，不得只运行 `npm run build` 或 `cargo build --release`。
- macOS `.app` 目标路径为 `frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app`。
- 如果打包失败，保留已有可用构建产物并向用户报告失败原因，不得先删除旧版。
