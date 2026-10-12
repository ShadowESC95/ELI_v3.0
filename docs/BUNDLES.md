# Bundles

A bundle is a separate ELI application, specialised for one field, that runs on ELI v3's core in a
window of its own. ELI Legal was the first and ELI VR is the second; others (Healthcare, Finance)
are meant to follow the same contract. The code is in `eli/bundles/`.

## Opening one

| How | What happens |
|---|---|
| Start ELI with one or more bundles installed | ELI asks: ELI v3, or one of the bundles. Tick "Always open this" to skip the question; `eli --choose` asks again, `eli --no-bundles` never asks |
| `eli --bundle legal` | Opens ELI Legal in place of ELI v3's window |
| `eli --bundle vr` | Opens ELI VR, with ELI's local server answering for it |
| `eli --bundles` | Lists the bundles installed, how each was found, and whether the licence allows it |
| Bundles menu in ELI v3's window | Opens a bundle in a window of its own beside ELI v3 |

The remembered choice is the `startup_target` setting: `ask` (the default), `eli`, or `bundle:<id>`.

## How a bundle makes itself known

| Kind | How it is found | Runs |
|---|---|---|
| In-process | A package installed in ELI v3's Python environment that advertises itself in the `eli.bundles` entry-point group | Inside ELI v3's process, so it can use ELI v3's loaded model and its settings |
| External | A separately installed program that wrote `<id>.json` into a `bundles` folder of ELI v3's data (`~/.local/share/ELI_v3/bundles`, or `$ELI_DATA_DIR/bundles`) | As its own program, with its own settings |

`ELI_EXTRA_BUNDLES=module:attr` adds an in-process bundle from a source checkout on the path, for
development.

## The contract (version 1)

An in-process bundle's entry point gives a dict, or a function returning one:

```python
{
    "contract": 1,
    "id": "legal",                  # letters, digits, - and _
    "title": "ELI Legal",
    "summary": "…",
    "version": "0.5.0",
    "edition": "legal",             # the licence edition it belongs to
    "icon": "/path/to/icon.png",
    "launch": "eli_legal.bundle:launch",
    "needs": ["server"],            # optional, see below
}
```

`launch(argv, *, host)` opens the bundle and returns an exit code. `host` carries `name`, `version`
and `root`. An external registration has the same fields with `"command": [program, args…]` in
place of `launch`.

## A bundle that is not a Python program

ELI Legal runs inside ELI v3's process and calls the model directly. ELI VR is a Godot application:
it cannot, so it reaches the core over ELI's local server (the world feed, speech in, chat, speech
out). A bundle like that says `"needs": ["server"]`, and before it starts ELI makes sure the server
is answering:

- If ELI's server is already running, that one is used and left running.
- Otherwise one is started in this process, on `127.0.0.1` only, and stopped when the bundle closes.
- The bundle is told the port: `host["api_port"]` for an in-process bundle, the `ELI_API_PORT`
  environment variable for an external one.
- If the port is held by another program, or the server cannot start, the bundle still opens and
  says ELI is not there.

From the Bundles menu such a bundle is started through `eli --bundle <id>` in a process of its own,
so the same happens there. Switch ELI's server on in its window first to have the bundle use the
model that window has already loaded. The code is `eli/bundles/core_server.py`.

## The bundles

| Bundle | Id | What it is | Its side of the contract |
|---|---|---|---|
| ELI Legal | `legal` | Governed case preparation, in a window of its own | `eli_legal.bundle` |
| ELI VR | `vr` | ELI's rooms as places to stand in, in a headset or on a monitor | `eli_vr.bundle`, and `needs: ["server"]` |

Each also has an edition pack under `eli/editions/`.

A bundle keeps its data in its own folder, never in ELI v3's.

## Licensing

`eli.bundles.entitled` decides whether the licence allows a bundle. Licences do not yet carry
editions (see `eli.editions.active_editions`), so for now it allows every bundle. When they do, it
should look up the bundle's `edition` there.
