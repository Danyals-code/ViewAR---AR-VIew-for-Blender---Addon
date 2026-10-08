# SPDX-License-Identifier: GPL-3.0-or-later
"""Tracks scene changes and turns them into ViewAR protocol frames."""

import json
import struct
import time
from dataclasses import dataclass, field

import bpy
import numpy as np
from mathutils import Matrix

from .server import KIND_JSON, KIND_MESH, encode_frame

PROTOCOL_VERSION = 1
SUPPORTED_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
GEOMETRY_MIN_INTERVAL = 0.1  # max 10 mesh resends per second per object
PING_INTERVAL = 2.0

# Depsgraph pads unused levels of ObjectInstance.persistent_id with this.
UNUSED_PERSISTENT_ID = 2**31 - 1

# Blender (x, y, z) -> RealityKit (x, z, -y). See protocol section 4.4.
AXIS = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
AXIS_INV = AXIS.inverted()

DEFAULT_MATERIAL = {
    "name": "Default",
    "color": [0.8, 0.8, 0.8, 1.0],
    "metallic": 0.0,
    "roughness": 0.5,
}

# Blender 5.0 deprecated Material.use_nodes: node materials are always used.
_HAS_USE_NODES = bpy.app.version < (5, 0, 0)


@dataclass
class Options:
    """What to stream and how. Read from the add-on settings every tick."""

    max_triangles: int = 300_000
    include_instances: bool = True
    max_instances: int = 500
    scope: str = "VISIBLE"  # VISIBLE, SELECTED or COLLECTION
    collection: str = ""
    origin: str = "WORLD"  # WORLD or CURSOR
    paused: bool = False


@dataclass
class Geometry:
    materials: list
    triangles: int = 0
    skipped: bool = False
    vertex_count: int = 0
    index_count: int = 0
    has_uvs: bool = False
    blobs: list = field(default_factory=list)


@dataclass
class _Instance:
    matrix: list
    frame: bytes = None  # mesh frame, when one was built this tick
    changed: bool = False  # True if existing devices need the frame


# ---------------------------------------------------------------- helpers


def _json_frame(message):
    data = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return encode_frame(KIND_JSON, data)


def _to_srgb(c):
    c = min(max(float(c), 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _flatten(m):
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


def _persistent_id(instance):
    pid = tuple(instance.persistent_id)
    if UNUSED_PERSISTENT_ID in pid:
        pid = pid[: pid.index(UNUSED_PERSISTENT_ID)]
    return pid


def material_info(mat):
    if mat is None:
        return dict(DEFAULT_MATERIAL)
    color = list(mat.diffuse_color)
    metallic = float(mat.metallic)
    roughness = float(mat.roughness)
    tree = mat.node_tree
    if tree is not None and (mat.use_nodes if _HAS_USE_NODES else True):
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


def read_geometry(obj_eval, max_triangles):
    """Triangulate an evaluated object (or instance) into protocol blobs."""
    materials = [material_info(s.material) for s in obj_eval.material_slots]
    geometry = Geometry(materials=materials or [dict(DEFAULT_MATERIAL)])
    mesh = obj_eval.to_mesh()
    try:
        if mesh is None:
            return geometry
        if hasattr(mesh, "calc_loop_triangles"):
            mesh.calc_loop_triangles()
        n_tris = len(mesh.loop_triangles)
        geometry.triangles = n_tris
        geometry.skipped = n_tris > max_triangles
        if not n_tris or geometry.skipped:
            return geometry

        n_loops = len(mesh.loops)
        vertex_index = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", vertex_index)
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        positions = _to_realitykit(coords.reshape(-1, 3)[vertex_index])

        normals = np.empty(n_loops * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        normals = _to_realitykit(normals.reshape(-1, 3))

        blobs = geometry.blobs
        blobs.append(positions.astype("<f4").tobytes())
        blobs.append(normals.astype("<f4").tobytes())

        uv_layer = mesh.uv_layers.active
        if uv_layer is not None:
            uvs = np.empty(n_loops * 2, dtype=np.float32)
            uv_layer.data.foreach_get("uv", uvs)
            blobs.append(uvs.astype("<f4").tobytes())
            geometry.has_uvs = True

        indices = np.empty(n_tris * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", indices)
        face_materials = np.empty(n_tris, dtype=np.int32)
        mesh.loop_triangles.foreach_get("material_index", face_materials)
        np.clip(face_materials, 0, len(geometry.materials) - 1, out=face_materials)

        blobs.append(indices.astype("<u4").tobytes())
        blobs.append(face_materials.astype("<u4").tobytes())

        geometry.vertex_count = n_loops
        geometry.index_count = n_tris * 3
        return geometry
    finally:
        obj_eval.to_mesh_clear()


def encode_mesh(object_id, geometry):
    header = {
        "id": object_id,
        "vertexCount": geometry.vertex_count,
        "indexCount": geometry.index_count,
        "hasUVs": geometry.has_uvs,
        "materials": geometry.materials,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload = b"".join(
        [struct.pack("<I", len(header_bytes)), header_bytes, *geometry.blobs]
    )
    return encode_frame(KIND_MESH, payload)


def _scope_filter(view_layer, opts):
    """Return a predicate for objects (or instancers) the user wants in AR."""
    if opts.scope == "SELECTED":

        def in_scope(obj):
            return obj.select_get(view_layer=view_layer)

    elif opts.scope == "COLLECTION":
        coll = bpy.data.collections.get(opts.collection)
        names = {o.name_full for o in coll.all_objects} if coll else set()

        def in_scope(obj):
            return obj.name_full in names

    else:

        def in_scope(obj):
            return True

    return lambda obj: getattr(obj, "viewar_visible", True) and in_scope(obj)


# ---------------------------------------------------------------- streamer


class SceneStreamer:
    def __init__(self, server, options):
        self.server = server
        self._options = options  # callable returning Options, read every tick
        self._dirty = set()
        self._known = set()
        self._sent_matrices = {}
        self._world_cache = {}  # id -> (world matrix, converted matrix)
        self._space = None
        self._last_geometry = {}
        self._triangles = {}
        self._last_ping = 0.0
        self._budget = None
        self._sync_key = None
        self.skipped = set()
        self.clients = []
        self.object_count = 0
        self.instance_count = 0
        self.instances_capped = False

    # stats for the UI
    @property
    def client_labels(self):
        return [c.label for c in self.clients]

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
            elif isinstance(block, bpy.types.ShaderNodeTree):
                # Shader Editor edits can report the embedded node tree
                # instead of the material that owns it.
                for mat in bpy.data.materials:
                    if mat.node_tree is not None and mat.node_tree == block:
                        materials.add(mat.name_full)
            elif update.is_updated_geometry and isinstance(
                block, (bpy.types.Mesh, bpy.types.Curve, bpy.types.MetaBall)
            ):
                datas.add(block.name_full)

        if not (materials or datas):
            return
        # All objects, not just the scene's: instanced collections often
        # live outside the scene.
        for obj in bpy.data.objects:
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
        self.clients = clients
        if not clients:
            return

        opts = self._options()
        context = bpy.context
        scene = context.scene
        view_layer = context.view_layer or scene.view_layers[0]
        depsgraph = context.evaluated_depsgraph_get()

        # "hello" carries the scene name and unit scale, so resend it on change.
        sync_key = (scene.name_full, scene.unit_settings.scale_length)
        if self._sync_key is not None and sync_key != self._sync_key:
            self.request_full_sync()
        self._sync_key = sync_key
        # Objects skipped under the old triangle budget may fit now, and vice versa.
        if self._budget is not None and opts.max_triangles != self._budget:
            self._dirty |= self._known
        self._budget = opts.max_triangles

        existing = [c for c in clients if not c.needs_full_sync]
        fresh = [c for c in clients if c.needs_full_sync]
        frames = []
        if now - self._last_ping > PING_INTERVAL:
            self._last_ping = now
            frames.append(_json_frame({"type": "ping"}))

        if opts.paused and not fresh:
            for client in existing:
                for frame in frames:
                    client.send(frame)
            return

        space = AXIS
        if opts.origin == "CURSOR":
            space = AXIS @ scene.cursor.matrix.inverted_safe()
        if space != self._space:
            self._space = space
            self._world_cache.clear()

        # While paused, new devices still get the current scene, but state is
        # left alone so existing devices catch up on everything after resuming.
        commit = not opts.paused
        accept = _scope_filter(view_layer, opts)
        objects = {
            o.name_full: o
            for o in scene.objects
            if o.type in SUPPORTED_TYPES
            and o.visible_get(view_layer=view_layer)
            and accept(o)
        }
        instances = self._gather_instances(
            depsgraph, accept, opts, now, full=bool(fresh), commit=commit
        )
        self.object_count = len(objects)
        self.instance_count = len(instances)
        matrices = {n: self._convert(n, o.matrix_world) for n, o in objects.items()}
        matrices.update((i, entry.matrix) for i, entry in instances.items())
        built = {}

        if commit:
            current = matrices.keys()
            removed = self._known - current
            added = current - self._known
            self._known = set(current)
            for name in removed:
                self._forget(name)
            if removed:
                frames.append(_json_frame({"type": "remove", "ids": sorted(removed)}))

            self._dirty |= added & objects.keys()
            for name in list(self._dirty):
                if name in instances:
                    continue  # rate limited instance, retried by _gather_instances
                obj = objects.get(name)
                if obj is None:
                    self._dirty.discard(name)
                    continue
                if now - self._last_geometry.get(name, 0.0) < GEOMETRY_MIN_INTERVAL:
                    continue  # rate limited, try again next tick
                self._dirty.discard(name)
                self._last_geometry[name] = now
                if existing:  # fresh clients get everything in their full sync
                    built[name] = self._object_frame(name, obj, depsgraph, opts)
                    frames.append(built[name])
            frames.extend(e.frame for e in instances.values() if e.changed)

            changed = []
            for name, matrix in matrices.items():
                if self._sent_matrices.get(name) != matrix:
                    self._sent_matrices[name] = matrix
                    changed.append({"id": name, "m": matrix})
            if changed:
                frames.append(_json_frame({"type": "transforms", "objects": changed}))

        for client in existing:
            for frame in frames:
                client.send(frame)
        for client in fresh:
            self._full_sync(client, scene, depsgraph, opts, objects, instances, matrices, built)

    # internals
    def _gather_instances(self, depsgraph, accept, opts, now, full, commit):
        """Collection, particle and Geometry Nodes instances, flattened.

        Protocol v1 has no instancing, so each instance is sent as its own
        object. Meshes shared by several instances are only built once.
        """
        self.instances_capped = False
        if not opts.include_instances:
            return {}
        out = {}
        cache = {}
        for inst in depsgraph.object_instances:
            if not inst.is_instance:
                continue
            obj = inst.object
            if obj.type not in SUPPORTED_TYPES:
                continue
            parent, source = inst.parent.original, obj.original
            pid = _persistent_id(inst)
            if source == parent and len(pid) == 1:
                # Curve, text, surface and metaball objects list their own
                # evaluated mesh here too. It is already sent as the object.
                continue
            if not (accept(parent) and getattr(source, "viewar_visible", True)):
                continue
            if len(out) >= opts.max_instances:
                self.instances_capped = True
                break
            iid = f"{parent.name_full}/{source.name_full}#{'.'.join(map(str, pid))}"
            dirty = (
                iid in self._dirty
                or iid not in self._known
                or parent.name_full in self._dirty
                or source.name_full in self._dirty
            )
            due = now - self._last_geometry.get(iid, 0.0) >= GEOMETRY_MIN_INTERVAL
            entry = _Instance(self._convert(iid, inst.matrix_world))
            if full or (dirty and due):
                entry.frame = self._instance_frame(iid, obj, opts, cache)
                entry.changed = dirty
            if commit:
                if entry.changed:
                    self._dirty.discard(iid)
                    self._last_geometry[iid] = now
                elif dirty:
                    self._dirty.add(iid)
            out[iid] = entry
        return out

    def _convert(self, name, world):
        """World matrix in RealityKit space, cached because rounding 16
        floats for every object on every tick dominates idle ticks."""
        cached = self._world_cache.get(name)
        if cached is not None and cached[0] == world:
            return cached[1]
        flat = _flatten(self._space @ world @ AXIS_INV)
        self._world_cache[name] = (world.copy(), flat)
        return flat

    def _instance_frame(self, iid, obj, opts, cache):
        key = (
            obj.data.as_pointer() if obj.data is not None else 0,
            tuple(s.material.name_full if s.material else "" for s in obj.material_slots),
        )
        geometry = cache.get(key)
        if geometry is None:
            geometry = cache[key] = read_geometry(obj, opts.max_triangles)
        self._record(iid, geometry)
        return encode_mesh(iid, geometry)

    def _object_frame(self, name, obj, depsgraph, opts):
        geometry = read_geometry(obj.evaluated_get(depsgraph), opts.max_triangles)
        self._record(name, geometry)
        return encode_mesh(name, geometry)

    def _record(self, name, geometry):
        self._triangles[name] = 0 if geometry.skipped else geometry.triangles
        if geometry.skipped:
            self.skipped.add(name)
        else:
            self.skipped.discard(name)

    def _full_sync(self, client, scene, depsgraph, opts, objects, instances, matrices, built):
        client.needs_full_sync = False
        client.send(
            _json_frame(
                {
                    "type": "hello",
                    "version": PROTOCOL_VERSION,
                    "scene": scene.name,
                    "unitScale": round(scene.unit_settings.scale_length, 6),
                }
            ),
            force=True,
        )
        client.send(_json_frame({"type": "clear"}), force=True)
        for name, obj in objects.items():
            if name not in built:  # shared between devices joining together
                built[name] = self._object_frame(name, obj, depsgraph, opts)
            client.send(built[name], force=True)
        for entry in instances.values():
            client.send(entry.frame, force=True)
        transforms = [{"id": n, "m": m} for n, m in matrices.items()]
        client.send(_json_frame({"type": "transforms", "objects": transforms}), force=True)

    def _forget(self, name):
        self._dirty.discard(name)
        self._sent_matrices.pop(name, None)
        self._world_cache.pop(name, None)
        self._last_geometry.pop(name, None)
        self._triangles.pop(name, None)
        self.skipped.discard(name)
