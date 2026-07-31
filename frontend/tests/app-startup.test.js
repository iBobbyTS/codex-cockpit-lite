import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import App from '../src/App.svelte';

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

function config() {
  return {
    version: 1,
    api: {
      port: 8844,
      selected_accounts: [],
    },
  };
}

test('后端未就绪时只显示全局启动状态，不提前显示空账号', async () => {
  const backendReady = deferred();
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/v1/cockpit/status') return backendReady.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(App, { apiClient });

  expect(screen.getByText('正在启动 Codex Cockpit Lite')).toBeTruthy();
  expect(screen.queryByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeNull();
  expect(apiClient).toHaveBeenCalledTimes(1);
});

test('后端就绪后等待账号接口，明确返回空数组才显示空账号', async () => {
  const backendReady = deferred();
  const accountsReady = deferred();
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/v1/cockpit/status') return backendReady.promise;
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/api/accounts') return accountsReady.promise;
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(App, { apiClient });
  await act(() => backendReady.resolve({ healthy: true }));

  expect(await screen.findByText('正在读取账号…')).toBeTruthy();
  expect(screen.queryByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeNull();

  await act(() => accountsReady.resolve([]));

  expect(await screen.findByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeTruthy();
  expect(screen.queryByText('正在读取账号…')).toBeNull();
});

test('后端连接失败显示可读错误，重试成功后进入账号页面', async () => {
  const retryReady = deferred();
  let statusCalls = 0;
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/v1/cockpit/status') {
      statusCalls += 1;
      if (statusCalls === 1) return Promise.reject(new Error('sidecar exited'));
      return retryReady.promise;
    }
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/api/accounts') return Promise.resolve([]);
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(App, { apiClient });

  expect(await screen.findByText('后端启动失败')).toBeTruthy();
  expect(screen.getByText('Error: sidecar exited')).toBeTruthy();

  await fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(screen.getByText('正在启动 Codex Cockpit Lite')).toBeTruthy();

  await act(() => retryReady.resolve({ healthy: true }));
  expect(await screen.findByText('还没有导入账号。点击上方按钮导入 auth.json。')).toBeTruthy();
});
