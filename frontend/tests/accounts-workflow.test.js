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
