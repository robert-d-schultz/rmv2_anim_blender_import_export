"""Blender material construction for imported RMV2 meshes.

Builds a Principled BSDF node tree from the texture list of an RMV2
material.  Texture paths inside the file are pack-relative
(``variantmeshes\\...\\foo.dds``); they are resolved against a user supplied
"texture root" directory (add-on preference, overridable per import).

Several CA-specific texture packing conventions are unpacked into proper
node graphs rather than just being dropped:
  - MaterialMap (id 29): R = metallic, G = roughness.
  - Mask (id 3): RGB channels select where up to 3 adjustable "player
    colour" tints are painted onto the base colour (faction colouring);
    alpha drives emission strength (banners, glowing bits, etc).
  - Normal (id 1): packed "orange" style (R=1, G=Y, B=0, A=X, Z
    reconstructed) rather than a standard tangent-space RGB normal map;
    unpacked by a cached node group, see _get_orange_normal_group().

The node tree is a *preview* nicety only — the exporter reads texture paths
from the RMV2 object settings, never from these nodes.

Layout: nodes sit on a 300-unit grid, columns left to right by processing
stage (textures -> mask separator -> pickers/mixers/single-hop processors
-> BSDF -> output), single-purpose utility nodes (Separate Color, Mix,
Normal Map, Invert) are collapsed so the graph reads as compact "wires
between meaningful boxes" rather than a wall of expanded nodes.
"""

from __future__ import annotations

import os

import bpy

from . import rmv2_format as rf

# Texture types wired into the shader
_TT_DIFFUSE = 0
_TT_NORMAL = 1
_TT_MASK = 3
_TT_SPECULAR = 11
_TT_GLOSS = 12
_TT_BASE_COLOUR = 27
_TT_MATERIAL_MAP = 29

_PLAYER_COLOURS = ((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0),
                   (0.0, 0.0, 1.0, 1.0))
_EMISSION_COLOUR_DEFAULT = (1.0, 1.0, 1.0, 1.0)

_ALT_EXTENSIONS = (".dds", ".png", ".tga", ".jpg")

# Grid: everything lives on a 300-unit step so the graph lines up cleanly.
_STEP = 300
_COL_TEX = -3 * _STEP      # texture image nodes
_COL_SEP = -2 * _STEP      # mask's Separate Color (feeds the mix chain, not
                           # the BSDF directly, so it sits one column earlier
                           # than the other single-hop processing nodes)
_COL_PICKER = -1 * _STEP   # player-colour / emission-colour RGB pickers
_COL_PROC = 0              # mix chain, normal map, materialmap separator,
                           # gloss-invert: everything that feeds the BSDF
                           # directly (one hop away)
_COL_BSDF = 1 * _STEP
_COL_OUT = 2 * _STEP

# Top-to-bottom texture stacking order, mirroring roughly where each one
# ends up on the Principled BSDF (Base Color, then Metallic/Roughness,
# Normal, Specular, Emission) so link lines don't criss-cross as much.
_TEX_ROW_PRIORITY = {
    _TT_BASE_COLOUR: 0,
    _TT_DIFFUSE: 0,
    _TT_MATERIAL_MAP: 1,
    _TT_GLOSS: 2,
    _TT_NORMAL: 3,
    _TT_SPECULAR: 4,
    _TT_MASK: 5,
}


def resolve_texture_path(pack_path: str, texture_root: str) -> str | None:
    """Find a pack-relative texture on disk, trying alternate extensions."""
    if not pack_path or not texture_root:
        return None
    rel = pack_path.replace("\\", os.sep).replace("/", os.sep).lstrip(os.sep)
    candidate = os.path.join(texture_root, rel)
    if os.path.isfile(candidate):
        return candidate
    stem = os.path.splitext(candidate)[0]
    for ext in _ALT_EXTENSIONS:
        alt = stem + ext
        if os.path.isfile(alt):
            return alt
    return None


def _load_image(pack_path: str, texture_root: str):
    disk_path = resolve_texture_path(pack_path, texture_root)
    if disk_path is None:
        return None
    try:
        image = bpy.data.images.load(disk_path, check_existing=True)
        return image
    except RuntimeError:
        return None


def _find_input(node, *names):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _new_separate_rgb(tree, location):
    """ShaderNodeSeparateRGB was folded into ShaderNodeSeparateColor in
    Blender 4.0; support both. Returns (node, r_out, g_out, b_out)."""
    try:
        node = tree.nodes.new("ShaderNodeSeparateColor")
        node.mode = "RGB"
        r, g, b = node.outputs["Red"], node.outputs["Green"], \
            node.outputs["Blue"]
    except RuntimeError:
        node = tree.nodes.new("ShaderNodeSeparateRGB")
        r, g, b = node.outputs["R"], node.outputs["G"], node.outputs["B"]
    node.location = location
    node.hide = True
    return node, r, g, b


def _new_mix_rgb(tree, location, label, fac_socket, colour_a, colour_b):
    mix = tree.nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MIX"
    mix.label = label
    mix.location = location
    mix.hide = True
    tree.links.new(colour_a, mix.inputs["Color1"])
    tree.links.new(colour_b, mix.inputs["Color2"])
    tree.links.new(fac_socket, mix.inputs["Fac"])
    return mix.outputs["Color"]


def _new_combine_rgb(tree, location, r, g, b):
    """ShaderNodeCombineRGB was folded into ShaderNodeCombineColor in
    Blender 4.0; support both. r/g/b are each either a socket (linked) or
    a plain number (default_value)."""
    try:
        node = tree.nodes.new("ShaderNodeCombineColor")
        node.mode = "RGB"
        sockets = (node.inputs["Red"], node.inputs["Green"],
                  node.inputs["Blue"])
    except RuntimeError:
        node = tree.nodes.new("ShaderNodeCombineRGB")
        sockets = (node.inputs["R"], node.inputs["G"], node.inputs["B"])
    node.location = location
    node.hide = True
    for socket, value in zip(sockets, (r, g, b)):
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            tree.links.new(value, socket)
    return node.outputs["Color"]


def _new_math(tree, operation, location, *values):
    """ShaderNodeMath with 1-3 inputs. Each of `values` is either a socket
    (linked) or a plain number (default_value). Returns the Value output."""
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = location
    node.hide = True
    for i, value in enumerate(values):
        if isinstance(value, (int, float)):
            node.inputs[i].default_value = value
        else:
            tree.links.new(value, node.inputs[i])
    return node.outputs[0]


_ORANGE_NORMAL_GROUP = "RMV2 Orange Normal Decode"


def _get_orange_normal_group() -> bpy.types.ShaderNodeTree:
    """Total War's Warscape engine packs normal maps in what modders call
    the "orange" layout (named for the R=1/B=0 filler channels giving the
    raw DDS an orange tint if viewed directly): R=1 (constant), G=Y
    (bump direction), B=0 (constant), A=X. Z is never stored - it's
    reconstructed from X/Y at render time.

    Decoded exactly the way the game's own live shader does it
    (GetPixelNormal() in TheAssetEditor's MathFunctions.hlsli, included
    by both its PBR shaders): X = R*A, Y = 1-2*G, Z = sqrt(1-X^2-Y^2).
    Note the green channel is NOT un-gamma'd here even though CA's own
    export tooling (BlueToOrangeNormalMapProcessor) boosts it by ^(1/2.2)
    on the way in - the live shader reads it raw, so matching that (not
    the theoretically "correct" inverse) is what stays faithful to how
    the game actually renders these textures.

    Repacked to [0,1] on the way out so it can feed a standard Normal Map
    node same as any other tangent-space normal map. Built once and cached
    in bpy.data.node_groups; every material with a Normal texture gets its
    own ShaderNodeGroup instance pointing at this."""
    existing = bpy.data.node_groups.get(_ORANGE_NORMAL_GROUP)
    if existing is not None:
        return existing

    ng = bpy.data.node_groups.new(_ORANGE_NORMAL_GROUP, "ShaderNodeTree")
    ng.interface.new_socket("Color", in_out="INPUT",
                            socket_type="NodeSocketColor")
    ng.interface.new_socket("Alpha", in_out="INPUT",
                            socket_type="NodeSocketFloat")
    ng.interface.new_socket("Color", in_out="OUTPUT",
                            socket_type="NodeSocketColor")

    group_in = ng.nodes.new("NodeGroupInput")
    group_in.location = (-1500, 0)
    group_out = ng.nodes.new("NodeGroupOutput")
    group_out.location = (1500, 0)
    alpha = group_in.outputs["Alpha"]

    sep, r, g, _b = _new_separate_rgb(ng, (-1200, 200))
    ng.links.new(group_in.outputs["Color"], sep.inputs["Color"])

    x_raw = _new_math(ng, "MULTIPLY", (-900, 200), r, alpha)
    x_signed = _new_math(ng, "MULTIPLY_ADD", (-600, 200), x_raw, 2.0, -1.0)
    y_signed = _new_math(ng, "MULTIPLY_ADD", (-600, -200), g, -2.0, 1.0)

    x2 = _new_math(ng, "MULTIPLY", (-300, 200), x_signed, x_signed)
    y2 = _new_math(ng, "MULTIPLY", (-300, -200), y_signed, y_signed)
    sum2 = _new_math(ng, "ADD", (0, 0), x2, y2)
    zsq = _new_math(ng, "SUBTRACT", (300, 0), 1.0, sum2)
    zsq_clamped = _new_math(ng, "MAXIMUM", (600, 0), zsq, 0.0)
    z = _new_math(ng, "SQRT", (900, 0), zsq_clamped)

    x_col = _new_math(ng, "MULTIPLY_ADD", (1200, 200), x_signed, 0.5, 0.5)
    y_col = _new_math(ng, "MULTIPLY_ADD", (1200, -200), y_signed, 0.5, 0.5)
    z_col = _new_math(ng, "MULTIPLY_ADD", (1200, 0), z, 0.5, 0.5)

    combined = _new_combine_rgb(ng, (1350, 0), x_col, y_col, z_col)
    ng.links.new(combined, group_out.inputs["Color"])
    return ng


def build_material(name: str, textures: list, alpha_mode: str,
                   texture_root: str) -> bpy.types.Material:
    """Create a Blender material for a list of (texture_type, path) pairs."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (_COL_OUT, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (_COL_BSDF, 0)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # Texture nodes, stacked top-to-bottom by _TEX_ROW_PRIORITY so link
    # lines to the BSDF stay roughly horizontal instead of crossing over.
    ordered_textures = sorted(
        enumerate(textures),
        key=lambda item: (_TEX_ROW_PRIORITY.get(item[1][0], 99), item[0]))

    by_type = {}
    first_row_y = _STEP
    row_y = first_row_y
    for _, (ttype, path) in ordered_textures:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.location = (_COL_TEX, row_y)
        row_y -= _STEP
        label = rf.TEXTURE_TYPE_NAMES.get(ttype, f"texture_{ttype}")
        node.label = label
        node.name = label
        node["rmv2_path"] = path
        image = _load_image(path, texture_root)
        if image is not None:
            node.image = image
            if ttype not in (_TT_DIFFUSE, _TT_BASE_COLOUR):
                image.colorspace_settings.name = "Non-Color"
        by_type.setdefault(ttype, node)

    # Base colour: prefer the PBR BaseColour slot, fall back to Diffuse. In
    # practice every real RMV2 material has one of these; if it's somehow
    # missing, fall back to a plain white RGB node so downstream wiring
    # (direct link or the mask mix chain) always has a real source to use.
    colour_node = by_type.get(_TT_BASE_COLOUR) or by_type.get(_TT_DIFFUSE)
    if colour_node is not None:
        base_colour_out = colour_node.outputs["Color"]
    else:
        white = tree.nodes.new("ShaderNodeRGB")
        white.label = "Base Colour (white placeholder)"
        white.location = (_COL_TEX, first_row_y + _STEP)
        white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        base_colour_out = white.outputs["Color"]

    mask_node = by_type.get(_TT_MASK)
    if mask_node is not None:
        # Player colour tinting: mask.rgb select where 3 adjustable colours
        # (faction colours in-game) are painted over the base colour. The
        # mask's own row becomes the anchor for the whole processing block,
        # which is always the bottom-most texture row (see
        # _TEX_ROW_PRIORITY), so this never collides with the single-hop
        # nodes above that reuse their own texture's row.
        block_top = mask_node.location.y
        sep_mask, mask_r, mask_g, mask_b = _new_separate_rgb(
            tree, (_COL_SEP, block_top + _STEP))
        tree.links.new(mask_node.outputs["Color"], sep_mask.inputs["Color"])

        picker_nodes = []
        for i, default in enumerate(_PLAYER_COLOURS):
            picker = tree.nodes.new("ShaderNodeRGB")
            picker.label = f"Player Colour {i + 1}"
            picker.location = (_COL_PICKER, block_top - i * _STEP)
            picker.outputs[0].default_value = default
            picker_nodes.append(picker)

        mixed = base_colour_out
        for i, (chan, picker) in enumerate(
                zip((mask_r, mask_g, mask_b), picker_nodes)):
            mixed = _new_mix_rgb(
                tree, (_COL_PROC, block_top - i * _STEP),
                f"Mask.{'RGB'[i]} -> Player{i + 1}", chan, mixed,
                picker.outputs["Color"])
        tree.links.new(mixed, bsdf.inputs["Base Color"])

        emission_colour = tree.nodes.new("ShaderNodeRGB")
        emission_colour.label = "Player Emission Colour"
        emission_colour.location = (_COL_PICKER, block_top - 3 * _STEP)
        emission_colour.outputs[0].default_value = _EMISSION_COLOUR_DEFAULT
        emission_in = _find_input(bsdf, "Emission Color", "Emission")
        if emission_in is not None:
            tree.links.new(emission_colour.outputs["Color"], emission_in)
        strength_in = _find_input(bsdf, "Emission Strength")
        if strength_in is not None and "Alpha" in mask_node.outputs:
            tree.links.new(mask_node.outputs["Alpha"], strength_in)
    else:
        tree.links.new(base_colour_out, bsdf.inputs["Base Color"])

    if colour_node is not None and alpha_mode == "TRANSPARENT":
        alpha_in = _find_input(bsdf, "Alpha")
        if alpha_in is not None:
            tree.links.new(colour_node.outputs["Alpha"], alpha_in)

    normal_node = by_type.get(_TT_NORMAL)
    if normal_node is not None:
        decode = tree.nodes.new("ShaderNodeGroup")
        decode.node_tree = _get_orange_normal_group()
        decode.label = "Orange -> Normal"
        decode.location = (_COL_SEP, normal_node.location.y)
        decode.hide = True
        tree.links.new(normal_node.outputs["Color"], decode.inputs["Color"])
        tree.links.new(normal_node.outputs["Alpha"], decode.inputs["Alpha"])

        nm = tree.nodes.new("ShaderNodeNormalMap")
        nm.space = "TANGENT"
        nm.location = (_COL_PROC, normal_node.location.y)
        nm.hide = True
        tree.links.new(decode.outputs["Color"], nm.inputs["Color"])
        tree.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])

    # MaterialMap (R=metallic, G=roughness) takes priority over Gloss, since
    # it's the newer/more complete of the two CA conventions.
    matmap_node = by_type.get(_TT_MATERIAL_MAP)
    if matmap_node is not None:
        sep_mm, mm_r, mm_g, _ = _new_separate_rgb(
            tree, (_COL_PROC, matmap_node.location.y))
        tree.links.new(matmap_node.outputs["Color"], sep_mm.inputs["Color"])
        tree.links.new(mm_r, bsdf.inputs["Metallic"])
        tree.links.new(mm_g, bsdf.inputs["Roughness"])
    else:
        gloss_node = by_type.get(_TT_GLOSS)
        if gloss_node is not None:
            inv = tree.nodes.new("ShaderNodeInvert")
            inv.location = (_COL_PROC, gloss_node.location.y)
            inv.hide = True
            inv.label = "Gloss to Roughness"
            tree.links.new(gloss_node.outputs["Color"], inv.inputs["Color"])
            tree.links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])

    spec_node = by_type.get(_TT_SPECULAR)
    if spec_node is not None:
        spec_in = _find_input(bsdf, "Specular IOR Level", "Specular")
        if spec_in is not None:
            tree.links.new(spec_node.outputs["Color"], spec_in)

    if alpha_mode == "TRANSPARENT":
        # EEVEE (legacy) and EEVEE Next use different settings; set whichever
        # this Blender version exposes.
        if hasattr(mat, "blend_method"):
            try:
                mat.blend_method = "HASHED"
            except TypeError:
                pass
        if hasattr(mat, "surface_render_method"):
            try:
                mat.surface_render_method = "DITHERED"
            except TypeError:
                pass

    return mat
