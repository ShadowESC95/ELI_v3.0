"""Talking to ELI's licence store, for products that are allowed to go online.

- :func:`shop_url` (what is on sale) or :func:`buy_url` (one offering) is the page a product's
  **Buy** button opens. It carries a *claim*: a random
  token the product made, which the store attaches to the order, so the product can then pick up
  the key itself (:func:`collect`) and activate it without the buyer copying anything. The buyer
  also sees the key on screen when payment completes, and receives it by email.
- :func:`request_trial_key` asks for a trial key bound to this machine. The store gives each
  machine one trial per product, so reinstalling, deleting files or changing the clock never
  starts another; the key it returns is signed, so it cannot be edited or forged.
- :func:`fetch_revocations` brings the latest signed list of withdrawn licences.

Only HTTPS is spoken (``http://localhost`` aside, for testing a store on this machine). What is
sent is what each call needs and nothing more: the claim token, or the product and this
machine's fingerprint (a hash, not the machine's name or address).
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

STORE_URL = "https://licences.geteli.tech"
_TIMEOUT = 10.0
_AGENT = "eli-sdk"


class StoreError(Exception):
    """The store could not be reached, or refused the request (the message says why)."""


def _base(store: str | None) -> str:
    url = (store or STORE_URL).rstrip("/")
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise StoreError(f"the store must be reached over HTTPS, not {url!r}")
    return url


def new_claim() -> str:
    """A fresh claim token for one purchase."""
    return secrets.token_urlsafe(24)


def shop_url(product: str | None = None, claim: str | None = None, *,
             store: str | None = None) -> str:
    """The store's page of what is on sale (for one product, if given), carrying the claim
    through to whichever offering the buyer chooses."""
    query = {k: v for k, v in (("product", product), ("claim", claim)) if v}
    return f"{_base(store)}/" + ("?" + urllib.parse.urlencode(query) if query else "")


def buy_url(offering: str, claim: str | None = None, *, store: str | None = None,
            email: str | None = None) -> str:
    """The store page for one offering (``legal-pro``, ``eli-personal``…), carrying the claim
    token so the product can collect the key, and the buyer's email if known."""
    query: dict[str, str] = {}
    if claim:
        query["claim"] = claim
    if email:
        query["email"] = email
    path = f"{_base(store)}/buy/{urllib.parse.quote(offering, safe='')}"
    return path + ("?" + urllib.parse.urlencode(query) if query else "")


def _call(method: str, url: str, body: dict[str, Any] | None = None,
          timeout: float = _TIMEOUT) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", "User-Agent": _AGENT,
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https enforced)
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error") or exc.reason
        except Exception:
            detail = exc.reason
        raise StoreError(f"the store refused: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StoreError(f"the store could not be reached ({exc})") from None
    except ValueError:
        raise StoreError("the store's answer was not readable") from None
    if not isinstance(payload, dict):
        raise StoreError("the store's answer was not readable")
    return payload


def collect(claim: str, *, store: str | None = None, timeout: float = _TIMEOUT) -> list[str]:
    """The keys bought under this claim, or [] while the payment is still being completed."""
    url = f"{_base(store)}/v1/claim/{urllib.parse.quote(claim, safe='')}"
    payload = _call("GET", url, timeout=timeout)
    return [str(k) for k in payload.get("keys") or []] if payload.get("status") == "ready" else []


def request_trial_key(product: str, machine: str, *, store: str | None = None,
                      timeout: float = _TIMEOUT) -> str:
    """This machine's trial key for a product: the same key every time it is asked for."""
    payload = _call("POST", f"{_base(store)}/v1/trial",
                    {"product": product, "machine": machine}, timeout=timeout)
    key = str(payload.get("key") or "")
    if not key:
        raise StoreError(str(payload.get("error") or "the store gave no trial key"))
    return key


def fetch_revocations(*, store: str | None = None, timeout: float = _TIMEOUT) -> list[str]:
    """The store's current signed revocation lists (verify them with KeyStore.add_revocations)."""
    payload = _call("GET", f"{_base(store)}/v1/revocations", timeout=timeout)
    return [str(t) for t in payload.get("lists") or []]
