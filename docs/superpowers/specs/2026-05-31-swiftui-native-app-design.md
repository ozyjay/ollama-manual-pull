# SwiftUI Native App Design

## Context

`ollamapull` now has a native multi-file SwiftUI macOS app that talks to a local Python HTTP server. The Python downloader continues to handle the safety-critical behavior: resumable downloads, SHA-256 verification, blob reuse, and manifest installation after all referenced blobs verify.

This migration was driven by several sharp edges in the previous generated native app:

- Standard macOS commands such as `Command-Q` were not available.
- The visible UI refreshed every second and appeared to clear or rebuild itself.
- Start and pause controls could scroll out of reach while downloads were active.
- The same download could appear multiple times in the queue.
- The generated Swift template mixed process startup, networking, state management, and all views.

The selected direction is a proper SwiftUI macOS app that keeps Python as the downloader engine.

## Goals

- Keep the native app in a small, maintainable SwiftUI app structure.
- Keep the Python downloader and local queue API as the engine for this migration.
- Make the queue UI stable while state refreshes.
- Ensure pause and start actions are always reachable.
- Add standard macOS menu behavior, including `Command-Q`.
- Prevent duplicate queued downloads by canonical model reference.
- Preserve existing CLI and web UI behavior during this migration.

## Non-Goals

- Porting downloader, registry, checksum, and manifest logic to Swift in this phase.
- Supporting multiple simultaneous downloads.
- Interrupting an in-flight blob transfer for immediate pause.
- Replacing the existing Python tests with Swift tests.
- Redesigning model search beyond what is needed to make the native app stable.

## User Experience

The native app should feel like a compact operational macOS utility, not a web page recreated in SwiftUI.

The window uses three regions:

- A left sidebar for high-level sections: queue, installed models, and search/add model.
- A central work area focused on the active download and the queue list.
- A right inspector for details about the selected queue item.

Primary controls sit in a fixed bottom command bar, outside scrollable content:

- Start Queue.
- Pause After Current.
- Add Model.
- Retry failed item.
- Remove waiting, completed, or failed item.

`Pause After Current` remains visible and enabled whenever the queue is running. The label should make clear that it does not abort the active blob write.

The queue list should preserve scroll position and selection during refreshes. Rows should not flicker, duplicate, or jump when progress changes. Progress updates should change only the affected values, progress bars, and status labels.

## Architecture

The macOS app should be split into focused Swift files under `macos/OllamaPull/`:

- `OllamaPullApp.swift`: app entry point, scene setup, command menus.
- `PythonServerSupervisor.swift`: launches and terminates the bundled Python server.
- `AppStore.swift`: observable application state, selection, refresh scheduling, and user actions.
- `APIClient.swift`: typed HTTP client for the local API.
- `Models.swift`: decodable API payloads and Swift-only view models.
- `ContentView.swift`: top-level layout.
- `SidebarView.swift`: app navigation.
- `QueueView.swift`: active download and queue list.
- `QueueRowView.swift`: stable row rendering.
- `InspectorView.swift`: selected item details.
- `SearchView.swift`: model search and direct add controls.
- `InstalledModelsView.swift`: installed model list.
- `Formatters.swift`: byte, ETA, date, and status formatting.

The build script continues to generate a macOS `.app`, but now compiles multiple Swift source files instead of writing all native code into a generated resource Swift file.

Python remains packaged in app resources as it is today:

1. The macOS app starts the Python local server with `Process`.
2. The Python server prints its bound local URL.
3. The Swift app connects through `APIClient`.
4. Swift polls state through typed endpoints.
5. Python owns queue execution, downloader progress, and filesystem writes.

## State And Refresh

The Swift app should treat `/api/state` as the source of truth but avoid wholesale UI resets.

`AppStore` should maintain:

- Current server URL and readiness state.
- Latest snapshot.
- Selected queue item ID.
- Search text, search results, and search state.
- Last visible app error.
- Refresh task lifecycle.

Refresh should use a single managed loop or timer. It must be cancellable when the app terminates and should avoid overlapping requests. If a refresh is still in flight, the next tick should skip instead of starting another request.

Selection rules:

- Preserve the selected item if it still exists.
- If selection disappears, select the active item.
- If no active item exists, select the first queue item.
- If the queue is empty, clear selection.

SwiftUI rows should use stable queue item IDs. Item IDs come from the server, but duplicate prevention should be based on canonical model reference so that a model cannot be added repeatedly just because it receives new queue IDs.

## Duplicate Download Prevention

Duplicate prevention belongs on the Python queue boundary so every client benefits.

When adding a model:

1. Parse and canonicalize the model reference with the existing model parser.
2. Convert implicit tags to explicit tags, such as `qwen3-coder` to `qwen3-coder:latest`.
3. Compare against queued or running items by canonical reference.
4. If an item already exists in `waiting`, `running`, or `completed`, return the existing item instead of appending a duplicate.
5. If a matching item is `failed`, return the failed item and let the user choose Retry.

The API should make this visible to the native client by adding `deduplicated: true` to the returned queue item when `/api/queue` reuses an existing item. Existing web and native clients that only read `id`, `model`, and `status` can ignore the extra field. The native app should show a small non-blocking message such as `Already in queue: qwen3-coder:30b`.

## Command And Menu Behavior

The app must use a standard SwiftUI `App` scene with command definitions so macOS handles expected menu items.

Required commands:

- Quit through `Command-Q`.
- Start Queue.
- Pause After Current.
- Retry Selected Item when selected item is failed.
- Remove Selected Item when selected item is not running.
- Refresh.

Commands should call the same `AppStore` actions as visible buttons. Disabled states must match the visible UI.

## Error Handling

Startup errors should be shown in a persistent banner with enough detail to act on:

- Missing bundled resources.
- Python interpreter unavailable.
- Python server startup failure.
- Invalid server URL emitted by the Python process.

Runtime API errors should not clear the current snapshot. The app should keep showing the last known state and display a non-blocking error message.

Search failures should not block direct model entry.

Queue errors should be tied to the action that failed:

- Queue failed.
- Start failed.
- Pause failed.
- Retry failed.
- Remove failed.

## Testing

Python tests should cover:

- Canonical model reference formatting.
- Duplicate add returns the existing item.
- Duplicate detection works for implicit `latest`.
- Failed duplicate returns the failed item without adding another row.
- Existing queue behavior remains one active download at a time.
- Pause after current still stops before the next waiting item.

Swift verification should cover:

- The app source compiles as multiple Swift files.
- The app bundle still includes Python resources.
- The generated app no longer depends on `WKWebView`.
- Menu commands exist in the Swift source.
- The source includes a fixed bottom command bar outside scrollable content.
- `AppStore` refresh logic avoids overlapping refreshes.

Manual smoke verification should cover:

- Launch app from Finder or `open`.
- Confirm `Command-Q` quits the app.
- Queue the same model twice and confirm only one queue row appears.
- Start a fake or small download and confirm the pause button remains visible while scrolling.
- Confirm progress updates do not reset scroll position or selection.

## Migration Strategy

Implement this in small phases:

1. Add queue canonicalization and duplicate prevention to Python, with tests.
2. Split Swift source into a real native app structure while preserving current behavior.
3. Add standard app commands and fixed command bar.
4. Replace broad refresh/reassignment behavior with a managed `AppStore` refresh loop.
5. Polish the queue, inspector, and search views around stable selection and row identity.
6. Update build and install tests to compile and package the new Swift source layout.

This keeps the downloader stable while making the macOS app reliable enough for long downloads.
