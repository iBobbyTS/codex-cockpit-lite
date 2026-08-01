import { invoke } from '@tauri-apps/api/core';

/** Complete one PKCE browser login and return the persisted Cockpit account. */
export async function browserLogin(reauthAccountId = null) {
  const text = await invoke('browser_login', { reauthAccountId });
  return JSON.parse(text);
}
