"""Blender import/export add-on for Creative Assembly's Total War
.rigid_model_v2 (RMV2) mesh format and .anim skeleton/animation format,
plus the pre-Rome 2 formats of Shogun 2 and Empire/Napoleon.

Supports RMV2 versions 5-8 on import (Rome 2 through Warhammer 3 era) and
6-8 on export, with LODs, bone weights, custom normals, tangents, two UV
channels, vertex colours, textures/materials, attachment points and
round-trip preservation of the format's more obscure fields.

.anim versions 5-8 import (skeletons and animations, armature creation,
actions) and 5-7 export; meshes and skeletons can be imported in either
order and are attached/renamed to match.

Shogun 2 predates all of that and is handled alongside it:

* .rigid_model_v2 versions 1 and 2 - a different header, material and
  vertex layout, with each mesh welded to a single bone instead of
  skinned (import and export).
* .anim version 1 and a headerless variant - UTF-16 bone names,
  uncompressed float quaternions, and animation events (FIRE_TIME and
  friends) carried inside the file (import and export).
* .animatable_rigid_model - a flat list of bone-welded objects, used for
  jointed props like siege engines (import and export).
* .rigid_model - the same container without the per-object bone index
  (the Empire/Napoleon era format, kept for static scenery); read and
  written by the same operators.
* .variant_part_mesh - the skinned unit parts (helmets, torsos, masks,
  saddles), with their own LOD ladder and up to two weighted bone
  influences per vertex.  Unlike everything else here it stores no
  model-space position: each vertex is held once per influence in that
  bone's space, so importing means skinning it, either against the
  model's reference .anim or against a bind pose recovered from the
  file's own geometry.  Its second, rigid vertex layout (plain float
  positions, used by equipment and crests) is handled as well, including
  the library files that pack dozens of separate props into one
  container (import and export).

Empire and Napoleon are older still, and share most of that machinery:
their .rigid_model and .anim are the same readers, and two formats are
their own:

* .variant_weighted_mesh - Empire's skinned unit meshes, one file per
  unit per LOD holding every body part its variants can draw.  Like
  .variant_part_mesh it stores no model-space position: a vertex lists
  one to eight bone influences and holds its position and normal once
  per influence, in that bone's space.  Every unit is rigged to the same
  41-bone skeleton (animations/reference/tpose.anim), which the importer
  asks for by name.  A file can also carry the props a unit hangs off a
  single bone - Empire's euro_equipment is 134 muskets, spears and
  flagpoles and nothing else - which import as rigid objects bound to
  that bone (import and export).
* .rigid_model_animation - not a new container at all, but the
  .animatable_rigid_model object list followed by a whole headerless
  .anim: self-contained animated props, used for Empire's destruction
  sequences (import and export).

None of the pre-Rome 2 formats appear in TheAssetEditor's C# reference,
so they were reverse-engineered from the games' own files; re-saving any
vanilla file byte-for-byte is a tested invariant for all of them.

Every version any of these formats is read at can also be written,
including the two AssetEditor refuses - .anim v8, whose per-bone packing
is reproduced rather than approximated, and .rigid_model_v2 v5.  Each
exporter opens on the version the model was imported as, and a model
built from scratch starts at what Warhammer 3 most commonly ships.
"""

bl_info = {
    "name": "Total War RigidModel (.rigid_model_v2, .anim)",
    "author": "rob + Claude",
    "version": (1, 17, 0),
    "blender": (3, 6, 0),
    "location": "File > Import-Export",
    "description": "Import-Export Total War RigidModel v2 meshes "
                   "(LODs, skinning, materials), .anim animations, and "
                   "the Shogun 2 and Empire/Napoleon model formats "
                   "including their skinned unit meshes",
    "category": "Import-Export",
    "doc_url": "",
    "tracker_url": "",
}

# Support add-on reload during development (F3 > Reload Scripts).
if "bpy" in locals():
    import importlib
    for _mod_name in ("rmv2_format", "anim_format", "arm_format",
                      "vmpf_format", "vwm_format",
                      "utils", "skeleton", "scene_layout",
                      "mesh_build",
                      "properties", "materials", "import_rmv2",
                      "export_rmv2", "import_anim", "export_anim",
                      "import_arm", "export_arm",
                      "import_vmpf", "export_vmpf",
                      "import_vwm", "export_vwm",
                      "import_rma", "export_rma", "ui", "io_ops"):
        if _mod_name in locals():
            importlib.reload(locals()[_mod_name])

import bpy  # noqa: E402,F401

from . import anim_format  # noqa: E402,F401
from . import arm_format  # noqa: E402,F401
from . import export_anim  # noqa: E402,F401
from . import export_arm  # noqa: E402,F401
from . import export_rma  # noqa: E402,F401
from . import export_rmv2  # noqa: E402,F401
from . import export_vmpf  # noqa: E402,F401
from . import export_vwm  # noqa: E402,F401
from . import import_anim  # noqa: E402,F401
from . import import_arm  # noqa: E402,F401
from . import import_rma  # noqa: E402,F401
from . import import_rmv2  # noqa: E402,F401
from . import import_vmpf  # noqa: E402,F401
from . import import_vwm  # noqa: E402,F401
from . import io_ops  # noqa: E402
from . import materials  # noqa: E402,F401
from . import mesh_build  # noqa: E402,F401
from . import properties  # noqa: E402
from . import rmv2_format  # noqa: E402,F401
from . import scene_layout  # noqa: E402,F401
from . import skeleton  # noqa: E402,F401
from . import ui  # noqa: E402
from . import utils  # noqa: E402,F401
from . import vmpf_format  # noqa: E402,F401
from . import vwm_format  # noqa: E402,F401


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
