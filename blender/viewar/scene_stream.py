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
