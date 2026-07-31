import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, expect, test, vi } from 'vitest';

import Service from '../src/routes/Service.svelte';

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

test('状态轮询失败时标记服务停止并显示可读错误', async () => {
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/v1/cockpit/status') {
      return Promise.reject(new Error('backend unavailable'));
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Service, { apiClient, pollIntervalMs: 0 });

  await screen.findByText(/读取服务状态失败: Error: backend unavailable/);
  expect(screen.getByText('已停止')).toBeTruthy();
});
