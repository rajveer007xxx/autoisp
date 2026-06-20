"""S43ZR — Gateway adapters (PayU implemented, others stubbed).

Each adapter implements 4 ops:
    create_order(amount_inr, customer, notes)   -> dict for frontend init
    verify_callback(form)                       -> dict {ok, payment_id, amount}
    verify_webhook(raw, sig, secret)            -> dict {ok, event_id, …}
    refund(payment_id, amount_inr, reason)      -> dict {ok, refund_id, msg}

Adapters return plain dicts (no FastAPI deps) for unit-testability.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, time, uuid
from datetime import datetime


# ─── Razorpay ─────────────────────────────────────────────────────────────

class RazorpayAdapter:
    name = "razorpay"

    def __init__(self, creds: dict):
        self.key_id = creds.get("key_id") or ""
        self.key_secret = creds.get("key_secret") or ""
        self.webhook_secret = creds.get("webhook_secret") or ""

    def _client(self):
        import razorpay
        return razorpay.Client(auth=(self.key_id, self.key_secret))

    def test(self) -> dict:
        try:
            self._client().payment.all({"count": 1})
            return {"ok": True, "message": "Razorpay credentials are valid."}
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Authentication" in msg or "BAD_REQUEST_ERROR" in msg:
                msg = "Razorpay rejected the keys (check Key ID + Secret)."
            return {"ok": False, "message": msg[:240]}

    def create_order(self, amount_inr: float, customer_id: str, company_id: str,
                     notes: dict | None = None) -> dict:
        amount_paise = int(round(float(amount_inr) * 100))
        n = dict(notes or {})
        n.setdefault("customer_id", customer_id)
        n.setdefault("company_id", company_id)
        order = self._client().order.create({
            "amount": amount_paise, "currency": "INR",
            "receipt": f"rcpt_{customer_id}_{int(time.time())}",
            "payment_capture": 1, "notes": n,
        })
        return {"ok": True, "order_id": order["id"], "key_id": self.key_id,
                "amount": amount_paise, "currency": "INR"}

    def verify_webhook(self, raw: bytes, sig: str) -> dict:
        try:
            self._client().utility.verify_webhook_signature(
                raw.decode(), sig, self.webhook_secret)
            body = json.loads(raw.decode() or "{}")
            pay = ((body.get("payload") or {}).get("payment") or {}).get("entity") or {}
            return {"ok": True, "event_id": body.get("event_id") or body.get("id"),
                    "event_type": body.get("event"),
                    "payment_id": pay.get("id"),
                    "order_id": pay.get("order_id"),
                    "amount": (pay.get("amount") or 0) / 100.0,
                    "status": pay.get("status"),
                    "notes": pay.get("notes") or {}}
        except Exception as e:
            return {"ok": False, "message": f"signature invalid: {e}"}

    def refund(self, payment_id: str, amount_inr: float | None = None,
               reason: str = "") -> dict:
        try:
            body = {"speed": "normal"}
            if amount_inr:
                body["amount"] = int(round(float(amount_inr) * 100))
            if reason:
                body.setdefault("notes", {})["reason"] = reason[:200]
            r = self._client().payment.refund(payment_id, body)
            return {"ok": True, "refund_id": r.get("id"),
                    "amount": (r.get("amount") or 0) / 100.0,
                    "status": r.get("status")}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# ─── PayU ─────────────────────────────────────────────────────────────────
# REST docs: https://devguide.payu.in/restapi/post-trans-api/.
# Hash formula for v3:
#   sha512("{key}|{txnid}|{amount}|{productinfo}|{firstname}|{email}|||||"
#          "|||||{salt}")
class PayUAdapter:
    name = "payu"

    BASE_LIVE = "https://secure.payu.in"
    BASE_TEST = "https://test.payu.in"

    def __init__(self, creds: dict):
        self.merchant_id = creds.get("merchant_id") or ""
        self.key = creds.get("key_id") or ""        # PayU Merchant Key
        self.salt = creds.get("key_secret") or ""   # PayU Merchant Salt
        self.webhook_secret = creds.get("webhook_secret") or self.salt
        # If key starts with "test_" treat as test mode
        self.base = self.BASE_TEST if self.key.lower().startswith("test") else self.BASE_LIVE

    def test(self) -> dict:
        """No public 'ping' API; best we can do is check that creds are present."""
        if not (self.key and self.salt):
            return {"ok": False, "message": "Merchant Key + Salt required."}
        return {"ok": True, "message": "PayU credentials stored. (Live test will happen on first payment.)"}

    def create_order(self, amount_inr: float, customer_id: str, company_id: str,
                     notes: dict | None = None) -> dict:
        """PayU doesn't have a pre-create-order API like Razorpay; instead
        the frontend POSTs a signed form to /_payment. We compute the hash
        + txnid and return everything the frontend needs to render an
        auto-submitting form."""
        txnid = f"tx_{customer_id}_{uuid.uuid4().hex[:10]}"
        amt = f"{float(amount_inr):.2f}"
        productinfo = (notes or {}).get("productinfo") or "ISP Bill"
        firstname = (notes or {}).get("firstname") or "Customer"
        email = (notes or {}).get("email") or "noreply@autoispbilling.com"
        udf1 = customer_id
        udf2 = company_id
        # PayU hash sequence (key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5||||||salt)
        raw = f"{self.key}|{txnid}|{amt}|{productinfo}|{firstname}|{email}|{udf1}|{udf2}|||||||||{self.salt}"
        hsh = hashlib.sha512(raw.encode("utf-8")).hexdigest()
        return {
            "ok": True,
            "order_id": txnid,
            "amount": amt,
            "currency": "INR",
            "post_url": f"{self.base}/_payment",
            "fields": {
                "key": self.key, "txnid": txnid, "amount": amt,
                "productinfo": productinfo, "firstname": firstname, "email": email,
                "udf1": udf1, "udf2": udf2,
                "surl": "",  # filled by caller
                "furl": "",
                "hash": hsh,
            },
        }

    def verify_callback(self, form: dict) -> dict:
        """Verify the form-post return-hash after PayU sends user back to surl/furl.
        Reverse-hash: sha512(salt|status|||||||||||{email}|{firstname}|{productinfo}|{amount}|{txnid}|{key})
        """
        try:
            need = ["status", "key", "txnid", "amount", "productinfo",
                    "firstname", "email", "hash"]
            for k in need:
                if k not in form:
                    return {"ok": False, "message": f"missing {k}"}
            udf1 = form.get("udf1", ""); udf2 = form.get("udf2", "")
            raw = (f"{self.salt}|{form['status']}|||||||||{udf2}|{udf1}|"
                   f"{form['email']}|{form['firstname']}|{form['productinfo']}|"
                   f"{form['amount']}|{form['txnid']}|{form['key']}")
            expected = hashlib.sha512(raw.encode("utf-8")).hexdigest()
            if expected != form["hash"]:
                return {"ok": False, "message": "hash mismatch"}
            ok = (form["status"] or "").lower() == "success"
            return {"ok": ok,
                    "payment_id": form.get("mihpayid") or form.get("payuMoneyId"),
                    "order_id": form["txnid"],
                    "amount": float(form["amount"] or 0),
                    "status": form["status"],
                    "notes": {"customer_id": udf1, "company_id": udf2}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_webhook(self, raw: bytes, sig: str) -> dict:
        """PayU's webhook signature (when enabled in dashboard) is an HMAC-SHA256
        of the body using the webhook secret."""
        try:
            expected = hmac.new(self.webhook_secret.encode("utf-8"),
                                raw, hashlib.sha256).hexdigest()
            if sig and not hmac.compare_digest(expected, sig):
                return {"ok": False, "message": "signature mismatch"}
            body = json.loads(raw.decode() or "{}")
            pay = body.get("data") or body
            return {"ok": True, "event_id": body.get("event_id") or pay.get("mihpayid"),
                    "event_type": body.get("event") or "payment.captured",
                    "payment_id": pay.get("mihpayid"),
                    "order_id": pay.get("txnid"),
                    "amount": float(pay.get("amount") or 0),
                    "status": pay.get("status"),
                    "notes": {"customer_id": pay.get("udf1") or "",
                              "company_id": pay.get("udf2") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def refund(self, payment_id: str, amount_inr: float | None = None,
               reason: str = "") -> dict:
        """PayU refund API (cancel_refund_transaction). Requires POSTing
        form-encoded data to /merchant/postservice.php?form=2."""
        import requests
        try:
            command = "cancel_refund_transaction"
            token_id = f"rfnd_{int(time.time())}"
            amt = f"{float(amount_inr or 0):.2f}"
            var1 = payment_id; var2 = token_id; var3 = amt
            hash_str = f"{self.key}|{command}|{var1}|{self.salt}"
            h = hashlib.sha512(hash_str.encode()).hexdigest()
            data = {"key": self.key, "command": command, "var1": var1,
                    "var2": var2, "var3": var3, "hash": h}
            r = requests.post(f"{self.base}/merchant/postservice.php?form=2",
                              data=data, timeout=30)
            jr = r.json()
            return {"ok": bool(jr.get("status") == 1),
                    "refund_id": token_id, "amount": float(amt),
                    "raw": jr}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# ─── Cashfree (PG v3) ──────────────────────────────────────────────────
# Docs: https://docs.cashfree.com/docs/pg-api-v3
# Live: https://api.cashfree.com/pg ; Sandbox: https://sandbox.cashfree.com/pg
class CashfreeAdapter:
    name = "cashfree"
    API_VERSION = "2023-08-01"

    def __init__(self, creds: dict):
        self.client_id     = (creds.get("key_id") or "").strip()
        self.client_secret = (creds.get("key_secret") or "").strip()
        self.webhook_secret = (creds.get("webhook_secret") or "").strip()
        # Sandbox keys typically start with TEST or contain 'TEST'
        is_test = self.client_id.upper().startswith("TEST") or "TEST" in self.client_id.upper()
        self.base = "https://sandbox.cashfree.com/pg" if is_test else "https://api.cashfree.com/pg"

    def _headers(self):
        return {
            "x-api-version": self.API_VERSION,
            "x-client-id":     self.client_id,
            "x-client-secret": self.client_secret,
            "Content-Type":    "application/json",
            "Accept":          "application/json",
        }

    def test(self) -> dict:
        if not (self.client_id and self.client_secret):
            return {"ok": False, "message": "Client ID + Secret required."}
        # No public 'ping' — issue a sandbox create with absurd ID and see if auth passes.
        import requests
        try:
            # Use a no-side-effect probe: GET an obviously-missing order returns
            # 404 (auth was accepted) vs 401 (auth was rejected).
            r = requests.get(f"{self.base}/orders/__probe_{int(time.time())}",
                              headers=self._headers(), timeout=15)
            if r.status_code == 401 or r.status_code == 403:
                return {"ok": False, "message": "Cashfree rejected credentials (401/403)."}
            return {"ok": True, "message": "Cashfree credentials look valid."}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def create_order(self, amount_inr: float, customer_id: str,
                      company_id: str, notes: dict | None = None) -> dict:
        import requests
        try:
            order_id = f"order_{customer_id}_{uuid.uuid4().hex[:10]}"
            body = {
                "order_id": order_id,
                "order_amount": float(round(amount_inr, 2)),
                "order_currency": "INR",
                "customer_details": {
                    "customer_id":    customer_id[:64],
                    "customer_name":  ((notes or {}).get("firstname") or "Customer")[:80],
                    "customer_email": ((notes or {}).get("email") or "noreply@autoispbilling.com")[:80],
                    "customer_phone": ((notes or {}).get("phone") or "9999999999")[:15],
                },
                "order_meta": {
                    # Cashfree will append `?order_id=...` to return_url for us.
                    "return_url": ((notes or {}).get("return_url") or
                                   "https://www.autoispbilling.com/api/pay/cashfree/return") + "?order_id={order_id}",
                    "notify_url": (notes or {}).get("notify_url") or
                                   "https://www.autoispbilling.com/api/webhooks/cashfree",
                },
                "order_tags": {"customer_id": customer_id, "company_id": company_id},
            }
            r = requests.post(f"{self.base}/orders", headers=self._headers(),
                              json=body, timeout=30)
            jr = r.json() if r.text else {}
            if r.status_code != 200 or not jr.get("payment_session_id"):
                return {"ok": False,
                        "message": jr.get("message") or jr.get("error_description") or
                                   f"Cashfree create-order failed (HTTP {r.status_code})"}
            return {"ok": True, "order_id": order_id,
                    "payment_session_id": jr["payment_session_id"],
                    # Customer is redirected here to start checkout:
                    "checkout_url": f"https://payments{'-test' if 'sandbox' in self.base else ''}.cashfree.com/order/#{jr['payment_session_id']}",
                    "amount": amount_inr, "currency": "INR"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_callback(self, form: dict) -> dict:
        """Customer is redirected back with order_id; we re-fetch order
        status from Cashfree to confirm payment."""
        import requests
        order_id = form.get("order_id") or ""
        if not order_id:
            return {"ok": False, "message": "missing order_id"}
        try:
            r = requests.get(f"{self.base}/orders/{order_id}",
                              headers=self._headers(), timeout=20)
            jr = r.json()
            paid = (jr.get("order_status") or "").upper() == "PAID"
            tags = jr.get("order_tags") or {}
            return {"ok": paid,
                    "payment_id": (jr.get("cf_order_id") and str(jr["cf_order_id"])) or order_id,
                    "order_id": order_id,
                    "amount": float(jr.get("order_amount") or 0),
                    "status": jr.get("order_status"),
                    "notes": {"customer_id": tags.get("customer_id") or "",
                              "company_id": tags.get("company_id") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_webhook(self, raw: bytes, sig: str, timestamp: str = "") -> dict:
        """Cashfree signs as base64(HMAC-SHA256(timestamp + body, secret))
        in the `x-webhook-signature` header (with timestamp in
        `x-webhook-timestamp`). When timestamp missing we fall back to
        plain HMAC of body."""
        try:
            if timestamp:
                blob = (timestamp + raw.decode("utf-8")).encode("utf-8")
            else:
                blob = raw
            expected = base64.b64encode(
                hmac.new(self.webhook_secret.encode("utf-8"), blob,
                          hashlib.sha256).digest()).decode("ascii")
            if sig and not hmac.compare_digest(expected, sig):
                return {"ok": False, "message": "signature mismatch"}
            body = json.loads(raw.decode() or "{}")
            data = body.get("data") or {}
            order = data.get("order") or {}
            pay = data.get("payment") or {}
            tags = order.get("order_tags") or {}
            return {"ok": True,
                    "event_id": body.get("event_id") or pay.get("cf_payment_id"),
                    "event_type": body.get("type") or "PAYMENT_SUCCESS_WEBHOOK",
                    "payment_id": pay.get("cf_payment_id") or order.get("cf_order_id"),
                    "order_id": order.get("order_id"),
                    "amount": float(pay.get("payment_amount") or order.get("order_amount") or 0),
                    "status": pay.get("payment_status") or order.get("order_status"),
                    "notes": {"customer_id": tags.get("customer_id") or "",
                              "company_id": tags.get("company_id") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def refund(self, payment_id: str, amount_inr: float | None = None,
                reason: str = "") -> dict:
        import requests
        try:
            refund_id = f"rfnd_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            body = {"refund_amount": float(amount_inr or 0),
                    "refund_id": refund_id,
                    "refund_note": (reason or "Refund")[:180]}
            # In Cashfree v3, payment_id here is actually the cf_order_id.
            r = requests.post(f"{self.base}/orders/{payment_id}/refunds",
                              headers=self._headers(), json=body, timeout=30)
            jr = r.json() if r.text else {}
            ok = r.status_code == 200 and bool(jr.get("refund_status"))
            return {"ok": ok,
                    "refund_id": refund_id,
                    "amount": float(amount_inr or 0),
                    "status": jr.get("refund_status"),
                    "raw": jr}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# ─── PhonePe (PG) ──────────────────────────────────────────────────────
# Docs: https://developer.phonepe.com/v1/reference/pay-api
# Auth: X-VERIFY header = sha256(base64Payload + apiPath + saltKey) + ### + saltIndex
class PhonePeAdapter:
    name = "phonepe"
    BASE_LIVE = "https://api.phonepe.com/apis/hermes"
    BASE_SANDBOX = "https://api-preprod.phonepe.com/apis/pg-sandbox"

    def __init__(self, creds: dict):
        self.merchant_id = (creds.get("merchant_id") or "").strip()
        self.salt_key    = (creds.get("key_secret") or "").strip()
        self.salt_index  = (creds.get("key_id") or "1").strip()
        # Common sandbox merchant IDs start with PGTESTPAY
        is_test = self.merchant_id.upper().startswith("PGTEST") or "UAT" in self.merchant_id.upper()
        self.base = self.BASE_SANDBOX if is_test else self.BASE_LIVE

    def _verify(self, b64_payload: str, api_path: str) -> str:
        digest = hashlib.sha256((b64_payload + api_path + self.salt_key)
                                  .encode("utf-8")).hexdigest()
        return f"{digest}###{self.salt_index}"

    def test(self) -> dict:
        if not (self.merchant_id and self.salt_key):
            return {"ok": False, "message": "Merchant ID + Salt Key required."}
        return {"ok": True, "message": "PhonePe credentials stored. (Live test happens on first payment.)"}

    def create_order(self, amount_inr: float, customer_id: str,
                      company_id: str, notes: dict | None = None) -> dict:
        import requests
        try:
            txn_id = f"MT{int(time.time())}{uuid.uuid4().hex[:8]}"[:35]
            payload = {
                "merchantId": self.merchant_id,
                "merchantTransactionId": txn_id,
                "merchantUserId": customer_id[:30],
                "amount": int(round(amount_inr * 100)),  # paise
                "redirectUrl": ((notes or {}).get("return_url") or
                                "https://www.autoispbilling.com/api/pay/phonepe/return") + f"?txnid={txn_id}",
                "redirectMode": "POST",
                "callbackUrl": (notes or {}).get("notify_url") or
                                "https://www.autoispbilling.com/api/webhooks/phonepe",
                "mobileNumber": ((notes or {}).get("phone") or ""),
                "paymentInstrument": {"type": "PAY_PAGE"},
                # PhonePe doesn't have native metadata; we stash company_id in a
                # transaction-context cache.
            }
            b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
            xverify = self._verify(b64, "/pg/v1/pay")
            r = requests.post(f"{self.base}/pg/v1/pay",
                              headers={"Content-Type": "application/json",
                                       "X-VERIFY": xverify,
                                       "accept": "application/json"},
                              json={"request": b64}, timeout=30)
            jr = r.json() if r.text else {}
            if not jr.get("success"):
                return {"ok": False, "message": jr.get("message", f"HTTP {r.status_code}")}
            url = ((jr.get("data") or {}).get("instrumentResponse") or {}) \
                  .get("redirectInfo", {}).get("url")
            if not url:
                return {"ok": False, "message": "no checkout URL returned"}
            # Persist tenant link via instant insert into webhook_log
            # ("event_id" placeholder so the webhook later can map txnid → company)
            try:
                from sqlalchemy import text as _t
                from database import SessionLocal
                _d = SessionLocal()
                _d.execute(_t(
                    "INSERT OR IGNORE INTO webhook_log "
                    "(company_id, gateway_name, event_id, event_type, signature, "
                    " signature_valid, payload_json, http_status, processed_payment_id, received_at) "
                    "VALUES (:c, 'phonepe', :e, 'create_order', '', 0, :p, 0, '', :t)"
                ), {"c": company_id, "e": f"prelink_{txn_id}",
                    "p": json.dumps({"customer_id": customer_id, "company_id": company_id,
                                      "txn_id": txn_id}),
                    "t": datetime.utcnow()})
                _d.commit(); _d.close()
            except Exception:
                pass
            return {"ok": True, "order_id": txn_id, "checkout_url": url,
                    "amount": amount_inr, "currency": "INR"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_callback(self, form: dict) -> dict:
        """PhonePe POSTs the redirect with `code` and `merchantTransactionId`.
        We re-query the status endpoint to confirm."""
        import requests
        txn_id = form.get("transactionId") or form.get("merchantTransactionId") or form.get("txnid") or ""
        if not txn_id:
            return {"ok": False, "message": "missing txnid"}
        try:
            path = f"/pg/v1/status/{self.merchant_id}/{txn_id}"
            digest = hashlib.sha256((path + self.salt_key).encode()).hexdigest()
            xverify = f"{digest}###{self.salt_index}"
            r = requests.get(f"{self.base}{path}",
                              headers={"X-VERIFY": xverify,
                                       "X-MERCHANT-ID": self.merchant_id,
                                       "Content-Type": "application/json"},
                              timeout=20)
            jr = r.json() if r.text else {}
            ok = bool(jr.get("success"))
            data = jr.get("data") or {}
            # Recover tenant from our pre-link cache
            cust = comp = ""
            try:
                from sqlalchemy import text as _t
                from database import SessionLocal
                _d = SessionLocal()
                row = _d.execute(_t(
                    "SELECT payload_json FROM webhook_log "
                    "WHERE gateway_name='phonepe' AND event_id=:e"
                ), {"e": f"prelink_{txn_id}"}).fetchone()
                _d.close()
                if row:
                    j = json.loads(row[0] or "{}")
                    cust = j.get("customer_id") or ""
                    comp = j.get("company_id") or ""
            except Exception:
                pass
            return {"ok": ok and (data.get("state") == "COMPLETED"),
                    "payment_id": data.get("transactionId") or txn_id,
                    "order_id": txn_id,
                    "amount": float(data.get("amount") or 0) / 100.0,
                    "status": data.get("state"),
                    "notes": {"customer_id": cust, "company_id": comp}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_webhook(self, raw: bytes, sig: str) -> dict:
        """Server-to-server callback. Body is `{"response": "<base64>"}`,
        signature is `sha256(base64 + salt) ###saltIndex`. Decode to get
        transactionId + state."""
        try:
            body = json.loads(raw.decode() or "{}")
            b64 = body.get("response") or ""
            if not b64:
                return {"ok": False, "message": "missing response"}
            expected = hashlib.sha256((b64 + self.salt_key).encode()).hexdigest() + f"###{self.salt_index}"
            if sig and sig != expected:
                return {"ok": False, "message": "signature mismatch"}
            decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
            data = decoded.get("data") or {}
            ok = (decoded.get("success") and data.get("state") == "COMPLETED")
            txn_id = data.get("merchantTransactionId") or ""
            # Recover tenant from pre-link
            cust = comp = ""
            try:
                from sqlalchemy import text as _t
                from database import SessionLocal
                _d = SessionLocal()
                row = _d.execute(_t(
                    "SELECT payload_json FROM webhook_log "
                    "WHERE gateway_name='phonepe' AND event_id=:e"
                ), {"e": f"prelink_{txn_id}"}).fetchone()
                _d.close()
                if row:
                    j = json.loads(row[0] or "{}")
                    cust = j.get("customer_id") or ""
                    comp = j.get("company_id") or ""
            except Exception:
                pass
            return {"ok": bool(ok),
                    "event_id": data.get("transactionId") or txn_id,
                    "event_type": "PAYMENT_SUCCESS",
                    "payment_id": data.get("transactionId") or txn_id,
                    "order_id": txn_id,
                    "amount": float(data.get("amount") or 0) / 100.0,
                    "status": data.get("state"),
                    "notes": {"customer_id": cust, "company_id": comp}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def refund(self, payment_id: str, amount_inr: float | None = None,
                reason: str = "") -> dict:
        import requests
        try:
            original_txn_id = payment_id  # PhonePe needs original merchantTransactionId
            refund_txn = f"RT{int(time.time())}{uuid.uuid4().hex[:8]}"[:35]
            payload = {"merchantId": self.merchant_id,
                       "merchantUserId": "system",
                       "originalTransactionId": original_txn_id,
                       "merchantTransactionId": refund_txn,
                       "amount": int(round(float(amount_inr or 0) * 100)),
                       "callbackUrl": ""}
            b64 = base64.b64encode(json.dumps(payload).encode()).decode()
            xverify = self._verify(b64, "/pg/v1/refund")
            r = requests.post(f"{self.base}/pg/v1/refund",
                              headers={"Content-Type": "application/json",
                                       "X-VERIFY": xverify,
                                       "accept": "application/json"},
                              json={"request": b64}, timeout=30)
            jr = r.json() if r.text else {}
            return {"ok": bool(jr.get("success")),
                    "refund_id": refund_txn,
                    "amount": float(amount_inr or 0),
                    "status": (jr.get("data") or {}).get("state"),
                    "raw": jr}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# ─── CCAvenue ──────────────────────────────────────────────────────────
# Docs: https://world.ccavenue.com/IntegrationKits.jsp
# Auth: AES-128/CBC encryption of form fields with key=MD5(working_key), IV=0
class CCAvenueAdapter:
    name = "ccavenue"
    POST_URL = "https://secure.ccavenue.com/transaction/transaction.do?command=initiateTransaction"

    def __init__(self, creds: dict):
        self.merchant_id  = (creds.get("merchant_id") or "").strip()
        self.access_code  = (creds.get("key_id") or "").strip()
        self.working_key  = (creds.get("key_secret") or "").strip()

    def _aes_encrypt(self, plain: str) -> str:
        from Cryptodome.Cipher import AES
        key = hashlib.md5(self.working_key.encode("utf-8")).digest()
        iv = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        cipher = AES.new(key, AES.MODE_CBC, iv)
        # PKCS#7 padding to 16 bytes
        pad = 16 - (len(plain) % 16)
        padded = plain + chr(pad) * pad
        return cipher.encrypt(padded.encode("utf-8")).hex()

    def _aes_decrypt(self, hex_cipher: str) -> str:
        from Cryptodome.Cipher import AES
        key = hashlib.md5(self.working_key.encode("utf-8")).digest()
        iv = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(bytes.fromhex(hex_cipher))
        # Strip PKCS#7 padding
        pad = decrypted[-1]
        return decrypted[:-pad].decode("utf-8", "ignore")

    def test(self) -> dict:
        if not (self.merchant_id and self.access_code and self.working_key):
            return {"ok": False, "message": "Merchant ID + Access Code + Working Key required."}
        # Smoke-encrypt to confirm working_key is usable
        try:
            self._aes_encrypt("smoke=1")
            return {"ok": True, "message": "CCAvenue credentials stored (encryption smoke-test passed)."}
        except Exception as e:
            return {"ok": False, "message": f"Working key invalid: {e}"}

    def create_order(self, amount_inr: float, customer_id: str,
                      company_id: str, notes: dict | None = None) -> dict:
        try:
            order_id = f"ORD{customer_id}_{uuid.uuid4().hex[:10]}"
            return_url = (notes or {}).get("return_url") or \
                          "https://www.autoispbilling.com/api/pay/ccavenue/return"
            fields = {
                "merchant_id":   self.merchant_id,
                "order_id":      order_id,
                "currency":      "INR",
                "amount":        f"{float(amount_inr):.2f}",
                "redirect_url":  return_url,
                "cancel_url":    return_url,
                "language":      "EN",
                "billing_name":  ((notes or {}).get("firstname") or "Customer")[:60],
                "billing_email": ((notes or {}).get("email") or "noreply@autoispbilling.com")[:80],
                "billing_tel":   ((notes or {}).get("phone") or "")[:15],
                "merchant_param1": customer_id,   # We get this back on callback
                "merchant_param2": company_id,
            }
            merchant_data = "&".join(f"{k}={v}" for k, v in fields.items())
            enc = self._aes_encrypt(merchant_data)
            return {"ok": True, "order_id": order_id,
                    "post_url": self.POST_URL,
                    "fields": {"encRequest": enc, "access_code": self.access_code},
                    "amount": amount_inr, "currency": "INR"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_callback(self, form: dict) -> dict:
        """CCAvenue POSTs back with encResp; we decrypt + parse."""
        try:
            enc = form.get("encResp") or ""
            if not enc:
                return {"ok": False, "message": "missing encResp"}
            decoded = self._aes_decrypt(enc)
            # Form-urlencoded → parse
            from urllib.parse import parse_qsl
            data = dict(parse_qsl(decoded, keep_blank_values=True))
            status = (data.get("order_status") or "").strip()
            ok = status.lower() == "success"
            return {"ok": ok,
                    "payment_id": data.get("tracking_id") or data.get("order_id"),
                    "order_id": data.get("order_id"),
                    "amount": float(data.get("amount") or 0),
                    "status": status,
                    "notes": {"customer_id": data.get("merchant_param1") or "",
                              "company_id": data.get("merchant_param2") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_webhook(self, raw: bytes, sig: str) -> dict:
        """CCAvenue doesn't push S2S webhooks for normal flow; the redirect
        callback IS the verification. This handler is provided for parity
        and for refund-status notifications when configured."""
        try:
            body = raw.decode() or ""
            # If body is encResp= form-urlencoded:
            from urllib.parse import parse_qs
            qp = parse_qs(body)
            enc = (qp.get("encResp") or [""])[0]
            if not enc:
                return {"ok": False, "message": "missing encResp"}
            return self.verify_callback({"encResp": enc})
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def refund(self, payment_id: str, amount_inr: float | None = None,
                reason: str = "") -> dict:
        """CCAvenue refund API: POST encrypted JSON to
        https://api.ccavenue.com/apis/servlet/DoWebTrans"""
        import requests
        try:
            ref_id = f"REF{int(time.time())}"
            req_payload = {
                "reference_no": payment_id,
                "refund_amount": f"{float(amount_inr or 0):.2f}",
                "refund_ref_no": ref_id,
            }
            enc = self._aes_encrypt(json.dumps(req_payload))
            data = {"enc_request": enc,
                    "access_code": self.access_code,
                    "command": "refundOrder",
                    "request_type": "JSON",
                    "response_type": "JSON",
                    "version": "1.2"}
            r = requests.post("https://api.ccavenue.com/apis/servlet/DoWebTrans",
                              data=data, timeout=30)
            return {"ok": (r.status_code == 200),
                    "refund_id": ref_id, "amount": float(amount_inr or 0),
                    "raw": r.text[:500]}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# ─── Stripe ─────────────────────────────────────────────────────────────
# Docs: https://stripe.com/docs/api/checkout/sessions
class StripeAdapter:
    name = "stripe"

    def __init__(self, creds: dict):
        self.pk = (creds.get("key_id") or "").strip()
        self.sk = (creds.get("key_secret") or "").strip()
        self.webhook_secret = (creds.get("webhook_secret") or "").strip()

    def _client(self):
        import stripe
        stripe.api_key = self.sk
        return stripe

    def test(self) -> dict:
        if not self.sk:
            return {"ok": False, "message": "Secret key required."}
        try:
            self._client().Balance.retrieve()
            return {"ok": True, "message": "Stripe credentials are valid."}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def create_order(self, amount_inr: float, customer_id: str,
                      company_id: str, notes: dict | None = None) -> dict:
        try:
            s = self._client()
            session = s.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": "inr",
                        "product_data": {"name": f"ISP Bill — {customer_id}"},
                        "unit_amount": int(round(amount_inr * 100)),
                    },
                    "quantity": 1,
                }],
                customer_email=((notes or {}).get("email") or None),
                metadata={"customer_id": customer_id, "company_id": company_id},
                success_url=((notes or {}).get("return_url") or
                              "https://www.autoispbilling.com/pay/success/" + customer_id) +
                              "?gw=stripe&session_id={CHECKOUT_SESSION_ID}",
                cancel_url=((notes or {}).get("cancel_url") or
                              "https://www.autoispbilling.com/pay/failed/" + customer_id) +
                              "?gw=stripe",
            )
            return {"ok": True, "order_id": session.id,
                    "checkout_url": session.url,
                    "amount": amount_inr, "currency": "INR"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_callback(self, form: dict) -> dict:
        """Customer is redirected to success_url with ?session_id=…; we
        re-fetch the session to confirm `payment_status=paid`."""
        try:
            sid = form.get("session_id") or ""
            if not sid:
                return {"ok": False, "message": "missing session_id"}
            s = self._client()
            sess = s.checkout.Session.retrieve(sid)
            ok = (sess.get("payment_status") == "paid")
            meta = sess.get("metadata") or {}
            return {"ok": ok,
                    "payment_id": sess.get("payment_intent") or sid,
                    "order_id": sid,
                    "amount": float(sess.get("amount_total") or 0) / 100.0,
                    "status": sess.get("payment_status"),
                    "notes": {"customer_id": meta.get("customer_id") or "",
                              "company_id": meta.get("company_id") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def verify_webhook(self, raw: bytes, sig: str) -> dict:
        try:
            s = self._client()
            evt = s.Webhook.construct_event(raw, sig, self.webhook_secret)
            obj = (evt.get("data") or {}).get("object") or {}
            meta = obj.get("metadata") or {}
            return {"ok": True,
                    "event_id": evt.get("id"),
                    "event_type": evt.get("type"),
                    "payment_id": obj.get("payment_intent") or obj.get("id"),
                    "order_id": obj.get("id"),
                    "amount": float(obj.get("amount_total") or obj.get("amount") or 0) / 100.0,
                    "status": obj.get("payment_status") or obj.get("status"),
                    "notes": {"customer_id": meta.get("customer_id") or "",
                              "company_id": meta.get("company_id") or ""}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}

    def refund(self, payment_id: str, amount_inr: float | None = None,
                reason: str = "") -> dict:
        try:
            s = self._client()
            body = {"payment_intent": payment_id}
            if amount_inr:
                body["amount"] = int(round(float(amount_inr) * 100))
            if reason:
                body["reason"] = "requested_by_customer"
                body["metadata"] = {"note": reason[:200]}
            r = s.Refund.create(**body)
            return {"ok": True, "refund_id": r.id,
                    "amount": float(r.amount or 0) / 100.0,
                    "status": r.status}
        except Exception as e:
            return {"ok": False, "message": str(e)[:240]}


# Registry → name -> class
ADAPTERS = {
    "razorpay": RazorpayAdapter,
    "payu":     PayUAdapter,
    "cashfree": CashfreeAdapter,
    "phonepe":  PhonePeAdapter,
    "ccavenue": CCAvenueAdapter,
    "stripe":   StripeAdapter,
}


def make(name: str, creds: dict):
    """Construct adapter by name. Returns None when name unknown."""
    cls = ADAPTERS.get((name or "").lower())
    if not cls:
        return None
    return cls(creds or {})
