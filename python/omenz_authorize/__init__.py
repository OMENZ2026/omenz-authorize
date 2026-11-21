"""omenz-authorize: run OMENZ Authorize before every x402 payment your agent makes.

Works with whatever x402 client you already use. Pass a requests-compatible session that already pays x402
(for example the x402 Python SDK's wrapped session); this library:
  1. calls POST https://www.omenz.org/api/tollbooth/v1/authorize ($0.002, paid by your same session),
  2. pays the target only if the decision is APPROVE (REVIEW raises unless allow_review=True; DECLINE always raises),
  3. returns the target's response plus the signed authorization for your ledger.

    from omenz_authorize import AuthorizedPayer
    payer = AuthorizedPayer(paying_session, max_price_usd=0.05, expected_pay_to=None, policy={"daily_cap_usd": 5})
    resp, auth = payer.request("POST", "https://api.example.com/x402/search", json={"q": "..."})

No keys leave your process. OMENZ never sees your private key and never holds funds.
"""
from .client import AuthorizedPayer, AuthorizationDeclined, AuthorizationNeedsReview, authorize, CheckoutPayer, CheckoutStopped, GuardPayer, GuardStopped, send_check, SendStopped

__all__ = ["AuthorizedPayer", "AuthorizationDeclined", "AuthorizationNeedsReview", "authorize", "CheckoutPayer", "CheckoutStopped", "GuardPayer", "GuardStopped", "send_check", "SendStopped"]
__version__ = "0.4.0"
