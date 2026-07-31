<script>
  import { SvelteSet } from 'svelte/reactivity';
  import { apiClient as defaultApiClient } from '../lib/apiClient.js';
  import { getCodexPlanPresentation } from '../lib/codexPlans.js';
  import { formatQuotaReset } from '../lib/quotaTime.js';

  let { apiClient = defaultApiClient, pollIntervalMs = 5000 } = $props();

  let config = $state(null);
  let accounts = $state(null);
  let initialLoading = $state(true);
  let showImport = $state(false);
  let importing = $state(false);
  let deleteTarget = $state(null);
  let duplicate = $state(null);
  let pendingImport = $state(null);
  let unsupportedModal = $state(false);
  let importJson = $state('');
  let importName = $state('');
  let errorMsg = $state('');
  let nowMs = $state(Date.now());
  const refreshingIds = new SvelteSet();

  async function refreshAll() {
    try {
      const [cfg, acc] = await Promise.all([
        apiClient('GET', '/api/config'),
        apiClient('GET', '/api/accounts'),
      ]);
      config = cfg;
      accounts = acc;
    } catch (e) {
      errorMsg = '读取配置失败: ' + String(e);
    } finally {
      initialLoading = false;
    }
  }

  function retryInitialLoad() {
    initialLoading = true;
    void refreshAll();
  }

  function showImportedAccount(imported) {
    const currentAccounts = accounts ?? [];
    const current = currentAccounts.find((account) => account.id === imported.id);
    const next = current
      ? {
          ...current,
          ...imported,
          plan_type: imported.plan_type || current.plan_type,
          subscription_expires_at:
            imported.subscription_expires_at ?? current.subscription_expires_at,
          team_name: imported.team_name || current.team_name,
          quota: imported.quota?.queried_at ? imported.quota : current.quota,
        }
      : imported;

    accounts = current
      ? currentAccounts.map((account) => (account.id === next.id ? next : account))
      : [...currentAccounts, next];

    if (config?.api && !config.api.selected_accounts.includes(next.id)) {
      config = {
        ...config,
        api: {
          ...config.api,
          selected_accounts: [...config.api.selected_accounts, next.id],
        },
      };
    }
  }

  function replaceAccount(updated) {
    accounts = (accounts ?? []).map((account) => (account.id === updated.id ? updated : account));
  }

  async function refreshAccount(accountId) {
    if (refreshingIds.has(accountId)) return;
    refreshingIds.add(accountId);

    try {
      const updated = await apiClient('POST', '/api/accounts/' + accountId + '/refresh');
      replaceAccount(updated);
    } catch (e) {
      errorMsg = '刷新账号失败: ' + String(e);
    } finally {
      refreshingIds.delete(accountId);
    }
  }

  function completeImport(result) {
    showImportedAccount(result);
    void refreshAccount(result.id);
  }

  function handleImportError(error, pending, prefix) {
    const msg = String(error);
    if (msg.includes('只支持 ChatGPT')) {
      unsupportedModal = true;
    } else if (msg.includes('DUPLICATE:')) {
      duplicate = msg.split('DUPLICATE:')[1].trim();
      pendingImport = pending;
    } else {
      errorMsg = prefix + msg;
    }
  }

  function cancelDuplicate() {
    duplicate = null;
    pendingImport = null;
  }

  async function importAccount() {
    errorMsg = '';
    const pending = {
      json: importJson.trim(),
      name: importName.trim() || 'Codex Account',
    };
    if (!pending.json) {
      errorMsg = '导入失败: 请粘贴 auth.json 内容';
      return;
    }

    showImport = false;
    importing = true;
    try {
      const result = await apiClient('POST', '/api/accounts/import', {
        auth_json: pending.json,
        name: pending.name,
      });
      importJson = '';
      importName = '';
      completeImport(result);
    } catch (e) {
      handleImportError(e, pending, '导入失败: ');
    } finally {
      importing = false;
    }
  }

  async function overrideImport() {
    if (!pendingImport) return;
    const pending = pendingImport;
    cancelDuplicate();
    errorMsg = '';
    importing = true;
    try {
      let result;
      if (pending.fromCodex) {
        result = await apiClient('POST', '/api/accounts/import-from-codex', { force: true });
      } else {
        result = await apiClient('POST', '/api/accounts/import', {
          auth_json: pending.json,
          name: pending.name,
          force: true,
        });
      }
      completeImport(result);
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
      const result = await apiClient('POST', '/api/accounts/import-from-codex');
      completeImport(result);
    } catch (e) {
      handleImportError(e, { fromCodex: true }, '从 ~/.codex 导入失败: ');
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
      await apiClient('DELETE', '/api/accounts/' + id);
      await refreshAll();
    } catch (e) {
      errorMsg = '删除失败: ' + String(e);
      await refreshAll();
    }
  }

  async function toggleAccount(id, enabled) {
    errorMsg = '';
    try {
      await apiClient('PUT', '/api/accounts/' + id + '/toggle', { enabled });
      await refreshAll();
    } catch (e) {
      errorMsg = String(e);
      await refreshAll();
    }
  }

  function selectedIds() {
    return new Set(config?.api?.selected_accounts || []);
  }

  $effect(() => {
    refreshAll();
    if (pollIntervalMs <= 0) return;
    const interval = setInterval(refreshAll, pollIntervalMs);
    return () => clearInterval(interval);
  });

  $effect(() => {
    const interval = setInterval(() => {
      nowMs = Date.now();
    }, 60_000);
    return () => clearInterval(interval);
  });
</script>

<div class="page">
  <div class="header">
    <h1>账号管理</h1>
    <div class="actions">
      <button onclick={importOfficial} disabled={importing || initialLoading || accounts === null}
        >从 ~/.codex 导入</button
      >
      <button
        class="primary"
        onclick={() => (showImport = !showImport)}
        disabled={importing || initialLoading || accounts === null}
      >
        {showImport ? '取消' : '导入 auth.json'}
      </button>
    </div>
  </div>

  {#if showImport}
    <div class="card import-panel">
      <textarea bind:value={importJson} placeholder="粘贴 auth.json 内容..."></textarea>
      <div class="import-row">
        <input bind:value={importName} placeholder="显示名称（可选）" />
        <button class="primary" onclick={importAccount} disabled={importing}>导入</button>
      </div>
    </div>
  {/if}

  {#if deleteTarget}
    <div class="modal-layer">
      <button
        class="modal-backdrop"
        type="button"
        aria-label="取消删除"
        onclick={() => (deleteTarget = null)}
      ></button>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
        <h3 id="delete-dialog-title">确认删除</h3>
        <p>此操作不可撤销，该账号的所有凭据将被移除。</p>
        <div class="modal-actions">
          <button class="danger" onclick={confirmDelete}>删除</button>
          <button onclick={() => (deleteTarget = null)}>取消</button>
        </div>
      </div>
    </div>
  {/if}

  {#if importing}
    <div class="toast">正在导入...</div>
  {/if}

  {#if errorMsg}
    <div class="toast error">
      {errorMsg} <button class="toast-close" onclick={() => (errorMsg = '')}>✕</button>
    </div>
  {/if}

  {#if duplicate}
    <div class="modal-layer">
      <button class="modal-backdrop" type="button" aria-label="取消覆盖" onclick={cancelDuplicate}
      ></button>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="duplicate-dialog-title">
        <h3 id="duplicate-dialog-title">重复账号</h3>
        <p>该邮箱已存在账号，是否覆盖更新？覆盖将刷新凭据信息。</p>
        <div class="modal-actions">
          <button class="danger" onclick={overrideImport}>覆盖</button>
          <button onclick={cancelDuplicate}>取消</button>
        </div>
      </div>
    </div>
  {/if}

  {#if unsupportedModal}
    <div class="modal-layer">
      <button
        class="modal-backdrop"
        type="button"
        aria-label="关闭提示"
        onclick={() => (unsupportedModal = false)}
      ></button>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="unsupported-dialog-title">
        <h3 id="unsupported-dialog-title">不支持的认证方式</h3>
        <p>Codex Cockpit Lite 只支持 ChatGPT 登录。</p>
        <div class="modal-actions">
          <button onclick={() => (unsupportedModal = false)}>我知道了</button>
        </div>
      </div>
    </div>
  {/if}

  <div class="account-list" aria-busy={initialLoading}>
    {#if initialLoading}
      <div class="loading-state" role="status" aria-live="polite">
        <span>正在读取账号…</span>
        {#each [0, 1] as placeholder (placeholder)}
          <div class="card account-skeleton" aria-hidden="true">
            <div class="skeleton-info">
              <span class="skeleton-line skeleton-name"></span>
              <span class="skeleton-line skeleton-email"></span>
            </div>
            <div class="skeleton-quota">
              <span class="skeleton-line"></span>
              <span class="skeleton-line"></span>
            </div>
          </div>
        {/each}
      </div>
    {:else if accounts?.length}
      {#each accounts as account (account.id)}
        {@const sel = selectedIds().has(account.id)}
        {@const plan = getCodexPlanPresentation(account.plan_type)}
        <div class="card account-card">
          <div class="account-main">
            <div class="account-info">
              <h3>{account.name || account.email || account.id.slice(0, 8)}</h3>
              <span class="email">{account.email}</span>
              <div class="tags">
                {#if account.plan_type}
                  <span class="badge {plan.className}">
                    {plan.label}
                  </span>
                {/if}
                {#if account.team_name}
                  <span class="team">{account.team_name}</span>
                {/if}
              </div>
            </div>
            <div class="account-quota">
              {#if refreshingIds.has(account.id)}
                <span
                  class="refresh-indicator"
                  role="status"
                  aria-label="正在刷新 {account.email || account.name}"
                >
                  <span class="refresh-spin">⟳</span>
                </span>
              {/if}
              <div class="quota-row">
                <div class="quota-bar">
                  <div
                    class="quota-fill"
                    style="width: {account.quota?.weekly_percent || 0}%"
                  ></div>
                </div>
                <span class="quota-pct" class:low={account.quota?.weekly_percent < 20}
                  >{account.quota?.weekly_percent || 0}%</span
                >
                <span class="quota-reset"
                  >{formatQuotaReset(account.quota?.weekly_resets_at, nowMs)}</span
                >
              </div>
              <div class="quota-row">
                <div class="quota-bar">
                  <div
                    class="quota-fill"
                    style="width: {account.quota?.hourly_percent || 0}%"
                  ></div>
                </div>
                <span class="quota-pct" class:low={account.quota?.hourly_percent < 20}
                  >{account.quota?.hourly_percent || 0}%</span
                >
                <span class="quota-reset"
                  >{formatQuotaReset(account.quota?.hourly_resets_at, nowMs)}</span
                >
              </div>
            </div>
          </div>
          <div class="account-actions">
            <button
              onclick={() => refreshAccount(account.id)}
              disabled={refreshingIds.has(account.id)}
              aria-label="刷新 {account.email || account.name}"
            >
              刷新
            </button>
            <button
              class={sel ? 'danger' : 'primary'}
              onclick={() => toggleAccount(account.id, !sel)}
            >
              {sel ? '禁用' : '启用'}
            </button>
            <button class="danger" onclick={() => (deleteTarget = account.id)}>删除</button>
          </div>
        </div>
      {/each}
    {:else if accounts !== null}
      <p class="empty">还没有导入账号。点击上方按钮导入 auth.json。</p>
    {:else}
      <div class="card load-error">
        <span>账号数据未能加载。</span>
        <button onclick={retryInitialLoad}>重试</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .page {
    max-width: 800px;
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
  }
  .actions {
    display: flex;
    gap: 8px;
  }

  .import-panel {
    margin-bottom: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .import-row {
    display: flex;
    gap: 8px;
  }
  .import-row input {
    flex: 1;
  }
  .error {
    color: var(--danger);
    font-size: 13px;
  }

  .account-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .loading-state {
    display: flex;
    flex-direction: column;
    gap: 10px;
    color: var(--text-muted);
    font-size: 13px;
  }

  .account-skeleton {
    min-height: 86px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
  }

  .skeleton-info,
  .skeleton-quota {
    display: flex;
    flex-direction: column;
    gap: 9px;
  }

  .skeleton-info {
    width: 220px;
  }

  .skeleton-quota {
    width: 240px;
  }

  .skeleton-line {
    display: block;
    width: 100%;
    height: 8px;
    border-radius: 4px;
    background: linear-gradient(90deg, var(--border) 25%, #343442 50%, var(--border) 75%);
    background-size: 200% 100%;
    animation: shimmer 1.2s ease-in-out infinite;
  }

  .skeleton-name {
    width: 65%;
    height: 12px;
  }

  .skeleton-email {
    width: 85%;
  }

  .load-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--text-muted);
    font-size: 13px;
  }

  @keyframes shimmer {
    from {
      background-position: 200% 0;
    }
    to {
      background-position: -200% 0;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .skeleton-line {
      animation: none;
    }
  }

  .account-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .account-main {
    flex: 1;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .account-info h3 {
    font-size: 15px;
    margin-bottom: 2px;
  }
  .email {
    font-size: 12px;
    color: var(--text-muted);
  }
  .tags {
    display: flex;
    gap: 6px;
    margin-top: 4px;
    align-items: center;
    flex-wrap: wrap;
  }
  .team {
    font-size: 12px;
    color: var(--text-muted);
  }

  .account-quota {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-right: 20px;
  }
  .quota-row {
    display: grid;
    grid-template-columns: 80px 32px 128px;
    align-items: center;
    gap: 6px;
  }
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
  .quota-pct {
    font-size: 12px;
    font-weight: 600;
    width: 32px;
    text-align: right;
  }
  .quota-pct.low {
    color: var(--warning);
  }
  .refresh-indicator {
    position: absolute;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
  }
  .refresh-spin {
    display: block;
    font-size: 14px;
    color: var(--accent);
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }
  .quota-reset {
    font-size: 10px;
    color: var(--text-muted);
    width: 128px;
    white-space: nowrap;
    text-align: left;
  }

  .account-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }
  .empty {
    color: var(--text-muted);
    text-align: center;
    padding: 40px;
  }

  .modal-layer {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .modal-backdrop {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: rgba(0, 0, 0, 0.6);
  }
  .modal {
    position: relative;
    z-index: 1;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    min-width: 340px;
    max-width: 400px;
    box-shadow: 0 8px 40px rgba(0, 0, 0, 0.4);
  }
  .modal h3 {
    font-size: 16px;
    margin-bottom: 8px;
  }
  .modal p {
    font-size: 13px;
    color: var(--text-muted);
    margin-bottom: 20px;
    line-height: 1.5;
  }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  .toast {
    position: fixed;
    top: 16px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--accent);
    color: white;
    padding: 8px 20px;
    border-radius: 8px;
    font-size: 14px;
    z-index: 200;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .toast.error {
    background: var(--danger);
  }
  .toast-close {
    background: none;
    border: none;
    color: white;
    font-size: 14px;
    cursor: pointer;
    padding: 0 2px;
  }
</style>
