# Plan — cross-section RV placement & de-overlap via YARDS SEQ / logical-track survey

Status: design note (2026-08-26). Not yet implemented. Context: the world-save RV
plotter draws two cosmetic artifacts on cars whose two trucks sit on *different*
Run8 sections (see `cross-section-rv-cosmetic-artifacts` memory and the worked case in
`_test_world.xml`, train 99991190):

1. **Off-angle car** — a cross-section car is drawn as a straight chord between its two
   truck points, so it tilts off a curved section joint.
2. **Overlapping cars** — cars are placed independently with no neighbour awareness, so
   two cars whose metric extents overlap are drawn stacked.

Both stem from the same root: we place each car purely from its own two trucks, in a
**per-section** coordinate world, with no model of how sections chain together or which
cars share a physical track. This note describes how to fix that by reintroducing the
YARDS logical-track / `SEQ` model — and, importantly, how much of its input data we
**already parse today**.

---

## 1. What YARDS `SEQ` actually is (and isn't)

Per `world-import-rail-vehicle-ordering.md`, YARDS turns each truck into one **monotonic
1-D coordinate** along a *logical track* (a surveyed, linearly-ordered chain of Run8
sections):

```
truckPosition   = sectionOrdinal + clamp(distanceFromConfiguredStart, 0, L) / L
vehiclePosition = min(valid truckPositionA, truckPositionB)
SEQ             = rank of vehiclePosition within the logical track (1 = yard-direction end)
```

Two things matter for us:

- **It is an *ordering*, not a *drawing position*.** Every section is one coordinate
  unit regardless of real length (`… / L`), so `SEQ` deliberately throws away metric
  distance. It answers *"in what order do cars sit on this track?"* — not *"where on the
  map is this car?"*. Our current metric walk (metres along the real polyline) is already
  **more** precise for drawing than the YARDS coordinate. So SEQ alone does **not** fix
  either artifact.
- **The reusable asset underneath SEQ is the *logical track*:** an explicitly **ordered,
  direction-normalized chain of adjacent sections**, plus the knowledge of **which cars
  belong to the same physical track** (including cars from different `TrainLoader` cuts).
  That chain is exactly what we lack, and exactly what cures the cross-boundary problem.

So the goal is not "compute SEQ" for its own sake — it's to **build the ordered
logical-track section chain** SEQ is derived from, and use it two ways:
(a) a *contiguous* multi-section coordinate for drawing, and (b) a shared ordering for a
de-overlap pass.

---

## 2. Data we already have vs. what's missing

| Ingredient | Have it? | Where |
|---|---|---|
| Per-section polyline + cumulative metres | ✅ | `SectionData.paths`, `_concat_section_polyline`, `_cumulative_lengths` |
| Per-truck `(section, start_node, distance, route_prefix)` | ✅ | `world_parser`, consumed in `_SectionPlacer.truck()` |
| **Section adjacency / topology** (which section connects to which) | ⚠️ partial | the switch-network traversal already walks `next_section` connectivity in `extract_sections` — enough to *derive* adjacency, but we don't persist a section→neighbours graph |
| **Ordered logical-track section chain + direction (`START_NODE`)** | ⚠️ **parsed but discarded** | the `.ind` file (`industry_db = …Config.ind`) is already read by `extract_industries`; each track record carries `route_prefix`, `track_section`, **`track_direction`** (r8lib `Industry`/`Track`), but we keep only a flat, unordered `IndustryData.track_sections` list |
| Logical-track grouping (`TRACK_CODE`) | ❌ | YARDS derives this from the `.ind` survey (`r8Industry2YARDS.ParseIndustryConfig`); we don't reconstruct it |
| Per-section arc length for the survey | ✅ | `calculate_section_length` / `SectionData.length_m` |

**Key finding:** the ordered-section survey that YARDS calls "track reference data" is
generated *from the same `.ind` file we already open*. We are throwing away the ordering
and the direction field on parse. "Bringing back YARDS SEQ" is therefore mostly a matter
of **retaining data we currently drop**, not sourcing anything new.

---

## 3. Two data sources, two problems — they are separable

The angle fix and the overlap fix have different minimal requirements. Worth stating
plainly because one is cheap and universal, the other is heavier and yard-scoped.

### 3a. Section topology (from the track DB) → fixes the ANGLE, everywhere

To stop drawing a cross-section car as a chord, we only need **section adjacency +
orientation** so we can walk one *continuous* polyline through the joint:

- Build a `section → [neighbouring sections, with which end joins which]` graph. The
  switch-network traversal in `extract_sections` already visits `next_section` links; we
  persist that into a `SectionGraph` keyed by `(route_prefix, section_id)`.
- In `_SectionPlacer`, when truck A is on section *S1* and truck B on adjacent *S2*,
  concatenate the two section polylines at their shared endpoint into one oriented
  polyline, then walk **cumulative metres** across the joint just like the same-section
  case (`_subpolyline`). The body follows the real curve; no chord.
- Generalize to N sections for a car spanning >1 boundary (rare, but a long car in tight
  yard geometry can).

This needs **no YARDS data and works on mainline track too.** It is the recommended first
increment and it removes artifact #1 outright.

### 3b. Logical-track survey (from `.ind`) → enables DE-OVERLAP, yard-scoped

To resolve artifact #2 we need to know **which cars share one physical track** and their
**order** along it, so we can lay them without overlap (and across separate cuts). That is
the logical-track model:

- Extend the `.ind` parse to keep, per logical track: an **ordered** section list and each
  section's **direction** (`track_direction` → `START_NODE`), plus a track id
  (`TRACK_CODE` — see the grouping caveat below). Store as a `LogicalTrack` reference set.
- Assign each car to a logical track by truck A's section (truck B fallback), matching
  YARDS' rule.
- Compute the YARDS monotonic coordinate for ordering **and** the contiguous metric
  coordinate for drawing (§3a chain gives us the latter for free — a logical track *is* an
  ordered section chain, so build one continuous polyline per logical track and walk it in
  metres).
- **De-overlap pass:** within a logical track, sort cars by coordinate, then sweep and
  push abutting bodies apart to at least a coupler gap (or clamp each body so neighbours
  don't intersect). This is the piece that actually removes the visual overlap; it runs on
  the shared coordinate, whichever coordinate we choose.

This only covers **surveyed yard tracks**. Cars on unsurveyed track (`NOSH` / mainline)
have no logical track, so they keep the §3a metric placement with no de-overlap — which is
fine, because overlaps cluster in yards where cars are densely packed.

---

## 4. Proposed implementation phases

Incremental; each phase is independently shippable and testable.

**Phase A — Section graph + through-joint drawing (no YARDS data).**
- New `SectionGraph` (adjacency with end-matching) built during `extract_sections`.
- `_SectionPlacer`: add a `body_across(keys…, d0, d1)` that walks a concatenated,
  oriented multi-section polyline; use it in `extract_trains` when the two trucks are on
  adjacent sections instead of `_extend_segment`.
- Keep `_extend_segment` only as the last-resort fallback (non-adjacent / unresolved).
- **Fixes artifact #1.** Verify on train 99991190 car #182906 (trucks on 2278/2279).

**Phase B — Retain the `.ind` logical-track survey.**
- Extend `extract_industries` (or a sibling `extract_logical_tracks`) to emit ordered
  section chains + `track_direction` per track, keyed by `(route_prefix, track_code)`.
- Emit to region JSON / manifest so both the batch (`output_generator`) and live
  (`serve.py`) paths have it. No behaviour change yet — data plumbing only.

**Phase C — Logical-track coordinate + `SEQ`.**
- Build one continuous polyline per logical track (from the ordered chain + direction).
- Port the YARDS coordinate (`sectionOrdinal + clamped/L`, min of trucks) for ordering,
  and derive `SEQ` per track. Attach `logical_track` + `seq` to each `RailVehicleData`
  (nice for tooltips/search too).
- Reference: the pseudocode in `world-import-rail-vehicle-ordering.md` §"Reference
  pseudocode". Do **not** reproduce the Level 1/2/3 legacy mutations unless we later need
  byte-for-byte YARDS parity — we don't; we want correct order, not bug-compat.

**Phase D — De-overlap pass.**
- Within each logical track, sort by coordinate and resolve body overlaps to a minimum
  coupler gap. Decide the policy: (i) *shift* cars to remove overlap (preserves lengths,
  moves cars off their exact truck points), or (ii) *clamp* bodies (keeps truck points,
  shortens drawn body). (i) reads better in a packed yard; (ii) stays truthful to the
  save. Recommend (ii) as default with (i) behind a flag.
- **Fixes artifact #2** on surveyed track.

---

## 5. Caveats & hazards (carried from the YARDS doc)

- **Yard-only coverage.** `.ind` surveys yard tracks; mainline/`NOSH` cars are unaffected.
  De-overlap is therefore a yard feature. Set expectations accordingly.
- **`TRACK_CODE` derivation is the non-trivial bit.** YARDS builds logical-track codes
  from the `.ind` via `r8Industry2YARDS`; our `.ind` records give sections + direction but
  we must confirm how they group into a single ordered yard track (one industry record vs.
  a track spanning several). This needs a short spike against a real `Config.ind`.
- **Make section order explicit.** YARDS leans on unordered SQL row order (a documented
  bug). We must store an explicit ordinal / derive the chain from topology + configured
  direction — never rely on parse order.
- **Direction (`START_NODE`) must be right** or the coordinate inverts. Map Run8
  `track_direction` → YARDS `START_NODE` carefully and unit-test both orientations.
- **Boundary ties.** ordinal *i* at fraction 1.0 == ordinal *i+1* at 0.0; break ties by a
  documented ordinal comparator (RV id), as YARDS does.
- **Duplicate sections.** a section id must map to one logical track; reject/first-wins
  like YARDS.
- **Paired truck values stay aligned.** already handled in `world_parser` (two truck
  records), keep it that way.

---

## 6. Recommendation

Do **Phase A alone first.** It removes the more jarring artifact (the tilted car),
needs no YARDS/`.ind` work, and helps mainline *and* yard cars. Reassess whether the
overlap is still bothersome after A — in many views the tilt was the eye-catching part.

If overlap still needs fixing, do **B→C→D** as a yard-scoped feature. The heavy lift is
Phase B/C's survey plumbing and the `TRACK_CODE` grouping spike, not the math (the
coordinate is ~15 lines). Keep it opt-in per config (`[trains] deoverlap = true`) so the
default stays a faithful 1:1 plot of the save.

**Explicitly out of scope / not worth it:** bug-for-bug YARDS parity (Level 1/2/3
positioner mutations, the SQL-row-order dependency, exception-swallowing). We want the
coordinate model and the ordered-chain, implemented cleanly.

---

## DECISIONS LOCKED (2026-08-26) — within-consist rigid layout

Scope narrowed by the user to **within-consist overlap only** (cross-cut/yard-track
overlap deferred → no `.ind` survey needed for now). A read-only spike on `_test_world.xml`
train 99991190 (Bakersfield, prefix 250) grounded these decisions:

- **Order = XML consist order, trusted.** Top-to-bottom in the world save = front-to-back
  of the consist (user-confirmed). The clean cars (0–10) already march monotonically along
  the reconstructed section chain at even ~17 m spacing, so no physical position sort is
  needed within a single train.
- **Anchor = the consist midpoint** (user-confirmed). Lay cars symmetrically about the
  consist's centre so drift from the true positions is spread to both ends. Compute the
  anchor as the **median** of the cars' along-chain positions (median resists outliers from
  bad-geometry cars — see below).
- **Layout = rigid end-to-end by DB body length.** Position each car by *order + length*,
  never by its own (possibly corrupt) per-truck metres. Body length = `RV_LENGTH −
  2·COUPLER_OFFSET` (fallback: truck span). Draw each body as the sub-polyline of the
  contiguous chain between `[centre − body/2, centre + body/2]` so it follows curves. This
  sidesteps BOTH the cross-section chord and the corrupt-geometry problem at once.

**Spike evidence (train 99991190):**
- Occupied sections chain via 0.00 m endpoint joins:
  `2277→2278→2279→2282→2283→(2280)→2281→2287→2288→2289→2290`.
- The three overlapping cars (827939, 467104, 582385) ALL have a truck on **section 2283**,
  whose extracted polyline is **degenerate** (start == end, yet length 150.9 m), producing
  impossible 26 m / 46 m truck spans for ~15.5 m cars. This is an **independent geometry
  bug** (affects the track map too), flagged as its own task — do not try to fix it inside
  the de-overlap work; the rigid layout tolerates it by design.

**Algorithm (per train):**
1. Take cars in XML order (front→back).
2. From the section-adjacency graph, build the ordered chain of sections the consist
   occupies (head car's section → tail car's section) and its one continuous polyline `P`
   with cumulative length; orient head→tail from the end cars' positions.
3. Assign each car a length (DB body, fallback truck span).
4. Anchor = median of cars' along-chain positions. Lay cars end-to-end about the anchor
   with a small coupler gap between neighbours.
5. Each car body = sub-polyline of `P` for its `[lo, hi]` span.
6. Fallback to today's per-truck placement for any car whose section isn't on the chain
   (shouldn't occur within one consist).

Still TBD before/while building: exact coupler-gap value, how to build the chain path when
the consist branches through a switch (the `2283→2280` jump crosses branches), and whether
to gate this behind a config flag (default on? `[trains] deoverlap`).

## Source map (our code)

| Concern | File / symbol |
|---|---|
| Current truck placement + chord fallback | `region_extractor.py` — `_SectionPlacer.truck/body`, `_extend_segment`, `extract_trains` |
| Per-section polyline + cumulative length | `region_extractor.py` — `_concat_section_polyline`, `_cumulative_lengths`, `_subpolyline` |
| Section length | `region_extractor.py` — `calculate_section_length`, `SectionData.length_m` |
| Switch-network / `next_section` traversal (topology source) | `region_extractor.py` — `extract_sections` |
| `.ind` survey parse (already reads track_section + track_direction) | `region_extractor.py` — `extract_industries`; `r8lib.py` — `Industry`, `Track`, industry-track record |
| RV → JSON | `output_generator.py` — `rail_vehicle_to_dict`, manifest emit |
| Live path | `serve.py` — `trains_payload` (reuses `extract_trains` + `train_to_dict`) |
| YARDS reference model | `world-import-rail-vehicle-ordering.md` |
