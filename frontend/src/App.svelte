<script>
  import Accounts from './routes/Accounts.svelte';
  import { apiClient as defaultApiClient } from './lib/apiClient.js';
  import Service from './routes/Service.svelte';
  import Settings from './routes/Settings.svelte';

  let { apiClient = defaultApiClient } = $props();
  let page = $state('accounts');
  let backendState = $state('starting');
  let backendError = $state('');

  const navItems = [
    { id: 'accounts', label: '账号管理' },
    { id: 'service', label: 'API 服务' },
    { id: 'settings', label: '设置' },
  ];

  async function connectBackend() {
    backendState = 'starting';
    backendError = '';
    try {
      await apiClient('GET', '/v1/cockpit/status');
      backendState = 'ready';
    } catch (error) {
      backendError = String(error);
      backendState = 'error';
    }
  }

  $effect(() => {
    void connectBackend();
  });
</script>

<div class="layout">
  <nav class="sidebar">
    <div class="brand">
      <h2>Codex Cockpit</h2>
      <span class="version">Lite v0.1</span>
    </div>
    {#each navItems as item (item.id)}
      <button class="nav-btn" class:active={page === item.id} onclick={() => (page = item.id)}>
        {item.label}
      </button>
    {/each}
  </nav>
  <main class="content">
    {#if backendState === 'starting'}
      <section class="startup-state" role="status" aria-live="polite">
        <span class="startup-spinner" aria-hidden="true"></span>
        <strong>正在启动 Codex Cockpit Lite</strong>
        <p>正在连接后端并读取配置…</p>
      </section>
    {:else if backendState === 'error'}
      <section class="startup-state startup-error" role="alert">
        <strong>后端启动失败</strong>
        <p>{backendError}</p>
        <button class="primary" onclick={connectBackend}>重试</button>
      </section>
    {:else if page === 'accounts'}
      <Accounts {apiClient} />
    {:else if page === 'service'}
      <Service {apiClient} />
    {:else if page === 'settings'}
      <Settings {apiClient} />
    {/if}
  </main>
</div>

<style>
  .layout {
    display: flex;
    width: 100%;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .sidebar {
    width: 200px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 16px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex-shrink: 0;
  }

  .brand {
    padding: 0 4px 16px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 8px;
  }

  .brand h2 {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
  }

  .version {
    font-size: 11px;
    color: var(--text-muted);
  }

  .nav-btn {
    text-align: left;
    padding: 8px 10px;
    border: none;
    background: transparent;
    border-radius: 6px;
    font-size: 14px;
  }

  .nav-btn:hover {
    background: rgba(255, 255, 255, 0.05);
  }

  .nav-btn.active {
    background: var(--accent);
    color: white;
  }

  .content {
    flex: 1;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 24px;
  }

  .startup-state {
    min-height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--text);
  }

  .startup-state strong {
    margin-top: 14px;
    font-size: 15px;
  }

  .startup-state p {
    max-width: 560px;
    margin-top: 6px;
    color: var(--text-muted);
    font-size: 13px;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .startup-error button {
    margin-top: 16px;
  }

  .startup-spinner {
    width: 24px;
    height: 24px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .startup-spinner {
      animation: none;
    }
  }
</style>
