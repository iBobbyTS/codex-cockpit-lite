<script>
  import Toast from '../lib/Toast.svelte';
  import { apiClient as defaultApiClient } from '../lib/apiClient.js';

  let { apiClient = defaultApiClient } = $props();
  let configDir = $state('');
  let loading = $state(true);
  let errorMsg = $state('');

  function dismissError() {
    errorMsg = '';
  }

  async function loadSettings() {
    loading = true;
    errorMsg = '';
    try {
      const configDirInfo = await apiClient('GET', '/api/config-dir');
      configDir = configDirInfo.path;
    } catch (e) {
      errorMsg = '读取设置失败: ' + String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    loadSettings();
  });
</script>

<div class="page">
  <div class="header">
    <h1>设置</h1>
  </div>

  {#if loading}
    <div class="card loading">正在读取设置...</div>
  {:else if configDir}
    <div class="card">
      <h2>配置目录</h2>
      <p class="path">{configDir}</p>
      <p class="hint">
        可通过环境变量 <code>CODEX_COCKPIT_HOME</code> 修改。 账号 auth.json 文件存储在
        <code>accounts/</code> 子目录中。
      </p>
    </div>
  {:else}
    <div class="card load-error">
      <p>设置内容未能加载。</p>
      <button onclick={loadSettings}>重试</button>
    </div>
  {/if}

  {#if errorMsg}
    <Toast message={errorMsg} tone="error" onDismiss={dismissError} closeLabel="关闭错误提示" />
  {/if}
</div>

<style>
  .page {
    max-width: 600px;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
  }
  h1 {
    font-size: 20px;
    font-weight: 700;
  }
  h2 {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 12px;
  }
  .loading {
    color: var(--text-muted);
    font-size: 13px;
  }

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
  .load-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .load-error p {
    color: var(--text-muted);
    font-size: 14px;
  }
</style>
