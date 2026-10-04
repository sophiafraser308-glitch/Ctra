# Authentication (cTrader OAuth2)

1. Telegram → Accounts → **Add Account** → name → **DEMO/LIVE** (explicit, permanent for that account).
2. `AccountService.start_oauth` stores an `OAuthState` (random `state`, 15 min TTL) and returns the authorisation URL (`https://id.ctrader.com/my/settings/openapi/grantingaccess/`, scope `trading` or `accounts`).
3. The user authorises; cTrader redirects to `CTRADER_REDIRECT_URI` → aiohttp route (`/auth/ctrader/callback` by default).
4. The callback validates `state` (unknown/expired/used ⇒ rejected), exchanges the **single-use** code at `https://openapi.ctrader.com/apps/token`, lists accounts for the token (`ProtoOAGetAccountListByAccessTokenReq`) and keeps only accounts matching the requested environment. Tokens are stored **encrypted** (Fernet) in the pending state.
5. Telegram sends the user an inline list; choosing an account creates `TradingAccount` + `CredentialMetadata` (encrypted access/refresh tokens, expiry, status) and connects. Live accounts need double confirmation and start with trading permission **OFF**.
6. `TokenService.get_access_token` refreshes when < `TOKEN_REFRESH_MARGIN_SECONDS` remain (serialised per account); a periodic check also runs. Failure ⇒ credential status `REFRESH_FAILED`, account `AUTH_FAILED`, admin notification, trading blocked.

Secrets never appear in logs (masker), audit rows, Telegram messages or exceptions. Removing an account deletes its stored tokens.
