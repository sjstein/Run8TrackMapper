# Session handoff — Cross-tile switch-leg rendering fix (option B) (2026-08-21)

Branch: **`openmaps_integration`**. Change is implemented and verified in
`region_extractor.py`; **not committed yet** (unless committed after this note).

## TL;DR
Switches located right on a tile boundary rendered **"backward"** — a turnout's
diverging (or through) leg pointed the wrong way, sometimes crossing the other leg.
Root cause is a tile-seam stitch error. Fixed by **rebuilding cross-tile switch legs
from the throat using the stored tangent bearings** (tangent-based reconstruction),
and snapping the connecting neighbour's endpoint to close the joint. The fix is local
to switch legs and is a proven no-op on everything else. It does **not** remove the
underlying tile seam, so a residual artifact remains (see Limitations).

## The bug
Example: SanBernardino section **10034**, tile **(87,-107)/(87,-108)** boundary.
- A turnout's short curved diverging leg ends in the *neighbouring* tile. That far
  endpoint is stored in the neighbour tile's local frame and re-stitched using a single
  global tile height (`LOCAL_TILE_HEIGHT_M = 1023.2` in `region_extractor.py`; geographic
  mode reuses the same z-normalisation).
- Right on a block-boundary seam the true tile spacing differs from 1023.2 by ~2–3 m.
  A diverging leg only travels a few metres itself, so a ~3 m vertical stitch error is
  comparable to the leg's own travel and **flips** it (heads "up" when it should head
  "down"), producing a geometrically impossible crossing of the through route.
- Reproduced identically in **both** the tile-based (align) viewer and geographic mode,
  and the `.tr4` corners here stitch continuously (seam gap 0), confirming it is in the
  shared *extraction* logic, not the viewer or a tile-correction.

Proof the endpoint was wrong: the stored per-node tangent (`tan_deg[1]`, a compass
bearing = `atan2(dx,-dz)`) says the diverging leg must curve **down**, but the extracted
far endpoint sat ~2.9 m north of where the tangent requires.

## The fix (option B — tangent-based leg reconstruction)
All in `region_extractor.py`:
- **`compute_switch_leg_fixes(db)`** — one pre-pass over the DB. For each switch, every
  drawn (forward) leg that emanates from the throat and **crosses a tile boundary**
  (`throat_tile != far_tile`), straight *and* curved:
  - Anchor at the **throat** (the switch point, from `is_switch_node` — a reliable
    in-tile anchor shared with the other legs).
  - Read tangent bearings from the leg's node + its forward/reverse twin; the signed
    **sweep = far_bearing − throat_bearing** (magnitude cross-checked against `curve_deg`;
    `0` for straight legs — no `curve_sign` guessing).
  - Forward-integrate the arc (`_reconstruct_arc`) to get the leg in the throat tile's
    local frame.
  - Returns `leg_recon` (rebuilt leg geometry per node) and `point_fix` (original shared
    far endpoint + its corrected location, unrounded).
- **`extract_sections`**:
  - Draws rebuilt legs directly (converts the throat-frame points with the active mode).
  - Builds a world-space snap map `point_fix_world` and, after each section's paths are
    assembled, **`_snap_path_endpoints`** tapers any path whose endpoint lands on a
    rebuilt far point (0 at the opposite end → full delta at the matching end). This
    closes the joint for **straight and curved** neighbours, in both modes, without
    moving the neighbour's other end (no cascade).

## Verified
- **10034** (curved leg), **9847** (straight through leg), **1869** (curved, throat in the
  lower tile) — all now head the correct way; joint gaps to their continuations are
  **0.0 m** in tile mode and **0.0°** in geographic mode.
- **0** cross-tile switch legs missed across SanBernardino (94 legs rebuilt).
- **Mid-tile switches unchanged by construction** (validated: reconstruction reproduces
  the stored geometry to < 0.01 m; they never enter the `throat_tile != far_tile` gate,
  and the taper only moves genuine far-point neighbours).
- Full `python output_generator.py config-socal-align.ini` regenerates all 11 regions
  cleanly. `output/socal_align/` is current. The fix is in the extractor, so it applies
  to every mode (default align, `--minimal` geo, `--tile-based`) on regenerate.

## Limitations (deliberately accepted — see "Why not option C")
1. **Connector skew at seam-straddling switch chains.** Where two turnouts sit on
   opposite sides of a seam with a short connector between them (example cluster
   **1806 / 1807 / 1811 / 1812**), each turnout is anchored to its *own* throat's tile.
   1807's throat is in −107, but the connector **1811** and 1812's throat are in −108
   (their `z` is actually past −108's north edge, ~in −107, but labelled −108). The
   ~2.9 m −107/−108 seam offset then has to appear somewhere, and it surfaces as a
   **skew/kink in the connector 1811** (it ramps ~3 m over ~19 m). The switch legs
   themselves are correct; the connector absorbs the seam discontinuity. This is
   inherent to per-switch local correction — translating the connector just moves the
   gap to its other end; only propagating the correction across the whole tile removes
   it (= option C).
2. **Detection gap for same-label cross-seam legs.** The trigger is
   `throat_tile != far_tile`. A leg whose *both* endpoints are labelled the same tile
   but whose far `z` is out of that tile's bounds (past the seam) — e.g. **1812's**
   diverging leg `n0`, both labelled −108 — is **not** reconstructed. Broadening the
   trigger to "endpoint `z` outside `[-tile_height, 0]`" would catch these, but it would
   **not** fix the connector skew above (that clash is on the −107 side), so it was left
   out for now.
3. **Throat coincidence (untested edge case).** If one switch's *throat* were to coincide
   exactly (same world point) with another switch's rebuilt *far* point, the taper could
   shift throat-side sections. Not observed in SanBernardino.

## Why not option C (the "proper" fix), deferred
Option C = derive the per-tile seam offset (the tangent rebuild measures it precisely,
e.g. −2.89 m for −107/−108 here) and shift the affected tile so the tiles align. Deferred
because:
- It lives in the **core coordinate transform** (`convert_run8_to_tile_coords` /
  `convert_run8_to_latlon`) that **every** section, signal, industry, tile boundary,
  area label, and the alignment seed flows through — whole-map blast radius vs B's 94
  legs. High regression risk.
- A rigid per-tile shift **relocates** the seam (opens new seams at the tile's other
  edges) rather than removing it — matching the existing "seams can only be relocated,
  not removed" finding. Doing it *right* needs non-uniform per-tile-row heights or a
  rubber-sheet warp — a substantial project.

If revisited: **survey first** (read-only) how many connectors network-wide are skewed
and by how much, to decide whether C's investment is justified, before touching the
global transform.

## Files
- Changed: `region_extractor.py` (`compute_switch_leg_fixes`, `_reconstruct_arc`,
  `_norm180`, `_bearing_dxdz`, `_pt2`, `_d2`, and the `extract_sections` wiring:
  `_to_world`, `_leg_to_path`, `point_fix_world`, `_snap_path_endpoints`, the rebuilt-leg
  branch).
- Regenerated: `output/socal_align/`.
