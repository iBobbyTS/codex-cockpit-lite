# SCSC OAuth token chain invalidated

## Confirmed observations

- Account UUID: `573c411e-043e-4d15-8801-4898043277c1` (`yuecheng.sun@scsc.ca`).
- A real `/v1/responses` request returned upstream `401 token_invalidated`.
- The forced refresh then returned `401 refresh_token_invalidated` with `Your session has ended. Please log in again.`
- The account quota endpoint also returns 401, while the UI previously retained an older 100% snapshot.

## Root cause

- Both the access token and stored refresh token have been invalidated.
- Quota errors were swallowed and the management endpoint returned stale account metadata as a successful refresh.
- Authentication availability was not persisted as account state.

## Current hypothesis and repair

- Persist `requires_reauth` as the authoritative invalid-token-chain state.
- Clear quota percentages and reset dates whenever quota cannot be queried.
- Add PKCE browser login and identity-aware persistence so the same identity replaces its account, while a different identity started from re-login creates a new account and leaves the old account invalid.

## Verification target

- Backend regression tests cover quota failure, rejected refresh, same-account replacement, and different-account addition.
- Frontend regression tests cover `--` quota rendering and the re-login controls.
- Rust tests cover PKCE and callback state validation.
- Run `python3 scripts/check.py` and package the macOS app.

## Verification results

- `python3 scripts/check.py` passed: backend 67, frontend 63, Rust 16, sidecar build and smoke test.
- `npx tauri build --bundles app` completed and the final macOS app passed strict deep code-signature verification.
- A real interactive ChatGPT authorization remains for user validation because it requires choosing and signing into an account.
