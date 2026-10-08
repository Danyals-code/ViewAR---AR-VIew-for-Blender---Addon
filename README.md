# ViewAR – AR View for Blender (Add-on)

Live preview your Blender scene in augmented reality on iPhone and iPad.

The add-on runs a small server inside Blender and streams the open scene over your local network to the **ViewAR** iOS app. Move an object, add a modifier or tweak a material, and the AR view updates within a fraction of a second.

> The full technical specification (architecture, wire protocol, source walkthrough) is in [ViewAR-Blender-Addon.md](ViewAR-Blender-Addon.md).

---

## Features

- **Live streaming.** Object transforms are sent at up to 60 updates per second. Meshes are re-sent when their geometry or materials change (at most 10 times per second per object).
- **Only changes go over the network.** A full scene is sent only when a device connects, when you open another .blend file, change the Unit Scale or switch scenes, or when you press **Resync**.
- **Modifiers applied.** What you see in the viewport (evaluated geometry) is what you get in AR.
- **Instances.** Collection instances, particle instances and Geometry Nodes instances (scattering, arrays of objects) are streamed, up to a limit you set.
- **Choose what to stream.** Everything visible, only the selection, or one collection, and any object can be kept out of AR with **Show in AR**.
- **AR origin.** Use the world origin or the 3D cursor as the point that lands where you tap, so models far from the origin are easy to place.
- **Pause updates** while you make heavy edits. Devices catch up when you resume.
- **Basic materials.** Base Color, Metallic, Roughness and Alpha from the Principled BSDF.
- **Automatic discovery.** Blender appears in the app under *Nearby* via Bonjour (mDNS), on every network interface. You can also enter or paste the IP manually.
- **Multiple devices.** Several phones can connect at once, a phone that joins late receives the whole scene, and you can see how much data each device received or disconnect it.
- **Remembers your settings.** Port, update rate and limits are kept in Blender's preferences, and the server can start automatically with Blender.
- **Protects slower phones.** Per-object triangle and instance budgets, plus automatic resync for devices that fall behind.

**Streamed:** mesh, curve, surface, metaball and text objects (and instances of them) that are visible in the current view layer, with per-corner normals and the active UV map.
**Not yet streamed:** lights, cameras and image textures (see the [roadmap](#roadmap)).

The add-on still speaks wire protocol v1, so every feature works with the existing ViewAR app.

---

## Requirements

| Item | Requirement |
|---|---|
| Blender | 4.2 or newer (tested on 5.2 LTS) |
| OS | Windows, macOS or Linux |
| iOS | The ViewAR app on an ARKit-capable iPhone or iPad |
| Network | Computer and phone on the **same** Wi-Fi or LAN, without client isolation |

No extra Python packages need to be installed. The Bonjour libraries are bundled as wheels, and Blender installs them for you.

---

## Installation

### From the ready-made zip

1. Take `dist/viewar-<version>.zip` from this repository (or build it yourself, see below).
2. In Blender go to **Edit › Preferences › Get Extensions**.
3. Open the **⌄** dropdown in the top right and choose **Install from Disk…**.
4. Select the zip. ViewAR is enabled automatically.

### Building the zip

The quickest way needs only Python 3:

```bash
python3 build.py
```

This writes `dist/viewar-<version>.zip`, taking the version from `blender_manifest.toml`, and deletes older zips. When you work in Claude Code, a project hook (`.claude/settings.json`) runs this automatically every time a file in `blender/viewar/` changes, so the zip in `dist/` always matches the source.

To also validate the manifest, use Blender's own tooling (Blender must be on your `PATH`):

```bash
blender --command extension validate ./blender/viewar
```

```bash
blender --command extension build --source-dir ./blender/viewar --output-dir ./dist
```

On macOS the Blender binary is at `/Applications/Blender.app/Contents/MacOS/Blender`.

---

## Usage

1. In the 3D Viewport press **N** to open the sidebar, then select the **ViewAR** tab.
2. Press **Start Server**. The panel shows this computer's address and port (plus any other network addresses), and whether it is visible to nearby devices. The copy button next to the address puts it on the clipboard. With Universal Clipboard you can paste it straight into the app on your iPhone.
3. Open the ViewAR app and pick your computer under **Nearby**, or enter the address shown in the panel.
4. Tap a surface in the app to place the scene. You see Blender's **Front** view first.
5. Work as usual. **Pause Updates** freezes what the devices show, and **Resume Updates** sends everything that changed in the meantime.
6. **Resync** sends the whole scene again. **Stop** disconnects all devices and closes the port.

The **Devices** box lists each connected phone with the data it has received. The **×** button disconnects that phone.

### Streaming sub-panel

| Option | What it does |
|---|---|
| **Visible Objects / Selected Objects / Collection** | What to send. *Selected* is handy for checking one asset; *Collection* streams a single collection you pick. |
| **AR Origin** | *World Origin*, or *3D Cursor* so a model far from the origin still lands where you tap (**Shift+S › Cursor to Selected** first). |
| **Include Instances** | Stream collection, particle and Geometry Nodes instances. Each instance is sent as its own mesh, so very large scatters are capped by **Max Instances**. |
| **Show *object* in AR** | Keeps the active object out of AR while it stays visible in Blender, for example reference planes or helper meshes. **Alt+click** changes all selected objects. This setting is saved with the .blend file. |

These options last for the session and are not saved into your .blend files.

### Statistics sub-panel

This shows the number of streamed objects, instances and triangles. It also warns when the scene is heavy for older iPhones, when the instance limit is reached, and which objects were skipped for exceeding the triangle budget.

### Settings sub-panel

These are also under **Edit › Preferences › Add-ons › ViewAR**, and Blender remembers them between sessions:

| Setting | Default | Notes |
|---|---|---|
| **Port** | `51515` | Can only be changed while the server is stopped. |
| **Updates per Second** | `30` | Lower it to 15–20 for heavy animated scenes. |
| **Max Triangles per Object** | `300,000` | Objects above this are skipped. Changing it applies immediately. |
| **Max Instances** | `500` | Instances beyond this are not sent. |
| **Start Server with Blender** | off | Starts streaming automatically whenever Blender opens. |

### Tips for smooth results on older iPhones

- Keep the whole scene under about **1 million triangles**. The panel warns you above that.
- Lower the **viewport** level of Subdivision Surface modifiers while previewing.
- Hide objects you don't need, or untick **Show in AR**. They are removed from the stream immediately.
- Stream only the **Selected Objects** or one **Collection** when you are working on part of a big scene.
- For heavy animated scenes, reduce **Updates per Second** to 15–20, and use **Pause Updates** during heavy edits such as sculpting.

---

## Network and firewall setup

| Platform | What to do |
|---|---|
| **Windows** | When Windows Defender Firewall asks, allow Blender on **Private networks**. If your Wi-Fi profile is set to *Public*, change it to *Private*. You don't need to install Apple Bonjour. |
| **macOS** | Allow Blender to "find devices on your local network" when asked. You can change this later in **System Settings › Privacy & Security › Local Network**. |
| **Linux** | If you use a firewall (e.g. `ufw`), open **TCP 51515** and **UDP 5353** (mDNS). |
| **All** | Guest networks and many office or university networks block device-to-device traffic. A home router or phone hotspot works reliably. |

The server only starts when you press **Start Server**, listens only on your local network, and never contacts the internet.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not open port" | Another program or a second Blender is using the port. Choose a different port in **Settings** and enter it in the app. |
| "Use manual IP in the app" | Auto discovery failed to start. Manual IP still works. Check the system console for the error. |
| App never finds the computer | Usually the firewall or network (see above). Try manual IP first to tell discovery problems apart from connection problems. If the panel lists several addresses, try each; VPNs and docks with Ethernet add extra ones. |
| Connected, but nothing appears | Tap a surface to place the scene. Check that the objects are visible in the current view layer, that **Stream** is not limited to an empty selection or collection, and that updates are not paused. Then press **Resync**. |
| Model appears far away from where I tapped | It is far from Blender's world origin. Set **AR Origin** to **3D Cursor** and snap the cursor to the model. |
| An object is missing in AR | It is over the triangle budget (listed as *Skipped*), is an unsupported type, has **Show in AR** turned off, or is an instance beyond **Max Instances**. |
| Wrong colors | Only unlinked Principled BSDF values are read. If Base Color is driven by a texture, the material's viewport display color is used. |
| Scene is huge or tiny | Check **Scene Properties › Units › Unit Scale**. One Blender unit equals one meter at Unit Scale 1. Changes are sent to devices automatically. |

The most recent error is shown at the top of the ViewAR panel. Full tracebacks are printed to the system console (**Window › Toggle System Console** on Windows, or start Blender from a terminal on macOS and Linux).

---

## Project layout

```
.
├── README.md
├── CHANGELOG.md
├── ViewAR-Blender-Addon.md      # full specification and wire protocol
├── build.py                     # packages the add-on into dist/
├── dist/
│   └── viewar-<version>.zip     # ready to install
├── .claude/settings.json        # rebuilds the zip after each add-on edit
└── blender/
    └── viewar/
        ├── blender_manifest.toml   # extension metadata, permissions, wheels
        ├── __init__.py             # preferences, panels, operators, handlers, timer
        ├── server.py               # threaded TCP server and frame encoding
        ├── discovery.py            # network addresses and Bonjour (_viewar._tcp) advertising
        ├── scene_stream.py         # change tracking, filtering, instances and mesh export
        └── wheels/                 # zeroconf, ifaddr, async_timeout (pure Python)
```

### How it works

```
depsgraph_update_post ──► SceneStreamer.on_depsgraph_update()   mark changed objects dirty
bpy.app.timers        ──► SceneStreamer.tick()                  remove / meshes / transforms / ping / full sync
                                    │
                                    ▼
                       Server ──► one sender thread per device
```

All `bpy` access happens on Blender's main thread. Only finished byte frames are passed to the network threads.

Instances come from `depsgraph.object_instances`. Protocol v1 has no instancing, so each instance is sent as an ordinary object with a stable id such as `Trees/Pine#0.12` (instancer / source object # persistent id). A mesh shared by many instances is triangulated only once per update.

### Wire protocol (v1), in short

Each frame has a 4-byte big-endian length, a 1-byte kind (`1` = JSON, `2` = binary mesh), then the payload. JSON messages are `hello`, `clear`, `transforms`, `remove` and `ping`. Mesh payloads carry little-endian positions, normals, optional UVs, triangle indices and per-triangle material indices, already converted to RealityKit's Y-up space. Full details are in section 4 of the [specification](ViewAR-Blender-Addon.md#4-wire-protocol-version-1).

### Updating the discovery wheels

The bundled `zeroconf 0.39.4` is the newest release published as a pure Python (`py3-none-any`) wheel, so one file works on every OS. Newer zeroconf releases only ship platform-specific wheels. If you upgrade, download one wheel per platform and list them all in `blender_manifest.toml`:

```bash
pip download zeroconf ifaddr async-timeout --no-deps --only-binary=:all: --python-version 3.11 -d blender/viewar/wheels
```

If you remove the wheels entirely, the add-on still works and users type the IP manually.

---

## Roadmap

These need a protocol update in the iOS app as well:

1. Image textures (base color, normal, roughness)
2. Native instancing (send each shared mesh once plus instance matrices, instead of one mesh per instance)
3. Pairing code for connections
4. One-shot USDZ snapshot for full material fidelity
5. Delta meshes (send only positions when topology is unchanged)
6. Lights and cameras

---

## License

GPL-3.0-or-later, because the add-on uses Blender's Python API. The ViewAR iOS app is a separate program and is not covered by this license.
