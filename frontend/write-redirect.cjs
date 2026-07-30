const fs = require('fs');
const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0;url=http://localhost:8844">
  <style>
    body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #0a0a0f; color: #e0e0e0; }
    a { color: #6366f1; }
  </style>
</head>
<body>
  <div style="text-align:center">
    <p>正在连接到 Codex Cockpit Lite...</p>
    <p style="font-size:13px;color:#888">如果未自动跳转，请<a href="http://localhost:8844">点击这里</a></p>
  </div>
</body>
</html>`;
const dist = process.argv[2] || 'dist';
fs.mkdirSync(dist, { recursive: true });
fs.writeFileSync(`${dist}/index.html`, html);
console.log(`Redirect page written to ${dist}/index.html`);
