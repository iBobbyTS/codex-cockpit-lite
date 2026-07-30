<script>
  const API = 'http://localhost:8844';
  let config = $state(null);
  let accounts = $state([]);
  let status = $state(null);
  let backendRunning = $state(false);
  let portMismatch = $state(false);
  let reportedPort = $state(0);

  async function loadConfig() {
    try {
      const resp = await fetch(API + '/api/config');
      config = await resp.json();
    } catch {}
  }

  async function loadAccounts() {
    try {
      const resp = await fetch(API + '/api/accounts');
      accounts = await resp.json();
    } catch {}
  }

  async function loadStatus() {
    try {
      const resp = await fetch(API + '/v1/cockpit/status');
      if (resp.ok) {
        status = await resp.json();
        backendRunning = true;
        if (status?.actual_port && status.actual_port !== config?.api?.port) {
          reportedPort = status.actual_port;
          portMismatch = true;
          if (config) {
            config.api.port = status.actual_port;
            await fetch(API + '/api/config', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config) });
          }
        } else {
          portMismatch = false;
        }
      }
    } catch {
      backendRunning = false;
    }
  }

  async function saveAutoSwitch() {
    if (!config) return;
    await fetch(API + '/api/config', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config) });
  }

  $effect(() => { loadConfig(); loadAccounts(); });
  $effect(() => {
    if (config) {
      loadStatus();
      const interval = setInterval(loadStatus, 3000);
      return () => clearInterval(interval);
    }
  });
</script>

<div class="page">
  <h1>API 服务</h1>

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
        <span class="label">端口:</span> {config?.api?.port || ''}
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
