import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import Settings from '../src/routes/Settings.svelte';

afterEach(() => cleanup());

test('设置页只显示非 API 设置', async () => {
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config-dir') {
      return Promise.resolve({ path: '/Users/test/.config/codex-cockpit' });
    }
    return Promise.reject(
      new Error(`Unexpected API call: ${method} ${path} ${JSON.stringify(body)}`),
    );
  });

  render(Settings, { apiClient });

  expect(screen.getByText('正在读取设置...')).toBeTruthy();
  await screen.findByText('/Users/test/.config/codex-cockpit');
  expect(screen.queryByRole('heading', { name: 'API 服务' })).toBeNull();
  expect(screen.queryByLabelText('端口')).toBeNull();
  expect(screen.queryByRole('combobox', { name: '绑定地址' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: '默认速度' })).toBeNull();
  expect(apiClient).toHaveBeenCalledTimes(1);
  expect(apiClient).toHaveBeenCalledWith('GET', '/api/config-dir');
});

test('配置读取失败时显示错误和重试入口', async () => {
  const apiClient = vi.fn(() => Promise.reject(new Error('backend unavailable')));

  render(Settings, { apiClient });

  await screen.findByText(/读取设置失败: Error: backend unavailable/);
  expect(screen.getByText('设置内容未能加载。')).toBeTruthy();
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
});
