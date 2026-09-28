# Cellular idle animation design

Date: 2026-09-28
Module: `blender/scripts/animation_idle.py` (new)
Collections: `01_TUMOR`, `02_HEALTHY_TISSUE`, `04_IMMUNE_SYSTEM`
Touches: `asset_healthy_cell.py`, `asset_tumor_cell.py`, `asset_immune_cells.py`

## Goal

The scene must never look completely static, but it must never look *busy*
either. Cells currently sit at a fixed pose: a render at frame 1 and a render at
frame 120 are the same image, and a viewer notices the absence of life even if
they cannot say what is missing.

This is an **idle** layer, not a narrative beat. It is ambient motion that runs
underneath the six scripted beats in `animation_*.py` and exists to keep the
frame alive when nothing plot-relevant is happening. It must be safe to leave
running for all 240 frames underneath whatever the beats do.

Three behaviours, by cell class:

1. **Healthy** — very subtle membrane movement, slow internal motion.
2. **Tumour** — subtle membrane deformation, more irregular. *Not* faster.
3. **Immune** — slow drifting plus subtle membrane movement.

## The scientific constraint that shapes everything

**The animation must not imply that cancer cells move faster because they are
cancerous.** This is the governing requirement, and it is a claim about the
visualisation's honesty, not a taste preference: a science-fair audience that
concludes "cancer = faster" has been taught something false. Real tumour cells
are not faster than healthy ones. What distinguishes them morphologically is
irregular membrane, blebbing, and a lumpy nucleus — not rate.

The rule this design follows: **express the difference as morphology, never as
rate.** Tumour cells differ in *what* their motion looks like (higher spatial
frequency, more irregular) and in *nothing* about how fast it happens.

This is enforced in code, not just in a comment. `validate_idle()` asserts that
the tumour class's peak surface speed never exceeds the healthy class's, so a
future edit that quietly speeds up the tumour cells fails validation. A
constraint that only lives in prose is a constraint that erodes.

## Decisions taken during brainstorming

| Decision | Choice | Why |
|---|---|---|
| Tumour "more dynamic" means | Irregular, not faster | Higher spatial frequency, same or lower rate. Enforced by a validator check. |
| Membrane mechanism | Reuse `MOD_MembraneDeform` | Healthy and tumour cells already ship this hook. Immune cells get it added, matching the other two. |
| Which cells drift | Immune only | Immune cells genuinely patrol. Healthy and tumour cells are anchored in tissue. Only one class translates, so no per-class speed exists to misread. |
| Pause and reduced-motion | One strength multiplier | Both are the same knob at different values. One control, one validator check. |

## Constraints carried from the existing modules

These are not negotiable; the existing module docstrings, README and validators
all depend on them.

- **One Blender unit == 1 micrometre.** `unit_settings.scale_length` is 1e-6.
  A membrane that moves 0.4 um at 24 fps is a real micrometre of travel, and any
  motion constant is in those units.
- **The name is the identity key.** `ut.get_or_create_object` fetches by name
  before creating, which is what makes rebuilds idempotent.
- **Animation lives in the animation module.** The asset modules own geometry.
  `asset_immune_cells.py` gains only a deformation *hook*, not animation.
- **Idempotent rebuild.** Re-running must not stack duplicate F-curves. The
  project has already been bitten by this once; `ut.clear_animation` is the
  documented cure.
- **No glowing, no pulsing, no emission animation.** The brief rules these out
  and they are also wrong for the microscopy look — a pulsing cell reads as
  sci-fi, not as tissue.

## Architecture

One new module, `animation_idle.py`. It owns every keyframe it writes and
touches no other animation module. This follows the rule the README already
states: *the individual beats own nothing but their own keyframes*, so a single
beat can be re-rendered in isolation. The same isolation has to hold for the
idle layer.

It does **not** import the six `animation_*.py` beats and they do not import it.
The idle layer is ambient and must not depend on, or constrain, the narrative
timeline. When the beats animate a cell, the idle layer's contribution on that
cell is the same as everywhere else.

### Public API

```python
def animate_idle(healthy_ctrl=None, tumor_ctrl=None, immune_ctrl=None,
                 motion_scale: float = IDLE_MOTION_SCALE,
                 frame_start: int = 1, frame_end: int = LOOP_FRAMES) -> dict
def set_idle_motion(scale: float) -> float
def clear_idle() -> int
def validate_idle(collection=None) -> dict
```

`animate_idle` returns a report: which classes were touched, how many objects
got keyframes, and the measured peak speed per class. That report is what the
validator reads, and it is also what a headless run prints.

`set_idle_motion(scale)` is the accessibility control. It re-keys or rescales in
place. It is a module-level function rather than a scene custom property, so
there is exactly one way it gets set.

### How each class moves

**Healthy.** The membrane's existing `MOD_MembraneDeform` strength oscillates
over a long period. The nucleus drifts on a small closed path inside the
cytoplasm. Amplitude deliberately tiny: `HEALTHY_MEMBRANE_AMPLITUDE` is a
fraction of a micrometre.

**Tumour.** Same mechanism, different numbers, and the difference is in the
*shape* of the signal, not its rate:

- The Displace texture's `noise_scale` is finer, so the wrinkles are smaller and
  more numerous — lumpiness, which is what a real tumour membrane looks like.
- The oscillation period is **equal to or longer than** healthy's.
- The nucleus wobbles slightly more but on a period no shorter than healthy's.

**Immune.** Membrane as above, plus the root empty translates along a small
closed path — the patrol drift. The path is a closed loop so it returns exactly
to its start; see Looping below.

### Looping

`LOOP_FRAMES = 240`, matching the scene's `FRAME_RANGE` and 10 seconds at 24 fps.
A seamless loop needs every animated value to equal its frame-1 value at frame
241.

The construction, precisely: a cell whose period is `P` gets its keys laid out
over frames `1 .. 1 + P` — the start value and one full cycle — with the value
at `1 + P` equal to the value at `1`. A `CYCLES` F-curve modifier on
`mode_before='REPEAT'` / `mode_after='REPEAT'` then repeats that block for the
rest of the 240-frame range. So each cell writes `P + 1` keys per channel, not
241, and the cost per cell scales with its own period rather than the scene
length.

Interpolation is `BEZIER` with auto-clamped handles, which is smooth at the
seam and cannot overshoot the key values — an overshooting membrane curve would
push the surface outside its authored bounds.

The subtlety: **periods must divide 240 evenly** or the seam shows. Per-cell
variation means each cell gets its own period drawn from a small set of exact
divisors of 240 (240, 120, 80, 60, 48, 40) rather than an arbitrary number. A
cell with a 137-frame period would arrive at frame 241 in a different pose from
frame 1 and the loop would visibly jump. Constraining the period set is what
makes "loops seamlessly" true for every cell rather than true on average.

### Per-cell variation

Every cell must move differently, or a population reads as clones. Variation
comes from three seeded sources, all pure functions of the cell's index:

- **Period** drawn from the divisor set above.
- **Phase offset** so cells are not all at the same point in their cycle.
- **Sign and direction** of the drift, and the drift's axis.

Seed comes from the cell's ordinal, parsed from its name, which is the project's
identity key. A cell's idle motion is therefore a pure function of its identity
— the same cell always idles the same way, which is what makes the validator
able to reproduce and check it.

### Pause and reduced motion

One multiplier, `IDLE_MOTION_SCALE`, applied to every amplitude at key time.

- `1.0` — full idle motion. The default.
- `0.3` — reduced motion, for viewers who are sensitive to motion.
- `0.0` — paused. Keys are still written, so the timeline structure is
  identical and re-enabling is a one-call change, but every curve is flat. A
  paused scene is indistinguishable from a static one, which is the point:
  pausing must not require clearing the animation.

Writing flat curves rather than deleting them means pause and resume cannot
desynchronise the beat layer, which shares the same frame range.

### Performance

The concern is real: `01_TUMOR` builds 20 cells, and the project will grow.

- **Keyframe count is bounded by construction.** Three F-curves per cell at six
  keyframes each is ~18 keys per cell. The membrane curve is a single Displace
  `strength` value — it does not displace vertices per frame on the CPU; Cycles
  and EEVEE both evaluate it on the GPU.
- **No per-frame handlers.** Nothing runs in Python during playback, so viewport
  scrubbing stays responsive and headless render is unaffected.
- **One shared texture.** All membranes in all three classes use the existing
  shared Clouds texture, so texture memory does not scale with cell count.
- **Immune cells get the same instanced treatment** as the rest, so a large
  immune population does not multiply mesh datablocks.

`validate_idle()` reports total keyframe count so growth is visible rather than
discovered during a slow render.

## Testing

`blender --background --python blender/scripts/animation_idle.py` builds a test
population, animates it, and validates. The checks:

1. **Loop is seamless** — for every animated channel, the value at frame 1
   equals the value at frame `LOOP_FRAMES + 1`. Compared by actually sampling
   the evaluated F-curves, not by trusting the construction.
2. **Tumour is not faster** — measured peak surface speed per class; tumour
   peak must be `<=` healthy peak. This is the constraint from the brief, and it
   is a measurement rather than a constant comparison, so it catches a
   regression introduced through any channel.
3. **Reduced motion is quieter** — peak displacement at `motion_scale=0.3` is
   `<=` that at `1.0`, and at `0.0` every channel is flat.
4. **Periods divide the loop** — no cell's period fails to divide 240.
5. **Cells vary** — at least two distinct periods and two distinct phases
   across the population, so the population is not synchronised.
6. **Rebuild is idempotent** — re-running leaves the same F-curve count and no
   `CYCLES`-stacked duplicates.
7. **Nothing outside the animation module moved** — the geometry object count is
   unchanged by animating, proving the layer adds keys, not objects.

Check 7 matters: it is the test that keeps this an *animation* feature and stops
it quietly becoming a geometry feature.

## Out of scope

- The six narrative beats in `animation_*.py`. Untouched.
- `density_at` / viral spread. The ECM hook is built and waiting; not used here.
- Ground substance and fibronectin/laminin in the ECM.
- Nucleus rotation as a separate axis from nucleus drift. Folded into the drift
  path; splitting it is a knob nobody has asked to turn.
