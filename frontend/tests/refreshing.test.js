/**
 * Test that the refreshing state array is correctly mutated by push/splice
 * in async promise callbacks. This is the core of the spinner bug.
 */

import { expect, test, vi } from 'vitest';

// Mock Tauri invoke
window.__TAURI_INTERNALS__ = {
  invoke: vi.fn((cmd, args) => {
    if (cmd === 'api_call' && args.path === '/v1/cockpit/status') {
      return Promise.resolve(JSON.stringify({ running: true, accounts: [] }));
    }
    if (cmd === 'api_call' && args.path === '/api/accounts') {
      return Promise.resolve(JSON.stringify([{ id: 'test-id', email: 'test@example.com' }]));
    }
    if (cmd === 'api_call' && args.path === '/api/config') {
      return Promise.resolve(JSON.stringify({ version: 1, api: { port: 8844, selected_accounts: ['test-id'] } }));
    }
    if (cmd === 'api_call' && args.path.startsWith('/api/accounts/') && args.path.endsWith('/refresh')) {
      return Promise.resolve(JSON.stringify({ ok: true }));
    }
    if (cmd === 'api_call' && args.method === 'POST' && args.path === '/api/accounts/import') {
      return Promise.resolve(JSON.stringify({ id: 'new-id', email: 'new@example.com' }));
    }
    return Promise.reject(new Error(`Unexpected invoke: ${cmd} ${args.path}`));
  }),
};

test('push adds to array, splice removes after async callback', async () => {
  // Simulate the refreshing state pattern
  let refreshing = [];

  const accountId = 'test-account';

  // Step 1: push before async call
  refreshing.push(accountId);
  expect(refreshing).toContain(accountId);

  // Step 2: splice in .then() callback after resolution
  await Promise.resolve().then(() => {
    const i = refreshing.indexOf(accountId);
    if (i >= 0) refreshing.splice(i, 1);
  });

  expect(refreshing).not.toContain(accountId);
  expect(refreshing.length).toBe(0);
});

test('splice in .catch() also removes', async () => {
  let refreshing = [];
  const accountId = 'test-account';
  refreshing.push(accountId);

  await Promise.reject(new Error('fail')).catch(() => {
    const i = refreshing.indexOf(accountId);
    if (i >= 0) refreshing.splice(i, 1);
  });

  expect(refreshing).not.toContain(accountId);
});

test('push is visible immediately, splice is visible after await', async () => {
  let refreshing = [];
  const accountId = 'test-account';

  refreshing.push(accountId);
  expect(refreshing.includes(accountId)).toBe(true);

  // Simulate API call
  const promise = new Promise(resolve => setTimeout(resolve, 10));

  const removePromise = promise.then(() => {
    const i = refreshing.indexOf(accountId);
    if (i >= 0) refreshing.splice(i, 1);
  });

  // Before await, still present
  expect(refreshing.includes(accountId)).toBe(true);

  await removePromise;

  // After await, removed
  expect(refreshing.includes(accountId)).toBe(false);
});
