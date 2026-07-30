<script>
  import { invoke } from '@tauri-apps/api/core';

  async function api(method, path, body) {
    const text = await invoke('api_call', { method, path, body: body ? JSON.stringify(body) : null });
    return JSON.parse(text);
  }
  let config = $state(null);
  let accounts = $state([]);
  let showImport = $state(false);
  let importing = $state(false);
  let deleteTarget = $state(null);
  let duplicate = $state(null);
  let refreshing = $state([]);
  $effect(() => { console.log('[refreshing]', refreshing.slice()); });
  let pendingImport = $state(null);
  let unsupportedModal = $state(false);
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
    // Close panel immediately, show loading
    showImport = false;
    importJson = '';
    importName = '';
    importing = true;
    try {
      const result = await api('POST', '/api/accounts/import', {
        auth_json: importJson.trim() || '{}',
        name: importName.trim() || 'Codex Account',
      });
      refreshing.push(result.id); refreshing = refreshing;
      api('POST', '/api/accounts/' + result.id + '/refresh').then(() => {
        refreshing.splice(refreshing.indexOf(result.id), 1); refreshing = refreshing;
      }).catch(() => {
        refreshing.splice(refreshing.indexOf(result.id), 1); refreshing = refreshing;
      });
      await refreshAll();
    } catch (e) {
      const msg = String(e);
      if (msg.includes('只支持 ChatGPT')) { unsupportedModal = true; }
      else if (msg.includes('DUPLICATE:')) {
        duplicate = msg.split('DUPLICATE:')[1].trim();
        pendingImport = { json: importJson.trim() || '{}', name: importName.trim() || 'Codex Account' };
      }
      else { errorMsg = '导入失败: ' + msg; }
    } finally {
      importing = false;
    }
  }

  async function overrideImport() {
    if (!pendingImport) return;
    importing = true;
    try {
      if (pendingImport.fromCodex) {
        await api('POST', '/api/accounts/import-from-codex', { force: true });
      } else {
        await api('POST', '/api/accounts/import', {
          auth_json: pendingImport.json,
          name: pendingImport.name,
          force: true,
        });
      }
      // Refresh quota for the overwritten account
      refreshing.push(duplicate); refreshing = refreshing;
      api('POST', '/api/accounts/' + duplicate + '/refresh').then(() => {
        refreshing.splice(refreshing.indexOf(duplicate), 1); refreshing = refreshing;
      }).catch(() => {
        refreshing.splice(refreshing.indexOf(duplicate), 1); refreshing = refreshing;
      });
      duplicate = null;
      pendingImport = null;
      await refreshAll();
    } catch (e) {
      errorMsg = '覆盖导入失败: ' + String(e);
    } finally {
      importing = false;
    }
  }

  async function importOfficial() {
    errorMsg = '';
    importing = true;
    try {
      const result = await api('POST', '/api/accounts/import-from-codex');
      refreshing.push(result.id); refreshing = refreshing;
      api('POST', '/api/accounts/' + result.id + '/refresh').then(() => {
        refreshing.splice(refreshing.indexOf(result.id), 1); refreshing = refreshing;
      }).catch(() => {
        refreshing.splice(refreshing.indexOf(result.id), 1); refreshing = refreshing;
      });
      await refreshAll();
    } catch (e) {
      const msg = String(e);
      if (msg.includes('只支持 ChatGPT')) { unsupportedModal = true; }
      else if (msg.includes('DUPLICATE:')) { duplicate = msg.split('DUPLICATE:')[1].trim(); pendingImport = { fromCodex: true }; }
      else { errorMsg = '从 ~/.codex 导入失败: ' + msg; }
    } finally {
      importing = false;
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const id = deleteTarget;
    deleteTarget = null;
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

  {#if deleteTarget}
    <div class="modal-backdrop" onclick={() => deleteTarget = null}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h3>确认删除</h3>
        <p>此操作不可撤销，该账号的所有凭据将被移除。</p>
        <div class="modal-actions">
          <button class="danger" onclick={confirmDelete}>删除</button>
          <button onclick={() => deleteTarget = null}>取消</button>
        </div>
      </div>
    </div>
  {/if}

  {#if importing}
    <p class="loading">正在导入...</p>
  {/if}

  {#if duplicate}
    <div class="modal-backdrop" onclick={() => duplicate = null}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h3>重复账号</h3>
        <p>该邮箱已存在账号，是否覆盖更新？覆盖将刷新凭据信息。</p>
        <div class="modal-actions">
          <button class="danger" onclick={overrideImport}>覆盖</button>
          <button onclick={() => duplicate = null}>取消</button>
        </div>
      </div>
    </div>
  {/if}

  {#if unsupportedModal}
    <div class="modal-backdrop" onclick={() => unsupportedModal = false}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h3>不支持的认证方式</h3>
        <p>Codex Cockpit Lite 当前仅支持 ChatGPT (OAuth) 登录方式。API Key 和 Agent Identity 暂不可用。</p>
        <div class="modal-actions">
          <button onclick={() => unsupportedModal = false}>我知道了</button>
        </div>
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
            </div>
          </div>
          <div class="account-quota">
            <div class="quota-row">
              <div class="quota-bar">
                <div class="quota-fill" style="width: {account.quota?.weekly_percent || 0}%"></div>
              </div>
              {#if refreshing.includes(account.id)}
                <span class="refresh-spin">⟳</span>
              {/if}
              <span class="quota-pct" class:low={account.quota?.weekly_percent < 20}>{account.quota?.weekly_percent || 0}%</span>
              <span class="quota-col">5h</span>
            </div>
            <div class="quota-row">
              <div class="quota-bar">
                <div class="quota-fill" style="width: {account.quota?.hourly_percent || 0}%"></div>
              </div>
              <span class="quota-pct" class:low={account.quota?.hourly_percent < 20}>{account.quota?.hourly_percent || 0}%</span>
              <span class="quota-col">7d</span>
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
          <button class="danger" onclick={() => deleteTarget = account.id}>删除</button>
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

  .account-quota { display: flex; flex-direction: column; gap: 2px; }
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
  .quota-pct { font-size: 12px; font-weight: 600; width: 32px; text-align: right; }
  .quota-pct.low { color: var(--warning); }
  .refresh-spin { font-size: 14px; color: var(--accent); animation: spin 1s linear infinite; }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  .quota-col { font-size: 10px; color: var(--text-muted); width: 24px; text-align: right; }

  .account-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .empty { color: var(--text-muted); text-align: center; padding: 40px; }

  .loading { color: var(--accent); text-align: center; padding: 12px; font-size: 14px; }

  .modal-backdrop {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    min-width: 340px;
    max-width: 400px;
    box-shadow: 0 8px 40px rgba(0,0,0,0.4);
  }
  .modal h3 { font-size: 16px; margin-bottom: 8px; }
  .modal p { font-size: 13px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.5; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 10px; }

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
