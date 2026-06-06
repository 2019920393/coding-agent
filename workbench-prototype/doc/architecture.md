# Codo Workbench Prototype

## Purpose

This prototype locks the first desktop workbench shape before any real Electron, React, Monaco, or backend integration.

The current direction follows a VSCode-like shell:

- left Activity Bar with only Explorer in v1
- left Explorer for the project tree
- center editor area for an empty state, code snippets, or the Agents Team page
- right AI assistant panel for conversation only
- bottom status bar for workspace state

## Current Layout

The default screen intentionally stays quiet:

1. Activity Bar shows only Explorer. Other workbench modes are intentionally deferred.
2. Explorer shows a familiar project tree: `src`, `public`, `App.tsx`, `main.tsx`, `index.html`, `package.json`, `tsconfig.json`, and `README.md`.
3. Main editor starts with a centered empty state. Clicking a file opens a static code snippet.
4. Right panel is a dedicated AI assistant chat with its own input box.
5. Agents Team is still available as an Explorer entry: `codo/agents.team`.

## Interaction Model

- The single Activity Bar button keeps Explorer selected and returns the center to the empty editor state.
- Explorer file rows open representative static code snippets in the editor.
- The `agents.team` Explorer entry opens the visual multi-agent collaboration page.
- The right AI assistant input supports focus state, ready send state, Enter to send, and Shift+Enter for a new line.
- Sending a message appends a local-only user bubble. No real AI request is made.

## Constraints

- Prototype files live only under `workbench-prototype/`.
- No `desktop` directory is scanned or modified.
- No Codo core logic is changed.
- No backend API, real file IO, or model call is connected in v1.

## Future Direction

After this shell is approved, the next technical step can be:

- `Electron` for the desktop window
- `React + TypeScript + Vite` for frontend state and components
- `Monaco Editor` for the center editor surface
- Python process bridge for Codo runtime events and tool calls
- real multi-agent state streamed into the Agents Team view
