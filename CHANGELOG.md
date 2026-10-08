# Changelog

All notable changes to the ViewAR Blender add-on. The add-on version is the `version` in `blender/viewar/blender_manifest.toml`. The wire protocol has its own version (`PROTOCOL_VERSION` in `scene_stream.py`).

## 1.1.0 — 2026-10-09

Wire protocol stays at **v1**, so this release works with the existing ViewAR iOS app.

### Added
- **Instances.** Collection, particle and Geometry Nodes instances are streamed. Each one is sent as its own object with a stable id, and a mesh shared by many instances is built only once per update. Toggle it with **Include Instances** and cap it with **Max Instances** (default 500).
- **Choose what to stream.** Visible objects, selected objects, or a single collection.
- **Show in AR** per object, to keep reference or helper objects out of AR while they stay visible in Blender. It is saved with the .blend file, and Alt+click applies it to all selected objects.
- **AR Origin.** Use the world origin or the 3D cursor as the point placed in AR.
- **Pause Updates.** Freezes what devices show during heavy edits. Devices catch up on resume, and a device that joins while paused still gets the current scene.
- **Saved preferences.** Port, update rate, triangle and instance limits are now add-on preferences, kept between sessions and also editable under Edit › Preferences › Add-ons.
- **Start Server with Blender** option.
- **Copy Address** button, which works with Universal Clipboard on iPhone.
- All LAN addresses are listed in the panel and advertised over Bonjour, which helps with VPNs and machines on both Ethernet and Wi-Fi.
- Per-device data counter and **Disconnect** button.
- **Statistics** and **Settings** sub-panels, and the most recent error shown in the panel.
- `CHANGELOG.md`, `build.py`, and a ready-to-install zip in `dist/`.

### Changed
- Changing the Unit Scale or switching the active scene resyncs devices automatically. Before, it needed a manual Resync.
- Changing **Max Triangles per Object** while streaming applies immediately.
- Material edits now update objects that live outside the scene, such as sources of instanced collections.
- Idle ticks are about 2.5× cheaper, because converted matrices are cached until an object moves.

### Fixed
- Curve, text, surface and metaball objects are not sent twice when instances are enabled. Blender 5 also lists their evaluated mesh as an instance of themselves.
- No `Material.use_nodes` deprecation warning on Blender 5.
- `unitScale` is rounded, so 0.01 is sent as `0.01` and not `0.009999999776`.
- README used the old `extension validate --source-dir` syntax, which Blender 5.2 rejects.

### Tested
- Blender 5.2.2 LTS (Python 3.13) on macOS: a headless suite of 47 checks over a real TCP connection, covering full sync, live edits, instances, filters, origin, pause, budgets, resync triggers, devices, discovery, panel drawing, the busy-port error, auto start, and disable/re-enable.

## 1.0.0 — 2026-10-08

First release, built from the specification in `ViewAR-Blender-Addon.md`.

- TCP server with length-prefixed frames and one sender thread per device.
- Live streaming of transforms and evaluated meshes, with Principled BSDF materials.
- Optional Bonjour discovery with bundled pure-Python `zeroconf`, `ifaddr` and `async_timeout` wheels.
- Material changes made in the Shader Editor are picked up through the material's node tree.
