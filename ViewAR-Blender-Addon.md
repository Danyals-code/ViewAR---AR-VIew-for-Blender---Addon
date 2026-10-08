# ViewAR - AR for Blender: Blender Add-on

Everything needed to build, install and maintain the Blender side of ViewAR. The add-on streams the open scene over the local network to the ViewAR iOS app, which shows it in AR and updates as you work.

For the iOS app, see `ViewAR-Developer-Guide.md`. The wire protocol in section 4 is the contract between the two, so keep it in sync with the app.

---

## 1. Overview

**What it does**

1. Runs a small TCP server inside Blender (default port 51515).
2. Advertises itself on the network as `_viewar._tcp` so the app finds it automatically (optional).
3. Watches the scene for changes and sends only what changed: object transforms at up to 60 updates per second, and a fresh mesh for an object when its geometry or materials change (at most 10 times per second per object).
4. Sends the whole scene to any device that connects, and again when you open another .blend file or press Resync.

**Requirements**

| Item | Requirement |
|---|---|
| Blender | 4.2 or newer (extension format, `Mesh.corner_normals`) |
| OS | Windows, macOS or Linux |
| Python packages | None required. `zeroconf` and `ifaddr` wheels are optional, for auto discovery |
| Network | Computer and phone on the same network, without client isolation |

**What gets streamed**

Mesh, curve, surface, metaball and text objects that are visible in the current view layer, with modifiers applied (evaluated geometry), per-corner normals, the active UV map, and basic material values from the Principled BSDF (base color, metallic, roughness, alpha). Lights, cameras, textures and instances are not streamed in v1 (see section 10).

---

## 2. Folder layout

```
blender/
└── viewar/
    ├── blender_manifest.toml   # extension metadata and permissions
    ├── __init__.py             # settings, panel, operators, handlers, timer
    ├── server.py               # TCP server and frame encoding
    ├── discovery.py            # optional Bonjour advertising
    ├── scene_stream.py         # change tracking and mesh export
    └── wheels/                 # optional zeroconf + ifaddr wheels
```

---

## 3. How it works

```
depsgraph_update_post ──► SceneStreamer.on_depsgraph_update()
                              marks objects with changed geometry or materials as dirty

bpy.app.timers (30 Hz) ──► SceneStreamer.tick()
                              1. diff visible objects  -> "remove" for deleted or hidden ones
                              2. rebuild dirty meshes  -> mesh frames (rate limited)
                              3. compare world matrices -> one "transforms" frame
                              4. every 2 s              -> "ping"
                              5. new devices            -> full sync (hello, clear, all meshes, all transforms)
                                     │
                                     ▼
                         Server ──► one sender thread per device
```

Everything that touches `bpy` runs on Blender's main thread (the depsgraph handler and the timer). Only finished byte frames are handed to the network threads, so the add-on never accesses Blender data from another thread.

If a device falls behind (slow Wi-Fi with a big scene), its backlog is dropped once it exceeds 64 MB and it receives a fresh full sync instead, so it never shows a half-updated scene.

---

## 4. Wire protocol (version 1)

### 4.1 Framing

Every message is one frame:

| Bytes | Type | Meaning |
|---|---|---|
| 0..3 | `uint32` big-endian | Payload length in bytes (not counting these 5 header bytes) |
| 4 | `uint8` | Kind: `1` = JSON control message, `2` = binary mesh |
| 5.. | bytes | Payload |

Direction is Blender to app only. The app never sends data in v1.

### 4.2 JSON control messages (kind 1)

UTF-8 JSON object with a `type` field.

| type | Fields | Purpose |
|---|---|---|
| `hello` | `version` (int), `scene` (string), `unitScale` (float) | First message after connecting. `unitScale` is Blender's Unit Scale (Blender units to meters). |
| `clear` | none | Remove everything. Always follows `hello`. |
| `transforms` | `objects`: list of `{ "id": string, "m": [16 floats] }` | World matrices in RealityKit space, column-major. Only changed objects are sent. |
| `remove` | `ids`: list of strings | Objects deleted or hidden in Blender. |
| `ping` | none | Sent every 2 s. Keeps the connection alive and detects dead clients. |

Unknown `type` values must be ignored, so newer add-ons stay compatible with older apps.

### 4.3 Mesh payload (kind 2)

All numbers little-endian.

| Section | Size | Notes |
|---|---|---|
| Header length | `uint32` | Length of the JSON header that follows |
| Header JSON | variable | `{ id, vertexCount, indexCount, hasUVs, materials: [ {name, color[4], metallic, roughness} ] }` |
| Positions | `vertexCount * 3 * float32` | RealityKit space, object local |
| Normals | `vertexCount * 3 * float32` | Per corner (split normals), so hard edges look right |
| UVs | `vertexCount * 2 * float32` | Only present if `hasUVs` is true |
| Indices | `indexCount * uint32` | Triangle list |
| Face materials | `indexCount / 3 * uint32` | Index into `materials` for each triangle |

An `indexCount` of 0 means "this object currently has no visible geometry" (or it was skipped for exceeding the triangle budget). The app removes its model but keeps the entity.

Colors are sent already converted from Blender's linear space to sRGB.

### 4.4 Coordinate conversion

Blender is Z-up, RealityKit is Y-up. Both are right-handed and both use meters.

```
Blender (x, y, z)  ->  RealityKit (x, z, -y)

C = | 1  0  0  0 |
    | 0  0  1  0 |
    | 0 -1  0  0 |
    | 0  0  0  1 |

vertex_rk  = C * vertex_blender
matrix_rk  = C * matrix_world_blender * C^-1
```

Because vertices and matrices are converted with the same `C`, the app never has to know about Blender's axes. A useful side effect: Blender's Front view looks along +Y, so the "front" of a model faces -Y in Blender, which becomes +Z in RealityKit. The app rotates the scene so +Z points at the camera when you place it, meaning you see the front view first.

### 4.5 Object identity

Objects are identified by `Object.name_full` (unique even with linked libraries). Renaming an object is treated as a delete plus an add. Each object is sent flat with its world matrix, so parenting, constraints and drivers all work without the app knowing the hierarchy.

---

## 5. Source files

### 5.1 `blender_manifest.toml`

```toml
schema_version = "1.0.0"

id = "viewar"
version = "1.0.0"
name = "ViewAR"
tagline = "Live preview your scene in AR on iPhone and iPad"
maintainer = "Your Name <you@example.com>"
type = "add-on"

website = "https://github.com/your-account/viewar"
tags = ["3D View", "Import-Export"]

blender_version_min = "4.2.0"

license = ["SPDX:GPL-3.0-or-later"]

# Optional: enables automatic discovery in the app. Without these wheels
# the add-on still works and users type the IP address instead.
wheels = [
  "./wheels/zeroconf-REPLACE-py3-none-any.whl",
  "./wheels/ifaddr-REPLACE-py3-none-any.whl",
]

[permissions]
network = "Streams the scene to the ViewAR app on your local network"
```

Check the Blender Extensions platform guidelines on naming before publishing there. The add-on name above is just "ViewAR" to stay on the safe side.

### 5.2 `server.py`

```python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal threaded TCP server with length-prefixed frames."""

import queue
import socket
import struct
import threading

KIND_JSON = 1
KIND_MESH = 2

# If a phone falls this far behind, drop its backlog and resync it.
MAX_PENDING_BYTES = 64 * 1024 * 1024
SEND_TIMEOUT = 15.0

def encode_frame(kind, payload):
    return struct.pack(">IB", len(payload), kind) + payload

class Client:
    def __init__(self, conn, address):
        self._conn = conn
        self.address = address
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self.alive = True
        self.needs_full_sync = True
        threading.Thread(
            target=self._send_loop,
            name=f"ViewAR client {address[0]}",
            daemon=True,
        ).start()

    @property
    def label(self):
        return f"{self.address[0]}:{self.address[1]}"

    def send(self, data, force=False):
        if not self.alive:
            return
        with self._lock:
            overloaded = not force and self._pending > MAX_PENDING_BYTES
            if not overloaded:
                self._pending += len(data)
        if overloaded:
            self._drain()
            self.needs_full_sync = True
            return
        self._queue.put(data)

    def _drain(self):
        while True:
            try:
                data = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._pending -= len(data)

    def _send_loop(self):
        try:
            while self.alive:
                try:
                    data = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                self._conn.sendall(data)
                with self._lock:
                    self._pending -= len(data)
        except OSError:
            pass
        finally:
            self.close()

    def close(self):
        self.alive = False
        try:
            self._conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._conn.close()
        except OSError:
            pass

class Server:
    def __init__(self, port):
        self.port = port
        self._clients = []
        self._lock = threading.Lock()
        self._sock = None
        self._thread = None
        self._running = False

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop, name="ViewAR accept", daemon=True
        )
        self._thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                conn, address = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(SEND_TIMEOUT)
            with self._lock:
                self._clients.append(Client(conn, address))

    def live_clients(self):
        with self._lock:
            self._clients = [c for c in self._clients if c.alive]
            return list(self._clients)

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            for client in self._clients:
                client.close()
            self._clients.clear()
```

### 5.3 `discovery.py`

```python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Bonjour (mDNS) advertising so the app finds Blender automatically."""

import socket
import threading

try:
    from zeroconf import ServiceInfo, Zeroconf

    AVAILABLE = True
except Exception:  # wheel not bundled or failed to load
    AVAILABLE = False

SERVICE_TYPE = "_viewar._tcp.local."

def local_ip():
    """Best guess at this machine's LAN address. Sends no packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()

class Advertiser:
    def __init__(self):
        self._lock = threading.Lock()
        self._zeroconf = None
        self._info = None
        self._stopped = False
        self.active = False
        self.error = None

    def start(self, port):
        if not AVAILABLE:
            self.error = "zeroconf is not bundled"
            return
        # Registration blocks for about a second, so keep it off the UI thread.
        threading.Thread(target=self._register, args=(port,), daemon=True).start()

    def _register(self, port):
        try:
            host = socket.gethostname().split(".")[0] or "blender"
            info = ServiceInfo(
                SERVICE_TYPE,
                f"ViewAR on {host}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip())],
                port=port,
                properties={"v": "1"},
                server=f"{host}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
        except Exception as exc:
            self.error = str(exc)
            return
        with self._lock:
            if self._stopped:
                _close(zc, info)
                return
            self._zeroconf, self._info = zc, info
            self.active = True

    def stop(self):
        with self._lock:
            self._stopped = True
            zc, info = self._zeroconf, self._info
            self._zeroconf = self._info = None
            self.active = False
        if zc is not None:
            threading.Thread(target=_close, args=(zc, info), daemon=True).start()

def _close(zc, info):
    try:
        zc.unregister_service(info)
    except Exception:
        pass
    finally:
        zc.close()
```

### 5.4 `scene_stream.py`

```python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tracks scene changes and turns them into ViewAR protocol frames."""

import json
import struct
import time

import bpy
import numpy as np
from mathutils import Matrix

from .server import KIND_JSON, KIND_MESH, encode_frame

PROTOCOL_VERSION = 1
SUPPORTED_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
GEOMETRY_MIN_INTERVAL = 0.1  # max 10 mesh resends per second per object
PING_INTERVAL = 2.0

# Blender (x, y, z) -> RealityKit (x, z, -y). See protocol section 4.4.
AXIS = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
AXIS_INV = AXIS.inverted()

DEFAULT_MATERIAL = {
    "name": "Default",
    "color": [0.8, 0.8, 0.8, 1.0],
    "metallic": 0.0,
    "roughness": 0.5,
}

# ---------------------------------------------------------------- helpers

def _json_frame(message):
    data = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return encode_frame(KIND_JSON, data)

def _to_srgb(c):
    c = min(max(float(c), 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055

def _rk_matrix(obj):
    m = AXIS @ obj.matrix_world @ AXIS_INV
    return [round(m[row][col], 6) for col in range(4) for row in range(4)]

def _to_realitykit(points):
    out = points[:, [0, 2, 1]]
    out[:, 2] *= -1.0
    return out

def _socket_value(node, name, fallback):
    sock = node.inputs.get(name)
    if sock is None or sock.is_linked:
        return fallback
    return sock.default_value

def material_info(mat):
    if mat is None:
        return dict(DEFAULT_MATERIAL)
    color = list(mat.diffuse_color)
    metallic = float(mat.metallic)
    roughness = float(mat.roughness)
    tree = mat.node_tree
    if tree is not None and getattr(mat, "use_nodes", True):
        principled = next(
            (n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None
        )
        if principled is not None:
            color = list(_socket_value(principled, "Base Color", color))
            metallic = float(_socket_value(principled, "Metallic", metallic))
            roughness = float(_socket_value(principled, "Roughness", roughness))
            color[3] = float(_socket_value(principled, "Alpha", color[3]))
    return {
        "name": mat.name,
        "color": [_to_srgb(c) for c in color[:3]] + [min(max(color[3], 0.0), 1.0)],
        "metallic": metallic,
        "roughness": roughness,
    }

def build_mesh_frame(obj, depsgraph, max_triangles):
    """Return (frame_bytes, triangle_count, skipped)."""
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        n_tris = 0
        if mesh is not None:
            if hasattr(mesh, "calc_loop_triangles"):
                mesh.calc_loop_triangles()
            n_tris = len(mesh.loop_triangles)
        skipped = n_tris > max_triangles

        materials = [material_info(s.material) for s in obj.material_slots]
        materials = materials or [dict(DEFAULT_MATERIAL)]
        header = {
            "id": obj.name_full,
            "vertexCount": 0,
            "indexCount": 0,
            "hasUVs": False,
            "materials": materials,
        }
        blobs = []

        if n_tris and not skipped:
            n_loops = len(mesh.loops)

            vertex_index = np.empty(n_loops, dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", vertex_index)
            coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", coords)
            positions = _to_realitykit(coords.reshape(-1, 3)[vertex_index])

            normals = np.empty(n_loops * 3, dtype=np.float32)
            mesh.corner_normals.foreach_get("vector", normals)
            normals = _to_realitykit(normals.reshape(-1, 3))

            blobs.append(positions.astype("<f4").tobytes())
            blobs.append(normals.astype("<f4").tobytes())

            uv_layer = mesh.uv_layers.active
            if uv_layer is not None:
                uvs = np.empty(n_loops * 2, dtype=np.float32)
                uv_layer.data.foreach_get("uv", uvs)
                blobs.append(uvs.astype("<f4").tobytes())
                header["hasUVs"] = True

            indices = np.empty(n_tris * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("loops", indices)
            face_materials = np.empty(n_tris, dtype=np.int32)
            mesh.loop_triangles.foreach_get("material_index", face_materials)
            np.clip(face_materials, 0, len(materials) - 1, out=face_materials)

            blobs.append(indices.astype("<u4").tobytes())
            blobs.append(face_materials.astype("<u4").tobytes())

            header["vertexCount"] = n_loops
            header["indexCount"] = n_tris * 3
    finally:
        obj_eval.to_mesh_clear()

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload = b"".join([struct.pack("<I", len(header_bytes)), header_bytes, *blobs])
    return encode_frame(KIND_MESH, payload), n_tris, skipped

# ---------------------------------------------------------------- streamer

class SceneStreamer:
    def __init__(self, server, max_triangles):
        self.server = server
        self._max_triangles = max_triangles  # callable, read live from settings
        self._dirty = set()
        self._known = set()
        self._sent_matrices = {}
        self._last_geometry = {}
        self._triangles = {}
        self._last_ping = 0.0
        self.skipped = set()
        self.client_labels = []

    # stats for the UI
    @property
    def object_count(self):
        return len(self._known)

    @property
    def triangle_count(self):
        return sum(self._triangles.values())

    def request_full_sync(self):
        for client in self.server.live_clients():
            client.needs_full_sync = True

    # called from depsgraph_update_post (main thread)
    def on_depsgraph_update(self, scene, depsgraph):
        materials, datas = set(), set()
        for update in depsgraph.updates:
            block = getattr(update.id, "original", update.id)
            if isinstance(block, bpy.types.Object):
                if update.is_updated_geometry:
                    self._dirty.add(block.name_full)
            elif isinstance(block, bpy.types.Material):
                materials.add(block.name_full)
            elif update.is_updated_geometry and isinstance(
                block, (bpy.types.Mesh, bpy.types.Curve, bpy.types.MetaBall)
            ):
                datas.add(block.name_full)

        if not (materials or datas):
            return
        for obj in scene.objects:
            if obj.type not in SUPPORTED_TYPES:
                continue
            if obj.data is not None and obj.data.name_full in datas:
                self._dirty.add(obj.name_full)
            elif materials and any(
                s.material is not None and s.material.name_full in materials
                for s in obj.material_slots
            ):
                self._dirty.add(obj.name_full)

    # called from bpy.app.timers (main thread)
    def tick(self):
        now = time.monotonic()
        clients = self.server.live_clients()
        self.client_labels = [c.label for c in clients]
        if not clients:
            return

        context = bpy.context
        scene = context.scene
        view_layer = context.view_layer or scene.view_layers[0]
        depsgraph = context.evaluated_depsgraph_get()
        max_tris = self._max_triangles()

        current = {
            o.name_full: o
            for o in scene.objects
            if o.type in SUPPORTED_TYPES and o.visible_get(view_layer=view_layer)
        }
        existing = [c for c in clients if not c.needs_full_sync]
        fresh = [c for c in clients if c.needs_full_sync]
        frames = []

        removed = self._known - current.keys()
        added = current.keys() - self._known
        self._known = set(current)
        for name in removed:
            self._forget(name)
        self._dirty |= added
        if removed:
            frames.append(_json_frame({"type": "remove", "ids": sorted(removed)}))

        for name in list(self._dirty):
            obj = current.get(name)
            if obj is None:
                self._dirty.discard(name)
                continue
            if now - self._last_geometry.get(name, 0.0) < GEOMETRY_MIN_INTERVAL:
                continue  # rate limited, try again next tick
            self._dirty.discard(name)
            self._last_geometry[name] = now
            if existing:  # fresh clients get everything in their full sync
                frames.append(self._mesh_frame(obj, depsgraph, max_tris))

        changed = []
        for name, obj in current.items():
            matrix = _rk_matrix(obj)
            if self._sent_matrices.get(name) != matrix:
                self._sent_matrices[name] = matrix
                changed.append({"id": name, "m": matrix})
        if changed:
            frames.append(_json_frame({"type": "transforms", "objects": changed}))

        if now - self._last_ping > PING_INTERVAL:
            self._last_ping = now
            frames.append(_json_frame({"type": "ping"}))

        for client in existing:
            for frame in frames:
                client.send(frame)
        for client in fresh:
            self._full_sync(client, scene, depsgraph, current, max_tris)

    # internals
    def _mesh_frame(self, obj, depsgraph, max_tris):
        frame, tris, skipped = build_mesh_frame(obj, depsgraph, max_tris)
        name = obj.name_full
        self._triangles[name] = 0 if skipped else tris
        if skipped:
            self.skipped.add(name)
        else:
            self.skipped.discard(name)
        return frame

    def _full_sync(self, client, scene, depsgraph, current, max_tris):
        client.needs_full_sync = False
        client.send(
            _json_frame(
                {
                    "type": "hello",
                    "version": PROTOCOL_VERSION,
                    "scene": scene.name,
                    "unitScale": scene.unit_settings.scale_length,
                }
            ),
            force=True,
        )
        client.send(_json_frame({"type": "clear"}), force=True)
        for obj in current.values():
            client.send(self._mesh_frame(obj, depsgraph, max_tris), force=True)
        transforms = [{"id": n, "m": _rk_matrix(o)} for n, o in current.items()]
        client.send(_json_frame({"type": "transforms", "objects": transforms}), force=True)

    def _forget(self, name):
        self._dirty.discard(name)
        self._sent_matrices.pop(name, None)
        self._last_geometry.pop(name, None)
        self._triangles.pop(name, None)
        self.skipped.discard(name)
```

### 5.5 `__init__.py`

```python
# SPDX-License-Identifier: GPL-3.0-or-later
import traceback

import bpy
from bpy.app.handlers import persistent

from . import discovery, scene_stream, server

HEAVY_SCENE_TRIANGLES = 1_000_000

_state = {
    "server": None,
    "streamer": None,
    "advertiser": None,
    "ip": "",
    "last_ui": None,
}

# ---------------------------------------------------------------- handlers

@persistent
def _on_depsgraph_update(scene, depsgraph):
    streamer = _state["streamer"]
    if streamer is not None:
        streamer.on_depsgraph_update(scene, depsgraph)

@persistent
def _on_load_post(*_args):
    streamer = _state["streamer"]
    if streamer is not None:
        streamer.request_full_sync()

def _tick():
    streamer = _state["streamer"]
    if streamer is None:
        return None  # unregisters the timer
    try:
        streamer.tick()
    except Exception:
        traceback.print_exc()
    _redraw_if_changed(streamer)
    return 1.0 / max(bpy.context.window_manager.viewar.update_rate, 1)

def _redraw_if_changed(streamer):
    snapshot = (
        tuple(streamer.client_labels),
        streamer.object_count,
        streamer.triangle_count,
        len(streamer.skipped),
    )
    if snapshot == _state["last_ui"]:
        return
    _state["last_ui"] = snapshot
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

# ---------------------------------------------------------------- lifecycle

def start(context):
    settings = context.window_manager.viewar
    srv = server.Server(settings.port)
    srv.start()  # raises OSError if the port is taken
    _state["server"] = srv
    _state["ip"] = discovery.local_ip()
    _state["streamer"] = scene_stream.SceneStreamer(
        srv, max_triangles=lambda: bpy.context.window_manager.viewar.max_triangles
    )
    advertiser = discovery.Advertiser()
    advertiser.start(settings.port)
    _state["advertiser"] = advertiser

    handlers = bpy.app.handlers
    if _on_depsgraph_update not in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in handlers.load_post:
        handlers.load_post.append(_on_load_post)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1, persistent=True)

def stop():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    handlers = bpy.app.handlers
    if _on_depsgraph_update in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_load_post in handlers.load_post:
        handlers.load_post.remove(_on_load_post)
    if _state["advertiser"] is not None:
        _state["advertiser"].stop()
    if _state["server"] is not None:
        _state["server"].stop()
    _state.update(server=None, streamer=None, advertiser=None, ip="", last_ui=None)

# ---------------------------------------------------------------- UI

class ViewARSettings(bpy.types.PropertyGroup):
    port: bpy.props.IntProperty(
        name="Port", default=51515, min=1024, max=65535
    )
    update_rate: bpy.props.IntProperty(
        name="Updates per Second", default=30, min=5, max=60
    )
    max_triangles: bpy.props.IntProperty(
        name="Max Triangles per Object",
        default=300_000,
        min=1_000,
        max=5_000_000,
        description="Objects above this are not sent, to keep older iPhones smooth",
    )

class VIEWAR_OT_start(bpy.types.Operator):
    bl_idname = "viewar.start"
    bl_label = "Start Server"
    bl_description = "Stream this scene to the ViewAR app on your local network"

    def execute(self, context):
        if _state["server"] is not None:
            return {"CANCELLED"}
        try:
            start(context)
        except OSError as exc:
            stop()
            self.report({"ERROR"}, f"Could not open port: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}

class VIEWAR_OT_stop(bpy.types.Operator):
    bl_idname = "viewar.stop"
    bl_label = "Stop"
    bl_description = "Stop streaming and disconnect all devices"

    def execute(self, context):
        stop()
        return {"FINISHED"}

class VIEWAR_OT_resync(bpy.types.Operator):
    bl_idname = "viewar.resync"
    bl_label = "Resync"
    bl_description = "Send the whole scene to all connected devices again"

    def execute(self, context):
        if _state["streamer"] is not None:
            _state["streamer"].request_full_sync()
        return {"FINISHED"}

class VIEWAR_PT_main(bpy.types.Panel):
    bl_idname = "VIEWAR_PT_main"
    bl_label = "ViewAR"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ViewAR"

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.viewar
        srv = _state["server"]

        if srv is None:
            col = layout.column(align=True)
            col.prop(settings, "port")
            col.prop(settings, "update_rate")
            col.prop(settings, "max_triangles")
            layout.operator(VIEWAR_OT_start.bl_idname, icon="PLAY")
            if not discovery.AVAILABLE:
                layout.label(text="Auto discovery unavailable.", icon="INFO")
                layout.label(text="Enter this computer's IP in the app.")
            return

        streamer = _state["streamer"]
        advertiser = _state["advertiser"]

        box = layout.box()
        box.label(text=f"{_state['ip']}:{srv.port}", icon="URL")
        if advertiser is not None and advertiser.active:
            box.label(text="Visible to nearby devices", icon="CHECKMARK")
        else:
            box.label(text="Use manual IP in the app", icon="INFO")

        clients = streamer.client_labels
        box = layout.box()
        box.label(
            text=f"Devices: {len(clients)}",
            icon="LINKED" if clients else "UNLINKED",
        )
        for label in clients:
            box.label(text=label)

        if clients:
            box = layout.box()
            box.label(text=f"Objects: {streamer.object_count}")
            box.label(text=f"Triangles: {streamer.triangle_count:,}")
            if streamer.triangle_count > HEAVY_SCENE_TRIANGLES:
                box.label(text="Heavy for older iPhones", icon="ERROR")
            for name in sorted(streamer.skipped)[:5]:
                box.label(text=f"Skipped: {name}", icon="ERROR")

        row = layout.row(align=True)
        row.operator(VIEWAR_OT_resync.bl_idname, icon="FILE_REFRESH")
        row.operator(VIEWAR_OT_stop.bl_idname, icon="CANCEL")

classes = (
    ViewARSettings,
    VIEWAR_OT_start,
    VIEWAR_OT_stop,
    VIEWAR_OT_resync,
    VIEWAR_PT_main,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.viewar = bpy.props.PointerProperty(type=ViewARSettings)

def unregister():
    stop()
    del bpy.types.WindowManager.viewar
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
```

## 6. Bundling the discovery wheels

Check which Python your Blender uses (Scripting workspace, Python console: `import sys; sys.version`), then download wheels for that version:

```bash
cd blender/viewar
mkdir -p wheels
pip download zeroconf ifaddr --no-deps --only-binary=:all: \
    --python-version 3.11 -d ./wheels
```

Replace `3.11` with your Blender's Python version. If PyPI offers a `py3-none-any` wheel, that single file works on every OS. If only platform-specific wheels exist, download one per platform (add `--platform win_amd64`, `--platform macosx_11_0_arm64`, `--platform manylinux_2_28_x86_64`, and so on), list all of them in the manifest, and Blender installs the matching one. Update the file names in `blender_manifest.toml`.

If you skip this step entirely, everything still works. Users just type the IP shown in the panel.

## 7. Build and install

```bash
blender --command extension validate --source-dir ./blender/viewar
blender --command extension build --source-dir ./blender/viewar --output-dir ./dist
```

Install the resulting zip via **Edit > Preferences > Get Extensions > (dropdown) Install from Disk**. The panel appears in the 3D Viewport sidebar (press **N**) under the **ViewAR** tab.

## 8. Platform notes

**Windows.** The first time you press Start Server, Windows Defender Firewall asks whether to allow Blender. Allow it on **Private networks**. If your Wi-Fi is set to "Public" in Windows settings, inbound connections are blocked, so switch it to "Private". The zeroconf library implements mDNS itself, so Apple's Bonjour service does not need to be installed on Windows.

**macOS.** Recent macOS versions may ask whether Blender can find devices on your local network. Allow it, otherwise discovery and connections fail silently. You can change this later in System Settings > Privacy & Security > Local Network.

**Linux.** If you run a firewall such as `ufw`, open TCP 51515 and UDP 5353 (mDNS).

**All platforms.** Phone and computer must be on the same network. Guest networks and some office or university networks use "client isolation", which blocks device to device traffic. A phone hotspot or home router works reliably.

---

## 9. Using the add-on

1. Open the 3D Viewport sidebar with **N** and select the **ViewAR** tab.
2. Optionally adjust **Port**, **Updates per Second** and **Max Triangles per Object**. These can only be changed while the server is stopped.
3. Press **Start Server**. The panel shows this computer's IP and port, and whether it is visible to nearby devices.
4. In the ViewAR app, pick the computer under Nearby, or enter the IP and port manually.
5. The **Devices** box lists connected phones. Once a device is connected, the panel shows how many objects and triangles are being streamed, and lists objects that were skipped for exceeding the triangle budget.
6. **Resync** sends the whole scene again to every device. **Stop** disconnects everyone and closes the port.

Settings live on the window manager, so they are not saved into your .blend files and do not affect files you share with others.

**Tips for smooth results on non-Pro iPhones**

1. Keep the whole scene under about 1 million triangles. The panel warns above that.
2. Lower the viewport level of Subdivision Surface modifiers while previewing. ViewAR streams the evaluated mesh, which uses viewport settings.
3. Hide objects you don't need in AR. Hidden objects are removed from the stream immediately.
4. Lower **Updates per Second** to 15 or 20 for heavy animated scenes on older phones.

---

## 10. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "Could not open port" when starting | Another program (or a second Blender) uses the port. Pick a different port in the panel and enter it in the app. |
| Panel says "Use manual IP in the app" | The zeroconf wheels are missing or failed to load, or the hostname contains unusual characters. Manual IP still works. Check the Blender system console for the error. |
| App never finds the computer | Firewall or network issue. See section 8. Try manual IP first to tell discovery problems apart from connection problems. |
| App connects, but nothing appears | Tap a surface in the app to place the scene. If still empty, check that objects are visible in the current view layer and press Resync. |
| Object appears in Blender but not in AR | It is over the triangle budget (listed as "Skipped"), is not a supported type, or comes from a collection instance. |
| Wrong colors | Only Principled BSDF values are read. If Base Color is driven by a texture or node, the add-on falls back to the material's viewport display color. |
| Scene is huge or tiny in AR | Check Scene Properties > Units > Unit Scale, then use the app's scale menu. One Blender unit is one meter at Unit Scale 1. |
| Updates stutter during heavy edits | Lower the triangle count of the object being edited, or reduce Updates per Second. |

Errors inside the timer are printed with a full traceback to the system console (**Window > Toggle System Console** on Windows, or launch Blender from a terminal on macOS and Linux).

---

## 11. Testing checklist

**Connection**
1. Start Server, the computer appears in the app under Nearby within a few seconds.
2. Manual IP works with the discovery wheels removed.
3. Stopping the server shows "Blender closed the connection" in the app.
4. Stop then Start again, reconnect, the scene is clean with no duplicates.
5. Two phones connected at once both receive updates, and a phone joining late gets the full scene.
6. Disabling the add-on while running closes the port (Start works again after re-enabling).

**Live updates**
1. Moving, rotating, scaling an object (G, R, S) updates smoothly in AR.
2. Adding a modifier (Subdivision, Bevel) updates the mesh.
3. Changing Base Color, Metallic, Roughness on a Principled BSDF updates the material. Also try editing via the Shader Editor, since some node edits may report as the node tree instead of the material.
4. Deleting or hiding an object removes it. Unhiding brings it back.
5. Renaming an object replaces it cleanly (old name removed, new name added).
6. Animation playback (Space) shows animated transforms and armature deformation.
7. Edit Mode changes: confirm they stream live on your Blender version. Blender's evaluated mesh normally reflects Edit Mode edits.
8. Opening a different .blend file triggers a full resync.
9. Changing Unit Scale in Scene Properties changes the AR size after Resync.
10. An object above the triangle budget shows "Skipped" in the panel and disappears in the app.

**Platforms**
1. Windows with the firewall prompt accepted on a Private network.
2. macOS with Local Network permission accepted.
3. Linux with TCP 51515 and UDP 5353 open.
4. At least one older and one current Blender version (4.2 LTS and latest).

---

## 12. Licensing and publishing

The add-on uses Blender's Python API (`bpy`), so it is licensed **GPL-3.0-or-later**. Every Python file starts with an SPDX header, and the manifest declares the license. Keep all add-on code in this folder and never copy it into the iOS app, which stays separate and can use any license.

To publish on the Blender Extensions platform:

1. Run `blender --command extension validate --source-dir ./blender/viewar` and fix any warnings.
2. Read the platform's guidelines on naming, tags, and network access. The manifest already declares the `network` permission with a reason.
3. The server only starts when the user presses Start Server, and only listens on the local network. It never contacts the internet.
4. Bump `version` in the manifest for every release, and bump `PROTOCOL_VERSION` in `scene_stream.py` only when the wire format changes (the app warns users when versions differ).

---

## 13. Add-on roadmap

1. **Textures.** Save base color, normal and roughness images to PNG in a temp folder and send them as blobs per material.
2. **Instancing.** Iterate `depsgraph.object_instances` to support collection instances and Geometry Nodes instances, sending each unique mesh once plus instance matrices.
3. **Pairing code.** Show a 4 digit code in the panel, require the app to send it after connecting, drop clients that don't.
4. **USDZ snapshot.** A "Send Snapshot" button using Blender's USD exporter for full material fidelity when live updates aren't needed.
5. **Delta meshes.** When topology is unchanged (sculpting, shape keys), send only vertex positions instead of the whole mesh.
6. **Node tree updates.** Map shader node tree updates back to their owning material for more reliable material refreshes.
