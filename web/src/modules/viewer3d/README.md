# Viewer3D module

`modules/viewer3d` owns the reusable 3-D renderer and its in-memory navigation, clipping, explosion, measurement, tree visibility, and selection presentation. It does not fetch feature state, inspect account stores, or decide model access. Hosts adapt data and pass a role plus callbacks through `index.ts`.

The only supported imports are from `modules/viewer3d`:

```ts
import { UnifiedViewer, type UnifiedViewerProps, type ViewerToolbarSpec } from '../../modules/viewer3d';
import { PartTree, type PartTreeProps } from '../../modules/viewer3d';
```

`UnifiedViewerProps` accepts a stable `sourceKey` for the loaded artifact and a `resetKey` for the machine/user scope, either a host-built `glbUrl` or neutral `activeParts` mesh DTOs, a `toolbarSpec` (`'full'` or `'simplified'`), neutral tree nodes, optional controlled selection, localization strings, callbacks, and `hostExtras`. Active mesh DTO rows carry only key, family, colour, vertices, triangle faces, and optional opacity. A tree node carries a stable key, display label, optional colour swatch, scene-node names, nested nodes, initial visibility, and presence.

Camera projection, section axis/offset, explosion amount, measurement picks, and row visibility live in the viewer component's memory. `resetKey` or role changes reset tool state; changing only labels or loaded tree metadata does not. Hosts receive scene groups/ranges through `onLoaded`, measured distance through `onMeasure`, selected tree keys through `onSelectedKeyChange`, and load errors through `onError`. Hosts retain network lookup, permission decisions, catalog/material editing, downloads, mass summaries, and active mesh/business filtering.

`PartTree` is the minimal reusable tree view. Its rows contain names, colour dots, visibility eyes, and expansion arrows; the title and collapse control are the panel header. It accepts controlled `hiddenKeys` and `onToggle`, and optional controlled selection. It has no accounting badges, mass/material slots, isolate or show-all actions.

## M14 checklist reference

- Navigation/localisation stays shared while model, permission, and material meanings remain with hosts.
- Feature hosts import only this public entry and provide callbacks; the module imports no `components/*` or feature stores/hooks.
- Account/engineering state is supplied as neutral input and is never persisted by the viewer. Tool state is transient and scoped by `resetKey`.
- Access is decided by the host from server permission; simplified role cannot expose tree, section, explode, or measurement.
- Preserve request count, effect ordering, payload values/order, permission routing, stale-load cancellation, and host progress/error semantics.
- Preserve accessible/localized control labels and the EN/ZH string pairs; extracted UI cannot create consent behavior.
- Engineering state, accounting, and consent remain with their existing owners; formatting never turns estimates into solved values.
