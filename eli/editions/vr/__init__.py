"""ELI VR edition pack.

A composable overlay on the shared ELI core (see eli/editions/README.md).
manifest.json describes the pack; its personas and report templates live in this
package's subfolders.

ELI VR itself is a separate application (a bundle, see docs/BUNDLES.md). It reads
ELI's world over the local server (eli/world/spatial_feed.py) and needs nothing
from this pack to run.
"""
