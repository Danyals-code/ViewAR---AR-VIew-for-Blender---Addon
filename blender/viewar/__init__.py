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
