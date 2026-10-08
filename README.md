# ViewAR – AR View for Blender (Add-on)

Live preview your Blender scene in augmented reality on iPhone and iPad.

The add-on runs a small server inside Blender and streams the open scene over your local network to the **ViewAR** iOS app. Move an object, add a modifier or tweak a material, and the AR view updates within a fraction of a second.

> The full technical specification (architecture, wire protocol, source walkthrough) is in [ViewAR-Blender-Addon.md](ViewAR-Blender-Addon.md).

---

## Features

- **Live streaming.** Object transforms are sent at up to 60 updates per second. Meshes are re-sent when their geometry or materials change (at most 10 times per second per object).
- **Only changes go over the network.** A full scene is sent only when a device connects, when you open another .blend file, or when you press **Resync**.
- **Modifiers applied.** What you see in the viewport (evaluated geometry) is what you get in AR.
- **Basic materials.** Base Color, Metallic, Roughness and Alpha from the Principled BSDF.
- **Automatic discovery.** Blender appears in the app under *Nearby* via Bonjour (mDNS). You can also enter the IP manually.
- **Multiple devices.** Several phones can connect at once, and a phone that joins late receives the whole scene.
- **Protects slower phones.** A per-object triangle budget, plus automatic resync for devices that fall behind.

**Streamed:** mesh, curve, surface, metaball and text objects that are visible in the current view layer, with per-corner normals and the active UV map.
**Not yet streamed:** lights, cameras, image textures, and collection or Geometry Nodes instances (see the [roadmap](#roadmap)).

---

## Requirements

| Item | Requirement |
|---|---|
| Blender | 4.2 or newer |
| OS | Windows, macOS or Linux |
| iOS | The ViewAR app on an ARKit-capable iPhone or iPad |
| Network | Computer and phone on the **same** Wi-Fi or LAN, without client isolation |

No extra Python packages need to be installed. The Bonjour libraries are bundled as wheels, and Blender installs them for you.

---

## Installation

### From a release zip

1. Download `viewar-<version>.zip` (or build it yourself, see below).
2. In Blender go to **Edit › Preferences › Get Extensions**.
3. Open the **⌄** dropdown in the top right and choose **Install from Disk…**.
4. Select the zip. ViewAR is enabled automatically.

### Building the zip

With Blender on your `PATH`:

```bash
blender --command extension validate --source-dir ./blender/viewar
```

```bash
blender --command extension build --source-dir ./blender/viewar --output-dir ./dist
```

Without Blender on your `PATH`, zipping the folder contents gives the same result:

```bash
cd blender/viewar && zip -r ../../dist/viewar-1.0.0.zip . -x '*__pycache__*' '.*'
```

On macOS the Blender binary is at `/Applications/Blender.app/Contents/MacOS/Blender`.

---

## Usage

1. In the 3D Viewport press **N** to open the sidebar, then select the **ViewAR** tab.
2. Optionally adjust the settings (only editable while the server is stopped):
   - **Port** (default `51515`)
   - **Updates per Second** (default `30`)
   - **Max Triangles per Object** (default `300,000`). Objects above this are skipped.
3. Press **Start Server**. The panel shows this computer's IP and port, and whether it is visible to nearby devices.
4. Open the ViewAR app and pick your computer under **Nearby**, or type the IP and port shown in the panel.
5. Tap a surface in the app to place the scene. You see Blender's **Front** view first.
6. Work as usual. The panel lists connected devices, the number of streamed objects and triangles, and any objects skipped for being over budget.
7. **Resync** sends the whole scene again. **Stop** disconnects all devices and closes the port.

Settings are stored on Blender's window manager, so they are **not** saved into your .blend files.

### Tips for smooth results on older iPhones

- Keep the whole scene under about **1 million triangles**. The panel warns you above that.
- Lower the **viewport** level of Subdivision Surface modifiers while previewing.
- Hide objects you don't need. Hidden objects are removed from the stream immediately.
- For heavy animated scenes, reduce **Updates per Second** to 15–20.

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
| "Could not open port" | Another program or a second Blender is using the port. Choose a different port and enter it in the app. |
| "Use manual IP in the app" | Auto discovery failed to start. Manual IP still works. Check the system console for the error. |
| App never finds the computer | Usually the firewall or network (see above). Try manual IP first to tell discovery problems apart from connection problems. |
| Connected, but nothing appears | Tap a surface to place the scene. Check that the objects are visible in the current view layer, then press **Resync**. |
| An object is missing in AR | It is over the triangle budget (listed as *Skipped*), is an unsupported type, or comes from a collection instance. |
| Wrong colors | Only unlinked Principled BSDF values are read. If Base Color is driven by a texture, the material's viewport display color is used. |
| Scene is huge or tiny | Check **Scene Properties › Units › Unit Scale**. One Blender unit equals one meter at Unit Scale 1. |

Errors are printed with full tracebacks to the system console (**Window › Toggle System Console** on Windows, or start Blender from a terminal on macOS and Linux).

---

## Project layout

```
.
├── README.md
├── ViewAR-Blender-Addon.md      # full specification and wire protocol
└── blender/
    └── viewar/
        ├── blender_manifest.toml   # extension metadata, permissions, wheels
        ├── __init__.py             # settings, panel, operators, handlers, timer
        ├── server.py               # threaded TCP server and frame encoding
        ├── discovery.py            # optional Bonjour (_viewar._tcp) advertising
        ├── scene_stream.py         # change tracking and mesh export
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

1. Image textures (base color, normal, roughness)
2. Collection and Geometry Nodes instancing
3. Pairing code for connections
4. One-shot USDZ snapshot for full material fidelity
5. Delta meshes (send only positions when topology is unchanged)

---

## License

GPL-3.0-or-later, because the add-on uses Blender's Python API. The ViewAR iOS app is a separate program and is not covered by this license.
