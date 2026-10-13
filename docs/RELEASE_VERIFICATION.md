
# Release verification: 2026.10.13.02

**2026.10.13.02 = `.13.01` plus three fixes measured live on `.13.01` on 2026-10-13** (everything below that names this release applies to it; `.13.01` is named short):

* **D-A-4, a reload closed the wrong document.** `lib_reload_library library_path=X` with another document Y focused closed Y (an unmodified SchLib): `Lib_ReloadLibrary` ran `WorkspaceManager:CloseObject` with `FileName`=X, and CloseObject acts on the FOCUSED document whatever FileName says. `RefreshSchLibFromDisk` had the same close. Both now close X's own server document through `CloseServerDocByObject` (Application.pas, `App_CloseDocument`'s route: `Client.CloseDocument`, then `Client.GetDocumentByPath` again). The reload refuses `MODIFIED` (nothing closed) when the library reads modified or this bridge edited it and has not written it, answers `CLOSE_FAILED` when it is still open after the close, only opens a library that was not open, and replies `was_open`, `closed` and `other_documents_closed` (open document paths before and after, `OpenDocumentPaths` / `PathsGoneOtherThan`). Probe P54. `tests/test_close_by_object.py`.
* **D-A-1, generic component creates wrote OwnerPartId -1.** `obj_create` / `obj_batch_create` with `container=component` saved the primitive with `OwnerPartId=-1` (`IsNotAccesible=T`), while `lib_add_symbol_lines` wrote the symbol's current part. Both now read the owner part and display mode from the narrowed component before the PreProcess (`OwnerPartIdOf` / `OwnerDisplayModeOf`, as every `Lib_Add*` does) and write them with `SetOwnerPartUnlessGiven` (Library.pas) before `AddSchObject`, unless the caller's properties set that field.
* **D-A-2, obj_count ignored a lib_component scope.** `Gen_GetObjectCount` parsed the scope and never called `ApplyLibComponentScope`, so `lib_component:TP` counted whatever symbol was current, not TP. It now applies it exactly as `obj_query` does (same `NOT_FOUND`), and a PCB type with any scope but `active_doc` is refused `SCOPE_NOT_SUPPORTED` as the query refuses it. The second measurement (0 even with TP current and `active_doc`) was D-A-1: the SchLib iterator yields the current part's primitives only, so the -1-owned lines were invisible; D-A-1's fix resolves it. Re-check: `obj_count object_type=eLine scope=lib_component:TP` on a symbol drawn with `obj_create container=component` answers the number drawn.

**`.13.01` = `.12.03` (the carrier-local line: sections 22 to 39, whose numbers are cited elsewhere and are unchanged) merged with upstream `.12.1` (upstream's own sections 22 to 36, renumbered here as sections 40 to 54, in the same order, with every cross-reference to them renumbered too).** Nothing in either line was dropped. The next live run is on 2026.10.13.02: section 39 ("The next live run") first, then the upstream sections 40 to 54 that record no live result.

`.12.03` was the previous carrier-local release (`.12.02`) plus the fixes from its live run on 2026-10-12 (section 39, "Results 2026-10-12 (the .12.02 build)"):

* **D-F2-1, the P8 halt.** In a freshly restarted Altium with nothing created or selected through the bridge, `lib_add_symbol_lines` with no `component_name` halted the engine ("Undeclared identifier: CurrentPartID", Library.pas `OwnerPartIdOf`) and the loop died. Cause, read from the source (session A below is its live proof): the four held-symbol globals (`LastCreatedLibComponent` and its name, document and path) were never assigned when the pump started. In DelphiScript an unassigned global is Empty, and Empty does NOT compare equal to Nil, so every `= Nil` guard let it through and the member read ran on Empty. A new `ResetSessionGlobals` (StatusForm.pas), called by `StartMCPPump` before anything reads state and by `RunSelfTest` (which runs handlers without the pump), assigns them (through `ForgetRememberedLibComponent`: `:= Nil` / `:= ''`) and EVERY other global the request path reads (`RefreshingLib`, `LastCompiledProject`, `LastCompileTick`, the save-pass counters, `DocNameMemberKeys`, the next-step and property-diagnostic buffers, the status form's in-flight fields and flags, and the four perf lists, which were Nil-tested by `EnsureStatusBuffers` - a test that does nothing on Empty - and created only inside `ShowStatusForm`'s Try), each as a top-level statement. `tests/test_live_fresh_pump_globals.py` executes `StartMCPPump` on a fixture whose globals start Empty-not-Nil, and its guard WALKS `StartMCPPump` in execution order (callees inlined, up to the statement that arms the poll timer): a global read before an unconditional assignment, or assigned only under an If, a loop, a Try body or after a conditional Exit (the `If X = Nil Then X := ...` shape included), fails it; negative tests run the guard on those injected shapes. USER-VISIBLE: a symbol held by `lib_create_symbol` / `lib_set_current_component` no longer survives a StartMCPServer re-run (or an Altium restart), so after a recovery the next nameless symbol edit answers `NO_COMPONENT`: pass `component_name` or call `lib_set_current_component` again. The mechanism is consistent with the 2026-10-11 P8 halt (Library.pas:122) that was blamed on the editor's `CurrentSchComponent`, to be confirmed by session A (P8 on 2026.10.13.02); the editor's-selection diagnosis is now UNPROVEN (the reads of it stay removed: harmless and conservative).
* **D-TOOL-1.** `last_fault.when` is now the FIRST sighting (`first_seen`); later failed keep-alives move `last_seen` only. `found_by` says how it was found, and a dead loop found by its box carries "script_fatal box seen" with guidance for that case (note the line, press OK, Stop, rerun StartMCPServer) instead of the generic "probably not running". A sighting in a NEW pump session (a newer `_session_start` row in `activity.log`, stored as `session`) starts a new record, so a loop that dies again on the first call after a recovery does not inherit the old incident's time and box guidance.
* **D-TOOL-2.** `app_read_panel` / `app_list_panels` enumerate EDGE-TAB panels only (`enumerates: edge_tabs_only` in every reply); a panel docked open in a pane group (SCH Library on the left, F2) is not found yet, and the reply now says so instead of reading as "closed". `app_list_panels` no longer raises KeyError (`holder_class`) when panels are listed.
* **39.3 item 1.** `app_get_preferences` and `app_exit_active_command` report `sch_server_loaded`; on AD 26.10.1.5 the schematic server was already registered in a fresh session with no schematic document (when it loads - at startup, through the Projects panel or through the `Altium_API.PrjScr` script project - was not measured), so `sch_post_process` reads true there (item 1 corrected).

`.12.02` was `.12.01` plus one rename: `Lib_UninstallLibrary`'s local `Stored` (refused by the compiler at StartMCPServer; Delphi reads `stored` as a property directive, like `index`) is `InstalledAs`, and `scripts/altium/lint.py` refuses `Stored` and `Index` as declared names. Where a row below says what 2026.10.13.02 does, the change landed in `.12.01`, `.12.02` or `.12.03` unless it names one of the items above; it is carried unchanged. NAMING: "the .03 build" below is the third 2026-10-11 build (run live early on 2026-10-12), "the .12.02 build" the release before `.12.03`; this release (2026.10.13.02) is always named in full.

Unless a section records live verification explicitly, the Pascal below has
been checked by the linter (and by FPC where a section says so) but **not
executed by Altium's DelphiScript engine**. FPC is not available on every
build machine: the 2026.10.06.x build in step 32 was NOT checked by FPC. The two are not the
same: each accepts identifiers the other rejects, and an undeclared one
faults at runtime where `Try/Except` cannot catch it, halting the
polling loop.

Work top to bottom. Step 1 needs nothing but Altium and takes seconds,
and is the step most likely to catch a compile-level problem. If it
fails, stop and fix that before running the rest.

**No step may put up an Altium dialog.** Every refusal below must answer
without one, and `app_ping`'s `caught_exceptions` must not rise across
it. Section 39 says how to read that counter around each section, and
lists the few calls that are run on purpose to see whether they raise.

If there is only time for some of it, the risk is not evenly spread.
What separates the steps is whether the Altium property being written
is already written somewhere in shipped code, because a property that
works elsewhere cannot be an undeclared identifier:

| Step | Property | Written elsewhere? | Risk |
|---|---|---|---|
| 5, 3D placement | `StandoffHeight` | only by step 15, itself unverified | highest |
| 5, 3D placement | `SetState` on a model | no, nowhere | highest, and it can stop the loop |
| 2, pin edges | `Symbol_OuterEdge`, `Symbol_InnerEdge` | no, nowhere | high, and can stop the loop |
| 7, enum words | `StrToPinElectrical` | yes, `Lib_AddPins` | medium |
| 4, DNP paste | `PasteMaskExpansion` | yes, `PCB_MakePasteGrid` | low, but it edits the board |
| 3, filled body | `AreaColor`, `IsSolid` | yes, `Generic.pas` | low |
| 5, 3D placement | `MoveByXY` | yes, `PCB_ReplicateLayout` | low |
| 11, copy and rename | `LibReference` | yes, `Lib_CreateSymbol` | low |
| 12, delete variant | `DM_RemoveProjectVariant` | no, nowhere | highest, and it destroys work |
| 13, sheet symbol filename | `Text` on a sheet symbol's sub-object | yes, on other objects | medium |
| 14, polygon pour options | `RemoveDead`, `RemoveNarrowNecks`, `RemoveIslandsByArea` | new here, and in two places at once | high, and it can stop the loop |
| 15, 3D body on a board | `StandoffHeight` on a free body | only by step 5, itself unverified | highest, shared with step 5 |
| 15, 3D body on a board | `SetState` on a model | only by step 5, itself unverified | highest, shared with step 5 |
| 22, replace placeholders | `RemovePCBObject` on a footprint | yes, `Lib_DeleteFootprintPrimitives` | low, but it deletes bodies |
| 16, region kind | `Kind` on a region | read by four reference scripts, written by none | high, and it can stop the loop |
| 17, component UniqueId | `UniqueId` on a placed component | no, nowhere | highest, and a wrong id costs the next Update PCB |
| 18, pin owner part | `OwnerPartId` | yes, by two independent reference scripts | medium, behavioural not declarative |
| 6, mirrored text | `MirrorFlag` | yes, `PCB.pas` | lowest |
| 20, bulk vias | `Size`, `HoleSize`, `LowLayer`, `HighLayer` on a via | yes, `PCB_PlaceVia` | low |
| 20, track width | `Width` on a track, now in fractions of a mil | yes, `PCB_PlaceTrack` | low |
| 23, pad editing | `RemovePCBObject` | yes, `Lib_DeleteFootprintPrimitives` | medium: it deletes, and whether removing a session-added pad's second listing reaches disk is unmeasured |
| 23, pad editing | `SetState_StackShapeOnLayer` | yes, `Library.pas` | medium: the through-hole fix hands the method top, bottom and inner layers it was never given before |
| 23, pad editing | `HoleType` | yes, `Lib_AddFootprintPads` | low: `eRoundHole` is a new value for it |
| 23, pad editing | `Description` | yes, `Lib_CreateFootprint` | low: written on an existing footprint for the first time |
| 24, footprint keep-out | `IsKeepout` | read in `PCB.pas`, written nowhere else | high, and it can stop the loop |
| 24, region kind in a footprint | `Kind` | only by step 16, itself unverified | high, shared with step 16 |
| 24, region outline in a footprint | `SetOutlineContour` | yes, `PCB_PlaceRegion` | medium, first use in a library |
| 24, fill in a footprint | `X1Location` | yes, `PCB_PlaceFill` | low |
| 24, choosing the footprint | `CurrentComponent` | yes, `Lib_SetDesignator` | low |
| 24, delete from the library board | `RemovePCBObject` | yes, `PCBGeneric.pas` on boards | medium, first use on a library board; reached only with `remove_from_board=true` |
| 25, component kind | `ComponentKind` | yes, `Gen_SetNetTie` | low for the net-tie kinds, medium for the five written by ordinal |
| 26, pin properties | `Designator` | yes, `Lib_AddPins` | low |
| 27, current model | `IsCurrent` | yes, `Lib_NormalizeImplementations` | low |
| 28, No-ERC on a pin | `Location` | yes, `Gen_PlaceNetLabel` | low |
| 30, batch rename | `Name` on a footprint | yes, `Lib_RenameFootprint` | low |
| 31, STEP Z offset | `SetState` on a model, now with a non-zero Dz | yes, `Body3D_ApplyModelState` ran it live with Dz 0 | settled 2026-10-05: Dz 3 stored and built into both heights (+3 mil exactly) |
| 31, delete diagnostics | `BoardIterator_Destroy` (the library-board walk), read-only | yes, `LibBoardRemoveEntries` ran it live | low |
| 36, Description to the component | `ComponentDescription` | yes, `Lib_SetComponentDescription` | low: first write on a PLACED component |
| 36, new parameter hidden | `IsHidden` | yes, `Generic.pas` (on parameters, run live) | low |
| 36, new parameter on its component | `Location` | yes, `Generic.pas` | low: what is new is reading `Comp.Location` into a parameter before it is added |
| 36, shown new parameter, value only | `ShowName` | yes, `Lib_SetPinProperties` (on pins only) | medium, first write on a parameter; reached only with `visible=true` |
| 36, shown new parameter, turned with its Comment label | `Orientation` | yes, `Generic.pas` (net labels, power ports) | medium, first write on a parameter; reached only with `visible=true`; the value now comes from `Comp.Comment.Orientation` (36.7 re-verify) |
| 36, shown new parameter, justified like its Comment label | `Justification` on a parameter (read from `Comp.Comment`, written on the new one) | yes, `Generic.pas` (net labels, the generic property writer) | medium, first write on a parameter; reached only with `visible=true` (36.7 re-verify) |
| 36, existing Description parameter kept in step | `Text` | yes, `SetCompParamTextEx` (the same write on the same object) | low |
| 37, footprint height | `Height` on a footprint | yes, `Lib_UpdateFootprintHeightsFrom3D` | low: the same write, now bracketed with `PCBM_BeginModify`/`PCBM_EndModify` and read back after the footprint switches |
| 37, making the footprint current after the write | `CurrentComponent` | yes, `LibMakeCurrentVerified` | low: the shipped verified switch; what is new is calling it after a height write, which is the measured recipe that kept 55 of 55 heights |
| 37, an already-current footprint switched away and back | `LibParkCurrentElsewhere` (LibraryIterator walk + `CurrentComponent`) | yes, `Lib_DeleteFootprintPrimitives` and `Lib_DeleteFootprintPads` (their park-current option) | low: shipped members; new is running it on the DEFAULT path (`footprint_name` empty) of a height write (37.2b) |
| 37, component_description | `ComponentParamFieldsJson`, which READS DM_Description on IComponent (a read, so the property column names the new routine that makes it) | no, nowhere: shipped code reads DM_Description only on IProjectVariant | HIGH: an unknown member stops the bridge loop on the first component-info call. Read only with with_parameters (the default, so the orchestrator's and the dashboard's calls reach it); run 37.6 FIRST |
| 37, does the part carry a Description parameter | `SchDescriptionParamState`, which reads DM_LogicalDesignator, then GetSchDocumentByPath and the eParameter walk | no, nowhere as a routine; every member it reads is shipped (DM_LogicalDesignator in the Generic.pas sheet snapshot, the walk and name test in ModifyCompParamIfPresent) | low; reached only when the part has exactly one Description entry equal to its component description |
| 38, the dirty mark also on the focused document | `SetModified` on the focused view's owner document (Client.GetCurrentView, then OwnerDocument, both read by GetPCBBoardAnywhere already) | yes, `WriteDocAndNote` (every document the save walk and the record sweep write) | low: shipped members; new is marking and reading the focused document when it is the marked file, and the save pass writing a document the bridge recorded although its flag reads clean |
| 40, text size | `Size`, `Width` on a board text | yes, `Lib_AddFootprintText` | low |
| 40, silkscreen restore | `MoveToXY` on a designator | yes, `Generic.pas`, on components | low |
| 40, silkscreen anchors | `ChangeNameAutoposition` | yes, `PCB_AutoplaceSilkscreen` before this change | low |
| 41, via template | `CopyTo` on a via's template link | yes, `Library.pas`, on footprints only | highest, and it can stop the loop |
| 41, via mode | `Mode` on a via | yes, on pads, `PCB.pas` | medium |
| 41, via size | `Size`, `HoleSize` on a via | yes, `PCB_PlaceVia` | low |
| 41, Routing Via rule | `PreferedWidth`, `PreferedHoleWidth`, `MinHoleWidth`, `MaxHoleWidth` | no, nowhere | high, and it can stop the loop |
| 43, DbLib connection | `ConnectionString`, `LoginPrompt`, `Connected` on an ADO connection | no, nowhere | highest, and it can stop the loop |
| 43, DbLib query | `Connection` on an ADO query | no, nowhere | highest, and it can stop the loop |
| 43, DbLib record key | `ParamByName`, `Value` on an ADO parameter | no, nowhere | high, and it can stop the loop |
| 43, DbLib placement | `LoadComponentFromDatabaseLibrary` | no, nowhere | highest, and it edits the sheet |
| 43, DbLib placement | `AddSchObject`, `SetState_Orientation` | yes, `Gen_PlaceSchComponentFromLibrary` | low |
| 44, repour | `PolygonRepour` on the system options | no, nowhere | high, and it can stop the loop |
| 44, ECO direction | `AddStringParameter` with `Action=UpdateMe` | yes, `Proj_UpdatePCB` | high, and executing it edits the board |
| 44, exact vias | `Size`, `HoleSize` on a via, in fractions of a mil | yes, `PCB_PlaceVia` | low |
| 44, replicated part | `RegisterSchObjectInContainer` | yes, `Generic.pas` | medium |
| 44, footprint target | `CurrentComponent` | yes, `Lib_SetDesignator` | low |
| 44, copied footprint | `MoveByXY` on a footprint's primitives | yes, `Library.pas`, on a 3D body | medium |
| 45, lengths in mm | `TopXSize`, `TopYSize` on a pad, now from `CoordFromUnits` | yes, `Lib_AddFootprintPads` | low |
| 45, new document | `ShowDocument` on the new document | yes, `App_SetActiveDocument` | low |
| 46, simulation run | `AddStringParameter` with `ObjectKind=Simulator` | yes, `Proj_UpdatePCB` | medium, a process run rather than a write |
| 46, results saved | `SetFileName`, `DoFileSave` on the results document | yes, `App_CreateDocument` | low |
| 46, settings reload | `SupportsReload`, `DoFileLoad`, `UpdateModifiedDate` | no, nowhere | highest, and it can stop the loop |
| 47, slotted hole | `HoleType`, `HoleWidth` on a library pad | no, nowhere | high, and it can stop the loop |
| 47, pad mask expansion | `SolderMaskExpansion`, `SolderMaskExpansionValid` on a pad cache | yes, `PCB_SetViaSoldermaskRelief`, on a via's cache | medium, a library pad is new |
| 47, non-plated hole | `Plated` on a library pad | read in `PCB.pas`, written nowhere else | medium |
| 47, pad paste expansion | `PasteMaskExpansion`, `PasteMaskExpansionValid`, `SetState_Cache` | yes, `PCB_MakePasteGrid` | low, but now on a library pad |
| 47, paste windows | `SetOutlineContour`, `Replicate` on a region contour | yes, `PCB_PlaceRegion` | medium, a region in a footprint is new |
| 47, footprint height | `Height` on a footprint | yes, `Lib_SetFootprintHeight` | low |
| 49, board select | `Selected` on a board primitive, now read back | yes, `SetPCBProperty` | low |
| 49, sheet select | `Selection` on a schematic object, now read back | yes, `SetSchProperty` | low, the read is the one `Gen_ClearHighlights` makes |
| 50, DRC report stamp | `FileAge` on the board's .DRC report | yes, `Proj_ExportSTEP` | low |
| 50, managed loader | `LoadComponent` | yes, `Lib_GetVaultComponent`, also new; the call was recorded working on AD 26.9.1, before this rewrite | high, the whole managed placement depends on it |
| 50, managed placement | `AddSchObject` with a loaded managed component | yes, `PutLoadedComponent`, which file-library placement now shares | low |
| 50, dry run | `DestroySchObject` on a loaded component no sheet owns | yes, `Gen_CreateObject`, on objects never added | medium |
| 50, Workspace name | `LibraryIdentifier`, `LibIdentifierKind`, `VaultGUID` read off placed parts | yes, `VaultNameOnSheet`, also new | low |
| 50, managed lookups | `FindComponentLibraryPath`, `FindComponentDisplayPath`, `GetLifeCycleState`, `GetOrderedRevisionList`, `GetComponentPlacementParameters` | no, nowhere | low, each in its own Try and reported in `diag` when it raises |
| 51, plane net | `InternalPlaneNetName` on the board, written and read | no, nowhere | medium; there is no second route (the merge removed upstream's layer-object fallback, `LayerObj.Net` being the measured undeclared identifier), so a write that does not read back answers `applied: false` |
| 51, net-class members | `IsMember` on a net class | yes, `PCB_AddTestpointsForNetClass` | low, but one call per class and net |
| 51, DbLib part | `CurrentPartID` on a placed DbLib part | yes, `SetSchProperty` | low |
| 51, pad designator | `Name` on a pad, through the generic setter | yes, `PCB_RenumberPads` | low |
| 51, poll timer | `Enabled` on the poll timer, read back after arming | yes, `StartMCPPump` writes it | low |
| 52, 3D body opacity | `BodyOpacity3D` on a component body | no, nowhere | high, a wrong member name stops the loop; used by several scripts in reference/ |
| 52, slot rotation | `HoleRotation` written on a library pad | no, nowhere | medium, read on the board side already |
| 52, pad copper centre | `BoundingRectangle` on a library pad | yes, `BuildViolationJson`, on any primitive | low |
| 52, pad setter | `SetState_Cache` on a library pad | yes, `Lib_AddFootprintPads` | low |
| 52, arc mirror | `StartAngle`, `EndAngle` written on a placed arc | yes, `Lib_AddSymbolArc` | low |
| 53, footprint switch | `SetState_CurrentComponent` on the library, away from the target and back | yes, `Lib_RedrawAssembly` (the same away-and-back switch; upstream cited Lib_Link3DModel, which no longer writes it since the carrier-local verified switch replaced the call) | low |
| 53, freeing a removed primitive | `DestroyPCBObject` on a primitive just removed, inside the open iterator | no, nowhere | medium, a freed primitive used again takes the loop down; ran 25 cycles live without a fault. Since the `.13.01` merge only `ClearDrawingOnLayer` frees: the footprint delete removes without freeing, as carrier-local's always did |
| 53, temporary holder | `GetUniqueCompName`, `DestroyPCBLibComp` on a footprint made for the delete | no, nowhere | low, ran live in a single-footprint library |
| 54, body layer | `Layer` written on a library 3D body | yes, `PCB_Place3DBody` | low |
| 54, mechanical check | `MechLayerFromNumber` (the guarded `LayerUtils.MechanicalLayer` accessor), through the merge's `IsMechanicalLayerId`, which replaced upstream's unproven `LayerUtils.IsMechanicalLayer` | yes, `Lib_GetMechLayers` | low, eMechanical1..16 are compared as enum values and only higher numbers go through the accessor |
| 54, assembly text | `UnderlyingString`, `Size`, `Width` on a new library text | yes, `Lib_AddFootprintText` | low |
| 54, settle after an add | `SetState_CurrentComponent` on the footprint already current | yes, `Lib_RedrawAssembly` (upstream cited Lib_Link3DModel and Lib_DeleteFootprintPrimitives; the merge keeps carrier-local's verified CurrentComponent switch in the first and the opt-in park in the second, so neither writes it now) | low, ran after all eight add kinds and survived save and restart |
| 54, modify bracket | `BeginModify`, `EndModify` round an obj_modify write and a placed body's move | yes, `PCB_SetTrackWidth` | low |
| 54, mask sides | `UseSeparateExpansions`, `SolderMaskBottomExpansion` on the pad cache | yes, `Lib_SetPadProperties` | medium, missing from the API reference; read and written live on pads and vias |
| 54, via hole edge | `SolderMaskExpansionFromHoleEdge` written on a via | no, nowhere | high, the expansion assigned straight onto a via faults (measured again); this member and the cache route did not |
| 54, mechanical enabled | `MechanicalLayerEnabled` read on a library layer | yes, `Lib_GetMechLayers` | low |
| 54, rule gap | `Gap` read on a typed clearance rule | yes, `PCB_SetRuleProperties` | low |
| 54, designator mirror | `Location` written on a placed designator | yes, `SetSchProperty` | low |
| 50, footprint on a placed part | `ModelName` on a footprint implementation | yes, `Lib_SetModelName` | medium, now on a placed part rather than a library symbol |
| 50, footprint link name | `EntityName` on a model datafile link | yes, `Lib_NormalizeImplementations` | low |

Steps 5 and 2 are the ones that justify a live session. The bottom rows
write properties this codebase already exercises, so they are checking
the new call site rather than the API.

Step 5 appears three times because its properties do not share a risk.
`MoveByXY` is inherited from `IPCB_Primitive` and `PCB_ReplicateLayout`
already calls it, so it cannot be an undeclared identifier and a failure
there would be behavioural: whether moving a group moves its children.
`SetState` is the 3D rotation, written on the MODEL because the body has
no `Rotation` (assigning one stopped the loop on 26.9.1.9). No shipped
code calls it. It is attested by the AD26 script binding itself
(ScriptingSystem.dll declares `SetState(RotX, RotY, RotZ : Double;
Dz : Integer)` and `GetState` with the same four parameters as OUT) and
by Altium.SDK.Interfaces.dll, which is why it is ranked as an
unexercised identifier rather than a guessed one. It runs only when a
rotation is non-zero, so every other 3D step stays clear of it.

Two steps now write `StandoffHeight` and neither has run, so they do not
vouch for each other: steps 5 and 15 share one unproven identifier. Run
whichever is easier and the other drops to a behavioural check.

### What a default call can never reach

Every unproven member and every unproven SEQUENCE in this release sits
behind an argument that has to be passed on purpose. A call with default
arguments makes only calls that have run live before, so an ordinary
session cannot stop the polling loop on one of these. Keep each one
opt-in until its step has passed, and only then consider flipping a
default:

| Opt-in | What it reaches | Step |
|---|---|---|
| a non-zero rotation (or, on `lib_link_3d_model`, a non-zero `offset_z`) on `lib_link_3d_model` / `pcb_place_3d_body` | `IPCB_Model.SetState` (ran live 2026-10-04 with a rotation; `GetState` is no longer called: it returned zeros) | 5, 15, 31 |
| `model_state=true` on `lib_get_footprint_bodies` (default false) | `IPCB_Model.ModelType`, `FileName`, `Rotation` (ran live once without a fault) | 22, after 5 |
| `remove_from_board=true` on `lib_delete_footprint_primitives`, `lib_delete_footprint_pads`, `lib_add_footprint_text`, `lib_link_3d_model` with `replace`, `lib_auto_link_3d_models` with `replace` | `RemovePCBObject` on the library board, and making the footprint current first | 24 |
| `keepout=true` on a fill, or `kind=keepout` on a region, NOT on KeepOutLayer | the `IsKeepout` write | 24 |
| `kind=cutout` on a footprint region | the `Kind := eRegionKind_Cutout` write | 16, 24 |
| `visible=true` on `sch_set_components_parameters` | `ShowName := False`, `Orientation := Comp.Comment.Orientation` (the part's only as a fallback), `Justification := Comp.Comment.Justification` and a `Location` offset from `Comp.Comment.Location` on a parameter it creates | 36 |

Both writes in the last two rows are read back (`keepout_applied`,
`kind_applied`), so the first live call that reaches them says whether
they took, if it does not stop the loop. If one does stop it, the fix is
to refuse that argument in the Python payload builder, not to retry.

Two READS on default paths are new at their call site but not new
identifiers, so they cannot fault as undeclared: `Component.ComponentKind`
in `lib_get_component_details` (written by `Gen_SetNetTie`), and
`Body.Model` in `Body3D_GetModel` (written by every body placement here).
Only their read accessors are unexercised, and each read sits in a `Try`.
Confirm both on any live call you make anyway: `lib_get_component_details`
on a symbol answers `component_kind` and `component_kind_ordinal` with a
plausible value, and `lib_get_footprint_bodies` with `model_state=true`
(after step 5) gives each body a `model` object rather than null.

### What this release adds, and why it is a different kind of risk

The steps above check whether an identifier exists. This release
carries almost none of that risk: every Altium property and method it
touches is already written somewhere in shipped code, so an undeclared
identifier is close to ruled out by construction. The cross-document
hints touch no Altium API at all, being string handling only.

`Proj_UpdatePCB` now reads `DM_FocusedDocument` and `DM_DocumentKind`
before comparing, and refuses while a schematic is focused. Both
identifiers are already used across several units, so neither is new
exposure. Everything else is string handling: the reply fields that
used to overstate what had been checked, and the removal of the
`Client:RunMenu` fallback in `App_ExecuteMenu`, which now refuses an
unmapped path instead of guessing a MenuID.

One thing here cannot be settled by running the steps below. The
refusal is deliberately absent from `Proj_UpdateSchematic`, the
opposite direction, because the focus Altium wants there has never been
measured. If you exercise that direction, record what it does.

Footprint height is the same shape of risk, which is to say almost
none. `Lib_SetFootprintHeight` writes `Footprint.Height`, which the
height sweep beside it has always written, and reads the library
through the same iterator thirty-odd other handlers use. What is new is
a DIRECTION: the sweep can now lower a height to the model, not only
raise it. Check it by setting a footprint absurdly tall by hand, running
the sweep in `match` mode, and confirming it comes back down. Confirm
the reverse too, that `raise` leaves it alone, because a mode that
ignores its argument would pass the first test on its own.

The case worth being careful about is a footprint with NO 3D body. It
must not be written at all. Writing the 0 that an absent model implies
does not relax placement-collision DRC, it disables it for that part,
and doing that across every unmodelled footprint would switch the rule
off wholesale while reporting a clean sweep. Run `match` on a library
where at least one footprint has no model, and check that footprint's
height is untouched and that its name comes back under
`without_model_names`.

What it adds instead is **logic that decides what to touch**, which
fails silently rather than loudly:

| Added | What could go wrong | How you would know |
|---|---|---|
| `GetPCBBoardForMutation` | An edit refuses when it should proceed, or proceeds when two boards are open and none focused | Open two PcbDocs, focus neither, run `pcb_delete_object`. It must refuse and name both |
| Board mechanical layers | A paired kind is written on the layer rather than the pair, which silently does nothing | Set a paired kind, then read it back with `pcb_get_mech_layer_names` |
| `lib_delete_footprint_primitives` | Removes from the wrong footprint, or takes pads with it | Probe the footprint first, delete one layer, probe again and compare pad count |
| Library parameter delete | Matches zero and reports success | Delete a named parameter from a library symbol, then read the symbol's parameters |
| Multi-part scope suffix | Returns part one's pins under another part's name | `lib_get_pin_list` on a multi-part symbol with `@2` and `@3`, and compare the counts |
| Cross-document hints | The hint is appended to an error it does not apply to, or doubles up on a message that already names a tool | With NO PcbDoc open in any project, focus a SchDoc and run `pcb_delete_object`. The refusal must name the PCB tool once, and read as one sentence. (With exactly one PcbDoc open anywhere the call does not refuse: it opens that board and edits it) |
| Refusals name the focused document | A wrong-document refusal says what is missing and not what is there, so the caller goes looking for whatever took the focus | Focus a SchLib and run `sch_place_components`. The refusal must name the .SchLib by file name only, no folder, and still carry the `lib_` hint once. Name the library something containing `lib_` and repeat: the hint must still appear |

None of these can halt the polling loop the way an undeclared
identifier does, so they are safe to run in any order and safe to run
last. The cost of getting one wrong is a wrong answer, not a dead
bridge.

### Carried from the previous build: five fixes whose symptom was silence

These came out of one live session and a bug report, and they share a
shape: the tool reported success, the board or sheet did not agree, and
nothing anywhere said so. A guard test covers each one against the
source, which is not the same as having watched it work.

| Fixed | What it did before | How you would know it is fixed |
|---|---|---|
| Component movers use `MoveByXY` | Assigning `Comp.x` moved the record and left every pad where it was, so the pour kept clearing the old footprint and the DRC kept measuring it | Move a placed component with `pcb_move_components`, then `pcb_repour_polygons`. The copper must clear the NEW position and nothing at the old one |
| Placed copper joins its net | `Prim.Net := N` sets a reference, but the net keeps its own collection and connectivity, the ratsnest and the pour all walk that one. Copper had a net, reported that net, and was invisible to everything downstream | `pcb_place_via` on a named net, then `pcb_get_unrouted_nets`. The via must count as connected, and a pour on that net must give it thermal relief |
| Schematic writes mark the document | A write that does not set the modified flag is invisible to `SmartCompile`, which checks `ProjectHasDirtyDocs` and skips. Symptom: ERC and the netlist keep reporting the state before your edit until the sheet is reopened | Place something with `sch_place_no_erc`, then `app_context`. The sheet must appear in `unsaved`, and `proj_run_erc` must see the edit without a reopen |
| Position reads resolve per type | `GetPCBProperty` read `Obj.x` off the declared `IPCB_Primitive`. A type that does not publish it raised "Undeclared identifier: x", which the engine shows as a modal before any `Try/Except` runs, stopping the polling loop | `obj_query` a text object's position, then an arc's. Both must answer, and a track must come back `unreadable` rather than with one of its ends |
| `app_context` can see unsaved work | It filtered the open-document list on a `modified` key the handler never emitted, so the list was always empty and every session opened reporting itself clean | Edit any sheet without saving, then `app_context`. `unsaved` must name it |

`pcb_set_via_soldermask_relief` refuses on this build rather than being
fixed. The write raises an access violation inside
`ScriptingSystem.DLL` on AD 26.10.1.6, measured twice on a scratch
board with three vias and nothing else, once through `BeginModify` and
once through `SendMessageToRobots`. Because the fault lands between
`PreProcess` and `PostProcess` it also leaves an open transaction
behind it.

The refusal is in the PYTHON tool, and that is the half to check.
Reaching the handler means putting the command on the wire, and any
session running a deployed script from before the refusal landed still has the
crashing write in it. Confirm the tool answers `NOT_SCRIPTABLE`
instantly and that `bridge_trace.log` shows no request for it.

### Three new design-rule kinds, and these DO carry identifier risk

Unlike everything else in this release, these write Altium symbols this
codebase has never used. Run them before anything else in a session you
mind losing, because an undeclared identifier here stops the polling
loop rather than returning an error.

| `rule_type` | Symbols | Evidence they exist | How to check |
|---|---|---|---|
| `paste_mask_expansion` | `eRule_PasteMaskExpansion`, `IPCB_PasteMaskExpansionRule.Expansion` | `NofittedNoPaste.pas` in the reference corpus creates one exactly this way | Create one, then read it back with `pcb_get_design_rules` and look at `descriptor`, not `rule_kind`, which is a raw enum ordinal |
| `solder_mask_expansion` | `eRule_SolderMaskExpansion`, `IPCB_SolderMaskExpansionRule.Expansion` | enum used by three reference scripts; interface and property in the SDK reference. The FACTORY call is unproven | Create with `scope="IsVia"` and a negative value, then look at the mask layer over a via |
| `vias_under_smd` | `eRule_ViasUnderSMD`, `IPCB_ViasUnderSMDConstraint.Allowed` | same: enum used by three reference scripts, interface and property documented, factory call unproven | Create with `allowed=false`, place a via inside an SMD pad, run DRC |

`paste_mask_expansion` is the one to run first. It is the only one of
the three whose factory call is demonstrated by working published code,
so if it fails the problem is this handler rather than the symbol, and
if it succeeds the other two are down to their own enum values.

These close the gap the via-relief refusal used to point at: tenting is
a Solder Mask Expansion rule scoped `IsVia`, and via-in-pad is caught
by Vias Under SMD. Both were previously "set it in the dialog".

### Close without saving, and OutJob runs that wrote nothing

Two handler changes. Neither adds an identifier this codebase has not
already called, so the risk is behavioural rather than a halted loop.
Run both on a scratch project, never a client one: the first one is
built to throw edits away.

| Changed | What is unmeasured | How to check |
|---|---|---|
| `Proj_Close` with `save=false` clears each loaded member's modified flag before closing, without reading it | MEASURED on this build, local scratch project: the tab showed the sheet modified, the close raised no prompt, `attempts` was 1, and the file on disk was byte-identical afterwards. On the previous build the clear was gated on a read that returned False, and the close prompted. A managed project is untested | On a managed project, edit a sheet without saving, then `proj_close(project_path=..., save=False)`. Record whether a prompt appears |
| If a prompt appears anyway, `proj_close` answers it: Save None, then OK once the title reads "Confirm Not Saving" | The sequence was MEASURED on the previous build with `app_invoke_element`. The tool running it has not run live, because on this build the prompt did not appear | Only reachable where the step above still prompts. The reply then carries `prompts_answered` |
| It presses nothing when the prompt lists a document outside the project | Unmeasured | Do not arrange it on a project with real work open |
| `App_GetActiveDocument` and `Proj_GetDocuments` put a path in `file_path` | MEASURED: the previous build reported the bare file name from both, and this build reports absolute paths from both | Done |
| A close without saving that stays open is retried once | MEASURED on the previous build, and misread at first: `attempts: 2` on an unmodified scratch project was not the retry doing its job. The first close had closed a different project, the focused one | See the next row |
| `Proj_Close` focuses the named project before closing, refuses when it cannot, and stops without retrying when anything else closes | MEASURED on the previous build: `CloseObject` given a project's full path closed the FOCUSED project instead, and nothing reported it. This build's guard is unmeasured | Open two scratch projects, each with a loaded sheet. Focus a sheet of the first, then `proj_close` the second: only the second may close, and `closed_instead` must be empty. Then `proj_close` a project with no loaded document: it must refuse and close nothing |
| `Proj_RunOutJob` replies `process_issued`, and the Python tools decide `success` from the output folder | Nothing Altium-side | Run a container with its outputs switched off: `proj_run_outjob` must return `success: false` with a `reason`. Run one that writes: `files_written` must be above zero |

---

## Gather these before you start

Each step wants a particular document open or a particular file on
disk, and none of it is interesting to discover halfway through. Step 5
in particular needs a STEP model, which is not something to go looking
for mid-session.

| Step | What must be open, or on disk |
|---|---|
| 0, 1 | Altium running with `Altium_API` loaded and the loop started |
| 2 | a SchLib, and the name of a symbol in it (held with `lib_set_current_component` first: the editor's own selection is never used) |
| 3 | a SchLib |
| 4 | a PCB whose active variant has Not-Fitted parts |
| 5 | a PcbLib with a footprint, plus a `.step` file |
| 6 | a PcbLib, plus a KiCad `.kicad_mod` carrying `B.SilkS` text |
| 7 | a SchLib for the pins (a symbol held with `lib_set_current_component` first), and a schematic sheet for the power ports |
| 22 | a COPY of a PcbLib built by the IPC generator, plus a maker STEP for one of its footprints |
| 36.7 | a scratch sheet with an upright part and a part rotated 90 whose Comment label is horizontal |
| 37 | a COPY of a PcbLib with three or more footprints, a scratch COPY of a sheet (and of a project holding a disposable sheet, for 37.4b), and a placed part carrying a Description parameter |

Steps 4 and 5 write to the design. Take an `app_checkpoint` first, or
work on a copy. Everything else either reads or builds new library
objects you can delete afterwards.

---

## 0. Confirm what is actually loaded

```
app_ping
```

Expect `altium_script_version` = `2026.10.13.02`, `version_match` =
`true`, and `mcp_server_version` = `0.6.1`. On a freshly started loop
`caught_exceptions` is `0` and `last_caught` null (section 39): note both,
since every later section is read against them.

Those are two different versions and they fail differently.
`altium_script_version` is the Pascal that Altium compiled;
`mcp_server_version` is the Python package answering the call. A wrong
Pascal version means a stale deploy. A wrong package version means the
installed wheel is not this tree, so the tools themselves differ from
what this document describes.

A mismatch means Altium is still running an older compiled copy.
Reload the script project (**File > Run Script**, pick the project,
or close and reopen it) and ping again. Do not interpret any later
result until this matches: a stale script produces failures that look
like defects in the new code.

**No answer at all is a different failure with a different fix.** A
mismatch means the wrong script is running; a timeout usually means no
script is running. Altium's process being alive proves nothing here,
since the polling loop can be stopped while Altium itself is fine. That
was the state of this machine while this document was written.

Read `last_fault.json` in the workspace directory rather than guessing.
The bridge writes a diagnosis and numbered steps there, and tells the
three cases apart:

| `fault` | What it means | Fix |
|---|---|---|
| `dead_loop` | no response and no heartbeat | dismiss any Altium error dialog, Stop, relaunch |
| `stuck_handler` | keep-alives answered, one command never returned | look for an Altium error dialog first: press OK and the call returns `INTERNAL_ERROR` (record the dialog text and `app_get_status`'s `in_flight` before pressing). No dialog: Stop, relaunch |
| `corrupt_response` | Altium crashed mid-write | retry once, the bad file is already removed |

Stop is the red button in the Script IDE (**Run > Stop**, `Ctrl+F3`,
or `Ctrl+Pause` if it is truly hung). Relaunch is **File > Run
Script... > Altium_API > Dispatcher.pas > StartMCPServer**.

Leftover `request_*.json` files in the workspace are a symptom of this,
not a cause: they are calls the loop never picked up. They are harmless
and the next healthy loop consumes them.

---

## 1. Pure logic, no document needed

`RunSelfTest` is no longer listed in the Run Script dialog. It carries a
dummy argument like every other internal routine, so the dialog offers
the one thing a user does: start the bridge. To run the self test,
temporarily drop the argument from `Procedure RunSelfTest(Dummy : Integer)`
in `SelfTest.pas`, reload the project, and pick it from the dialog.

It also ends in `ShowMessage`, so it cannot be reached over the bridge
without splitting the summary out first.

Expect `Failed: 0`. The log is written to the workspace directory.

The self test no longer runs `stop_server`. It used to, and that call runs
the save pass over EVERY open document (rewriting files nobody edited) and
can meet Altium's own "A command is currently active" prompt before it
stops the loop. Its contract is now executed offline from the real source
by `tests/test_popup_free_core.py`. `App_GetActiveDocument`, which the self
test does call, no longer raises when no schematic has been opened this
session (it used to, on its schematic fallback). Read `caught_exceptions`
before and after: it must not move.

This runs 27 assertions over `StrToIeeeSymbol`, `IeeeSymbolToStr` and
`StripChar` inside Altium's own engine. The values match what
`tests/test_cross_validate.py` pins against the FPC-compiled originals,
so a failure here means DelphiScript disagrees with Free Pascal. That
is the gap this step exists to close.

**If it fails:** the log names the assertion. Report the text; the
converters are in `scripts/altium/Utils.pas`.

---

## 2. Pin edge decorations (task #20)

Needs a SchLib open. Hold the symbol to edit FIRST: the symbol merely
selected in the editor is never used (its members cannot be read safely
from a script; reading one halted the bridge on 2026-10-11, section 39),
so without this the call answers `NO_COMPONENT`, which is the intended
behaviour, not a failure of this step. Passing `component_name=<symbol>`
on the call itself does the same.

```
lib_set_current_component(component_name="<a symbol in that library>")
lib_add_pins(pins=[
  {"designator": "1", "name": "RESET", "x": -300, "y": 0,
   "symbol_outer_edge": "dot"},
  {"designator": "2", "name": "CLK",   "x": -300, "y": -100,
   "symbol_inner_edge": "clock"},
])
```

Then **look at the symbol**. Pin 1 must carry an inversion bubble at its
outer end; pin 2 a clock wedge at the body end.

The returned `added` count says how many pins were created, not how they
were drawn, so it cannot confirm this. Render or view it.

**What is actually being tested:** whether `Symbol_OuterEdge` /
`Symbol_InnerEdge` are settable on a pin from `SchObjectFactory`
*before* `AddSchObject`, and whether assigning an Integer to those
enum-typed properties behaves the way `Pin.Orientation` does. Step 1
already proved the value mapping.

**The two failure shapes, and why the assignment is deliberately not
wrapped in `Try/Except`:**

* the property exists but rejects the value: an Altium error dialog
  appears FIRST (the engine shows the exception before any `Except`
  runs). Note its text and press OK; the dispatcher then answers
  `INTERNAL_ERROR: Unhandled exception processing: library.add_pins`,
  `caught_exceptions` rises by 1 and `last_caught.where` is `dispatcher`.
  The loop keeps running.
* the property name is not real: an undeclared identifier faults where
  `Try/Except` cannot reach, and the polling loop stops. Recover with
  Detach and `StartMCPServer`.

Guarding the assignment would turn both into a silent success that adds
undecorated pins, which is the one outcome this step could not tell
apart from working. That is also why this step is early: it is the
first thing here that can halt the loop.

---

## 3. Filled IC bodies (task #28)

```
lib_create_ic_symbol(...)          # any part
```

The body rectangle must be **filled Altium light-yellow**, not a bare
outline. This is discipline rule 17, and it has been silently unmet:
the tool sent `AreaColor 8454143` and the bridge discarded it because
`IsSolid` was pinned False.

Then confirm the no-fill path still works:

```
lib_create_passive_symbol(kind="resistor", ...)
```

That sends `fill_color=-1`, the documented no-fill sentinel, and its
body must stay **unfilled**. `-1` is also the parameter's default, so it
arrives on nearly every call; treating it as a colour would turn every
symbol rectangle solid.

---

## 4. DNP paste exclusion (task #26)

**This edits the board.** Work on a copy, or `app_checkpoint` first.

Needs a PCB with a variant that has Not-Fitted parts.

```
pcb_apply_dnp_paste_exclusion(dry_run=True)
```

Check the designator list matches `audit_variant_not_fitted`. Then:

```
pcb_apply_dnp_paste_exclusion()
pcb_get_pad_properties(designator="<one of them>")
```

`paste_mask_expansion` must be negative.

A negative expansion is necessary but not sufficient. Only the artwork
proves the fab outcome:

```
proj_generate_fab_package(...)
```

Open the paste layer and confirm those apertures are **absent**. Then
restore, passing back the designators the apply reported in `items`:

```
pcb_apply_dnp_paste_exclusion(designators=["<from the apply reply>"],
                              restore=True)
```

Regenerate and confirm the apertures come back.

**Restore will refuse a bare `restore=True`**, and that refusal is the
behaviour to verify, not a defect. It is decided in Python before
anything reaches Altium and is covered offline by
`tests/test_dnp_paste_exclusion.py`, so it needs no live call of its own;
run it only if the live session has time to spare. Resolving a restore from the current
variant is only correct while the variant has not changed since the
apply; when it has, a component excluded under the old variant keeps
its aperture suppressed and nothing says so. Check that the refusal
names both the way forward and `use_current_variant`, which is the
explicit opt-in to the old resolve-from-the-variant behaviour.

After a restore, `paste_mask_expansion` reads **0**, which is what a
successful restore looks like and not a separate failure. The field is
always present; `PCB_GetPadProperties` initialises it to 0 and only
substitutes the real value when the pad cache is manual. Restore sets
the cache back to invalid, so the design rule drives the aperture and
the reported number falls to 0.

A pad that was never excluded also reads 0, so this distinguishes
applied from not-applied, not restored from never-touched. The artwork
is what settles that.

Check a **bottom-side** DNP part too if the board has one:
`Pad.TopXSize` is the top-layer size and drives the expansion for both
sides.

---

## 5. 3D model placement (task #27)

> **Partly settled live on 2026-10-04; read step 31 first.** `SetState`
> ran and the rotation persisted (`MODEL.3D.ROTX=90 / ROTZ=45` in the
> saved file). `GetState` ran and returned zeros, so it is no longer
> called: `model_state` is now always null and `rotation_readback`
> reports `available: false`; items 2 and the `model.rotation` checks
> below are replaced by `lib_read_pcblib_file`. `offset_z` now writes the
> model's `MODEL.3D.DZ` (Altium's own field), not `StandoffHeight`, so
> `applied.standoff_height` is false and `applied.z_offset` true. With
> `align` none the model origin now goes to the FOOTPRINT origin.

The highest-risk item, and the one that can stop the loop: `SetState`
and its read-back `GetState` are IPCB_Model members no earlier call
here exercised. Take an `app_checkpoint`, use a scratch footprint, and
run the rotated call FIRST so that if it faults, nothing else in the
session is lost with it.

```
lib_link_3d_model(component_name="<fp>", model_path="<...>.step",
                  offset_z=40, rotation_z=90, offset_x=10)
```

If the bridge stops here, the dialog names the identifier. `SetState` or
`GetState` means the binding differs from what ScriptingSystem.dll
declares on this build: record which. A link with no rotation still
works, because neither is reached without one.

Read the reply in this order:

1. `applied`: `standoff_height`, `rotation`, `rotation_z`, `offset_xy`
   must all be `true` for the call above. They mean "this was applied",
   not "this was accepted": the handler skips a value of 0, so keep every
   argument non-zero while verifying.
2. `model_state`: the rotation the MODEL reports holding. `{x: 0, y: 0,
   z: 90}` and `rotation_readback_matches: true` is the result that
   settles `SetState`. A `null` there with `applied.rotation: true` means
   the write returned but the OUT-parameter read did not come back; a
   rotation of 0 means the write went somewhere else. `dz_mils` shows
   whether `offset_z` also landed in the model's own Z offset.
3. `body.bbox` against `bbox_expected`: equal means BoundingRectangle is
   live on a body; `bbox_readback_stale: true` means it is cached, as it
   is on texts, and the stale flag is doing its job.
4. `body.bbox` against `frame.pads_bbox`: the model's box beside the
   land, in the same frame. For a 90 degree turn of a non-square part,
   width and height must have swapped against an unrotated link of the
   same file; if they did not, the body's outline was derived before the
   rotation took and the order needs revisiting.

Then open the 3D view: the body should be lifted, turned and nudged.
Save, `lib_reload_library`, and `lib_get_footprint_bodies` on the same
footprint: `model.rotation` must still read 90 after the round trip. The
saved file is the final word and needs no Altium at all: the body's
record in the .PcbLib carries `MODEL.3D.ROTZ=90.000` as plain text.

Repeat with `rotation_x=90` and no other rotation. A Y-up STEP should
now stand on the board; `model_state.x` must read 90.

**Units:** the tool documents mils and applies `MilsToCoordF`. A body
that moves 25.4x too far means the property wanted different units.

---

## 6. Mirrored bottom-side text (task #21)

Lowest risk of the set. `MirrorFlag` is already written by `PCB.pas` and
read by `Audit.pas`; only the call site is new.

Import a footprint carrying `B.SilkS` text, then:

```
audit_find_mirrored_pcb_text
```

Expect **zero** violations. Before this change the importer produced
`bottom_overlay_text_is_not_mirrored` on every such item, i.e. output
this server's own audit rejected.

---

## 7. Enumerated words map to the right Altium enum (task #33)

This covers what `tests/test_enum_vocabularies.py` cannot. That test
proves every advertised spelling has a branch in the `StrTo*` converter.
It cannot prove the branch assigns the enum member the word names,
because the ordinals only exist inside Altium.

The failure is silent. Each converter ends in an `Else` that picks a
default, so a wrong or missing branch yields Passive, or a supply Bar,
with no error reported.

Place four pins with different electrical types, then read them back.
Hold the symbol first (or pass `component_name` to `lib_add_pins`): with
nothing held the call answers `NO_COMPONENT` by design, since the editor's
own selection is never used.

```
lib_set_current_component(component_name="<a symbol in that library>")
lib_add_pins(pins=[
  {"designator": "1", "name": "VIN",  "x": -300, "y":    0,
   "electrical_type": "power"},
  {"designator": "2", "name": "NRST", "x": -300, "y": -100,
   "electrical_type": "open_collector"},
  {"designator": "3", "name": "SDA",  "x": -300, "y": -200,
   "electrical_type": "io"},
  {"designator": "4", "name": "OUT",  "x": -300, "y": -300,
   "electrical_type": "output"},
])
lib_get_component_details(...)
```

Each pin must report the type it was given. All four reading `passive`
means the string never matched and every one took the default;
`open_collector` alone reading `passive` means the underscore handling
is the part that broke.

Then one power port per glyph:

```
sch_place_power_port(text="GND",  style="gnd_signal", x=1000, y=1000)
sch_place_power_port(text="+3V3", style="bar", x=1400, y=1000,
                     orientation=1)
```

`orientation=1` on the rail is required. The style-based default sends
`bar` and `wave` down with the grounds, so a VCC bar drawn without it
points down and looks like a ground symbol.

Look at the sheet. A signal-ground glyph and a rail bar are visually
distinct; two identical bars mean `gnd_signal` fell through to the
`ePowerBar` default.

---

## 8. ERC violations name the objects they are about

**`proj_run_erc()`, then `proj_get_erc_violations()`**

On a project that reports violations, every entry should now carry
`related_objects`, each with a `kind`, a `document` and a `cross_probe`
string.

This is the step that decides whether the tool is usable at all. A
violation reported as a category and a sheet name cannot be acted on:
the only safe response to "floating input pin, somewhere on this sheet"
is to do nothing, because a NoERC marker placed by guesswork silently
suppresses a real disconnection and is worse than the warning it clears.
`cross_probe` is what Altium itself uses to jump to an object, so it is
what identifies the specific pin or net.

Compare against the Messages panel: the objects listed there for a given
violation should match `related_objects` for the same index.

**Expect `related_object_count` to be non-zero** for floating-pin and
unconnected-object violations. Zero across every violation means
`DM_RelatedObjects` returned nothing in this build, which is a different
outcome from the call faulting, and is why the count is reported
separately from the list.

**If it fails:** the risk here is `DM_PrimaryCrossProbeString`. It is
declared on `IDMObject`, the base every related object implements, so it
should be safe on all of them, but that is reasoned from the reference
rather than measured. An undeclared identifier faults where `Try/Except`
cannot catch it and halts the polling loop, so a dead loop right after
calling this tool points at that call. `Gen_GetErcViolations` is in
`scripts/altium/Generic.pas`.

---

## 9. Reading a symbol's pins no longer faults (task #34)

Needs a SchLib open.

```
lib_get_pin_list(component_name="<a symbol in that library>")
```

Expect a pin list. Then `lib_set_current_component(component_name="<the
same symbol>")` and call it with no `component_name`: the same pin list
(a by-name `lib_get_pin_list` does not hold the symbol; the set does). The
symbol selected in the editor is never used: its members cannot be read
safely, which is this step's own crash. A nameless call answers the HELD
symbol's pins, and `NO_COMPONENT` only when nothing has been held this
session. Run in order, sections 2, 3 and 7 have already held a symbol
(`lib_set_current_component`, and symbol creation holds the new one), so a
nameless call before the set answers THAT symbol's pins, not
`NO_COMPONENT` - record which symbol it names. The nothing-held case is
P8's, in a fresh session.

**This one is a fix for an observed crash, not a new feature.** The
deployed script answered
`Undeclared identifier: SchIterator_Create` and stopped the polling
loop. The identical call appears ten times in `Library.pas` and works
everywhere else; the difference was where the component came from.
Every working reader fetches it through `GetState_SchComponentByLibRef`
or a SchLib iterator, while this one used the editor's
`CurrentSchComponent` directly. DelphiScript narrows an interface at
iterator-return, and a component obtained any other way does not carry
the methods.

So this step is really asking one question: does resolving through the
library make the iterator available? If it does, the explanation holds.

`component_name` is the other half of the fix. Reading a symbol's pins
used to depend on, and disturb, whatever the editor had selected, which
is why exporting one symbol could change which symbol later calls saw.

**If it fails with the same identifier**, the narrowing explanation is
wrong. Say so rather than trying variations: that reasoning came from
comparing call sites, not from proving the mechanism, and the next step
would be to instrument rather than guess again.

**If it fails with a different identifier**, that is a second undeclared
name in the same function and the message will say which.

---

## Step 9: the multi-part scope suffix actually switches part

Reported in GH #11 against a 4-part TPS23881B, with numbers. The `@N`
suffix parsed and reached the part-switch code, and the switch itself
did nothing: `Component.CurrentPartID := N` takes the value, the
editor's part spinner does not move, and the SchLib iterator follows
the DISPLAYED part. So `obj_query` returned part 1's pins whatever the
scope said, and with the spinner moved by hand the suffix was ignored
outright. Nothing errored in either direction.

The fix drives the editor's own command, `SCH:NextComponentPart`, and
reads `GetState_CurrentSchComponentPartId` back after each step. Both
appear in two independent scripts under `reference/`, so neither is a
guess, but neither has run from this codebase.

Open a multi-part SchLib and, with the editor showing part 1:

    obj_query  scope lib_component:<NAME>@2  kind ePin
    obj_query  scope lib_component:<NAME>@3  kind ePin

Each must return that part's own pin count, and every returned pin must
carry the matching `OwnerPartId`. Then the sharper test: switch the
spinner to part 3 by hand and query `@1`. It must return part 1.

**Two ways this fails quietly.** If
`GetState_CurrentSchComponentPartId` is undeclared, the polling loop
halts, which is loud. If it returns -1 instead, the stepping is skipped
by design and the behaviour is exactly the bug being fixed: the same
wrong answer, no error. So a run that still returns part 1 is not
evidence the command is wrong, it is evidence the part id could not be
read. Report which.

The loop is bounded by `PartCount` because the command wraps at the
last part. A target that can never be reached leaves the editor moved
but not where asked, so check the spinner afterwards.

**Part 1 goes a different way, new in the previous build.** The command only
steps forward and stops at the last part, so from part 3 there is no
step back to part 1, and `@1` read whichever part was on screen.
`@1` now selects some other component in the library and then this
one again, which reopens it on part 1, and checks the pins it can see
afterwards. None of that has run in Altium. Three checks:

* Spinner on part 3 by hand, query `@1`. Part 1's pins, and the
  spinner on part 1 afterwards.
* The same in a library holding only that one component. It must
  refuse and say in `next_step` that there is no other component to
  reselect from, not answer with part 3's pins.
* A scope with no suffix at all, `lib_component:<NAME>`, with the
  spinner on part 2. It now reads part 2, the part on screen, and
  leaves the spinner where it was. It used to be treated as `@1`.

---

## 10. UNC paths survive the trip (task #44)

This release deletes the vestigial second unescape
(`StringReplace(x, '\\', '\', -1)`) from all 94 path-taking handlers.
`ExtractJsonValue` already unescapes the JSON, so the second pass was a
no-op for local paths and stripped one leading backslash from UNC
paths: `\\server\share\lib.SchLib` arrived as
`\server\share\lib.SchLib` and failed as a missing file.

No new identifiers are involved, only deletions, so the compile risk
is nil; what needs proving is the behaviour. From a machine with any
reachable share (an admin share like `\\localhost\C$\...` works):

    lib_get_components  library_path \\localhost\C$\<path-to-any>.SchLib

Before the fix this fails with a file-not-found flavoured error;
after it, the library opens and lists components. Local absolute paths
must keep working unchanged, which step 9's queries already exercise.

`tests/test_no_double_unescape.py` pins the site count at zero from
now on, so this is a one-time verification, not a recurring step.

## 11. Copy and rename actually change the library

Reported against the previous script build: `lib_copy_component` and
`lib_rename_component` both answered `success:true` while the component
count did not move, the copy's `new_name` resolved nowhere, and the
renamed part was still there under its old name.

`AddSchComponent` overrides `LibReference` with an auto-generated
`Component_<N>` on the second and later additions to a SchLib in one
session, so an assignment made before the add does not survive it.
`Lib_CreateSymbol` already re-asserts after the add for this reason;
these two did not. No new identifiers are involved, so the compile risk
is nil, and what needs proving is the behaviour.

On a scratch library, with a symbol that is not the first added this
session:

    lib_copy_component    source_name <existing>  new_name COPY_PROBE
    app_save_all
    lib_get_components    library_path <the same library>

`COPY_PROBE` must appear, and the count must be one higher. Then:

    lib_rename_component  component_name COPY_PROBE  new_name RENAME_PROBE
    app_save_all
    lib_get_components    library_path <the same library>

`RENAME_PROBE` must appear, `COPY_PROBE` must be gone, and the count
must be unchanged.

**Save before reading.** `lib_get_components` reads the FILE, so without
the save these steps can only fail. And `verified:true` is not evidence
of anything on disk: the handlers verify through `LookupLibComponent`,
which returns the component this session last created or renamed by
name, whether or not the library holds it.

**What the previous builds did, live.** Copy and rename both answered
`verified:true`, the library was saved, and the file kept the old name
and never gained the copy. Each first asked whether the new name already
existed, through the lookup that reopens the library on a miss, and a
miss is the normal answer. The reopen saved, closed and reopened the
library, so the handler went on to edit a component in a closed
document, and every later save wrote the reopened library without the
edit. `lib_batch_rename`, which never asks, persisted on the same
scratch library. The existence checks now use a lookup that cannot
reopen. That is the change these steps test.

Note that `part_count` from `lib_get_components` is not evidence of
anything here. It comes from the CompInfoReader, which has been measured
reporting 2 for a symbol created single-part whose every pin carries
`OwnerPartId 1`, while `lib_get_component_details` reported 1 for an
identically created symbol.

## 12. Deleting a project variant (highest risk in this release)

`DM_RemoveProjectVariant` is the only write in Altium's entire variant
API, and nothing in this codebase has ever called it, so it carries the
undeclared-identifier risk in full: if the name is wrong it faults where
`Try/Except` cannot catch it and takes the polling loop with it.

It is also the only step here that destroys work which cannot be
rebuilt from this bridge. A variant's entries record which components
are not fitted and which carry an alternate part, and there is no
documented way to add a variation entry back. Deleting a populated
variant means re-making every one of those decisions in the Variant
Management dialog.

**Work on a copy of a project, or take an `app_checkpoint` first.**

Create a throwaway variant in the Variant Management dialog, then:

    proj_list_variants
    proj_delete_variant   variant_name SCRATCH_VARIANT
    proj_list_variants

The reply must carry `verified: true`, `variant_count_after` one lower
than before, and `entries_removed`. The second listing must not contain
the name. If the loop stops answering instead, the identifier is not
declared in DelphiScript and the reference is wrong about it; say so
and the tool comes back out.

Then check the refusals, which cost nothing: a name that does not exist
must return `VARIANT_NOT_FOUND` and change no count, and an empty
`variant_name` must return `MISSING_PARAMS`.

`proj_set_active_variant` changed in the same release and is cheap to
check alongside. It used to report success on the strength of the name
existing, without asking which variant was actually current afterwards.
Switch to a variant and confirm the reply carries `verified: true`;
the failure reply now names `current_variant` so a switch that did not
take is distinguishable from one that did.

## 13. obj_modify stops reporting writes it did not make

Measured on a live project: `obj_modify` was asked three times to set a
sheet symbol's `FileName`, answered `matched:1, saved:true` each time,
and wrote nothing. The property is readable and had no case in the
writer, so each attempt was recorded as an unknown name in a diagnostic
buffer that only `batch_modify` ever rendered. Nothing in the reply
distinguished it from a real one, and an operator spent a session
working around a rename that had never happened.

`matched` counts what the FILTER selected. It never said anything about
whether a write landed. Every modify reply now carries `properties`
and an explicit `success`.

The cheapest check needs no sheet symbol at all. On any open schematic:

    obj_modify  object_type eNetLabel  filter <anything that matches one>
                set NotAPropertyName=1

That must come back `success:false` with `NotAPropertyName` under
`properties.unknown`, and `matched` may still be 1. Before this release
it returned `matched:1, saved:true` and nothing else.

Then the real one, on a sheet symbol whose child sheet you do NOT mind
re-pointing, or on a scratch copy of a project:

    obj_query   object_type eSheetSymbol  properties Filename,UniqueId
    obj_modify  object_type eSheetSymbol  filter UniqueId=<the id>
                set Filename=SOMETHING_ELSE.SchDoc
    obj_query   object_type eSheetSymbol  properties Filename,UniqueId

The reply must be `success:true` with an empty `properties.unknown`,
and the second query must show the new text. A write that does not
stick now reports under `properties.failed`, because the setter reads
the label back rather than trusting the assignment.

**This re-points a symbol, it does not rename a sheet.** The filename
lives in three places: this label, the file on disk, and the project's
document list. Only Altium's **Sheet Symbol Actions > Rename Child
Sheet** does all three, and it keeps the symbol's `UniqueId`, which is
the project's handle for that sheet instance. Deleting and re-placing a
symbol issues a new id, and the next Update PCB then proposes
delete-and-re-add for every component on the sheet instead of matching
them.

## 14. Polygon pour options, and the PCB writer that never reported

Three properties this codebase has never written, so this carries the
undeclared-identifier risk in full. They are declared on `IPCB_Polygon`
with both accessors:

    Property RemoveDead : Boolean Read GetState_RemoveDead
                                  Write SetState_RemoveDead;

and the same shape for `RemoveNarrowNecks` and `RemoveIslandsByArea`.
Reported from a live board as "not an exposed property on this API",
which was a fair reading of a modify that answered `matched:2` and wrote
nothing.

**Nothing here repours.** These decide what the NEXT pour produces, so
the copper does not change until `pcb_repour_polygons` runs. Check the
flag first and the pour second, or a working change looks like a failed
one.

    pcb_get_polygons
    pcb_modify_polygon  index 0  remove_dead true
    pcb_get_polygons

The reply must carry `changed: ["remove_dead"]`, `modified: true` and
`repour_needed: true`. Then repour and confirm the dead copper goes.

Set it back to `false` afterwards and confirm `changed` names it again:
a handler that ignored the value and always wrote true would pass the
first half on its own.

Two refusals, which cost nothing and are the point of the release:

    pcb_modify_polygon  index 0  hatch_style Horizontal
    pcb_modify_polygon  index 0  net NO_SUCH_NET

Both must come back with `success: false` and the field named under
`not_applied`. `Horizontal` was documented by the tool and handled by no
branch, so it changed nothing and reported success; a net name that
matches nothing did the same.

The PCB property writer also reports now, so the generic route says so
too. On any board:

    obj_modify  object_type ePolyObject  set NotAProperty=1

must come back `success:false` with the name under
`properties.unknown`. Before this it returned a bare `matched` count.

## 15. A STEP model straight onto the board, and a read that stopped moving focus

Two things from one session, and the second is why the first took so
long to diagnose.

**THIS TOOL HAS ALREADY CRASHED ALTIUM ONCE.** The first build set
Layer, x, y and StandoffHeight on the body before adding it to the
board, and the PCB engine went down with "Access violation ... Read of
address 0x20" inside ADVPCB.DLL. A null dereference at a small field
offset is a property setter reaching for state that an owning board
provides. It also assigned x and y directly, where the reference and
`Lib_Link3DModel` both use `MoveByXY` after the add.

Both are corrected: add, register, then Layer, then MoveByXY, then
StandoffHeight. Treat this step as unproven all the same, and take an
`app_checkpoint` before running it.

**`pcb_place_3d_body`** puts a STEP model on the open PcbDoc as a free
3D body, which is Altium's Place > 3D Body > Generic STEP Model. Until
now `lib_link_3d_model` was the only STEP importer in the toolset and it
writes into a `.PcbLib` footprint, so putting a fixture or a
device-under-test on a board meant inventing a library, authoring a
footprint, placing it and deleting all of it again. The call sequence
here is the one `Lib_Link3DModel` already uses, minus the footprint
binding, so every identifier in it is exercised by shipped code.

On a scratch board, with any `.step` to hand:

    pcb_place_3d_body  model_path <path>  x 1000  y 1000  standoff_height 100
    obj_switch_view    3d

The reply's `x` and `y` are READ BACK from the placed body rather than
echoed, and they are now the CENTRE OF THE BODY'S BOX: the first build
computed the move from `Body.x`, which the AD26 SDK does not declare on
a body, so that read would have stopped the loop on its first live call.
`x` and `y` must come back as 1000, 1000 (or `bbox_readback_stale` must
say why not). Confirm `standoff_applied` is true, then look: the body
must be visible, centred on 1000, 1000 and sitting 100 mils proud.

Then the rotation, after step 5 has settled `SetState`:

    pcb_place_3d_body  model_path <path>  x 1000  y 1000  rotation_z 90

`rotation_applied` true; `model_state` is null and
`rotation_readback.available` false since step 31 (the GetState read
returned zeros live), so read the rotation from a saved file or the 3D
view. And `layer` with a name the
board does not have must now answer `UNKNOWN_LAYER` and place nothing;
it used to put the body on the top layer and echo the bad name.

Then the refusals, which cost nothing:

    pcb_place_3d_body  model_path C:\nope.step

It must be `FILE_NOT_FOUND`. Then

    pcb_place_3d_body  model_path <an existing .txt file>

must be `MODEL_LOAD_FAILED`, with no dialog and `caught_exceptions`
unchanged: the extension is refused (`Model3DExtMayLoad`, a DENY-list of
formats that are certainly not a model: no extension, `.txt`, `.pdf`,
`.csv`, `.json`, `.png`, `.jpg`) BEFORE `ModelFactory_FromFilename` or the
PCB transaction is reached, with the reply the factory's Nil branch gives.
Every other extension still reaches the loader as before; whether it
raises on one it cannot read is section 39.4 probe P7.
They are separated on purpose: the path is checked before anything is
created, because `ModelFactory_FromFilename` on a missing file returns
Nil and leaves an orphan body behind.

**The focus fix has no visible output, so check it by its absence.**
Nine read-only library actions used to leave the active document on the
library they read, because focusing it is the only way the PCBServer and
SchServer accessors will answer about it. Measured: `lib_probe_footprint`
silently focused a PcbLib, and the `obj_switch_view 3d` that followed
switched the LIBRARY into 3D. The board looked untouched and the model
looked absent.

With a PcbDoc focused:

    lib_probe_footprint  library_path <some .PcbLib>  footprint_name <one>
    app_get_active_document

The active document must still be the PcbDoc. Try it with
`lib_search`, `lib_get_component_details` and `lib_audit_styles` too;
all nine are listed in `LibActionIsReadOnly`.

Then confirm the opposite, which is the half that could regress
silently: library WRITES must still leave the library focused, because
authoring is a sequence of calls against a current component.

    lib_set_current_component  <a symbol>
    lib_add_pins               <a pin or two>

The pins must land on that symbol. If they land nowhere, the restore has
been applied to writes and the authoring flow is broken.

## 16. A region's Kind, and board cutouts

Reported from a live board as "the Board Cutout flag on a Region isn't
reachable through this API". It is:

    Property Kind : TRegionKind Read GetState_Kind Write SetState_Kind;

and it had simply never been exposed, which is the third instance this
release of a property being called absent because a reply said nothing.

The five identifiers are attested rather than assumed: four independent
scripts in `reference/` COMPARE against `eRegionKind_BoardCutout`,
`_Cutout`, `_Copper`, `_NamedRegion` and `_Cavity`, so they exist in
DelphiScript. None of them ASSIGNS one, so the write is unproven in the
way `StandoffHeight` is. Read first, and take an `app_checkpoint` before
writing.

Words, not numbers, in both directions. The ordinals are undocumented,
and publishing one invites a caller to write it back.

On a board with an existing cutout:

    obj_query   object_type eRegionObject  properties Kind,Layer

At least one region must come back `board_cutout` or `cutout`. If every
region reads `unknown`, the comparison is not matching and the write
below must not be attempted.

Then, on a scratch region you do not mind losing:

    obj_modify  object_type eRegionObject  filter <one region>
                set Kind=board_cutout
    obj_query   object_type eRegionObject  properties Kind

It must read back `board_cutout`, and the board outline must show the
hole. An unknown word is refused rather than ignored:

    obj_modify  object_type eRegionObject  filter <one>  set Kind=nonsense

must come back `success:false` with `Kind` under `properties.unknown`.

---

## 17. Writing a component's UniqueId (highest risk in this release)

`sch_set_component_unique_id` and `sch_replicate_component` both assign
`ISch_Component.UniqueId`. Nothing in this repository wrote it before,
and **no independent script in `reference/` writes it at all**, so the
identifier is unattested. It is guarded with `Try/Except`, which is no
guard: an undeclared identifier is not catchable in DelphiScript, so if
the name is wrong the modal takes the polling loop down.

It also carries a second risk that has nothing to do with whether the
call works. **A UniqueId is the project's handle for a component.** Give
one the wrong value and the next Update PCB stops matching that part and
proposes delete-and-re-add for it instead, taking its placement and
routing with it. That is a worse outcome than the tool failing.

**Work on a copy of a project.** An `app_checkpoint` does not cover the
PCB side of this.

Read one first, so there is something to put back:

    obj_query   object_type eSchComponent  filter Designator=<one>
                properties UniqueId

Then write the same value back to itself, which is the only edit here
that cannot lose anything:

    sch_set_component_unique_id  designator <the same>  unique_id <what it read>

The reply must carry `success: true` and `unique_id_after` equal to what
was asked. If the loop stops answering instead, the identifier is not
declared and the tool comes back out.

`success: false` with `unique_id_after` different is the OTHER outcome
worth knowing: the call worked, Altium declined the assignment and kept
its own id. That is a real answer, not a failure of the bridge, and it
is what the comparison was added to surface.

Only then try a component you do not mind re-annotating, and check with
`proj_compare_sch_pcb` that the PCB still matches before and after.

## 18. Moving a pin to another sub-part

`lib_set_pin_owner_part` writes `OwnerPartId`, which two independent
scripts in `reference/` also write, so unlike step 17 the identifier is
attested and the risk is behavioural rather than declarative.

The contributor stated plainly that this tool has never executed against
a live Altium. Two things are worth checking because they fail quietly:

    lib_get_pin_list        component_name <a multi-part symbol>
    lib_set_pin_owner_part  component_name <the same>  pin_designators "3, 12"
                            owner_part_id 2
    lib_get_pin_list        component_name <the same>

Both pins must come back with `owner_part_id` 2, and the count in the
reply must be 2 rather than 1. The spaced form is deliberate: `"3, 12"`
used to match nothing and report `count: 0` as a successful no-op.

Then the bound, which is the one that corrupts rather than refuses:

    lib_set_pin_owner_part  ...  owner_part_id 99

must be refused. An id above the symbol's `PartCount` is accepted by the
assignment and maps to no displayable part, so the pin vanishes from
every sub-part view while the library still contains it.

`owner_part_id 0` is Part Zero and is always legal: the pin is shared
across all parts. Confirm it reads back as 0 rather than being treated
as an error.

Save with `app_save_all` and reopen the library before trusting any of
it. A library edit is real in memory and absent from disk until then.

## 19. The layout model read

`pcb.get_layout_model` reads a whole board for the new placement and
routing engines, one section per call, in Altium's internal units. It
only reads, so the cost of a mistake is a wrong or missing field, except
for one class of mistake: these identifiers are read here and are NOT
used anywhere else in this codebase. Altium's own example scripts read
them the same way, which is why they are expected to work, but an
undeclared one stops the polling loop:

| Identifier | Where |
|---|---|
| `IsKeepout` on any primitive | copper section |
| `StackShapeOnLayer`, `XStackSizeOnLayer`, `YStackSizeOnLayer`, `StackCRPctOnLayer`, `XPadOffset`, `YPadOffset` | pads section |
| `MidShape`, `MidXSize`, `MidYSize`, `BotShape`, `BotXSize`, `BotYSize` | pads section |
| `Region.HoleCount`, `Region.Holes[i]` | copper and components sections |
| `Fill.Rotation` | copper section |
| `Pad.IsPadRemoved(Layer)` | pads section |
| `Via.IntersectLayer(Layer)`, `Via.SizeOnLayer(Layer)` | copper section |
| `Obj.Polygon`, and that polygon's `Name` and `Net` | copper section |

Open any board and run each section in turn, the riskiest first:

    pcb.get_layout_model  section=pads     limit=50
    pcb.get_layout_model  section=copper   limit=50
    pcb.get_layout_model  section=board
    pcb.get_layout_model  section=components
    pcb.get_layout_model  section=rules
    pcb.get_layout_model  section=classes

Each must answer. Then check the values against the board, not just
that they arrived:

* A through-hole pad lists copper on every signal layer, a surface pad
  on its own layer only, and sizes divided by 10000 match the pad's
  properties in mils.
* A rounded-rectangle pad carries its corner percentage.
* On a board with internal planes, `board` lists `split_planes`, each
  with its layer, its net and its regions. A plane's net is read from
  there because `LayerObj.Net` on a stack layer faulted live as an
  undeclared identifier. `pcb_set_plane_net` no longer touches
  `LayerObj.Net` at all (since the first 2026-10-11 build). Since the
  2026.10.13.02 merge it writes the board's `InternalPlaneNetName` and
  reads it back (section 51 item 4); when that does not read back it
  answers `success: false`, `applied: false` and a note pointing at the
  Layer Stack Manager, and raises no dialog. Tying a plane through the
  split-plane objects is unverified and not attempted.
* A part known to be locked reports `locked: true`.
* On a board with poured polygons, a poured region or track in the
  `copper` section carries `pour` (the polygon's name) and `pour_net`
  (its net), and a region's `copper` flag is false for a polygon
  cutout.
* On a board whose vias have unused inner pads removed, a via's `sizes`
  lists every signal layer it spans, and on a removed layer the size is
  the hole size. A through-hole pad's copper entry for such a layer ends
  in `true`.
* `pcb.get_layout_model section=copper limit=50 trace=true` answers as
  without `trace`, and `workspace/layout_trace.log` gains a line
  `copper offset=0 limit=50`, then per object its index, `kind=`, and a
  line before each of `owner`, `common`, `via sizes`, `region shape` and
  `polygon points` that the object reaches. Twice the copper read of a
  board crashed the scripting system (an access violation no Try can
  catch) with nothing to say which object it was on; with `trace`, the
  last line names it. Delete the file afterwards.

---

## 20. Router output lands where the router put it

`pcb_place_vias` places a batch of vias in one call (`pcb.place_vias`),
and `pcb_place_tracks` now keeps decimals in coordinates and in width.
The router works on a grid finer than a mil and a rule set in mm is not
whole mils either: a 0.1 mm width is 3.937 mil, and parsed as an integer
it fell back to the 10 mil default.

On a scratch board, not a client's:

    pcb_place_vias  vias=[{"x": 1000.25, "y": 1000.5, "size": 12.5, "hole_size": 6.25},
                          {"x": 1100, "y": 1000, "size": 20, "hole_size": 10,
                           "net": "<a net on the board>"}]
    pcb_place_tracks tracks=[{"x1": 1000.25, "y1": 1100, "x2": 1200.75, "y2": 1100,
                              "width": 3.937, "layer": "TopLayer"}]

Both must report every item placed. Then read them back and compare,
not just the counts:

* The first via sits at 1000.25, 1000.5 with a 12.5 mil pad and a 6.25
  mil hole; the second carries its net.
* The track is 3.937 mil wide, not 10 and not 4.
* A via with an unknown layer name is counted in `failed` and named in
  `unknown_layers`, and the rest of the batch is still placed.

Then the tools that use them, on a public example board opened in Altium
(never a client's):

    pcb_autoroute        expect_file=<the board's full path>
    design_job_status    job_id=<from the reply>        until done
    design_job_result    job_id=<same>
    pcb_autoroute_apply  job_id=<same> expect_file=<same path>

* `pcb_autoroute` with another board focused answers with an error and
  starts no job.
* The job's `summary` reports completion, violations, routing layers, the
  planes it relied on and the pours that join a net. On a two-layer board
  with a ground polygon on each side, `pours` names both and the ground
  net has tracks only where a pour left it apart.
* `pcb_autoroute_apply` places exactly the `tracks` and `vias` counts the
  result lists, names the checkpoint it took, and when the job relied on
  planes or pours answers `repoured: true` with the polygons repoured
  round the new copper (`repour=false` leaves them alone).
* Then `pcb_run_drc`: Altium's own count of clearance violations and
  unrouted nets should match the job's summary. A net joined by a pour
  that Altium leaves unjoined means its repour and the engine's differ
  (a thermal relief or a neck width): write down where.
  A difference is a finding about the engine's DRC or its board read;
  write it down with the rule it came from.

Taking the routing up first, on a saved copy of a public example board:

    obj_count  object_type=eTrackObject  filter=InComponent=false|InPolygon=false|Layer=TopLayer
    obj_count  object_type=eTrackObject  filter=InComponent=true
    pcb_unroute  expect_file=<the copy's full path>

* The two counts differ from each other and from an unfiltered count:
  footprint copper and free copper are told apart. `obj_query` with
  `properties=Component,Locked,Kind` answers them all; `Kind` used to be
  refused as unknown.
* `pcb_unroute` reports tracks, arcs and vias removed. Afterwards
  `pcb_get_board_statistics` shows no free routing left, every footprint
  is intact (its pads and silkscreen drawn as before), polygons and
  keepouts are still there, and a track locked beforehand
  (`pcb_lock_net_routing`) is still there and counted in `kept_locked`.
* `pcb_unroute nets=["NOT_A_NET"]` answers with an error and removes
  nothing; with another board focused it refuses the same way.
* `obj_batch_delete` with a board type (`eViaObject`,
  `filter=InComponent=false|Net=<a net>`) deletes them and reports a
  `results` row with the count; a board type with `scope=project` is
  reported as `scope_not_supported`, not skipped in silence.
  `obj_batch_modify` takes the same board types.
* Undo (Ctrl+Z) in Altium after `pcb_unroute` puts the routing back.
* `obj_query object_type=eConnectionObject properties=Net,X1,Y1,X2,Y2,Layer1,Layer2`
  lists one row per ratsnest line, as many as `pcb_get_unrouted_nets`
  counts, each with both ends and their layers filled in.
* On a board whose nets are all joined (Altium's own DRC reports no
  un-routed net), `pcb_get_unrouted_nets` may still list stored lines.
  Read them with `properties=Net,IsRedundant,Mode` and note both values
  per line; then `pcb_get_unrouted_nets reanalyze=true` and note whether
  the count drops to 0. Both calls must answer: a halted polling loop here
  means the script engine does not know IsRedundant, Mode or AnalyzeNet.

Placement next, on the same copy:

    pcb_autoplace        expect_file=<the copy's full path>
    design_job_status    job_id=<from the reply>        until done
    design_job_result    job_id=<same>
    pcb_autoplace_apply  job_id=<same> expect_file=<same path>

* `pcb_autoplace` with another board focused answers with an error and
  starts no job; a designator in `parts` that is not on the board is
  named in the error.
* The job's `summary` reports parts moved and fixed, wirelength before
  and after, and the engine's DRC count on the placed board. Locked
  parts and connectors, mounting holes and test points are not in
  `moves`.
* `pcb_autoplace_apply` answers `moved` equal to `expected` and names
  its checkpoint. Read the parts back: each sits at the whole-mil x, y
  and rotation the job listed, on the side it started on.
* Then `pcb_autoroute` and its apply as above, and `pcb_run_drc`.
  Component clearance violations Altium reports on the placed board
  that the job's summary did not are a finding about the placer's
  body outlines: write down the two parts.

---

## 21. Closing one document by its path

`app_close_document` closes the document it is given, found by its full
path, not whatever has the focus: `WorkspaceManager:CloseObject` acts on
the focused object and once closed a different project than the one
named. It calls `Client.CloseDocument`, which the reference scripts use;
nothing in this codebase called it before, so an undeclared identifier
here would stop the polling loop.

On scratch documents only:

1. Open two scratch boards; focus the FIRST.
2. `app_close_document file_path=<the SECOND's full path>`. It answers
   `closed: true`, the second is gone from `app_list_documents`, and the
   first is still open and still focused.
3. `app_close_document` on a path that is not open answers `NOT_LOADED`
   and closes nothing.
4. Place anything on the remaining scratch board without saving, then
   `app_close_document` on it: it answers `MODIFIED` and the board stays
   open. If the modified read misses the edit and Altium raises its save
   prompt instead, record it: the refusal is a floor, not a guarantee.
5. The same call with `discard_changes=true` closes it with no prompt,
   answers `discarded: true`, and the file on disk is unchanged (compare
   its hash before and after).

---

## 22. Replacing placeholders, aligning a body, and the body list

Three additions to the 3D tools, none of which touches an identifier
step 5 does not already cover: `replace` removes bodies with the
footprint's `RemovePCBObject`, exactly as `lib_delete_footprint_primitives`
does; `align` moves with `MoveByXY` from the box `BoundingRectangle`
reports; and `lib_get_footprint_bodies` only reads. Its `model_state`
reads (`ModelType`, `FileName`, `Rotation`; `GetState` was dropped in
step 31 after it returned zeros live) are as unproven
as step 5's, which is why `model_state` is OFF unless passed as true: a
default list touches none of them. Run step 5 first, and only then
`model_state=true`. If that stops the loop, the bridge needs a manual
restart, and the default list still works.

First the default list, which reads no model at all:

    lib_get_footprint_bodies  library_path <copy.PcbLib>

Every IPC footprint must show `bodies` above 0 and the reply
`model_state: false`. Then, after step 5 has passed, on a copy of a
library whose footprints came from the IPC generator:

    lib_get_footprint_bodies  library_path <copy.PcbLib>  model_state true

Every IPC footprint must show `extruded` above 0 and `generic` 0. Then
one footprint in detail:

    lib_get_footprint_bodies  library_path <copy.PcbLib>  footprint_name <fp>
                              model_state true

The generator's body is centred on the land, so its `bbox` and
`frame.pads_bbox` must be roughly concentric about 0, 0. If both are far
from 0 the frame is wrong (the footprint origin read off a component that
was not current), which is the fault `fpcheck.py` found in
`lib_get_pad_geometry`; write down the numbers.

Then the swap:

    lib_link_3d_model  library_path <copy.PcbLib>  component_name <fp>
                       model_path <the maker's STEP>  replace true
                       align center

`replaced.removed_bodies` must equal the body count read above,
`replaced.remaining_before_add` must be 0, and `body.bbox` must now be
centred on the origin. `replaced.board_removal` must read
`{requested: false}`: the replace removes from the footprint only. Save,
`lib_reload_library`, list the footprint again: one `generic` body and
no `extruded` one. If the placeholder is back after the reload, the
footprint-side removal is not enough on this build; repeat on a fresh
copy with `remove_from_board true` (step 24's measurement) and record
both replies.

Run `align pads` on a footprint whose land is NOT centred on its origin
(a connector), and confirm `body.bbox` centres on `frame.pads_bbox`
instead.

Finally the refusal: `align middle` answers `BAD_ALIGN` before anything
is touched. The `.txt` file as `model_path` with `replace true` answers
`MODEL_LOAD_FAILED` (placeholders left where they were) with no dialog:
refused by extension before the transaction, as in section 15.

---

## 23. Pad editing inside a PcbLib footprint

`lib_modify_footprint_pads`, `lib_delete_footprint_pads` and
`lib_set_footprint_description` are new, and the add tools now write a
roundrect on every copper layer. Every Altium member they touch is already
used elsewhere, so an undeclared identifier is ruled out by construction;
what is unmeasured is BEHAVIOUR, and each handler reports what it observed so
one run settles it. Call `app_ping` after every step.

On a scratch .PcbLib only:

1. `lib_create_footprint` a scratch footprint, then `lib_add_footprint_pads`
   with: pad "1" through-hole roundrect (hole_size 40, 70 x 70, corner_radius
   25); pad "2" SMD rectangular; two SMD pads both named "3"; one unplated
   blank pad (designator "", hole_size 40, plated false). The reply must say
   `roundrect_requested: 1, roundrect_unconfirmed: 0`. If it says
   `roundrect_unconfirmed: 1`, the through-hole fix did not take: record it
   and continue, step 2 will say more.
2. `lib_modify_footprint_pads` on "1" with `shape: "rectangular"`, then
   again with `shape: "roundrect", corner_radius: 40`. Record each reply's
   `shape_status` (`confirmed` or `top_only`, either possibly ending
   `_via_top_shape`, or `unconfirmed`) and the pad's `mode`, `bottom` and
   `mid` blocks. `confirmed` means writing the top/bottom/inner stack entries
   works; a `_via_top_shape` suffix means only `TopShape :=
   eRoundedRectangular` did, and the add path should try it first.
3. Modify "3" with only `x`: it must answer `AMBIGUOUS` and change nothing.
   With `index: 1, x: 10.5` exactly one pad moves, and
   `lib_get_pad_geometry` shows which (the second "3" in its list).
4. Modify "2" with `layer: "BottomLayer"`, `mask_expansion: 2`,
   `paste_expansion: "rule"`. Every field must come back with `mismatched`
   empty.
5. `app_save_all`, `lib_reload_library`, `lib_get_pad_geometry`: everything
   from steps 2-4 must still be there. This is the step that proves the
   `PCBM_BeginModify` bracket made the edits persist.
6. `lib_delete_footprint_pads` on "3" index 0 with `confirm: true`. The
   library was reloaded in step 5, so expect `removed: 1`, `still_listed: 0`,
   `double_listed: 0`. Then add one more pad "9" and delete it WITHOUT a
   reload: that pad is listed twice, so expect `double_listed: 1`, and
   `board_removal: {requested: false}`. Save, reload, read: neither pad
   may come back. If one does, removing it from the footprint is not
   enough; step 24's delete measurement says whether `remove_from_board`
   cures it. Record which pad came back.
7. `lib_set_footprint_description` once with `footprint_name` and once with
   `descriptions` for two footprints. Save, reload, `lib_get_footprints`: the
   new text must be on disk.

---

## 24. Footprint fills, regions, sub-mil tracks, and the doubled text

Six handlers changed and three are new: `lib_add_footprint_fills`,
`lib_add_footprint_regions` and `lib_set_current_footprint`. Every
footprint add tool, the two pad tools included, now takes
`component_name` and `library_path`, made
current through `ResolveLibFootprint`, which READS THE SWITCH BACK and
refuses when it did not take. Work on a scratch .PcbLib, take an
`app_checkpoint` first, and run the riskiest row first.

`IsKeepout` is the one identifier nothing else writes. It is read in
PCB.pas and PCBGeneric.pas, so the name resolves, and Altium's SDK
assembly declares a SetState_IsKeepout dispatch member on IPCB_Primitive,
but no shipped code has assigned it. Only a keep-out on a copper layer
reaches it; a keep-out on KeepOutLayer does not:

    lib_set_current_footprint  component_name <scratch fp>
    lib_add_footprint_regions  regions [{kind keepout, layer KeepOutLayer,
                               vertices [[0,0],[20,0],[20,20],[0,20]]}]
    lib_add_footprint_regions  regions [{kind keepout, layer TopLayer,
                               vertices [[0,0],[20,0],[20,20],[0,20]]}]

The first must report `added: 1` and `keepout_requested: 0`. The second
must report `keepout_requested: 1` and `keepout_applied: 1`. If the
polling loop stops on the second, the write is the cause: refuse
kind=keepout on non-KeepOut layers in `_regions_payload` and drop
`keepout` from `_fills_payload` until another spelling is proven.

The outline write is PCB_PlaceRegion's, used for the first time inside a
library. `vertices` in the reply must equal the points sent, and a region
whose contour did not take is reported under `failures` and never kept.
Then a cutout, whose `Kind` write is step 16's:

    lib_add_footprint_regions  regions [{kind cutout, layer TopLayer,
                               vertices [[0,0],[20,0],[20,20],[0,20]]}]

`kind_applied` must be 1. Fills on TopPaste and TopSolder, then
`app_save_all`, `lib_reload_library`, and `lib_probe_footprint` with the
same footprint: every fill and region must still be there, once.

REGISTRATION ORDER. `RegisterLibPrimitive` now emits the four calls in
the order `Lib_AddFootprintPad` carries inline (footprint add, board
add, then both broadcasts); the version before this interleaved them.
Every track, arc, text, fill and region add above runs through it, so
the save-reload-probe of fills and regions is also the check that the
order persists, exactly once.

THE DOUBLED TEXT. Measured 2026-10-03: one `lib_add_footprint_text`
call, two identical texts. Add one text to a scratch footprint and read
the reply: `new_objects` must be 1, `group_yields` 1 or 2,
`duplicates_found` 0, `duplicate_cleanup` `none`. Any other
`duplicate_cleanup` than `none` or `removed` means the guard refused to
clean up, because the created text's address could not be read or the
footprint did not list it; record the reply. Then `app_save_all`,
`lib_reload_library`, and
`lib_probe_footprint`: the string must appear exactly once. If it
appears twice with `duplicates_found` 0, the copy is made at save time,
not in the session; repeat on a fresh footprint with
`registration="footprint"` and again with `registration="board"`, and
make whichever persists once the default in `RegisterLibPrimitive`'s
caller.

THE DELETE, AND WHETHER THE LIBRARY BOARD HALF IS NEEDED. Every library
removal (`lib_delete_footprint_primitives`, `lib_delete_footprint_pads`,
the 3D `replace`, the duplicate-text cleanup) removes from the FOOTPRINT
only by default, the removal that has run live. The library-board half
is behind `remove_from_board=true` on each of them, through one shared
helper (`LibBoardRemoveEntries`) with one rule: the footprint is made
current and read back first, the footprint half runs as usual, and only
then is the board swept by address, never for an object the footprint
still lists. Nobody has watched that run. Two premises in this code
contradicted each other until now (a comment said a footprint-only
removal comes back after save and reload, while the pad and 3D paths
relied on it), and this measurement is what settles them. On a scratch
footprint, `app_checkpoint` first, `app_ping` after every call:

1. Add two texts, "A" and "B", WITHOUT a reload in between (each is
   listed twice). Then
   `lib_delete_footprint_primitives  footprint_name <fp>  object_type string
   layer TopOverlay  confirm true` with NO `remove_from_board`. Expect
   `removed: 2`, `double_listed: 2`, `still_listed: 0`,
   `board_removal: {requested: false}`. `app_save_all`,
   `lib_reload_library`, probe. RECORD whether the texts came back.
2. If they did NOT come back, the footprint half is enough: the board
   half is unnecessary, keep it opt-in (or delete it), and the
   contradiction resolves in favour of the pad and 3D paths. Stop here.
3. If they DID come back, the footprint half is not enough. Add two new
   texts the same way and delete them with `remove_from_board true`.
   Read `board_removal`: `made_current: true`, `objects: 2`, and
   `listed_before` is the number the library board still listed after
   the footprint removal (2 confirms the premise; 0 means the board does
   not hold them and something else brings them back). `removed` and
   `still_listed: 0` say the board let go. `app_ping`, then
   `app_save_all`, `lib_reload_library`, probe: they must be gone.
4. If step 3 stops the loop, record the last reply and do not retry: the
   board removal stays opt-in for good, and the documented workaround
   (save, reload, delete again) stands.

Only a passing step 3 justifies making `remove_from_board` the default,
and then for all four paths at once, by changing the one default in the
Python tools; the Pascal already shares the helper.

Sub-mil: `lib_add_footprint_tracks` with `x1: 12.5`, and
`lib_add_footprint_arc` with `radius: 12.5`, must read back 12.5 through
`obj_query` or the saved file, not 12 or 13, and not the 100 mil
default the old integer parse returned for any decimal.

---

## 25. Component kind on a library symbol

On scratch documents only. New scratch SchLib with one symbol `KIND_TEST`.
`lib_set_component_kind KIND_TEST net_tie_no_bom` -> `kind_after:
net_tie_no_bom`. Then `graphical`, `jumper`, `standard_no_bom`,
`standard`. After each: Properties panel Component Type must show the same
words. ENUM_LAYOUT_MISMATCH on the first numbered kind means the anchors
moved (record `details`). `app_save_all`, then
`lib_get_component_details` reports `component_kind`. Mismatch between
panel and reply for 5 or 6 = the ordinal assumption is wrong: remove those
two from `ComponentKindOrdinal`.

---

## 26. Pin properties on a library symbol

On scratch documents only. On `KIND_TEST` with pins 1 and 2:

    lib_set_pin_properties  component_name KIND_TEST
        pins [{designator "1", name "EN", electrical_type "input", length 300},
              {designator "2", new_designator "1"}]

-> op 1 applied+verified, op 2 `NEW_DESIGNATOR_EXISTS`, pin 2 unchanged.
`electrical_type: "inptu"` -> `BAD_ELECTRICAL_TYPE`, pin unchanged.
Multi-part symbol: edit a part-2 pin with no scope -> applied; the editor's
displayed part does not move. Then `obj_modify` (lib scope, ePin, filter
`Designator=1`, set `Designator=9`) -> matched 1 AND `lib_get_pin_list`
shows 9 (the pin `Designator` write in `SetSchProperty`, which used to
report handled and change nothing).

---

## 27. Current model

On scratch documents only. Symbol with footprint A:
`lib_link_footprint replace=false footprint=B` -> `is_current: false,
current_footprint_count: 1`; `lib_set_current_model model_name=B` ->
`current_count: 1`, models list shows only B current; `lib_link_footprint
replace=false footprint=C set_current=true` -> `demoted: 1`. The Properties
panel Footprint list must agree each time.

---

## 28. No-ERC on a pin

On scratch documents only. Scratch sheet with an op-amp, input pin
unconnected: `run_erc` / `proj_get_erc_violations(limit=0)` lists the
floating input. `sch_place_no_erc designator=U1 pin=3` -> `on_pin_end`
names U1/3. `sch_get_no_erc_markers(cross_check_erc=True)` immediately (no
reload): record whether `still_reported` names U1/3. Then step 29, then
recompile and re-read. Then the Messages panel. Record all three: this is
the measurement that resolves the open No-ERC question.

---

## 29. Reload a sheet

On the step-28 sheet: `sch_reload_document` -> `saved, closed, reopened:
true`, `free_document_after: false`, `recompiled: true`, `objects_before ==
objects_after`. Then delete a net label with `obj_delete`,
`sch_reload_document`, and confirm `objects_after.net_labels` dropped by one
and `proj_get_nets` no longer names the label's net. Make an edit, then
`sch_reload_document save=false` -> `MODIFIED`, sheet still open.

Live session order for steps 25 to 29: 25 first (one ordinal-risk write on
a scratch library), then 27, 26, 29, 28.

---

## 30. IPC batch generator, library save and batch rename (not yet run live)

Two new handlers, `library.save_library` and `library.rename_footprints`,
use only members that shipped handlers already use: `Lib_SaveLibrary` is
`SaveOneDocByDocRef`'s `SetModified` + `DoFileSave('')` plus `FileAge`
from `AgesForPaths`; `Lib_RenameFootprints` is `Lib_RenameFootprint`'s
walk and `Name` write in a loop. Low compile risk; run item 1 below
first anyway, on a scratch library, because the batch tool chains them.

1. On a scratch PcbLib with two footprints `A` and `B`, open in Altium:

   ```
   lib_rename_footprints(renames=[{"from": "A", "to": "A2"},
                                  {"from": "Missing", "to": "X"},
                                  {"from": "B", "to": "A2"}],
                         library_path=<scratch>)
   ```

   Expect statuses `renamed`, `not_found`, `name_exists`, and
   `pending_save: true`. Then `lib_save_library(<scratch>)`: expect
   `ok: true`, `written_to_disk: true`, `still_modified: false`. Then
   `lib_reload_library` and `lib_get_footprints`: `A2` and `B`. Confirm
   `app_ping` answers after each call.

2. `lib_ipc_batch_generate(csv_paths=[<a one-row SOT23 CSV whose
   FootprintName contains a '.'>], library_path=<scratch>,
   preflight_only=True)`: expect `ok: true` and the name under
   `preflight.expected_names`. Nothing opens in Altium.

3. The same call without `preflight_only`. RECORD these read-backs, which
   settle what was taken from the extension's binary rather than seen:
   `runs[0].dialog.dialog_title`, `runs[0].dialog.before.options`,
   `runs[0].dialog.before.output_folder_candidates`,
   `runs[0].dialog.destination.label`,
   `runs[0].dialog.add_file.route.route` (`handle` or `keyboard`),
   `runs[0].dialog.run.finished_by`, `runs[0].dialog.run.seen_stop`,
   `runs[0].dialog.run.main_window_blocked`, `runs[0].engine_free`,
   `runs[0].report_closed`. Expect `ok: true`, the footprint under
   `generated` with its '.', and one entry under `renamed`.

4. Run item 3 again unchanged: it must refuse at `stopped.stage =
   "preflight"` naming the footprint as already present, and open nothing.

---

## 31. Deletes that stick, a STEP on its footprint, and the saved-file reader (run live 2026-10-05, results at the end)

Four fixes from one live session on 2026-10-04 (the previous bridge build,
carrier.PcbLib, throwaway footprint `ZZ_BRIDGE_TEST`, frame origin
reported as (50000, 50000) mils). Each was found by reading the SAVED
file, which is now a tool: `lib_read_pcblib_file` parses the .PcbLib with
olefile and no Altium, so every check below reads disk, not memory.
Pascal risk is low: the only Altium call that is new anywhere is
`Model.SetState` with a NON-ZERO fourth argument (`Dz`), the same member
and signature that ran live with 0; `LibBoardStillListsCount` is the
read-only board walk `LibBoardRemoveEntries` ran live, now run only while
the footprint is current; `LibParkCurrentElsewhere` is a LibraryIterator
walk (ResolveLibFootprint's) plus `LibMakeCurrentVerified`, both shipped;
`GetState` is no longer called at all.

A review of the first version of these fixes added three things the
steps below check:

* **Two theories for bug A.** The evidence fits "a session-created
  primitive's board copy survives" AND "the save rebuilds the CURRENT
  footprint from the board". `reload_first` (save + reopen) answers the
  first only; the deletes now also PARK the footprint (make another one
  current) after the reopen when it came back current, and item 3b
  separates the theories.
* **One call settles it.** `board_still_listed` is 0 by construction for
  a footprint that is not current, so it is now NULL then, and the
  deletes save again and read the FILE themselves (`verify_on_disk`,
  `persisted`), with `ok`/`success` false when a removed primitive is
  still on disk.
* **A stale box read is not a placement failure.** `placement.basis`
  says which box was measured; a stale read measures the expected box
  and reports `ok: null` (unverified) when that is on the footprint.

What was measured, so the read-backs below can be judged:

* **Bug A, delete.** A pad created this session and deleted
  (`still_listed 0`) was still in the file after `lib_save_library`; a
  second delete with `remove_from_board=true` found it on the library
  board, removed it (`remove_calls 2`) and still listed it
  (`still_listed 1`); the save wrote it again, now first among the
  pads. After `lib_reload_library` the same footprint-only delete
  persisted, and that save read `was_modified false`.
* **Bug B, 3D placement.** `align` none (the default) left the STEP
  origin at ABSOLUTE (0, 0): `body.bbox` x -50024.30..-49931.60 in the
  footprint frame, and `bbox_expected` identical to it, so the check
  passed. The file stored `MODEL.2D.X=-49989.5mil MODEL.2D.Y=-50020mil`;
  every other body in that library stores 0mil. `offset_z` 3 wrote
  `STANDOFFHEIGHT=3mil` beside `MODEL.3D.DZ=0mil`, and `OVERALLHEIGHT`
  stayed the model's own top: not lifted. Altium's AD26 example
  `MiniPC.PcbLib` places generic bodies with `MODEL.3D.DZ` (787.4016mil,
  39.3701mil) and derived, negative standoffs.
* **Bug C, rotation read-back.** The rotation landed (file
  `MODEL.3D.ROTX=90.000 ROTZ=45.000`) while `GetState` read 0, 0, 0 in
  both `lib_link_3d_model` and `lib_get_footprint_bodies(model_state=true)`.
* **Bug D, IPC batch.** The destination radio reads `Generate all
  footprints in H:\git\multiviewer\hardware\multiviewer\carrier.PcbLib`
  on this build, not `... current PcbLib file`; the run refused at
  `configure` (safely, form closed unstarted).

Run in this order on a scratch footprint (`<lib>` = the carrier library
or a scratch copy, `<fp>` = a new throwaway footprint). Confirm `app_ping`
answers after each call.

1. **Version.** `app_ping`: `altium_script_version` = the version in
   step 0 (this step ran live against the previous build).

2. **The reader on its own.** `lib_read_pcblib_file(<lib>, <fp>)` before
   anything else (after creating `<fp>` and one `lib_save_library`):
   expect `ok: true`, `found: true` and the footprint's current pads.
   No Altium call is made; it works with Altium closed.

3. **A session-created pad stays deleted (bug A, the one call).**
   `lib_add_footprint_pads(component_name=<fp>, library_path=<lib>,
   pads=[{designator:"1", x:0, y:0}, {designator:"3", x:200.25, y:0}])`,
   then, WITHOUT saving:

   ```
   lib_delete_footprint_pads(footprint_name=<fp>, library_path=<lib>,
                             pads=[{designator:"3"}], confirm=true)
   ```

   (`reload_first` and `verify_on_disk` are on by default.) Expect
   `precheck.selected: {"3": 1}`, `reload_first: {ran: true, saved: true,
   reloaded: true}`, `removed: 1`, `still_listed: 0`, `marked_modified:
   true`, and `persisted: {checked: true, saved: true, absent: ["3"],
   still_present: [], ok: true}`. RECORD `target_was_current`,
   `parked_elsewhere`, `target_current_during_removal` and
   `board_still_listed` (null whenever the footprint was not current
   during the removal: the board then holds none of its pads, so there
   is nothing to count; a number means it WAS current). That one call
   settles the default path; `lib_read_pcblib_file(<lib>, <fp>)` shows
   the same thing by hand.

   3b. **Which theory (disk-loaded AND current).** Add pad `5`, then
   `lib_save_library(<lib>)`, `lib_reload_library(<lib>)`,
   `lib_set_current_component(<fp>)`, and delete it with
   `reload_first=false` (no reopen, no park, no on-disk check of its
   own). Expect `target_was_current: true`,
   `target_current_during_removal: true` and an integer
   `board_still_listed`. Save, then `lib_read_pcblib_file(<lib>, <fp>)`.
   Pad `5` ABSENT: disk-loaded is enough, current or not. Pad `5`
   PRESENT: the save rebuilds the current footprint from the board, and
   parking is what makes item 3 pass. Record which.

4. **The old path still reproduces (optional, diagnostic).** Add pad `4`,
   then delete it with `reload_first=false`. RECORD `board_still_listed`
   and `target_was_current`; save; read the file. Pad `4` present in the
   file with `board_still_listed` above 0 confirms the board copy is the
   carrier of the bug.

5. **Primitives delete uses the same rule.**
   `lib_add_footprint_track` on `<fp>`, then
   `lib_delete_footprint_primitives(footprint_name=<fp>,
   library_path=<lib>, object_type="track", confirm=true)`: expect
   `reload_first.reloaded: true`, `persisted: {checked: true, ok: true,
   still_present: 0}` with `by_kind.tracks.after` one less than
   `.before`. `board_still_listed` follows item 3's rule.

   5b. **The 3D replace, on disk.** With a body already on `<fp>`:
   `lib_link_3d_model(component_name=<fp>, library_path=<lib>,
   model_path=<step>, replace=true)`. Expect `reload_first.ran: true`,
   `replaced.route: "delete_then_link"`, `replaced.removed_bodies` = the
   bodies there before, `replaced.parked_elsewhere` recorded,
   `applied.layer_source: "replaced body"`, and `persisted: {ok: true,
   bodies_after: <bodies before - removed + 1>}`. Then
   `lib_read_pcblib_file(<lib>, <fp>)` lists exactly that many bodies
   (one, when one was replaced).

6. **The STEP origin lands on the footprint origin (bug B), and Dz lifts
   it.** First a CONTROL, Dz 0:

   ```
   lib_link_3d_model(component_name=<fp>, library_path=<lib>,
       model_path=<Crystal_SMD_2520-4Pin_2.5x2.0mm.step>,
       rotation_x=90, rotation_z=45, offset_x=10.5, offset_y=-20,
       replace=true)
   ```

   RECORD `persisted.matching_bodies[0]` `standoff_height_mils` and
   `overall_height_mils` (the 2026-10-04 run suggests about -39.37 /
   39.37 once Altium derives them). Then the same call with
   `offset_z=3`.

   Expect `applied.rotation: true`, `applied.z_offset: true`,
   `applied.standoff_height: false`, `placement.ok: true` with
   `placement.basis: "bbox_readback"` and `placement.gap_mils` 0
   (overlapping the pads) or small, `placement_ok: true`, and
   `body.bbox` within a few hundred mil of the origin (NOT near -50000).
   `expected_in_file`: `model_2d_x_mils 10.5`, `model_2d_y_mils -20`,
   `model_3d_rotx 90`, `model_3d_rotz 45`, `model_3d_dz_mils 3`; with
   replace, `persisted.ok: true` checks those against the SAVED file
   (`lib_read_pcblib_file(<lib>, <fp>)` shows them by hand: `model.x_mils`
   10.5, `model.y_mils` -20, `rot_x` 90, `rot_z` 45, `dz_mils` 3).

   THE LIFT: both heights must read about 3 mil higher than the control
   (about -36.37 / 42.37), in the reply's `body` and in the file.
   `dz_mils` 3 alone proves the value was STORED, not that the model
   moved. If the file has DZ 3 but the heights equal the control, report
   the body's heights as inconsistent with its model (Altium did not
   build Dz into them). If `dz_mils` reads 0, the Dz argument is ignored
   by this build: record both heights and report it.

7. **The placement check fails a misplaced body.** Link the same model
   with `offset_x=5000` (far from any pad), NO replace: expect `ok:
   false`, `success: false`, `placement_ok: false`, `placement.basis`
   recorded, and a `reason` naming the gap. Undo it with
   `lib_delete_footprint_primitives(footprint_name=<fp>,
   object_type="componentbody", confirm=true, library_path=<lib>)`.
   NOTE: that removes EVERY body on `<fp>`, including item 6's, and its
   `reload_first` save writes the misplaced body to disk before the
   delete removes it. Expect `persisted.ok: true` and ZERO bodies in
   `lib_read_pcblib_file(<lib>, <fp>)` afterwards; re-run item 6 before
   item 8.

8. **center and pads still work.** Link with `align="center"` and then
   `align="pads"`, `replace=true`: `placement.ok: true`, and the body's
   box centre within 1 mil of (offsets) / (pads centre + offsets).

9. **The rotation read-back is honest (bug C).**
   `lib_get_footprint_bodies(footprint_name=<fp>, library_path=<lib>,
   model_state=true)`: each model has `rotation: null` and
   `rotation_readback.available: false`; nothing reports 0, 0, 0. The
   link reply has `model_state: null`, `rotation_readback_matches: null`
   and `rotation_readback.available: false`.

10. **The IPC batch generator accepts the path caption (bug D).**
    Repeat step 30 item 3. Expect `runs[0].dialog.destination.ok: true`,
    `match: "path"` and `named_path` equal to `<lib>`, and the run to go
    past `configure`. The refusal for a caption naming a DIFFERENT
    library is covered by tests/test_ipc_batch_dialog.py (the flow
    focuses the target library first, so it is hard to provoke live).

Record every read-back named above in this section once run, the way
steps 22 to 24 record theirs.

FOLLOW-UPS RECORDED HERE, not fixed by this change:

* `lib_add_footprint_text`'s duplicate cleanup (`RemoveLibTextByAddress`)
  removes a session-created text from the CURRENT footprint, the case
  item 3 is about, without saving, reopening or parking first. A
  `duplicates_removed` above 0 may not survive the save; its docstring
  says so. Settle it the way item 3 does once item 3b has run.
* `pcb_place_3d_body` still writes Z as the body's StandoffHeight (Dz 0).
  Whether that lifts a STEP model on a BOARD is unmeasured; in a library
  it did not. Measure it (place with `standoff_height=50`, look in 3D,
  save the board), then route it through the model's Dz if it does not.


### Results 2026-10-05

Run on Altium 26.10.1.5, the previous bridge build (the 2026-10-04 SCRIPT_VERSION), library
`carrier.PcbLib`, footprint `ZZ_BRIDGE_TEST`. Item numbers are the ones
that run used; the list above in brackets where it differs.

* **Item 1, the reader** (above: 2): PASS. `lib_read_pcblib_file` read 97
  footprints, `incomplete: []`, the footprint's state as expected.
* **Item 3, session-added pad, default delete**: PASS.
  `reload_first` ran, saved and reloaded; `removed: 1`;
  `target_was_current: false`, `parked_elsewhere: false`,
  `board_still_listed: null`; `persisted: {absent: ["3"], ok: true}`.
  `marked_modified` was FALSE: the delete does not set the library's
  dirty flag. Harmless here (verify_on_disk saves anyway), still open.
* **Item 3b, which theory**: SETTLED, the second one. Pad `5`
  disk-loaded (saved + reloaded), the footprint made CURRENT with
  `lib_set_current_footprint`, deleted with `reload_first=false`:
  `target_was_current: true`, `target_current_during_removal: true`,
  `parked_elsewhere: false`, `board_still_listed: 1`. After
  `lib_save_library` pad `5` was STILL IN THE FILE. **Being CURRENT is
  sufficient to bring a removed primitive back**, even one loaded from
  disk. This does NOT show that having been added this session is not
  also sufficient: no run has removed a session-created primitive from a
  footprint that was not current, without a reload. The mechanism (the
  save writing the library board's copy of the current footprint) is
  inferred, not measured. Item 3 passed because after the reopen the
  target was not current. Parking alone (without a reload) could not be
  measured: `park_current` was sent only when reload_first ran. CHANGED
  in the tools since (not yet re-measured): both deletes now send
  `park_current` by default with or without reload_first
  (`park_current=False` opts out; with `remove_from_board` it is never
  sent), and a 3D replace that names its footprint always removes the
  old bodies through the parked delete, with or without reload_first
  (the link handler's own replace removes from the CURRENT footprint;
  with no footprint named it still runs, and the reply warns). All of
  it measured on PADS only.
* **Item 6, primitives delete** (above: 5): PASS, `persisted` tracks
  2 -> 1.
* **Item 7, replace link, Dz 0** (above: 6, the control): PASS. The
  saved model: x 10.5, y -20, rot 90/0/45, dz 0. Body bbox
  -24.2986..68.4048 / -77.9048..14.7986 mil, on the footprint;
  standoff -39.3701, overall 39.3701 mil.
* **Item 8, offset_z 3** (above: 6, the lift): PASS. File `dz_mils` 3,
  and both heights moved by exactly +3 (standoff -36.3701, overall
  42.3701): Altium honours SetState's Dz and builds it into the heights.
* **Item 9, offset_x 5000** (above: 7): correctly `ok: false`, gap
  4832.2 mil, with the undo hint; the componentbody delete removed both
  bodies and persisted.
* **Item 10, align** (above: 8): `center` put the box centre exactly on
  (10.5, -20); `pads` exactly on the pads' centre + offset (62, 27.5).
* **Item 13, IPC batch** (above: 10): FAILED for a NEW reason: the
  options read empty because COM was not initialised on the executor
  thread. Being fixed on its own branch (`bridge-com-thread`).
* **Also seen**: `lib_add_footprint_tracks` and
  `lib_modify_footprint_pads` leave the library's Modified flag false
  too (`lib_save_library` then reports `was_modified: false` and still
  writes). Harmless with lib_save_library; `app_save_all` relies on the
  flag, so an edit made only through these tools may be skipped by it.
* **Real use**: a STEP that is a one-instance ASSEMBLY wrapper (written
  by FreeCAD, the transform carried by the assembly placement), linked
  with `align="none"` and zero offsets, lands correctly: body at (0, 0),
  correct heights.

Still open after this run:

* Parking WITHOUT a reload, now the default on both deletes, is not yet
  measured live. Two runs, because they answer different questions:
  * (a) repeat item 3b (a DISK-LOADED pad, footprint made current,
    `reload_first=false`) with the new default: expect
    `parked_elsewhere: true`, `target_current_during_removal: false`,
    and the pad ABSENT from the saved file. Settles "parking is enough
    for a disk-loaded primitive".
  * (b) MEASURED 2026-10-06 (Altium 26.10.1.5, the previous bridge build,
    carrier-local b36ebd4), PASS: fresh footprint ZZ_PARK_TEST created
    (so it was current), three pads added this session, then
    `lib_delete_footprint_pads(pads=[3], reload_first=false)` with the
    default park -> `target_was_current: true`, `parked_elsewhere: true`,
    `target_current_during_removal: false`, `board_still_listed: null`,
    `marked_modified: true` (the dirty flag IS set on this path);
    `lib_save_library` then `lib_read_pcblib_file`: pad 3 ABSENT, pads
    1 and 2 present. So parking alone is enough for a SESSION-CREATED
    primitive on a footprint that was current: being added this session
    is NOT a second cause, and the 2026-10-04 resurrection was the
    current-footprint mechanism all along. Consequence: `reload_first`
    (and its save-every-pending-edit side effect) is no longer needed
    for correctness on the parked paths; it can become opt-in. (a) is
    implied by (b) but still unrun as a separate case.
* The handlers park BEFORE they validate: `Lib_DeleteFootprintPrimitives`
  before the `layer` check (INVALID_LAYER), `Lib_DeleteFootprintPads`
  before resolving the pad selectors. A delete they then refuse still
  leaves another footprint current, not put back (documented in both
  docstrings). At the next SCRIPT_VERSION bump: move the park below the
  validation, and correct the three Library.pas comments (above
  `LibParkCurrentElsewhere`, and beside the park in both handlers) that
  still say `park_current` is "sent only after the tool's reload_first
  ran".
* `marked_modified` (and the Modified flag after track adds and pad
  edits) still reads false.
* The IPC batch's COM initialisation, on `bridge-com-thread`.
* `lib_auto_link_3d_models` gained `layer` (bare footprints otherwise get
  Mechanical 1, the house convention is Mechanical 13), `model_map` and a
  per-footprint `persisted` check (count, placement and, with a layer,
  the saved body's layer); not yet run live. Run it on two bare
  footprints with `layer="Mechanical 13"`, `replace=True`: expect each
  `persisted.layer_ok: true`, and `lib_read_pcblib_file` showing
  `Mechanical13` on both bodies. A library that renames that layer gives
  `layer_ok: null` (the file names layers canonically), by design.

---

## 32. A sheet delete that removes the sheet, and a save-all that leaves libraries alone (run live 2026-10-07, results at the end)

Two fixes from the live session of 2026-10-06 (Altium 26.10.1.5, the previous bridge build
(the 2026-10-04 SCRIPT_VERSION), the multiviewer carrier project). **FPC was NOT
run on this build**, so nothing here has been through a Pascal compiler. The
first cut (the previous build of this same date, never deployed) carried a stray `End;` after
`App_SaveAll` that would have stopped the whole script project compiling,
with the linter, build.py and the offline suite all green; the linter now
counts Begin/Try/Case/Record against End (`block-end-closes-nothing`,
`tests/test_pas_block_balance.py`). Step 1 is the compile check. Every
Altium call below is already shipped and has run live elsewhere.
`Gen_DeleteSheet` now uses `Project.DM_RemoveSourceDocument` and the
membership re-read (`Proj_RemoveDocument`'s), `Client.GetDocumentByPath` +
`Client.CloseDocument` + a second lookup (`App_CloseDocument`'s, step 21),
`ServerDoc.FileName` (`App_SetActiveDocument`'s), and `SetModified` +
`DoFileSave` on the project's own server document (`SaveProjectMembers`'s).
The save-all policy adds one read of `ServerDoc.Modified` inside
`SaveOneDocByDocRef`, already read by `DocIsModified`. `FileAge`,
`ReadFileContent`, `FileExists` and `DeleteFile` are all shipped.

What was measured, so the read-backs below can be judged:

* **proj_delete_sheet.** `file_path=...STUBS.SchDoc` with
  PWR_IN_BARREL.SchDoc FOCUSED replied `{success: true, removed: STUBS}`,
  but it CLOSED PWR_IN_BARREL (the next `app_set_active_document` on it
  said `Document not loaded`), STUBS stayed a project member after
  `WorkspaceManager:SaveObject ObjectKind=FocusedProject` and stayed
  loaded, and the next `app_save_all` wrote STUBS back to disk. With STUBS
  focused first: success again, still a member. The old handler ran
  `WorkspaceManager:CloseObject` and `RemoveObjectFromProject` with
  `ObjectKind=Document|FileName=<path>` and never looked again;
  `CloseObject` acted on the focused document. What worked every time:
  `proj_remove_document`, then `app_close_document`, then a project save.
  That is the route the handler takes now.
* **app_save_all.** With nothing edited it answered `documents_seen 45,
  documents_attempted 29, documents_written 29, still_dirty 0`. The write
  of carrier.SchLib moved its mtime, which keys the offline symbol cache,
  and broke a parallel tuning run; every SchDoc got fresh parameter
  UniqueIDs (git churn). It forces the write (`SetModified(True)` then
  `DoFileSave`) because the modified flag is unreliable on some edit paths
  (`lib_add_footprint_tracks`, `lib_modify_footprint_pads` read
  `modified: false` after real edits). Default now: a library that reads
  unmodified is skipped; `include_libraries=true` restores the old pass.

Work on a COPY of a project (or a scratch project) with at least three
sheets, `A`, `B` and `STUBS`, all saved and loaded, plus one open
library `<lib>` (a scratch copy) that has not been edited. Confirm
`app_ping` answers after each call.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`.

2. **The default save-all leaves an unedited library alone.** Note
   `<lib>`'s mtime. `app_save_all` with no arguments. Expect
   `include_libraries: false`, `only_modified: false`, `<lib>` listed in
   `skipped_unmodified` AND in `libraries_skipped` (once each, even if it
   is open in two projects), a `warning` naming the skipped library count,
   `documents_skipped` at least 1, and `<lib>`'s mtime UNCHANGED on disk. The sheets are still written (that is the old
   behaviour for sheets, and `documents_written` counts them).

3. **include_libraries restores the old pass.** `app_save_all
   include_libraries=true`. Expect `libraries_skipped: []`, no `warning`,
   `documents_skipped: 0`, and `<lib>`'s mtime NEWER.

4. **A library that reads modified is still written by default.** Make
   an edit that is known to raise the flag (`lib_set_component_description`
   on a throwaway symbol in `<lib>`, then `app_list_documents` shows
   `<lib>` `modified: true`). `app_save_all`: `<lib>` is NOT in
   `skipped_unmodified` and its mtime moves. If the flag read false
   instead, record which edit path that was: it is the trade-off this
   default makes, and `lib_save_library` is the route for it.

5. **only_modified leaves clean sheets alone.** With nothing edited,
   `app_save_all only_modified=true`. Expect `saved: true`,
   `documents_attempted: 0`, every open document in `skipped_unmodified`,
   the `note` saying the policy left them alone, and no SchDoc mtime
   changed (`git status` on a versioned copy shows no churn).

6. **The delete removes the NAMED sheet while another is focused.**
   Focus sheet `A` (`app_set_active_document`). Then
   `proj_delete_sheet file_path=<STUBS full path>`. Expect
   `success: true`, `removed: true`, `still_member: false`,
   `was_loaded: true`, `closed: true`, `previously_focused` = `A`'s path,
   `previously_focused_still_open: true`, `project_saved: true`,
   `project_file_checked: true`, `project_file_lists_sheet: false`,
   `file_deleted: false`,
   `file_still_on_disk: true`. Then confirm independently:
   `app_set_active_document` on `A` SUCCEEDS (the old handler closed it);
   `proj_list_documents` no longer lists STUBS; `app_list_documents` does
   not show STUBS loaded; the .PrjPcb on disk has no `DocumentPath=` line
   naming STUBS.
   `project_saved` compares `FileAge` before and after, and `FileAge` is
   only 2-second accurate: if the project was saved moments before this
   call it can read `false` for a save that did happen. Judge the save by
   `project_file_lists_sheet: false` and the file itself; `success` does not
   depend on `project_saved`.

7. **The removal survives a save-all.** Note STUBS.SchDoc's mtime.
   `app_save_all`. STUBS.SchDoc's mtime is UNCHANGED (the old run wrote
   it back because it was still loaded and a member).

8. **Refusals change nothing.** Make an edit on `B` so it reads modified,
   then `proj_delete_sheet file_path=<B>`: expect error `MODIFIED`, and
   `B` still a member and still loaded. `proj_delete_sheet` on a path
   that is not a member: `NOT_FOUND`. On a project member that is not a
   sheet (the .PcbDoc, if any): `NOT_A_SHEET`. Reduce a scratch project
   to one sheet and delete it: `SAFETY_CHECK`.

9. **delete_file only after the project lets go.** Re-add a throwaway
   sheet `ZZ.SchDoc` (`proj_add_sheet name=ZZ`), then
   `proj_delete_sheet file_path=<ZZ> delete_file=true`. Expect
   `project_file_checked: true`, `project_file_lists_sheet: false`,
   `file_deleted: true`,
   `file_still_on_disk: false`, and the file gone from the folder.

10. **save_project=false leaves the file alone.** Add another throwaway
    sheet and delete it with `save_project=false`: expect
    `project_saved: false` and `success: true` (membership removed in
    memory, sheet closed); `proj_list_documents` does not list it, but
    the .PrjPcb on disk still names it until `proj_save`. Adding
    `delete_file=true` to such a call must answer `file_deleted: false`
    and `success: false`: the file on disk still names the sheet.

11. **An unreadable project file is not a confirmation.** Open a sheet as
    a free document (not in any project) if your build lets
    `proj_delete_sheet` reach one, or otherwise skip this step: any reply
    with `project_file_checked: false` must carry
    `project_file_lists_sheet: null`, `success: false` (with
    `save_project` on) and, with `delete_file`, `file_deleted: false`.

If step 6 answers `project_saved: false` with
`project_file_lists_sheet: true`, the project document's `DoFileSave`
did not write the .PrjPcb on this build: record it, and the next fix is
the project-save route, not the removal (the reply already says
`removed: true` from the membership re-read).

Not changed, and worth knowing: `application.stop_server` (which
`app_detach` sends) and `proj_save`, `proj_close` still write every open
member whether modified or not, as before. So `app_detach` still writes
an unedited open library and moves its mtime (the symbol-cache key) at
every session end. Left that way on purpose for now: detach is the last
safety net for library edits whose modified flag never propagated, and
skipping there would lose them silently with no later call to report it.
Changing it is a separate decision. `app_checkpoint` passes
`include_libraries=true`, so a checkpoint still captures library edits
whose flag never propagated.

### Results 2026-10-07

Run on Altium 26.10.1.5, the second 2026-10-06 bridge build, the live multiviewer
carrier project (31 sheets, `carrier.SchLib` open and unedited). Steps 3,
4 and 11 and three of step 8's refusals were NOT run: they write the
library (moving the symbol-cache key) or aim a refusal at the project's
PcbDoc, and belong on a scratch project.

* **Step 1, version**: PASS. `app_ping` matched that build; the
  script project compiled (the compile check the stray `End;` would have
  failed).
* **Step 2, default save-all**: PASS. `libraries_skipped:
  [carrier.SchLib]` with the warning; the SchLib's mtime unchanged on disk
  (still 2026-10-06 16:16:09), so the symbol cache stayed fresh.
* **Step 5, only_modified**: PASS. `documents_seen 49,
  documents_attempted 0, documents_written 0, documents_skipped 24` (every
  loaded document), the library also in `libraries_skipped`, and the note.
* **Steps 6 + 9, the named sheet with delete_file**: PASS. A throwaway
  `ZZ_DELETE_TEST` (from `proj_add_sheet`), `multiviewer__PWR_MECH.SchDoc`
  focused, `proj_delete_sheet delete_file=true`: `success, removed,
  still_member false, was_loaded, closed, previously_focused = PWR_MECH,
  previously_focused_still_open, project_saved, project_file_checked,
  project_file_lists_sheet false, file_deleted, file_still_on_disk false`.
  Independently: PWR_MECH still loaded, no `ZZ_DELETE_TEST` anywhere in the
  .PrjPcb, the file gone, and `git diff` on the .PrjPcb EMPTY (byte-identical
  to the committed project).
* **Step 7, survives a save-all**: covered. The step-5 save-all ran after
  the delete and did not recreate the deleted file.
* **Step 8, refusals**: `NOT_FOUND` PASS ("Sheet not found in project" for
  a path that is not a member). `MODIFIED`, `NOT_A_SHEET`, `SAFETY_CHECK`
  not run (see above).
* **Step 10, save_project=false**: PASS, and it found a defect ELSEWHERE.
  First try: add `ZZ_T10`, delete it with `save_project=false
  delete_file=true`. It answered `project_file_lists_sheet: false,
  file_deleted: true`, not the predicted `true / false`, because the
  .PrjPcb had NEVER named the sheet: its mtime was still that of the step-9
  save. **proj_add_sheet does not write the project file**, although it
  answers `saved: true` (that is the sheet's own save). The delete handler
  judged the disk correctly and deleting was safe; the add is fixed in
  step 33. Second try with the project written first
  (`WorkspaceManager:SaveObject ObjectKind=FocusedProject`, grep showed
  `DocumentPath=ZZ_T10B.SchDoc`): `success: false, project_saved: false,
  project_file_lists_sheet: true, file_deleted: false, file_still_on_disk:
  true`, with the reason. Exactly as specified. Cleanup by a project save +
  removing the file left the .PrjPcb byte-identical to the committed one.

---

## 33. A new sheet that the project file names (run live 2026-10-07, results at the end)

Found by step 32's step 10 (2026-10-07, the second 2026-10-06 bridge build):
`proj_add_sheet` answered `{success: true, saved: true, added_to_project:
true}` while the .PrjPcb on disk kept its old timestamp and did not name
the sheet. `saved` was the new SHEET's `DoFileSave`; `DM_AddSourceDocument`
changes membership in memory only. Two more holes in the same handler:
`added_to_project` was a literal `True` written after the call, and
nothing looked at the target path, so a name whose file existed would have
been replaced by a blank sheet.

`Gen_AddSheet` now refuses an existing file (`EXISTS`) before
`Client.OpenNewDocument`, re-reads membership with `Gen_SheetIsMember`,
and (with `save_project`, the default) writes the project through its own
server document and reads the file back with `Gen_ProjectFileState`:
exactly `Gen_DeleteSheet`'s route, which steps 6, 9 and 10 above ran live.
The two helpers moved above `Gen_AddSheet` (DelphiScript wants a function
declared before use). Every Altium call here has already run live; the
compile is what is new.

Work on the live project only if the .PrjPcb is committed (the check is a
`git diff`), or on a copy. Focus some other sheet `A` first.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`.

2. **A default add writes the project.** Note the .PrjPcb's mtime and two
   SchDoc mtimes. `proj_add_sheet name=ZZ33`. Expect `success: true,
   sheet_saved: true, saved: true, added_to_project: true,
   project_file_checked: true, project_file_lists_sheet: true` and usually
   `project_saved: true` (FileAge is 2-second accurate; judge the save by
   the file). The .PrjPcb contains `DocumentPath=ZZ33.SchDoc` and its mtime
   moved; the two SchDoc mtimes did NOT (the project save writes only the
   project document).

3. **An existing name is refused.** `proj_add_sheet name=ZZ33` again:
   error `EXISTS`; `ZZ33.SchDoc`'s mtime unchanged; `proj_list_documents`
   lists it once.

4. **save_project=false.** `proj_add_sheet name=ZZ33B save_project=false`:
   `success: true, added_to_project: true, project_saved: false`,
   `project_file_lists_sheet: false` (the file was read and does not name
   it), and the .PrjPcb mtime unchanged.

5. **Clean up with the delete.** `proj_delete_sheet file_path=<ZZ33B>
   delete_file=true`, then the same for `ZZ33`: both `success: true,
   file_deleted: true`. `git diff` on the .PrjPcb is empty.

### Results 2026-10-07

Run on Altium 26.10.1.5, the first 2026-10-07 bridge build, the live multiviewer
project, `multiviewer__PWR_MECH.SchDoc` focused.

* **Step 1**: PASS. `app_ping` matched; `tool_catalog` showed the new
  `save_project` parameter (the Python side reloaded too).
* **Step 2**: PASS. `proj_add_sheet name=ZZ33`: `success, sheet_saved,
  saved, added_to_project, project_saved, project_file_checked,
  project_file_lists_sheet` all true. The .PrjPcb gained
  `DocumentPath=ZZ33.SchDoc` and its mtime moved; PWR_MECH, AUDIO and
  carrier.SchLib mtimes did not.
* **Step 3**: PASS. `EXISTS` with the message; `ZZ33.SchDoc`'s mtime
  unchanged; `proj_list_documents` listed it once.
* **Step 4**: PASS. `ZZ33B` with `save_project=false`: `success: true,
  project_saved: false, project_file_lists_sheet: false`, the .PrjPcb mtime
  unchanged.
* **Step 5**: **FAILED, and it is a defect in step 32's delete, not in the
  add.** ZZ33B (never in the file) deleted cleanly. ZZ33 (in the file)
  answered `success: false, still_member: false, closed: true,
  project_saved: true, project_file_lists_sheet: true, file_deleted:
  false` with the delete_file reason. In memory ZZ33 was gone
  (`proj_list_documents`); on disk it was still listed although the file
  had just been rewritten. Repeated in isolation (add ZZ34, wait four
  seconds, delete ZZ34): same answer, timestamp moved, sheet still listed.
  `WorkspaceManager:SaveObject ObjectKind=FocusedProject` run straight
  after wrote the file WITHOUT the sheet, byte-identical to the committed
  project, and rewrote no sheet (every SchDoc mtime unchanged). So
  SetModified + DoFileSave on the project's own server document writes a
  REMOVED member back in. Step 32 never met this because every sheet it
  deleted had never been in the file (the step-33 add defect). The handler
  stayed honest throughout; it could not finish. Fixed in step 34.

---

## 34. A removal that the project file actually loses (run live 2026-10-07, results at the end)

Step 33's step 5 (2026-10-07, the first 2026-10-07 bridge build): after
`DM_RemoveSourceDocument`, SetModified + DoFileSave on the project's own
server document rewrote the .PrjPcb with the removed sheet still listed.
`WorkspaceManager:SaveObject ObjectKind=FocusedProject` dropped it. The
same document save did write a new member after `DM_AddSourceDocument`.

Both handlers now write through `Gen_WriteProjectFile`: the document save
first, a read-back with `Gen_ProjectFileState`, and only if the file
disagrees, `SaveObject` with `ObjectKind=FocusedProject` (the parameters
`obj_run_process` passed live) and a second read-back. The fallback runs
only when the target project IS the focused project, because that process
saves whichever project has the focus. If the project was focused when the
call began but closing the sheet moved the focus away (deleting the focused
sheet), another loaded sheet of the project is shown first with
`Client.ShowDocument` (`App_SetActiveDocument`'s call); a `project_path` that
was not focused is never refocused. Replies gain `project_save_route`:
`document_save`, `save_object`, `save_object_refocused`, `not_focused`,
`not_confirmed`, or `none` (no project write attempted). A review of the
first cut also made the add's read-back match the sheet's EXACT path
(`Gen_ProjectFileStateExact`; a same-named sheet in another folder must not
confirm it) and write the project only for a sheet that is on disk.

Focus some other sheet `A` of the focused project first.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`.

2. **The add still takes the document save.** `proj_add_sheet name=ZZ35`:
   as step 33's step 2, plus `project_save_route: document_save`.

3. **The removal falls back and the file loses the sheet.**
   `proj_delete_sheet file_path=<ZZ35> delete_file=true`. Expect
   `success: true, project_save_route: save_object,
   project_file_lists_sheet: false, file_deleted: true`. `git diff` on the
   .PrjPcb is empty and no SchDoc mtime moved.

4. **A sheet the file never named needs no fallback.** `proj_add_sheet
   name=ZZ35B save_project=false`, then `proj_delete_sheet file_path=<ZZ35B>
   delete_file=true`: `success: true, project_save_route: document_save`
   (the file already does not name it), and `git diff` on the .PrjPcb
   empty: the document save must not have written back the sheet that
   step 3 removed.

5. **Deleting the FOCUSED sheet.** `proj_add_sheet name=ZZ36`, then
   `app_set_active_document` on ZZ36 itself, then `proj_delete_sheet
   file_path=<ZZ36> delete_file=true`. Expect `success: true`,
   `file_deleted: true`, `project_save_route` = `save_object_refocused`
   (or `save_object` if Altium kept the focus inside the project; either
   way the file must lose the sheet), `git diff` on the .PrjPcb empty, and
   `proj_get_focused` naming the same project afterwards.

6. **Unfocused project (needs a second open project; skip otherwise).**
   Add a throwaway sheet to project `P2`, save `P2`, focus a sheet of the
   first project, then `proj_delete_sheet file_path=<it>
   project_path=<P2>`: expect `project_save_route: not_focused`,
   `success: false`, the reason naming `ObjectKind=FocusedProject`, and
   the FIRST project's .PrjPcb mtime UNCHANGED (the fallback must never
   save the wrong project).

### Results 2026-10-07

Run on Altium 26.10.1.5, this build, the live multiviewer project
(31 sheets), `multiviewer__PWR_MECH.SchDoc` focused unless stated.

* **Step 1**: PASS. `app_ping` matched; `tool_catalog` showed both
  handlers' parameters (the Python side reloaded too).
* **Step 2**: PASS. `proj_add_sheet name=ZZ35`: `success`,
  `project_save_route: document_save`, `project_file_lists_sheet: true`;
  `DocumentPath=ZZ35.SchDoc` in the file.
* **Step 3**: PASS, the defect step 33 found is gone.
  `proj_delete_sheet delete_file=true` on ZZ35 (listed in the file):
  `success: true, project_save_route: save_object,
  project_file_lists_sheet: false, file_deleted: true`. `git diff` on the
  .PrjPcb EMPTY; every SchDoc, SchLib and PcbDoc mtime unchanged.
* **Step 4**: PASS. ZZ35B added with `save_project=false` (route `none`,
  file not naming it), then deleted: `project_save_route: document_save`,
  `file_deleted: true`, `git diff` on the .PrjPcb EMPTY (the document save
  did not write back the sheet step 3 removed).
* **Step 5**: PASS. ZZ36 added, focused, deleted: `success: true,
  project_save_route: save_object` (Altium moved the focus to PWR_MECH,
  inside the project, so no refocus was needed; the refocus branch is
  still not exercised live), `file_deleted: true`, `git diff` EMPTY,
  `proj_get_focused` the same project.
* **Step 6**: not run (needs a second open project).


---

## 35. A single-sheet object edit says the write is deferred (run live 2026-10-07, results at the end)

Measured on the previous build (2026-10-07, Step 0 of the 7.1 audio redraw):
five `obj_modify` label renames on `02.SchDoc` each answered `saved: true`,
and the file's mtime did not move until `app_save_all only_modified=true`
wrote it. `ProcessActiveDoc` / `ProcessDocByPath` set `Saved := True` right
after `MarkDocDirtyByPath`. They now answer `saved: false` with
`marked_modified`, `pending_save` and `written_to_disk: false` (the fields
`lib_delete_footprint` already uses). No Altium call changed; only the reply.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`.
2. **The reply is honest.** Focus a sheet, `obj_modify` a net label to its
   own current text. Expect `saved: false, marked_modified: true,
   pending_save: true, written_to_disk: false`, and the sheet's mtime
   UNCHANGED.
3. **The explicit save writes it.** `app_save_all only_modified=true`:
   `documents_written: 1` (that sheet) and its mtime moves.

### Results 2026-10-07

Run on Altium 26.10.1.5, this build, `02.SchDoc` of the live multiviewer
project. Step 1 PASS (versions matched). Step 2 PASS: `obj_modify` on the
AUD_PWR_OK label answered `{matched: 1, saved: false, marked_modified: true,
pending_save: true, written_to_disk: false}`; mtime still 13:09:56 (the
earlier save). Step 3 PASS: `documents_attempted 1, documents_written 1`,
libraries skipped.

---

## 36. Parameter stamps arrive exactly, Description is the component's, new parameters hidden (run live 2026-10-10: steps 1-6 and 8-10 on the 2026-10-08 build, step 7 re-verified on the 2026-10-10 build; all PASS)

Field reports 2026-10-08 (carrier project, the bridge build of 2026-10-07), all on
`sch_set_components_parameters`: a `;` in an MPN or Description arrived as
`,`; key `Description` created a new VISIBLE parameter called Description
instead of setting the component's description; and every parameter the tool
created sat visible at (0,0) of the sheet. Now: the stamp payload is
percent-escaped (`% ; ~ =`) with `value_encoding=pct1` and decoded by the new
`PctDecode` (Main.pas) after the split; `Description` (any case) writes
`ComponentDescription` and `Comment` (any case) writes `Comment.Text`, like
`Value`; a CREATED parameter gets `Location := Comp.Location` and
`IsHidden := True` (shown only with `visible=true`, which also writes
`ShowName := False`) before it is added. `SetCompParamText` delegates to the
new `SetCompParamTextEx(..., False)`, so the singular stamp, the SPICE
stampers and design_execute_plan's stamping get the hidden-on-the-part default
too. Existing parameters still only have their `Text` changed.

Review follow-ups (same day): `Description` ALSO updates a parameter called
Description that the part already carries (`ModifyCompParamIfPresent`,
modify-only, never creates; reported in `description_parameters_also_set`),
because the old route updated it and leaving it stale gives the part two
descriptions. A shown created parameter (`visible=true`) is placed one 100 mil
row per already-shown parameter away from the Comment label (along X on a part
turned 90/270) and takes the part's `Orientation`, instead of every one piling
onto the symbol origin. The plan executor and the sheet emitter now check the
reply for the `value_encoding` echo too (`stamp_reply_problem`), and a policy
refusal is no longer reported as a stale script. Empty values are reported in
`skipped_empty`; control characters other than tab/CR/LF are refused.

**Run live 2026-10-10 on the 2026-10-08 build: every step passed except 7**
(results at the end of this section). Step 7's two defects were fixed in the
2026-10-10 build (`SetCompParamTextEx`, see "Fixed after the live run"
below), and step 7 was re-run live on it the same day: PASS. Independent of
the version check, the reply echoes `value_encoding: "pct1"`
only from the new handler, and every sender reports its absence (the tool as
a `warning`, the plan paths as a note), so step 2 below detects a stale
script by itself.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`
   (the 2026-10-08 build is the one the results below were measured on),
   `version_match` true.
2. **Exact values.** On a scratch sheet with a placed part (say `R1`) that has
   NO parameter called `ZZ Test`, call `sch_set_components_parameters` with
   `stamps=[{"designator": "R1", "ZZ Test": "A;B,C=D|E \"q\" 50% ~x~~y 10µF"}]`.
   Expect `value_encoding: "pct1"` in the reply, NO `warning`,
   `parameters_created: 1`, `created: [{designator: "R1", name: "ZZ Test"}]`,
   `key_routing: {"ZZ Test": "parameter"}`. Read it back
   (`proj_get_component_info` or `obj_query` on `eParameter` `Name=ZZ Test`):
   the text must be byte-identical to what was sent, `;` and `~~` included.
3. **Created hidden, on the part.** The same parameter reads `IsHidden` true
   and its `Location` equals `R1`'s location (not 0,0). Nothing new is drawn
   on the sheet (`sch_render_svg` or a screenshot).
4. **Description is the component's.** Stamp
   `{"designator": "R1", "Description": "Res; thick film"}`. Expect
   `properties_set: 1`, `parameters_created: 0`,
   `key_routing: {"Description": "property:ComponentDescription"}`; the
   component's `ComponentDescription` reads `Res; thick film`, and NO
   parameter named Description was added (count `eParameter` `Name=Description`
   on R1 before and after: unchanged), `description_parameters_also_set: []`.
   Then a part that ALREADY carries a Description parameter (an imported or
   DbLib-style symbol; or add one by hand to `R2` with text `old`): stamp
   `{"designator": "R2", "Description": "Res; new"}`. Expect
   `properties_set: 1`, `parameters_modified: 1`, `parameters_created: 0`,
   `description_parameters_also_set: [{designator: "R2", name:
   "Description"}]`; BOTH `ComponentDescription` and the parameter read
   `Res; new`, and the count of `Description` parameters on R2 is still 1.
5. **Comment.** Stamp `{"designator": "R1", "Comment": "10k"}`: `properties_set:
   1`, the part's Comment shows `10k`, no parameter named Comment created.
6. **Existing parameter untouched otherwise.** Make `ZZ Test` visible and move
   it by hand, then stamp a new value: only the text changes, it stays visible
   and where it was (`parameters_modified: 1`).
7. **Opt-in show (the unproven writes).** Stamp two fresh keys
   `{"designator": "R1", "ZZ Shown": "x", "ZZ Shown2": "y"}` with
   `visible=true`: both are visible, value only (no `ZZ Shown =` prefix),
   NOT on the symbol origin and NOT on each other: on an upright R1 the first
   sits EXACTLY 100 mil below R1's Comment label (plus 100 mil per OTHER
   parameter already shown; the Comment and Designator do not count), the
   second 100 mil below that. Repeat on a part rotated 90 (`R3`) whose
   Comment label is horizontal (Comment `Orientation` 0, the usual case): the
   two read `Orientation` 0 like the COMMENT, not 1 like the part, and sit
   100 and 200 mil BELOW the Comment (step -Y), clear of its text. If R3's
   Comment is itself vertical (Comment `Orientation` 1 or 3), they read that
   orientation and step +X beside it instead. Every new row reads the same
   `Justification` as its part's Comment (`obj_query` both); set one
   Comment to Center by hand first and confirm its rows follow it. The bridge answers `app_ping`
   afterwards. If the loop stops here, `ShowName`, `Orientation` or
   `Justification` is not a parameter member on this Altium: drop that line
   from `SetCompParamTextEx` rather than retrying.
8. **Nothing written says so.** Stamp `{"designator": "R1", "ZZ Test": ""}`:
   the reply is `ok: false` with `skipped_empty: [{designator: "R1", key:
   "ZZ Test"}]` and `ZZ Test` keeps its text (the tool cannot clear a
   parameter). Offline-verified; this only confirms nothing reaches Altium.
9. Undo the scratch edits (or close the sheet without saving).
10. **Keep-out regions read back as keep-outs (offline, VERIFIED 2026-10-08
    on a copy of the carrier library).** `lib_read_pcblib_file` on
    `carrier.PcbLib` with footprint `QSFP28_CAGE_1x1_PF-10_TE2170704-5`
    (the `-5` is part of the stored name; without it the reader answers
    `found: false`): 4 regions, every one `is_keepout: true`,
    `keepout_restrictions: 31`, `keepout_restrictions_defaulted: false`,
    `keepout_restricts: [via, track, copper, smd_pad, th_pad]`; the footprint
    has no fills (any it had would read `is_keepout: false`). Run with the
    branch reader against a copy of the library: exactly that. Needs no
    Altium; repeat after deploy only to confirm the installed package is
    this tree.

### Results 2026-10-10

Run on Altium 26.10.1.5, the 2026-10-08 build, a scratch sheet. Steps 1-6
and 8-10 PASS. **Step 7 FAIL**, two defects, both in the `visible=true`
placement only (hidden creation, the default, was correct):

* **(a) One row too far.** Every shown created parameter landed one 100 mil
  row further from the Comment than specified. `SetCompParamTextEx` counted
  the visible parameters its eParameter iterator returns, and that iterator
  ALSO returns the component's visible Comment parameter (`obj_query` lists
  `Name=Comment` as an eParameter on every part), so the label the rows are
  measured from was counted as a row.
* **(b) Across the Comment on a turned part.** On a part at `Orientation` 1
  whose Comment label is horizontal (`Orientation` 0), the new parameters took
  the part's orientation (vertical text) and stepped along X, so they ran over
  the Comment text.

### Fixed after the live run (the 2026-10-10 build)

`SetCompParamTextEx` skips parameters named Comment and Designator (any case)
when counting shown rows, and a shown created parameter takes the COMMENT
label's orientation (the part's only when the Comment cannot be read) and
steps perpendicular to the Comment's text: Comment 0/2 -> -Y, 1/3 -> +X.
Review addition: it also copies the Comment's `Justification`, because the
100 mil step clears the Comment only when both labels share orientation AND
justification (a TopLeft or Center Comment would still overlap a
factory-justified row). Hidden creation is unchanged. Offline proof:
`tests/test_shown_param_rows_follow_the_comment.py` evaluates the counting
guard from the source on the live iterator contents and reproduces the
2026-10-08 build's misplacement exactly ((1200, 2000) vertical where
(1000, 1900) horizontal was wanted); it fails on that build and passes on
this one. Re-run live on the 2026-10-10 build: PASS (below).

### Step 7 re-verified live 2026-10-10 (the 2026-10-10 build): PASS

Altium 26.10.1.5, a free scratch sheet holding R1 (upright), R2 (carrying a
Description parameter shown by hand) and R3 (rotated 90). `app_ping` matched
before, during and after.

* **R1 upright:** Comment at (1100,2860), Orientation 0, Justification 0. ZZ
  Shown at (1100,2760) and ZZ Shown2 at (1100,2660), exactly 100 and 200 mil
  below it, both Orientation 0 / Justification 0, value only (screenshot), not
  on the origin and not on each other.
* **R3 rotated, Comment horizontal and set to Center by hand first:** Comment
  at (4040,3100) Justification 4; the rows at (4040,3000) and (4040,2900),
  Orientation 0 (the Comment's, not the part's 1), Justification 4. Step -Y.
* **R2 with one OTHER shown parameter:** the new row lands 200 mil below the
  Comment; the Comment and Designator were not counted (defect (a) fixed).
* **Persisted:** after a save the sheet's records match the in-memory
  positions, carry no `IsHidden=T`, and R3's rows carry `Justification=4`.
* The bridge answered `app_ping` afterwards. `ShowName` reads empty through
  `obj_query` (not listed as unreadable), so "value only" rests on the
  screenshot and the saved records.

---

## 37. Heights that reach the save, a discard that does not deny itself, the component description under its own key (run live 2026-10-10 on the 2026-10-10 build, results at the end; `marked_modified` fixed since, see section 38)

Three more gaps found on 2026-10-10 with the 2026-10-08 build, by the 3D-link
agent and the live checker on the multiviewer carrier:

* **`lib_set_footprint_height` lost its writes on save.** 55 heights set,
  every reply `changed: true`; after `lib_save_library` 51 of the 55 were not
  in the file, and making each footprint current again before the save kept
  all 55. That is the measurement. Which four survived was not recorded:
  probably the footprints that were current at some point after their write
  (a hypothesis, not a reading). The handler wrote `Target.Height` on
  whatever the walk found, restored the previous current footprint, never
  switched to the edited one, and never marked the library modified. Now the
  write is bracketed `PCBM_BeginModify`/`PCBM_EndModify`, then the library
  SWITCHES TO the footprint on every path (the measured recipe): a footprint
  that was not current is made current and the caller's current one
  restored; one that was ALREADY current (`footprint_name` empty, the
  default, or naming it) is parked on another footprint
  (`LibParkCurrentElsewhere`) and switched back, since making the current
  footprint current is a no-op. The height is read back AFTER the switches
  (a switch that reloaded the stored copy cannot hide behind an earlier
  read; the pre-switch read is reported as `read_back_before_switch_mm`),
  and the library document is marked modified. The reply adds
  `read_back_height_mm`, `read_back_before_switch_mm`, `was_current`,
  `parked_elsewhere_first`, `made_current_after_write`, `current_restored`,
  `marked_modified`, `pending_save` (true only when the height changed AND
  the switch happened) and `written_to_disk: false`; `changed` comes from the
  final read.
* **`app_close_document(discard_changes=true)` replied `discarded: false`**
  for a sheet whose title bar showed it modified, while the file on disk was
  untouched (the edits WERE thrown away). `discarded` was `Discard And
  WasModified`, and `WasModified` is `IServerDocument.Modified`, a floor that
  missed the editor's edits. Now three-valued: `true` (read modified, thrown
  away), `null` (discard asked, the read said clean: unknown), `false` (not
  asked); plus `discard_requested`, `modified_read` and
  `file_unchanged_on_disk` (FileAge before and after the close). Review
  follow-up: `proj_delete_sheet` (`Gen_DeleteSheet`) built the same field the
  same way; it now uses the same three values (`false` also when the sheet
  was not loaded or the named document was not the one closed) and carries
  `discard_requested` and `modified_read`.
* **`proj_get_component_info` `parameters['Description']` had two
  meanings**: the component's description when the part had no Description
  parameter, the parameter's value when it had one (the compiled DM list
  synthesises the entry). Both the single and the batch handler now report
  `component_description` (`DM_Description`) and keep `parameters` to real
  parameters. Review follow-up, before any live run: the first version
  dropped the Description entry whose VALUE equalled the description, which
  drops a REAL parameter in the bridge's own normal state (the Description
  stamp writes both, equal on purpose). The copy is now chosen by structure:
  two or more Description entries, drop one equal to the description; one
  entry with different text, keep it; one entry with the same text, the
  placed part on its sheet decides (the walk `ModifyCompParamIfPresent`
  makes), and with the sheet not open the entry is KEPT and
  `description_parameter_present` is `null`. The fields are built only with
  `with_parameters` (the default), so a `with_parameters=false` call never
  reaches `DM_Description`. The dashboard's component drawer, which reads
  `description`, now takes `component_description`.

**Run live 2026-10-10 on the 2026-10-10 build** (results at the end of this
section): the heights reach the file and the discard reply holds, but
`marked_modified` read false on every height write, fixed in this build
(section 38). On any re-run, run 37.6 FIRST: it is the step that can stop
the loop.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`,
   `version_match` true.
2. **Heights survive the save.** On a COPY of a .PcbLib with at least three
   footprints, focus footprint A, then `lib_set_footprint_height` on B and C
   (NOT current) with distinctive values (say 7.11 and 7.22 mm). Each reply:
   `changed: true`, `read_back_height_mm` = the value (and
   `read_back_before_switch_mm` the same), `was_current: false`,
   `parked_elsewhere_first: false`, `made_current_after_write: true`,
   `current_restored: true`, `marked_modified: true`, `pending_save: true`,
   `written_to_disk: false`, and the current footprint is still A.
   `lib_save_library` on that path: `written_to_disk: true`. Then
   `lib_read_pcblib_file` (it reports each footprint's `height_mm` from the
   file since this build; or close and reopen the library and
   `lib_get_footprints`): B and C carry 7.11 and 7.22 mm. On the
   2026-10-10 build `marked_modified` read FALSE here while the tab showed
   `*` (results below); this build reports the read-back with a `mark`
   object, re-verified in section 38. This is the field
   case; on the 2026-10-08 build B and C came back with their old heights.
   If the loop stops on the first call, `PCBM_BeginModify` on a library
   footprint is the suspect (it is shipped in `Lib_SetFootprintDescription`,
   so this is unlikely). If a reply has `changed: false` with
   `read_back_before_switch_mm` = the new value, the switch itself discarded
   the write: record it, the recipe does not hold.
   2b. **The default path.** With A current, `lib_set_footprint_height` with
   `footprint_name` EMPTY and 7.33 mm. Expect `was_current: true`,
   `parked_elsewhere_first: true`, `made_current_after_write: true`,
   `current_restored: true` (A is current again), `pending_save: true`.
   Save, then read A's height back from the FILE: 7.33 mm. (The first
   version of this fix never switched on this path; whether a write to the
   already-current footprint survived was an inference.)
   NOT CHANGED: the sweep `lib_update_footprint_heights_from_3d` writes the
   same property on every footprint it walks without this recipe, so it very
   likely loses its non-current writes the same way. Its reply now says so
   (`heights_confirmed_on_save: false` and a `save_note`). While here, run it
   on the copy (mode `match`), save, and read back two footprints it reported
   in `items[]`: if their heights did not reach the file, the sweep needs the
   same fix (record it; do not rely on the sweep until then).
3. **A same-value write is not a change.** Repeat B at 7.11: `changed:
   false`, `pending_save: false`, and `save_note` says there is nothing to
   save.
4. **The discard reply.** On a scratch COPY of a sheet, move one object (the
   title bar shows the sheet modified), then `app_close_document(file_path,
   discard_changes=true)`. Expect `closed: true`, `discard_requested: true`,
   `file_unchanged_on_disk: true`, and `discarded` = `true` if `modified_read`
   is true, `null` if it is false; NEVER `false`. Record which: a `null` with
   the title bar showing modified confirms the floor read (the old reply would
   have said `false`). The file's mtime is unchanged. Reopen it: the move is
   gone.
   4b. **The same field on a sheet delete.** In a scratch COPY of a project,
   edit a disposable sheet without saving, then `proj_delete_sheet(file_path,
   discard=true)`: `discard_requested: true`, `discarded` `true` or `null`
   by `modified_read`, never `false` while `closed` is true.
5. **A close without discard.** Close an unmodified copy with
   `discard_changes=false`: `discarded: false`, `discard_requested: false`,
   `file_unchanged_on_disk: true`.
6. **The description key. RUN THIS FIRST.** `proj_get_component_info` on a
   part with a component description and NO Description parameter, its sheet
   OPEN: `component_description` is that text, `parameters` has no
   `Description` key, `description_parameter_present: false`. On a part that
   carries a Description PARAMETER whose text differs from its component
   description (36.4's R2 after editing one of the two by hand):
   `parameters['Description']` = the parameter's text,
   `component_description` = the component's,
   `description_parameter_present: true`. The same through
   `proj_get_component_info_many`. If `component_description` comes back as
   the PARAMETER's text instead, `DM_Description` reads the parameter on this
   Altium: record it, because the two-entry rule would then drop the real
   parameter and keep the copy.
   6b. **After a stamp.** `sch_set_components_parameters` with
   `{"designator": "R2", "Description": "Res; same"}` (R2 carries a real
   Description parameter), then `proj_get_component_info` on R2:
   `parameters['Description']` = `Res; same` is STILL present,
   `component_description` = `Res; same`, `description_parameter_present:
   true`. The first version of this fix dropped it here. Repeat with R2's
   sheet CLOSED (project still open): the entry is kept and
   `description_parameter_present` is `true` (two DM entries) or `null`
   (one); record which, it says how the DM list carries a real parameter.
   6c. `with_parameters=false` on the same part: no `component_description`,
   no `parameters` (the `DM_Description` read is not reached).
   RISK: `DM_Description` is read on a compiled `IComponent` for the first
   time here (shipped code reads it only on project variants). A read
   cannot write a wrong value, but an undeclared identifier faults where
   `Try/Except` cannot catch it: if the bridge stops answering on the
   first `proj_get_component_info`, that read is the cause, and every
   default component-info call (the orchestrator's, the dashboard drawer's)
   would hit it. Run this step before anything else that needs component
   info.

### Results 2026-10-10 (the 2026-10-10 build)

Run on Altium 26.10.1.5 against a free scratch sheet and a byte copy of the
carrier library opened as its own (free) document; the carrier files were
checked untouched (mtime and sha256) before and after. `app_ping` matched
throughout and the bridge answered every call. Reviewed and corrected after
the run; the table is the corrected one.

| Step | Result | What was read |
|---|---|---|
| 1 version | PASS | the 2026-10-10 build, `version_match` true |
| 6 single, carrier part U105 (no Description parameter, sheet open) | PASS | `component_description` = the component description, `description_parameter_present: false`, no `Description` key; the bridge stayed up (the first `DM_Description` read on a compiled `IComponent` is safe on AD 26.10.1.5) |
| 6 batch (U105, R129, C127) and scratch R1 / R2 | PASS | R2 (parameter `old` differing from its description): single and batch agree, `parameters['Description']` = `old`, `component_description` = the component's, present `true`; `DM_Description` does NOT read the parameter, so the two-entry rule is sound |
| 6b after a stamp, sheet open | PARTIAL | the stamp reply was exactly as specified (`properties_set 1`, `parameters_modified 1`, `description_parameters_also_set` R2, `value_encoding pct1`); the read-back was done only through `proj_get_component_info_many` - the SINGLE handler was not exercised after the stamp |
| 6b sheet CLOSED | NOT RUN | needs a project: closing a free document drops it from Free Documents, and no scratch project was in scope |
| 6c `with_parameters=false` | PASS on U105 | neither `component_description` nor `parameters` in the reply. Run on U105, not on R2 as written |
| 2 heights on non-current B, C | heights PERSIST; `marked_modified` FAIL | every other field as specified (`changed`, read-backs 7.11 / 7.22, `was_current false`, `made_current_after_write`, `current_restored`, `pending_save`, `written_to_disk false`); A stayed current. **`marked_modified: false` on both, where true is required**, while the tab showed `carrier_copy.PcbLib *`. `lib_save_library`: `written_to_disk: true`, `was_modified: false`. The FILE (each footprint's Parameters `HEIGHT`, read with olefile because `lib_read_pcblib_file` had no height field): B 279.9213 mil = 7.11 mm, C 284.252 mil = 7.22 mm |
| 2b default path, A current, 7.33 | heights PERSIST; `marked_modified` FAIL | `was_current`, `parked_elsewhere_first`, `made_current_after_write`, `current_restored`, `pending_save` all true; `marked_modified: false`; file A = 288.5827 mil = 7.33 mm; A current again |
| 2, the sweep (`mode=match`) | writes PERSIST, with a caveat | inspected 108, updated 37; after `lib_save_library` all 37 reported items carry their new height in the file, non-current footprints included. Caveat: the library was not freshly opened (A, B, C had been switched to earlier), so `heights_confirmed_on_save: false` stays until a run on a freshly opened copy |
| 3 same value | PASS | `changed: false`, `pending_save: false`, "nothing to save"; no `*` afterwards |
| 4 discard reply | PASS | sheet saved, R1 moved by hand (title bar `*`, API `modified:false`), then `discard_changes=true`: `closed true`, **`discarded: null`**, `discard_requested true`, `modified_read false`, `file_unchanged_on_disk true`; sha256 and mtime unchanged, the move gone on reopen. The `null` with a modified title bar is the floor read this reply was rebuilt for |
| 4b `proj_delete_sheet` discard | NOT RUN | needs a scratch project copy |
| 5 close without discard | PASS | `discarded false`, `discard_requested false`, `modified_read false`, `file_unchanged_on_disk true` (the sheet and the library copy) |

What it showed beyond the steps: `IServerDocument.Modified` read FALSE on
documents whose tab showed `*`, for the bridge's height writes AND for a move
made by hand (step 4). With `app_save_all`'s default (skip a library that
reads unmodified) those heights would have been left unwritten. The same was
seen after `obj_modify` on a free scratch sheet: the reply said
`marked_modified: true` (a constant) and the active document read
`modified: false` fourteen seconds later with `*` on the title bar. Fixed in
this build: section 38.

---

## 38. A dirty mark that says whether it took, and a save-all that writes what the bridge edited (run live 2026-10-11 on the first 2026-10-11 build and re-checked 2026-10-12 on the .03 build, results at the end; re-check on 2026.10.13.02)

Found by section 37's live run (2026-10-10, the 2026-10-10 build): the
library's modified flag read false after every `lib_set_footprint_height`
while its tab showed `*`, `lib_save_library` reported `was_modified: false`,
and `obj_modify` on a free scratch sheet answered `marked_modified: true` (a
constant) while the sheet read `modified: false`. Both documents were FREE
documents under a Temp path. `MarkDocDirtyByPath` looked the document up with
`Client.GetDocumentByPath`, exited silently when that returned Nil and
swallowed a failing `SetModified`; a free document's `DocumentName` /
`DM_FullPath` is a bare name, which is what `obj_modify` passed.

Now (Main.pas, Utils.pas, Generic.pas, Library.pas, Project.pas,
Application.pas):

* `MarkDocDirty` (wrapped by `MarkDocDirtyByPath`) RETURNS the flag read back
  after the mark. Its lookup, `FindOpenServerDoc`, answers the two kinds of
  request by different rules and NEVER with a namesake (review 2026-10-11,
  F1: the first version let a free scratch copy's bare name resolve to the
  project's same-named sheet). An ABSOLUTE path is answered only by THAT
  file (the exact lookup, then a listed document whose `DM_FullPath` or own
  `FileName` has the same key, then the focused document on the same key;
  the exact lookup's answer counts only when its own `FileName` has the
  requested key - an empty or bare one proves nothing). A BARE name (what
  `obj_modify` passes for the current sheet) only ever comes from a FREE
  document - a project member's `DM_FullPath` and `DocumentName` are
  absolute - so it is NEVER answered by a member of a real project,
  focused or not (review of the first repair: with the carrier sheet
  focused, the free copy's bare name resolved to the sheet, so the save
  walk skipped a hand-edited copy as unmodified and a bare mark for an edit
  to the non-focused copy marked and recorded the sheet). Otherwise it is
  answered by the focused document when its file name matches, else only
  when exactly ONE listed document carries that name (`DocNameCount`,
  counted from the listing before any lookup, closed project members
  included); several, and it is refused as `ambiguous_name`. It also marks and reads
  the focused document when that is the same file by key. `DocIsModified`
  uses the same lookup, so a closed project member no longer reads the flag
  of an open free copy that shares its name.
* Every reply that claims `marked_modified` carries `mark`: `{path,
  file_name, found, via (path | walk | walk_name | focused | ambiguous_name
  | none), modified_read, focused_modified_read (null when the focused
  document is another file), recorded_for_save_all, problem}`.
  `recorded_for_save_all` is true when the mark put the file on the record
  under an ABSOLUTE path, the only kind `app_save_all` writes from (it was a
  constant true before the review, F8).
* `obj_modify` reports the read-back (no longer a constant), `pending_save`
  follows what the call changed (false when nothing matched, F6), and a
  `save_hint` (app_save_all WITHOUT only_modified, or lib_save_library)
  appears when the flag did not register. Only a sheet the call changed is
  marked: project scope counts `sheets_marked` only for sheets that matched
  and whose flags read back, and lists the rest in `marks_not_read_back`.
  `obj_batch_modify` rows carry each op's `marked_modified`, plus
  `ops_mark_not_read_back` and a `save_hint`. `lib_set_footprint_height` and
  the two footprint delete handlers carry `mark`.
* THE BRIDGE'S OWN RECORD (`BridgeEditedPaths`). One entry per FILE, keyed
  by its resolved ABSOLUTE path and stamped with the file's age at the mark
  (F2, F4). NEVER a bare name: a mark that resolves nothing records nothing
  and says `found: false` with a `save_hint` (the first repair kept a bare
  entry "when nothing resolved"; it answered for the clean carrier sheet
  once the scratch copy closed and survived a Ctrl+S on it). When the
  caller's absolute spelling differs from the document's own `FileName` (a
  subst drive, an 8.3 or UNC path), only the `FileName` key is kept, so a
  written edit is never reported lost from a second key. THAT HOLDS ONLY
  WHEN SOME PROJECT LISTING CARRIES THE CALLER'S SPELLING (a project opened
  through `F:\` while the document's `FileName` says `H:\`). When the
  caller spells `F:\` and both the listing and `FileName` say `H:\`, the
  lookup cannot equate them: the mark answers `found: false`, the `F:\`
  key stays on the record, and `app_save_all` lists it in
  `bridge_record_unresolved` (kept, NOT written, with a
  `bridge_record_hint`) instead of calling the edit lost. Pass the path as
  the document's tab shows it. `app_save_all`
  writes a recorded document even when its flag reads clean (default
  libraries and `only_modified` sheets included); one the project walk
  cannot reach (a free document whose bare `DM_FullPath` resolves to
  nothing - the field case, F3) is written afterwards from its absolute
  path. When its file got newer it is listed in `written_by_bridge_record`
  and dropped. When it did not: `bridge_record_not_written` plus a
  `bridge_record_warning` if the flag still reads modified OR the file was
  last written before the current 2 s FileAge tick (a real write would
  have moved it, whatever the flag says); `bridge_record_same_tick`, NOT a
  failure, if the file was last written inside the current tick and the
  flag reads clean (F7 - FileAge has 2 s resolution; the entry stays for
  one more write). A recorded document no longer open is dropped and
  listed in `bridge_record_closed` with the warning; one whose file exists
  and is open under another spelling is kept and listed in
  `bridge_record_unresolved`.
  `free_documents_not_reached` lists free documents whose bare listing the
  walk could not resolve to the free document itself (the name is carried
  by several listed documents, or resolves only to a project member or to
  nothing); such a copy is neither written nor called unmodified, and its
  non-bridge edits are left in memory. Any write of the
  file by any route (the user's Ctrl+S, `sch_reload_document`, a library
  refresh) moves its age and retires the entry; a close
  (`app_close_document`, `proj_close`, `proj_delete_sheet`, the reload and the refresh)
  retires it too. `bridge_record_pending` is what the record still holds.
  Documents nobody edited are still left alone. `lib_save_library` reports
  `bridge_edited` and also clears the record on a write.
* `app_close_document` treats a bridge-edited, unwritten document as
  modified (F5): without `discard_changes` it is refused `MODIFIED`, the
  reply carries `bridge_edited`, `modified_read` stays the raw flag, and the
  record is let go only on a discard or a clean close. `proj_delete_sheet`
  (without `discard`) and `sch_reload_document` (with `save=false`) refuse
  the same way.
* A handler that resolves a sheet from a path marks THAT sheet: `obj_modify`
  with a `doc:` scope and `sch_mirror_component` with `doc_path` mark the
  caller's path when it is absolute (`SchDocMarkPath`), never the sheet's
  bare `DocumentName` (which, for a free copy with its project namesake
  focused, marked the namesake). A BARE `doc:` scope or `doc_path` that more
  than one listed document carries is refused `AMBIGUOUS_NAME` BEFORE any
  object is read or changed (it used to edit whatever
  `GetSchDocumentByPath(bare)` returned and only then refuse the mark).
  `sch_mirror_component` now carries `marked_modified` and `mark`.
* `sch_reload_document` (save=true) judges its save as `app_save_all`
  does: the file's age moved, or the flag reads clean AND the file's
  previous write is inside the current 2 s tick. A save the editor
  declined whose flag never registered is `SAVE_FAILED`, the sheet stays
  open and its record entry stays (it used to read clean, close the sheet
  and drop the edit with the record's only witness).
* `documents_seen` / `documents_written` now include free documents (the
  timestamp check walked project members only).
* `lib_read_pcblib_file` reports each footprint's `height_raw`,
  `height_mils` and `height_mm` from the file (Python, offline).

**The second 2026-10-11 build, after the 2026-10-11 live run** (carried
unchanged into the .03 build; results at the end of this section; the fixes, each with an executed test that fails on the
first 2026-10-11 build, are in `tests/test_live_*.py`, one module per
finding, sharing `tests/live_2026_10_11.py`):

* **THE FLAG IS READ AS A REAL BOOLEAN (D1, the root cause).** Live, after
  `SetModified(True)`, `IServerDocument.Modified` read as NEITHER true NOR
  false: `If X` and `If Not X` were both false. Every mark answered
  `marked_modified: false` while the tab showed `*`, and the branches that
  test the NEGATION never fired either (no `save_hint` on `obj_modify`,
  `mark.problem` empty, the generic close refusal). The likely value is a
  Boolean variant holding 1 rather than True's -1. Every read of the flag,
  in every unit, now goes through `ModifiedFlagState` / `ModifiedFlagOf`
  (Main.pas), built ONLY from the two forms that ran live on this very value
  with 0 catches, `If X` and `If Not X`: `If X` = modified, `If Not X` =
  clean, NEITHER (the odd value) = modified (doubt writes), a read that
  raises = unreadable (counted under `ModifiedFlagState:Modified`). Save,
  close and count gates treat odd and unreadable alike as modified; the
  MARK, a read-back, claims `marked_modified: true` only for modified or
  odd, and for an unreadable flag says `false` with problem "the modified
  flag could not be read" (when the found object's flag is unreadable but
  the focused tab of the same file reads modified, `true` with problem "the
  found document's modified flag could not be read; the focused tab's flag
  reads modified"). No compare of a Boolean with an integer or with the
  `True` / `False` literals runs ANYWHERE, at any value (no such compare
  exists elsewhere in these units, a SchDoc reads the odd value after every
  mark, and a raise would be a modal on every edit - the engine shows it
  BEFORE the `Except`, so a `Try` does not contain it), and no Variant
  routine (`VarType`,
  `VarIsNull`, ...) is used: none has ever run in this codebase, and an
  undeclared one would fault inside the polling loop. So on this build a
  bridge mark is expected to read `marked_modified: true`. CORRECTED BY
  MEASUREMENT (the second 2026-10-11 build re-run on a fresh Altium, the
  ".02b" run, AD 26.10.1.5): the odd value follows `SetModified(True)` on
  EVERY kind measured - the free SchDoc, project sheets, the namesake copy
  AND the free PcbLib (`lib_set_footprint_height`) all read `flag_raw`
  `odd` / `if=0,not=0`, more than 25 marks - while a document nobody marked
  reads a normal False (`modified_read: false`; `odd` was never seen on a
  clean read). D1 is not schematic-side, and this text's earlier "PcbDoc
  and PcbLib flags already read true" was wrong. Consequence: no bridge
  edit reads `marked_modified: false` on this Altium, so step 4's
  record-route variant (an edit whose mark reads FALSE) is UNREACHABLE
  here; run its plain version.
* **The diagnostic.** Every `mark` now carries `flag_raw` and
  `flag_tests`, read right after `SetModified(True)`, from the two proven
  tests ONLY. `flag_raw`: `true` / `false` when `If X` / `If Not X` held,
  `odd` when neither did, `unreadable` when the read raised, empty when
  nothing was found. `flag_tests`: `if=1|0,not=1|0`, or `unreadable`. A
  normal True reads `true` / `if=1,not=0`, a normal False `false` /
  `if=0,not=1`, the field's value `odd` / `if=0,not=0`. The numeric value
  behind the odd one (1? -1?) is deliberately NOT probed: the first cut of
  this build compared it with 1, -1, 0, `True` and `False` "only on the odd
  value", but a SchDoc reads odd after EVERY mark, so those never-run
  compares ran on every schematic edit (review of d198b65), and no decision
  reads the number. Record both on the first SchDoc mark of the run, and a
  PcbLib mark's for comparison.
* **One visit per file per save pass (D2).** The walk reached a file once
  per listing (a project member and the Free Documents listing of the same
  file, or two slash spellings) and, after writing it on the first visit,
  filed it under `skipped_unmodified` / `libraries_skipped` on the second.
  A file the pass has attempted is not looked at again, and the skip list
  is deduped by `DocPathKey`. That early exit applies INSIDE an
  `app_save_all` walk only: `proj_save` / `proj_close save=true` after an
  `app_save_all` write an edit made since (the first cut of this fix
  skipped every file the last save-all had written, review of 0c4f20e). A
  listed document with no absolute file name (unnamed, or a bare name) is
  never written - `DoFileSave('')` could only ask where - and is listed in
  `free_documents_not_reached` (a bare one by its name, one with no name at
  all as `(unnamed document N)`, numbered so two are two entries). A document whose flag reads modified is
  written by the walk itself, so it is NOT listed in
  `written_by_bridge_record` (that list is for recorded documents whose
  flag reads clean) - with D1 fixed that is the normal case.
* **A one-op `obj_batch_modify` works (D3).** The client joined ops with
  `~~`, so one op had none and the bridge picked its legacy positional
  parser (`unknown_object_type`, `object_type` `object_type=eSchComponent`).
  The bridge now takes any `key=value` framed payload as tilde framing,
  and the client also ends every payload with `~~`.
* **A zero-match op is not a failed mark (D4).** Its row reads
  `marked_modified: null` and `ops_mark_not_read_back` counts only ops
  that matched.
* **The close refusal names the bridge edit whenever there is one (D5).**

The steps below are the 2026-10-11 procedure with the second-build
expectations, unchanged on the .03 build (its re-check on the second
build never started: the loop was dead, see the section 39 results).
SCRIPT_VERSION is `2026.10.13.02`. Use scratch copies only:
a scratch sheet opened as a free document, a scratch PROJECT copy with at
least one sheet, a byte copy of one of that project's sheets saved under the
SAME FILE NAME in another folder (opened as a free document), and a byte copy
of a .PcbLib opened on its own.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.13.02`,
   `version_match` true.
2. **Read-back on a FREE document.** On the free scratch sheet (focused),
   `obj_modify` one property (say a part's `Location.X`). Expect
   `marked_modified: true`, `pending_save: true`, NO `save_hint`, and `mark`
   with `found: true`, `modified_read: true`, `recorded_for_save_all: true`
   (the mark resolved the bare name to the sheet's ABSOLUTE path:
   `mark.file_name` is that path), `problem` empty; record `mark.via` and
   `mark.focused_modified_read`, and `mark.flag_raw` / `mark.flag_tests`
   (the D1 diagnostic: see above; record them whatever they say).
   `app_get_active_document` then reads
   `modified: true`. Then `lib_set_footprint_height` on a non-current
   footprint of the free library copy: `marked_modified: true`,
   `mark.found: true`. An `obj_modify` whose filter matches nothing:
   `matched: 0`, `pending_save: false`, `mark: null`.
   IF `marked_modified` IS STILL FALSE, record the whole `mark`: `found:
   false` means the lookup missed (`via` says how far it got); `found: true`
   with `modified_read: false` and `focused_modified_read: true` means the
   path lookup reaches a different object from the focused tab; both false
   means `IServerDocument.Modified` does not reflect `SetModified` on this
   Altium at all. Either way the reply now SAYS so (`save_hint`), and step 4
   is the safety net. On the .03 build a false here comes with `flag_raw`
   `false` (`If Not X` held: the flag really reads False after
   `SetModified(True)`, or an unassigned value, which converts to False) or
   `unreadable` (the problem then says "the modified flag could not be
   read" and `caught_exceptions` rose by one under
   `ModifiedFlagState:Modified`) - record `flag_tests` exactly. The readers
   hold no compare at all (only `If X` / `If Not X`, both proven live on
   this value), so a modal on the FIRST mark is not expected; if one
   appears, record its text and `last_caught`.
3. **Read-back on a PROJECT document.** In the scratch project copy, the same
   `obj_modify` with `scope` `doc:<sheet path>` while ANOTHER document is
   focused: `marked_modified: true`, `mark.found: true`, `mark.via: path`,
   `mark.focused_modified_read: null`. With `scope` `project` and a filter
   that matches on ONE sheet only: `sheets_marked` 1, `marks_not_read_back:
   []`, and the other sheets do not read modified. One `obj_batch_modify` op
   on that sheet (ONE op, nothing else in the batch: the first 2026-10-11
   build refused every one-op batch `unknown_object_type`): its row reads
   `object_type` `eSchComponent`, `marked_modified: true`,
   `ops_mark_not_read_back: 0`, no `save_hint`. Then two ops, the second
   matching nothing: the second row reads `matched: 0`,
   `marked_modified: null`, and `ops_mark_not_read_back` stays 0.
4. **app_save_all writes a bridge-edited document whose flag reads clean.**
   Needs an edit whose `marked_modified` reads FALSE: step 2's height write
   if it still does, else `lib_add_footprint_tracks` or
   `lib_modify_footprint_pads` (both read `modified:false` on earlier builds).
   If no bridge edit reads false on this build, record that and skip: the
   record path is then unreachable in practice. ON the .03 build THAT IS
   THE EXPECTED OUTCOME (D1: a mark now reads true; MEASURED on the .02b
   run - every mark on every kind read `odd`, which counts as modified, so
   the variant could not be run): record "no bridge edit
   reads false" with the `flag_raw` / `flag_tests` of the edit tried, and
   run instead the plain version - after a bridge edit that reads true,
   `app_save_all` writes the copy, lists it in NEITHER `skipped_unmodified`
   NOR `libraries_skipped` (the first 2026-10-11 build listed a file it had
   just written there too, D2), and `written_by_bridge_record` may be `[]`
   (the walk wrote it as a modified document). Otherwise, after such an edit
   on the library copy, FOCUS ANOTHER DOCUMENT (so the walk cannot reach the
   free copy through the focus) and wait at least 3 s (FileAge has 2 s
   resolution; a write inside the tick of the file's previous write cannot
   move its age). `app_save_all` with NO arguments. Expect the copy NOT in
   `libraries_skipped`, listed in `written_by_bridge_record`, absent from
   `bridge_record_pending`, `bridge_record_not_written` and
   `bridge_record_same_tick` empty, its mtime NEWER, and
   `lib_read_pcblib_file` on it showing the new `height_mm`. Record
   `documents_attempted`, `bridge_record_pending` and
   `free_documents_not_reached` (the copy may be listed there - the walk
   could not reach it - while the record wrote it). Then `app_save_all`
   again with nothing edited: the copy is back in `libraries_skipped` and
   its mtime does not move (the record let go). For a sheet: the same with
   `only_modified=true` after a sheet edit that reads `marked_modified:
   false`; the sheet is listed in `written_by_bridge_record`. IF the copy
   is in `bridge_record_same_tick` instead, the save landed inside the 2 s
   tick: record it, wait 3 s and repeat - it must not appear in
   `bridge_record_not_written`.
5. **lib_save_library says what the flag cannot.** After a bridge edit on the
   library copy, `lib_save_library`: `bridge_edited: true`,
   `written_to_disk: true`. A second call straight after: `bridge_edited:
   false`. Then a new bridge edit and, WITHIN 2 s, `app_save_all`: the copy
   may land in `bridge_record_same_tick` (not a failure, no
   `bridge_record_warning`) or in `written_by_bridge_record`; it must not be
   in `bridge_record_not_written`. Record which. On
   the .03 build, with the edit reading `marked_modified: true`, the walk
   writes it as a modified document: it is counted in `documents_written`,
   both lists may be empty, and it must NOT be in `libraries_skipped`.
6. **A pass that writes only free documents counts them.** With only the
   free scratch sheet edited, `app_save_all`: `documents_written` at least 1
   and `saved: true` (before this build such a pass could read "not one got
   newer on disk"). Record `documents_attempted` and `bridge_record_pending`
   (expected `[]`). The sheet written is NOT also in `skipped_unmodified`
   (it was, on the first 2026-10-11 build: D2).
7. **Nothing unedited is written.** Then, with nothing edited,
   `app_save_all only_modified=true`: `documents_attempted: 0`,
   `written_by_bridge_record: []`, `bridge_record_pending: []`, no mtime
   moves.
8. **The file reader shows heights (offline, VERIFIED 2026-10-11).**
   `lib_read_pcblib_file` on the carrier library, footprint
   `ADM6-60-01_5-L-4-2-A-TR`: `height_raw` `130.7087mil`, `height_mils`
   130.7087, `height_mm` 3.32; every one of its 108 footprints reports a
   height. Needs no Altium.
9. **A namesake is never marked or written (F1, F2).** Open the scratch
   project (its sheet `X.SchDoc` loaded) AND the same-named byte copy of
   `X.SchDoc` from the other folder as a free document; focus the free
   copy. `obj_modify` one property: `mark.file_name` is the FREE COPY's
   path, `mark.via` `focused` (or `path`: in the 2026-10-11 session free
   documents reported an absolute path, so the exact lookup answered first;
   either is a pass); the project sheet reads `modified: false`.
   `app_save_all only_modified=true`: the project sheet's mtime does not
   move and it is not in `written_by_bridge_record`; `bridge_record_pending`
   names no project path.
   a. **Bare scope is refused before any edit.** Record `obj_count` (or
      `obj_query` count) for one object type on BOTH files by their
      ABSOLUTE `doc:` paths. Focus a third document and run `obj_modify`
      with `scope` `doc:X.SchDoc` (bare) on that type: refused
      `AMBIGUOUS_NAME`, no `mark`. The same with the project sheet focused
      and with the free copy focused. The counts on both files are
      unchanged and NEITHER file reads modified.
   b. **The mirror marks its doc_path sheet.** Focus the PROJECT sheet.
      `sch_mirror_component` on a part of the FREE COPY with `doc_path` set
      to the copy's ABSOLUTE path: `changed: true`, `mark.file_name` is the
      copy's path, `mark.found: true`. `app_save_all only_modified=true`
      reply (or `bridge_record_pending` before it; a NO-argument save-all
      writes the clean project sheet by design, so it cannot be used here -
      corrected after the 2026-10-12 run): no project path on
      `bridge_record_pending`, the project sheet not in
      `written_by_bridge_record`, its mtime unchanged. Mirror it back the
      same way. With `doc_path` bare (`X.SchDoc`): refused
      `AMBIGUOUS_NAME`, the part unmoved in both files.
   c. **A hand edit to the free copy is never called unmodified.** Project
      sheet still focused, edit the FREE COPY by hand (move a part on its
      tab), then focus the project sheet again and run `app_save_all
      only_modified=true`. The bare `X.SchDoc` is NOT in
      `skipped_unmodified`; the copy is either written (its mtime moved) or
      listed in `free_documents_not_reached`. The project sheet's mtime does
      not move. Record which. Then `app_save_all` with no arguments: the
      project sheet is written ONCE (its mtime moves once; record
      `documents_attempted`).
   d. **An ambiguous mark leaves nothing behind.** Focus the library copy.
      `obj_modify` with `scope` `doc:X.SchDoc` (bare) is refused (as a).
      Close the free copy without saving, then `app_save_all
      only_modified=true`: the project sheet is NOT in
      `written_by_bridge_record` and its mtime does not move;
      `bridge_record_pending` is `[]`.
10. **A close does not throw a bridge edit away (F5, D5).** After a bridge
    edit on the library copy, `app_close_document` on it WITHOUT
    `discard_changes`: refused `MODIFIED`, the message starts "This bridge
    edited the document" WHATEVER the flag reads (the first 2026-10-11
    build gave the generic "The document has unsaved changes" - D5), the
    copy still open. Then with `discard_changes=true`: `closed: true`,
    `discarded: true`, `bridge_edited: true`, `file_unchanged_on_disk:
    true`, and `modified_read` the flag as `ModifiedFlagOf` reads it (`true`
    on the .03 build when the mark read true; `false` only when it did
    not - record which).
11. **A file written outside the record is not rewritten (F4).** Bridge
    edit on the library copy, then save it in Altium by hand (Ctrl+S on its
    tab), wait 3 s, then `app_save_all` with no arguments: the copy is in
    `libraries_skipped`, NOT in `written_by_bridge_record`, its mtime is the
    Ctrl+S one, and `bridge_record_pending` is `[]`.
12. **A close that wrote nothing retires the entry.** In the scratch project,
    a bridge edit on a sheet, then `proj_close` with `save=false`, then
    `proj_open` it again and `app_save_all only_modified=true`: the sheet's
    mtime does not move and `bridge_record_pending` is `[]`.
13. **A reload whose save is declined does not close the sheet.** After a
    bridge edit on the free scratch sheet whose `marked_modified` reads
    FALSE (as step 4; on the .03 build a mark is expected to read true, so
    run it with that edit and record the flag: a declined save then keeps
    the flag reading modified, which is `SAVE_FAILED` too, and the final
    save-all writes the sheet as a modified document rather than from the
    record), start an interactive command on it (Place > Wire,
    leave it running) and call `sch_reload_document` on its absolute path:
    `SAVE_FAILED` with `still_modified` recorded, and the sheet still open
    with the edit visible. End the command (`app_exit_active_command`),
    wait 3 s and run `app_save_all only_modified=true`: the sheet is in
    `written_by_bridge_record` (the record kept the edit) and its mtime
    moved. If the save is not declined while the command runs on this
    build, record that and skip.

### Results 2026-10-11 (the first 2026-10-11 build, AD 26.10.1.5)

Run against scratch copies only, with the carrier project open throughout
(the carrier folder was baselined before and after every save: unchanged).
Because the carrier was open, every "app_save_all with NO arguments" ran
as `only_modified=true` instead (marked SUB). `caught_exceptions` 0,
`cast_errors` 0 and `last_caught` null before and after EVERY step.

| Step | Verdict | What happened | On the .03 build |
|---|---|---|---|
| 1 | PASS | version matched | re-check the new version |
| 2 | FAIL | `marked_modified` false, `modified_read` false with `*` on the tab, NO `save_hint`, `mark.problem` empty; the no-match half passed | D1 fixed (`ModifiedFlagState` / `ModifiedFlagOf`, `If X` / `If Not X` only); record `flag_raw` / `flag_tests` |
| 3 | FAIL | doc: and project scope marks read false (`sheets_marked` 0); a ONE-op batch refused `unknown_object_type`; a two-op batch counted its no-match op in `ops_mark_not_read_back` | D1, D3, D4 fixed |
| 4 | PARTIAL (SUB) | the edit WAS written (height on disk), but the reply listed the file in `skipped_unmodified` + `libraries_skipped` with the "NOT written" warning, `written_by_bridge_record` `[]`; the repeat passed | D2 fixed; the "reads false" precondition is not expected to arise (D1) |
| 5 | PARTIAL (SUB) | `bridge_edited` true / `written_to_disk` true, then false: pass; the within-2 s save wrote it but listed it in `libraries_skipped` | D2 fixed |
| 6 | PARTIAL (SUB) | `documents_written` 1, `saved` true; the written sheet also in `skipped_unmodified` | D2 fixed |
| 7 | PASS | nothing attempted, no mtime moved | unchanged |
| 8 | PASS | offline heights as documented | unchanged |
| 9 main | PARTIAL | mark found the free copy, project sheet untouched; `mark.via` was `path`, not `focused` | the step now accepts `path` |
| 9a, 9b, 9d | PASS | bare scopes refused before any edit; the mirror marked its doc_path copy | unchanged |
| 9c | NOT RUN | needs a hand edit (owner session) | still owner-gated |
| 10 | PARTIAL | refused `MODIFIED` and stayed open, but with the GENERIC message; the discard close passed every field | D5 fixed |
| 11 | NOT RUN | needs a hand Ctrl+S (owner session) | still owner-gated |
| 12 | PASS | the closed edit was not written; `bridge_record_pending` `[]` | unchanged |
| 13 | NOT RUN | needs an interactive command left running (owner session) | still owner-gated |

The second 2026-10-11 build's re-check of this section (2026-10-11
20:24-20:27) did NOT START: the polling loop was dead after section 39's
P8 (results there) and Altium was not restarted. Nothing was run and the
scratch and carrier folders were unchanged. Run it on the .03 build.

Why the replies contradicted the writes: the raw flag read neither true
nor false, so `If Not WasModified` (the record's route) did not run and
the walk wrote the file as if modified; a second visit of the same file
(a free document also reached through `DM_Projects`, or the other slash
spelling) then read the now-clean flag and listed it as skipped. Nothing
was lost - only the replies were wrong.

### Results 2026-10-11 (the second 2026-10-11 build re-run on a fresh Altium, the ".02b" run, AD 26.10.1.5)

Run 21:03-21:17 on scratch copies; the carrier folder (41 of 41 files, 99
with subfolders) and the scripts folder (15 of 15) baselined at the start,
after every save and at the end: unchanged. `caught_exceptions` 0,
`cast_errors` 0, `last_caught` null around every step; no dialog.
EVERY save-all ran as `only_modified=true` (marked SUB): the walk reached
the bridge's own `Altium_API.PrjScr`, and a no-argument save-all would have
rewritten the loaded `...\EDA Agent\scripts\Library.pas`, which all 14
save-alls listed in `skipped_unmodified` (N2 below).

| Step | Verdict | What happened |
|---|---|---|
| 1 | PASS | version matched, 0 / 0 / null, `in_flight` [] |
| 2 | PASS | free sheet: `marked_modified` true, `pending_save` true, no `save_hint`, `mark.found` true, `via` `path`, `flag_raw` `odd` / `if=0,not=0`; the PcbLib height mark read `odd` too (see D1); the no-match half `mark: null` |
| 3 | PASS | doc: scope (`focused_modified_read` null), project scope `sheets_marked` 1, a one-op batch (`eSchComponent`, D3 fixed), a two-op batch (`marked_modified` null, D4 fixed) |
| 4 | PASS (SUB, plain version) | no bridge edit reads false (D1); the library written, in NEITHER skip list (D2 fixed), its height on disk; the repeat attempted 0; the sheet half written |
| 5 | PASS (SUB) | `bridge_edited` true then false; the within-tick save-all wrote it, not in `libraries_skipped` |
| 6 | PASS (SUB) | only the free sheet edited: attempted 1, written 1, not skipped |
| 7 | PASS | nothing attempted, no mtime moved |
| 8 | PASS | offline heights as documented, 108 of 108 footprints |
| 9 main, 9a, 9b, 9d | PASS (SUB) | the free copy marked by path; bare scopes refused `AMBIGUOUS_NAME` in all three focus cases; the mirror marked its doc_path copy; the discard close passed every field |
| 9c, 11, 13 | OWNER-GATED | hand actions |
| 10 | PASS | refused `MODIFIED` with "This bridge edited the document..." (D5 fixed); the discard close read `modified_read` true |
| 12 | PASS (SUB) | `proj_close save=false` discarded; reopened, attempted 0, U816 at the saved value |
| 12b (D2 re-check) | PASS | `proj_save` and `proj_close save=true` after an `app_save_all` each wrote the later edit (mtimes moved; the final read showed E4). Both replies carried only `success` / `project_path` (N4 below) |

Fixed after this run (each with an executed test that fails on the
second build, `tests/test_live_02b_findings.py`):

* **N2: save-all never touches a script unit.** The walks skip any
  project whose file is a `.PrjScr` (`IsScriptProject`: `SaveAllDirty`,
  `SaveProjectMembers` - so a `proj_save` with the script project focused
  writes nothing either - `CountDirtyDocuments`, `WorkspaceDocPaths`), and
  a SCRIPT UNIT is never written or listed (`IsScriptUnitDoc`: the kind
  EditScriptDS, or a .pas / .bas / .vbs / .js / .tcl / .dfm file). A script
  unit is never in `skipped_unmodified`, `free_documents_not_reached`,
  `documents_seen` or `still_dirty`, and never reaches `WriteDocAndNote`.
  REVISED by the review of 498e5ca: the first fix was an ALLOW-list of five
  kinds, which also stopped writing, listing and counting every other kind
  (a BOM document, a Draftsman drawing, a harness): `app_save_all` then
  answered `still_dirty: 0` with one dirty, and `proj_close save=true`
  closed with it dirty - Altium's own save prompt, which blocks the loop.
  Every non-script kind is written and counted as before 498e5ca, and
  `proj_close save=true` now refuses the close (`still_modified`) when
  anything still reads modified after its save.
* **N4: `proj_save` and `proj_close save=true` say what reached disk.**
  Both replies carry `only_modified`, `documents_attempted`,
  `documents_written` (file ages before and after, as `app_save_all`
  measures), `skipped_unmodified` and `bridge_record_pending`. A new
  optional `only_modified` (default false = unchanged: every open member is
  written) skips a member whose flag reads clean, sheet or library, unless
  the bridge's record holds an edit to it.

Re-check on the .03 build (after the steps above):

14. **A no-argument save-all leaves the script units alone (N2).** With
    the script project loaded and a script unit open (Library.pas is,
    after any script error), and ONLY scratch documents open otherwise:
    `app_save_all` with NO arguments. No path under the scripts folder in
    `skipped_unmodified`, `free_documents_not_reached` or anywhere else in
    the reply; the scripts folder byte-identical (sizes and mtimes) before
    and after; `documents_seen` counts no script unit (every other
    document kind is still counted and written).
15. **proj_save reports (N4).** In the scratch project, a bridge edit on a
    sheet, wait 3 s, `proj_save`: `documents_attempted` >= 1,
    `documents_written` >= 1 (the sheet's mtime moved),
    `bridge_record_pending` `[]`, `only_modified` false. Then with nothing
    edited, `proj_save only_modified=true`: `documents_attempted` 0,
    `documents_written` 0, every loaded member in `skipped_unmodified`, no
    mtime moved. Then a sheet edit and `proj_close save=true`: the close
    reply carries the same fields, with `documents_written` >= 1.
    `bridge_record_pending` names THIS project's members only (and, for
    `proj_close`, what is still pending AFTER the close): edits left
    pending on free documents or another project by earlier steps are not
    listed, so `[]` is expected here whatever those steps left. Then, with
    a member whose save is declined (an interactive command left running
    on it, as step 13), `proj_close save=true`: `success` false, `closed`
    false, `still_modified` names it, no prompt, the project still open.

### Results 2026-10-12 (the .03 build, AD 26.10.1.5)

Run 02:17-02:28 on a freshly restarted Altium, scratch copies only; the
carrier folder (99 of 99 files) and the scripts folder (15 of 15)
unchanged at the start, around every save and at the end, including after
all five NO-ARGUMENT `app_save_all` calls. `caught_exceptions` 0,
`cast_errors` 0, `last_caught` null, `in_flight` [] and `last_fault` null
around every step; no dialog.

| Step | Verdict | What happened |
|---|---|---|
| 1 | PASS | version matched |
| 2 | PASS | free sheet and free PcbLib: `marked_modified` true, `mark.found` true, `via` path; every mark (20+) read `flag_raw` `odd` / `if=0,not=0`; the no-match half `mark: null` |
| 3 a-d | PASS | doc: scope, project scope (`sheets_marked` 1), one-op and two-op batches |
| 4 | PASS (plain version) | no bridge edit read false; NO-argument save written 5, the library in neither skip list, its height on disk; the repeat put it back in `libraries_skipped` with its mtime unmoved |
| 5, 6, 7 | PASS | `bridge_edited` true then false; free-only pass written; nothing unedited written |
| 8 | PASS | 108 of 108 footprints report `height_mm` |
| 9 main, 9a, 9b, 9d | PASS | namesake never marked or written; bare scopes refused `AMBIGUOUS_NAME` in all three focus cases; the mirror marked its doc_path copy (run with `only_modified=true`, the wording now in 9b) |
| 10, 12 | PASS | the close refusal names the bridge edit; a close that wrote nothing retires the entry |
| 14 (N2) | PASS | `documents_seen` 5 with no script unit; no scripts-folder path in any of the segment's 11 save-all replies |
| 15 (N4) | PASS | `proj_save` and `proj_close save=true` report attempted / written / skipped; `bridge_record_pending` is project-scoped |
| 9c, 11, 13, the declined half of 15 | OWNER-GATED | hand actions |

Notes, no code change: a NO-argument save-all rewrites every clean open
document by design (it rewrote another segment's scratch board five
times); `proj_save` with the default `only_modified=false` rewrites clean
members (use `only_modified=true`). Nothing in this section changed in
2026.10.13.02: re-run steps 1-3 as the smoke check only.

---

## 39. Popup-free run (run live 2026-10-11 on the first and second 2026-10-11 builds and 2026-10-12 on the .03 and .12.02 builds, results at the end; re-check on 2026.10.13.02)

**Why.** Altium's script engine shows an exception as a modal "press OK"
dialog BEFORE the surrounding `Try/Except` runs, and the polling loop
waits on it. This release stops the exception happening wherever an
ordinary bad input used to raise one (check first: a version gate, a Nil
test, a folder or file test, a syntax-and-range test before a
conversion) and COUNTS what is left: every `Except` that is not a
fault-only backstop now calls `RecordCaughtException`, and so does the
dispatcher's catch-all.

**THE COUNT SEES ORDINARY EXCEPTIONS ONLY (corrected 2026-10-11).** A
member that does not resolve on a late-bound interface - "Undeclared
identifier: X" - is not an exception: the script engine halts on the line,
the Script IDE parks there, no `Except` runs, nothing is counted, a
`Finally` is skipped, and after OK the polling loop is DEAD. The first
popup-free build filed three sites as "counted, not guarded"
(`SetOwnerPart`, `TargetLibComponentName`, `MarkLibDirty`, all reading the
editor's `CurrentSchComponent`) on the premise that a failure would be
counted; live on the second build the first of them halted the loop (39
results, P8). CORRECTED 2026-10-12 (D-F2-1): the same box came back on
the .12.02 build with every read of `CurrentSchComponent` removed. The
cause found is an UNASSIGNED global: the held symbol was never assigned at
pump start, an unassigned DelphiScript global is Empty, and Empty is not
Nil, so the guards passed it and the member read ran on Empty. That
is read from the source and is CONSISTENT WITH both P8 halts, to be
confirmed by session A (P8 on 2026.10.13.02); blaming the editor's
selection is UNPROVEN. A
member read on an unassigned (Empty) Variant is the same fatal class as a
member the runtime object does not publish, and 2026.10.13.02 assigns
every global the request path reads when the pump starts. The .03 build reads no member of that reference anywhere,
and none of the IPCB_Rule constraint read in `PCB_NormalizeVias` (P23). For
every site still counted below, "0 catches" means only "no ordinary
raise": a dead loop is the undeclared-identifier answer.

**WHAT DECIDES IT (corrected 2026-10-12, from the .03 run's P21).** The
engine resolves a member against the object it actually HOLDS at runtime;
the type a local, parameter or Result is declared with is not consulted.
P21 wrote `Conn.Text` and then `Conn.Side` through the same local declared
`ISch_GraphicalObject`: Text (which the cross-sheet connector has) ran,
Side (which it lacks) halted. P20's `HarnessType` ran because the harness
connector has it, not because its local was declared at the derived type.
So re-declaring a local at a derived interface changes nothing, base
members are not guaranteed either (a schematic polygon lacks `Location`),
and the guard that works is an ObjectId test before the access, or a
member proven on that exact runtime type. Every probe below whose failure
would be a member the runtime object lacks is FATAL-IF-WRONG (39.4).
`tests/test_member_on_base_typed_reference.py` fails the suite on any new
non-base member read through a base-typed reference that nobody reviewed.

**So the editor's own selection is never a target any more.** Every
symbol edit (and the nameless pin readers) acts on the symbol HELD from
`lib_create_symbol` / `lib_set_current_component`, or on the one its
`component_name` names, and answers `NO_COMPONENT` otherwise. Sections 2,
7 and 9 hold a symbol first for that reason; a `NO_COMPONENT` from a
nameless call with nothing held is the intended answer, not a FAIL.

**Reviewed before deploy (same version, the .03 build).** A review of the
first .03 draft found the same class in the fix itself, all closed with
executed tests (`tests/test_review_fatal_class_followups.py`):
`obj_batch_create component_name=` resolved through a local declared
`ISch_Document` while the named route reads `ISch_Lib`-only members (a
typed copy does not narrow) - it now uses an `ISch_Lib` local assigned
straight from `GetCurrentSchDocument`, and a guard checks every resolver's
argument type; `MarkLibDirty` read the HELD global on every library edit
(a freed one would have halted every later edit) - it now touches no
component, and the handlers redraw the component they edited
(`MarkLibDirtyComp`); `PCB_NormalizeVias` had moved its read to a typed
iterator local whose type name, members and `eRule_RoutingViaStyle` had
never run, for every rule on every board - it now parses `Descriptor`
(a proven base member), takes the rule kind as the literal 11, and reads
no rule on a board without a free via; `Pin.OwnerSchComponent` (P36) is
gone for the proven component-then-pin walk. Also: a symbol found by
`component_name` on part 0 is moved to part 1 as
`lib_set_current_component` would; a new parameter from
`lib_batch_set_params` keeps the part as read (0 included); the bridge
records `dead_loop` (with its time) the moment it sees a `script_fatal`
box, and any successful call clears a fault another server recorded.

What carries the count (all new in this build):

* `app_ping` answers `caught_exceptions` (catches this pump session; each
  one is a dialog that fired, even one somebody clicked away) and
  `last_caught` (`{where, command, request_id, at}`, or null).
  `cast_errors` and `caught_exceptions` both start at 0 when the pump
  starts.
* `app_get_status` answers `in_flight` (the calls whose progress
  heartbeat is on disk: `command`, `request_id`, `age_s`) and
  `last_caught` (the last `_caught_exception` row of `activity.log`). Both
  are read from files, so they answer while a dialog blocks Altium.
* The status form shows `<command>  #<first 8 of the request id>  since
  hh:nn:ss` while a call is in flight, and a stuck-handler timeout names
  the in-flight command and says an Altium error dialog may be open
  (pressing OK lets the handler finish with `INTERNAL_ERROR`).

### 39.1 The counter starts clean

    app_ping

Right after `StartMCPServer`: `caught_exceptions` = `0`, `last_caught` =
null, `cast_errors` = `0`. `app_get_status`: `in_flight` is empty (or
holds only the ping itself) and `last_caught` is null: it reads only rows
after the newest `_session_start`, so a catch from an earlier pump session
in the same `activity.log` is not reported.

### 39.2 Read the counters around EVERY section

Before each section of this document, `app_ping` and write down
`caught_exceptions` and `cast_errors`. After the section, ping again.
**Any increase means a modal fired**, whether or not anyone saw it:
record the section, the step, and `last_caught` (`where` names the site,
`command` and `request_id` the call). The expected increase is 0 for
every section, except a probe in 39.4 and step 2's first failure shape,
which are listed because they may raise.

**NO INCREASE DOES NOT MEAN NO DIALOG.** The counter sees the script
engine's exception box only, and only for a CATCHABLE exception. Two other
boxes are never counted, before or after OK, and all three are class
`TMessageForm` (live 2026-10-11: the fatal box AND Altium's own "Warning"
both reported that class), so the CLASS TELLS NOTHING: classify a box by
its TITLE, its TEXT and whether the Script IDE jumped to a source line
(39.5). They are the script engine's FATAL
late-bound error (title "Error", text "Undeclared identifier: ...", OK
only - the same caption and button as the exception box; live 2026-10-11
on the second build, after which the loop was dead), and Altium's OWN
message boxes (e.g. title "Warning", text "File type not recognised.",
which `Client.OpenDocument` put up live on 2026-10-11). Neither is an
exception: no `Except` runs. While one is up the call never returns, `app_ping` answers "not
responding" and its counters are unreadable. Only `app_get_status`
(`in_flight` names the stuck call and its age) and `app_list_open_dialogs`
(the box's title, class and text) see it. So after any call that does not
return promptly, read both before concluding anything from the counter.

### 39.3 Calls that used to raise and must not now

Each must answer with no dialog and leave `caught_exceptions` unchanged.

1. **No schematic document.** Restart Altium, open only a PcbDoc (no
   SchDoc or SchLib this session), focus the Home page.
   `app_get_active_document` answers (the board, the Home page, or `{}`);
   `app_get_preferences` answers `"schematic": null` with
   `sch_server_loaded: true`; `app_exit_active_command` answers
   `sch_post_process: true`, `sch_server_loaded: true`, no dialog.
   CORRECTED after the .12.02 run (F1): this item expected
   `sch_post_process: false` ("no schematic server"). On AD 26.10.1.5 the
   schematic server was already registered in a fresh session with no
   schematic document open (WHEN it loads - at startup, through the
   Projects panel or through the `Altium_API.PrjScr` script project - was
   not measured), so `SchServer` is not Nil and its `PostProcess` answers without
   raising: expect TRUE. The `SchServer = Nil` branches of both handlers
   are therefore NOT reachable by restarting Altium (they stay as the
   guard for a build that loads the server lazily; executed offline in
   `tests/test_popup_free_core.py`). `sch_server_loaded` (new in
   2026.10.13.02) reports the Nil test on its own, so "server present, no
   current schematic document" (`sch_server_loaded: true`, `"schematic":
   null`) can be told from "no server" (`false`, `null`).
2. **A board path that cannot be a board.** `pcb_place_components` with
   `board_path` = `C:\nope.PcbDoc`, then with the absolute path of an
   existing `.SchDoc`. Each takes the same fallback a failed open always
   took (it does NOT refuse: a bad `board_path` still lands on whichever
   board the fallback finds, so use a scratch board). A `.PcbLib` as
   `board_path`, EXISTING OR MISSING, is refused `BOARD_PATH_IS_LIBRARY`
   (the message names the `lib_` tools) with NOTHING opened, no board
   changed and no dialog; the same for `pcb_focus_board`. CORRECTED after
   the 2026-10-11 run: this item used to say an existing `.PcbLib` "is
   still opened, as before". It is not what Altium does:
   `Client.OpenDocument('PCB', <a .PcbLib>)` puts up Altium's OWN modal
   "Warning - File type not recognised." inside the call - not an
   exception, so uncounted - and the bridge blocks until somebody presses
   OK. The "as before" was that same modal (the eec334c build behaved
   identically) - and WORSE after OK: the call went on, `OpenDocument` still
   opened the library, and the library then read `modified: true` with
   `bridge_edited: true`, i.e. `pcb_place_components` very likely placed its
   part INTO THE LIBRARY. A modal followed by a silent write into a library
   is why this is a refusal, not an open and not a fallback. Count the
   components on the scratch board before and after: unchanged. A `.PcbDoc`
   that is NOT open, IS on disk and cannot be loaded (empty, unreadable, or
   not a compound/ASCII file: a head that is neither an OLE compound file
   nor the ASCII `|` record form) is refused `BOARD_FILE_UNREADABLE` the
   same way, nothing opened - probe P6b, now a check. KNOWN LIMIT: the OLE
   test proves only that the file is SOME compound document. A `.PcbLib`,
   `.SchDoc` or `.SchLib` renamed `.PcbDoc` passes it and reaches the open
   (Altium's own box, as for the `.PcbLib` above); `.Pcb` and `.OutJob` are
   judged by extension plus "readable and not empty". Not a regression -
   these opened the same way before - and not tested here.

   **POSITIVE CONTROLS FIRST, before any P6b negative probe.** The pre-open
   read (`FileLooksLoadable`: `TStringList.LoadFromFile` + `.Text`) runs on
   every CLOSED `.PcbDoc` / `.SchDoc` before it is opened, and it has never
   run live on a healthy BINARY file (its precedent, `ReadFileContent`,
   read small text request files). If Altium's decode raised on all 12
   attempts (a UTF-8 system code page with a strict conversion would), every
   healthy closed board would be refused and every closed binary sheet
   would fail to load. So, on scratch copies:
   (a) `pcb_focus_board` and then `pcb_place_components` with `board_path`
       naming a CLOSED healthy binary `.PcbDoc` (a carrier copy): it opens,
       no refusal, no dialog, 0 catches;
   (b) `proj_load_sheets` on a project whose binary `.SchDoc`s are ALL
       closed: none fails;
   (c) the same with an ASCII-saved `.PcbDoc` and `.SchDoc`;
   (d) time the first `app_get_preferences` with the carrier board listed
       and closed (the read decodes the whole file to look at 8 characters;
       record the seconds).
   A `BOARD_FILE_UNREADABLE` or a failed sheet on a healthy file means the
   read or the head test misjudged it: record the system ANSI code page
   (GetACP: the `ACP` value under the registry key
   `HKLM\SYSTEM\CurrentControlSet\Control\Nls\CodePage`), the file's
   size and its first 16 bytes, and stop - P6b's
   negative half means nothing until the positive half passes.
3. **A project that lists a board missing on disk.** Copy a scratch
   project without its `.PcbDoc`, open it, focus a sheet, run
   `app_get_preferences`: `"pcb": null` (or another open board).
4. **Home page focused.** With a project holding a PcbDoc open and the
   Home page focused, `app_get_preferences`. The board opens in a
   BACKGROUND tab and focus stays on the Home page throughout: the active
   document afterwards is still the Home page, the new board tab sits
   behind it, and nothing is restored because nothing moved (measured on
   the .02b run). `"pcb"` carries that board's settings.
5. **A file into a missing folder.** `app_create_document kind=SCH
   file_path=C:\no_such_dir\x.SchDoc add_to_project=false`: `saved:
   false`, no Save As dialog, no error dialog.
6. **A hand-edited config.** Stop the bridge, set
   `poll_interval_active_ms` to `600000.0` in the workspace
   `mcp_config.json`, start it again: no dialog, the default polling
   speed. Put the file back.
7. **A focused SchLib, no PCB document this session.** Restart Altium,
   open ONLY a SchLib and focus it. With NO library path:
   `lib_add_footprint_pad`, `lib_copy_footprint source_name=X new_name=Y`,
   `lib_get_footprints`, `lib_delete_footprint footprint_name=X`,
   `lib_get_footprint_bodies`, `lib_link_3d_model model_path=<an existing
   .step>`. Each answers `NO_PCBLIB` (its own message, unchanged) with
   no dialog:
   these named `PCBServer` with only a SchLib focused, the uncatchable
   modal (`ResolveLibFootprint`, `FocusPcbLibForEdit`, `Lib_CopyFootprint`
   and the fifteen handlers that default the path to the focused
   document now ask `PcbLibMayBeCurrentAt` first).
8. **An outjob path that is not an OutJob.** `proj_run_outjob
   container_name=X outjob_path=<an existing scratch .PcbLib>`: refused
   `NO_OUTJOB` ("does not name an .OutJob file"), nothing opened, no
   dialog (it used to reach `OpenDocument('OUTPUTJOB', ...)`, the same
   kind-mismatch box as item 2).

The conversion guards (`StrToIntDef`, `StrToFloatDef`, `LoadMCPConfig`)
are executed offline against the real source by
`tests/test_popup_free_core.py`; nothing here needs a value that would
raise.

### 39.4 Live probes: sites left counted, not guarded

No conservative pre-check exists for these, so the `Try` stays and its
`Except` records the site. Run each ONCE, on a scratch setup, with the
39.2 readings around it, and record: dialog or not, the
`caught_exceptions` change, `last_caught.where`, and the reply.

A count settles only an ORDINARY raise. Where a probe's failure would be
an undeclared identifier (a member the interface lacks, or one read on an
un-narrowed reference), it is not counted at all: it is 39.5's class (b),
the loop is dead after OK, and every later probe in the session is lost.
Rows marked **FATAL-IF-WRONG** are that kind: run them deliberately, LAST
in the session, ONE per session, with a human present to stop the Script IDE
and rerun StartMCPServer (39.5). Since the .03 run every row whose failure
would be a member absent from the runtime object is marked so - P15, P16,
P17, P20 (its read-back), P24, P26, P28, P29, P35, P37-P40 and the sweep's
P41-P49 - and a row that reads "1 = the member does not exist" is wrong in
the same way: that outcome is a dead loop, never a count.

| Probe | Site (`where`) | Setup and call | What settles it |
|---|---|---|---|
| P1 | `App_ExitActiveCommand:PCBServer.PostProcess` | Restart Altium, open ONLY a SchLib, `app_exit_active_command` | 0 = `PCBServer` answers with no board open; 1 = it raises and the loop now stops after the first (it used to raise 8 times); a stopped loop or `where` = `dispatcher` = an undeclared identifier, which no `Try` catches |
| P1b | same | Open a PcbLib, close it, `app_exit_active_command` | `pcb_post_process: true` confirms a closed library leaves the server registered (why this site is NOT gated on an open PCB document) |
| P2 | `dispatcher` (in `App_CreateDocument`) | `app_create_document kind=NOTAKIND file_path=<scratch>\x.dat add_to_project=false` | `CREATE_FAILED` = `OpenNewDocument` answers Nil, nothing to do; `INTERNAL_ERROR` + a dialog = it raises, and unknown kinds need a refusal |
| P3 | `GetPCBBoardAnywhere:OwnerDocument` | 39.3 item 4 | 0 = the Nil test was enough; 1 = reading `OwnerDocument` itself raises on a non-document view |
| P4 | `MechLayerFromNumber:LayerUtils.MechanicalLayer` | Any tool given the layer `Mechanical21` on a board or library (e.g. `pcb_get_mech_layer_names` on one using mechanical 17+), and `pcb_set_layer_visibility layer=Mechanical21` | 0 expected on AD26; on a pre-AD19 build, record the result (an undeclared `LayerUtils` would show as `dispatcher` or a stopped loop). The .03 build: the visibility reply echoes `"layer":"Mechanical21"` (the first 2026-10-11 build echoed `Unknown`; `GetLayerString` now names Mechanical17-64 by a verified `MechLayerFromNumber` lookup, only on AD19 and later) |
| P5 | `ReadMechKind:Kind` | Only on a build below AD19: `pcb_get_mech_layer_names` | kinds read -1 and nothing is counted (the read is gated off below 19) |
| P6 | `App_CreateDocument:DoFileSave`, `App_CreateDocument:SaveObject` | 39.3 item 5, and the same with a folder that exists but is read-only | 0 confirms the folder test is the whole story; a count means a save raises in an existing folder |
| P6b | `ResolvePCBBoard:OpenDocument`, `GetPCBBoardAnywhere:OpenDocument` | FIRST the positive controls in 39.3 item 2 (a closed HEALTHY board must still open). Then a 0-byte file renamed `scratch.PcbDoc`: pass it as `pcb_place_components` `board_path`, then list it in a scratch project and run `app_get_preferences` with a sheet focused | THREE possible outcomes for a broken file, not two: 0 = Altium answers Nil; 1 = it raises (counted); and the THIRD, the one measured on the .02b run: an UNCOUNTED Altium modal ("Warning", "File type not recognised.", class (c)) blocking ~13 s, then a NON-Nil BLANK document - `pcb_place_components` placed its part into it and `app_get_preferences` reported its settings. On the .03 build neither call reaches the open: `pcb_place_components` (and `pcb_focus_board`) are refused `BOARD_FILE_UNREADABLE` (the message names the path and says "Nothing was opened or changed"), and `app_get_preferences` answers `"pcb": null` (or another open board), with no dialog, 0 catches, and `scratch.PcbDoc` still closed and still 0 bytes. A box = the pre-open file test (`FileLooksLoadable`) let it through: record the file's size and first bytes |
| P7 | `Lib_Link3DModel:ModelFactory_FromFilename`, `PCB_Place3DBody:ModelFactory_FromFilename`, `PCB_Place3DBody:RemovePCBObject` | `pcb_place_3d_body` and `lib_link_3d_model` with a `.wrl`, an `.igs` and an `.stl` model, then with a renamed `.dat` file; count the bodies on the board (and on the footprint) before and after each call | `Model3DExtMayLoad` is a DENY-list, so each of these reaches the loader. An empty model (no readable box AND an overall height of 0) is `MODEL_LOAD_FAILED` "loaded as an empty model". BOARD half: the body is taken off the board in the same transaction (PASS on the .03 run: counts unchanged). LIBRARY half, CORRECTED for 2026.10.13.02 (the .03 run FAILED here: the footprint-only undo left the empty body LISTED on the footprint - 2 bodies, 7 after four empty loads - and `lib_save_library` wrote it into the file): the undo now makes ONE footprint removal by address (`RemoveLibBodyByAddress`: at most two `RemovePCBObject` calls on the body, `Body3D_RemoveAll`'s documented limit - a third sighting means the removal is silently refused) and reads the footprint again; only if the footprint no longer lists the body AND is current does it try the library board (`LibBoardRemoveEntries`, rule 4), then reads both again. The error's `details` carry `footprint_still_lists`, `board_still_lists`, `footprint_remove_rounds`, `footprint_current`, `board_removal` and `removed_from_both`. EXPECT THE LIKELY OUTCOME, the .03 one made honest: `footprint_still_lists: true`, `footprint_remove_rounds: 1`, `board_removal: {requested: false}`, `removed_from_both: false`, and the message saying "could NOT be taken off" and "Do NOT save this library" (the .03 removal answered True and the body stayed listed, and repeating it is not a fix). Record it, do NOT save, `lib_reload_library`, and count the bodies again (the count from before the call). That outcome is the evidence that the real fix is judging the model BEFORE the add ("Still open" below). If instead `footprint_still_lists: false` and `board_still_lists: 0` ("taken off the footprint and off the library board"), MEASURE THE FILE: `lib_save_library` on the scratch copy, `lib_read_pcblib_file` on that footprint: no extra body. If `board_still_lists` reads 1 (section 31 measured a board removal on a PAD that did not take; no run has removed a component body from the board) the message says "Do NOT save": record it and do not save. The renamed STEP and a real `.step` still place. Do NOT add an extension to the deny-list: the content decides |

The rows below are the per-file stages' sites (Library, Generic, PCB,
Project), filled in at integration. A `<prop>` / `<id>` in a `where` is
the property name and the schematic `ObjectId` the row carries, so the
row itself names the pair that popped.

| Probe | Site (`where`) | Setup and call | What settles it |
|---|---|---|---|
| P8 | (none: nothing is read off the editor's selection any more, and the held-symbol globals are assigned at pump start - D-F2-1) | **FATAL-IF-WRONG. The FIRST call of a fresh session** (restart Altium, StartMCPServer, nothing else through the bridge). If it passes, the session may continue with non-fatal sections; no other FATAL-IF-WRONG probe in that session. Open a SchLib by hand (a symbol is selected in the editor; nothing created through the bridge). WITHOUT component_name: `lib_add_symbol_lines lines=[{x1:0,y1:0,x2:100,y2:0}]`, `lib_add_pins` (two pins), `lib_add_symbol_rectangle`, `lib_get_pin_list`, `lib_split_pin_functions`, `obj_create object_type=eLine container=component`, `lib_link_footprint footprint_name=X` | Every one answers `NO_COMPONENT` (the edits' message names component_name and lib_set_current_component), no dialog, no count, the library file unchanged and not marked modified. On the second 2026-10-11 build the first call halted the loop ("Undeclared identifier: CurrentPartID", Library.pas:122), and on the .12.02 build again (Library.pas:146, `OwnerPartIdOf`; D-F2-1: the held global was Empty, not Nil, because the pump never assigned it). 2026.10.13.02 assigns it at pump start. A box now is class (b): stop the run and record the unit and line the Script IDE shows |
| P9 | (none) | Same session, after P8. `lib_add_pins component_name=<the selected symbol>` with two pins, one carrying `owner_part_id=0`; then `lib_add_symbol_lines` and `lib_get_pin_list` WITHOUT a name; then `obj_batch_create component_name=<another symbol> operations=[two eLine items with container=component]` | The pins land on that symbol (visible in the editor without reselecting it: `MarkLibDirtyComp` redraws the component the edit wrote into), the symbol is now current, the explicit pin reads `owner_part_id` 0 and the other the symbol's CURRENT part, its `CurrentPartID` (1 on a single-part symbol, never 0: a symbol that reported part 0 is moved to part 1, display mode 0, as `lib_set_current_component` does; on a multi-part symbol `CurrentPartID` can differ from the part the editor's spinner shows, so check the owner part, not the picture); the nameless calls act on the same symbol (it is now held). The batch answers `created: 2` on the other symbol (it resolves through an `ISch_Lib` local: the first .03 draft passed its `ISch_Document`), the library reads modified afterwards and the two lines are drawn without reselecting the symbol (`MarkLibDirtyComp`, as every `Lib_Add*` handler; before the review the component-container create paths flagged nothing). `obj_create object_type=eLine container=component component_name=<another symbol>` the same. No dialog, no count. `NO_COMPONENT` for a name that exists = the index and the walk both missed it (record whether `lib_set_current_component` finds it) |
| P10 | (none: the held component's liveness) | `lib_create_symbol X`, then close and reopen that SchLib in the UI, then `lib_add_pins` without a name; then a by-name edit of ANOTHER symbol in that library: `lib_set_component_description component_name=<another symbol> description=p10`, and `lib_batch_set_params` on it | `NO_COMPONENT` for the nameless add, no dialog, no count: the held component was dropped when its library closed (`LastCreatedLibIsLive`), and the editor's selection is no longer a fallback. The by-name edits succeed with no dialog whatever the liveness answer: `MarkLibDirty` touches no component (the first .03 draft invalidated the held one on every edit). **FATAL-IF-WRONG**: a dialog on the nameless add = `Client.GetDocumentByPath` hands back the same interface after a reopen, so the freed held component was used (record which 39.5 class, and whether two lookups return the same object) |
| P11 | `ApplyLabelFormat:write` | Scratch SchLib: `lib_set_label_formats ops=[{target: designator, orientation: 90}]`, then `[{target: designator, justification: 12}]`, then the in-range `[{target: designator, orientation: 1, justification: 4}]` | SETTLED on the .03 run (DEFECT): no raise, the values were STORED as given and the save wrote `Orientation=90\|Justification=12` into the file. 2026.10.13.02: the first two are refused `INVALID_ARGUMENT` by the Python tool (naming `ops[0].orientation=90` and the range 0..3 / 0..8), nothing sent, the library unmodified and its file unchanged; the third applies. A stale client reaching the handler gets the same refusal from `LabelOrdinalProblem`, before any focus or PreProcess |
| P12 | `Lib_InstallLibrary:InstallLibrary`, `Lib_UninstallLibrary:UnInstallLibrary` | Install and uninstall a real scratch `.SchLib`; then install a RELATIVE name that does not exist; then uninstall a name that is installed under another spelling of its path (forward slashes, other case) | The .03 run (DEFECT): the relative name answered `installed: true` and the respelled uninstall `uninstalled: true` while the list did not change. 2026.10.13.02 reads the installed list before and after: the real file `installed: true` with `added` naming it, then `uninstalled: true` with `removed` naming it; the relative name `installed: false` with a reason ("the installed list did not change"); the respelled path `uninstalled: false`, the reason naming the installed spelling, the library still installed. `lib_get_installed_libraries` agrees with every reply. 0 catches |
| P13 | (none since `.13.01`: neither handler asks `GetPCBLibraryByPath` or loads in the background any more) | `lib_copy_footprint` and `lib_move_footprints` into a destination `.PcbLib` that exists on disk but is NOT open; then a copy into a 0-byte `.PcbLib` and into a relative missing name | SETTLED on the .03 run: the lookup answered Nil (0 catches) and the background load ran - and the copy reported success into a library no document owns: not in the file, `lib_save_library` `NOT_LOADED` (DEFECT). 2026.10.13.02 loads NOTHING in the background and never asks `GetPCBLibraryByPath` in these two handlers: every library, source or destination, read or edited, is taken from its open DOCUMENT (opened first with WorkspaceManager:OpenObject when closed - only an absolute path to an existing, readable `.PcbLib` - then focused and read with `GetCurrentPCBLibrary`, the route every lib_ edit uses). EXPECT the library to appear as an open document, the copy to answer `dest_opened: true`, `marked_modified: true`, `pending_save: true`, and after `lib_save_library` (`bridge_edited` true, `file_age_changed` true) the footprint IN THE FILE (`lib_read_pcblib_file`). The 0-byte and relative cases: `DEST_NOT_OPEN`, "Nothing was changed", the message saying whether an open was tried, no dialog. A move with the default `delete_from_source` and a closed source opens the source too (or `SOURCE_NOT_OPEN`), deletes only footprints whose NEW copy the destination lists, and both libraries save. THEN THE STALE-INSTANCE SEQUENCE (the review's case; run it in a session where the .03 build never loaded these files): copy-only out of a CLOSED X (`lib_move_footprints delete_from_source=false`), then `lib_copy_footprint` and `lib_move_footprints` (default delete) INTO X, then `lib_save_library` X and `lib_read_pcblib_file` X: every footprint reported moved or copied must be IN THE FILE, and the moved ones gone from their source's file. A footprint reported but missing from X's file = the edit went to a library the document does not own: record it, it is a defect. `DEST_NOT_OPEN` "could not be taken from the document" = the focus did not land (click the tab, retry, record it) |
| P14 | (none: the reader's source) | Save a SchLib, add a symbol through the bridge WITHOUT saving, then `lib_get_component_details` on it by index | The new symbol is absent = the component reader reads the FILE, as the `LibFileMissing` guards assume; present = it reads the open document, and those guards would lose components of an unsaved library |
| P15 | `GetSchProperty:<prop>:<id>`, `SetSchProperty:<prop>:<id>`, `GetSchVertexProperty:VerticesCount:<id>` | **FATAL-IF-WRONG**, ONE pair per fresh session, human present. Only pairs the 2026.10.13.02 allowlists ADMIT but no live run has proven. THE COMPLETE LIST (shrink the allowlists from this evidence): **eParameterSet** - `Name`, `FontId`, `LineWidth`, `Style`, `Alignment`, `TextColor`, `Justification`, `Width`, `IsSolid`, `IsMirrored`, `Orientation` (port-like AND label-like members on one type, and the class is measured to lack `Text`, the sweep marks `ParamSet.Name` UNKNOWN - P44: the riskiest row, run it first); `Name` on eProbe, eHarnessConnector; `FontId` on ePort, ePowerObject, eLabel, eSheetEntry, eTextFrame, eNote, eProbe, eCrossSheetConnector; `LineWidth` on ePort, eBus, eBusEntry, eSheetSymbol, eImage, eTextFrame, eNote, eProbe, eHarnessConnector; `Style` on eSheetEntry, eNoERC, eProbe, eHarnessConnector, eCrossSheetConnector; `IOType` on eSheetEntry, eHarnessConnector; `Alignment` on eSheetEntry, eTextFrame, eNote, eProbe, eHarnessConnector; `TextColor` on every type it admits (ePort, eSheetSymbol, eSheetEntry, eTextFrame, eNote, eProbe, eHarnessConnector); `Justification` on eNetLabel, ePort, ePowerObject, eLabel, eTextFrame, eNote, eProbe, eCrossSheetConnector; `Width` on eSheetEntry, eTextFrame, eNote, eProbe, eHarnessConnector; `IsSolid` on eSheetSymbol, eImage, eTextFrame, eNote, eProbe, eHarnessConnector; `IsMirrored` on eNetLabel, ePowerObject, eParameter, eLabel (P17). eCompileMask is in no allowlist (the identifier itself has never been evaluated live). `obj_query` the one pair on a scratch sheet | CORRECTED: this row used to say "each row names a pair to add" - it assumed a missing member is COUNTED. It is the engine's halt (P21): the first gap ends the session. A pair OUTSIDE the allowlists is never asked any more: `obj_query eNetLabel properties=Name` (zero-risk, run it in any session) answers `""` listed under `properties.unreadable`, no dialog. A box on an admitted pair = drop that type from that name's allowlist |
| P16 | `Gen_HighlightNet:Selection:<id>`, `Gen_ClearHighlights:Selection:<id>`, `SetSchProperty:Selection:<id>` | **FATAL-IF-WRONG** (a type without `Selection` is the halt, not a count): one session, last. `obj_select` on ePin, eSheetEntry and eParameter; `obj_highlight_net` on a net that reaches a sheet entry; `obj_clear_highlights` on a sheet with parts | No box. A box names a type without `Selection`: skip it in the loops (and in `Gen_CopyObjects`, which sets Selection with no Try) |
| P17 | `SetSchProperty:IsMirrored:<id>` | **FATAL-IF-WRONG**, one pair per session, last: `obj_modify set='IsMirrored=false'` on eLabel, eNetLabel, eParameter, eParameterSet (the non-component types the allowlist still admits) | No box. eNoERC, eLine, eImage, eTextFrame are no longer written at all (`SchObjectLacksIsMirrored` is an allowlist): zero-risk, nothing touched, and the reply lists `IsMirrored` under `failed` (it used to claim the write). A box on an admitted type = drop it from the allowlist |
| P18 | `Gen_WriteProjectFile:DM_ProjectFullPath`, `Gen_AddSheet:DM_ProjectFullPath`, `Gen_DeleteSheet:DM_ProjectFullPath`, `Gen_DeleteSheet:FocusedProjectFullPath` | With ONLY free documents open: `proj_add_sheet`, `proj_delete_sheet` with no `project_path`, and a sheet delete that refocuses | 0 = fault-only (expected). If `proj_add_sheet` builds its path from the text `Free Documents`, that is a separate correctness bug |
| P19 | `Gen_GetConstraintGroups:DM_ConstraintGroupCount`, `Gen_GetConstraintGroups:DM_ConstraintGroups`, `Gen_GetConstraintGroups:DM_ConstraintCount`, `Gen_GetConstraintGroups:DM_Constraints`, `Gen_GetConstraintGroups:DM_Kind`, `Gen_GetConstraintGroups:DM_Data`, `Gen_GetConstraintGroups:DM_TargetKindString`, `Gen_GetConstraintGroups:DM_TargetId` | `sch_get_constraint_groups` on a sheet, then on a sheet that has constraint groups | No dialog (it used to raise on EVERY call: an ISch_Document has no DM_ members). On the second sheet the group and constraint reads had NEVER run before this build: 0 = they answer; each row names a member `IConstraintGroup` / `IConstraint` lacks (one dialog per group or constraint), to drop from the read |
| P20 | `Gen_PlaceHarnessConnector:HarnessType` | `sch_place_harness_connector` with `harness_type` set, then `obj_query object_type=eHarnessConnector properties=HarnessType` | SETTLED on the .03 run: the write RESOLVES (no box, no count). The stored value was unverified because `GetSchProperty` had no branch; 2026.10.13.02 reads it on eHarnessConnector only. EXPECT the `harness_type` you passed. The read is a getter of the member the write proved, on the same type, but it has never run: run the query LAST in its session (**FATAL-IF-WRONG**) |
| P21 | (none: the write is gone) | `sch_place_cross_sheet_connector` with `side=left`, then `side=right`, then with no `side` and `net=P21` | SETTLED FATAL on the .03 run (2026-10-12 02:51): "Undeclared identifier: Side", Generic.pas line 8307, inside an open PreProcess, loop dead. The cross-sheet connector publishes no `Side`, and the line before it (`Conn.Text`, through the SAME base-typed local) ran - so the declared type of a local is not what the engine resolves against. 2026.10.13.02: any non-empty `side` is refused `NOT_SCRIPTABLE` by the Python tool (nothing sent) and by the handler (a stale client), before anything is created. ZERO-RISK NOW: no dialog, no count, nothing placed; the no-side call places the connector named P21 |
| P22 | `ProcessSchDocObjects:owner-remove` | `obj_delete object_type=eSheetEntry` with a filter matching one entry on a hierarchical sheet | 0 and the entry gone = removal through the owning symbol works; 1 = the owner refused and the document-level fallback ran |
| P23 | (the rule read is no longer counted; since the `.13.01` merge, which brought upstream's via template and annular-ring work (section 41), the per-via filter and write are: `PCB_NormalizeVias:ViaPassesFilter`, `PCB_NormalizeVias:SetViaGeometry` - a count on either = that via skipped (`filtered`) or `failed`, the rest of the run stands; record the `last_caught`) | **FATAL-IF-WRONG (one unproven identifier left: `Board.FindDominantRuleForObject`, unchanged since before 2026-10-11 and never yet run).** First, zero-risk: `pcb_get_rule_properties name=RoutingVias` and record `rule_kind` and `descriptor`. Then `pcb_normalize_vias` on a scratch board with one free via and only the default RoutingVias rule; then on a board whose only vias are inside a component | Record the reply. Every rule is now asked only `Name` and `Descriptor` (both proven), the values are parsed from the descriptor (`PreferedWidth=` / `PreferedHoleWidth=`, mil or mm), and the kind is the LITERAL 11 (the second 2026-10-11 build read `PreferedWidth` off the dominant `IPCB_Rule`; the first .03 draft read it through an `IPCB_RoutingViaStyleRule` iterator local, with `eRule_RoutingViaStyle` - all never run, for every rule on every board). Numeric descriptor and `rule_kind` 11: `free: 1`, `changed: 1`, `via_rule_values` >= 1, `no_rule_values: 0`, the via at the rule's preferred size and hole. Template descriptor ("Templates Used To Check Via: ..."): `via_rule_values: 0`, `no_rule_values: 1`, `changed: 0` - not a defect, it records that the values must come from elsewhere. `rule_kind` not 11 with `via_rule_values` >= 1 and `no_rule_values: 1` = the literal is wrong (record the number). The component-only board: `free: 0`, nothing read, nothing changed, the board NOT marked modified (it is marked only when `changed` > 0). ORDER MATTERS: the zero-risk `pcb_get_rule_properties` step first, so the ordinal is known before the unproven call runs; the dominant-rule lookup is made only when some rule gave values (a template-form board never makes it), and it runs in a read-only pass BEFORE `PCBServer.PreProcess`, so a halt there leaves no PreProcess open. A box = class (b) on `FindDominantRuleForObject`; it CANNOT tell an unknown identifier from a refused Integer kind argument (the 11 is passed in an Integer variable; Integer-for-enum precedent exists only for other parameters), and either one means the call must change - record the line the Script IDE shows |
| P24 | `PCB_PlaceEmbeddedBoard:XLocation`, `PCB_PlaceEmbeddedBoard:YLocation`, `PCB_PlaceEmbeddedBoard:DocumentPath`, `PCB_PlaceEmbeddedBoard:RowCount`, `PCB_PlaceEmbeddedBoard:ColCount`, `PCB_PlaceEmbeddedBoard:RowSpacing`, `PCB_PlaceEmbeddedBoard:ColSpacing`, `PCB_PlaceEmbeddedBoard:MirrorFlag` | **FATAL-IF-WRONG**: one `pcb_place_embedded_board` call on a scratch board, last in its session | No box. A box names a member `IPCB_EmbeddedBoard` lacks (it is the halt, not a count); for the locations, position the board with `MoveByXY` from its bounding rectangle instead |
| P25 | `PCB_SetMechLayerKind:AddPair` | `pcb_set_mech_layer_kind` with a paired kind and `partner_layer` on two layers that are ALREADY paired | 0 = `AddPair` is idempotent; 1 = it raises on an existing pair, and the add needs a `PairDefined` check too |
| P26 | `PCB_ModifyLayer:CopperThickness`, `PCB_ModifyLayer:DielectricType`, `PCB_ModifyLayer:DielectricHeight`, `PCB_ModifyLayer:DielectricConstant`, `PCB_ModifyLayer:DielectricMaterial`, `DielectricTypeToken:Dielectric` | **FATAL-IF-WRONG**, last: `pcb_modify_layer` on Mechanical1 with `copper_thickness_mils`, and (another session) on TopSolder with `dielectric_constant` | No box. A box names a member that layer kind lacks: refuse it for that kind up front (see P40) |
| P27 | `dispatcher` (in `PCB_CreateDesignRule`) | `pcb_create_design_rule` with `rule_type=solder_mask_expansion`, then `rule_type=vias_under_smd` | 1 = that rule type's factory or member is not scriptable: refuse it with `NOT_SCRIPTABLE` up front, as `pcb_set_via_soldermask_relief` does |
| P28 | `GetPCBProperty:<prop>` | **FATAL-IF-WRONG**, last: `obj_query object_type=eConnectionObject props=IsRedundant,Mode` | No box. A box: delete that branch and drop the name from `IsKnownPCBProperty` / `KnownPCBPropertyList` |
| P29 | `SetPCBProperty:<prop>` | **FATAL-IF-WRONG**, last: `obj_modify` on a scratch region, `set='Kind=board_cutout'` | No box. A box = the region Kind write is not scriptable: drop the write branch, keep the read |
| P30 | `Proj_Create:ForceDirectories`, `Proj_Create:Rewrite` | `proj_create` over an existing READ-ONLY project stub in an existing folder | `Proj_Create:Rewrite` = the residual fault (read-only or locked stub) the folder and name checks cannot see; `ForceDirectories` should never count |
| P31 | `Proj_ImportDocument:CopyFile` | With a project focused, `proj_import_document` with a `source_path` outside the project folder | A count + dialog = `CopyFile` is not in the binding: copy in Python (`shutil.copy2`). The copy is now checked by its OUTCOME (`FileExists(DestPath)` after the call, `COPY_FAILED` if absent), so a `CopyFile` that returns False quietly answers `COPY_FAILED` like one that raises, and the missing-folder refusal before it is that same reply whichever it is. Success with no file at `dest_path` would be a bug in the outcome check |
| P32 | (none: `PcbLibMayBeCurrent`, `PcbLibMayBeCurrentAt`) | (a) Right after `app_create_document kind=PCBLIB`, with no other step between: `lib_create_footprint`, `lib_add_footprint_pad`, then `pcb_renumber_pads`. (b) Focus a SchLib in the UI, then `lib_reload_library` on a SAVED `.PcbLib` (its programmatic close and reopen can leave the DM focus on the SchLib, stale rather than Nil) | (a) All act on the new library. (b) `reloaded: true` (`PcbLibMayBeCurrentAt` accepts a PcbLib open at `library_path` whatever the focus says). `NO_PCBLIB` in either = the DM focus AND the open-document walk (and, in b, `GetDocumentByPath` at the path) all missed a library that is current, and the gate needs widening |
| P33 | (none: syntax check) | `audit_find_via_antennas` on a board with an internal plane | No dialog. A dialog per via and plane layer = `Via.IsConnectedToPlane[LayerId]` needs parentheses, not brackets (the docs show `( )`) |
| P34 | `ModifiedFlagState:Modified`, `ModifiedFlagRaw:Modified`, `ModifiedFlagTests:Modified` | Every bridge edit (section 38 step 2 on the free SchDoc is the one that matters): the mark reads the flag through these readers | 0 expected for the `:Modified` sites: the read and the two proven tests (`If X`, `If Not X`) never raised live. A `:Modified` count = the read raised; the mark then reads `marked_modified: false` with problem "the modified flag could not be read" and `flag_raw` `unreadable` (saves still write it: doubt writes). The readers run no compare of the value at all (the `:compare` sites of the first cut are gone, review of d198b65: they ran on every SchDoc mark, and a `Try` does not stop the engine's modal), so nothing here can raise on the odd value itself. A count, a modal or a `dispatcher` catch on the first mark = the READ raised - record the modal text and `last_caught` |

The rows below are the 2026-10-11 sweep's UNKNOWN sites (a member read on
a reference that may not be narrowed, never measured). Each is
**FATAL-IF-WRONG**: run them last, one per session if possible, with a
human present. P36 is the exception since the pre-deploy review: its
unproven read is gone, so it is an ordinary check.

| Probe | Site | Setup and call | What settles it |
|---|---|---|---|
| P35 | `CurrentFootprintNarrowed` (Library.pas; `ResolveLibFootprint`, `Lib_GetFootprintPads`, `Lib_Link3DModel`, `PCB_RenumberPads`) | Restart Altium, open a PcbLib by hand and select a footprint (nothing through the bridge). With NO footprint name: `lib_export_kicad_footprint output_path=<a scratch .kicad_mod>` (it reads the pads through `Lib_GetFootprintPads`), `lib_add_footprint_pad designator=99`, `pcb_renumber_pads`; then `lib_link_3d_model model_path=<a scratch .step>` | Each acts on the selected footprint, no dialog. The .03 build takes the library iterator's reference when it compares equal to `PcbLib.CurrentComponent` (the measured LibMakeCurrentVerified compare) and `CurrentComponent` itself otherwise, exactly as before - so a box means `CurrentComponent`'s members fail like `CurrentSchComponent`'s AND the compare missed |
| P36 | `Audit_FindNetLabelConflicts` (`PinOwnerDesignatorAt`) | `audit_find_net_label_conflicts` on a scratch sheet with a net label sitting on a pin's connection end | NO LONGER FATAL-IF-WRONG: `Pin.OwnerSchComponent` is gone, and the owner is found by the proven shape (a sheet iterator over components, each component's own pin iterator, `Comp.Designator.Text`). An ordinary check: the entry names `<designator>.<pin>`, no dialog. `.<pin>` with an empty designator = no component's pin sat at that location with that number (record the sheet) |
| P37 | `Proj_Annotate` (TInterfaceList items) | `proj_annotate` on a scratch project with three unlocked parts, order `across_then_down` | Designators renumbered, no dialog. The .03 build holds the list as the base `ISch_GraphicalObject` and narrows after retrieval (the PCB side's measured cure); a box = the SCH side fails differently |
| P38 | `FocusedServerDoc` / `GetPCBBoardAnywhere` (`View.OwnerDocument`) | 39.3 item 4 (P3) with the Home page focused, then `app_list_documents` with the Home page focused and at least one document LOADED and clean (each clean loaded document asks the focused view for its owner) | Both answer, no dialog (the 2026-10-11 `app_list_documents` ran with the Home page focused but nothing loaded, so it did not reach the read). A box = `OwnerDocument` is not on a non-document view: use `DM_FocusedDocument.DM_FullPath` + `Client.GetDocumentByPath` |
| P39 | `PCB_GetSelectedObjects` (`Board.SelectecObject[]`) | Select a track and a pad on a scratch board by hand, `pcb_get_selected_objects` | Both listed with their properties, no dialog. A box = the indexed property hands back an un-narrowed object: collect the selection with the iterator flag scan instead |
| P40 | `PCB_ModifyLayer` on a non-copper layer (P26) | P26's calls | As P26, but a box there is class (b), not a count: refuse those members for that layer kind up front |

The rows below are the 2026-10-12 base-typed sweep's UNKNOWN sites: a
member reached in normal use on a runtime type it has never run on, the
SDK suggesting it is present. Nothing was guessed for them in
2026.10.13.02; each is **FATAL-IF-WRONG**: one per fresh session, LAST,
human present. Where a box would also leave a transaction open, the row
says so (after the restart, `app_exit_active_command`).

| Probe | Site | Setup and call | What settles it |
|---|---|---|---|
| P41 | `PCB_ReplicateLayout` (`Prim.ReplicateWithChildren`, inside `PCBServer.PreProcess`) | `pcb_replicate_layout` on a scratch board whose source channel holds a polygon pour, then (another session) one holding a region, each on an internal net | No box, the pour / region copied. A box = the member is absent on that type: regions go back to the proven `Replicate`, polygons are skipped with a count. A box ALSO leaves the PCB PreProcess open (every PCB save refused until `app_exit_active_command` after the restart) |
| P42 | `Library.pas SetOwnerPart` on a polygon (`OwnerPartId` / `OwnerPartDisplayMode`, inside PreProcess) | `lib_add_symbol_polygon` on a held scratch symbol | No box, the polygon on the symbol's current part. The polygon class is measured to lack the BASE member `Location`, so a base member is not enough here. A box = move the call before the PreProcess and drop the part stamp for polygons |
| P43 | `Audit_FindBadConnections` (`TearDrop` on tracks and arcs) | `audit_find_bad_connections` on a scratch routed board | No box. A box = drop the TearDrop test (teardrop copper then joins the candidates) |
| P44 | `Gen_GetDirectives` / `Gen_PlaceDirective` (`ParamSet.Name`, `ParamSet.Location` on eParameterSet) | `sch_get_directives` on a scratch sheet with one directive, then (another session) `sch_add_directive` | No box, the directive named. The parameter-set class is already measured to lack `Text`. A box = take the name from the child parameter and drop the `ParamSet.Name` write |
| P45 | `PCB_AddTestpointsForNetClass` (`IsTestpoint_*` / `IsAssyTestpoint_*` on a VIA, inside PreProcess) | `pcb_add_testpoints_for_net_class` on a scratch board whose class has a via | No box. A box = test pads only; it also leaves the PreProcess open |
| P46 | `SetSchProperty:UniqueId` (`SetState_UniqueId` with no ObjectId gate) | `obj_modify object_type=eSheetSymbol set='UniqueId=ABCDEFGH'` on a scratch sheet | No box. A box = gate the write to eSchComponent (release step 17's scope) |
| P47 | `Gen_PlaceImage` (`Corner`, `FileName` on eImage) | `sch_place_image` with a scratch PNG | No box, the image placed and sized. `Gen_MirrorSchComponent` deliberately leaves eImage out of its Corner branch, so Corner is the riskier write. A box = place without Corner and size it another way |
| P48 | PCB base members (`Layer`, `Net`, `InComponent`, ...) on rule, connection, dimension and component-body objects; `pcb_get_selected_objects`' default `Layer,Net` | (a) `obj_query object_type=eDimensionObject properties=Layer,Net`; (b) another session: select a dimension and a 3D body by hand, `pcb_get_selected_objects` with no `properties` | No box. A box = a per-ObjectId allowlist for those types, and the selection reads Layer/Net only on track/pad/via/arc/fill/text/region/poly/component |
| P49 | Sheet members (`SheetStyle`, `SheetSizeX/Y`, `TitleBlockOn`, grids, units) on a focused SchLib (`Gen_GetDocumentInfo`, `Gen_SetGrid`, `Gen_SetSchUnits`, `App_GetPreferences`; `Gen_SetSheetSize`) | Restart Altium, open ONLY a scratch SchLib and focus it: `obj_get_document_info`, then `app_get_preferences` | No box. A box = skip the sheet fields when the focused document is `eSchLib` (report null) and refuse `sch_set_sheet_size` there |

Four more counted sites arrived with the `.13.01` merge: upstream
code (sections 40 to 54) now runs inside the counter. P53 is the
exception to what follows (its members run nowhere else, see its row);
for P50 to P52, every member they
call is already called elsewhere in shipped code (`DM_Projects` /
`DM_ProjectFullPath` by the project tools, `Client.ShowDocument` by
`App_SetActiveDocument`, `SetModified` / `DoFileSave` on a server document
by `WriteDocAndNote`), so none is FATAL-IF-WRONG; they run in session A
with S3. Read `caught_exceptions` before and after each, as everywhere in
this section.

| Probe | Site | Setup and call | What settles it |
|---|---|---|---|
| P50 | `App_Ping:ScriptProjects` (`DM_Projects`, `DM_ProjectFullPath` over the workspace) | `app_ping` with the script project open, then with a design project open as well | The reply lists the open `Altium_API.PrjScr` path(s), the count unchanged. A count = the field is empty and the rest of the reply stands; record the `last_caught` it names |
| P51 | `App_CreateDocument:ShowDocument` (`Client.ShowDocument` on the new document) | `app_create_document` of a scratch schematic into an existing folder while a board is focused | `saved: true`, `focused: true`, the new document in front, the count unchanged. `focused: false` with no count = the request was made and did not take: record it |
| P52 | `App_SaveDocument:SetModified`, `App_SaveDocument:DoFileSave` (on the server document of an open file) | `app_save_document` on a scratch schematic edited through the bridge, then on one that is open and clean | Both save (`file_age_changed` / `saved` true, the file on disk newer), no dialog, the count unchanged. A script unit (`.pas`) is refused `SCRIPT_UNIT` before either member is reached |
| P53 | `Gen_SimCfgDocument:SupportsReload` (`SupportsReload` on the open `.simcfg`'s server document, then `DoFileLoad` / `UpdateModifiedDate`, uncounted) | Section 46 item 3: `sim_setup_analysis` on a scratch project whose settings file is open, LAST in a fresh session, human present | `reloaded` true, no "Reload?" prompt, the count unchanged. Unlike P50-P52 these members are called NOWHERE else (the section 1 table's "46, settings reload" row): **FATAL-IF-WRONG**, a halt means one of them is not published on the runtime server document. A count with no halt = `RELOAD_UNSUPPORTED` came back; record the `last_caught` |
| P54 | `CloseServerDocByObject:CloseDocument` (`Client.CloseDocument` on the named library's server document, then `Client.GetDocumentByPath` again; called by `Lib_ReloadLibrary` and `RefreshSchLibFromDisk` since 2026.10.13.02, D-A-4) | Open a scratch SchLib and a scratch PcbLib, both saved and clean, FOCUS the SchLib, then `lib_reload_library library_path=<the PcbLib>`; then edit the PcbLib through the bridge and call it again | First call: `reloaded: true`, `was_open: true`, `closed: true`, `other_documents_closed: []`, the SchLib still open (`app_list_documents`), the count unchanged. Second call: `MODIFIED`, nothing closed. `Client.CloseDocument` is already called by `App_CloseDocument`, so this is not FATAL-IF-WRONG. A count = the close raised and `closed` reads false (`CLOSE_FAILED`); record the `last_caught` |

### 39.5 If a dialog does appear

Do not press OK straight away. First call `app_get_status`: `in_flight`
must name the command and request that are blocked, and the status form
must show the same command, request id and start time. Then
`app_list_open_dialogs`. ALL THREE kinds below are class `TMessageForm`
(measured 2026-10-11 for (b) and (c)), so NEVER classify by class.
Classify by three things together, all readable while the box is up:

| Kind | Title | Text | Script IDE |
|---|---|---|---|
| (a) catchable exception | "Error" | the exception's message (a failed conversion, "Access violation", ...) | does NOT take over |
| (b) fatal late-bound error | "Error" | starts "Undeclared identifier" | opens a `.pas` unit with the execution point on a source line |
| (c) Altium's own box | "Warning" (or another Altium caption) | Altium's sentence, e.g. "File type not recognised." | does NOT take over |

(a) and (b) share the caption and the single OK, so the text and the IDE
decide between them; (c) differs by caption and text.

* **(a) A catchable script exception** (a failed conversion, a call that
  raised). The Script IDE does not take over. Press OK: the call returns
  `INTERNAL_ERROR` (or the site's own reply), `caught_exceptions` rises by
  1, and `last_caught.where` names the site or `dispatcher`.
* **(b) The script engine's FATAL late-bound error.** Text starting
  "Undeclared identifier" (`app_list_open_dialogs` reports kind
  `script_fatal`, `ok_resumes_the_bridge: false`), and the Script IDE
  opens a `.pas` unit with the execution point on a source line. It is NOT
  an exception: no `Except` runs and nothing is counted. Record the text
  and the unit and line the IDE shows, then press OK. The call does NOT
  return: `app_ping` keeps answering "not responding", `in_flight` keeps
  the call, and `app_get_status` reports `last_fault` `dead_loop` with a
  `loop_note` (while `attached` still reads true). The bridge records it
  the moment its dialog probe sees the box (about 3 s into the call, or
  when the heartbeat extensions run out), with the local time in
  `last_fault.when`; before the .03 build it was recorded only once a LATER
  call timed out, and `when` was always empty. Since `.13.01` `when`
  (= `first_seen`) stays at that first sighting while later failed
  keep-alives move `last_seen` only, and `found_by` reads
  "script_fatal box seen" (the .12.02 run saw `when` drift with every
  failed ping). Recovery: in the Script
  IDE press Stop (Run > Stop, or Ctrl+F3), close the unit's tab without
  saving, and rerun File > Run Script > Altium_API > Dispatcher.pas >
  StartMCPServer (or restart Altium). A `PreProcess` the handler opened
  before the halt is still open; `app_exit_active_command` after the
  restart unwinds it. OK ALONE IS NOT A RECOVERY: the halted session leaves
  `Running` True, and `StartMCPPump`'s first statement (`If Running Then
  Exit`) then makes a StartMCPServer re-run do NOTHING, so press Stop
  before rerunning it. That Stop resets the script's globals is NOT
  measured; if StartMCPServer after Stop does not bring the dashboard
  back, restart Altium. After the relaunch no library symbol is held
  (`ResetSessionGlobals`): pass `component_name` or call
  `lib_set_current_component` again. Every such box is a defect (a member read the bridge
  must not make), and the session's remaining probes are lost: this is
  what ended the 2026-10-11 run at P8.
* **(c) Altium's OWN message box** (e.g. "Warning - File type not
  recognised."). It is NOT counted, before or after OK:
  `caught_exceptions` does not rise and `last_caught` does not change.
  The handler resumes after OK with whatever the Altium call answered.
  Record it as a dialog all the same - it is a defect: a path that can
  reach it must be refused before the call.

Record all of it, including the dialog's title, class and text.

### Results 2026-10-11 (the first 2026-10-11 build, AD 26.10.1.5)

39.1 PASS (0 / 0 / null, `in_flight` empty). Counters stayed 0 / 0 / null
around every call up to the modal below.

| Item | Verdict | What happened | On the .03 build |
|---|---|---|---|
| 39.3 item 2, `C:\nope.PcbDoc` and an existing `.SchDoc` | PASS | each took the fallback to the focused scratch board (one part placed on it), no dialog | unchanged |
| 39.3 item 2, an existing `.PcbLib` | FAIL | Altium's own modal "Warning - File type not recognised." inside `OpenDocument('PCB', ...)`; `in_flight` named the call, `caught_exceptions` unreadable while blocked, the library file unchanged while blocked. RUN STOPPED; the owner pressed OK. AFTER OK the library was open and read `modified: true`, `bridge_edited: true`: the part very likely went INTO the library | refused `BOARD_PATH_IS_LIBRARY` before any open (B1); the item's text corrected |
| 39.3 item 2, a missing `.PcbLib` | NOT RUN | stopped | refused the same way |
| 39.3 item 5 / P6 first half | PASS | `saved: false`, no folder created, no dialog | unchanged |
| P2 | PASS | `OpenNewDocument` answered Nil for `NOTAKIND` (`CREATE_FAILED`), no dialog | unchanged |
| P4 | PASS, echo defect | 0 catches with the scan to 64 and the `Mechanical21` name route; the reply echoed `"layer":"Unknown"` | echo fixed (L1) |
| P6 second half | NOT RUN | the read-only folder was not set up | owner step |
| P6b, P7, item 3, P1b | NOT RUN | the run stopped at the modal | run them |
| P10-P33 | NOT RUN | still blocked by the modal | run them |
| items 1, 4, 6, 7; P1, P3, P8, P9, P18 | NOT RUN | need an Altium restart, the Home page or hand actions (owner sessions) | owner-gated |

Also fixed in the second 2026-10-11 build from the same class as item 2 (a caller path
handed to `OpenDocument` under a kind its file type does not have):
`proj_run_outjob` refuses an `outjob_path` that is not an `.OutJob`
(item 8). Every other `OpenDocument` / `OpenDocumentIfPresent` call takes
a path from a project listing filtered by its document kind, not from the
caller.

### Results 2026-10-11 (the second 2026-10-11 build, AD 26.10.1.5)

Run 20:18-20:39 on scratch copies, with the carrier folder, the scripts
folder and every scratch file baselined before and after: all unchanged.

| Item | Verdict | What happened | On the .03 build |
|---|---|---|---|
| 39.1 | PASS | `caught_exceptions` 0, `cast_errors` 0, `last_caught` null; `in_flight` [] | unchanged |
| 39.3 item 7 | PASS | with only `fa_lib.SchLib` open and focused, all six calls answered `NO_PCBLIB` (confirmed in activity.log), no dialog, the counters 0 / 0 / null between every call | unchanged |
| P8 | **FAIL, FATAL** | `lib_add_symbol_lines lines=[{0,0,100,0}]` in the fresh session (the editor's current symbol was `24AA02E48T-I_OT`, the first ALPHABETICAL one, not the first stored; nothing created through the bridge): the script engine's "Undeclared identifier: CurrentPartID" (OCR "CurrentPartlD"), a `TMessageForm` titled "Error" with only OK, and the Script IDE opened Library.pas at line 122 (`SetOwnerPart`). P9's site ran BEFORE P8's own (`MarkLibDirty`), which was never reached. Not counted (`last_caught` stayed null). After OK: `app_ping` "not responding", `in_flight` stuck on the call (age 15 s, then 1120 s), `last_fault.json` `dead_loop`, the IDE still parked on line 122. The `PreProcess` opened before the halt was left open and the factory's line orphaned; the library file was unchanged | GetTargetLibComponent no longer falls back to the editor's selection: NO_COMPONENT before any PreProcess, `component_name` on every symbol-edit tool, the owner part read once from a narrowed component before the PreProcess, `MarkLibDirty` / `TargetLibComponentName` / obj_create / obj_batch_create / the library parameter delete off that reference too; P8-P10 rewritten |
| P9, P1 (the rest of the fresh-session segment) | NOT RUN | the loop was dead | run P9 after P8; P1 in a fresh session |
| Section 38 re-check; 39.3 items 2, 3, 4, 5, 8; P1b, P2-P4, P6, P6b, P7; P10-P34 | NOT RUN | three later segments (20:24-20:39) each found the bridge down and stopped with nothing changed: Altium was NOT restarted (the same pid 48856, started 20:02:48) and StartMCPServer was not rerun, although a relayed message said Altium had been restarted. Recovery needed the owner at the Script IDE (39.5 (b)) | run them on the .03 build |

What the halt taught, now in 39 "Why", 39.2, 39.4 and 39.5: the
popup-free premise that a remaining raise is COUNTED does not hold for an
undeclared identifier, and that box looks exactly like the counted one.
A whole-code sweep that night (every unit, at 3aca481) found the same
class at eight sites - every member read off `CurrentSchComponent` (four
routes into the eight `Lib_Add*` handlers, `lib_link_footprint`, the pin
readers, obj_create / obj_batch_create into a component and the library
parameter delete) and the `IPCB_Rule` constraint read in
`pcb_normalize_vias` - all fixed in the .03 build, and seven UNKNOWN ones
(three applied conservatively, one - `OwnerSchComponent`, P36 - replaced by
the proven component-iterator shape after review, the rest probes P35 and
P37-P40). A `owner_part_id`
on every pin would not have saved P9: the next line, `AddSchObject`, is a
member of the same reference.

### Results 2026-10-11 (the second 2026-10-11 build re-run on a fresh Altium, the ".02b" run, AD 26.10.1.5)

Run 21:00-21:32 after an Altium restart, on scratch copies; the carrier
folder (41 of 41, 99 with subfolders) and the scripts folder (15 of 15)
unchanged at every checkpoint, including while each modal below was up.
`caught_exceptions` 0, `cast_errors` 0 and `last_caught` null before and
after EVERY step. The section 38 re-check ran in the same session (its
results above: all PASS, every save-all as `only_modified=true`).

| Item | Verdict | What happened | On the .03 build |
|---|---|---|---|
| 39.1, P1 | PASS | 0 / 0 / null; P1 (only a SchLib open) `app_exit_active_command` answered with no dialog | unchanged |
| 39.3 item 4 + P3 | PASS | `app_get_preferences` returned the project board's settings; the board opened in a background tab, focus never left the Home page; P3 = 0 (the Nil owner test was enough) | item 4's wording corrected |
| P2 | PASS | `CREATE_FAILED` for `NOTAKIND`, nothing created, no dialog | unchanged |
| 39.3 item 5 / P6 first half | PASS | `saved: false`, `added_to_project` false, no folder, no dialog | unchanged |
| 39.3 item 3 | PASS | `"pcb": null`, no dialog, the missing board not created | unchanged |
| 39.3 item 8 | PASS | `NO_OUTJOB` in 15 ms, nothing opened, no dialog | unchanged |
| P4 | PASS | the visibility reply echoed `"layer":"Mechanical21"` (from the script's own activity.log row) | unchanged |
| 39.3 item 2 | PASS | `C:\nope.PcbDoc` and an existing `.SchDoc` took the fallback (one part each, the SchDoc not opened); an EXISTING and a MISSING `.PcbLib` refused `BOARD_PATH_IS_LIBRARY` in 32 ms, nothing opened; `pcb_focus_board` the same | unchanged |
| P1b | PASS | `pcb_post_process: true` after the library closed; dirty 0 -> 0 | unchanged |
| **P6b** | **FAIL** | BOTH halves: Altium's own "Warning" / "File type not recognised." (class `TMessageForm`, OK only, uncounted, `in_flight` named the call) blocked ~13.5 s; after OK `OpenDocument` handed back a NON-Nil blank document - `pcb_place_components` placed its part INTO `scratch.PcbDoc` (`placed 1`), and `app_get_preferences` reported that blank document's settings as `"pcb"`. Both closed with discard; the file stayed 0 bytes | refused before the open (N1): `BOARD_FILE_UNREADABLE` from the board_path handlers, the board walk skips the listing, `OpenDocumentIfPresent` answers Nil; the P6b row rewritten |
| **P7** | **reply defect** | 0 catches, 0 dialogs on all 12 calls; the stubs loaded EMPTY: `pcb_place_3d_body` success with `bbox` null / height 0 and one empty body left per call; `lib_link_3d_model` `ok` false with the body left; the renamed STEP `.dat` and a real `.step` loaded | empty model = `MODEL_LOAD_FAILED`, the body removed in the same transaction (N3); the P7 row rewritten |
| P6 second half, items 1, 6, 7, P10, the 38 hand steps | OWNER-GATED | a read-only folder, Altium restarts, hand edits | owner sessions |
| P8, P9 | DEFERRED | the known fatal symbol-edit path on the second build | run on the .03 build |

Fixed after this run, each with an executed test that fails on the second
build (`tests/test_live_02b_findings.py`):

* **N1: a file that cannot be loaded never reaches `Client.OpenDocument`.**
  One pre-open predicate, `FileLooksLoadable`, asked only of a document
  that is NOT open (`UnopenedFileUnloadable`: `Client.GetDocumentByPath`
  answers Nil) and IS on disk: the file must read (TStringList.LoadFromFile,
  the `ReadFileContent` route proven on every request - an ordinary,
  catchable EFOpenError, retried; NOT AssignFile / Reset, which modals
  before the Except), must not be empty, and a `.PcbDoc` / `.SchDoc` must
  start like one (the OLE magic's 0x11 within its first characters, or the
  ASCII form's leading `|`). `ResolvePCBBoard` answers Nil for such a named
  board and never falls back; `pcb_place_component(s)` and
  `pcb_focus_board` refuse `BOARD_FILE_UNREADABLE` before it;
  `GetPCBBoardAnywhere` skips such a listing (Continue);
  `OpenDocumentIfPresent` (`proj_load_sheets`' `SCH` opens - a 0-byte
  member is counted `failed` - and `proj_run_outjob`) answers Nil. The
  read-only callers (`app_get_preferences` and every board read) KEEP the
  walk that opens a real board in a background tab: 39.3 item 4 measured it
  with focus never leaving the Home page, and with the unloadable listings
  skipped, what the walk still opens is a real board.
* **N3: an empty model is a failed load.** See the P7 row.

### Results 2026-10-12 (the .03 build, AD 26.10.1.5, pid 6260)

Four segments on one Altium session, scratch copies only; the carrier
folder (99 of 99) and the scripts folder (15 of 15) unchanged at every
checkpoint, including while the fatal box was up. Counters 0 / 0 / null
around every step up to P21.

| Item | Verdict | What happened | Since (.12.02, carried into 2026.10.13.02) |
|---|---|---|---|
| Section 38 (incl. N2 step 14, N4 step 15) | PASS | see the section 38 results | unchanged |
| N1 (P6b both halves, 0-byte and text `.PcbDoc`, 0-byte `.SchDoc` member) | PASS | `BOARD_FILE_UNREADABLE` in ~16 ms, nothing opened; `"pcb": null`; the 0-byte sheet `failed` 1 | unchanged |
| B1 (39.3 item 2) | PASS | an existing and a missing `.PcbLib` refused `BOARD_PATH_IS_LIBRARY`; the fallbacks placed on the focused board | unchanged |
| Positive controls (a1), (a2), (b), (d) | PASS | closed healthy boards opened, closed binary sheets loaded; (d) 1062 ms with the 726,016 B board listed | unchanged; (c) still needs an ASCII fixture (owner) |
| 39.3 items 3, 4, 5, 8; P1b, P2, P3, P4, P38 | PASS | as each row expects | unchanged |
| P7 board half | PASS | every empty stub `MODEL_LOAD_FAILED`, the board count unchanged | unchanged |
| **P7 library half** | **FAIL** | the empty body stayed listed on the footprint (2, then 7 after four empty loads) and the save wrote it into the file | D-S2-1: ONE footprint removal (two `RemovePCBObject` at most), the board only if the footprint let go, both read back and reported - expect `footprint_still_lists: true` and "Do NOT save" (P7 row) |
| **P11** | **DEFECT** | orientation 90 / justification 12 stored as given and saved | D-S3-2: refused `INVALID_ARGUMENT` before any write |
| **P12** | **DEFECT** | a relative missing name `installed: true`, a respelled uninstall `uninstalled: true`, the list unchanged | D-S3-3: replies report the list's change |
| **P13** | **DEFECT** | a copy into a closed PcbLib said success into a background-loaded library; not in the file, `NOT_LOADED` | D-S3-4: every library from its open document (opened first), nothing loaded in the background, or `DEST_NOT_OPEN` / `SOURCE_NOT_OPEN` |
| P14, P19 first half | PASS | the reader reads the FILE; no constraint groups, no dialog | unchanged |
| P20 | PASS (value unverified) | the `HarnessType` write resolved | read-back branch added (P20 row) |
| **P21** | **FAIL, FATAL** | `side=left`: "Undeclared identifier: Side", Generic.pas 8307, inside an open PreProcess; loop dead; nothing pressed; `last_fault` `dead_loop` 02:51:29 | D-S3-1: `side` refused in Python and the handler; the write is gone |
| P15-P18, P22-P34 | NOT RUN | the loop was dead after P21 | run them (the FATAL-IF-WRONG ones last, one per session) |
| Sections 2, 7, 9; P8, P9; 39.3 item 1 | NOT RUN | three agents died on a network error before reaching them | run them FIRST (below) |
| 39.3 items 6, 7; P1; P6 second half; positive control (c) | OWNER-GATED | restarts, a config edit, a read-only folder, an ASCII fixture | owner sessions |

Fixed since the .03 build (in .12.01 / .12.02, carried into 2026.10.13.02),
each with an executed test that fails on the .03 build (`tests/test_live_03_findings.py`), plus the sweep's guard
(`tests/test_member_on_base_typed_reference.py`):

* **D-S3-1 (P21).** No `Side` write anywhere; a `side` is refused
  `NOT_SCRIPTABLE` before the connector is created.
* **The sweep's FATAL-LIKELY pairs.** The schematic property getter and
  setter (obj_query, obj_modify, filters, obj_create, obj_batch_create)
  govern `Name`, `FontId`, `LineWidth`, `Style`, `IOType`, `Alignment`,
  `TextColor`, `Justification`, `Width`, `IsSolid`, `Text`,
  `Orientation`, `IsMirrored`, the LibReference family, `Electrical` /
  `PinLength`, `XSize` / `YSize` and the vertex reads with per-ObjectId
  ALLOWLISTS: a type not listed is never asked and answers `""` under
  `properties.unreadable`; a write is not made and is listed under
  `failed` (`Orientation`, its long-standing case, under `unknown`). `obj_query eNetLabel
  properties=Name` and `obj_query eLine properties=VerticesCount` are now
  zero-risk.
* **D-S3-2, D-S3-3, D-S3-4, D-S2-1** as in the table.
* The sweep's UNKNOWN sites are probes P41-P49 (FATAL-IF-WRONG), nothing
  guessed.

### Results 2026-10-12 (the .12.02 build, AD 26.10.1.5)

Two fresh sessions on scratch copies (F1: Altium pid 6084, only a PcbDoc;
F2: pid 47056, only a SchLib), each after a restart, nothing else using
the bridge. The carrier folder unchanged throughout; the scripts folder
unchanged within each session (its baseline re-taken after the .12.02
deploy). Counters 0 / 0 / null and no dialog around every step up to P8.

| Item | Verdict | What happened | In 2026.10.13.02 |
|---|---|---|---|
| 39.1 (F1 and F2) | PASS | version matched, `caught_exceptions` 0, `cast_errors` 0, `last_caught` null, `in_flight` [], `last_fault` null (the .03 run's dead loop no longer showing) | unchanged |
| 39.3 item 1 (F1) | PASS, expectation corrected | `app_get_active_document` answered the Home page; `app_get_preferences` `"schematic": null` and the board's settings (the board loaded in the background, the Home page kept focus); `app_exit_active_command` `pcb_post_process: true`, **`sch_post_process: true`**, dirty 0 / 0, no box, no count. True means `SchServer` is registered with no schematic document open | item 1 now expects true; `sch_server_loaded` added to both replies |
| 39.3 item 6 (config test) | PASS | `poll_interval_active_ms` 600000.0 in `mcp_config.json`, the bridge restarted after the edit: three pings in about 2 s (default polling), no dialog, counters 0; the file restored byte-identical | unchanged |
| 39.3 item 7 (F2) | PASS | only `f2_lib.SchLib` open and focused: all six calls answered `NO_PCBLIB` (activity.log), no dialog, counters 0 after each | unchanged |
| P1 (F2) | PASS, outcome 0 | `app_exit_active_command` with only a SchLib: `pcb_post_process: true`, `sch_post_process: true`, dirty 0 / 0; no PCB document appeared | unchanged |
| **P8 (F2)** | **FAIL, FATAL** | the first call, `lib_add_symbol_lines lines=[{0,0,100,0}]` with no `component_name`: "Undeclared identifier: CurrentPartID" (`TMessageForm` "Error", kind `script_fatal`), the Script IDE on Library.pas line 146 in `OwnerPartIdOf`, about 3 s in. Loop dead; `last_fault` `dead_loop`. Nothing pressed, no primitive made, no PreProcess left open (the read runs before it), the library file unchanged. activity.log since `_session_start` shows no caller that could have set the held symbol | **D-F2-1**: the held-symbol globals (and every other global the request path reads) are assigned in `StartMCPPump`; executed fresh-pump test + a general guard |
| `app_read_panel "SCH Library"` (F2) | DEFECT (tool) | `ok: false`, "no panel named 'SCH Library'" while SCH Library was visibly open, docked on the left; it offered the right-edge tabs | **D-TOOL-2**: replies state `enumerates: edge_tabs_only` and that a docked-open panel is not found yet; the `holder_class` KeyError in `app_list_panels` fixed. Finding docked panels needs the docked tab class MEASURED first |
| `last_fault` after the halt | DEFECT (tool) | `when` moved with every failed keep-alive; the guidance was the generic dead-loop text although the box had been seen | **D-TOOL-1**: `first_seen` kept, `last_seen` moves, `found_by` "script_fatal box seen" with its own guidance |
| P8's remaining calls, P9, sections 2, 7, 9, P32, S1-S3, P23 | NOT RUN | the loop was dead | the next run, below |

### The next live run (2026.10.13.02), in this order

Done on the .12.02 build and not repeated: 39.1, 39.3 items 1, 6 and 7,
P1. Every session starts with a restart of Altium and StartMCPServer.

1. **Session A: P8 FIRST, then P9.** P8's first call is the first call of
   the session (only `app_ping` / `app_get_status` /
   `app_list_open_dialogs` and opening the SchLib by hand before it).
   Expect `NO_COMPONENT` for every P8 call. Then P9 in the same session.
   ONLY IF P8 AND P9 BOTH PASS, the session may continue with non-fatal
   sections: 2, 7 and 9 (pin edge decorations, enum words, reading a
   symbol's pins), P32 (a) and (b), then S1 (the section 38 short
   regression: 38.1-38.3, one no-argument save-all, 38.15), S2 (boards and
   models: 39.3 item 4 with positive control (d), P3, P38, positive
   controls (a1)(a2)(b), P6b both halves, P7 library half, P13, P11, P12,
   P21, the allowlist checks) and S3 (P14, P19 first half, P36, P22, P31,
   P25, P33, P18, P30, P34, P50, P51, P52, P27 last). No other FATAL-IF-WRONG probe in
   session A. A box anywhere: 39.5 (b), stop the session.
2. **P23 in a later session of its own** (its zero-risk read first, then
   `pcb_normalize_vias`), human present; the session ends after it.
3. **Each other FATAL-IF-WRONG probe in a later session of its own, ONE
   per session, human present:** P20's read-back, P10, P15 (one admitted
   pair), P16, P17, P24, P26, P28, P29, P35, P37-P40, then P41-P49. After
   any box: 39.5 (b), restart, `app_exit_active_command`, next session.


---

## 40. Board tools from a full board run

Nine findings from one board driven end to end. On a scratch board and a
scratch schematic only. Every Altium member below is declared in the
API reference or used by a working reference script; none is new to
Altium, so the risk is behaviour, not a halted loop. The two least
exercised are `BoardOutline.SetState_XSizeYSize` and
`Board.UpdateBoardOutline` (step 4).

1. **Filters refuse what they cannot apply.** `obj_delete
   object_type=eTrackObject filter="OnLayer('TopLayer')"` answers
   BAD_FILTER and deletes nothing; `obj_count` before and after agree.
   `obj_query object_type=eTextObject filter="IsDesignator"` answers
   BAD_FILTER with the hint `IsDesignator=true`, and with that filter it
   returns designators only, no pin-number or free texts.
2. **Text size.** `obj_query object_type=eTextObject
   properties="Text,Height,StrokeWidth,IsDesignator"` reads all four.
   `pcb_set_text_style height_mils=31.5 stroke_mils=3.94
   designators=["<one part>"]` answers `changed: 1` and the text reads
   back at those values; the designator is visibly smaller. Repeat with
   `obj_modify ... set="Height=40"` on a free text. `pcb_place_text
   height=31.5 stroke=3.94` reads back the same. A designator not on the
   board comes back in `not_found`.
3. **pcb_delete_object on a track's length.** Place a track
   (1300,1300) to (8250,1300) on a scratch layer and delete at
   (2000,1300): it goes, `distance_mils` near 0, `bbox_mils` is that
   track.
4. **Outline extents after a reshape.** `pcb_set_board_shape` to a
   larger rectangle, then `pcb_get_board_outline`: `bounding_rect`
   matches the vertices. `pcb_render_svg` shows the whole board. Then
   `app_ping` answers.
5. **audit_find_acute_angles returns.** On a board with long tracks it
   answers parseable JSON; each acute join is listed once.
6. **pcb_autoplace_silkscreen keeps clearance.** On a board with no Silk
   To Silk violations, the call answers `placed: 0` and every designator
   `already_clear`; DRC still shows none. Move one designator onto a
   neighbour's outline by hand: the call moves only that one, or lists it
   in `unplaced_designators` and leaves it where you put it. The reply
   names the rules its clearances came from.
7. **app_click_menu checks what it clicked.** `app_click_menu
   menu_path="Tools|Update From PCB Libraries"` (no ellipsis) either
   opens the update wizard or fails naming the item it found; it never
   reports ok with SI Setup Options open. `app_update_from_libraries
   launch="menu"` opens the right wizard.
8. **WPF buttons.** Open Tools > Silkscreen Preparation and
   `app_invoke_element name="Cancel"`: the dialog closes and the reply
   says `dialog_closed: true`. `app_press_dialog_button` on the same
   button answers `method: "uia"`.
9. **route_plan joins what it reports routed.** Place a route_plan
   result verbatim: Altium's DRC shows no Un-Routed Net or Net Antennae
   for the nets reported `routed`, and each `validation.violations`
   record names its two layers.

Verified on AD 26.10.1.6 with the previous script, build 2 of 2026.10.02
(scratch board), through the script side: step 1 (a bare `IsDesignator` filter refused with the
`=true` hint; an `OnLayer(...) Or ...` filter on `obj_delete` refused with
all four tracks intact), step 2 for a free text (Height 31.5 and
StrokeWidth 3.94 written and read back under both names; `Height=abc`
reported failed, text unchanged), step 3 (a point 3800 mil from a track's
midpoint deleted it at distance 0, with its bbox), step 4 (a reshape to
8000 x 5400 read back the same bounding_rect, the render covered it, and
app_ping answered after the outline refresh), step 5 (one acute corner of
24 degrees reported once, a 90 degree corner not). The silkscreen placer
read both clearances from the board's rules (10 mil each). Still to run:
steps 2 and 6 on parts, and steps 7 to 9, which need the MCP server
restarted on this tree.

---

## 41. Vias keep an annular ring, and take a template from another via

`pcb_normalize_vias` read a template-based Routing Via rule's size fields
and set nearly every via on a board to a pad no larger than its hole.
`obj_modify` could not repair them. On a scratch board only:

1. **Template rule refused.** Give the board a Routing Via rule in
   template mode, then `pcb_normalize_vias dry_run=true`: every via counts
   under `refused_template_rule`, `changed` is 0 and `note` points at
   `pcb_apply_via_template`. Without `dry_run` nothing changes either.
2. **Explicit sizes.** `pcb_normalize_vias size_mils=23.622
   hole_mils=11.811 from_size_mils=<one group>`: only that group changes,
   `before` and `after` count it, and `obj_query eViaObject
   properties="Size,HoleSize"` reads 23.622 and 11.811.
   `size_mils=12 hole_mils=12` is refused before anything is sent.
3. **obj_modify on a via.** `set="Size=30|HoleSize=15"` on a 20/12 via
   writes both; `set="HoleSize=40"` on it is reported failed and the via
   is unchanged. `set="TopXSize=50"` on a via is reported unknown, not
   success.
4. **Template copy (highest risk).** Set one via's template in the
   Properties panel, then `pcb_apply_via_template source_x=<x>
   source_y=<y> dry_run=true`, then without `dry_run`. Every other free
   via reads the source's size and hole, and the Properties panel shows
   the same template on a sample of them. `TemplateLink` is used by one
   reference script and nowhere in this codebase; an undeclared one
   would stop the polling loop, so `app_ping` after the call.
5. **Routing Via sizes.** On a rule not in template mode,
   `pcb_set_rule_properties name=<rule> preferred_via_size_mils=24
   preferred_via_hole_mils=12` answers `via_sizes_written: true` and the
   rule editor shows the values. A preferred hole at or above the
   preferred size is refused with nothing written.

Verified on AD 26.10.1.6 with the previous script, build 2 of 2026.10.02
(scratch board): step 3
(`Size=30|HoleSize=15` grew a 20/12 via and read back; `HoleSize=40` on it
reported failed, via unchanged; `TopXSize` on a via reported unknown), and
step 2's rule path (`pcb_normalize_vias` set two vias to the min/max rule's
50/28 with `before` and `after` counts, app_ping answered after). Still to
run: step 1 (needs a rule in template mode), step 2 with explicit sizes,
steps 4 and 5, which need the MCP server restarted on this tree.

---

## 42. lib_search says what it searched

`lib_search` reads open .SchLib files only, and an empty result used to
read as "no such part" even with a database library installed. Python
only: no script change.

1. With a DbLib (or an IntLib) installed, `lib_search query=<anything
   absent>` answers `count: 0`, lists the open .SchLib files under
   `libraries_searched`, and lists the DbLib under `not_searched` with
   `library_type: "database"` and a `coverage_note`.
2. With `library_path` set, `libraries_searched` is that one path and the
   reply carries no `not_searched`.

---

## 43. Database libraries: read, search, one record, place

A DbLib is a .DbLib file naming a database and its tables; each row is a
part. Four new bridge commands in Library.pas read it: `get_dblib_info`,
`query_dblib`, `get_dblib_record` and `place_dblib_component`, behind
`lib_dblib_info`, `lib_dblib_search`, `lib_dblib_get_record` and
`sch_place_dblib_component`. `lib_search` now searches every installed
DbLib too. The table definitions are read from the .DbLib file inside the
handler; the rows are read over ADO, which nothing in this codebase has
used before.

**This is the highest-risk step in the release.** Every ADO member below is
new here and comes from one reference script
(`reference/altium-delphiscripts-brett/common/libADOQuery (clean).pas`):
`TADOConnection`, `TADOQuery`, `ConnectionString`, `LoginPrompt`,
`Connected`, `Connection`, `SQL.Add`, `Open`, `First`, `Eof`, `Next`,
`Close`, `FieldCount`, `Fields[I].DisplayName`, `Fields[I].AsString` and
`Parameters.ParamByName(...).Value`. Placement adds
`LoadComponentFromDatabaseLibrary` (declared in the schematic API, used by
`reference/altium-delphiscripts-brett/Sch/CompPlaceFromLib.pas`) and reads
back `DatabaseTableName`. An undeclared one stops the polling loop with a
dialog no `Try/Except` catches, so run `app_ping` after every item, and
stop at the first item that does not answer.

You need an installed DbLib whose database this machine can open (an
Access .mdb or .accdb is the simplest), one key value from it, and a
scratch schematic sheet. Item 6 writes to that sheet: `app_checkpoint`
first.

1. **File only, no ADO.** `lib_dblib_info library_path=<dblib>
   with_fields=false`. Expect every table the DbLib editor shows, with its
   `enabled` flag; `connection.kind` and `provider` right for the database;
   `has_password` true only if the connection has one, and
   `connection.redacted` showing `***` where it was; `connected: null`.
   Record each table's `key_field_source` and the keys under `settings`:
   the file keys for the key column (`Key`, `UserWhere`) are inferred, not
   documented, and this is where the guess is checked. `app_ping`.
2. **ADO connection (highest risk).** The same call without `with_fields`.
   Expect `connected: true` and each enabled table's `fields` listing the
   columns the DbLib editor's field mappings show, `key_field_found: true`,
   and `symbol_ref_field` / `footprint_fields` matching its [Library Ref] /
   [Footprint Ref] mappings. Repeat with the DbLib open in the Libraries
   panel: Access is then held Share Deny Write by Altium, and the Mode=Read
   connection must still open. `app_ping`. If the loop stops here on
   `TADOConnection`, this Altium does not expose ADO to DelphiScript:
   record the version and skip to item 7.
3. **Search.** `lib_dblib_search query=<part of a known part number>`:
   hits carry `table`, `key`, `symbol_ref`, `footprints` and
   `matched_field`, and `rows_scanned` is above 0. A query that matches
   nothing gives `count: 0` with every enabled table under
   `tables_searched`. `fields=["<a real column>","Nonsense"]` searches the
   one and lists `Nonsense` under `unknown_fields`. `query="10%'"` answers
   without error and matches only that literal text. `app_ping`.
4. **One record.** `lib_dblib_get_record library_path=<dblib>
   table=<table> key=<a key from item 3>`: `found: true`,
   `lookup: "parameter"`, and `fields` holds every column with the values
   the database shows. A key that is not there gives `found: false`, not
   an error. `app_ping`. A `lookup` of `"scan"` means the parameterised
   query failed on this provider: the answer is still right; record the
   provider.
5. **lib_search.** `lib_search query=<the same text>`: the DbLib rows
   are in `results` with `source: "dblib"`, the DbLib is in
   `libraries_searched` and not in `not_searched`, and `dblib_searches`
   reports it searched. With `include_dblibs=false` the reply is step
   42's: the DbLib under `not_searched`, its reason naming the switch.
6. **Placement (highest risk, edits the sheet).** On the scratch sheet,
   `sch_place_dblib_component library_path=<dblib> table=<table> key=<key>
   x=1000 y=1000 rotation=90 designator=U99`: `placed: true`,
   `database_linked: true`, its library reference the symbol from item 4. The part
   is on the sheet at (1000, 1000), turned 90 degrees, with the record's
   parameters, and Update From Libraries sees it as a DbLib part. Nothing
   is left on the cursor. `app_ping`. If the call times out, look for an
   Altium dialog with `app_list_open_dialogs` before anything else.
   Uninstall the DbLib and repeat: `NOT_AVAILABLE`, sheet unchanged. A
   table the DbLib does not declare: `TABLE_UNKNOWN`, sheet unchanged.
7. **Credentials.** With a DbLib that has a password (a SQL Server login
   or a password-protected .accdb), no reply carries it:
   `connection.redacted` reads `Password=***`. Point a copy of that DbLib
   at a server that does not exist: `CONNECT_FAILED`, and neither the
   error nor anything in `libraries` quotes the connection string.
8. **A part's link through obj_query.** `obj_query eSchComponent
   properties="DatabaseTableName,DatabaseLibraryName,DesignItemId"` on the
   sheet from item 6 reads the table, the DbLib's file name and the key.
   On a part from a .SchLib the first two read empty; on any other object
   type they are listed under `properties.unreadable`.

Verified on AD 26.10.1.6 (scratch folder, Access .accdb through ACE OLE DB
12.0, a two-table sample DbLib with a .SchLib and .PcbLib made by this
server's own library tools): items 1 to 6, with `app_ping` answering after
each. ADO works from DelphiScript: the connection opened read-only and
listed every column; a search containing SQL matched nothing and did no
harm; the record was found with `lookup: "parameter"`; lib_search found
a part by its manufacturer part number in the DbLib; and the placed part
carried its database link, key and every mapped column (Value visible,
the rest hidden), at the location and rotation asked. Two findings from
the run: Altium's own DbLib layout keeps the connection in
`[DatabaseLinks]`, keys each table by its `UserWhereText`, and puts every
field mapping in a `[FieldMapN]` section (a first sample with the
connection in a `[DataSource]` section was parsed by this reader but
Altium found no connection and placed nothing); and Altium's component
count for a DbLib is not its row count (0, then 1, for five rows), so
`lib_get_installed_libraries` reports it as unknown. Item 7 is not run.
Item 8 is new in the following build.

---

## 44. Fixes from a second full board run

Results that looked right and were not, from a board taken end to end on
a Spanish Altium. On scratch documents only. New identifiers, all from
Altium's API reference or two independent reference scripts:
`SystemOptions.PolygonRepour` and `eAlwaysRepour` (documented),
`SetState_CopperPourInvalid` (PolygonReFitBO.pas), `PourIndex` and
`Poured` (PolygonBenchmark.pas and PCBObjectInspector.pas), and
`IPCB_Region.Area` (documented). Any of them undeclared stops the loop at
the first repour or polygon read, so run item 2 before relying on the rest.

1. **Exact vias.** `pcb_place_via x=1000 y=1000 size_mm=1.2
   hole_size_mm=0.6`, then `obj_query eViaObject properties="Size,HoleSize"`:
   47.244 and 23.622 mil (1.2 and 0.6 mm), not 47 and 24. `size=20
   hole_size=20` is refused with `BAD_SIZE` and places nothing.
2. **Repour.** A scratch board with a solid pour that runs past the board
   edge and a Board Outline Clearance rule. `pcb_get_polygons`: note
   `copper_area_mm2` and `copper_pieces`. Raise the rule's clearance,
   `pcb_repour_polygons`: `repoured` true, `rebuilt` 1, the item's
   `copper_area_mm2_after` below `_before`, no yes/no prompt, and
   `pcb_get_polygons` reads the new area while `area_mm2` (the outline)
   is unchanged. Shelve the polygon and repour: `shelved` 1, `repoured`
   false, nothing poured. `app_ping` after each.
3. **STEP export.** With a schematic tab focused, `proj_export_step
   output_path=<scratch>\board.step`: the board is focused, and either
   `generated` true with the file there, or `generated` false with
   `dispatched` true and nothing claimed. Record which; false means
   `PCB:ExportSTEP3D` does not take `FileName` on this build.
4. **ECO direction.** A scratch project whose schematic has one changed
   footprint. With the SCHEMATIC focused, `proj_sync_pcb`: the board is
   focused and the ECO dialog lists the PcbDoc, not the SchDoc, as the
   affected document. Press Close without executing, and confirm the
   schematic is unchanged.
5. **Replicated part.** On a sheet with a dual op-amp `U1` part A,
   `sch_replicate_component designator=U1 part_id=2`: `shared` true. Move
   both parts with the mouse: no "An item with the same key has already
   been added". Save, close and reopen the sheet: both parts are there,
   once each.
6. **Pads near a round edge.** A round scratch board with a pad turned
   329.5 degrees 0.5 mm inside the edge. `audit_find_pads_near_board_edge
   clearance_mils=118` lists it at about 19.7 mil, and `unmeasured` is 0.
7. **Outline render.** `pcb_render_svg` on the same board draws a full
   circle, with no straight edge across any quadrant.
8. **Footprint target.** In a scratch PcbLib with footprints A and B, B
   open in the editor: `lib_add_footprint_tracks footprint_name=A` puts
   the tracks in A (`lib_probe_footprint` on both), and a name that does
   not exist is refused with nothing written.
9. **3D body origin.** `lib_link_3d_model component_name=A` with no
   offset: `moved_to_footprint_origin` true, and after a save and reload
   the body sits on A's origin, not 50000 mil away.
10. **Footprint copy.** `lib_copy_footprint source_name=A new_name=A2`
    in the same library with A open in the editor: `primitives_realigned`
    above 0, and after a save and reload A2's pads read the same
    coordinates as A's (`lib_get_pad_geometry`).
11. **Copper less its holes.** On item 2's board, put the pour on a net
    (`pcb_modify_polygon net=GND`) with an unnetted via inside it and
    repour: `copper_area_mm2_after` drops by the cleared ring and
    `copper_area_exact` is true. Reads `Region.MainContour`,
    `Region.HoleCount`, `Region.Holes` and a contour's `X`/`Y`
    (Hyperlynx_Exporter.pas), new here.
12. **Pad name and shape.** `pcb_place_pad shape=rectangular name=P1`
    places a rectangular pad, and `obj_query ePadObject
    properties="Name"` reads `P1` (it read empty). `shape=square` is
    refused with `BAD_SHAPE` and places nothing.

Verified on AD 26.10.1.6 with the script of 2026-10-05, build 2, on a scratch board and
library kept out of every project: item 1 (47.2441 and 23.622 mil read
back; 20/20 refused, nothing placed); item 2 (no prompt, `app_ping`
answered, and after the pour was put on GND the repour cleared the
unnetted via, seen on screen); item 3 (`generated` false with `dispatched`
true, no dialog and no file, so `PCB:ExportSTEP3D` takes no file name on
this build); item 6 on a rectangular board, since no tool draws a round
outline (a 47.2 mil via 500 mil in at 476.38 mil, and an obround
12 x 45 mil pad turned 329.5 degrees at 9.78 mil, which is its end cap);
item 8 (FP_A got its tracks with FP_B current; an unknown name refused);
item 9 (the footprint origin read 50000,50000 mil and the body sat on it);
item 10 (A2's pads at plus and minus 1.27 mm like A's, from a fresh read
but not after a save and reload). Not run: items 4 and 5 need a person at
the editor, item 7 a round outline. Found on the way, and items 11 and 12
in the following build: `copper_area_mm2` stayed at the outline's area
because `IPCB_Region.Area` is the outer contour only; a pad's `Name`
read empty through `obj_query`; and `pcb_place_pad` turned
`shape=rectangular` into a round pad. Also open: a same-library copy
of a footprint with 4 primitives counted 6.

Items 11 and 12 verified on AD 26.10.1.6 with the script of 2026-10-06,
build 1, on the same scratch board: a GND pour over an unnetted 50 mil
via read `copper_area_mm2` 410.405 against an outline of 412.902, the
ring of a 25 mil radius plus 10 mil clearance, with `copper_area_exact`
true and the loop answering afterwards; `shape=rectangular` placed a
rectangular pad whose `Name` read `P1` and matched a `Name=P1` filter,
and `shape=square` was refused with nothing placed.

---

## 45. Lengths in millimetres, and a new document in front

Footprint and pad tools took whole mils only, so a metric land pattern
came out up to 12.7 um off its grid. `units="mm"` now carries lengths as
given. On scratch documents only.

1. **A new document is focused.** With another PcbLib in front,
   `app_create_document kind=PCBLIB` on a scratch path: `focused` true,
   and `app_get_active_document` names the new file. A path in a folder
   that does not exist is refused with `NOT_SAVED`, naming the folder.
2. **Pads in mm.** In the new library, `lib_create_footprint`, then
   `lib_add_footprint_pads units=mm` with pads at x 1.625 and -1.625,
   0.3 x 1.15: `lib_get_pad_geometry` reads 1.625 and 0.3 x 1.15 mm,
   not 1.6256 and 0.3048. A pad with no size reads 1.524 mm (60 mil).
3. **Tracks, arc and text in mm.** `lib_add_footprint_tracks units=mm`
   with a 0.12 mm width, `lib_add_footprint_arc units=mm radius=0.5` and
   `lib_add_footprint_text units=mm size=1`: each lands at the size
   given. `units=inch` is refused on every one with nothing written.
4. **Mils unchanged.** The same calls without units place what they did
   before: 60 mil pads where no size is given, whole mils exact.
5. **Board pad in mm.** On a scratch board, `pcb_place_pad units=mm
   x=12.7 y=25.4 shape=rect x_size=0.3 y_size=1.15`: `pcb_get_pad_properties`
   reads 500 and 1000 mil, 11.811 x 45.276.

Verified on AD 26.10.1.6 with the script of 2026-10-06, build 1, on a
scratch library and board kept out of every project, with a client sheet
in front at the start: item 1 (`focused` true and the new library active;
a missing folder refused, named); item 2 (pads at -1.62500056 and
1.62500056 mm, 0.2999994 x 1.15000024 mm, each within one internal unit;
the unsized pad 1.524 mm); item 3 (track, arc and text created in mm and
`inch` refused with nothing written; their sizes have no readback tool,
and they go through the same conversion as the pads); item 4 (plus and
minus 1.27 mm, 0.762 x 1.016 mm, default 1.524 mm, as before); item 5
(63.5 and 50.8 mm read 2500 and 2000 mil, 0.3 x 1.15 mm read 11.811 x
45.2756 mil, rectangular). The first attempt at item 2 came back rounded
to whole mils because the MCP server was still running the previous
Python; a server restart is part of loading this change.

## 46. A simulation runs, and a dialog press presses what it names

`sim_run` sent `Sim:RunMixedSim`, which AD 26 does not run: nothing
opened, nothing was written, and the tool reported success. It now runs
the project's simulator output, the way the schematic's Simulate menu
does, and succeeds only when a results document appears. Separately,
`app_press_dialog_button` asked for "Don't Save" on the WPF "Unsaved
Changes" prompt pressed Save. Item 3 is Python, so it needs the MCP
server restarted. On scratch documents only.

1. **A simulation runs and reads back.** Copy one sheet of Altium's
   "Simulation Quick Start" example into a scratch folder, then
   `proj_create`, `proj_add_document`, `proj_load_sheets`, activate the
   sheet and call `sim_run`: `ran` true, `results_document` names
   `Simulation\<project>.sdf`, `results_saved` true with the file on
   disk and no Save As dialog, `results` holds an Operating Point with
   v(in) about 2.52 V, and the focused document is the sheet again.
2. **No run, no success.** With a scratch PcbLib focused and no
   schematic project, `sim_run` returns `SIM_NOT_RUN` and nothing opens.
3. **Settings reach the next run.** With the item 1 project still open,
   `sim_setup_analysis transient={"stop":"2u","step":"10n"}
   outputs=["v(out)"]`: `reloaded` true and no "Reload?" prompt. Then
   `sim_run` and `sim_get_results analysis="Transient Analysis"`: the
   time wave ends at 2 us. Change it to 3u and repeat without closing
   the project: it ends at 3 us. This is the step where `DoFileLoad`,
   `SupportsReload` and `UpdateModifiedDate` first run; if the loop stops
   at it, one of them is not available to scripts.
4. **Don't Save presses Don't Save.** Close a scratch project whose
   settings file Altium changed in memory, so that "Unsaved Changes"
   lists it. Answer with `app_press_dialog_button button_caption="Don't
   Save" dialog_title="Unsaved Changes"`: `method` is `uia`, the dialog
   closes, no Save As dialog follows, and nothing is written.

Measured by hand on AD 26.10.1.6 before the change, through
`obj_run_process`: `Sim:RunMixedSim` left the scratch folder unchanged;
`WorkspaceManager:GenerateReport` with `ObjectKind=Simulator|Index=1|Action=Run`
opened `Simulation\SimOne.sdf` with an operating point (V(in) 2.524 V); an
`.nsx` netlist opened as a document and run with `ADVSIM:RunSimulator`
produced a transient and an operating point; and the old press sent Enter,
which opened "Save [project_sim_config.simcfg] As...", cancelled by hand.
Also measured: a hand-written `.simcfg` added to a project is read at
open (its transient stop time and AC and DC values were used), but a run
from a script did an operating point, DC sweep, transient and AC every
time whatever its on/off flags said; and an edit made while the project
was open was not used, raised "has been modified ... Reload?", and was
later overwritten by Altium's own copy. The handlers themselves have not
run yet.

## 47. Footprints from a package drawing

`lib_create_footprint_from_package` builds a whole footprint from a
datasheet package description: IPC-7351B lands, paste windows, silkscreen
with a pin 1 dot, assembly outline and `.Designator`, courtyard and
height, then reads the pads back and fails on any difference. It needs
two new handlers (`add_footprint_regions`, `get_mech_layers`) and four
optional fields on `add_footprint_pads`, so the script must be the new
build and the MCP server restarted. On a scratch PcbLib only, kept out of
every project. The package specs below are the datasheet cases the engine
is tested on; print one as JSON with
`python -c "import json; from tests.design._landpattern_cases import soic8; print(json.dumps(soic8().to_dict()))"`
(and `qfn16` for item 3).

1. **Layers by role, or a refusal.** `app_create_document kind=PCBLIB` on
   a scratch path, then `lib_create_footprint_from_package` with the
   SOIC-8 spec and no layer arguments. Either `layers` names the
   assembly and courtyard layers with the rule that chose them (a kind,
   or a name), or the call refuses, lists the library's mechanical
   layers, and `lib_get_footprints` shows nothing was created. If it
   refused, run `lib_set_mech_layers` with Mechanical13 named "Top
   Assembly", kind "Assembly Top", and Mechanical15 named "Top
   Courtyard", kind "Courtyard Top", and repeat: `layers.assembly.rule`
   now names the kind.
2. **SOIC-8 (TI D0008A).** The repeat of item 1: `ok` true,
   `readback.ok` true with 8 pads read. `lib_get_pad_geometry` reads pad
   1 at -2.45, 1.905 mm, every pad 1.95 x 0.6 mm, `roundrectangle` with
   `corner_pct` 50, within one internal unit, and no manual paste or mask
   expansion. In the editor: silkscreen on Top Overlay clear of every pad
   with a filled dot by pad 1, the body outline with its pin 1 chamfer
   and a `.Designator` on the assembly layer, a rectangle on the
   courtyard layer enclosing everything, and the footprint height 1.75 mm.
3. **QFN-16 with an exposed pad (TI RGT0016C).** `ok` true, 17 pads read
   back. Pad 17 reads `paste_expansion_source` manual and a negative
   value equal to its larger side. With only Top Paste visible: four
   windows inside pad 17 and no opening the size of the pad. Every other
   pad keeps its own rule-driven paste. This is the first region placed
   in a footprint; if the windows are missing after a save and reload,
   the registration did not take.
4. **Slot, non-plated hole and a mask expansion.** A custom spec with a
   slotted pad and a non-plated hole, and `drafting={"mask_expansion":
   0.05}`:
   `{"family": "custom", "name": "SLOT_TEST", "body_x": 6, "body_y": 4,
   "custom_pads": [{"number": "1", "x": -1.5, "y": 0, "width": 2.4,
   "height": 1.4, "shape": "oval", "hole": 0.8, "hole_length": 1.6},
   {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
   "shape": "circle", "hole": 1.0, "plated": false}]}`.
   `lib_get_pad_geometry`: pad 1 `hole_type` slot, `hole_mm` 0.8,
   `hole_w_mm` 1.6; pad MH `plated` false; both `mask_expansion_source`
   manual at 0.05 mm. This is where `HoleType`, `HoleWidth`, `Plated` and
   the solder mask cache fields first run; if the loop stops here, one of
   them is not available to scripts, and the reply's `property_failures`
   names a write Altium refused without stopping.
5. **The mils rounding fix.** `lib_create_standard_footprint name=R_FIX
   family=chip pin_count=2 pitch=59.055 pad_w=31.496 pad_h=39.37
   body_w=78.74 body_h=49.213`. Select a silkscreen track: its ends sit
   at x = 39.37 mil either side, not 39. There is no track readback tool,
   so this one is read in the Properties panel.
6. **Refusals that write nothing.** Repeat item 2: refused, "already in
   the library". `lib_add_footprint_regions` with a region that has no
   layer: refused before sending. With `layer=Mechanical13` and a
   triangle: `added` 1.

Not run yet: none of these handlers or writes has executed in Altium. The
EasyEDA and KiCad twins are not part of this step; the KiCad one writes a
file that its own reply parses back, and the EasyEDA one needs a live
editor to check whether its footprint canvas is Y up.

## 48. Footprint validation

`lib_audit_footprint_vs_package` audits a footprint that already exists
against its package description: pad count, numbering, pin 1 and mirror
images, pitch, span, land sizes, land over terminal, paste, mask, origin,
and from the footprint's other primitives the courtyard, silkscreen,
assembly outline and height. It needs one new handler,
`get_footprint_primitives` in Library.pas, which READS only: the
footprint's tracks, arcs, regions (bounding box and area), fills and texts
with their layers, in mm, and its height. Every property it reads is
already read in shipped code: Track `X1`, `Y1`, `X2`, `Y2`, `Width` and
Arc `XCenter`, `YCenter`, `Radius`, `StartAngle`, `EndAngle`, `LineWidth`
(GetPCBProperty, PCBGeneric.pas), `Region.Area` (RegionCopperSqMils,
PCB.pas), `BoundingRectangle` on a region (Audit.pas) and on a primitive
(PCB.pas), the text reads of Lib_GetLibraryGeometry with `Rotation`
(Lib_ProbeDesignator), and the footprint's `X`, `Y` and `Height`. Reading
does not make it risk-free: an identifier this build rejects still stops
the loop, so run `app_ping` after item 1. The script must be the new build
and the MCP server restarted. `datasheet_fetch`, `footprint_check_package`
and `footprint_render_package` are Python only. On the scratch PcbLib of
step 47 only, with its SOIC-8 and QFN-16 footprints in it.

1. **The read.** `lib_audit_footprint_vs_package footprint_name="SOIC-8
   (D)"` with the same SOIC-8 spec as step 47: `ok` true, no findings,
   `not_examined` empty, and `primitives_read` counting the silkscreen,
   assembly and courtyard tracks and the pin 1 arc, with `height_mm`
   1.75. `app_ping` answers. If the loop stops here, one of the reads
   above is not available on this build: record which footprint was open.
2. **Paste windows and their area.** The same call on QFN-16 with the
   `qfn16` spec: `ok` true, and `notes` says 4 paste windows cover about
   65.8 % of pad 17. A coverage near 0 or near 100 times too large means
   `Region.Area` is not in internal units squared on this build.
3. **Defects are seen.** `lib_copy_footprint` the SOIC-8 to SOIC-8-BAD in
   the same library. On the copy, delete the courtyard tracks with
   `lib_delete_footprint_primitives`, and draw one Top Overlay track from
   (-3, 1.905) to (-1.5, 1.905) mm, 0.2 mm wide, with
   `lib_add_footprint_tracks units=mm`. The audit of SOIC-8-BAD reports a
   `courtyard` error (no courtyard) and a `silk` error on pad 1, and
   nothing else. The original still audits clean.
4. **Height and origin.** `lib_set_footprint_height` the copy to 0: a
   `height` warning, "no height". Move every primitive of the copy 0.5 mm
   right in the editor: an `origin` warning of 0.5 mm, `ok` still true.
5. **A turned footprint.** Rotate a third copy a quarter turn in the
   editor (select all, then rotate): `ok` true and
   `alignment.rotation_deg` 90 for a counter-clockwise turn, 270 for a
   clockwise one.
6. **Python only.** `footprint_check_package` on the SOIC-8 spec: `ok`
   true and `cross_check.ok` true. With the spec's `pitch` set to 4.9: a
   `row_fits_body` error naming the pitch and body_y. `footprint_render_package`
   writes an SVG (and a PNG where Edge or Chrome is installed) showing the
   lands with dashed green terminals over them. `datasheet_fetch
   url=https://www.ti.com/lit/ds/symlink/lm358b.pdf part_number=LM358B`:
   `ok` true, `cache` miss, a `path` under the workspace's `datasheets`
   folder that opens as the datasheet, and `pages` close to its page
   count; the same call again answers `cache` hit at once.

Not run yet: the handler has not executed in Altium. The EasyEDA twin,
`easyeda_audit_footprint_vs_package`, reads pads only and is not part of
this step; it needs the same Y-direction check as step 47's EasyEDA tool.

---

## 49. Issue fixes, phase 1 (#41, #39)

Pascal changes, none of which uses an identifier shipped code does not
already use. Nineteen error paths no longer call `PostProcess` before an
`Exit` whose `Finally` calls it again (fourteen in PCB.pas,
`Lib_SetDesignator` three times, `Gen_SetSheetSize` twice), and
`Proj_Annotate` with `order="none"` no longer closes each sheet twice.
`Gen_SelectObjects` resets the property notes, selects, then reads the
selection back with a second query pass; `GetSchProperty` answers
`Selection` for that (the read `Gen_ClearHighlights` already makes).
`obj_create` and `obj_copy` refuse with `SchOnlyTypeMessage`, which lists
schematic types only. `MatchesFilterPCB` compares a length as a number
(`FilterValueMatchesPCB`: `IsLengthProperty`, `IsFloatStr`,
`StrToFloatDef`, `Abs`). `App_Ping` lists the open `Altium_API.PrjScr`
paths (`GetWorkspace`, `DM_Projects`, `DM_ProjectFullPath`, as
`Proj_GetOpenProjects` does). The script must be the new build and the
MCP server restarted. Scratch documents only.

1. **Ping.** `app_ping`: `script_projects_open` lists the script project
   Altium has open, `bundled_script_path` names the Main.pas compared,
   and on a normal install `script_location_match` is true and
   `warnings` is empty. If `script_projects_open` is empty while the
   script project is open, this build does not list script projects in
   `DM_Projects`: record it; nothing else depends on the field.
2. **Error paths close once.** On a scratch board,
   `pcb_create_design_rule name="t" rule_type="bogus"` answers
   INVALID_PARAM; then save the board. It must save without "A command
   is currently active". On a scratch sheet, `sch_set_sheet_size
   style="Q9"` answers INVALID_STYLE; `sch_set_sheet_size
   orientation="landscape"` alone works; save the sheet. On a scratch
   project, `proj_annotate order="none"` resets designators and the
   sheets save. The create-failed paths need a factory returning Nil
   and cannot be triggered on demand.
3. **Select reports what it selected.** On the scratch board,
   `obj_modify object_type=eTrackObject filter="Layer=TopLayer"
   set="NoSuchProp=1"` (reports `properties.unknown`), then
   `obj_select object_type=eTrackObject filter="Layer=TopLayer"`: the
   reply is `matched` N, `selected` N, `success` true, with no
   `properties`, and the tracks show selected. On the scratch sheet,
   `obj_select object_type=eNetLabel`: `selected` equals `matched`.
   `obj_deselect_all` afterwards. If the loop stops on the sheet select,
   reading `Selection` is not available on that object type: record it.
4. **obj_create refusal.** `obj_create object_type=eComponentBodyObject
   properties=""`: INVALID_TYPE, saying obj_create works on schematic
   objects only and naming `pcb_place_3d_body`, with no PCB type in the
   list that follows.
5. **Decimal filters.** On the scratch board, place a track whose end
   sits on a fraction of a mil, read its `X2` with `obj_query`, then
   query with `filter="X2=<that value>"`, the same with a trailing zero,
   and the same with a comma for the point: one track each time, and no
   script dialog. Repeat on a machine set to a comma decimal separator if
   one is at hand. If a dialog appears, record its exact text: the filter
   is not written into script text anywhere, so it comes from elsewhere.

Python only, same session: `pcb_create_design_rule name="t"
scope="IsPad AND HoleSize > 3"` answers BAD_SCOPE with `suggested_scope`
"IsPad AND (HoleSize > 3)" and creates nothing; with the suggested scope
the rule is created and the Rules editor does not call it incorrectly
defined (delete it after). `app_list_panels` with Properties open answers
without a KeyError. `app_open_panel name="Properties"` with it open
answers `opened` false and clicks nothing; with a panel open that
`app_list_panels` does not list, the panel is still open afterwards and
the reply carries `toggled_back`. `app_click_menu` on a Tools path ten
times: any first read that came back empty or wrong shows `retried`. A
second MCP session started on the same workspace shows up in the first
session's `app_ping` and `app_get_status` as `other_servers`, and nothing
is stopped.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
items 1 to 5 as written. The bogus rule type was refused and the board and
sheet then saved; `orientation="landscape"` alone needed the tool to pass
`orientation`, which it now does (it had required a style and never sent
one). The select replies read 3 of 3 tracks and 2 of 2 net labels, and
`X2=250.5`, `250.50` and `250,5` each found the one track with no dialog.
The scope refusal and its suggested scope behaved as written, the rule
was created and deleted again; `app_ping` listed other servers. Not run:
`proj_annotate order="none"`, the Rules editor's own verdict, the panel
and menu checks, and a comma-separator machine.

---

## 50. DRC freshness, managed placement, footprint stamp

The script must be the new build and the MCP server restarted.
Scratch documents only.

1. **DRC confirmed by a fresh report.** `PCB_RunDRC` reads the age of
   the board's `.DRC` report before and after the run. On a scratch
   board that already has a `.DRC` beside it, run
   `pcb_run_drc allow_modal=true` and CANCEL the dialog:
   `drc_confirmed` false, with the reason naming a cancelled check.
   Run it again and answer the dialog: `drc_confirmed` true and the
   report's modified time is now. If the second run reads false, check
   that Create Report File is ticked in the checker before suspecting
   the code.
2. **Managed (Workspace) placement.** Needs a signed-in Workspace and
   an open sheet that already carries one managed part, since the
   Workspace name is read off open sheets. `lib_get_vault_name`:
   `found` true and the name Altium shows. `lib_get_vault_component
   design_item_id=<a real ID>`: `placeable` true and non-empty
   `vault_guid` and `item_guid`. Then `sch_place_vault_components` with
   that ID, three placements, on the scratch sheet with
   `allow_manual_layout=true`: `placed` 3, `linked` 3, and every item
   carries non-empty `vault_guid` and `item_guid`. Save, close and
   reopen the sheet: the parts still show as managed in the Properties
   panel. Four placements onto an empty sheet WITHOUT
   `allow_manual_layout` are refused before anything is sent. With
   every managed sheet closed and no `vault`, both tools answer
   VAULT_NOT_RESOLVED. Recorded on AD 26.9.1 before this rewrite, on
   an installation with no file libraries: `LoadComponent(4,
   <Workspace>, <ID>)` returned a linked component and the link
   survived a save, while `FindComponentSymbol` said no for every kind,
   which is why the dry run asks the loader.
3. **Footprint stamp on a placed part.**
   `sch_set_components_parameters stamps=[{"designator":"R1",
   "Footprint":"<another footprint in the same library>"}]` on a
   scratch sheet: `footprints_applied` 1, and the Properties panel shows
   the new footprint with the same library. Compile: no "Missing
   Component Models" for R1. A part with no footprint model lands in
   `footprints_not_applied`.

Python only: `design_bom_file` on a `.PcbDoc` or a missing path is an
error, and a project never compiled (no `.PrjPcbStructure`) is an error
saying so; a sheet with no parts returns an empty BOM with a `warning`.
`design_review_file` refuses the same three. Under WSL,
`design_plan_from_sheet` on a Windows sheet path names the sheet by its
file, so a re-emit lands on the same sheet.

Not run yet: none of this has executed in Altium on this build.

---

## 51. Paging, read-backs, managed lookup, relaunch

The script must be the new build and the MCP server restarted. Scratch
documents only.

1. **Paged violations.** On a board with more than 200 stored
   violations, `pcb_get_clearance_violations`: `returned` 200,
   `has_more` true, `violation_count` the full number. Again with
   `offset=200`: the next page, no repeats.
2. **Truncation flags.** `proj_get_nets limit=10` on any project:
   `limit_hit` true and `count` 10. With the default limit on a small
   project: `limit_hit` false. `proj_get_bom limit=2` the same.
   `skipped_nil` 0 on a healthy project.
3. **Net-class members.** Make a class of two nets with
   `pcb_create_net_class`, then `pcb_get_net_classes`: that class lists
   both nets, `member_count` 2, `probe_errors` 0. `members=false`
   returns names only, faster. Record the time on a large board.
4. **Plane net.** On a scratch board with an internal plane,
   `pcb_set_plane_net layer=InternalPlane1 net=GND`: `applied` true,
   `route` "InternalPlaneNetName", and the Layer Stack Manager shows GND
   on that plane. `applied` false with an empty `route` = the
   documented property did not read back on this build: record it. There
   is no fallback any more (the `.13.01` merge dropped upstream's
   `LayerObject.Net` route, the identifier that faulted live), so no
   dialog may appear either way.
5. **DbLib part.** `sch_place_dblib_component` with a multi-part row
   and `part_id=2`: `part_id` 2, `part_id_applied` true, and the sheet
   shows part B. `part_id=99`: placed, `part_id_applied` false.
6. **Pad designator.** `obj_modify` a pad on a scratch board with
   `Designator=7`: the pad reads 7 under both `Name` and `Designator`.
   `Name=` (empty) is refused and the pad keeps 7.
7. **Managed name lookup.** With a sheet open that carries managed
   parts, `lib_find_vault_components name=<a comment on one of them>`:
   every part with that comment is listed once per Design Item ID, with
   a lifecycle state. A name on no open sheet: `count` 0.
8. **Poll timer.** After StartMCPServer the activity log has
   `_session_start` and then `_tick_first`. No `_arm_failed`.
9. **Ctrl+Z while attached.** With the bridge running and a scratch
   sheet focused, move a part by hand and press Ctrl+Z: the move undoes
   at once. Press it twice more, then Detach: nothing further undoes on
   detach. Repeat with the status window minimised. Never measured on
   this codebase before.
10. **Relaunch.** Stop the bridge from the status window, then run
    `eda-agent relaunch --dry-run`: it prints the X2.EXE command. Run
    `eda-agent relaunch`: the status window comes back and `app_ping`
    answers. Run it again: "already answers", nothing started. The
    ProcName form `Dispatcher.pas>StartMCPServer` is the one Altium's own
    menu files use; if the script does not start, record what Altium
    showed.

Offline only, already covered by tests: the wheel carries the EasyEDA
extension source and `eda-agent easyeda-extension path|build` finds or
builds it; CI compiles everything on Python 3.11 and installs the built
wheel; `validate_requirement` returns board-intent questions in `notes`
without failing; SECURITY.md and the templates match the repository.

Recorded on AD 26.10.1.6 with the previous script build, scratch board: item 3
(class of two nets: both listed, `member_count` 2, `probe_errors` 0, 0.04 s
either way on a board this small), item 4 (`route` "InternalPlaneNetName",
read back), item 6 (the pad reads 7 under both names; `Name=` is not
written and the pad keeps 7, reported under `properties.failed`), item 8
(`_session_start` then `_tick_first`, no `_arm_failed`) and item 10:
`eda-agent relaunch` started the loop in a running Altium whose loop had
stopped, and with the loop answering it started nothing. It cannot start
one while Altium still counts a halted script as running ("Another script
executing now"); after a script halt only a restart of Altium clears that.
Not run: items 1, 2, 5, 7 and 9.

---

## 52. Issue fixes, phase 2 (#36, #46 to #58)

The script must be the new build, deployed with `install-scripts`, and the
script project closed and reopened. Scratch documents only.

1. **Unit stamps (#47).** `app_ping`: `unit_stamps` lists ten units with
   12-character values, `stale_units` empty. Edit a comment in a deployed
   unit, deploy again, re-run StartMCPServer without closing the project,
   and ping: that unit is named in `stale_units`. Close and reopen the
   project: empty again.
2. **Layer names (#46).** On a scratch PcbLib footprint with silkscreen and
   top copper, `lib_delete_footprint_primitives layer="Top Assembly"` (or
   any name the stack does not have) is refused with the layer list, and
   the copper is untouched. A mechanical layer the stack names works.
   `pcb_place_3d_body layer=<unknown>` is refused before anything is made.
3. **Pad read (#48, #50, #58).** `lib_get_pad_geometry` on a footprint with a
   custom pad: `copper_x_mm` is where the copper is and `x_mm` the origin.
   On a slotted pad `hole_rotation` reads, and `slot_aligned` is false for a
   slot whose hole crosses its copper. `mask_expansion_state` reads manual
   on a pad with a manual value.
4. **Pad setter (#54).** `lib_set_pad_properties` on a scratch pad: `plated`
   false leaves the shape alone; `mask_expansion` 0.1 (mm) reads back with
   state manual; `hole_rotation` 90 on a slot reads back 90. Save, reopen:
   the values held.
5. **Footprint vias (#55).** A footprint with a via: `lib_get_footprint_primitives`
   lists it with its diameter and hole.
6. **Save one document (#56).** Edit a scratch sheet while another open sheet
   sits in a read-only folder. `app_save_document` on the scratch sheet:
   `saved` true, and the read-only one is untouched with no dialog.
7. **Opacity (#57).** `obj_modify` an `eComponentBodyObject` with
   `BodyOpacity3D=0.5`: the Properties panel shows 50%, and it reads back
   0.5. `BodyOpacity3D=50` is refused.
8. **Mirror (#36).** Mirror a part whose body has diagonal lines (an op-amp
   triangle) and one with an arc bubble: the triangle points the other way
   and the bubble moves to the other tip, as mirroring by hand does.
   `primitives_skipped` is 0.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
item 1 (ten 12-character stamps; a comment added to the deployed Utils.pas
named Utils in `stale_units` with the reload advice), item 2 (both
refusals, nothing created or removed), items 3 and 4 (a slot across tall
copper read `slot_aligned` false, and true once `hole_rotation` 90 turned
it along the copper; mask 0.1 mm read back manual; plated false left the
shape; all held after save and a restart of Altium), and item 6 in part:
`saved` true for the one document. `was_modified` read false on a board
changed only by script, and the file was written all the same. Not run:
item 5 (no tool adds a via to a footprint), item 7 (needs a STEP model),
item 8, and the read-only half of item 6.

The three left waiting on a live measurement, the pad cache's separate
top and bottom mask fields (#59, #61), the via mask write (#60) and text
that could not be selected after an X/Y move (#39.14), were measured and
fixed in step 54. Item 7 ran there too, with a real STEP model: 0.5 read
back and 50 was refused. Item 8 ran there, and found arcs were not moved
at all.

---

## 53. Footprint delete edits from another footprint (#42, #45, #39.16)

The current footprint's primitives live on the library board, which is
written back into the footprint whenever another footprint becomes
current. Measured on AD 26.10.1.6 on a scratch PcbLib:
`lib_delete_footprint_primitives` on the footprint on screen reported
`removed` 6 and all six were back after switching away and back;
`Board.RemovePCBObject` removed nothing from a library board, whether the
primitive came from a list, a board iterator, or was first removed from
the footprint. A remove while another footprint was current lasted
through switches, save and reopen, with the primitive freed, for 25
cycles each on primitives made in the session and on primitives loaded
from disk.

The delete now makes another footprint current when the target is on
screen (a temporary one in a single-footprint library, removed again),
removes each match while its iterator is still open, and shows the
target again after a temporary holder. `was_current` reports the switch.

MERGED BEHAVIOUR (2026.10.13.02). Upstream parked always and freed each
removed primitive; the merge keeps carrier-local's park as opt-in
(`park_current`, which `lib_delete_footprint_primitives` sends by default
unless `remove_from_board` is set), parks on another footprint first
(`parked_elsewhere`) and on upstream's temporary holder only in a
single-footprint library, and does not free what it removes. After a park
on another footprint that footprint stays on screen; after a temporary
holder the target is shown again and the holder removed.

1. **Delete on screen.** With a footprint on screen holding tracks on a
   mechanical layer, `lib_delete_footprint_primitives layer=<that
   layer> confirm=true`: `removed` N, `was_current` true,
   `parked_elsewhere` true (another footprint is on screen afterwards).
   Switch to another footprint and back: the tracks stay gone. A second
   run removes 0.
2. **Single-footprint library.** The same in a library of one footprint:
   no extra footprint is left in the library, before or after a save.
3. **Off screen.** The same on a footprint that is not on screen:
   `was_current` false and the footprint on screen does not change.

Recorded on AD 26.10.1.6 with the previous script build: items 1 to 3, ten
cycles each, then save and a restart of Altium: the tracks stayed gone and
the library held only its own footprints. Board deletes (`obj_delete`,
`pcb_delete_object`) measured the same way on a scratch board, 25 cycles:
every delete held through save and reopen and sentinel tracks were
untouched; they are unchanged and do not free what they remove.


---

## 54. Library cleanups, footprint adds listed once, mask sides, and five field-report fixes

New: `lib_move_bodies_to_layer` and `lib_redraw_assembly` (#43). The rest
are fixes found by live measurement on AD 26.10.1.6:

* a footprint add left the footprint on screen listing the new primitive
  twice until the next footprint switch; every footprint writer now makes
  the same footprint current again afterwards;
* `obj_modify` X/Y on a board text, and `pcb_place_3d_body`, moved the
  object outside its modify bracket, so the spatial index kept the old
  place and the mouse could not pick it (#39.14);
* `pcb_set_via_soldermask_relief` refused; it writes through the via's pad
  cache now, since only the direct `SolderMaskExpansion` assignment faults
  (#60), and pad and via mask reads report the bottom side and the
  separate flag (#59, #61);
* a Board Outline Clearance rule's descriptor says `Gap=0mil` whatever it
  holds; the rule readers add `gap_mm` from the rule itself (#39.7);
* the EasyEDA importer read a region's SVG path as a point list and drew
  per-pad lead outlines as silkscreen (#39.22);
* `sch_mirror_component` never moved an arc, because a local `EArc` hid the
  `eArc` kind (names are case-insensitive), and skipped the designator.
  `lint.py` now flags a local that hides an enum constant.

1. **Bodies to one layer.** In a PcbLib with bodies on different
   mechanical layers, `lib_move_bodies_to_layer layer=Mechanical13`:
   `moved` counts each body not already there, and after save and
   reopen every body is on Mechanical13. `layer=TopLayer` is refused; a
   mechanical layer the library has not enabled is accepted with
   `layer_enabled` false and a `warning`.
2. **Assembly redraw.** `lib_redraw_assembly layer=Mechanical13
   confirm=true` on footprints with a 3D body: a rectangle the size of the
   body, `.Designator` centred in it. Run it twice: `cleared` is 5 per
   footprint the second time and one drawing remains. A body on that
   layer is not cleared. Without `confirm` it is refused.
3. **One footprint in the library.** The same redraw in a single-footprint
   library leaves no extra footprint behind.
4. **Adds listed once.** Add a track to the footprint on screen:
   `lib_get_footprint_primitives` lists it once, and so does Altium's own
   iterator.
5. **Picking a moved text.** `obj_modify eTextObject X=..|Y=..` on a board
   text, then click it at its new place in the editor: it selects.
6. **Via relief.** `pcb_set_via_soldermask_relief expansion_mils=3` on a
   board with vias: `vias_verified` equals `vias_matched`, Altium stays up,
   and each via's mask opening is 3 mil from the hole edge after save and
   reopen.
7. **Mask sides.** `lib_set_pad_properties mask_expansion=0.2
   mask_bottom_expansion=0.05 units=mm`: `lib_get_pad_geometry` reads
   0.2 / 0.05 with `mask_separate` true.
8. **Outline clearance gap.** A Board Outline Clearance rule set to 3 mm:
   `pcb_get_rule_properties` reads `gap_mm` 3 while the descriptor still
   says 0.
9. **Mirror an arc.** Mirror a placed part with an arc: the arc's centre
   reflects, a half arc's opening turns the other way,
   `primitives_skipped` is 0.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
items 1 to 4 and 6 to 9. Item 1: two bodies moved and kept their layer
through save and a restart of Altium, the footprint on screen and one off
screen alike. Item 2: a 0603 body gave a 1.6 x 0.8 mm outline, three runs
left one outline and one text each, through save and restart. Item 4:
Altium's iterator read 11 entries for 11 objects after the add (12 for 11
before the fix); pads, pads in bulk, tracks, tracks in bulk, an arc, text,
a region and a 3D body added this way all survived save and restart.
Item 5 was measured with a spatial iterator rather than the mouse: the
text and a placed body were found at their new places and not at the
old. Item 6: two vias, 3 mil both sides from the hole edge, kept through
save and restart. Item 8: 3 mm and then 2.5 mm read back. Item 9: the
arc bubble moved from 210 to -210 mil about the part and back, and a
90..270 degree arc became 270..90. Item 5 by mouse is not run.

---

## Still open, and not blocking

* **The empty-model judge (D-S2-1's real fix).** `lib_link_3d_model` judges an empty model only AFTER the body is added to the footprint and the library board, because no code has ever read an UNOWNED body (a SETTER on one crashed ADVPCB.DLL in `PCB_Place3DBody`). The undo can then only report what stays listed (P7). Judging first needs a read-only `Body3D_ReadRect` / `OverallHeight` on the unowned body, proven by its own FATAL-IF-WRONG probe (last, one per session, human present) before any handler relies on it.

* **#23** font size: the importer places symbol text but not its height.
  Altium's font size is not in mils and the conversion is undocumented,
  so the source range is reported rather than guessed. Calibrating it
  needs a live measurement.
* **#39**, three items that need a live session before any change:
  a `>` or `<` in a `pcb_create_design_rule` scope raised a parse modal
  and left a rule Altium later called incorrectly defined; Board Outline
  Clearance rules read `Gap` 0 while the real value sits in a per-object
  matrix no documented property reaches; and 3D bodies deleted with
  `lib_delete_footprint_primitives` came back on the next save. The
  first is now refused before sending when a comparison sits unbracketed
  next to And, Or, Xor or Not (step 49), and the last is fixed by step 53.
  The Gap reading came from the descriptor, which says 0 whatever the rule
  holds; the readers now add `gap_mm` from the rule (step 54). The
  per-object table itself is still not exposed to scripts.


## Generic property rejection on Altium 21 (live verified)

The same reads stopped polling on Altium 26.10.1.6 before this change:
`Text` on a parameter set, `IsHidden` on a net label, and `IsHidden` on a
wire. `IsHidden` is now accepted only on pins, parameters, designators, sheet
names and sheet file names, the types whose interface declares it; `Text` is
refused on the types whose interface has none, wires among them. Repeat
steps 3 to 7 on AD 26 with the wire included.

Verified on AD 26.10.1.6 with the script from commit 57760ac: the three reads return
empty fields listed under `properties.unreadable`, `IsHidden=true` on a wire
is refused under `properties.unknown`, `IsHidden` reads and writes on
parameters, and the bridge answered `app_ping` after each call.

Recorded on Altium 21.4.1.30: `IsHidden` on `eNetLabel` and `Text` on
`eParameterSet` opened an undeclared-identifier dialog and stopped polling.
The fix guards reads and writes before accessing those members. It does not
claim a complete property capability table for every Altium version.

Unsupported query fields remain empty and are listed in
`properties.unreadable`; other requested fields remain available. Unsupported
writes are listed in `properties.unknown` and are not reported as applied.
Single creation returns `UNSUPPORTED_PROPERTY` with the property and object
type in the error message. Batch creation rejects only that item, without
registering it, and includes `reason: "UNSUPPORTED_PROPERTY"`, `property`,
`object_type`, and the zero-based `index` in `failures`. Valid items continue.
This preflight covers the guarded Text, IsHidden and TextColor combinations, not every
unknown property. Existing inputs and successful response fields are unchanged.

Use visible net labels with `Text`; do not hide them with `IsHidden`. Query a
parameter-set directive's coordinates without `Text`. The bridge does not
substitute a different field for the rejected property.

Live verification of commit `89c017f` on Altium 21.4.1.30 passed on
2026-09-29 after a full Altium restart. The deployed Generic.pas SHA-256 was
`36f9c0dde95de366a1b0ea60bc653793ed6f915c1c7714d08ee20bb791aef57e`.

- Unsupported reads returned empty fields plus unreadable diagnostics, while
  coordinates and the directive Name remained readable.
- Single writes returned success=false with unknown-property diagnostics;
  batch writes reported both rejected properties in their diagnostic list.
- Both single creates failed without changing object counts. The mixed batch
  returned created=2, failed=2, total=4, with failures at indexes 0 and 2 and
  the expected property/type. Queries confirmed only the valid objects existed.
- Ordinary parameter IsHidden read back false, true, then false after restore.
- Pings and valid queries succeeded after every rejecting call. No error modal
  or polling restart was needed. The recorded response assertions passed.
- Free Pascal execution remains separately pending: local FPC is absent and
  the upstream GitHub run is action_required with no jobs executed.

For future acceptance runs, close unrelated design documents first or retain
byte-for-byte backups. The bridge's save-all operation rewrites open clean
schematics too; it must not be treated as a no-op just because app_context
reports no unsaved documents.

Repeatable acceptance, on a disposable schematic only:

1. Save normal work, install the candidate scripts, restart Altium (scripts
   are cached), start the bridge, and confirm `app_ping` succeeds.
2. Create an ordinary visible net label and parameter-set directive. Record
   their count and positions. Query valid label Text and directive coordinates.
3. Query `IsHidden` on the label and `Text` on the directive, each alongside
   coordinates. Expect empty unsupported fields, unreadable diagnostics, and
   unchanged valid coordinates. Neither call may open a modal or stop polling.
   Do the same for `IsHidden` and `Text` on a wire.
4. Attempt those writes with `obj_modify` and `obj_batch_modify`. Expect
   failure diagnostics, unchanged objects, and no modal. These operations
   preflight the complete assignment list for known unsupported Text,
   IsHidden and TextColor pairs before writing any other field. Unknown
   names outside that preflight retain the existing partial-write semantics.
5. Attempt each unsupported pair through `obj_create` and `obj_batch_create`.
   Verify failed objects are absent. In a batch containing invalid, valid,
   invalid, and valid items, expect two creations, two indexed failures and
   no property diagnostic leaking from one item into another.
6. Exercise IsHidden on an ordinary parameter supporting visibility and
   confirm read/write behaviour is unchanged. Restore its original value.
7. After each rejection, run `app_ping` and a valid query. Both must complete
   without dismissing an error or restarting the polling loop.

Record exact Altium and bridge versions, responses, and object counts. Offline
source guards and Free Pascal tests do not establish live Altium acceptance.

## TextColor property rejection

Recorded on AD 21.4.1.30 with script 2026-10-01 revision 1 on 2026-10-03:
`obj_query` on an existing `ePowerObject` with `Text,Color,TextColor` raised
`Undeclared identifier: TextColor` at the generic getter and stopped polling.
The setter had the same unguarded access. See issue #37.

The script from this change allows TextColor only on ports, sheet entries
and harness entries and uses their typed interfaces. Altium's [schematic API
reference](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference)
documents those owners; power objects use Color. The bridge never substitutes
Color for a rejected TextColor request.

Queries retain other fields and report unsupported TextColor under
`properties.unreadable`, including project scope. Filters using a known
unsupported property return no match before reading it; an empty expected
value must not turn an unreadable property into a match. Modifications reject
the complete assignment list before applying a positional or other write.
Creation retains the existing single error and indexed batch-failure formats.

Repeatable live acceptance on disposable documents:

1. Reload the script project and verify ping reports the version at the top
   of this document. Create a
   power object, a net label, a port, and a sheet symbol with a sheet entry.
2. Query TextColor alongside valid fields on the power object and net label.
   Expect empty TextColor fields with unreadable diagnostics. Repeat with
   document and disposable-project scopes.
3. Filter the power object with `TextColor=` and `TextColor=128`. Expect no
   matches, including a delete using that filter; verify the object remains.
4. Attempt `Color=123|Location.X=100|TextColor=128` through single and batch
   modification. Expect rejection diagnostics and unchanged Color/position.
   Include a valid independent batch item and verify it completes.
5. Attempt single creation and a mixed batch containing two unsupported
   power-object items and two valid items. Expect no invalid objects, two
   indexed failures, and exactly two valid creations.
6. Read/write/read TextColor on a port and sheet entry. Save ONLY the scratch
   document, close/reopen it, and verify persistence. Harness-entry support
   must be checked on a fixture that actually contains one; it is not exposed
   by the current generic object-type mapping.
7. Ping and perform a valid query after each rejection. No error dialog,
   debugger stop, or polling restart may be needed. Restore editor focus and
   close the disposable documents at the end.

Live results for this candidate are recorded below once performed.
