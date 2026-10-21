---
name: brain_map
description: Render and send a PNG map of the Obsidian vault's notes and their [[wikilinks]].
---

# Brain map skill

- `export_brain_map()` takes no arguments. It reads every note in the vault (remote vault
  first, falling back to the local one), builds a graph from real `[[wikilinks]]` only, and
  renders a deterministic PNG -- it never invents relationships or calls an image model.
- Only call this when explicitly asked to show, render, or export the brain/vault map.
