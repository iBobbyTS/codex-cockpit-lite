import { invoke } from '@tauri-apps/api/core';

/** Call the backend through Tauri without exposing its dynamic port to the WebView. */
export async function apiClient(method, path, body) {
  const text = await invoke('api_call', {
    method,
    path,
    body: body === undefined ? null : JSON.stringify(body),
  });
  return JSON.parse(text);
}
