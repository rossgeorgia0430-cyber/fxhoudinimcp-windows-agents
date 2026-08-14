# HeightField COP Terrain — Houdini 22 游戏关卡地形

Playbook for building game-level terrain in **Houdini 22 Copernicus**.
H22 moved the HeightField toolset into COPs. Source: `/news/22/model`
(Terrains) and `/heightfields_cop/index`.

Use this when the request sounds like: "COP heightfield", "erode / terrace
/ strata terrain", "mask by feature", "export a heightfield to SOP / UE /
Solaris displacement".

---

## 0. Start inside a HeightField COP Network

The tab-menu **HeightField COP Network** SOP (`sop/copnet`) sets the
Copernicus default canvas size/orientation for terrain. Prefer that over
a generic `copnet`.

Tab-menu starters (they configure existing COP types):

| Tab-menu tool | Actual COP type | Role |
|---------------|-----------------|------|
| HeightField COP | `layer` | Initial height layer |
| HeightField Noise COP | `fractalnoise3d` | Vertical terrain noise |
| HeightField Blur COP | `blur` | Smooth |

---

## 1. H22 HeightField COP nodes (use these names)

Verified present on Houdini 22.0.368 (Copernicus):

| Node | Type name | Role |
|------|-----------|------|
| HeightField Erode | `heightfield_erode` | Hydraulic + thermal erosion |
| HeightField Strata | `heightfield_strata` | Sedimentary layers |
| HeightField Terrace | `heightfield_terrace` | Stepped plains |
| HeightField Mask by Feature | `heightfield_maskbyfeature` | Masks from terrain features |
| HeightField Slump | `heightfield_slump` | Loose material slides downhill |
| HeightField Clip | `heightfield_clip` | Clamp min/max height |
| HeightField Project | `heightfield_project` | Project geometry onto the field |
| HeightField Transform 2D | `heightfield_xform2d` | 2D scale/translate/rotate |
| HeightField Transform 3D | `heightfield_xform` | 3D transform |
| HeightField to Mono | `heightfieldtomono` | Height → grayscale |
| Mono to HeightField | `monotoheightfield` | Grayscale → height |
| HeightField Visualize | `heightfield_visualize` | Tint + cable → 2D volumes / SOP geo |

Always `list_cop_node_types(filter="heightfield")` before creating.

SOP-side companion: `neuralterraingenerate` (ML terrain inference) and
SOP `heightfield_visualize` for traditional HeightField SOPs.

---

## 2. Typical game-level graph

```
HeightField COP Network SOP (copnet, terrain canvas)
  layer                  # base height
  fractalnoise3d         # large forms
  heightfield_erode      # wear
  heightfield_strata / heightfield_terrace
  heightfield_maskbyfeature + slump
  heightfield_visualize  # 3D preview / SOP exit
```

Turn on the Copernicus **3D output flag** to preview the height in the
3D viewer independently of the 2D canvas. `Preview Material` COP can
export a textured heightfield to Solaris as a displacement grid.

Recipes in the tab menu: Terrain Cobblestone, Terrain Sandy Rock.

---

## 3. Exit to SOP / engine

- `heightfield_visualize` (COP or SOP) converts the field to geometry
  for `export_file` / FBX / Alembic.
- `heightfieldtomono` writes a height texture (PNG/EXR) for UE Landscape
  or a displacement map.
- For Niagara / VAT motion of a deforming terrain, mesh first then
  `bake_attribute_to_spatial_atlas`.

---

## 4. Verification

1. `get_cop_info` / `get_cop_layer` — resolution and layer names.
2. 3D output flag + `capture_screenshot` / `flipbook`.
3. After SOP convert: `get_geometry_info` and `attribute_stats` on `height`.

{network_housekeeping}
