<script>
  import { invoke } from '@tauri-apps/api/core';

  let config = $state(null);
  let accounts = $state([]);
  let status = $state(null);
  let backendRunning = $state(false);
  let portMismatch = $state(false);
  let reportedPort = $state(0);
  let noAccountsError = $state(false);
  let hasTriedAutoStart = $state(false);

  async function loadConfig() {
    config = await invoke('get_config');
  }

  async function loadAccounts() {
    accounts = await invoke('list_accounts');
  }

  async function loadStatus() {
    try {
      const base = config?.api?.port || 8844;
      const resp = await fetch(`http://localhost:${base}/v1/cockpit/status`);
      if (resp.ok) {
        status = await resp.json();
        backendRunning = status?.running || false;
        if (status?.actual_port && status.actual_port !== config?.api?.port) {
          reportedPort = status.actual_port;
          portMismatch = true;
          if (config) {
            config.api.port = status.actual_port;
            await invoke('save_config', { config });
          }
        } else {
          portMismatch = false;
        }
      }
    } catch (e) {
      backendRunning = false;
      status = null;
    }
  }

  async function start() {
    if (!accounts.length) {
      noAccountsError = true;
      return;
    }
    noAccountsError = false;
    try {
      await invoke('start_backend');
      await new Promise(r => setTimeout(r, 2000));
      await loadConfig();
      await loadStatus();
    } catch (e) {
      if (String(e).includes('NO_ACCOUNTS')) {
        noAccountsError = true;
      }
    }
  }

  async function stop() {
    await invoke('stop_backend');
    backendRunning = false;
    status = null;
    portMismatch = false;
  }

  async function saveAutoSwitch() {
    if (!config) return;
    await invoke('save_config', { config });
  }

  $effect(() => { loadConfig(); loadAccounts(); });
  $effect(() => {
    if (config) {
      loadStatus();
      const interval = setInterval(loadStatus, 3000);
      return () => clearInterval(interval);
    }
  });
  // Auto-start backend when accounts exist and not already running
  $effect(() => {
    if (accounts.length > 0 && !backendRunning && !hasTriedAutoStart && config) {
      hasTriedAutoStart = true;
      start();
    }
  });
</script>

<div class="page">
  <h1>API 服务</h1>

  {#if noAccountsError}
    <div class="card mismatch-banner" style="background: rgba(239,68,68,0.12); border-color: var(--danger);">
      <div class="mismatch-content">
        <span class="mismatch-icon">⚠️</span>
        <div>
          <strong>无法启动服务</strong>
          <p>当前没有已导入账号，请先在「账号管理」中导入账号。</p>
        </div>
      </div>
      <button class="mismatch-close" onclick={() => noAccountsError = false}>✕</button>
    </div>
  {/if}

  {#if portMismatch}
    <div class="card mismatch-banner">
      <div class="mismatch-content">
        <span class="mismatch-icon">⚠️</span>
        <div>
          <strong>端口变更通知</strong>
          <p>端口 {config?.api?.port} 已被更新为 {reportedPort}（源端口被占用）。配置已自动同步。</p>
        </div>
      </div>
      <button class="mismatch-close" onclick={() => portMismatch = false}>✕</button>
    </div>
  {/if}

  <div class="card status-card">
    <div class="status-row">
      <div>
        <span class="label">状态:</span>
        <span class="status-dot" class:running={backendRunning} class:stopped={!backendRunning}></span>
        {backendRunning ? '运行中' : '已停止'}
      </div>
      <div>
        <span class="label">配置端口:</span> {config?.api?.port || ''}
        {#if portMismatch}
          <span class="mismatch-hint">→ 实际: {reportedPort}</span>
        {/if}
      </div>
      {#if status}
        <div>
          <span class="label">活跃账号:</span> {status.active_account_email || '无'}
        </div>
        <div>
          <span class="label">请求总数:</span> {status.total_requests}
        </div>
      {/if}
    </div>
    <button
      class={backendRunning ? 'danger' : 'primary'}
      onclick={backendRunning ? stop : start}
    >
      {backendRunning ? '停止服务' : '启动服务'}
    </button>
  </div>

  {#if config}
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
              min="0" max="100"
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
        {#each status.recent_requests.slice(0, 20) as req}
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
  .page { max-width: 800px; }
  h1 { font-size: 20px; font-weight: 700; margin-bottom: 16px; }
  h2 { font-size: 16px; font-weight: 600; margin-bottom: 10px; }

  .status-card { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  .status-row { display: flex; gap: 20px; flex-wrap: wrap; }
  .label { color: var(--text-muted); font-size: 13px; margin-right: 4px; }
  .status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }
  .status-dot.running { background: var(--success); }
  .status-dot.stopped { background: var(--danger); }

  .toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    cursor: pointer;
  }
  .form-row { display: flex; gap: 8px; align-items: center; }
  .form-row label { font-size: 14px; }
  .form-row input { width: 80px; }

  .log-list { display: flex; flex-direction: column; gap: 4px; max-height: 400px; overflow-y: auto; }
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
  .log-method.post { color: var(--accent); }
  .log-method.get { color: var(--success); }
  .log-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .log-status { min-width: 32px; font-weight: 600; }
  .log-status.ok { color: var(--success); }
  .log-status.err { color: var(--danger); }
  .log-account { color: var(--text-muted); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .log-time { color: var(--text-muted); font-size: 11px; min-width: 40px; text-align: right; }

  .mismatch-banner {
    background: rgba(245, 158, 11, 0.12);
    border-color: var(--warning);
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  .mismatch-content { display: flex; align-items: flex-start; gap: 10px; flex: 1; }
  .mismatch-icon { font-size: 20px; flex-shrink: 0; }
  .mismatch-content strong { font-size: 14px; }
  .mismatch-content p { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
  .mismatch-close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 16px;
    padding: 0 4px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .mismatch-close:hover { color: var(--text); }
  .mismatch-hint { color: var(--warning); font-size: 12px; margin-left: 4px; }
</style>
