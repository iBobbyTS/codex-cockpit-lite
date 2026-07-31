import { isTauri } from '@tauri-apps/api/core';
import { writeText } from '@tauri-apps/plugin-clipboard-manager';

export async function copyTextToClipboard(text) {
  if (isTauri()) {
    await writeText(text);
    return;
  }
  if (globalThis.navigator?.clipboard?.writeText) {
    await globalThis.navigator.clipboard.writeText(text);
    return;
  }
  throw new Error('当前环境不支持剪贴板写入');
}
