<script>
  import { apiClient as defaultApiClient } from '../lib/apiClient.js';

  let { apiClient = defaultApiClient, pollIntervalMs = 3000 } = $props();
  let config = $state(null);
  let serviceDraft = $state(null);
  let status = $state(null);
  let backendRunning = $state(false);
  let portMismatch = $state(false);
  let sourcePort = $state(0);
  let reportedPort = $state(0);
  let startupPortChecked = $state(false);
  let errorMsg = $state('');
  let savedMsg = $state('');
  let saving = $state(false);

  async function loadConfig() {
    try {
      config = await apiClient('GET', '/api/config');
      serviceDraft = {
        port: config.api.port,
        bind_host: config.api.bind_host,
        speed: config.api.speed,
      };
    } catch (error) {
      errorMsg = '读取配置失败: ' + String(error);
    }
  }

  async function loadStatus() {
    try {
      status = await apiClient('GET', '/v1/cockpit/status');
      if (status) {
        backendRunning = true;
        if (!startupPortChecked && config) {
          startupPortChecked = true;
          if (status?.actual_port && status.actual_port !== config.api.port) {
            sourcePort = config.api.port;
            reportedPort = status.actual_port;
            portMismatch = true;
            config.api.port = status.actual_port;
            if (serviceDraft) {
              serviceDraft.port = status.actual_port;
            }
            await apiClient('PUT', '/api/config', config);
          }
        }
      }
    } catch (error) {
      backendRunning = false;
      errorMsg = '读取服务状态失败: ' + String(error);
    }
  }

  async function saveConfig(nextConfig, successMessage, failureMessage) {
    if (!nextConfig || saving) return false;
    saving = true;
    errorMsg = '';
    savedMsg = '';
    try {
      await apiClient('PUT', '/api/config', nextConfig);
      savedMsg = successMessage;
      return true;
    } catch (error) {
      errorMsg = failureMessage + ': ' + String(error);
      return false;
    } finally {
      saving = false;
    }
  }

  async function saveServiceConfig() {
    if (!config || !serviceDraft) return;
    const nextConfig = {
      ...config,
      api: {
        ...config.api,
        port: serviceDraft.port,
        bind_host: serviceDraft.bind_host,
        speed: serviceDraft.speed,
      },
    };
    const saved = await saveConfig(nextConfig, 'API 服务设置已保存', '保存 API 服务设置失败');
    if (saved) {
      config = nextConfig;
    }
  }

  function saveAutoSwitch() {
    return saveConfig(config, '自动切换设置已保存', '保存自动切换设置失败');
  }

  $effect(() => {
    loadConfig();
  });
  $effect(() => {
    if (config) {
      loadStatus();
      if (pollIntervalMs <= 0) return;
      const interval = setInterval(loadStatus, pollIntervalMs);
      return () => clearInterval(interval);
    }
  });
</script>

<div class="page">
  <h1>API 服务</h1>

  {#if errorMsg}
    <div class="toast error">
      {errorMsg}
      <button class="toast-close" aria-label="关闭错误提示" onclick={() => (errorMsg = '')}
        >✕</button
      >
    </div>
  {/if}

  {#if savedMsg}
    <div class="toast success">
      {savedMsg}
      <button class="toast-close" aria-label="关闭保存提示" onclick={() => (savedMsg = '')}
        >✕</button
      >
    </div>
  {/if}

  {#if portMismatch}
    <div class="card mismatch-banner">
      <div class="mismatch-content">
        <span class="mismatch-icon">⚠️</span>
        <div>
          <strong>端口变更通知</strong>
          <p>
            端口 {sourcePort} 已被更新为 {reportedPort}（源端口被占用）。配置已自动同步。
          </p>
        </div>
      </div>
      <button class="mismatch-close" onclick={() => (portMismatch = false)}>✕</button>
    </div>
  {/if}

  <div class="card status-card">
    <div class="status-row">
      <div>
        <span class="label">状态:</span>
        <span class="status-dot" class:running={backendRunning} class:stopped={!backendRunning}
        ></span>
        {backendRunning ? '运行中' : '已停止'}
      </div>
      <div>
        <span class="label">端口:</span>
        {config?.api?.port || ''}
        {#if portMismatch}
          <span class="mismatch-hint">→ 实际: {reportedPort}</span>
        {/if}
      </div>
      {#if status}
        <div>
          <span class="label">活跃账号:</span>
          {status.active_account_email || '无'}
        </div>
        <div>
          <span class="label">请求总数:</span>
          {status.total_requests}
        </div>
      {/if}
    </div>
  </div>

  {#if config}
    <div class="card" style="margin-top: 16px;">
      <div class="card-header">
        <h2>服务配置</h2>
        <button onclick={saveServiceConfig} disabled={saving}>
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
      {#if serviceDraft}
        <div class="service-form">
          <label>
            端口
            <input type="number" min="1" max="65535" bind:value={serviceDraft.port} />
          </label>
          <label>
            绑定地址
            <select bind:value={serviceDraft.bind_host}>
              <option value="127.0.0.1">127.0.0.1 (仅本机)</option>
              <option value="0.0.0.0">0.0.0.0 (局域网)</option>
            </select>
          </label>
          <label>
            默认速度
            <select bind:value={serviceDraft.speed}>
              <option value="standard">Standard</option>
              <option value="fast">Fast</option>
            </select>
          </label>
        </div>
      {/if}
    </div>

    <div class="card" style="margin-top: 16px;">
      <h2>自动切换</h2>
      <label class="toggle">
        <input
          type="checkbox"
          bind:checked={config.api.auto_switch.enabled}
          onchange={saveAutoSwitch}
        />
        额度用完后自动切换到下一个账号
      </label>
      {#if config.api.auto_switch.enabled}
        <div class="form-row" style="margin-top: 10px;">
          <label>
            额度阈值 (%):
            <input
              type="number"
              min="0"
              max="100"
              bind:value={config.api.auto_switch.quota_threshold_percent}
              onchange={saveAutoSwitch}
            />
          </label>
        </div>
      {/if}
    </div>
  {/if}

  {#if status?.recent_requests?.length}
    <div class="card" style="margin-top: 16px;">
      <h2>最近请求</h2>
      <div class="log-list">
        {#each status.recent_requests.slice(0, 20) as req (req.id)}
          <div class="log-entry">
            <span class="log-method {req.method.toLowerCase()}">{req.method}</span>
            <span class="log-path">{req.path}</span>
            <span class="log-status" class:ok={req.status < 400} class:err={req.status >= 400}>
              {req.status}
            </span>
            <span class="log-account">{req.account_email}</span>
            <span class="log-time">{req.duration_ms}ms</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .page {
    max-width: 800px;
  }
  h1 {
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 16px;
  }
  h2 {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 10px;
  }

  .status-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .status-row {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
  .label {
    color: var(--text-muted);
    font-size: 13px;
    margin-right: 4px;
  }
  .status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }
  .status-dot.running {
    background: var(--success);
  }
  .status-dot.stopped {
    background: var(--danger);
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 10px;
  }
  .card-header h2 {
    margin-bottom: 0;
  }
  .service-form {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .service-form label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    color: var(--text-muted);
    font-size: 14px;
  }
  .service-form input,
  .service-form select {
    width: 100%;
  }

  .toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    cursor: pointer;
  }
  .form-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .form-row label {
    font-size: 14px;
  }
  .form-row input {
    width: 80px;
  }

  .log-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 400px;
    overflow-y: auto;
  }
  .log-entry {
    display: flex;
    gap: 12px;
    font-size: 13px;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    align-items: center;
  }
  .log-method {
    font-weight: 600;
    min-width: 36px;
    text-transform: uppercase;
    font-size: 11px;
  }
  .log-method.post {
    color: var(--accent);
  }
  .log-method.get {
    color: var(--success);
  }
  .log-path {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .log-status {
    min-width: 32px;
    font-weight: 600;
  }
  .log-status.ok {
    color: var(--success);
  }
  .log-status.err {
    color: var(--danger);
  }
  .log-account {
    color: var(--text-muted);
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .log-time {
    color: var(--text-muted);
    font-size: 11px;
    min-width: 40px;
    text-align: right;
  }

  .mismatch-banner {
    background: rgba(245, 158, 11, 0.12);
    border-color: var(--warning);
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  .mismatch-content {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    flex: 1;
  }
  .mismatch-icon {
    font-size: 20px;
    flex-shrink: 0;
  }
  .mismatch-content strong {
    font-size: 14px;
  }
  .mismatch-content p {
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 2px;
  }
  .mismatch-close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 16px;
    padding: 0 4px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .mismatch-close:hover {
    color: var(--text);
  }
  .mismatch-hint {
    color: var(--warning);
    font-size: 12px;
    margin-left: 4px;
  }
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
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
  }
  .toast.error {
    background: var(--danger);
  }
  .toast.success {
    background: var(--success);
  }
  .toast-close {
    background: none;
    border: none;
    color: white;
    padding: 0 2px;
  }

  @media (max-width: 720px) {
    .service-form {
      grid-template-columns: 1fr;
    }
  }
</style>
