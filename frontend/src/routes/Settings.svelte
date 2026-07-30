<script>
  import { fetch } from '@tauri-apps/plugin-http';
  const API = 'http://127.0.0.1:8844';
  let config = $state(null);

  async function loadConfig() {
    try {
      const resp = await fetch(API + '/api/config');
      config = await resp.json();
    } catch {}
  }

  async function save() {
    if (!config) return;
    await fetch(API + '/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
  }

  $effect(() => { loadConfig(); });
</script>

<div class="page">
  <h1>设置</h1>

  {#if config}
    <div class="card">
      <h2>API 服务</h2>
      <div class="form">
        <label>
          端口
          <input
            type="number"
            bind:value={config.api.port}
            onchange={save}
          />
        </label>
        <label>
          绑定地址
          <select bind:value={config.api.bind_host} onchange={save}>
            <option value="127.0.0.1">127.0.0.1 (仅本机)</option>
            <option value="0.0.0.0">0.0.0.0 (局域网)</option>
          </select>
        </label>
        <label>
          默认速度
          <select bind:value={config.api.speed} onchange={save}>
            <option value="standard">Standard</option>
            <option value="fast">Fast</option>
          </select>
        </label>
      </div>
    </div>

    <div class="card" style="margin-top: 16px;">
      <h2>配置目录</h2>
      <p class="path">{configDir}</p>
      <p class="hint">
        可通过环境变量 <code>CODEX_COCKPIT_HOME</code> 修改。
        账号 auth.json 文件存储在 <code>accounts/</code> 子目录中。
      </p>
    </div>
  {/if}
</div>

<style>
  .page { max-width: 600px; }
  h1 { font-size: 20px; font-weight: 700; margin-bottom: 16px; }
  h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; }

  .form { display: flex; flex-direction: column; gap: 12px; }
  .form label { display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: var(--text-muted); }
  .form input, .form select { width: 200px; }

  .path {
    font-family: monospace;
    font-size: 13px;
    color: var(--accent);
    padding: 4px 0;
  }
  .hint {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 6px;
    line-height: 1.5;
  }
  .hint code {
    background: var(--border);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 11px;
  }
</style>
