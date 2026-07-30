# 构建指南

## 前置条件

- Node.js 24+、npm 11+
- Rust 1.88+（`cargo`）
- Python 3.11+（`python3`、`pip3`）
- macOS：Xcode Command Line Tools（`xcode-select --install`）
- 可选：`librsvg`（`brew install librsvg`，仅重新生成图标时需要）

## 开发模式

```bash
# Python 后端（独立运行，调试用）
cd backend
pip3 install -r requirements.txt
python3 main.py

# 前端（Vite 热更新）
cd frontend
npm install
npm run dev        # http://localhost:1420
```

## 构建 .app

**唯一正确的方式：**

```bash
cd frontend
npm install
npx tauri build --bundles app
```

产物：
```
frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app
```

常见错误：不要用 `cargo build --release` 手动构建，它不会嵌入前端资源。

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
# Python 后端
cd backend && python3 -m pytest tests/ -v

# 前端（仅 Vite 构建检查）
cd frontend && npm run build

# Rust（仅类型检查，不嵌入资源）
cd frontend/src-tauri && cargo check
```
