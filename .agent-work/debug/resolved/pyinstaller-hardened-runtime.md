# PyInstaller sidecar 与 hardened runtime

## Confirmed observations

- 原始 `frontend/src-tauri/binaries/codex-cockpit-backend-aarch64-apple-darwin` 冒烟测试通过。
- Tauri 打包并以 `signingIdentity: "-"` 重签后的 bundle sidecar 可稳定复现退出码 255。
- 完整错误为 `libpython3.14.dylib ... mapping process and mapped file (non-platform) have different Team IDs`。
- `codesign -dvv` 显示 bundle sidecar flags 为 `adhoc,runtime`，即启用了 hardened runtime，TeamIdentifier 未设置。
- 现有冒烟测试只测试 Tauri 重签前的 binaries 目录，未覆盖 bundle 内最终 sidecar，因此漏检。

## Current hypothesis

根因是 Tauri 对 PyInstaller one-file sidecar 启用 hardened runtime 后触发 macOS library validation；运行时解压的 Python 动态库与 ad-hoc sidecar 没有一致 Team ID，因而被拒绝。最小修复应调整本地 ad-hoc bundle 的 hardened runtime/entitlement，而不是回退到系统 Python。

## Next diagnostic

1. 核对 Tauri 与 PyInstaller 官方代码签名说明。
2. 单独改变 hardened runtime 配置并重新打包。
3. 直接执行 bundle 内 sidecar 复现命令，并把该路径加入自动化冒烟测试。

## Official evidence

- Tauri 官方支持以 `signingIdentity: "-"` 做 ad-hoc 签名。
- PyInstaller 6.21 官方说明：启用 hardened runtime 需要有效 Apple-issued certificate；self-signed/ad-hoc 场景会造成共享库因 Team ID/library validation 加载失败。

## Minimal experiment

仅将 macOS `hardenedRuntime` 改为 `false`，保留 ad-hoc 签名。重新构建后直接运行 bundle 内 sidecar；不同时添加 entitlement 或改为 onedir。

## Verification

- 修复前直接运行 bundle sidecar：稳定退出 255，完整复现 Team ID 错误。
- 修复后 `codesign -dvv`：sidecar flags 从 `adhoc,runtime` 变为 `adhoc`。
- `python3 scripts/smoke_sidecar.py --binary <bundle-sidecar>`：通过，覆盖动态端口、status、config、WebUI 和端口释放。
- `codesign --verify --deep --strict`：最终 `.app` 验证通过。

## Resolution

本地 ad-hoc 构建设置 `hardenedRuntime: false`。正式 Developer ID/notarized 分发不直接复用此假设，需用同一有效 Apple identity 签名 PyInstaller 内嵌库与 Tauri bundle 后重新验证。
