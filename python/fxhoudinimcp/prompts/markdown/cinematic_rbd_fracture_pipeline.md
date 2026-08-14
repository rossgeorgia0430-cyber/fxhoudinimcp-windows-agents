# Cinematic RBD Fracture Pipeline

Production playbook for art-directed thin-shell fracture in Houdini, natural
packed-piece Bullet motion, artist-friendly controls, and bone-based FBX
delivery to Unreal Engine. It was distilled from a train-window shot, but the
rules below are intentionally shot-independent. Never copy a piece count,
seed, hash, frame number, axis preset, or impulse value from an older shot
without reading the live HIP and the current delivery files.

Use this playbook when the request sounds like:

- break glass, a window frame, a facade panel, or another mixed thin shell;
- make a fracture read as angular line-like silhouettes instead of a scale-up;
- replace procedural piece translation with natural rigid-body motion;
- simplify a large CTRL panel for artists and localize it;
- preserve multiple materials through packed RBD to FBX;
- export real tail frames for Sequencer retiming and validate the FBX by
  reimporting it.

## 0. The contract

The durable architecture is:

```text
source truth
  -> topology/material preflight
  -> material-specific fracture branches
  -> unique stable piece names
  -> pack
  -> one-time initial state (v, w, mass, collision, active)
  -> Bullet integrates translation and rotation
  -> packed simulation output
  -> material-preserving FBX preparation
  -> Labs RBD to FBX
  -> ASCII metadata audit + binary FBX reimport audit
```

Four invariants are non-negotiable:

1. Every piece has one unique, stable `name` at every sampled frame.
2. Final motion is rigid T/R. If `pscale` or `scale` attributes exist, they
   remain identity; absence of those optional attributes is also valid.
3. Axis and unit conversion happen once under an explicitly verified contract.
4. Export success is proved by inspecting and reimporting the written file,
   not by trusting a ROP success flag.

## 1. Discover the real source before building

Start with `get_scene_info`, `get_network_overview`, targeted `get_node_info`,
`get_geometry_info`, `get_bounding_box`, and `get_attribute_info` calls.
Establish:

- HIP path, Houdini version, FPS, frame and playback ranges;
- source node and whether it is world space or parent/local space;
- scene up axis and unit scale, using measured bounds and a known camera or
  reference transform rather than filename conventions;
- open versus closed shells, connected components, open edges, thickness,
  material fingerprints, and material-slot naming policy;
- the requested impact point, camera-facing direction, pre-impact frame,
  impact frame, delivery end, and retime-tail requirement;
- whether an importer upstream has already converted axes.

The summary geometry tools do not, by themselves, prove closedness, open-edge
count, connected components, or wall thickness. For those facts, discover the
version-specific native nodes with `get_node_card`, create a temporary probe
branch using Group/Connectivity/Measure or equivalent SOPs, and read back the
resulting groups/attributes. A read-only `execute_python` HOM audit that counts
edge incidence and connected components is also valid. Never infer topology
only from a bounding box or primitive count; remove reproducible probe nodes
after the audit.

Treat documents and old manifests as hints. Regenerate counts, hashes, and
parameter snapshots after the artist's final adjustment.

## 2. Mixed thin shells need different fracture languages

A glass pane and its frame should not share one fracture algorithm merely
because they arrive in one mesh.

```text
SOURCE
  |-- glass by stable material fingerprint
  |     -> solidify -> glass fracture driven by projected impact points
  |     -> prefix names with a glass family token
  |
  `-- frame by stable material fingerprint
        |-- scatter points on the original thin shell
        `-- solidify -> Voronoi/material fracture using those points
        -> prefix names with a frame family token

branches -> merge -> assemble/pack
```

Rules:

- Zero-thickness open shells must be solidified before Boolean, Voronoi, or
  material fracture. Validate the result, including side and back faces.
- Scatter frame seeds on the original surface, not blindly on the newly
  extruded side/back faces. This keeps cuts oriented across the frame bars.
- Preserve stable source attributes such as `fbx_material_name` through the
  fracture and packing stages.
- Prefix piece names per branch before merging so two fracture nodes cannot
  create the same name.
- Use one packed primitive / packed point per piece at the solver boundary.
- Measure piece-size distributions per material family. Glass should include
  small chips plus a few readable hero shards; a structural frame should read
  heavier and coarser.

### Impact design: one master plus weighted satellites

A single radial impact often produces a textbook spider web. Prefer one
artist-movable master point plus a small set of satellite points:

1. Generate satellites around the master in the pane plane.
2. Project every satellite back to the target surface with `xyzdist` and
   `primuv`.
3. Give the master the highest density/importance and vary lower satellite
   weights; do not make all impacts equal.
4. Drive both crack generation and the impulse field from the same master
   location so the visual crack origin and physical burst origin agree.
5. Break perfect concentric rings with discontinuity controls and edge noise.

Review the impact/start, early burst, mid-flight, and tail frames. The target
is a hierarchy of large silhouettes, thin black edge-on shards, and smaller
secondary debris, not merely a busy final frame.

## 3. Natural motion: initialize once, then integrate

The most common fake-looking setup recomputes positions every frame from a
formula such as `(rest - impact) * expansion * ease`. Even if `pscale` remains
one, the whole window undergoes a global similarity transform and reads as a
scale animation.

The physical pattern is:

```text
PACKED_REST
  -> SHARD_INIT       # one-time v/w/mass/collision/active authoring
  -> RBD_BULLET       # gravity, collision, bounce, friction, drag, angular drag
  -> OUT_RBD_PACKED
```

`SHARD_INIT` should author a coherent initial condition, not an animation:

- Use a smooth distance falloff from the master impact.
- Combine surface-normal push, in-plane radial/tangential spread, spatially
  coherent directional noise, and a small composition drift.
- Bias smaller pieces toward more speed and spin; keep hero shards and frame
  chunks heavier and slower.
- Give glass and frame separate speed/spin multipliers.
- Prefer spin axes in the pane plane so thin shards turn edge-on and produce
  readable line-like silhouettes.
- Keep a small, deliberate edge-hold population inactive when hanging remnants
  help the shot. Report held and active counts explicitly.
- Derive mass and collision scale from measured piece geometry. Do not hide
  pieces by zero scale.

Bullet owns every later frame. Tune gravity, substeps, collision padding,
bounce, friction, linear drag, and angular drag, then let velocity and angular
velocity evolve. Do not overwrite P or orient downstream to fake easing.

### Motion invariants

Across pre-impact, start, early, mid, end, and tail samples:

- piece count and unique names remain stable;
- if `pscale` or `scale` attributes exist, they stay at identity; they may be
  explicitly normalized before export, but their absence is not an error;
- transform determinant stays near +1 and basis lengths stay near one;
- active pieces change P and/or rotation; intentional held pieces do not;
- gravity produces a plausible velocity arc;
- `w` is nonzero where tumbling is expected and may continue in the tail;
- there is no shear, negative scale, or frame-dependent topology.

Use spatially coherent fields rather than independent white noise per piece.
Independent randomness causes neighbors to exchange order and reads as noisy
teleportation even when collisions prevent intersections.

## 4. Artist CTRL is a product surface

Expose one authoritative CTRL node. A practical layout is three tabs:

| Tab | Chinese example | Contents |
|---|---|---|
| Quick | `快速调节` | start frame, min/max burst speed, normal push, spread, base spin, gravity, seed |
| Shape | `碎片形态` | impact count/radius, crack density, size variation, chip ratio, thickness, frame chunking, edge hold |
| Physics | `物理动态` | falloff, size bias, noise, flip bias, material multipliers, bounce, friction, drag, angular drag, substeps |

Keep the quick tab to roughly 6-10 high-frequency controls. Put secondary
controls on the other tabs; hide solver trivia and compatibility channels.
Visible parameters should use the artist's language. For a Chinese-facing
scene, every visible parameter needs a Chinese label and a Chinese ToolTip.

Each ToolTip should say:

- what changes on screen;
- the unit (`frame`, `m`, `m/s`, `deg/s`, ratio, or seed);
- what increasing versus decreasing it does;
- an important risk or cook-cost note when applicable.

Implementation rules:

- Downstream nodes read the CTRL through `ch()`, `chf()`, or equivalent
  channel references. Directly assigning a downstream parameter can replace
  the expression and silently disconnect CTRL.
- Preserve old internal parameter names when existing channels reference them.
  Hide legacy parameters instead of deleting them.
- When rebuilding a `hou.ParmTemplateGroup`, preserve current values and
  defaults, make the operation idempotent, and find an old tab by child
  parameter membership or label rather than assuming Houdini kept its folder
  token.
- After saving a HIP that changed spare parameters, reload it in a separate
  Hython process and inspect the node. A bridge `save_scene` response alone is
  not persistence proof.

Audit all visible parameter labels, ToolTips, pages, defaults, and downstream
expressions before delivery.

## 5. Reusable fxhoudinimcp verification loop

For a graph of three or more nodes, use `build_network(dry_run=True)` first,
then build and `verify_network`. Use `get_node_card` instead of guessing
version-specific node types or parameter menus.

Choose representative frames from the live ranges:

```text
[pre-impact, impact, early, mid, requested end, first tail, tail midpoint, tail end]
```

Then combine:

- `verify_animation` on P and other relevant channels after proving that the
  sampled `name` sequence and element order are stable;
- `attribute_stats` on P, v, w, pscale, and scale;
- `compare_frames` to falsify accidental holds or duplicated tail frames only
  when the `name` sequence/order is identical;
- `get_attrib_values`/`sample_geometry` for piece names and spot checks;
- `verify_network`, `find_error_nodes`, and `get_node_errors_detailed`;
- `capture_screenshot` at visual milestones;
- `flipbook` from the shot camera for motion review.

Heavy cooks can exceed the bridge timeout while Houdini is still working. A
timeout means completion is unknown: inspect cook state and outputs before
retrying. Reissuing the same mutation can duplicate work or overwrite an
expression.

`verify_animation` and `compare_frames` compare flattened element arrays; they
do not key pieces by `name`. If order differs between frames, branches, or an
FBX reimport, build a `name -> element` mapping in a temporary HOM audit (or use
a future name-keyed diagnostic) and compare matched pieces. Never compare rest,
simulation, or reimported joints by raw element index without first proving the
ordering contract.

## 6. Preserve materials while applying packed RBD transforms

Direct Labs RBD-to-FBX export from the original packed geometry can collapse
all pieces to one Houdini shader. Use a material-preserving rest branch:

```text
REST_PACKED
  |-- unpack
  |-- map primitive fbx_material_name to named export placeholder materials
  `-- repack by stable name -----------------------------.
                                                           \
SIM_PACKED -> derive pure-rotation orient ------------------+-> xformpieces
REST_PACKED -> derive rest pure-rotation orient ------------'       |
                                                                   OUT_FBX_RBD
                                                                         |
                                                               Labs RBD to FBX 3.0
```

Requirements:

- Rest, simulation, and material-preserving repack branches have a one-to-one
  stable `name` mapping.
- Material assignment happens on unpacked primitives, where face-level
  material identity exists.
- Named placeholder shaders match the intended stable material fingerprints.
- Extract only the rotation component when converting packed transform
  intrinsics to `orient`; reject scale/shear before applying transforms.
- `xformpieces` applies the real Bullet T/R to the material-preserving rest
  pieces.
- Export root translation/rotation are zero and scale is one.

Labs RBD to FBX creates a flat bone-based hierarchy suitable for an Unreal
Skeletal Mesh and Animation Sequence. The expected invariant is one root plus
one joint/bone per unique piece.

## 7. Axis, units, world space, and retime tail

Never choose an axis preset from memory. Inspect the live ROP menu with
`get_node_card`, compare a known-good sibling asset when available, and audit
the written FBX metadata. A validated Houdini Y-up/meters to Unreal delivery
may use a Z-up/centimeters FBX, but the exact handedness/menu token is
version-specific and must be measured.

Choose exactly one transform contract:

- **World-space bake:** FBX contains final world placement; Unreal actor is at
  identity. If attached later, use Keep World Transform.
- **Local-space bake:** transform into the intended parent's local space in
  Houdini and apply the parent transform exactly once in Unreal.

Do not combine ROP conversion, Import Uniform Scale, actor scale, an extra 90
degree rotation, and a parent world transform. Repeated conversions are the
usual cause of 90-degree, 100x, and double-offset failures.

When the editor needs retime headroom, extend all three ranges together:

1. Houdini frame range;
2. Houdini playback range;
3. export ROP range.

Tail frames must be continued Bullet integration, not duplicates of the last
requested frame. Prove it by comparing P and rotation/angular velocity inside
the tail. Record the extra-tail frame count in the manifest, but do not make a
shot-specific number a global default.

## 8. FBX audit: inspect, then reimport

Write the final delivery as Binary FBX, but make a separate temporary ASCII
audit export from the same source node, frame range, axis, unit, naming, and
root-transform settings; the format toggle and output path should be the only
intentional differences. Check:

- material definitions and their names;
- mesh/model count versus unique pieces;
- one animation stack;
- start/end time, sample count, FPS/custom frame rate;
- up/front/coordinate axes and signs;
- unit scale metadata.

For character clips (not packed RBD), H22 `kinefx::rop_gltfcharacteroutput`
exports multiple glTF 2.0 animation clips; the FBX character ROP adds
**Remove Scaling from Joint Transforms** — turn it on when the engine
rejects joint scale. The `/out` glTF ROP type is now `gltf` / `gltf::2.0`
(`rop_gltf` is invalid there).

Then reimport the final Binary FBX with `kinefx::fbxcharacterimport` into a
temporary validation network and check:

- one root plus one joint per piece;
- unique joint names and stable hierarchy;
- clip name, source range, and sample rate;
- determinant near +1 and basis-vector lengths near one;
- source-versus-reimport position and rotation at representative frames,
  matched by stable piece/joint name rather than element index;
- expected material definitions/slots.

Set tolerances relative to scene scale and report the maximum observed errors;
do not silently declare a hardcoded tolerance universal. Delete temporary
audit nodes/files after validation only if they are reproducible.

Finally record the actual final file path, byte size, modification time,
SHA-256, FPS, ranges, piece/joint count, material list, axis/unit metadata, and
validation summary in a delivery manifest. Regenerate it after every artist
retune or re-export.

## 9. Unreal import baseline

For a Labs RBD-to-FBX bone hierarchy, the usual first import is:

- Skeletal Mesh: On;
- Import Mesh and Import Animations: On;
- Skeleton: None, creating a dedicated fracture skeleton;
- Create PhysicsAsset: Off;
- Animation Length: Exported Time;
- Use Default Sample Rate: Off, custom rate equal to the Houdini/export FPS;
- Import Bone Track: On;
- Convert Scene and Convert Scene Unit: On when this matches the audited FBX;
- Force Front XAxis: Off unless a measured contract proves otherwise;
- Import Uniform Scale: 1, import transform identity;
- Import Normals and Tangents when authored normals are the source of truth.

These labels describe the common Legacy FBX importer. Unreal versions and the
Interchange importer can expose different names or defaults, so inspect the
active importer and keep the audited metadata/transform contract authoritative.

Assign the audited material slots explicitly. Keep the complete animation
range, including real tail frames, and retime the section in Sequencer. If
flying shards are culled, inspect and enlarge the Skeletal Mesh Component
bounds deliberately. Film delivery should avoid frame stripping or destructive
animation compression that removes rigid-body keys.

## 10. Anti-patterns and gotchas

Do not ship these as the default solution:

- per-frame radial expansion/easing presented as rigid-body dynamics;
- animating scale to reveal, hide, or separate pieces;
- independent white-noise velocities that destroy neighborhood coherence;
- identical glass/frame speed and spin distributions;
- random 3D spin axes with no silhouette intent;
- fracture directly on a zero-thickness open shell;
- equal-weight satellite impacts or unprojected impact points;
- changing a downstream parameter directly and erasing its CTRL expression;
- trusting a direct packed FBX export without a material-definition audit;
- extending only the ROP range while the simulation/playback range still ends;
- copying the last frame to create a fake retime tail;
- applying axis/unit/world transforms at multiple stages;
- enabling automatic PhysicsAsset creation for hundreds of shards;
- treating an old document's piece count or hash as current truth.

Houdini-specific traps:

- A packed-point motion wrangle must run over points. Running `@P` logic over
  primitives can silently create a primitive P attribute while moving nothing.
- Assign `primintrinsic()` to an explicit `matrix3` before matrix arithmetic to
  avoid ambiguous generic multiplication.
- `sample_direction_uniform` expects a `vector2`, for example `set(u, v)`.
- A large whole-asset Stash can inflate the HIP by hundreds of megabytes. Use
  a versioned `.bgeo.sc` File Cache plus a manifest for production geometry;
  reserve Stash for small or temporary snapshots.

## 11. Definition of done

A reusable cinematic fracture delivery is complete only when all are true:

- source topology, materials, axis, units, FPS, and ranges were measured;
- the network is healthy and piece names/counts are stable;
- CTRL is the single artist entry point with localized labels and ToolTips;
- sampled motion is rigid T/R with no scale/shear (optional scale attributes
  are absent or identity) and real Bullet continuation;
- visual milestones and a camera-locked flipbook were inspected;
- materials survive the material-preserving export branch;
- ASCII metadata and Binary reimport audits pass;
- the final artifact manifest was regenerated after the last adjustment;
- any unverified proposal is labeled experimental rather than canonical.

One useful but still conditional proposal is hiding shared source-mesh material
slots on a component instance and restoring unrelated geometry with a patch
Static Mesh. It can avoid reimporting a many-slot hero asset, but it is not a
canonical solution until the patch export, live Unreal alignment, and material
overrides have been validated for that project.
