# Release verification: 2026.10.11.03

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
| 24, footprint keep-out | `IsKeepout` | no, nowhere | high, and it can stop the loop |
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

Expect `altium_script_version` = `2026.10.11.03`, `version_match` =
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
  `LayerObj.Net` at all (since the first 2026-10-11 build): after its refusals it answers
  `success: false`, `applied: false`, an empty `net_readback` and a note
  pointing at the Layer Stack Manager, writes nothing and raises no
  dialog. Tying a plane through the split-plane objects is unverified and
  not attempted.
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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`.
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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`
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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`,
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

## 38. A dirty mark that says whether it took, and a save-all that writes what the bridge edited (run live 2026-10-11 on the first 2026-10-11 build, results at the end; re-check on 2026.10.11.03)

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
unchanged into 2026.10.11.03; results at the end of this section; the fixes, each with an executed test that fails on the
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
expectations, unchanged on 2026.10.11.03 (its re-check on the second
build never started: the loop was dead, see the section 39 results).
SCRIPT_VERSION is `2026.10.11.03`. Use scratch copies only:
a scratch sheet opened as a free document, a scratch PROJECT copy with at
least one sheet, a byte copy of one of that project's sheets saved under the
SAME FILE NAME in another folder (opened as a free document), and a byte copy
of a .PcbLib opened on its own.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.11.03`,
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
   is the safety net. On 2026.10.11.03 a false here comes with `flag_raw`
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
   record path is then unreachable in practice. ON 2026.10.11.03 THAT IS
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
   2026.10.11.03, with the edit reading `marked_modified: true`, the walk
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
      copy's path, `mark.found: true`. `app_save_all` reply (or
      `bridge_record_pending` before it): no project path on
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
    on 2026.10.11.03 when the mark read true; `false` only when it did
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
    FALSE (as step 4; on 2026.10.11.03 a mark is expected to read true, so
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

| Step | Verdict | What happened | On 2026.10.11.03 |
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
scratch and carrier folders were unchanged. Run it on 2026.10.11.03.

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

Re-check on 2026.10.11.03 (after the steps above):

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

---

## 39. Popup-free run (run live 2026-10-11 on the first and second 2026-10-11 builds, results at the end; re-check on 2026.10.11.03)

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
results, P8). 2026.10.11.03 reads no member of that reference anywhere,
and none of the IPCB_Rule constraint read in `PCB_NormalizeVias` (P23). For
every site still counted below, "0 catches" means only "no ordinary
raise": a dead loop is the undeclared-identifier answer.

**So the editor's own selection is never a target any more.** Every
symbol edit (and the nameless pin readers) acts on the symbol HELD from
`lib_create_symbol` / `lib_set_current_component`, or on the one its
`component_name` names, and answers `NO_COMPONENT` otherwise. Sections 2,
7 and 9 hold a symbol first for that reason; a `NO_COMPONENT` from a
nameless call with nothing held is the intended answer, not a FAIL.

**Reviewed before deploy (same version, 2026.10.11.03).** A review of the
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

1. **No schematic server.** Restart Altium, open only a PcbDoc (no SchDoc
   or SchLib this session), focus the Home page. `app_get_active_document`
   answers (the board, or `{}`); `app_get_preferences` answers
   `"schematic": null`; `app_exit_active_command` answers
   `sch_post_process: false`.
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
in the session, with a human present to stop the Script IDE and rerun
StartMCPServer (39.5).

| Probe | Site (`where`) | Setup and call | What settles it |
|---|---|---|---|
| P1 | `App_ExitActiveCommand:PCBServer.PostProcess` | Restart Altium, open ONLY a SchLib, `app_exit_active_command` | 0 = `PCBServer` answers with no board open; 1 = it raises and the loop now stops after the first (it used to raise 8 times); a stopped loop or `where` = `dispatcher` = an undeclared identifier, which no `Try` catches |
| P1b | same | Open a PcbLib, close it, `app_exit_active_command` | `pcb_post_process: true` confirms a closed library leaves the server registered (why this site is NOT gated on an open PCB document) |
| P2 | `dispatcher` (in `App_CreateDocument`) | `app_create_document kind=NOTAKIND file_path=<scratch>\x.dat add_to_project=false` | `CREATE_FAILED` = `OpenNewDocument` answers Nil, nothing to do; `INTERNAL_ERROR` + a dialog = it raises, and unknown kinds need a refusal |
| P3 | `GetPCBBoardAnywhere:OwnerDocument` | 39.3 item 4 | 0 = the Nil test was enough; 1 = reading `OwnerDocument` itself raises on a non-document view |
| P4 | `MechLayerFromNumber:LayerUtils.MechanicalLayer` | Any tool given the layer `Mechanical21` on a board or library (e.g. `pcb_get_mech_layer_names` on one using mechanical 17+), and `pcb_set_layer_visibility layer=Mechanical21` | 0 expected on AD26; on a pre-AD19 build, record the result (an undeclared `LayerUtils` would show as `dispatcher` or a stopped loop). 2026.10.11.03: the visibility reply echoes `"layer":"Mechanical21"` (the first 2026-10-11 build echoed `Unknown`; `GetLayerString` now names Mechanical17-64 by a verified `MechLayerFromNumber` lookup, only on AD19 and later) |
| P5 | `ReadMechKind:Kind` | Only on a build below AD19: `pcb_get_mech_layer_names` | kinds read -1 and nothing is counted (the read is gated off below 19) |
| P6 | `App_CreateDocument:DoFileSave`, `App_CreateDocument:SaveObject` | 39.3 item 5, and the same with a folder that exists but is read-only | 0 confirms the folder test is the whole story; a count means a save raises in an existing folder |
| P6b | `ResolvePCBBoard:OpenDocument`, `GetPCBBoardAnywhere:OpenDocument` | FIRST the positive controls in 39.3 item 2 (a closed HEALTHY board must still open). Then a 0-byte file renamed `scratch.PcbDoc`: pass it as `pcb_place_components` `board_path`, then list it in a scratch project and run `app_get_preferences` with a sheet focused | THREE possible outcomes for a broken file, not two: 0 = Altium answers Nil; 1 = it raises (counted); and the THIRD, the one measured on the .02b run: an UNCOUNTED Altium modal ("Warning", "File type not recognised.", class (c)) blocking ~13 s, then a NON-Nil BLANK document - `pcb_place_components` placed its part into it and `app_get_preferences` reported its settings. On 2026.10.11.03 neither call reaches the open: `pcb_place_components` (and `pcb_focus_board`) are refused `BOARD_FILE_UNREADABLE` (the message names the path and says "Nothing was opened or changed"), and `app_get_preferences` answers `"pcb": null` (or another open board), with no dialog, 0 catches, and `scratch.PcbDoc` still closed and still 0 bytes. A box = the pre-open file test (`FileLooksLoadable`) let it through: record the file's size and first bytes |
| P7 | `Lib_Link3DModel:ModelFactory_FromFilename`, `PCB_Place3DBody:ModelFactory_FromFilename`, `PCB_Place3DBody:RemovePCBObject` | `pcb_place_3d_body` and `lib_link_3d_model` with a `.wrl`, an `.igs` and an `.stl` model, then with a renamed `.dat` file; count the bodies on the board (and on the footprint) before and after each call | `Model3DExtMayLoad` is a DENY-list (only no extension, `.txt`, `.pdf`, `.csv`, `.json`, `.png`, `.jpg` are refused before the call), so each of these reaches the loader exactly as before. MEASURED on the .02b run with placeholder stubs (NOT real files of those formats): the loader answered NON-Nil with an EMPTY model, 0 catches; `pcb_place_3d_body` said success with `bbox` null and `overall_height_mils` 0 and left one empty body per call; `lib_link_3d_model` said `ok` false but left the body too; a renamed STEP `.dat` LOADED (Altium reads the content). On 2026.10.11.03 an empty model (no readable box AND an overall height of 0) is `MODEL_LOAD_FAILED` "loaded as an empty model", and the body is taken off again inside the same transaction (board: `PCB_DeleteObject`'s broadcast + `RemovePCBObject`, its catch counted at `PCB_Place3DBody:RemovePCBObject`; footprint: `RemoveLibBodyByAddress`, the footprint half only), so the board's body count is unchanged and the footprint's is unchanged IN MEMORY; the renamed STEP and a real `.step` still place. THE LIBRARY HALF IS IN MEMORY ONLY: the body was also registered with the library board, which the footprint removal leaves alone, and a body created in the session and removed from its current footprint only was measured to come back on save (2026-10-04). The `lib_link_3d_model` message says so: "taken off the footprint IN MEMORY only", `board_still_lists=<n>` (or `null` when the footprint was not current), and "Do NOT save this library: run lib_reload_library". RECORD `board_still_lists`. Then MEASURE THE FILE, never memory: `lib_save_library` on a scratch copy, `lib_read_pcblib_file` on that footprint and count its bodies IN THE FILE; then `lib_reload_library` and count again. A body in the file after the save = the board copy persisted (with `board_still_lists` >= 1 that names the cause): the empty model must then be judged before `PcbLib.Board.AddPCBObject`, which needs a proven read of `OverallHeight` on an unowned body. Do NOT add an extension to the deny-list: the content decides |

The rows below are the per-file stages' sites (Library, Generic, PCB,
Project), filled in at integration. A `<prop>` / `<id>` in a `where` is
the property name and the schematic `ObjectId` the row carries, so the
row itself names the pair that popped.

| Probe | Site (`where`) | Setup and call | What settles it |
|---|---|---|---|
| P8 | (none: nothing is read off the editor's selection any more) | **FATAL-IF-WRONG; first in a session of its own, after every other section.** Restart Altium; open a SchLib by hand (a symbol is selected in the editor; nothing created through the bridge). WITHOUT component_name: `lib_add_symbol_lines lines=[{x1:0,y1:0,x2:100,y2:0}]`, `lib_add_pins` (two pins), `lib_add_symbol_rectangle`, `lib_get_pin_list`, `lib_split_pin_functions`, `obj_create object_type=eLine container=component`, `lib_link_footprint footprint_name=X` | Every one answers `NO_COMPONENT` (the edits' message names component_name and lib_set_current_component), no dialog, no count, the library file unchanged and not marked modified. On the second 2026-10-11 build the first call halted the loop ("Undeclared identifier: CurrentPartID", 39 results). A box now is class (b): stop the run and record the unit and line the Script IDE shows |
| P9 | (none) | Same session, after P8. `lib_add_pins component_name=<the selected symbol>` with two pins, one carrying `owner_part_id=0`; then `lib_add_symbol_lines` and `lib_get_pin_list` WITHOUT a name; then `obj_batch_create component_name=<another symbol> operations=[two eLine items with container=component]` | The pins land on that symbol (visible in the editor without reselecting it: `MarkLibDirtyComp` redraws the component the edit wrote into), the symbol is now current, the explicit pin reads `owner_part_id` 0 and the other the symbol's CURRENT part, its `CurrentPartID` (1 on a single-part symbol, never 0: a symbol that reported part 0 is moved to part 1, display mode 0, as `lib_set_current_component` does; on a multi-part symbol `CurrentPartID` can differ from the part the editor's spinner shows, so check the owner part, not the picture); the nameless calls act on the same symbol (it is now held). The batch answers `created: 2` on the other symbol (it resolves through an `ISch_Lib` local: the first .03 draft passed its `ISch_Document`), the library reads modified afterwards and the two lines are drawn without reselecting the symbol (`MarkLibDirtyComp`, as every `Lib_Add*` handler; before the review the component-container create paths flagged nothing). `obj_create object_type=eLine container=component component_name=<another symbol>` the same. No dialog, no count. `NO_COMPONENT` for a name that exists = the index and the walk both missed it (record whether `lib_set_current_component` finds it) |
| P10 | (none: the held component's liveness) | `lib_create_symbol X`, then close and reopen that SchLib in the UI, then `lib_add_pins` without a name; then a by-name edit of ANOTHER symbol in that library: `lib_set_component_description component_name=<another symbol> description=p10`, and `lib_batch_set_params` on it | `NO_COMPONENT` for the nameless add, no dialog, no count: the held component was dropped when its library closed (`LastCreatedLibIsLive`), and the editor's selection is no longer a fallback. The by-name edits succeed with no dialog whatever the liveness answer: `MarkLibDirty` touches no component (the first .03 draft invalidated the held one on every edit). **FATAL-IF-WRONG**: a dialog on the nameless add = `Client.GetDocumentByPath` hands back the same interface after a reopen, so the freed held component was used (record which 39.5 class, and whether two lookups return the same object) |
| P11 | `ApplyLabelFormat:write` | Scratch SchLib: `lib_set_label_formats ops=[{target: designator, orientation: 90}]`, then `[{target: designator, justification: 12}]` | 1 = an out-of-range ordinal raises, and both fields need a range refusal (0..3, 0..8); 0 = check with `lib_audit_styles` whether the value was stored as given |
| P12 | `Lib_InstallLibrary:InstallLibrary`, `Lib_UninstallLibrary:UnInstallLibrary` | Install and uninstall a real scratch `.SchLib`; then install a RELATIVE name that does not exist; then uninstall a name that is installed under another spelling of its path | 0 for the real file; the relative and respelled cases are still asked and show whether either call raises (the absolute-missing and never-installed cases are refused before the call) |
| P13 | `Lib_MoveFootprints:GetPCBLibraryByPath`, `Lib_CopyFootprint:GetPCBLibraryByPath` | `lib_copy_footprint` and `lib_move_footprints` into a destination `.PcbLib` that exists on disk but is NOT open | 0 = the lookup answers Nil for a closed library and the load route runs; 1 = it raises |
| P14 | (none: the reader's source) | Save a SchLib, add a symbol through the bridge WITHOUT saving, then `lib_get_component_details` on it by index | The new symbol is absent = the component reader reads the FILE, as the `LibFileMissing` guards assume; present = it reads the open document, and those guards would lose components of an unsaved library |
| P15 | `GetSchProperty:<prop>:<id>`, `SetSchProperty:<prop>:<id>`, `GetSchVertexProperty:VerticesCount:<id>` | Scratch sheet with one object of each type: `obj_query` and `obj_modify` for Name, FontId, LineWidth, Style, IOType, Alignment, TextColor, Justification, Width, IsSolid, AreaColor, Orientation; and LibReference, XSize, Electrical, VerticesCount on eLine, eBusEntry, eProbe, eParameterSet, eCompileMask | Each row names a pair to add to `SchPropAbsentOn` (or `SchObjectLacksVertexArray`); no rows = the denylists are complete for these names |
| P16 | `Gen_HighlightNet:Selection:<id>`, `Gen_ClearHighlights:Selection:<id>`, `SetSchProperty:Selection:<id>` | `obj_select` on ePin, eSheetEntry and eParameter; `obj_highlight_net` on a net that reaches a sheet entry; `obj_clear_highlights` on a sheet with parts | A row names a type without `Selection`: skip it in the loops (and in `Gen_CopyObjects`, which sets Selection with no Try) |
| P17 | `SetSchProperty:IsMirrored:<id>` | `obj_modify set='IsMirrored=false'` on eLabel, eNetLabel, eParameter, eNoERC, eLine, eImage, eTextFrame | A row adds that type to `SchObjectLacksIsMirrored` |
| P18 | `Gen_WriteProjectFile:DM_ProjectFullPath`, `Gen_AddSheet:DM_ProjectFullPath`, `Gen_DeleteSheet:DM_ProjectFullPath`, `Gen_DeleteSheet:FocusedProjectFullPath` | With ONLY free documents open: `proj_add_sheet`, `proj_delete_sheet` with no `project_path`, and a sheet delete that refocuses | 0 = fault-only (expected). If `proj_add_sheet` builds its path from the text `Free Documents`, that is a separate correctness bug |
| P19 | `Gen_GetConstraintGroups:DM_ConstraintGroupCount`, `Gen_GetConstraintGroups:DM_ConstraintGroups`, `Gen_GetConstraintGroups:DM_ConstraintCount`, `Gen_GetConstraintGroups:DM_Constraints`, `Gen_GetConstraintGroups:DM_Kind`, `Gen_GetConstraintGroups:DM_Data`, `Gen_GetConstraintGroups:DM_TargetKindString`, `Gen_GetConstraintGroups:DM_TargetId` | `sch_get_constraint_groups` on a sheet, then on a sheet that has constraint groups | No dialog (it used to raise on EVERY call: an ISch_Document has no DM_ members). On the second sheet the group and constraint reads had NEVER run before this build: 0 = they answer; each row names a member `IConstraintGroup` / `IConstraint` lacks (one dialog per group or constraint), to drop from the read |
| P20 | `Gen_PlaceHarnessConnector:HarnessType` | `sch_place_harness_connector` with `harness_type` set | 1 = the member does not exist; drop the write |
| P21 | `Gen_PlaceCrossSheetConnector:Side` | `sch_place_cross_sheet_connector` with `side=left`, then `side=right` | 1 = the member does not exist; drop the write |
| P22 | `ProcessSchDocObjects:owner-remove` | `obj_delete object_type=eSheetEntry` with a filter matching one entry on a hierarchical sheet | 0 and the entry gone = removal through the owning symbol works; 1 = the owner refused and the document-level fallback ran |
| P23 | (none: the read is no longer counted) | **FATAL-IF-WRONG (one unproven identifier left: `Board.FindDominantRuleForObject`, unchanged since before 2026-10-11 and never yet run).** First, zero-risk: `pcb_get_rule_properties name=RoutingVias` and record `rule_kind` and `descriptor`. Then `pcb_normalize_vias` on a scratch board with one free via and only the default RoutingVias rule; then on a board whose only vias are inside a component | Record the reply. Every rule is now asked only `Name` and `Descriptor` (both proven), the values are parsed from the descriptor (`PreferedWidth=` / `PreferedHoleWidth=`, mil or mm), and the kind is the LITERAL 11 (the second 2026-10-11 build read `PreferedWidth` off the dominant `IPCB_Rule`; the first .03 draft read it through an `IPCB_RoutingViaStyleRule` iterator local, with `eRule_RoutingViaStyle` - all never run, for every rule on every board). Numeric descriptor and `rule_kind` 11: `free: 1`, `changed: 1`, `via_rule_values` >= 1, `no_rule_values: 0`, the via at the rule's preferred size and hole. Template descriptor ("Templates Used To Check Via: ..."): `via_rule_values: 0`, `no_rule_values: 1`, `changed: 0` - not a defect, it records that the values must come from elsewhere. `rule_kind` not 11 with `via_rule_values` >= 1 and `no_rule_values: 1` = the literal is wrong (record the number). The component-only board: `free: 0`, nothing read, nothing changed, the board NOT marked modified (it is marked only when `changed` > 0). ORDER MATTERS: the zero-risk `pcb_get_rule_properties` step first, so the ordinal is known before the unproven call runs; the dominant-rule lookup is made only when some rule gave values (a template-form board never makes it), and it runs in a read-only pass BEFORE `PCBServer.PreProcess`, so a halt there leaves no PreProcess open. A box = class (b) on `FindDominantRuleForObject`; it CANNOT tell an unknown identifier from a refused Integer kind argument (the 11 is passed in an Integer variable; Integer-for-enum precedent exists only for other parameters), and either one means the call must change - record the line the Script IDE shows |
| P24 | `PCB_PlaceEmbeddedBoard:XLocation`, `PCB_PlaceEmbeddedBoard:YLocation`, `PCB_PlaceEmbeddedBoard:DocumentPath`, `PCB_PlaceEmbeddedBoard:RowCount`, `PCB_PlaceEmbeddedBoard:ColCount`, `PCB_PlaceEmbeddedBoard:RowSpacing`, `PCB_PlaceEmbeddedBoard:ColSpacing`, `PCB_PlaceEmbeddedBoard:MirrorFlag` | One `pcb_place_embedded_board` call on a scratch board | Each row names a member `IPCB_EmbeddedBoard` lacks; for the locations, position the board with `MoveByXY` from its bounding rectangle instead |
| P25 | `PCB_SetMechLayerKind:AddPair` | `pcb_set_mech_layer_kind` with a paired kind and `partner_layer` on two layers that are ALREADY paired | 0 = `AddPair` is idempotent; 1 = it raises on an existing pair, and the add needs a `PairDefined` check too |
| P26 | `PCB_ModifyLayer:CopperThickness`, `PCB_ModifyLayer:DielectricType`, `PCB_ModifyLayer:DielectricHeight`, `PCB_ModifyLayer:DielectricConstant`, `PCB_ModifyLayer:DielectricMaterial`, `DielectricTypeToken:Dielectric` | `pcb_modify_layer` on Mechanical1 with `copper_thickness_mils`, and on TopSolder with `dielectric_constant` | A row names a member that layer kind lacks: refuse it for that kind up front |
| P27 | `dispatcher` (in `PCB_CreateDesignRule`) | `pcb_create_design_rule` with `rule_type=solder_mask_expansion`, then `rule_type=vias_under_smd` | 1 = that rule type's factory or member is not scriptable: refuse it with `NOT_SCRIPTABLE` up front, as `pcb_set_via_soldermask_relief` does |
| P28 | `GetPCBProperty:<prop>` | `obj_query object_type=eConnectionObject props=IsRedundant,Mode` | A row: delete that branch and drop the name from `IsKnownPCBProperty` / `KnownPCBPropertyList` |
| P29 | `SetPCBProperty:<prop>` | `obj_modify` on a scratch region, `set='Kind=board_cutout'` | `SetPCBProperty:Kind` = the region Kind write is not scriptable: drop the write branch, keep the read |
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
| P35 | `CurrentFootprintNarrowed` (Library.pas; `ResolveLibFootprint`, `Lib_GetFootprintPads`, `Lib_Link3DModel`, `PCB_RenumberPads`) | Restart Altium, open a PcbLib by hand and select a footprint (nothing through the bridge). With NO footprint name: `lib_export_kicad_footprint output_path=<a scratch .kicad_mod>` (it reads the pads through `Lib_GetFootprintPads`), `lib_add_footprint_pad designator=99`, `pcb_renumber_pads`; then `lib_link_3d_model model_path=<a scratch .step>` | Each acts on the selected footprint, no dialog. 2026.10.11.03 takes the library iterator's reference when it compares equal to `PcbLib.CurrentComponent` (the measured LibMakeCurrentVerified compare) and `CurrentComponent` itself otherwise, exactly as before - so a box means `CurrentComponent`'s members fail like `CurrentSchComponent`'s AND the compare missed |
| P36 | `Audit_FindNetLabelConflicts` (`PinOwnerDesignatorAt`) | `audit_find_net_label_conflicts` on a scratch sheet with a net label sitting on a pin's connection end | NO LONGER FATAL-IF-WRONG: `Pin.OwnerSchComponent` is gone, and the owner is found by the proven shape (a sheet iterator over components, each component's own pin iterator, `Comp.Designator.Text`). An ordinary check: the entry names `<designator>.<pin>`, no dialog. `.<pin>` with an empty designator = no component's pin sat at that location with that number (record the sheet) |
| P37 | `Proj_Annotate` (TInterfaceList items) | `proj_annotate` on a scratch project with three unlocked parts, order `across_then_down` | Designators renumbered, no dialog. 2026.10.11.03 holds the list as the base `ISch_GraphicalObject` and narrows after retrieval (the PCB side's measured cure); a box = the SCH side fails differently |
| P38 | `FocusedServerDoc` / `GetPCBBoardAnywhere` (`View.OwnerDocument`) | 39.3 item 4 (P3) with the Home page focused, then `app_list_documents` with the Home page focused and at least one document LOADED and clean (each clean loaded document asks the focused view for its owner) | Both answer, no dialog (the 2026-10-11 `app_list_documents` ran with the Home page focused but nothing loaded, so it did not reach the read). A box = `OwnerDocument` is not on a non-document view: use `DM_FocusedDocument.DM_FullPath` + `Client.GetDocumentByPath` |
| P39 | `PCB_GetSelectedObjects` (`Board.SelectecObject[]`) | Select a track and a pad on a scratch board by hand, `pcb_get_selected_objects` | Both listed with their properties, no dialog. A box = the indexed property hands back an un-narrowed object: collect the selection with the iterator flag scan instead |
| P40 | `PCB_ModifyLayer` on a non-copper layer (P26) | P26's calls | As P26, but a box there is class (b), not a count: refuse those members for that layer kind up front |

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
  `last_fault.when`; before 2026.10.11.03 it was recorded only once a LATER
  call timed out, and `when` was always empty. Recovery: in the Script
  IDE press Stop (Run > Stop, or Ctrl+F3), close the unit's tab without
  saving, and rerun File > Run Script > Altium_API > Dispatcher.pas >
  StartMCPServer (or restart Altium). A `PreProcess` the handler opened
  before the halt is still open; `app_exit_active_command` after the
  restart unwinds it. Every such box is a defect (a member read the bridge
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

| Item | Verdict | What happened | On 2026.10.11.03 |
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

| Item | Verdict | What happened | On 2026.10.11.03 |
|---|---|---|---|
| 39.1 | PASS | `caught_exceptions` 0, `cast_errors` 0, `last_caught` null; `in_flight` [] | unchanged |
| 39.3 item 7 | PASS | with only `fa_lib.SchLib` open and focused, all six calls answered `NO_PCBLIB` (confirmed in activity.log), no dialog, the counters 0 / 0 / null between every call | unchanged |
| P8 | **FAIL, FATAL** | `lib_add_symbol_lines lines=[{0,0,100,0}]` in the fresh session (the editor's current symbol was `24AA02E48T-I_OT`, the first ALPHABETICAL one, not the first stored; nothing created through the bridge): the script engine's "Undeclared identifier: CurrentPartID" (OCR "CurrentPartlD"), a `TMessageForm` titled "Error" with only OK, and the Script IDE opened Library.pas at line 122 (`SetOwnerPart`). P9's site ran BEFORE P8's own (`MarkLibDirty`), which was never reached. Not counted (`last_caught` stayed null). After OK: `app_ping` "not responding", `in_flight` stuck on the call (age 15 s, then 1120 s), `last_fault.json` `dead_loop`, the IDE still parked on line 122. The `PreProcess` opened before the halt was left open and the factory's line orphaned; the library file was unchanged | GetTargetLibComponent no longer falls back to the editor's selection: NO_COMPONENT before any PreProcess, `component_name` on every symbol-edit tool, the owner part read once from a narrowed component before the PreProcess, `MarkLibDirty` / `TargetLibComponentName` / obj_create / obj_batch_create / the library parameter delete off that reference too; P8-P10 rewritten |
| P9, P1 (the rest of the fresh-session segment) | NOT RUN | the loop was dead | run P9 after P8; P1 in a fresh session |
| Section 38 re-check; 39.3 items 2, 3, 4, 5, 8; P1b, P2-P4, P6, P6b, P7; P10-P34 | NOT RUN | three later segments (20:24-20:39) each found the bridge down and stopped with nothing changed: Altium was NOT restarted (the same pid 48856, started 20:02:48) and StartMCPServer was not rerun, although a relayed message said Altium had been restarted. Recovery needed the owner at the Script IDE (39.5 (b)) | run them on 2026.10.11.03 |

What the halt taught, now in 39 "Why", 39.2, 39.4 and 39.5: the
popup-free premise that a remaining raise is COUNTED does not hold for an
undeclared identifier, and that box looks exactly like the counted one.
A whole-code sweep that night (every unit, at 3aca481) found the same
class at eight sites - every member read off `CurrentSchComponent` (four
routes into the eight `Lib_Add*` handlers, `lib_link_footprint`, the pin
readers, obj_create / obj_batch_create into a component and the library
parameter delete) and the `IPCB_Rule` constraint read in
`pcb_normalize_vias` - all fixed in 2026.10.11.03, and seven UNKNOWN ones
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

| Item | Verdict | What happened | On 2026.10.11.03 |
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
| P8, P9 | DEFERRED | the known fatal symbol-edit path on the second build | run on 2026.10.11.03 |

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

---

## Still open, and not blocking

* **#23** font size: the importer places symbol text but not its height.
  Altium's font size is not in mils and the conversion is undocumented,
  so the source range is reported rather than guessed. Calibrating it
  needs a live measurement.


## Generic property rejection on Altium 21 (live verified)

The same reads stopped polling on Altium 26.10.1.6 before this change:
`Text` on a parameter set, `IsHidden` on a net label, and `IsHidden` on a
wire. `IsHidden` is now accepted only on pins, parameters, designators, sheet
names and sheet file names, the types whose interface declares it; `Text` is
refused on the types whose interface has none, wires among them. Repeat
steps 3 to 7 on AD 26 with the wire included.

Verified on AD 26.10.1.6 with the 2026-10-01 script build: the three reads return
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
This preflight covers the guarded Text and IsHidden combinations, not every
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
   retain existing partial-write semantics for other valid assignments.
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
