# Golden Fluid Pipeline — curve-guided FLIP, rebake-free post-processing, UE delivery

Playbook for building/directing a **curve-guided fluid effect** (energy ribbon,
golden fluid, magic trail, comet) that must be art-directable in Houdini and
delivered to Unreal Engine as an Alembic GeometryCache plus optional VAT
textures for Niagara particles. Distilled from a shipped 25-iteration
production (shot46 golden fluid, Houdini 21 + UE 5.7, 60fps).

Use this when the request sounds like: "make a fluid follow a path / curve",
"loop a simulated fluid seamlessly", "change the fluid's shape/speed/length
without re-simming", "export this fluid to UE with material channels", or
"emit Niagara particles from an Alembic/VAT fluid surface".

---

## 0. The paradigm: simulate ONCE, then do everything else analytically

The expensive truth: directors never stop at the sim. They retime, re-path,
re-shape, loop, and add particles — weekly. The architecture that survives
this is:

```
FLIP sim (once, disk cache, CANONICAL — never touch again)
  → UNBEND (inverse-bind particles into curve-local tube space)
  → analytic post in tube space (loop / retime / reshape / visibility)
  → REBEND (map tube coords onto the current target curve)
  → recompute velocity AFTER all P edits (id-matched differences)
  → mesh (particlefluidsurface) → mesh-space shape layer (live knobs)
  → write material channels (uv / Cd / Alpha) on the mesh
  → pre-bake UE world space, export ABC, import with IDENTITY conversion
```

Everything downstream of the sim cache is math on attributes. Re-iteration
cost drops from "re-sim overnight" to "re-export in minutes". Keep the sim
cache and the frozen source curve **read-only forever**.

---

## 1. PTF frames — the foundation of every curve operation

Build a twist-free (Bishop / parallel-transport) frame per curve point.
Center-difference tangents, then rotate the previous normal by the axis-angle
between consecutive tangents (Rodrigues), re-orthogonalize, fix sign
continuity:

```vex
// per point i on a resampled curve (equal spacing!)
vector a = normalize(v@tangentu_prev);        // T[i-1]
vector b = normalize(v@tangentu);             // T[i]
vector ax = cross(a, b);
float  s  = length(ax);
float  c  = clamp(dot(a, b), -1.0, 1.0);
vector Ni = v@N_prev;
if (s > 1e-6) {
    ax /= s;
    float ang = atan2(s, c);
    Ni = Ni*cos(ang) + cross(ax, Ni)*sin(ang) + ax*dot(ax, Ni)*(1.0-cos(ang));
}
Ni = normalize(Ni - dot(Ni, b)*b);            // Gram-Schmidt
if (dot(Ni, v@N_prev) < 0) Ni = -Ni;          // sign continuity
v@B = cross(b, Ni);
```

Rules: resample the curve to **equal segment length** first (arclen == u × L);
seed N0 from a world up-vector (switch up axis if degenerate); store N, B,
tangentu as point attributes. Any later "position ↔ curve space" mapping is
just dot products against these frames.

## 2. UNBEND / REBEND — inverse bind into tube space

Per particle: `xyzdist()` onto the frozen source curve → parametric u and
offset d. Convert to tube coordinates:

- along-track: `s = u × curve_length`, stored relative to a **robust head
  anchor**: sort all s, take the 99th percentile (not max — outliers).
- cross-section: `off_n = dot(d, N)`, `off_b = dot(d, B)`.

Rewrite P as `(s_rel, off_n, off_b)` — the fluid is now a straight tube, head
at x≈0. Rotate v into the same frame. REBEND is the exact inverse on the
TARGET curve: `P = C(s) + off_n×N(s) + off_b×B(s)`, with straight-line tangent
extrapolation past both ends. Rebuild age/nage/dist attributes there.

Gotcha: keep a hidden straight **extension** appended past the target curve
end (e.g. 25 m) when the head can be pushed beyond the curve end later —
otherwise the tail clamps and piles up at the endpoint.

## 3. Seamless loop of a cached sim segment

Pick a pretty segment [loop_start, loop_end] of the raw cache. Loop clock
(subframe, id-matched interpolation):

```
tf = loop_start + ((F - shot_start) × flow_speed + phase) mod (loop_end - loop_start)
```

Sample floor/ceil cache files, match points by `id` (`findattribval`), lerp P
AND pscale. First and last frame states differ → distribute the closure
difference over the WHOLE cycle with a smootherstep weight (C0/C1/C2
continuous at the wrap, no local tug):

```vex
float u = clamp(cyc / period, 0.0, 1.0);
float w = u*u*u*(u*(u*6.0 - 15.0) + 10.0);    // 6u^5 - 15u^4 + 10u^3
@P -= w * (v@loop_PB - v@loop_PA);            // per-id endpoint states
```

Verify with `compare_frames` on the seam (frame N vs frame N+period on P) and
a flipbook over the wrap.

## 4. Two independent clocks — never couple them

- Clock 1 (internal flow): which sim frame plays (formula above). Changes
  surface detail speed only.
- Clock 2 (path advance): where the head sits on the target curve, in METERS.
  Build a TIMING archive first: evaluate the emitter motion per frame,
  accumulate arclength → (src_frame, s_orig) point attributes on a python
  SOP. Effective frame `174 + (t-174)×path_speed(t) + offset` → interpolate
  archive → head anchor in meters. Add a late "exit push":
  `anchor += smooth(exit_start, FEND, F) × exit_distance`.

Key the `path_speed` channel for direction (bezier keys), never touch clock 1.
Teach the artist: the two knobs are orthogonal by design.

## 5. Velocity is a DELIVERABLE — recompute it after every P edit

Any chain that rebuilds P (deform/retime/loop/rebend) silently breaks v:
direction dies (v stays in sim space) and magnitude drifts (double speed
scaling). UE GeometryCache uses v for per-render-frame motion-vector
extrapolation (`render_pos = P − shifted×MV`, import stores `MV = −v × 1/fps`),
so broken v = jelly jitter in UE while Houdini looks fine.

Fix: after the LAST node that edits P, recompute v by id-matched finite
difference (Houdini `trail` SOP: Compute Velocity, central difference,
**Match by Attribute = id**; or forward-difference against a timeshifted
input, unmatched → v=0). Do NOT pre-flip the sign for UE — the importer
negates itself. Position and velocity must go through the SAME export
transform (3×3), always.

Validation: particle-level prediction error `|P + v×dt − P_next@id|` over
id-matched pairs (p99 ≈ 0 by construction). Surface-level metrics can only
falsify, never certify (isosurface rebuild floor).

## 6. Form-preserving shape knobs on the MESH, not the particles

Wrong (classic): `pscale *= thickness × visibility` on particles — changes the
surfacing kernel radius, the mesh collapses when thinning. Right: keep REBEND
neutral and do shape on the surfaced mesh, so disk caches never bake the
knobs in:

```vex
// mesh vertex → project to curve (s, frame), live CONTROL knobs lf/th/vis
float s2 = anchor + (s - anchor) * lf;        // LENGTH: arclen remap around head anchor
vector d = v@P - cp;
float axial = dot(d, T);                      // keep axial component untouched
vector radial = d - axial*T;
float offn = dot(radial, N), offb = dot(radial, B);
v@P = cp2 + axial*T2 + (offn*N2 + offb*B2) * th * vis;   // THICKNESS/VIS: centripetal pinch
if (vis <= 1e-5) removepoint(0, @ptnum);
```

Thickness = cross-section pinch (density along track unchanged → same
silhouette, narrower). Length = arclen remap (cross-section untouched).
Visibility = section scale + mesh point kill (for VAT/particle paths: don't
kill, write vis into an ALPHA channel instead). The three knobs are
orthogonal; QA each by measuring radius/span/point-count invariance of the
other two.

Optional "edge break-up" for a livelier rim: outer-skin-only traveling ripple
— mask `smooth(core, 2.5×core, r)`, amplitude `amt × r × w` (grows with
off-axis distance), time phase `2π × cycles × (F−F0)/(frame_count−1)` with
INTEGER cycles over the delivery range (loop-safe), 3 noise channels along
radial/side/tangent weighted ~2.0/1.2/0.8.

## 7. "Juice → honey" look tuning without re-simming

- Raise `particlefluidsurface` voxel size + influence radius → high-freq
  wrinkles filtered out.
- Enable Mean Curvature Flow smoothing (volume-preserving, ~8 iterations)
  + a final mean-value pass — this reads as viscosity.
- Particle-side: a few spacing-relaxation passes (pcfind neighborhood, mean
  delta, strength ≤0.35, per-step cap ~8% of pscale) kill clumping.
- Lower the internal loop speed (clock 1) for weight.
- Convert droplet/stripe detail nodes to kernel-size-only or mask-only
  (never orbital displacement).

## 8. Material channels contract (mesh point attributes → UE standard streams)

Write per-point on the mesh; keep a strict keep-list before export
(attribdelete negate=1, e.g. keep only `N v uv Cd Alpha` + P):

| Attribute | Formula | UE stream | Meaning |
|---|---|---|---|
| uv.x | fit(s−anchor, −8×L, +2×L, 0, 1) | UV0.U | flow_u along-track 0→1, FIXED bounds (no breathing) |
| uv.y | frac(atan2(dot(d,B), dot(d,N)) / 2π + 1) | UV0.V | cross-phase, seamless via PTF |
| Cd.r | clamp(length(v)/16) | vertex color R | speed01 |
| Cd.g | clamp(length(v − dot(v,T)×T)/6) | vertex color G | turbulence01 |
| Cd.b | frac(((F−F0)×flow_speed)/period) | vertex color B | loop_phase01 (same formula as clock 1!) |
| Alpha | rand(floor(u×2048) + floor(v×1024)×4099 + 17) | vertex alpha | stable spatial hash, no time term |

UE materials then compose stripes as `sin(flow_u×k − loop_phase01×2π×m)`.
Re-audit the fixed fit bounds whenever the loop segment changes.

## 9. Houdini → UE Alembic contract

- Pre-bake UE space in Houdini: translate to the UE world spot (meters),
  then xform scale `(100, −100, 100)` = meters→centimeters + Y mirror
  (NOT an axis swap). The mirror flips winding → append a reverse-winding
  SOP. v goes through the same transform.
- UE import: Conversion = **None/identity**, GeometryCache type, import ABC
  velocities as motion vectors, flatten tracks. Import resets the materials
  array → capture and re-apply, then save the package.
- Export with a guard: bake → verify per-frame non-empty → write to
  `.tmp.<pid>` → atomic `os.replace`. Return codes lie; file sizes don't.
- Never batch-cook with Houdini in Manual update mode — it produces empty
  frames that look "done". Save/restore update mode around bakes.

## 10. VAT textures → Niagara particles on a dynamic-topology cache

Alembic vertex indices are unstable frame-to-frame, so "spawn on vertices" is
impossible. Bake **spatial flipbook atlases** instead (this MCP server has
the tool):

1. In Houdini, thin final particles to a regular grid count (stride sample →
   trim to exactly 64×64 = 4096), run them through the SAME shape math and
   the SAME UE transform as the mesh (world cm).
2. `bake_attribute_to_spatial_atlas(node_path=<staging null>, attrib_name=...,
   encode="raw", grid_cols=64, grid_rows=64, frames=[first,last],
   tiles_x/tiles_y=<near-square>, pad=1)` → one EXR per attribute
   (position+visibility alpha as RGBA; velocity as RGB).
3. UE: import both EXRs as float textures, sRGB OFF, bilinear.
4. Niagara: sample with a Custom HLSL node in **Function** usage (DynamicInput
   wraps code into a single-expression cast — texture sampling with locals
   cannot compile there). Frame index
   `(floor(Emitter.Age×fps) + frame_offset) % num_frames`; texel for seed j,
   frame k: tile (k mod tiles_x, k div tiles_x), pixel (j mod 64, j div 64)
   inside the 66px cell (64 + 2×pad).
5. Alignment: `frame_offset` = (niagara activation frame − cache start frame).
   Bounds: VAT world coords sit kilometers from the component — set emitter
   Fixed Bounds huge (e.g. ±200000) or get frustum-culled to invisibility.
6. Authoring pitfall: HLSL text bodies must contain REAL newlines. A literal
   backslash-n plus a leading `//` comment makes the tokenizer eat the whole
   body as one comment → outputs silently fall back to defaults (particles
   stuck at world origin). Verify by reading the generated
   NiagaraEmitterInstanceShader.usf.

## 11. Debug & acceptance toolbox

- `compare_frames` (seam check), `attribute_stats` over frames (channel
  ranges), `verify_animation` (is a channel actually animated),
  `analyze_alembic_output` (cook cost/topology/bbox before export),
  `inspect_image` + `sample_image`/`image_region_stats` (atlas spot checks),
  `flipbook` (motion review without scrubbing a heavy scene).
- Duplicate-frame falsification for retime bugs: real fluid frames are never
  bit-identical; expected hold count vs measured → binomial verdict.
- Alpha-sweep for broken velocities: predict `P + αv×dt`, measure nearest
  distance to next frame vs α; healthy v minimizes at α≈1.
- Adversarial review: one agent's job is to DISPROVE the conclusion; metrics
  that survive it can be shipped.

## 12. Hard-won red lines

1. Canonical caches (raw sim, frozen source curve, timing archive): never
   edit, never re-point.
2. Bake order: canonical → mesh → ABC; export only via a guarded button that
   re-verifies cache signature first.
3. A bake that "succeeds" in seconds wrote empty frames — check file sizes.
4. Reimport resets material slots; save packages before closing the editor.
5. UE-side double transforms double the error: identity conversion only.
6. Mesh-space shape knobs stay OUT of the mesh cache (rebake would freeze
   them); put them downstream of the cache node.
7. A display flag on an empty CONTROL null = "I can't see my fluid" — put
   the flag on a real output node.
8. Copying a hip to a sidecar for headless render breaks `$HIP`-relative
   cache paths (empty frames, rc=0). Patch paths in the copy, verify sizes.
