import { expect, test } from 'vitest';
import { getCodexPlanPresentation } from '../src/lib/codexPlans.js';

test.each([
  [undefined, 'Free', 'free'],
  ['', 'Free', 'free'],
  ['free', 'Free', 'free'],
  ['chatgptfreeplan', 'Free', 'free'],
  ['plus', 'Plus', 'plus'],
  ['chatgptplus', 'Plus', 'plus'],
  ['chatgptplusplan', 'Plus', 'plus'],
  ['team', 'Team', 'team'],
  ['chatgptteamplan', 'Team', 'team'],
  ['business', 'Business', 'team'],
  ['chatgptbusinessplan', 'Business', 'team'],
  ['enterprise', 'Enterprise', 'team'],
  ['chatgptenterpriseplan', 'Enterprise', 'team'],
  ['edu', 'Edu', 'team'],
  ['chatgpteduplan', 'Edu', 'team'],
  ['go', 'Go', 'plus'],
  ['chatgptgoplan', 'Go', 'plus'],
  ['prolite', 'Pro 5x', 'pro'],
  ['pro-lite', 'Pro 5x', 'pro'],
  ['pro_lite', 'Pro 5x', 'pro'],
  ['pro-5x', 'Pro 5x', 'pro'],
  ['codex-pro-5x', 'Pro 5x', 'pro'],
  ['chatgptprolite', 'Pro 5x', 'pro'],
  ['promax', 'Pro 20x', 'pro'],
  ['pro-max', 'Pro 20x', 'pro'],
  ['pro-20x', 'Pro 20x', 'pro'],
  ['codex-pro-20x', 'Pro 20x', 'pro'],
  ['chatgptpromax', 'Pro 20x', 'pro'],
  ['pro', 'Pro 20x', 'pro'],
  ['chatgptpro', 'Pro 20x', 'pro'],
  ['api', 'API', 'api'],
])('%s 映射为 %s', (raw, label, className) => {
  expect(getCodexPlanPresentation(raw)).toEqual({ label, className });
});

test('未知套餐保留原始名称和大小写', () => {
  expect(getCodexPlanPresentation('  K12-customPlan  ')).toEqual({
    label: 'K12-customPlan',
    className: 'free',
  });
});
