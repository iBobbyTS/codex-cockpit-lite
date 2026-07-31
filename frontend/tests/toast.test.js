import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import Toast from '../src/lib/Toast.svelte';

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test('顶部提示显示十秒后自动关闭', async () => {
  const onDismiss = vi.fn();
  render(Toast, { message: '设置已保存', tone: 'success', onDismiss });

  expect(screen.getByText('设置已保存')).toBeTruthy();
  await vi.advanceTimersByTimeAsync(9999);
  expect(onDismiss).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

test('顶部提示支持提前手动关闭', async () => {
  const onDismiss = vi.fn();
  render(Toast, { message: '读取失败', tone: 'error', onDismiss });

  await fireEvent.click(screen.getByRole('button', { name: '关闭提示' }));

  expect(onDismiss).toHaveBeenCalledTimes(1);
});
