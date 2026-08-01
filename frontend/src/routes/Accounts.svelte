<script>
  import Toast from '../lib/Toast.svelte';
  import { tick } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { apiClient as defaultApiClient } from '../lib/apiClient.js';
  import { browserLogin as defaultBrowserLogin } from '../lib/browserLogin.js';
  import { getCodexPlanPresentation } from '../lib/codexPlans.js';
  import { formatQuotaReset } from '../lib/quotaTime.js';

  let {
    apiClient = defaultApiClient,
    browserLoginClient = defaultBrowserLogin,
    pollIntervalMs = 5000,
  } = $props();

  let config = $state(null);
  let accounts = $state(null);
  let initialLoading = $state(true);
  let showImport = $state(false);
  let importing = $state(false);
  let browserLoggingIn = $state(false);
  let deleteTarget = $state(null);
  let duplicate = $state(null);
  let pendingImport = $state(null);
  let unsupportedModal = $state(false);

  function dismissError() {
    errorMsg = '';
  }
  function dismissNotice() {
    noticeMsg = '';
  }
  let importJson = $state('');
  let importName = $state('');
  let editingAccountId = $state(null);
  let editingDisplayName = $state('');
  let displayNameInput = $state(null);
  let savingDisplayName = $state(false);
  let errorMsg = $state('');
  let noticeMsg = $state('');
  let nowMs = $state(Date.now());
  let draggedAccountId = $state(null);
  let dropIndicator = $state(null);
  let savingOrder = $state(false);
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
          quota: imported.quota ?? current.quota,
        }
      : imported;

    accounts = current
      ? currentAccounts.map((account) => (account.id === next.id ? next : account))
      : [...currentAccounts, next];

    if (config?.api && !config.api.selected_accounts.includes(next.id)) {
      const currentOrder = config.api.account_order ?? [];
      config = {
        ...config,
        api: {
          ...config.api,
          account_order: currentOrder.includes(next.id) ? currentOrder : [...currentOrder, next.id],
          selected_accounts: [...config.api.selected_accounts, next.id],
        },
      };
    }
  }

  function hasRemainingQuota(account) {
    return (
      !account.requires_reauth &&
      (account.quota?.weekly_percent ?? 0) > 0 &&
      (account.quota?.hourly_percent ?? 0) > 0
    );
  }

  function formatQuotaPercentage(value) {
    return Number.isFinite(value) ? `${value}%` : '--';
  }

  function replaceAccount(updated) {
    const selected = selectedIds().has(updated.id);
    accounts = (accounts ?? []).map((account) =>
      account.id === updated.id
        ? {
            ...account,
            ...updated,
            schedulable: selected && hasRemainingQuota(updated),
            is_active: account.is_active && selected && hasRemainingQuota(updated),
          }
        : account,
    );
  }

  function automaticAccountName(account) {
    return account.name || account.email?.split('@')[0] || account.id.slice(0, 8);
  }

  function accountTitle(account) {
    return account.display_name || automaticAccountName(account);
  }

  async function startEditingDisplayName(account) {
    editingAccountId = account.id;
    editingDisplayName = account.display_name || '';
    await tick();
    displayNameInput?.focus();
    displayNameInput?.select();
  }

  function cancelEditingDisplayName() {
    editingAccountId = null;
    editingDisplayName = '';
  }

  async function saveDisplayName(accountId) {
    if (editingAccountId !== accountId || savingDisplayName) return;
    savingDisplayName = true;
    try {
      const updated = await apiClient('PUT', '/api/accounts/' + accountId + '/display-name', {
        display_name: editingDisplayName,
      });
      replaceAccount(updated);
      cancelEditingDisplayName();
    } catch (e) {
      errorMsg = '修改显示名称失败: ' + String(e);
    } finally {
      savingDisplayName = false;
    }
  }

  function handleDisplayNameKeydown(event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelEditingDisplayName();
    }
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
      name: importName.trim(),
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

  async function loginWithBrowser(reauthAccountId = null) {
    if (browserLoggingIn) return;
    errorMsg = '';
    noticeMsg = '';
    browserLoggingIn = true;
    try {
      const result = await browserLoginClient(reauthAccountId);
      showImportedAccount(result);
      if (reauthAccountId && result.id !== reauthAccountId) {
        noticeMsg = `登录的是另一个账号，已新增 ${result.email || accountTitle(result)}；原账号仍需重新登录`;
      } else {
        noticeMsg = reauthAccountId ? '重新登录成功' : '浏览器登录成功';
      }
      await refreshAccount(result.id);
    } catch (e) {
      errorMsg = (reauthAccountId ? '重新登录失败: ' : '浏览器登录失败: ') + String(e);
      await refreshAll();
    } finally {
      browserLoggingIn = false;
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

  async function activateAccount(accountId) {
    errorMsg = '';
    try {
      await apiClient('POST', '/api/accounts/' + accountId + '/activate');
      accounts = (accounts ?? []).map((account) => ({
        ...account,
        is_active: account.id === accountId,
      }));
    } catch (e) {
      errorMsg = '切换账号失败: ' + String(e);
      await refreshAll();
    }
  }

  function requestAccountActivation(account, schedulable) {
    noticeMsg = '';
    if (account.is_active) {
      noticeMsg = '已经在调度此账号';
      return;
    }
    if (!schedulable) {
      noticeMsg = '当前账号不可调度';
      return;
    }
    void activateAccount(account.id);
  }

  function startAccountDrag(event, accountId) {
    if (savingOrder) {
      event.preventDefault();
      return;
    }
    draggedAccountId = accountId;
    dropIndicator = null;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', accountId);
  }

  function accountDropPosition(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointerY = Number.isFinite(event.clientY) ? event.clientY : bounds.top;
    return pointerY < bounds.top + bounds.height / 2 ? 'before' : 'after';
  }

  function updateAccountDropIndicator(event, targetId) {
    if (draggedAccountId && draggedAccountId !== targetId) {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      dropIndicator = { targetId, position: accountDropPosition(event) };
    }
  }

  function updateAccountListDropIndicator(event) {
    if (event.target !== event.currentTarget || !draggedAccountId || !accounts?.length) return;
    const lastAccountId = accounts.at(-1)?.id;
    if (!lastAccountId || draggedAccountId === lastAccountId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    dropIndicator = { targetId: lastAccountId, position: 'after' };
  }

  function finishAccountDrag() {
    draggedAccountId = null;
    dropIndicator = null;
  }

  async function dropAccount(event, targetId, forcedPosition = null) {
    event.preventDefault();
    const sourceId = draggedAccountId || event.dataTransfer.getData('text/plain');
    const position =
      forcedPosition ??
      (dropIndicator?.targetId === targetId ? dropIndicator.position : accountDropPosition(event));
    finishAccountDrag();
    if (!sourceId || sourceId === targetId || savingOrder || !config) return;

    const currentOrder = (accounts ?? []).map((account) => account.id);
    const sourceIndex = currentOrder.indexOf(sourceId);
    if (sourceIndex < 0 || !currentOrder.includes(targetId)) return;

    const nextOrder = [...currentOrder];
    nextOrder.splice(sourceIndex, 1);
    const targetIndex = nextOrder.indexOf(targetId);
    const insertionIndex = targetIndex + (position === 'after' ? 1 : 0);
    nextOrder.splice(insertionIndex, 0, sourceId);
    const positions = new Map(nextOrder.map((id, index) => [id, index]));
    const previousAccounts = accounts;
    const previousConfig = config;
    const selected = selectedIds();
    const nextSelectedAccounts = nextOrder.filter((accountId) => selected.has(accountId));
    config = {
      ...config,
      api: {
        ...config.api,
        account_order: nextOrder,
        selected_accounts: nextSelectedAccounts,
      },
    };
    accounts = [...(accounts ?? [])].sort((left, right) => {
      const leftPosition = positions.get(left.id) ?? Number.MAX_SAFE_INTEGER;
      const rightPosition = positions.get(right.id) ?? Number.MAX_SAFE_INTEGER;
      return leftPosition - rightPosition;
    });

    savingOrder = true;
    errorMsg = '';
    try {
      await apiClient('PUT', '/api/accounts/order', { account_ids: nextOrder });
    } catch (e) {
      config = previousConfig;
      accounts = previousAccounts;
      errorMsg = '保存账号顺序失败: ' + String(e);
      await refreshAll();
    } finally {
      savingOrder = false;
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
      <button
        onclick={() => loginWithBrowser()}
        disabled={browserLoggingIn || importing || initialLoading || accounts === null}
        >浏览器登录</button
      >
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

  {#if browserLoggingIn}
    <div class="toast">正在等待浏览器登录...</div>
  {/if}

  {#if errorMsg}
    <Toast message={errorMsg} tone="error" onDismiss={dismissError} closeLabel="关闭错误提示" />
  {/if}

  {#if noticeMsg}
    <Toast message={noticeMsg} tone="info" onDismiss={dismissNotice} closeLabel="关闭切换提示" />
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

  <div
    class="account-list"
    role="list"
    aria-busy={initialLoading}
    ondragover={updateAccountListDropIndicator}
    ondrop={(event) => {
      if (event.target !== event.currentTarget || !accounts?.length) return;
      void dropAccount(event, accounts.at(-1).id, 'after');
    }}
  >
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
        {@const schedulable = sel && hasRemainingQuota(account)}
        {@const plan = getCodexPlanPresentation(account.plan_type)}
        <div
          class="card account-card"
          class:active-account={account.is_active}
          class:draggable-account={!savingOrder}
          class:dragging={draggedAccountId === account.id}
          class:drop-before={dropIndicator?.targetId === account.id &&
            dropIndicator.position === 'before'}
          class:drop-after={dropIndicator?.targetId === account.id &&
            dropIndicator.position === 'after'}
          role="listitem"
          draggable={!savingOrder}
          title="拖动卡片调整账号顺序"
          ondragstart={(event) => startAccountDrag(event, account.id)}
          ondragover={(event) => updateAccountDropIndicator(event, account.id)}
          ondrop={(event) => dropAccount(event, account.id)}
          ondragend={finishAccountDrag}
        >
          <span class="drag-grip" aria-hidden="true">⠿</span>
          <div class="account-main">
            <div class="account-info">
              {#if editingAccountId === account.id}
                <input
                  class="account-name-input"
                  bind:this={displayNameInput}
                  bind:value={editingDisplayName}
                  placeholder={automaticAccountName(account)}
                  maxlength="100"
                  aria-label="编辑 {automaticAccountName(account)} 的显示名称"
                  disabled={savingDisplayName}
                  onblur={() => saveDisplayName(account.id)}
                  onkeydown={handleDisplayNameKeydown}
                />
              {:else}
                <button
                  class="account-name"
                  title="双击编辑显示名称"
                  aria-label="双击编辑 {accountTitle(account)} 的显示名称"
                  onclick={(event) => {
                    if (event.detail === 0) void startEditingDisplayName(account);
                  }}
                  ondblclick={() => startEditingDisplayName(account)}
                >
                  {accountTitle(account)}
                </button>
              {/if}
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
                {#if account.is_active}
                  <span class="active-indicator">当前调度</span>
                {/if}
                {#if account.requires_reauth}
                  <span class="reauth-indicator">需要重新登录</span>
                {/if}
              </div>
            </div>
            <div class="account-quota">
              {#if refreshingIds.has(account.id)}
                <span
                  class="refresh-indicator"
                  role="status"
                  aria-label="正在刷新 {account.email || accountTitle(account)}"
                >
                  <span class="refresh-spin">⟳</span>
                </span>
              {/if}
              <div class="quota-row">
                <div class="quota-bar">
                  <div
                    class="quota-fill"
                    style="width: {account.quota?.weekly_percent ?? 0}%"
                  ></div>
                </div>
                <span
                  class="quota-pct"
                  class:low={Number.isFinite(account.quota?.weekly_percent) &&
                    account.quota.weekly_percent < 20}
                  >{formatQuotaPercentage(account.quota?.weekly_percent)}</span
                >
                <span class="quota-reset"
                  >{formatQuotaReset(account.quota?.weekly_resets_at, nowMs)}</span
                >
              </div>
              <div class="quota-row">
                <div class="quota-bar">
                  <div
                    class="quota-fill"
                    style="width: {account.quota?.hourly_percent ?? 0}%"
                  ></div>
                </div>
                <span
                  class="quota-pct"
                  class:low={Number.isFinite(account.quota?.hourly_percent) &&
                    account.quota.hourly_percent < 20}
                  >{formatQuotaPercentage(account.quota?.hourly_percent)}</span
                >
                <span class="quota-reset"
                  >{formatQuotaReset(account.quota?.hourly_resets_at, nowMs)}</span
                >
              </div>
            </div>
          </div>
          <div class="account-actions">
            {#if account.requires_reauth}
              <button
                class="primary reauth-action"
                onclick={() => loginWithBrowser(account.id)}
                disabled={browserLoggingIn}
                aria-label="重新登录 {account.email || accountTitle(account)}">重新登录</button
              >
            {:else}
              <button
                class:unavailable={!schedulable || account.is_active}
                aria-disabled={!schedulable || account.is_active}
                onclick={() => requestAccountActivation(account, schedulable)}
                title={!sel
                  ? '请先启用该账号'
                  : !schedulable
                    ? '5h 和 7d 剩余额度必须都大于 0'
                    : account.is_active
                      ? '当前正在调度此账号'
                      : '从此账号开始循环调度'}
              >
                切换
              </button>
              <button
                onclick={() => refreshAccount(account.id)}
                disabled={refreshingIds.has(account.id)}
                aria-label="刷新 {account.email || accountTitle(account)}"
              >
                刷新
              </button>
            {/if}
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
    min-height: 100%;
    display: flex;
    flex-direction: column;
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

  .reauth-indicator {
    color: #fca5a5;
    font-size: 11px;
    font-weight: 600;
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
    flex: 1;
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
    position: relative;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    transition:
      border-color 0.15s ease,
      opacity 0.15s ease;
  }
  .account-card.active-account {
    border-color: var(--accent);
  }
  .account-card.dragging {
    opacity: 0.55;
  }
  .account-card.draggable-account {
    cursor: grab;
  }
  .account-card.draggable-account:active {
    cursor: grabbing;
  }
  .account-card.drop-before::before,
  .account-card.drop-after::after {
    position: absolute;
    left: 0;
    right: 0;
    z-index: 2;
    height: 3px;
    border-radius: 999px;
    background: var(--accent);
    content: '';
    pointer-events: none;
    box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 65%, transparent);
  }
  .account-card.drop-before::before {
    top: -7px;
  }
  .account-card.drop-after::after {
    bottom: -7px;
  }
  .drag-grip {
    width: 20px;
    min-width: 20px;
    color: var(--text-muted);
    text-align: center;
    transition: color 0.15s ease;
  }
  .draggable-account:hover .drag-grip {
    color: var(--text);
  }
  .account-main {
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .account-name {
    display: block;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--text);
    font-size: 15px;
    font-weight: 700;
    line-height: normal;
    text-align: left;
    margin-bottom: 2px;
  }
  .account-name:hover {
    background: transparent;
  }
  .account-name-input {
    width: 220px;
    height: 24px;
    padding: 2px 6px;
    margin: -3px 0 1px -7px;
    font-size: 15px;
    font-weight: 700;
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
  .active-indicator {
    padding: 1px 6px;
    border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
    border-radius: 999px;
    color: var(--accent);
    font-size: 10px;
    font-weight: 600;
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
    display: grid;
    grid-template-columns: repeat(2, 64px);
    gap: 6px;
    flex-shrink: 0;
    justify-content: end;
    margin-left: auto;
  }
  .account-actions button {
    width: 64px;
  }
  .account-actions .reauth-action {
    grid-column: span 2;
    width: 100%;
  }
  .account-actions button.unavailable {
    cursor: not-allowed;
    opacity: 0.5;
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
</style>
