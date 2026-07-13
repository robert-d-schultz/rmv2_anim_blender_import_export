"""Blender material construction for imported RMV2 meshes.

Builds a Principled BSDF node tree from the texture list of an RMV2
material.  Texture paths inside the file are pack-relative
(``variantmeshes\\...\\foo.dds``); they are resolved against a user supplied
"texture root" directory (add-on preference, overridable per import).

Two CA-specific texture packing conventions are unpacked into proper node
graphs rather than just being dropped:
  - MaterialMap (id 29): R = metallic, G = roughness.
  - Mask (id 3): RGB channels select where up to 3 adjustable "player
    colour" tints are painted onto the base colour (faction colouring);
    alpha drives emission strength (banners, glowing bits, etc).

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
        nm = tree.nodes.new("ShaderNodeNormalMap")
        nm.location = (_COL_PROC, normal_node.location.y)
        nm.hide = True
        tree.links.new(normal_node.outputs["Color"], nm.inputs["Color"])
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
