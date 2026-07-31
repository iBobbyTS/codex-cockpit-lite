import { expect, test } from 'vitest';
import { formatQuotaReset } from '../src/lib/quotaTime.js';

test('格式化剩余时间和本地重置时间', () => {
  const now = new Date(2026, 6, 29, 10, 30, 0).getTime();
  const reset = new Date(2026, 6, 31, 14, 5, 0).getTime() / 1000;

  expect(formatQuotaReset(reset, now)).toBe('2d 3:35 (7/31 14:05)');
});

test('不足一分钟时向上取整，避免提前显示到期', () => {
  const now = new Date(2026, 6, 31, 14, 4, 30).getTime();
  const reset = new Date(2026, 6, 31, 14, 5, 0).getTime() / 1000;

  expect(formatQuotaReset(reset, now)).toBe('0d 0:01 (7/31 14:05)');
});

test('已到期和缺少重置时间使用稳定显示', () => {
  const now = new Date(2026, 6, 31, 14, 6, 0).getTime();
  const reset = new Date(2026, 6, 31, 14, 5, 0).getTime() / 1000;

  expect(formatQuotaReset(reset, now)).toBe('0d 0:00 (7/31 14:05)');
  expect(formatQuotaReset(null, now)).toBe('-- (--)');
  expect(formatQuotaReset('invalid', now)).toBe('-- (--)');
});
