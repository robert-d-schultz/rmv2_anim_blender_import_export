"""Blender import/export add-on for Creative Assembly's Total War
.rigid_model_v2 (RMV2) mesh format and .anim skeleton/animation format.

Supports RMV2 versions 5-8 on import (Rome 2 through Warhammer 3 era) and
6-8 on export, with LODs, bone weights, custom normals, tangents, two UV
channels, vertex colours, textures/materials, attachment points and
round-trip preservation of the format's more obscure fields.

.anim versions 5-8 import (skeletons and animations, armature creation,
actions) and 5-7 export; meshes and skeletons can be imported in either
order and are attached/renamed to match.
"""

bl_info = {
    "name": "Total War RigidModel (.rigid_model_v2, .anim)",
    "author": "rob + Claude",
    "version": (1, 3, 0),
    "blender": (3, 6, 0),
    "location": "File > Import-Export",
    "description": "Import-Export Total War RigidModel v2 meshes "
                   "(LODs, skinning, materials) and .anim animations",
    "category": "Import-Export",
    "doc_url": "",
    "tracker_url": "",
}

# Support add-on reload during development (F3 > Reload Scripts).
if "bpy" in locals():
    import importlib
    for _mod_name in ("rmv2_format", "anim_format", "utils", "skeleton",
                      "properties", "materials", "import_rmv2",
                      "export_rmv2", "import_anim", "export_anim", "ui",
                      "io_ops"):
        if _mod_name in locals():
            importlib.reload(locals()[_mod_name])

import bpy  # noqa: E402,F401

from . import anim_format  # noqa: E402,F401
from . import export_anim  # noqa: E402,F401
from . import export_rmv2  # noqa: E402,F401
from . import import_anim  # noqa: E402,F401
from . import import_rmv2  # noqa: E402,F401
from . import io_ops  # noqa: E402
from . import materials  # noqa: E402,F401
from . import properties  # noqa: E402
from . import rmv2_format  # noqa: E402,F401
from . import skeleton  # noqa: E402,F401
from . import ui  # noqa: E402
from . import utils  # noqa: E402,F401


def register():
    properties.register()
    ui.register()
    io_ops.register()


def unregister():
    io_ops.unregister()
    ui.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
