# 构建指南

## 前置条件

- Node.js 24.18.1 LTS、npm 11.16.0（见 `.nvmrc`）
- Rust 1.97.1，含 rustfmt、Clippy（见 `rust-toolchain.toml`）
- Python 3.14.6 与 uv 0.12.0（见 `.python-version`、`backend/uv.lock`）
- macOS：Xcode Command Line Tools（`xcode-select --install`）
- 可选：`librsvg`（`brew install librsvg`，仅重新生成图标时需要）

## 开发模式

```bash
# Python 后端（独立运行，调试用）
cd backend
uv sync --locked
uv run codex-cockpit-backend

# 前端（Vite 热更新）
cd frontend
npm ci
npm run dev        # http://localhost:1420
```

## 构建 .app

**唯一正确的方式：**

```bash
cd frontend
npm ci
npx tauri build --bundles app
```

`tauri.conf.json` 的 `beforeBuildCommand` 会自动执行 `scripts/build_sidecar.py`：

1. 使用 Vite 构建 `frontend/dist`。
2. 使用锁定的 Python 3.14 环境运行 PyInstaller one-file。
3. 通过 `rustc --print host-tuple` 生成 Tauri 官方要求的 sidecar 文件名。
4. 将 WebUI 一并嵌入 sidecar，使 API 端口仍可提供浏览器 WebUI。

无需手动复制 Python 源码，也不要在运行时依赖系统 Python。

产物：
```
frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app
```

常见错误：不要用 `cargo build --release` 手动构建最终应用；它不会执行完整的
Vite + PyInstaller sidecar + Tauri bundle 流程。

## 构建 .dmg

```bash
cd frontend
npx tauri build --bundles dmg
```

或先从 .app 手动创建：

```bash
hdiutil create -fs HFS+ \
  -srcfolder frontend/src-tauri/target/release/bundle/macos/Codex\ Cockpit\ Lite.app \
  -volname "Codex Cockpit Lite" \
  -ov frontend/src-tauri/target/release/bundle/macos/Codex\ Cockpit\ Lite_0.1.0_aarch64.dmg
```

## 重新生成图标

```bash
brew install librsvg

SVG=/path/to/logo.svg
D=frontend/src-tauri/icons

# 用 rsvg-convert 渲染（支持渐变），不要直接用 magick 渲染 SVG
rsvg-convert -w 1024 -h 1024 -b none "$SVG" -o "$D/logo-1024.png"

# 用 Pillow 转 RGBA PNG（Tauri 要求），不要用 magick
python3 << 'EOF'
from PIL import Image
D = 'frontend/src-tauri/icons'
src = Image.open(f'{D}/logo-1024.png').convert('RGBA')
for name, size in [('32x32.png', 32), ('128x128.png', 128), ('128x128@2x.png', 256)]:
    src.resize((size, size), Image.LANCZOS).save(f'{D}/{name}', 'PNG')
EOF
```

## 测试

```bash
# 全部本地门禁：锁文件、格式、lint、测试、Vite、sidecar 冒烟和 Rust
python3 scripts/check.py
```

sidecar 冒烟测试会使用临时配置目录，验证 status、config、WebUI、端口占用自动递增，
再通过 Tauri 使用的内部 `CONTROL` 令牌关闭 Uvicorn，并确认 PyInstaller one-file 的内外
两层进程均退出、端口释放。它不会读写真实的 `~/.config/codex-cockpit`。

Tauri 收到 `ExitRequested` 时必须先调用 `prevent_exit()`，将令牌化优雅关闭和 sidecar
等待放入 `spawn_blocking()`，完成后再调用 `AppHandle::exit()`；禁止在窗口或应用事件回调中
同步等待，否则 macOS 会显示彩色转轮。优雅关闭超时后的 fallback 只能针对保存的精确子
进程句柄/PID，禁止按进程名扫描或 `pkill`。

macOS 的 Command-Q、Dock Quit 或 Apple Quit Event 可能因 Tauri 上游限制跳过
`ExitRequested`。`RunEvent::Exit` 因此保留最长 250ms 的 localhost shutdown 发起保障，但不
等待 sidecar；请求不可用时只向保存的精确 PyInstaller 外层 PID 发送 SIGTERM。不得在该
最终事件中恢复 1–3 秒的同步等待。

macOS 最终 bundle 还应直接验证 Tauri 重签后的 sidecar，防止代码签名改变运行行为：

```bash
python3 scripts/smoke_sidecar.py --binary \
  "frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app/Contents/MacOS/codex-cockpit-backend"
```

本地构建使用 Tauri ad-hoc 签名并关闭 hardened runtime。PyInstaller 官方说明 hardened
runtime 需要有效的 Apple-issued identity；ad-hoc hardened runtime 会使 one-file 解压的
Python 动态库因 Team ID/library validation 不一致而加载失败。正式分发若启用 Developer ID
和 notarization，必须让 PyInstaller 内嵌库与 Tauri bundle 使用同一个有效身份重新验证。

## Windows

Windows 使用同一个 `scripts/build_sidecar.py` 和 `scripts/check.py`，并原生生成带 `.exe`
的目标三元组 sidecar。PyInstaller 不支持从 macOS 交叉构建 Windows 产物，因此 Windows
安装包必须在 Windows 主机执行并验证。
