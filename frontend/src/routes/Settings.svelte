<script>
  import { invoke } from '@tauri-apps/api/core';

  async function tauriApi(method, path, body) {
    const text = await invoke('api_call', { method, path, body: body ? JSON.stringify(body) : null });
    return JSON.parse(text);
  }

  let { apiClient = tauriApi } = $props();
  let config = $state(null);
  let configDir = $state('');
  let loading = $state(true);
  let saving = $state(false);
  let errorMsg = $state('');
  let savedMsg = $state('');

  async function loadConfig() {
    loading = true;
    errorMsg = '';
    try {
      const [loadedConfig, configDirInfo] = await Promise.all([
        apiClient('GET', '/api/config'),
        apiClient('GET', '/api/config-dir'),
      ]);
      config = loadedConfig;
      configDir = configDirInfo.path;
    } catch (e) {
      errorMsg = '读取设置失败: ' + String(e);
    } finally {
      loading = false;
    }
  }

  async function save() {
    if (!config || saving) return;
    saving = true;
    errorMsg = '';
    savedMsg = '';
    try {
      await apiClient('PUT', '/api/config', config);
      savedMsg = '设置已保存';
    } catch (e) {
      errorMsg = '保存设置失败: ' + String(e);
    } finally {
      saving = false;
    }
  }

  $effect(() => { loadConfig(); });
</script>

<div class="page">
  <div class="header">
    <h1>设置</h1>
    {#if saving}<span class="saving">正在保存...</span>{/if}
  </div>

  {#if loading}
    <div class="card loading">正在读取设置...</div>
  {:else if config}
    <div class="card">
      <h2>API 服务</h2>
      <div class="form">
        <label>
          端口
          <input
            type="number"
            min="1"
            max="65535"
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
  {:else}
    <div class="card load-error">
      <p>设置内容未能加载。</p>
      <button onclick={loadConfig}>重试</button>
    </div>
  {/if}

  {#if savedMsg}
    <div class="toast success">
      {savedMsg}
      <button class="toast-close" aria-label="关闭保存提示" onclick={() => savedMsg = ''}>✕</button>
    </div>
  {/if}

  {#if errorMsg}
    <div class="toast error">
      {errorMsg}
      <button class="toast-close" aria-label="关闭错误提示" onclick={() => errorMsg = ''}>✕</button>
    </div>
  {/if}
</div>

<style>
  .page { max-width: 600px; }
  .header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  h1 { font-size: 20px; font-weight: 700; }
  h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
  .saving, .loading { color: var(--text-muted); font-size: 13px; }

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
  .load-error { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .load-error p { color: var(--text-muted); font-size: 14px; }
  .toast {
    position: fixed;
    top: 16px;
    left: 50%;
    transform: translateX(-50%);
    padding: 8px 16px;
    border-radius: 8px;
    color: white;
    z-index: 200;
    display: flex;
    align-items: center;
    gap: 10px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }
  .toast.success { background: var(--success); }
  .toast.error { background: var(--danger); }
  .toast-close { background: none; border: none; color: white; padding: 0 2px; }
</style>
