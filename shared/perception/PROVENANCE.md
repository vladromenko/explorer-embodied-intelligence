# Pi perception: implementation provenance

The HailoRT 5 InferModel adapter, letterbox geometry, COCO class order and NMS
decoding derive from the owner's existing
`explorer-pi:/home/vlados/MILO_Final/milo_next/perception.py`. The relevant module
was copied privately before editing; SHA-256:
`a6a316d8589525505a15d363b61902745ce0012941d477de48a8823df07874af`.
The complete legacy module and its private runtime configuration are not imported
into this repository. Face recognition, audio and robot commands are excluded.

The new agent, authenticated HTTP surface, durable recording manifests, file
artifact adapter, event delivery and worker separation are Explorer glue. IoU
association supplies session-local track IDs; it does not establish identity,
force, physical contact, object possession or successful handover.

Read-only device audit on 2026-10-26 confirmed Hailo-10H driver/runtime/firmware
5.1.1. A synthetic blank-image `yolov8m_h10.hef` smoke test passed at
27.0/25.4/25.25 ms. This is an execution check, not detection accuracy evidence.
The C920 supplied 12 successful 640×480 frames with median receive interval
32.03 ms; those audit frames were discarded. Neither arm serial bus was opened.

The new implementation has software tests without hardware. Its deployment and
physical device acceptance remain separate steps owned by the integrator.
