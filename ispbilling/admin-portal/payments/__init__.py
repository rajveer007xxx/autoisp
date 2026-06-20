"""S43ZQ — Payment Gateway package (per-tenant credentials).

Loads /etc/ispbilling.env PAYMENT_GW_FERNET_KEY at import time. Provides:
  - crypto.encrypt(s) / decrypt(s)
  - registry.GATEWAYS — declarative field schema per gateway (drives UI)
  - routes_admin.register(app, ...) — admin gateway CRUD + Test endpoints
"""
