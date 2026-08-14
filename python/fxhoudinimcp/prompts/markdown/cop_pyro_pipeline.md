# COP Pyro Pipeline — Houdini 22 烟云火雾（Copernicus）

Playbook for fire / smoke / mist in **Houdini 22 Copernicus**, using a
Pyro Block instead of a DOP network. Source: H22 `/news/22/pyro` and
`/pyro/copintro`.

Use this when the request sounds like: "COP pyro", "pyro in Copernicus",
"candle / fireball / billowy smoke / dry ice", "implicit collision for
smoke", or "export a pyro VDB / flipbook / VAT for a game engine".

---

## 0. License first — do not guess

COP Pyro lives in COPs but **requires DOP-level permission**. It is
available in Houdini FX, Indie, Apprentice, and Education. It is **not**
available in Houdini Core.

If `create_cop_node` / `list_cop_node_types(filter="pyro")` cannot see
`pyro_block_begin`, `pyro_configure`, or `pyro_block_end`:

1. Call `get_houdini_connection_status` and read the license category.
2. Report that COP Pyro is missing because this session lacks a DOP-level
   license (or the H22 COP Pyro package is not loaded).
3. Do **not** invent a SOP/DOP substitute and call it COP Pyro. Fall back
   to `setup_pyro_sim` (SOP `pyrosolver`) only after stating the fallback.

---

## 1. Start from a Pyro Configure template (preferred)

H22 ships seven tab-menu **Pyro Configure** examples. They are shelf-like
scripts that drop a working Pyro Block, not single node types. Prefer them
as the starting graph; do not recreate the template from memory unless
`list_cop_node_types` proves the pieces exist.

| Template | What it illustrates |
|----------|---------------------|
| Pyro Configure Fire | Minimal fire |
| Pyro Configure Fireball | Rising fireball |
| Pyro Configure Candle Flame | Slow flame + soot |
| Pyro Configure Billowy Smoke | Dense rising smoke |
| Pyro Configure Cigarette Smoke | Wispy rising smoke |
| Pyro Configure Large Smoke Plume | Large plume pulled by wind |
| Pyro Configure Dry Ice | Falling cold mist + implicit colliders |

If a tab-menu script cannot be invoked from MCP (they are not always
plain `create_cop_node` types), **stay prompt-driven**: create the
minimal block below, then match parameters to the chosen look. Do not
hard-code a fake `setup_cop_pyro` graph that claims to be a template.

---

## 2. Minimal network (Pyro Block 2.0)

Inside a Copernicus `copnet` (not legacy COP2):

```
[source / collision / forces]
        |
pyro_block_begin::2.0
        |
pyro_configure::2.0          # voxelsize / resolution / origin
        |
  (optional: pyro_activate::2.0, pyro_advect::2.0,
             pyro_buoyancy::2.0, pyro_vortexconfinement,
             pyro_velocityscale, pyro_emitfromflame)
        |
pyro_block_end::2.0
        |
  get_cop_vdb / get_cop_layer / flipbook / VDB export
```

Verified type names on Houdini 22.0.368 (Copernicus `Cop` category):

- Block: `pyro_block_begin::2.0`, `pyro_block_end::2.0`
- Configure: `pyro_configure::2.0`
- Sources: `pyro_sourceshape`, `pyro_sourcefromshape`,
  `pyro_sourcefromvolume`, `pyro_sourcefromlayer`,
  `pyro_sourcefromlayer::2.0`, `pyro_sourcefrompoints`
- Collisions: `pyro_collisionshape`, `pyro_collisionfromshape`
- Forces: `pyro_buoyancy::2.0`, `pyro_vortexconfinement`,
  `pyro_velocityscale`, `pyro_advect::2.0`, `pyro_emitfromflame`,
  `pyro_disturbance`, `pyro_turbulence`, `pyro_uniformforce`,
  `pyro_axisforce`, `pyro_dissipate`

Always `list_cop_node_types(filter="pyro")` before creating. Prefer the
`::2.0` spelling when both exist.

---

## 3. Sources and implicit collisions

Implicit surfaces are **math-defined** shapes (zero voxel memory, exact
collision). Use them for sources, colliders, and domains.

- Emit: `pyro_sourceshape` (points that *are* implicit surfaces) →
  `pyro_sourcefromshape` / `pyro_sourcefromvolume` / `pyro_sourcefromlayer`
- Collide: `pyro_collisionshape` → `pyro_collisionfromshape`
- Dry-ice / glass / character collision: implicit collider first; only
  rasterize to VDB if you need a sampled field.

---

## 4. Verification loop (do not skip)

COP Pyro is time-driven. Frame 1 is often empty or tiny.

1. `get_cop_info` / `get_cop_layer` on the block-end (resolution, layers).
2. `get_cop_vdb` for density / temperature / flame / vel (voxel counts,
   bbox; on H22, min/max/average when NanoVDB/ImageLayer APIs exist).
3. `set_frame` to mid-shot, then `flipbook` or `capture_screenshot`.
   Do **not** judge the look on frame 1.
4. If fields are all zero: check license, check that a source is wired
   into the block, check `pyro_configure` voxel size vs source size.

---

## 5. Exits to game / PV

- **Engine VDB:** convert the COP VDB output to SOP (`sop/copnet` or
  a COP-to-SOP bridge) and write `.vdb` / `.bgeo.sc`.
- **Flipbook sequence:** `viewport.flipbook` or a ROP flipbook of the
  3D output flag preview.
- **VAT / Niagara:** after the motion is locked, `bake_attribute_to_spatial_atlas`
  on a meshed or point-sampled representation (normals / height). COP Pyro
  does not replace VAT; it feeds it.

---

## 6. Anti-patterns

| BAD | USE INSTEAD |
|-----|-------------|
| Guess Core-license failure as "node renamed" | Report DOP-level license |
| Recreate the 7 templates as one MCP tool | Prompt + tab-menu / block nodes |
| Dense `smokeobject` / `pyrosolver` DOP | COP Pyro, or SOP `pyrosolver`, or `pyrosolver_sparse` |
| Judge fire on frame 1 | Push frames, then flipbook |
| Manual VEX sourcing | `pyro_sourcefromshape` / `pyro_sourcefromlayer` |

{network_housekeeping}
