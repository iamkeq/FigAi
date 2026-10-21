---
name: obs
description: Control OBS Studio over its WebSocket server -- check recording/streaming status, start/stop either, and switch scenes.
---

# OBS skill

Only available when `OBS_WS_HOST` is configured in `.env` (OBS 28+'s built-in
WebSocket server, enabled under Tools > WebSocket Server Settings). If it isn't
configured, or OBS isn't running, every tool here returns a clear error instead
of hanging or crashing.

- `obs_status()` -- recording/streaming state, current scene, and the scene list.
- `obs_start_recording()` / `obs_stop_recording()`.
- `obs_start_streaming()` / `obs_stop_streaming()`.
- `obs_switch_scene(scene_name)` -- use a scene name from `obs_status()`'s scene list.

These are low-stakes, reversible controls -- no confirmation needed before running them.
