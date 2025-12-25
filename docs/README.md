Generating SVG from the Mermaid diagrams

Files:
- `docs/detection_fsm.mmd` — Mermaid state diagram for Detection FSM
- `docs/detection_sequence.mmd` — Mermaid sequence diagram for Detection lifecycle

Requirements
- Node.js (for npx)
- Optional: Docker

Option A — quick via npx (recommended if Node.js is installed)

1. From PowerShell in the project root run:

```pwsh
# render FSM -> SVG
npx @mermaid-js/mermaid-cli -i docs/detection_fsm.mmd -o docs/detection_fsm.svg
# render Sequence -> SVG
npx @mermaid-js/mermaid-cli -i docs/detection_sequence.mmd -o docs/detection_sequence.svg
```

This uses the mermaid CLI to convert .mmd to .svg files in `docs/`.

Option B — using Docker (if you prefer)

```pwsh
# pull mermaid CLI image (one-off)
docker run --rm -v ${PWD}:/data minlag/mermaid -i /data/docs/detection_fsm.mmd -o /data/docs/detection_fsm.svg
docker run --rm -v ${PWD}:/data minlag/mermaid -i /data/docs/detection_sequence.mmd -o /data/docs/detection_sequence.svg
```

Option C — online
- Paste the Mermaid source into https://mermaid.live/ and export SVG from the editor.

If you want, I can also generate the SVGs here and add them to the repo — but that requires a mermaid renderer available in this environment. If you prefer I produce the actual SVG files for you, tell me and I'll try to run mermaid-cli here; otherwise follow the above commands locally and you'll have `docs/detection_fsm.svg` and `docs/detection_sequence.svg`.