"""ELI v3's copy of eli-sdk is that release, file for file (scripts/licensing/sync_sdk.py)."""
import hashlib
import json
from pathlib import Path

SDK = Path(__file__).resolve().parents[1] / "eli" / "licensing" / "_sdk"


def test_the_copy_of_eli_sdk_is_unchanged():
    source = json.loads((SDK / "SOURCE.json").read_text())
    from eli.licensing import _sdk

    assert source["version"] == _sdk.__version__
    for name, digest in source["files"].items():
        assert hashlib.sha256((SDK / name).read_bytes()).hexdigest() == digest, (
            f"{name} differs from eli-sdk {source['version']}: change eli-sdk and sync again")


def test_elis_public_key_is_the_same_in_both():
    ours = (Path(__file__).resolve().parents[1] / "eli" / "licensing" / "keys" / "product_signing.pub").read_bytes()
    assert (SDK / "eli.pub").read_bytes() == ours
