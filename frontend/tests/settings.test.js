import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import Settings from '../src/routes/Settings.svelte';

afterEach(() => cleanup());

function config() {
  return {
    version: 1,
    api: {
      port: 8844,
      bind_host: '127.0.0.1',
      speed: 'standard',
      selected_accounts: [],
      auto_switch: {
        enabled: true,
        strategy: 'sequential',
        quota_threshold_percent: 95,
      },
    },
  };
}

test('显示完整设置并通过后端保存修改', async () => {
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/api/config-dir') {
      return Promise.resolve({ path: '/Users/test/.config/codex-cockpit' });
    }
    if (method === 'PUT' && path === '/api/config') return Promise.resolve({ ok: true });
    return Promise.reject(
      new Error(`Unexpected API call: ${method} ${path} ${JSON.stringify(body)}`),
    );
  });

  render(Settings, { apiClient });

  expect(screen.getByText('正在读取设置...')).toBeTruthy();
  await screen.findByDisplayValue('8844');
  const bindHost = screen.getByRole('combobox', { name: '绑定地址' });
  const speed = screen.getByRole('combobox', { name: '默认速度' });
  expect(bindHost.value).toBe('127.0.0.1');
  expect(speed.value).toBe('standard');
  expect(screen.getByText('/Users/test/.config/codex-cockpit')).toBeTruthy();

  await fireEvent.change(speed, { target: { value: 'fast' } });

  await screen.findByText('设置已保存');
  await waitFor(() => {
    expect(apiClient).toHaveBeenCalledWith(
      'PUT',
      '/api/config',
      expect.objectContaining({
        api: expect.objectContaining({ speed: 'fast' }),
      }),
    );
  });
});

test('配置读取失败时显示错误和重试入口', async () => {
  const apiClient = vi.fn(() => Promise.reject(new Error('backend unavailable')));

  render(Settings, { apiClient });

  await screen.findByText(/读取设置失败: Error: backend unavailable/);
  expect(screen.getByText('设置内容未能加载。')).toBeTruthy();
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
});
