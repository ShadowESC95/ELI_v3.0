# Bundles

A bundle is a separate ELI application, specialised for one field, that runs on ELI v3's core in a
window of its own. ELI Legal is the first; others (ELI VR, Healthcare, Finance) are meant to follow
the same contract. The code is in `eli/bundles/`.

## Opening one

| How | What happens |
|---|---|
| Start ELI with one or more bundles installed | ELI asks: ELI v3, or one of the bundles. Tick "Always open this" to skip the question; `eli --choose` asks again, `eli --no-bundles` never asks |
| `eli --bundle legal` | Opens ELI Legal in place of ELI v3's window |
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
}
```

`launch(argv, *, host)` opens the bundle and returns an exit code. `host` carries `name`, `version`
and `root`. An external registration has the same fields with `"command": [program, args…]` in
place of `launch`.

A bundle keeps its data in its own folder, never in ELI v3's.

## Licensing

`eli.bundles.entitled` decides whether the licence allows a bundle. Licences do not yet carry
editions (see `eli.editions.active_editions`), so for now it allows every bundle. When they do, it
should look up the bundle's `edition` there.
