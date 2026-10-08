# Release verification: 2026.10.07.03

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
| `visible=true` on `sch_set_components_parameters` | `ShowName := False` on a parameter it creates | 36 |

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
| Cross-document hints | The hint is appended to an error it does not apply to, or doubles up on a message that already names a tool | Focus a SchDoc and run `pcb_delete_object`. The refusal must name the PCB tool once, and read as one sentence |
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
| 2 | a SchLib, with a component selected |
| 3 | a SchLib |
| 4 | a PCB whose active variant has Not-Fitted parts |
| 5 | a PcbLib with a footprint, plus a `.step` file |
| 6 | a PcbLib, plus a KiCad `.kicad_mod` carrying `B.SilkS` text |
| 7 | a SchLib for the pins, and a schematic sheet for the power ports |
| 22 | a COPY of a PcbLib built by the IPC generator, plus a maker STEP for one of its footprints |

Steps 4 and 5 write to the design. Take an `app_checkpoint` first, or
work on a copy. Everything else either reads or builds new library
objects you can delete afterwards.

---

## 0. Confirm what is actually loaded

```
app_ping
```

Expect `altium_script_version` = `2026.10.07.03`, `version_match` =
`true`, and `mcp_server_version` = `0.6.1`.

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
| `stuck_handler` | keep-alives answered, one command never returned | Stop, relaunch |
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

This runs 27 assertions over `StrToIeeeSymbol`, `IeeeSymbolToStr` and
`StripChar` inside Altium's own engine. The values match what
`tests/test_cross_validate.py` pins against the FPC-compiled originals,
so a failure here means DelphiScript disagrees with Free Pascal. That
is the gap this step exists to close.

**If it fails:** the log names the assertion. Report the text; the
converters are in `scripts/altium/Utils.pas`.

---

## 2. Pin edge decorations (task #20)

Needs a SchLib open with a component selected.

```
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

* the property exists but rejects the value: the dispatcher catches it
  and answers `INTERNAL_ERROR: Unhandled exception processing:
  library.add_pins`. The loop keeps running.
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
behaviour to verify, not a defect. Resolving a restore from the current
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

Place four pins with different electrical types, then read them back:

```
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

Expect a pin list. Then call it again with no `component_name` and a
symbol selected in the editor, which must also work.

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
    pcb_place_3d_body  model_path <a .txt file>

The first must be `FILE_NOT_FOUND` and the second `MODEL_LOAD_FAILED`.
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
  undeclared identifier. `pcb_set_plane_net` reads and writes that same
  `LayerObj.Net`, so expect it to stop the polling loop the same way
  until it is changed; do not run it on a board in use.
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

Finally the refusals: `align middle` answers `BAD_ALIGN` before anything
is touched, and a `.txt` file as `model_path` with `replace true` answers
`MODEL_LOAD_FAILED` and leaves the placeholders where they were (list
the footprint to check).

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.07.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.07.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.07.03`.

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

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.07.03`.
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

## 36. Parameter stamps arrive exactly, Description is the component's, new parameters hidden (UNVERIFIED-LIVE)

Field reports 2026-10-08 (carrier project, bridge 2026.10.07.03), all on
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

**Not run live.** The script version was NOT bumped by this change; bump it at
deploy if the procedure needs app_ping to tell the builds apart. Independent of
that, the reply echoes `value_encoding: "pct1"` only from the new handler, and
the Python tool adds a `warning` when the echo is missing, so step 2 below
detects a stale script by itself.

1. **Version.** `app_ping`: `altium_script_version` = `2026.10.07.03` (or the
   bumped number), `version_match` true.
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
   on R1 before and after: unchanged).
5. **Comment.** Stamp `{"designator": "R1", "Comment": "10k"}`: `properties_set:
   1`, the part's Comment shows `10k`, no parameter named Comment created.
6. **Existing parameter untouched otherwise.** Make `ZZ Test` visible and move
   it by hand, then stamp a new value: only the text changes, it stays visible
   and where it was (`parameters_modified: 1`).
7. **Opt-in show (the one unproven write).** Stamp a fresh key
   `{"designator": "R1", "ZZ Shown": "x"}` with `visible=true`: `ZZ Shown`
   is visible at R1's location, value only (no `ZZ Shown =` prefix), and the
   bridge answers `app_ping` afterwards. If the loop stops here, `ShowName` is
   not a parameter member on this Altium: drop that line from
   `SetCompParamTextEx` rather than retrying.
8. Undo the scratch edits (or close the sheet without saving).

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
