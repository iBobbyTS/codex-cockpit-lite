# Account card drag and drop does not reorder in macOS bundle

## Confirmed observations

- The final macOS bundle shows a native drag preview and a green copy badge when an account card is dragged.
- No insertion line appears over another card and dropping does not call the reorder flow.
- jsdom component coverage passes because it dispatches synthetic `dragstart`, `dragover`, and `drop` events directly.
- `tauri.conf.json` leaves `app.windows[].dragDropEnabled` at its default `true` value.
- Tauri 2.11.5 documents that its webview drag/drop handler can be disabled with `dragDropEnabled: false`; Wry's macOS handler returns the native copy operation, matching the green copy badge in the screenshot.
- After disabling the native handler in the final bundle, dragging near another account card reorders successfully. Dragging into the empty lower half still fails because `.account-list` only covers its card content and has no list-level empty-space drop target.

## Failed approach

- Relying on synthetic HTML5 drag events in jsdom did not reproduce the WKWebView/Tauri interception boundary.

## Current hypothesis

Tauri's native webview drag/drop handler was the original blocker. The remaining failure is frontend hit testing: the card handlers work, but no element accepts a drop below the last card. Make the account page/list fill the remaining content height and treat list-background drops as insertion after the last account.

## Verification

- Add a configuration regression test that asserts the packaged window disables Tauri drag/drop interception.
- Add a component regression test for dragging onto the lower empty list area.
- Rebuild the final `.app` and manually reproduce dragging both between cards and into the lower empty area.
- Completed: full project checks passed with Python 40/40, frontend 60/60, and Rust 10/10.
- Completed: the final macOS bundle was rebuilt and the user manually verified that dragging into the lower empty area shows the insertion target and persists the new order.
