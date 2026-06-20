"""S43ZQ — Payment-gateway field registry.

Single source of truth for the UI dynamic-field form AND backend validation.
Each gateway lists which fields are required/optional. Field types: text,
password, url.

Adding a new gateway = add an entry here + (optionally) wire an adapter in
`payments/adapters.py`. The Settings UI updates automatically.
"""
from __future__ import annotations

# field: {"name": …, "label": …, "type": text/password/url, "required": bool,
#         "placeholder": …, "help": …}

GATEWAYS = {
    "razorpay": {
        "label": "Razorpay",
        "logo": "razorpay",
        "docs": "https://dashboard.razorpay.com/app/keys",
        "fields": [
            {"name": "key_id",       "label": "Key ID",         "type": "text",
             "required": True,  "placeholder": "rzp_live_xxxxxxxxxxxx"},
            {"name": "key_secret",   "label": "Key Secret",     "type": "password",
             "required": True,  "placeholder": "••••••••••••••"},
            {"name": "webhook_secret","label": "Webhook Secret","type": "password",
             "required": False, "placeholder": "webhook signing secret"},
        ],
        "active": True,
    },
    "payu": {
        "label": "PayU",
        "logo": "payu",
        "docs": "https://onboarding.payu.in/app/account/dashboard",
        "fields": [
            {"name": "merchant_id",  "label": "Merchant ID",    "type": "text",
             "required": True,  "placeholder": "M00xxxxx"},
            {"name": "key_id",       "label": "Merchant Key",   "type": "text",
             "required": True,  "placeholder": "key"},
            {"name": "key_secret",   "label": "Merchant Salt",  "type": "password",
             "required": True,  "placeholder": "salt"},
            {"name": "webhook_secret","label": "Webhook Secret","type": "password",
             "required": False, "placeholder": "optional"},
        ],
        "active": True,  # __S43ZS__ live adapter (s43ZR)
    },
    "cashfree": {
        "label": "Cashfree",
        "logo": "cashfree",
        "docs": "https://merchant.cashfree.com/merchants/pg/developers/api-keys",
        "fields": [
            {"name": "key_id",       "label": "Client ID",      "type": "text",
             "required": True,  "placeholder": "CFxxxxxxxxxxx"},
            {"name": "key_secret",   "label": "Client Secret",  "type": "password",
             "required": True,  "placeholder": "••••"},
            {"name": "webhook_secret","label": "Webhook Secret","type": "password",
             "required": False, "placeholder": "optional"},
        ],
        "active": True,  # __S43ZT__ live adapter (s43ZT)
    },
    "phonepe": {
        "label": "PhonePe",
        "logo": "phonepe",
        "docs": "https://business.phonepe.com/dashboard/developer",
        "fields": [
            {"name": "merchant_id",  "label": "Merchant ID",    "type": "text",
             "required": True,  "placeholder": "PGTESTPAYUAT86"},
            {"name": "key_secret",   "label": "Salt Key",       "type": "password",
             "required": True,  "placeholder": "salt key"},
            {"name": "key_id",       "label": "Salt Index",     "type": "text",
             "required": True,  "placeholder": "1"},
        ],
        "active": True,  # __S43ZT__ live adapter (s43ZT)
    },
    "ccavenue": {
        "label": "CCAvenue",
        "logo": "ccavenue",
        "docs": "https://world.ccavenue.com/control_panel/",
        "fields": [
            {"name": "merchant_id",  "label": "Merchant ID",    "type": "text",
             "required": True,  "placeholder": "xxxxx"},
            {"name": "key_id",       "label": "Access Code",    "type": "text",
             "required": True,  "placeholder": "AVxxxxxxxxxxxxxxx"},
            {"name": "key_secret",   "label": "Working Key",    "type": "password",
             "required": True,  "placeholder": "••••"},
        ],
        "active": True,  # __S43ZT__ live adapter (s43ZT)
    },
    "stripe": {
        "label": "Stripe",
        "logo": "stripe",
        "docs": "https://dashboard.stripe.com/apikeys",
        "fields": [
            {"name": "key_id",       "label": "Publishable Key","type": "text",
             "required": True,  "placeholder": "pk_live_xxxxxxxxxxxx"},
            {"name": "key_secret",   "label": "Secret Key",     "type": "password",
             "required": True,  "placeholder": "sk_live_xxxxxxxxxxxx"},
            {"name": "webhook_secret","label": "Webhook Secret","type": "password",
             "required": False, "placeholder": "whsec_xxx"},
        ],
        "active": True,  # __S43ZT__ live adapter (s43ZT)
    },
}


def list_gateways():
    """List public-facing dropdown options."""
    return [{"name": k, "label": v["label"], "active": v["active"]}
            for k, v in GATEWAYS.items()]


def get_schema(gateway_name: str):
    return GATEWAYS.get((gateway_name or "").lower())
