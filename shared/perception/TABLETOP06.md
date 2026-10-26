# Experiment06: calibration, selection and grasp preview

`explorer_shared.tabletop_grounding` is source-only software for the two SO-101
arms on the Pi tabletop. Experiment03 keeps the ROSMASTER target and its Nano
camera; a Pi image does not establish visibility of that robot. No function in
this module opens a camera, a serial port or an actuator transport. The latest
owner rule requires a separate user-observed test before **either** arm moves.
All examples/tests here are synthetic, and every plan has native execution off.

## Existing-source integration

`CachedFrame` takes the exact JPEG bytes and metadata from the existing single
camera owner's cache. Supply its camera/frame ID, original clock domain/boot,
receive/exposure timestamp basis and dimensions. Hashes bind a click, mask,
calibration and later preview to that image. A changed frame is rejected rather
than quietly reusing an old track pose. No second V4L2/GStreamer camera source is
created by this code.

`TableCalibration(original_bytes, source_payloads)` validates an identified
measured or explicitly synthetic `explorer_tabletop_calibration_v1` artifact:

- camera/robot/base/mount identities and exact image dimensions;
- metric units, pinhole intrinsics, five Brown-Conrady distortion coefficients;
- proper rigid `base_from_camera` and `base_from_table` transforms;
- workspace polygon in table XY, clock/boot validity interval;
- pixel/translation/rotation/plane uncertainty terms and fit residual;
- SHA256-verified original fiducial/calibration source files.

The library does not invent a physical calibration from CAD or the current JPEG.
Moving the mount/table/base requires a new measured artifact. The artifact's
declared validity and source hashes are not independent physical attestation.
Malformed transforms, nonconvergent undistortion, rays behind/parallel to the
table and points outside the calibrated polygon are rejected.

`metric_grid(cell_size_m)` projects metric table cells into the original image.
Every cell starts with occupancy `UNCONFIRMED`; the grid is not an obstacle map.
`select_target(frame, observation, pixel, purpose=...)` binds the selection to an
actual full-resolution Boolean instance mask and track-session ID. It refuses
missing masks, unresolved overlapping instances and placement on a detected
mask. `purpose` is object, receptacle or placement_cell. No-mask-at-click means
only that no supplied detection mask covers that pixel; occlusion/empty space
are not thereby proven. A caller can disambiguate overlapping tracks explicitly.

`ground_selection(...)` checks frame freshness in the same original clock/boot
as the calibration. A placement cell can intersect the measured table plane.
An object/receptacle needs actual aligned depth; RGB and VLM text cannot provide
its Z or grasp contact point. `AlignedDepth` accepts existing original uint16-mm
or float32-m cache bytes, validates dimensions and reads the selected pixel.
It requires the exact RGB frame ID/hash, matching clock/boot and an explicit
allowed RGB-depth skew. Zero/NaN depth remains unavailable. The output preserves
geometry-source hashes and a declared uncertainty estimate, and distinguishes
table placement surface from observed object surface. The uncertainty sum is
labelled as a model of the supplied terms, not a calibrated confidence interval.

The tabletop Pi webcam currently has no measured depth source. Accordingly,
this implementation alone does not produce object grasp Z from its RGB image.
Measured object/contact geometry or another qualified metric observation must
be added explicitly; no RGB-only Z default exists.

## Deterministic geometric baseline

`preview_grasp(chain, initial_positions, grounding, ...)` uses the existing real
`URDFChain.forward/inverse`, named joints and unchanged URDF limits. It checks
that the selected robot and base frame match that chain. The caller must supply
the actual initial aperture, identified tool/contact/aperture geometry, tool
orientation, contact offset, approach/lift distances and workspace. None is
chosen by the VLM or silently inferred from joint indices.

The six preview phases are pregrasp → open → descend → close → lift → hold.
Each reports the actual numerical IK result and residuals. Interpolated joint
and aperture paths are checked against the declared tool workspace and an
injected collision model, with source-model hashes in every receipt. Missing
collision evidence, an obstacle or an unreachable target blocks readiness.
The scope remains sampled paths in that named model, not continuous physical
clearance or confirmed contact. There is no force/holding success inference.

Even a complete synthetic preview returns `shadow_only=true`,
`native_execution_enabled=false`, and reasons requiring separate observed
qualification. It contains joint-space preview solutions, never a native servo
dispatch or an enabled physical policy. The real URDF tests establish geometric
computation only. No arm or chassis movement was used for these tests.

## Fresh Pi inventory and official compatibility, 2026-10-26

Read-only SSH verified Pi Python3.13.5/aarch64, HailoRT5.1.1, TAPPAS5.1.0 and
the existing observer active. That single process owns the camera, Hailo and
both SO-101 serial devices; it was not stopped or reconfigured. An existing
`yolov5n_seg_h10.hef` was parsed without inference: compatible HAILO10H/HAILO15H,
640×640 RGB and four raw segmentation output heads, SHA256
`edb7f33aa7212a24dca0090cfca9b83ca72cc81b7385a3225ca2cd465092b9ed`.
The active detection observer is not by itself proof of instance masks.

Primary upstream sources inspected and pinned:

| Source | Commit | Relevant fact |
| --- | --- | --- |
| [Hailo archived segmentation example](https://github.com/hailo-ai/Hailo-Application-Code-Examples/blob/7b850e0444a142ada88443d675e3c3ddda18bd74/runtime/python/instance_segmentation/README.md) | 7b850e0444a142ada88443d675e3c3ddda18bd74 | Hailo10H/5.1.1 is an explicitly tested combination |
| [Archived GenAI model catalog](https://github.com/hailo-ai/hailo_model_zoo_genai/blob/eb3270c386752379465ccd35c360a115eb456cba/docs/MODELS.rst) | eb3270c386752379465ccd35c360a115eb456cba | Qwen2-VL2B has a version5.1.1 artifact |
| [Current GenAI catalog](https://github.com/hailo-ai/hailo_model_zoo_genai/blob/187484184ff8bdfb04f035b30d6be2bbf4fcb57f/docs/MODELS.rst) | 187484184ff8bdfb04f035b30d6be2bbf4fcb57f | Current Qwen2/3 artifacts have later runtime requirements; do not substitute them |
| [Current Hailo GenAI API example](https://github.com/hailo-ai/hailo-apps/blob/c61f843b618ea2faa20dad79097c0591900319f4/.github/instructions/gen-ai-development.md) | c61f843b618ea2faa20dad79097c0591900319f4 | VLM API, RGB input and separate inference worker |

The Pi's actual Python5.1.1 package was also inspected without creating a
VDevice: it exports VLM, generate/generate_all, clear_context and release. The
latest generic app installation is not assumed compatible with this stack.
No driver upgrade is part of this work.

The official archived Qwen2 artifact is separately staged with exact URL, size,
SHA256 and download/transfer receipts outside Git. Downloading is distinct from
inference. VLM execution must first coordinate release of the current CV model
inside the owning observer while camera and serial owners keep running; a new
concurrent independent Hailo owner is not silently started. The event worker
must use a frozen cached source image, bounded/coalesced requests and discard
late cancelled results. VLM output is semantic text only, never metric geometry,
contact evidence or motor commands. Actual inference, mask integration, measured
table calibration, supervised grasp qualification and UI wiring are separate
remaining steps; this document does not claim them complete.
