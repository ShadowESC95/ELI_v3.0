"""ELI VR edition pack.

A composable overlay on the shared ELI core (see eli/editions/README.md).
Capabilities, personas, templates and tools live in this package's subfolders;
policy.py carries the edition's safety/compliance hooks. The pack is inert until
the EditionRegistry activates it for an entitled licence.

ELI VR itself is a separate application (a bundle, see docs/BUNDLES.md). It reads
ELI's world over the local server (eli/world/spatial_feed.py) and needs nothing
from this pack to run; the pack is where what ELI can do in its rooms will live.
"""
