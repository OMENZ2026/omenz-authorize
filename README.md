# omenz-authorize

Run **OMENZ Authorize** before every x402 payment your agent makes.

One call ($0.001, paid over x402 with your same wallet) checks the payment before money moves:
- **Endpoint:** up and answering with valid x402 requirements.
- **Price:** within your max, and **fair** compared with similar x402 endpoints.
- **Recipient:** the pay-to address is a **known x402 seller** (catches swapped wallets) or matches the one you expect.
- **Your rules:** the owner's spend policy (caps, allowlists) and Permission Passport, if you send them.

You get **APPROVE / REVIEW / DECLINE**, the reasons, and a signed 5-minute authorization for your ledger. Anyone can verify it offline with the OMENZ public key at https://www.omenz.org/api/tollbooth/v1/passport/key.

Your keys stay in your process. OMENZ never holds funds.

## Python

```bash
pip install "git+https://github.com/OMENZ2026/omenz-authorize#subdirectory=python"
```

```python
from omenz_authorize import AuthorizedPayer, AuthorizationDeclined

# paying_session = a requests-compatible session that already pays x402 (for example from the x402 Python SDK)
payer = AuthorizedPayer(paying_session, max_price_usd=0.05, policy={"daily_cap_usd": 5})
try:
    resp, auth = payer.request("POST", "https://api.example.com/x402/search", json={"q": "agent payments"})
except AuthorizationDeclined as e:
    print("blocked:", e.result["decline_reasons"])
```

## JavaScript

```bash
npm install github:OMENZ2026/omenz-authorize
```

```js
import { authorizedFetch } from "omenz-authorize/js/index.js";
// fetchWithPayment = the x402-paying fetch you already use
const pay = authorizedFetch(fetchWithPayment, { maxPriceUsd: 0.05, policy: { daily_cap_usd: 5 } });
const { response, authorization } = await pay("https://api.example.com/x402/search", { method: "POST", body: JSON.stringify({ q: "x" }) });
```

## Try it free

AYNE, the OMENZ playzone, shows a sample authorization without paying:
https://www.omenz.org/api/tollbooth/ayne/v1/authorize

## Links

- API and all 62 tools: https://www.omenz.org/openapi.json
- MCP: https://www.omenz.org/mcp
- Pricing policy (errors are not charged): https://www.omenz.org/api/tollbooth/v1/pricing-policy

MIT licensed. By OMENZ (https://omenz.org).
