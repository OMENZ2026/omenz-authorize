# omenz-authorize

Run **OMENZ Authorize** before every x402 payment your agent makes.

One call ($0.002, paid over x402 with your same wallet) checks the payment before money moves:
- **Endpoint:** up and answering with valid x402 requirements.
- **Price:** within your max, and **fair** compared with similar x402 endpoints.
- **Recipient:** the pay-to address is a **known x402 seller** (catches swapped wallets) or matches the one you expect.
- **Your rules:** the owner's spend policy (caps, allowlists) and Permission Passport, if you send them.

You get **APPROVE / REVIEW / DECLINE**, the reasons, and a signed 5-minute authorization for your ledger. Anyone can verify it offline with the OMENZ public key at https://www.omenz.org/api/tollbooth/v1/passport/key.

Your keys stay in your process. OMENZ never holds funds.

## NEW in 0.3.0: Transaction Guard: the OMENZ commerce control plane in one wrapper

`guardFetch` / `GuardPayer` run every payment your agent makes through **one OMENZ call before money moves** (`POST /v1/guard`, $0.05):
- budget authorized
- counterparty checked
- **duplicate payment** checked (OMENZ records + your on-chain history)
- quoted price unchanged
- settlement economics
- expected delivery recorded

It pays only on **GO**. Then it closes with a verified receipt: settled → delivery verified → signed receipt.

If the paid request **throws** (timeout or network error), it calls **Settlement Rescue** for you. You get SETTLED / NOT_SETTLED / STILL_UNCERTAIN, so your agent never re-pays blind.

Don't know which seller to use? Pass `{ need: "token price" }`. The OMENZ Market job desk picks the working provider and guards it.

Larger purchases on prepaid credits are value-priced at **10 bps of the protected value** (min $0.01, max $5).

```js
import { guardFetch } from "omenz-authorize/js/index.js";
const buy = guardFetch(fetchWithPayment, { payer: myWallet, maxPriceUsd: 0.05, budgetId, budgetKey });
const { data, receipt } = await buy("https://api.example.com/x402/price", { purpose: "BTC price", quotedPriceUsd: 0.003 });
const r2 = await buy({ need: "token price" });
```

```python
from omenz_authorize import GuardPayer, GuardStopped
buyer = GuardPayer(paying_session, payer="0xYourWallet", max_price_usd=0.05, budget_id="bg_...", budget_key="...")
resp, data, guard, receipt = buyer.request("GET", "https://api.example.com/x402/price", purpose="BTC price")
```

## NEW in 0.2.0: OMENZ Checkout (the whole buyer layer, one wrapper)

`checkoutFetch` / `CheckoutPayer` wrap your paying fetch/session so **every purchase** gets:

- **Before paying** (`checkout/open`):
  - the cheapest seller that answers right now, if you only say what you need (`{ q: "token price" }`)
  - Authorize
  - the seller's OMENZ track record
  - a draw from your team's **Shared Budget**

  You get GO / REVIEW / STOP. The library only pays on GO.
- **After paying** (`checkout/close`):
  - settlement verified on Base
  - delivery checked against what the seller advertised
  - a **signed receipt**
  - the money goes back to the budget if the payment never happened
  - the seller's track record is updated

Price: $0.002 per side over x402. High volume: buy prepaid credits once (`POST /v1/credits/buy-1`) and pass `creditId/creditKey` (`credit_id/credit_key`). Each side then costs **1% of the purchase, minimum $0.0002**, with no per-call settlement.

```js
import { checkoutFetch } from "omenz-authorize/js/index.js";
const buy = checkoutFetch(fetchWithPayment, { maxPriceUsd: 0.05, budgetId, budgetKey, agent: "worker-3" });
const { data, receipt } = await buy({ q: "token price" });          // OMENZ picks the seller
const r = await buy("https://api.example.com/x402/search", { method: "POST", body: JSON.stringify({ q: "x" }) });
```

```python
from omenz_authorize import CheckoutPayer, CheckoutStopped
buyer = CheckoutPayer(paying_session, max_price_usd=0.05, budget_id="bg_...", budget_key="...", agent="worker-3")
resp, data, checkout, receipt = buyer.request("GET", q="token price")
```

A team budget is one call: `POST https://www.omenz.org/api/tollbooth/v1/budget/create {"cap_usd": 2, "period": "day"}`. It gives every agent the `spender_key`. OMENZ keeps the count and never holds money.

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

- API and all tools: https://www.omenz.org/openapi.json
- MCP: https://www.omenz.org/mcp
- Pricing policy (errors are not charged): https://www.omenz.org/api/tollbooth/v1/pricing-policy

MIT licensed. By OMENZ (https://omenz.org).
