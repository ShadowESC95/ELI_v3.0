"""Activating a key on this computer, giving its seat back, and anchoring the trial to the store.

For products. A key bought to be activated (format 3) opens nothing by itself. :func:`activate`
sends it to the store once, with this computer's activation code; the store counts the computers
the licence is on against its seats and returns this computer's own key, which is kept and works
offline from then on. Asking again from a computer already counted uses no other seat, so
reinstalling never costs one. Any other key (one already bound to a computer, or made before
activation existed) is kept as it is.

:func:`deactivate` gives this computer's seat back (moving to a new computer); a buyer who can no
longer reach the old computer releases it on the store's activations page instead
(:func:`eli_sdk.store.manage_page`). A computer that cannot go online is activated from any other
device on the store's activation page, with the code :func:`activation_code` shows.

:func:`anchor_trial` asks the store, when it can be reached, for this computer's trial: the store
gives each computer one trial per product, so a trial whose records were deleted here ends when the
first one did.

What is sent, and only when one of these is asked for: the licence key, and this computer's
activation code. That code is :func:`eli_sdk.machine.fingerprint`, a one-way hash of the
operating system's install id: not the computer's name, network address, or anything about the
person using it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import store as _store
from .errors import InvalidKey, SDKError
from .keys import KeyStore, License, normalize, read_key
from .machine import fingerprint
from .products import product_label
from .trial import LocalTrial, TrialStatus

_ACTIVATE_TIMEOUT = 15.0
_TRIAL_TIMEOUT = 4.0


@dataclass(eq=False)
class ActivationError(SDKError):
    """Activating (or deactivating) did not happen. ``message`` is written for the buyer, and
    says what to do; ``code`` is the store's reason (eli_sdk.store.StoreError.code)."""

    message: str
    code: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def activation_code(machine: str | None = None) -> str:
    """This computer's activation code, as a buyer reads and copies it: 64 characters in eight
    groups. The store's pages accept it with or without the spaces."""
    code = (machine or fingerprint()).lower()
    return " ".join(code[i:i + 8] for i in range(0, len(code), 8))


def _message(err: _store.StoreError, lic: License, code: str, store: str | None) -> str:
    label = lic.label
    if err.code == "seats_full":
        seats = int(err.detail.get("seats") or lic.seats)
        used = int(err.detail.get("used") or seats)
        which = "computer" if seats == 1 else "computers"
        return (f"This {label} licence is already in use on {used} of its {seats} {which}. To use it "
                f"here, deactivate it on another computer ({label}: Licence, then Deactivate this "
                f"computer), or release a computer you can no longer reach at "
                f"{_store.manage_page(store)}")
    if err.code == "withdrawn":
        return (f"This {label} licence has been withdrawn, so it cannot be activated. If you think "
                "that is a mistake, contact the seller with your licence number "
                f"({lic.license_id}).")
    if err.code == "expired":
        return f"This {label} licence has ended, so it cannot be activated."
    if err.code == "busy":
        return "The licence store is busy. Try again in a few minutes."
    if err.code == "offline":
        return (f"Activating {label} needs the internet once, and the licence store could not be "
                f"reached. If this computer cannot go online, activate it from any other device at "
                f"{_store.activate_page(store)} with this computer's activation code:\n{code}\n"
                "and enter the key you are given there.")
    return f"{label} could not be activated: {err}"


def activate(key: str, keystore: KeyStore | None = None, *, public_key: Any = None,
             machine: str | None = None, store: str | None = None,
             timeout: float = _ACTIVATE_TIMEOUT) -> License:
    """Take a key in on this computer and return what it grants here.

    A key bought to be activated is sent to the store once and the computer's own key it returns
    is kept; any other key is kept as it is (KeyStore.add). Raises InvalidKey for a key no
    product accepts, ActivationError when activating did not happen (its message says why, and
    what to do)."""
    keystore = keystore or KeyStore.shared()
    lic = read_key(key, public_key)
    if not lic.needs_activation:
        return keystore.add(key, public_key, machine=machine)
    here = (machine or fingerprint()).lower()
    code = activation_code(here)
    try:
        bound = _store.activate(normalize(key), here, store=store, timeout=timeout)
    except _store.StoreError as err:
        raise ActivationError(_message(err, lic, code, store), err.code or "refused", err.detail) from None
    try:
        got = read_key(bound, public_key)
    except InvalidKey as exc:
        raise ActivationError(f"The licence store's answer was not a key {lic.label} accepts ({exc}).",
                              "invalid") from None
    same = (got.license_id, got.product, got.machine) == (lic.license_id, lic.product, here)
    if not same or got.needs_activation:
        raise ActivationError(f"The licence store answered with a key for something else; {lic.label} "
                              "was not activated.", "invalid")
    return keystore.add(bound, public_key, machine=here)


def deactivate(product: str, keystore: KeyStore | None = None, *, public_key: Any = None,
               store: str | None = None, timeout: float = _ACTIVATE_TIMEOUT) -> bool:
    """Give this computer's seat back and remove its key, so the licence can be activated on
    another computer. Returns False when no key is held for the product.

    A key that was activated online is removed only once the store has freed its seat: removing
    it first would leave the seat taken until it is released on the store's activations page.
    Any other key is simply removed."""
    keystore = keystore or KeyStore.shared()
    text = keystore.key_text(product)
    if text is None:
        return False
    try:
        lic = read_key(text, public_key)
    except InvalidKey:
        return keystore.remove(product)
    if lic.activation:
        try:
            _store.deactivate(text, store=store, timeout=timeout)
        except _store.StoreError as err:
            if err.code == "not_activated":  # already released (on the store's page, or by support)
                return keystore.remove(product)
            label = product_label(product)
            if err.code == "offline":
                raise ActivationError(
                    f"Deactivating gives this computer's {label} seat back to the licence store, "
                    "which could not be reached. Try again when this computer is online, or release "
                    f"it from any other device at {_store.manage_page(store)}.", "offline") from None
            raise ActivationError(f"{label} could not be deactivated: {err}", err.code or "refused",
                                  err.detail) from None
    return keystore.remove(product)


def anchor_trial(trial: LocalTrial, keystore: KeyStore | None = None, *, public_key: Any = None,
                 store: str | None = None, timeout: float = _TRIAL_TIMEOUT) -> TrialStatus:
    """The trial on this computer, held to the store's record of it when the store can be reached.

    The store gives each computer one trial per product, and the same trial key each time it is
    asked: its end date is fixed the first time. A trial started here after its records were
    deleted therefore ends when the first one did. Offline, the trial on this computer stands as
    it is. Asks nothing of the store until the trial has started here."""
    status = trial.status()
    if status.state == "available":
        return status
    keystore = keystore or KeyStore.shared()
    here = trial.machine
    held = _store_trial(keystore.key_text(trial.product), trial.product, here, public_key)
    if held is None:
        try:
            key = _store.request_trial_key(trial.product, here, store=store, timeout=timeout)
            held = _store_trial(key, trial.product, here, public_key)
            if held is not None:
                keystore.add(key, public_key, machine=here)
        except (_store.StoreError, InvalidKey):
            return status
    if held is None or not held.expires_at:
        return status
    return trial.no_later_than(held.expires_at, held.issued_at)


def _store_trial(text: str | None, product: str, machine: str, public_key: Any) -> License | None:
    if not text:
        return None
    try:
        lic = read_key(text, public_key)
    except InvalidKey:
        return None
    return lic if (lic.trial and lic.product == product and lic.machine == machine) else None
