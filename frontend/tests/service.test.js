import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
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

function status() {
  return {
    actual_port: 8844,
    service_url: 'http://127.0.0.1:8844/v1',
    active_account_email: null,
    total_requests: 0,
    recent_requests: [],
  };
}

test('显示完整服务地址并复制', async () => {
  const copyText = vi.fn(() => Promise.resolve());
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/v1/cockpit/status') return Promise.resolve(status());
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Service, { apiClient, copyText, pollIntervalMs: 0 });

  expect(await screen.findByText('http://127.0.0.1:8844/v1')).toBeTruthy();
  expect(screen.queryByText('端口:')).toBeNull();
  await fireEvent.click(screen.getByRole('button', { name: '复制' }));

  expect(copyText).toHaveBeenCalledWith('http://127.0.0.1:8844/v1');
  expect(await screen.findByText('服务地址已复制')).toBeTruthy();
});

test('显示并保存 API 服务设置', async () => {
  const apiClient = vi.fn((method, path, body) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/v1/cockpit/status') return Promise.resolve(status());
    if (method === 'PUT' && path === '/api/config') return Promise.resolve({ ok: true });
    return Promise.reject(
      new Error(`Unexpected API call: ${method} ${path} ${JSON.stringify(body)}`),
    );
  });

  render(Service, { apiClient, pollIntervalMs: 0 });

  expect(await screen.findByDisplayValue('8844')).toBeTruthy();
  expect(screen.getByRole('combobox', { name: '绑定地址' }).value).toBe('127.0.0.1');
  const speed = screen.getByRole('combobox', { name: '默认速度' });
  expect(speed.value).toBe('standard');

  await fireEvent.change(speed, { target: { value: 'fast' } });

  expect(apiClient.mock.calls.filter(([method]) => method === 'PUT')).toHaveLength(0);
  await fireEvent.click(screen.getByRole('button', { name: '保存' }));

  expect(await screen.findByText('API 服务设置已保存')).toBeTruthy();
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

test('API 服务设置保存失败时显示可读错误', async () => {
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/v1/cockpit/status') return Promise.resolve(status());
    if (method === 'PUT' && path === '/api/config') {
      return Promise.reject(new Error('write failed'));
    }
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Service, { apiClient, pollIntervalMs: 0 });

  const bindHost = await screen.findByRole('combobox', { name: '绑定地址' });
  await fireEvent.change(bindHost, { target: { value: '0.0.0.0' } });
  await fireEvent.click(screen.getByRole('button', { name: '保存' }));

  expect(await screen.findByText(/保存 API 服务设置失败: Error: write failed/)).toBeTruthy();
});

test('仅在启动检查发现端口被占用时显示端口变更通知', async () => {
  const loadedConfig = config();
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(loadedConfig);
    if (method === 'GET' && path === '/v1/cockpit/status') {
      return Promise.resolve({ ...status(), actual_port: 8845 });
    }
    if (method === 'PUT' && path === '/api/config') return Promise.resolve({ ok: true });
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Service, { apiClient, pollIntervalMs: 0 });

  expect(await screen.findByText('端口变更通知')).toBeTruthy();
  expect(screen.getByText(/端口 8844 已被更新为 8845/)).toBeTruthy();
  await waitFor(() => {
    expect(apiClient).toHaveBeenCalledWith(
      'PUT',
      '/api/config',
      expect.objectContaining({ api: expect.objectContaining({ port: 8845 }) }),
    );
  });
});

test('手动修改端口后再次轮询不会显示端口占用通知', async () => {
  const apiClient = vi.fn((method, path) => {
    if (method === 'GET' && path === '/api/config') return Promise.resolve(config());
    if (method === 'GET' && path === '/v1/cockpit/status') return Promise.resolve(status());
    if (method === 'PUT' && path === '/api/config') return Promise.resolve({ ok: true });
    return Promise.reject(new Error(`Unexpected API call: ${method} ${path}`));
  });

  render(Service, { apiClient, pollIntervalMs: 10 });

  const port = await screen.findByLabelText('端口');
  await waitFor(() => {
    expect(
      apiClient.mock.calls.filter(
        ([method, path]) => method === 'GET' && path === '/v1/cockpit/status',
      ).length,
    ).toBeGreaterThanOrEqual(1);
  });
  await fireEvent.change(port, { target: { value: '9000' } });
  await fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => {
    expect(
      apiClient.mock.calls.filter(
        ([method, path]) => method === 'GET' && path === '/v1/cockpit/status',
      ).length,
    ).toBeGreaterThanOrEqual(2);
  });

  expect(screen.queryByText('端口变更通知')).toBeNull();
});

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
