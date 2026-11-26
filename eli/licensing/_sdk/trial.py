"""A product's free trial on this machine, started on first use and kept so that the simple ways
of restarting it do not work.

Before a key is entered a product may run for ``TRIAL_DAYS`` days. A key can be entered at any
time: during the trial, at its end, or later. What does not restart the trial:

- **Deleting its record.** The record is mirrored to several places in this account (and, on
  Windows, the registry); a missing copy is written again from the others at the next launch.
- **Changing environment variables.** The places are found from the account itself
  (:func:`eli_sdk.machine.account_home`, Windows' known folders), never from ``$HOME``,
  ``XDG_*``, ``%APPDATA%`` or ``ELI_KEYS_DIR``.
- **Editing it.** Each copy is sealed with a code tied to this machine; an altered copy ends the
  trial, and so does a copy taken from another machine.
- **Setting the clock back.** The latest time the product was seen running is kept; a clock
  that goes back behind it by more than six hours stops the trial until the clock is right
  again (it cannot add days: the end date is fixed when the trial starts, and once the product
  has run past it the trial has ended for good).
- **Deleting every copy.** Where a product finds its own data from earlier use (its database,
  its cases) it says so (``used_before``), and no new trial starts.

This is tamper-*resistance*, not a proof: someone who deletes every copy *and* all the
product's data starts again, as on a new machine, and the code sealing the copies is inside
the program. The only grant that cannot be forged is a signed key; an online trial key from the
store (eli_sdk.store.request_trial_key) is one, bound to this machine.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .machine import account_home, fingerprint, windows_folder
from .products import TRIAL_DAYS, product_label

# Seals each copy. Tied to the machine below, so a copy from another machine does not verify.
_SEAL = b"eli-sdk/trial/v1/4d2b8f0e6a1c93e7"
_VERSION = 1
_CLOCK_SLACK = timedelta(hours=6)
_REGISTRY_KEY = r"Software\ELI\Trials"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _when(text: object) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def default_locations() -> list[Path]:
    """The folders a trial's record is mirrored to, found from the account itself."""
    # Folders of the ELI key store's own, never a generic "eli" folder: those belong to no one ELI
    # product (ELI v2 keeps its data in ~/.local/share/eli).
    home = account_home()
    if os.name == "nt":
        roaming = windows_folder("roaming") or home / "AppData" / "Roaming"
        local = windows_folder("local") or home / "AppData" / "Local"
        return [roaming / "ELI Keys", local / "ELI Keys" / "State", home / ".eli-keys"]
    if sys.platform == "darwin":
        lib = home / "Library"
        return [lib / "Application Support" / "ELI Keys", lib / "Preferences" / "ELI Keys",
                home / ".eli-keys"]
    return [home / ".config" / "eli-keys", home / ".local" / "share" / "eli-keys",
            home / ".local" / "state" / "eli-keys"]


@dataclass(frozen=True)
class TrialStatus:
    """Where a product's trial stands on this machine."""

    product: str
    state: str  # "available" | "active" | "ended"
    days: int
    started_at: str = ""
    ends_at: str = ""
    reference: str = ""
    why: str = ""
    # An ended trial stays ended (past its end, or a record altered); a clock set back only
    # stops it while the clock is behind.
    final: bool = True

    @property
    def active(self) -> bool:
        return self.state == "active"

    @property
    def days_left(self) -> int:
        ends = _when(self.ends_at)
        if self.state != "active" or ends is None:
            return 0
        seconds = int((ends - _now()).total_seconds())
        return max(0, -(-seconds // 86400))

    def describe(self) -> str:
        label = product_label(self.product)
        if self.state == "active":
            n = self.days_left
            return f"{label} trial: {n} day{'s' if n != 1 else ''} left"
        if self.state == "available":
            return f"a {self.days}-day {label} trial starts the first time it is used"
        return self.why or f"the {label} trial has ended"


class LocalTrial:
    """One product's trial on this machine.

    ``used_before`` answers whether the product has been used here already (its data exists);
    with no record of a trial that means one was used and its record deleted. ``locations``,
    ``machine`` and ``clock`` are for tests.
    """

    def __init__(self, product: str, *, days: int = TRIAL_DAYS,
                 used_before: Callable[[], bool] | None = None,
                 locations: Sequence[Path] | None = None, machine: str | None = None,
                 clock: Callable[[], datetime] | None = None, registry: bool = True):
        self.product = str(product).lower()
        self.days = int(days)
        self.used_before = used_before
        self._locations = list(locations) if locations is not None else None
        self._machine = machine
        self.clock = clock or _now
        self.registry = registry and os.name == "nt" and locations is None

    # ── where ───────────────────────────────────────────────────────────────
    @property
    def machine(self) -> str:
        return self._machine or fingerprint()

    def paths(self) -> list[Path]:
        folders = self._locations if self._locations is not None else default_locations()
        return [Path(f) / f".{self.product}-trial" for f in folders]

    # ── sealing ─────────────────────────────────────────────────────────────
    def _seal(self, record: dict[str, object]) -> str:
        secret = hashlib.sha256(_SEAL + self.machine.encode("utf-8")).digest()
        body = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(secret, body, hashlib.sha256).hexdigest()

    def _sealed(self, record: dict[str, object]) -> str:
        return json.dumps({**record, "seal": self._seal(record)}, sort_keys=True) + "\n"

    def _open(self, text: str) -> dict[str, object] | None:
        """The record if its seal holds for this machine and product; None if it does not."""
        try:
            raw = json.loads(text)
        except ValueError:
            return None
        if not isinstance(raw, dict):
            return None
        seal = str(raw.pop("seal", ""))
        if not hmac.compare_digest(seal, self._seal(raw)):
            return None
        if raw.get("product") != self.product or raw.get("machine") != self.machine:
            return None
        return raw

    # ── reading and writing every copy ──────────────────────────────────────
    def _copies(self) -> tuple[list[dict[str, object]], bool]:
        """(the copies whose seal holds, whether any copy was found that does not)."""
        good: list[dict[str, object]] = []
        altered = False
        texts: list[str] = []
        for path in self.paths():
            try:
                if path.is_file():
                    texts.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
        if self.registry:
            texts.extend(self._registry_read())
        for text in texts:
            record = self._open(text)
            if record is None:
                altered = True
            else:
                good.append(record)
        return good, altered

    def _write_all(self, record: dict[str, object]) -> int:
        text = self._sealed(record)
        written = 0
        for path in self.paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".t-")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(text)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, path)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                written += 1
            except OSError:
                continue
        if self.registry and self._registry_write(text):
            written += 1
        return written

    def _registry_read(self) -> list[str]:
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
                value, _ = winreg.QueryValueEx(key, self.product)
            return [str(value)]
        except (ImportError, OSError):
            return []

    def _registry_write(self, text: str) -> bool:
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
                winreg.SetValueEx(key, self.product, 0, winreg.REG_SZ, text)
            return True
        except (ImportError, OSError):
            return False

    # ── state ───────────────────────────────────────────────────────────────
    def _merged(self) -> tuple[dict[str, object] | None, bool]:
        good, altered = self._copies()
        if not good:
            return None, altered
        first = min(good, key=lambda r: str(r.get("started_at", "")))
        merged = dict(first)
        merged["last_seen"] = max(str(r.get("last_seen", "")) for r in good)
        if any(r.get("ended") for r in good):
            merged["ended"] = next(str(r["ended"]) for r in good if r.get("ended"))
        return merged, altered

    def status(self) -> TrialStatus:
        """Where the trial stands, read from every copy. Changes nothing."""
        label = product_label(self.product)
        record, altered = self._merged()
        if altered:
            return self._ended(record, "a record of the trial on this machine was altered, so "
                                       "the trial has ended")
        if record is None:
            if self.used_before is not None and self.used_before():
                return TrialStatus(self.product, "ended", self.days,
                                   why=f"{label} has been used on this machine before; the "
                                       "trial was for its first use")
            return TrialStatus(self.product, "available", self.days)
        started, ends = _when(record.get("started_at")), _when(record.get("ends_at"))
        seen = _when(record.get("last_seen")) or started
        now = self.clock()
        if started is None or ends is None:
            return self._ended(record, f"the {label} trial's record is unreadable")
        if record.get("ended"):
            return self._ended(record, str(record["ended"]))
        if seen is not None and now < seen - _CLOCK_SLACK:
            return self._ended(record, "the computer's clock is behind the last time "
                                       f"{label} ran: set the date and time right to go on",
                               final=False)
        if now > ends:
            return self._ended(record, f"the {self.days}-day {label} trial ended on "
                                       f"{ends.date().isoformat()}")
        return TrialStatus(self.product, "active", self.days, str(record["started_at"]),
                           str(record["ends_at"]), str(record.get("reference", "")))

    def _ended(self, record: dict[str, object] | None, why: str, *, final: bool = True) -> TrialStatus:
        record = record or {}
        return TrialStatus(self.product, "ended", self.days, str(record.get("started_at", "")),
                           str(record.get("ends_at", "")), str(record.get("reference", "")), why,
                           final)

    def start(self) -> TrialStatus:
        """Start the trial if it has never been used here; otherwise say where it stands."""
        current = self.status()
        if current.state != "available":
            return self.touch()
        now = self.clock()
        raw = uuid.uuid4().hex[:16].upper()
        record: dict[str, object] = {
            "v": _VERSION,
            "product": self.product,
            "machine": self.machine,
            "reference": f"TRIAL-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}",
            "started_at": _iso(now),
            "ends_at": _iso(now + timedelta(days=self.days)),
            "last_seen": _iso(now),
        }
        if not self._write_all(record):
            return TrialStatus(self.product, "ended", self.days,
                               why="the trial could not be recorded on this machine")
        return self.status()

    def touch(self) -> TrialStatus:
        """Record that the product is running now, and write back any copy that is missing, so
        deleting some copies never helps. Call it at each launch. An ended trial stays ended."""
        record, altered = self._merged()
        status = self.status()
        if record is None:
            return status
        if status.state == "ended" and status.final and not record.get("ended"):
            record["ended"] = status.why
        now = _iso(self.clock())
        if status.state == "active" and now > str(record.get("last_seen", "")):
            record["last_seen"] = now
        self._write_all(record)
        return self.status()
