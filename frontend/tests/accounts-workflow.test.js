import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import Accounts from '../src/routes/Accounts.svelte';

afterEach(() => cleanup());

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function account(overrides = {}) {
  return {
    id: 'account-1',
    name: 'Test Account',
    display_name: '',
    email: 'test@example.com',
    plan_type: '',
    subscription_expires_at: null,
    team_name: '',
    quota: {
      weekly_percent: 0,
      hourly_percent: 0,
      weekly_resets_at: null,
      hourly_resets_at: null,
      queried_at: 0,
    },
    ...overrides,
  };
}

function config(selectedAccounts = []) {
  return {
    version: 1,
    api: {
      port: 8844,
      selected_accounts: selectedAccounts,
    },
  };
}

test('首次账号请求完成前显示骨架，明确返回空数组后才显示空状态', async () => {
  const configReady = deferred();
  const accountsReady = deferred();
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return configReady.promise;
    if (method === 'GET' && path === '/api/accounts') return accountsReady.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });

  expect(screen.getByText('正在读取账号…')).toBeTruthy();
  expect(screen.queryByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeNull();
  expect(screen.getByRole('button', { name: '从 ~/.codex 导入' }).disabled).toBe(true);
  expect(screen.getByRole('button', { name: '导入 auth.json' }).disabled).toBe(true);

  await act(() => {
    configReady.resolve(config());
    accountsReady.resolve([]);
  });

  expect(await screen.findByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeTruthy();
  expect(screen.queryByText('正在读取账号…')).toBeNull();
  expect(screen.getByRole('button', { name: '从 ~/.codex 导入' }).disabled).toBe(false);
  expect(screen.getByRole('button', { name: '导入 auth.json' }).disabled).toBe(false);
});

test('首次账号请求失败后可重试，并在重试期间恢复骨架状态', async () => {
  const retryAccounts = deferred();
  let accountCalls = 0;
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/api/accounts') {
      accountCalls += 1;
      if (accountCalls === 1) return Promise.reject(new Error('read failed'));
      return retryAccounts.promise;
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });

  expect(await screen.findByText('账号数据未能加载。')).toBeTruthy();
  await fireEvent.click(screen.getByRole('button', { name: '重试' }));

  expect(screen.getByText('正在读取账号…')).toBeTruthy();
  expect(screen.queryByText('账号数据未能加载。')).toBeNull();

  await act(() => retryAccounts.resolve([]));
  expect(await screen.findByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeTruthy();
});

test('导入完成后切换为该账号独立刷新，刷新返回前标记保持可见', async () => {
  const importDone = deferred();
  const refreshDone = deferred();
  let storedAccounts = [];

  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve(storedAccounts);
    if (method === 'POST' && path === '/api/accounts/import') {
      return importDone.promise.then((result) => {
        storedAccounts = [result];
        return result;
      });
    }
    if (method === 'POST' && path === '/api/accounts/account-1/refresh') {
      return refreshDone.promise.then((result) => {
        storedAccounts = [result];
        return result;
      });
    }
    return Promise.reject(
      new Error(`Unexpected API call: ${method} ${path} ${JSON.stringify(body)}`),
    );
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('还没有导入账号。点击上方按钮导入 auth.json。');

  await fireEvent.click(screen.getByRole('button', { name: '导入 auth.json' }));
  await fireEvent.input(screen.getByPlaceholderText('粘贴 auth.json 内容...'), {
    target: { value: '{"auth_mode":"chatgpt"}' },
  });
  await fireEvent.input(screen.getByPlaceholderText('显示名称（可选）'), {
    target: { value: 'Imported Account' },
  });
  await fireEvent.click(screen.getByRole('button', { name: '导入' }));

  expect(screen.getByText('正在导入...')).toBeTruthy();
  expect(apiClient).toHaveBeenCalledWith('POST', '/api/accounts/import', {
    auth_json: '{"auth_mode":"chatgpt"}',
    name: 'Imported Account',
  });

  await act(() => importDone.resolve(account({ name: 'Imported Account' })));

  await screen.findByRole('status', { name: '正在刷新 test@example.com' });
  expect(screen.queryByText('正在导入...')).toBeNull();
  expect(screen.getByRole('button', { name: '刷新 test@example.com' }).disabled).toBe(true);

  const cardWhileRefreshing = screen.getByText('test@example.com').closest('.account-card');
  expect(within(cardWhileRefreshing).getAllByText('0%')).toHaveLength(2);

  await act(() =>
    refreshDone.resolve(
      account({
        name: 'Imported Account',
        plan_type: 'pro',
        quota: {
          weekly_percent: 100,
          hourly_percent: 94,
          weekly_resets_at: null,
          hourly_resets_at: null,
          queried_at: 123,
        },
      }),
    ),
  );

  await waitFor(() =>
    expect(screen.queryByRole('status', { name: '正在刷新 test@example.com' })).toBeNull(),
  );
  const refreshedCard = screen.getByText('test@example.com').closest('.account-card');
  expect(within(refreshedCard).getByText('100%')).toBeTruthy();
  expect(within(refreshedCard).getByText('94%')).toBeTruthy();
  expect(within(refreshedCard).getByText('Pro 20x')).toBeTruthy();
});

test('从 ~/.codex 重复导入可取消，并可确认覆盖后复用原账号', async () => {
  const existing = account();
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') {
      return Promise.resolve(config(['account-1']));
    }
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([existing]);
    if (method === 'POST' && path === '/api/accounts/import-from-codex') {
      if (body?.force) return Promise.resolve(existing);
      return Promise.reject(new Error('DUPLICATE: account-1'));
    }
    if (method === 'POST' && path === '/api/accounts/account-1/refresh') {
      return Promise.resolve(existing);
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');

  await fireEvent.click(screen.getByRole('button', { name: '从 ~/.codex 导入' }));
  let dialog = await screen.findByRole('dialog', { name: '重复账号' });
  await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(screen.queryByRole('dialog', { name: '重复账号' })).toBeNull();
  expect(
    apiClient.mock.calls.some(
      ([method, path, body]) =>
        method === 'POST' && path === '/api/accounts/import-from-codex' && body?.force,
    ),
  ).toBe(false);

  await fireEvent.click(screen.getByRole('button', { name: '从 ~/.codex 导入' }));
  dialog = await screen.findByRole('dialog', { name: '重复账号' });
  await fireEvent.click(within(dialog).getByRole('button', { name: '覆盖' }));

  await waitFor(() =>
    expect(apiClient).toHaveBeenCalledWith('POST', '/api/accounts/import-from-codex', {
      force: true,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: '正在刷新 test@example.com' })).toBeNull(),
  );
  expect(screen.getAllByText('test@example.com')).toHaveLength(1);
});

test('双击账号名称可编辑手动显示名称，清空后恢复自动名称', async () => {
  let stored = account({ name: 'Automatic Team', display_name: 'My Workspace' });
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') {
      return Promise.resolve(config(['account-1']));
    }
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([stored]);
    if (method === 'PUT' && path === '/api/accounts/account-1/display-name') {
      stored = { ...stored, display_name: body.display_name.trim() };
      return Promise.resolve(stored);
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  const title = await screen.findByRole('button', {
    name: '双击编辑 My Workspace 的显示名称',
  });

  await fireEvent.dblClick(title);
  let input = screen.getByRole('textbox', { name: '编辑 Automatic Team 的显示名称' });
  expect(input.value).toBe('My Workspace');
  expect(input.placeholder).toBe('Automatic Team');

  await fireEvent.input(input, { target: { value: 'Renamed Workspace' } });
  await fireEvent.blur(input);
  expect(
    await screen.findByRole('button', { name: '双击编辑 Renamed Workspace 的显示名称' }),
  ).toBeTruthy();
  expect(apiClient).toHaveBeenCalledWith('PUT', '/api/accounts/account-1/display-name', {
    display_name: 'Renamed Workspace',
  });

  await fireEvent.dblClick(
    screen.getByRole('button', { name: '双击编辑 Renamed Workspace 的显示名称' }),
  );
  input = screen.getByRole('textbox', { name: '编辑 Automatic Team 的显示名称' });
  await fireEvent.input(input, { target: { value: '' } });
  await fireEvent.blur(input);

  expect(
    await screen.findByRole('button', { name: '双击编辑 Automatic Team 的显示名称' }),
  ).toBeTruthy();
  expect(apiClient).toHaveBeenLastCalledWith('PUT', '/api/accounts/account-1/display-name', {
    display_name: '',
  });
});

test('手动刷新不进入导入状态，并在返回时同时更新数据和移除标记', async () => {
  const refreshDone = deferred();
  const initial = account({
    quota: {
      weekly_percent: 80,
      hourly_percent: 70,
      weekly_resets_at: null,
      hourly_resets_at: null,
      queried_at: 1,
    },
  });

  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config(['account-1']));
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([initial]);
    if (method === 'POST' && path === '/api/accounts/account-1/refresh') return refreshDone.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');

  await fireEvent.click(screen.getByRole('button', { name: '刷新 test@example.com' }));

  expect(screen.getByRole('status', { name: '正在刷新 test@example.com' })).toBeTruthy();
  expect(screen.queryByText('正在导入...')).toBeNull();
  const pendingCard = screen.getByText('test@example.com').closest('.account-card');
  expect(within(pendingCard).getByText('80%')).toBeTruthy();
  expect(within(pendingCard).getByText('70%')).toBeTruthy();

  await act(() =>
    refreshDone.resolve(
      account({
        quota: {
          weekly_percent: 100,
          hourly_percent: 94,
          weekly_resets_at: null,
          hourly_resets_at: null,
          queried_at: 2,
        },
      }),
    ),
  );

  await waitFor(() =>
    expect(screen.queryByRole('status', { name: '正在刷新 test@example.com' })).toBeNull(),
  );
  const refreshedCard = screen.getByText('test@example.com').closest('.account-card');
  expect(within(refreshedCard).getByText('100%')).toBeTruthy();
  expect(within(refreshedCard).getByText('94%')).toBeTruthy();
  expect(apiClient.mock.calls.filter(([, path]) => path === '/api/accounts')).toHaveLength(1);
});

test('刷新失败会清除该账号标记并显示可读错误', async () => {
  const refreshDone = deferred();
  const initial = account();

  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config(['account-1']));
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([initial]);
    if (method === 'POST' && path === '/api/accounts/account-1/refresh') return refreshDone.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');
  await fireEvent.click(screen.getByRole('button', { name: '刷新 test@example.com' }));
  expect(screen.getByRole('status', { name: '正在刷新 test@example.com' })).toBeTruthy();

  await act(() => refreshDone.reject(new Error('upstream unavailable')));

  await screen.findByText(/刷新账号失败: Error: upstream unavailable/);
  expect(screen.queryByRole('status', { name: '正在刷新 test@example.com' })).toBeNull();
  expect(screen.getByRole('button', { name: '刷新 test@example.com' }).disabled).toBe(false);
});

test('删除确认使用可访问弹窗，并可通过背景按钮取消', async () => {
  const initial = account();
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config(['account-1']));
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([initial]);
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');
  await fireEvent.click(screen.getByRole('button', { name: '删除' }));

  expect(screen.getByRole('dialog', { name: '确认删除' })).toBeTruthy();
  await fireEvent.click(screen.getByRole('button', { name: '取消删除' }));
  expect(screen.queryByRole('dialog', { name: '确认删除' })).toBeNull();
});

test('多个账号的刷新状态彼此独立', async () => {
  const firstRefresh = deferred();
  const secondRefresh = deferred();
  const first = account();
  const second = account({ id: 'account-2', name: 'Second Account', email: 'second@example.com' });

  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') {
      return Promise.resolve(config(['account-1', 'account-2']));
    }
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([first, second]);
    if (method === 'POST' && path === '/api/accounts/account-1/refresh')
      return firstRefresh.promise;
    if (method === 'POST' && path === '/api/accounts/account-2/refresh')
      return secondRefresh.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');
  await screen.findByText('second@example.com');

  await fireEvent.click(screen.getByRole('button', { name: '刷新 test@example.com' }));
  await fireEvent.click(screen.getByRole('button', { name: '刷新 second@example.com' }));
  expect(screen.getByRole('status', { name: '正在刷新 test@example.com' })).toBeTruthy();
  expect(screen.getByRole('status', { name: '正在刷新 second@example.com' })).toBeTruthy();

  await act(() =>
    firstRefresh.resolve(
      account({
        quota: {
          weekly_percent: 100,
          hourly_percent: 94,
          weekly_resets_at: null,
          hourly_resets_at: null,
          queried_at: 2,
        },
      }),
    ),
  );

  await waitFor(() =>
    expect(screen.queryByRole('status', { name: '正在刷新 test@example.com' })).toBeNull(),
  );
  expect(screen.getByRole('status', { name: '正在刷新 second@example.com' })).toBeTruthy();

  await act(() => secondRefresh.resolve(second));
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: '正在刷新 second@example.com' })).toBeNull(),
  );
});

test('显示当前调度账号，只有两个额度都大于零的账号可以强制切换', async () => {
  const active = account({
    is_active: true,
    schedulable: true,
    quota: {
      weekly_percent: 80,
      hourly_percent: 70,
      weekly_resets_at: null,
      hourly_resets_at: null,
      queried_at: 1,
    },
  });
  const available = account({
    id: 'account-2',
    name: 'Available',
    email: 'available@example.com',
    is_active: false,
    schedulable: true,
    quota: {
      weekly_percent: 60,
      hourly_percent: 50,
      weekly_resets_at: null,
      hourly_resets_at: null,
      queried_at: 1,
    },
  });
  const exhausted = account({
    id: 'account-3',
    name: 'Exhausted',
    email: 'exhausted@example.com',
    is_active: false,
    schedulable: false,
    quota: {
      weekly_percent: 100,
      hourly_percent: 0,
      weekly_resets_at: null,
      hourly_resets_at: null,
      queried_at: 1,
    },
  });
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') {
      return Promise.resolve(config(['account-1', 'account-2', 'account-3']));
    }
    if (method === 'GET' && path === '/api/accounts') {
      return Promise.resolve([active, available, exhausted]);
    }
    if (method === 'POST' && path === '/api/accounts/account-2/activate') {
      return Promise.resolve({ ok: true, active_account_id: 'account-2' });
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  const activeCard = (await screen.findByText('test@example.com')).closest('.account-card');
  const availableCard = screen.getByText('available@example.com').closest('.account-card');
  const exhaustedCard = screen.getByText('exhausted@example.com').closest('.account-card');

  expect(within(activeCard).getByText('当前调度')).toBeTruthy();
  const activeSwitch = within(activeCard).getByRole('button', { name: '切换' });
  const availableSwitch = within(availableCard).getByRole('button', { name: '切换' });
  const exhaustedSwitch = within(exhaustedCard).getByRole('button', { name: '切换' });
  expect(activeSwitch.getAttribute('aria-disabled')).toBe('true');
  expect(activeSwitch.classList.contains('unavailable')).toBe(true);
  expect(availableSwitch.getAttribute('aria-disabled')).toBe('false');
  expect(exhaustedSwitch.getAttribute('aria-disabled')).toBe('true');
  expect(exhaustedSwitch.classList.contains('unavailable')).toBe(true);

  await fireEvent.click(activeSwitch);
  expect(await screen.findByText('已经在调度此账号')).toBeTruthy();
  expect(apiClient).not.toHaveBeenCalledWith('POST', '/api/accounts/account-1/activate');

  await fireEvent.click(exhaustedSwitch);
  expect(await screen.findByText('当前账号不可调度')).toBeTruthy();
  expect(apiClient).not.toHaveBeenCalledWith('POST', '/api/accounts/account-3/activate');

  await fireEvent.click(availableSwitch);

  expect(within(availableCard).getByText('当前调度')).toBeTruthy();
  expect(within(activeCard).queryByText('当前调度')).toBeNull();
});

test('拖动启用账号后立即保存新的调度顺序', async () => {
  const first = account({
    quota: { weekly_percent: 80, hourly_percent: 70, queried_at: 1 },
  });
  const second = account({
    id: 'account-2',
    name: 'Second',
    email: 'second@example.com',
    quota: { weekly_percent: 60, hourly_percent: 50, queried_at: 1 },
  });
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') {
      return Promise.resolve(config(['account-1', 'account-2']));
    }
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([first, second]);
    if (method === 'PUT' && path === '/api/accounts/order') {
      return Promise.resolve({ ok: true, selected_accounts: body.account_ids });
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });
  const dataTransfer = {
    effectAllowed: '',
    dropEffect: '',
    setData: vi.fn(),
    getData: vi.fn(() => 'account-2'),
  };

  render(Accounts, { apiClient, pollIntervalMs: 0 });
  await screen.findByText('test@example.com');
  const secondHandle = screen.getByRole('button', {
    name: '拖动调整 Second 的调度顺序',
  });
  const firstCard = screen.getByText('test@example.com').closest('.account-card');

  await fireEvent.dragStart(secondHandle, { dataTransfer });
  await fireEvent.dragOver(firstCard, { dataTransfer });
  await fireEvent.drop(firstCard, { dataTransfer });

  await waitFor(() => {
    expect(apiClient).toHaveBeenCalledWith('PUT', '/api/accounts/order', {
      account_ids: ['account-2', 'account-1'],
    });
  });
  expect(screen.getAllByText(/@example\.com$/).map((node) => node.textContent)).toEqual([
    'second@example.com',
    'test@example.com',
  ]);
});
