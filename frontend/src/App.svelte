<script>
  import Accounts from './routes/Accounts.svelte';
  import Service from './routes/Service.svelte';
  import Settings from './routes/Settings.svelte';

  let page = $state('accounts');

  const navItems = [
    { id: 'accounts', label: '账号管理' },
    { id: 'service', label: 'API 服务' },
    { id: 'settings', label: '设置' },
  ];
</script>

<div class="layout">
  <nav class="sidebar">
    <div class="brand">
      <h2>Codex Cockpit</h2>
      <span class="version">Lite v0.1</span>
    </div>
    {#each navItems as item}
      <button
        class="nav-btn"
        class:active={page === item.id}
        onclick={() => page = item.id}
      >
        {item.label}
      </button>
    {/each}
  </nav>
  <main class="content">
    {#if page === 'accounts'}
      <Accounts />
    {:else if page === 'service'}
      <Service />
    {:else if page === 'settings'}
      <Settings />
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
    background: rgba(255,255,255,0.05);
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
</style>
