<script>
  import { invoke } from '@tauri-apps/api/core';

  async function api(method, path, body) {
    const text = await invoke('api_call', { method, path, body: body ? JSON.stringify(body) : null });
    return JSON.parse(text);
  }
  let config = $state(null);
  let accounts = $state([]);
  let showImport = $state(false);
  let importJson = $state('');
  let importName = $state('');
  let errorMsg = $state('');

  async function refreshAll() {
    try {
      const [cfg, acc] = await Promise.all([
        api('GET', '/api/config'),
        api('GET', '/api/accounts'),
      ]);
      config = cfg;
      accounts = acc;
    } catch (e) {
      errorMsg = '读取配置失败: ' + String(e);
    }
  }

  async function importAccount() {
    errorMsg = '';
    try {
      const result = await api('POST', '/api/accounts/import', {
        auth_json: importJson.trim(),
        name: importName.trim() || 'Codex Account',
      });
      showImport = false;
      importJson = '';
      importName = '';
      // Refresh quota immediately
      api('POST', '/api/accounts/' + result.id + '/refresh').catch(() => {});
      await refreshAll();
    } catch (e) {
      errorMsg = '导入失败: ' + String(e);
    }
  }

  async function importOfficial() {
    errorMsg = '';
    try {
      const result = await api('POST', '/api/accounts/import-from-codex');
      api('POST', '/api/accounts/' + result.id + '/refresh').catch(() => {});
      await refreshAll();
    } catch (e) {
      errorMsg = '从 ~/.codex 导入失败: ' + String(e);
    }
  }

  async function deleteAccount(id) {
    if (!confirm('确认删除此账号？')) return;
    errorMsg = '';
    try {
      await api('DELETE', '/api/accounts/' + id);
      await refreshAll();
    } catch (e) {
      errorMsg = '删除失败: ' + String(e);
      await refreshAll();
    }
  }

  async function toggleAccount(id, enabled) {
    errorMsg = '';
    try {
      await api('PUT', '/api/accounts/' + id + '/toggle', { enabled });
      await refreshAll();
    } catch (e) {
      errorMsg = String(e);
      await refreshAll();
    }
  }

  function selectedIds() {
    return new Set(config?.api?.selected_accounts || []);
  }

  $effect(() => { refreshAll(); });
  $effect(() => {
    const interval = setInterval(refreshAll, 5000);
    return () => clearInterval(interval);
  });
</script>

<div class="page">
  <div class="header">
    <h1>账号管理</h1>
    <div class="actions">
      <button onclick={importOfficial}>从 ~/.codex 导入</button>
      <button class="primary" onclick={() => showImport = !showImport}>
        {showImport ? '取消' : '导入 auth.json'}
      </button>
    </div>
  </div>

  {#if errorMsg}
    <div class="card error-banner">
      <span>❌</span>
      <span>{errorMsg}</span>
      <button class="mismatch-close" onclick={() => errorMsg = ''}>✕</button>
    </div>
  {/if}

  {#if showImport}
    <div class="card import-panel">
      <textarea
        bind:value={importJson}
        placeholder="粘贴 auth.json 内容..."
      ></textarea>
      <div class="import-row">
        <input bind:value={importName} placeholder="显示名称（可选）" />
        <button class="primary" onclick={importAccount}>导入</button>
      </div>
    </div>
  {/if}

  <div class="account-list">
    {#each accounts as account (account.id)}
      {@const sel = selectedIds().has(account.id)}
      <div class="card account-card">
        <div class="account-main">
          <div class="account-info">
            <h3>{account.name || account.email || account.id.slice(0, 8)}</h3>
            <span class="email">{account.email}</span>
            <div class="tags">
              {#if account.plan_type}
                <span class="badge {account.plan_type === 'pro' ? 'pro' : account.plan_type.includes('team') ? 'team' : 'free'}">
                  {account.plan_type}
                </span>
              {/if}
              {#if account.team_name}
                <span class="team">{account.team_name}</span>
              {/if}
              <span class="auth-badge">{account.auth_mode}</span>
            </div>
          </div>
          <div class="account-quota">
            <div class="quota-row">
              <div class="quota-bar">
                <div class="quota-fill" style="width: {account.quota?.hourly_percent || 0}%"></div>
              </div>
              <span class="quota-label" class:low={account.quota?.hourly_percent < 20}>{account.quota?.hourly_percent || 0}%</span>
              <span class="quota-window">5h</span>
            </div>
            <div class="quota-row">
              <div class="quota-bar">
                <div class="quota-fill" style="width: {account.quota?.weekly_percent || 0}%"></div>
              </div>
              <span class="quota-label" class:low={account.quota?.weekly_percent < 20}>{account.quota?.weekly_percent || 0}%</span>
              <span class="quota-window">7d</span>
            </div>
          </div>
        </div>
        <div class="account-actions">
          <button
            class={sel ? 'danger' : 'primary'}
            onclick={() => toggleAccount(account.id, !sel)}
          >
            {sel ? '禁用' : '启用'}
          </button>
          <button class="danger" onclick={() => deleteAccount(account.id)}>删除</button>
        </div>
      </div>
    {:else}
      <p class="empty">还没有导入账号。点击上方按钮导入 auth.json。</p>
    {/each}
  </div>
</div>

<style>
  .page { max-width: 800px; }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }
  .header h1 { font-size: 20px; font-weight: 700; }
  .actions { display: flex; gap: 8px; }

  .import-panel {
    margin-bottom: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .import-row { display: flex; gap: 8px; }
  .import-row input { flex: 1; }
  .error { color: var(--danger); font-size: 13px; }

  .account-list { display: flex; flex-direction: column; gap: 10px; }

  .account-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .account-main { flex: 1; display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  .account-info h3 { font-size: 15px; margin-bottom: 2px; }
  .email { font-size: 12px; color: var(--text-muted); }
  .tags { display: flex; gap: 6px; margin-top: 4px; align-items: center; flex-wrap: wrap; }
  .team { font-size: 12px; color: var(--text-muted); }
  .auth-badge { font-size: 11px; color: var(--accent); text-transform: uppercase; }

  .account-quota { display: flex; flex-direction: column; gap: 4px; min-width: 130px; }
  .quota-row { display: flex; align-items: center; gap: 6px; }
  .quota-bar {
    width: 80px;
    height: 5px;
    background: var(--border);
    border-radius: 2.5px;
    overflow: hidden;
  }
  .quota-fill {
    height: 100%;
    background: var(--success);
    border-radius: 2.5px;
    transition: width 0.3s;
  }
  .quota-label { font-size: 13px; font-weight: 600; min-width: 28px; text-align: right; }
  .quota-label.low { color: var(--warning); }
  .quota-window { font-size: 10px; color: var(--text-muted); min-width: 14px; }

  .account-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .empty { color: var(--text-muted); text-align: center; padding: 40px; }

  .error-banner {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--danger);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
  }
  .error-banner .mismatch-close {
    margin-left: auto;
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 16px;
    cursor: pointer;
    padding: 0 4px;
  }
  .error-banner .mismatch-close:hover { color: var(--text); }
</style>
