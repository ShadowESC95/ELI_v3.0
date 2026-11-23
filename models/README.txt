ELI v3 release artifacts (organised copy)

Use these folders by target platform (<version> is the release, such as 3.0.173):

Windows/
  ELI_v3-<version>-windows-portable.zip
  Extract, then run install.bat or install.ps1 inside the folder.

Linux/
  ELI_v3.0-<version>-linux-portable.tar.gz
  Extract, then ./INSTALL_ELI.sh and ./RUN_ELI.sh
  (Best tested on Linux x86_64 + NVIDIA.)

  eli-v3_<version>_amd64.deb
  sudo apt install ./eli-v3_<version>_amd64.deb
  (Up to 3.0.171 the package was named eli-mkxi; installing eli-v3 replaces it.)

macOS/
  ELI_v3-<version>-macos-arm64.dmg
  Open it and drag ELI to Applications.

Python/
  eli_v3-<version>-py3-none-any.whl
  python -m pip install eli_v3-<version>-py3-none-any.whl

Model / voice packs/
  Distributed separately via GitHub Release assets (too large for git),
  tag: local-assets-v3.0. Restore after install:
  ./RUN_ELI.sh --with-github-assets

Checksums/
  SHA256SUMS.txt alongside builds in dist/
