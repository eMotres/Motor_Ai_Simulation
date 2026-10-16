# 3-D viewer control inventory

This inventory traces each visible control to its state and scene effect before unification. Statuses describe the old implementation; the decision column describes the unified viewer.

| Host / control | Wiring and scene effect | Status / decision |
|---|---|---|
| Geometry Active - orbit / zoom | Three Fiber OrbitControls update the active scene camera from pointer and touch input. | WORKS; replace with shared mapping. |
| Geometry Active - pan | OrbitControls pan with its default mouse and touch bindings. | WORKS; replace with shared mapping. |
| Geometry Active - FRONT cube | Viewcube dispatches `viewcubeNavigate`; ViewcubeNavigation moves the camera to a fixed distance around its target. | WORKS, but fixed distance can misframe; replace with shared clickable cube. |
| Geometry Active - ORTHO / PERSP | `cameraMode` selects AdaptiveCamera; fit effect reframes after camera replacement. | WORKS; retain as shared toggle. |
| Geometry Active - initial fit | FitCameraOnLoad frames the initial active geometry; no explicit Fit button was in this host bar. | WORKS on load; add shared Fit and Home. |
| Geometry Active - 2D / 3D mode | View2dToggle changes `renderMode`; MotorComponents selects flat or extruded geometry. | WORKS; scene-mode control, removed from the shared toolbar. |
| Geometry Active - grid / axes | `showGrid` and `showAxes` gate scene helpers. | WORKS; removed per request. |
| Geometry Active - MATTE / POLISHED / METALLIC and Metal / Rough / Env | MaterialControls updates material settings consumed by active meshes and environment lighting. | WORKS; removed per request. |
| Geometry Active - black square / solid | App sets `viewMode` to `solid`, choosing the active geometry render branch. | WORKS; removed per request. |
| Geometry Active - STL icon | App sets `viewMode` to `stl`, choosing the STL/point-cloud branch. | WORKS; removed with the active-only view-mode buttons. |
| Geometry Active - wrench | App calls `runPipeline(geometry)` to build active-part meshes. | WORKS; removed from viewer controls. |
| Geometry Active - layers / cache clear | App clears the STL cache, then invokes the pipeline. | WORKS; removed from viewer controls. |
| Geometry Active - tree collapse / expand | ComponentTreeView owns collapse and row expansion state. | WORKS; shared PartTree retains title collapse and child expansion. |
| Geometry Active - tree eyes | ComponentTree routes group and coil/magnet eye actions to active visibility state; the active mesh adapter consumes that state. | WORKS; module now owns visibility for rendered active parts. Absent/excluded rows are omitted. |
| Geometry Active - tree Isolate / Show All | ComponentTree binds isolate and show-all store actions; these update the visible active parts. | WORKS historically; removed from the minimal tree. |
| Geometry Active - tree selection | ComponentTree writes `selectedPart`; active mesh/tree highlight follows selection. | WORKS; retained through host selection callback for MaterialBar assignment. |
| Geometry Active - tree state badge | PartStateBadge cycles included/reference/excluded in usePartStates; the active adapter omits excluded meshes, and the accounting state also drives calculation/material rules. Shaft is locked as always included. | WORKS historically; removed from tree presentation. Existing part-state data and filtering remain host-owned. |
| Geometry Active - MaterialBar | MaterialBar opens and saves an assignment for the selected active part. | WORKS; remains a host-specific extra beside the shared viewer. |
| Geometry Full - orbit / pan / zoom | ModelViewer's OrbitControls drive the GLB camera. | WORKS; use the same shared engine and mapping. |
| Geometry Full - Fit | Host toolbar calls ModelCanvasHandle.fit, then ModelViewer.fit. | WORKS; moved to shared toolbar. |
| Geometry Full - tree collapse / expand | ComponentTreeView owns collapse and row expansion state. | WORKS; shared PartTree owns title collapse and child expansion. |
| Geometry Full - tree eyes | useAssemblyTree produces hidden GLB group/instance names consumed by ModelViewer.apply. | WORKS; module now maps tree visibility to loaded GLB nodes. |
| Geometry Full - tree Isolate / Show All | useAssemblyTree binds these actions to hidden group sets. | WORKS historically; removed from the minimal tree. |
| Geometry Full - tree selection | useAssemblyTree produces GLB highlight names consumed by ModelViewer.apply. | WORKS; retained through shared scene selection/highlighting. |
| Geometry Full - tree state badge | PartStateBadge cycles accounting state through usePartStates; excluded parts are removed from the model/tree data and calculation state. Shaft remains locked. | WORKS historically; accounting controls/tags are removed from tree presentation. |
| Geometry Full - cube / projection / section / explode / measure | These controls were absent from this host's toolbar. | ABSENT; now supplied by the shared viewer for full access. |
| Geometry Full - files / model picker / NDA / download / info | FilesMenu and host actions select the model and perform permitted file and download operations. | WORKS; stays in the host bar. |
| Geometry Full - CAD drawer / kit / rebuild | Host controls open CAD parameters, display kit state, and invoke the rebuild action. | WORKS; stays in the host bar. |
| Materials Full - orbit / pan / zoom | ModelCanvas and ModelViewer control the GLB camera. | WORKS; use the shared engine and mapping. |
| Materials Full - Fit / cube / projection / section / explode / measure | No toolbar controls were supplied by this host. | ABSENT; now supplied by the shared viewer for full access. |
| Materials Full - tree collapse / expand | ComponentTreeView owns row and tree presentation state. | WORKS; shared PartTree owns title collapse and child expansion. |
| Materials Full - tree eyes / Isolate / Show All / selection | useAssemblyTree binds hidden and selected GLB names to ModelViewer.apply. | WORKS; visibility/selection retained, Isolate and Show All removed. |
| Materials Full - tree state badge | PartStateBadge changes included/reference/excluded accounting state, which is consumed by materials and calculation data. Shaft remains locked. | WORKS historically; removed from tree presentation. Mass and selected-part material picker remain in the host bar. |
| Materials Full - material picker | Tree row action opens MaterialPickerPopover; selection persists through useFullParts. | WORKS; remains host-specific. |
| Materials Full - mass and status | Mass values and availability/status notes are display-only data from the parts payload. | WORKS; remains in the host bar/summary. |
| 3-D model tab - orbit / pan / zoom | ModelViewer OrbitControls update the GLB camera. | WORKS; use shared engine and mapping. |
| 3-D model tab - Fit | Host toolbar calls ModelCanvasHandle.fit, then ModelViewer.fit. | WORKS; moved to shared toolbar. |
| 3-D model tab - explode | Host state feeds effectiveSettings and ModelViewer.apply; GLB group factors shift each group. | WORKS; retained and combined with radial active-part explode. |
| 3-D model tab - section axis / offset | Host state feeds effectiveSettings and the GLB clipping plane. | WORKS; retained with common axis math. Cuts are uncapped. |
| 3-D model tab - measure | ModelViewer ray-picks two points and reports their distance to the host. | WORKS; retained in the shared toolbar with a shared readout. |
| 3-D model tab - tree collapse / expand | ComponentTreeView owns collapse and row expansion state. | WORKS; shared PartTree owns title collapse and child expansion. |
| 3-D model tab - tree eyes / Isolate / Show All / selection | useAssemblyTree binds hidden and selected GLB names to ModelViewer.apply. | WORKS for full access; visibility/selection retained, Isolate and Show All removed. |
| 3-D model tab - tree state badge | PartStateBadge cycles accounting state; excluded parts are excluded from tree/model data and calculation state. Shaft remains locked. | WORKS historically; removed from tree presentation. |
| 3-D model tab - picker / downloads / signed NDA / sign / verification / info | Host handlers select models, request permitted files, run NDA actions, show access status, and display model metadata. | WORKS; stays in the host bar. |
| Simplified role - tree / section / explode / measure | model3dToolbar and effectiveSettings suppress full tools when `can_download` is false. | WORKS; shared policy leaves navigation, cube, Fit, Home, and projection. |
| Shared cap faces | Existing section behavior clips geometry but does not generate cap meshes. | ABSENT; section cuts are intentionally uncapped. |

## Implementation decision

All four model hosts render through `modules/viewer3d` and import only its public index. Geometry Active passes neutral extruded mesh DTOs; the module builds its scene locally without a GLB request. Geometry Full, Materials Full, and the 3-D model tab pass GLB URL/version data. Full access exposes one minimal tree (names, colour dots, eyes, expand arrows), section, explode, and measurement. Simplified access exposes navigation, cube, Fit, Home, and projection. Isolate, Show All, mass/tags/state badges, and injected row content are absent from the shared tree; Materials keeps mass and material editing in its host bar. Section cuts remain uncapped. Host Files, CAD, NDA and download actions stay in host bars.
