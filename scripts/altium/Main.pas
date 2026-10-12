{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Main.pas - Constants, IPC primitives and JSON helpers for the Altium bridge   }
{ The script polls for request_<id>.json files, processes commands, writes      }
{ response_<id>.json with the matching ID. Per-request files eliminate the      }
{ stale-response race that the old single-file scheme had.                       }
{..............................................................................}

Const
    // Bump SCRIPT_VERSION whenever the .pas sources change. Python reads the
    // same string from the on-disk Main.pas and compares it to what ping
    // returns, mismatch means Altium is running a stale compiled script
    // (DelphiScript caches compiled units until the script project is
    // reopened or Altium is restarted).
    SCRIPT_VERSION = '2026.10.12.02';

    // How far a linked 3D body's box may sit from its footprint's pads (or
    // origin, with no pads) before lib_link_3d_model reports placement_ok
    // false. A model is drawn around its package, so 1000 mil (25.4 mm) of
    // clear space between the two is a body that is not on its footprint.
    // Measured case it exists for: 2026-10-04, a STEP placed 50,000 mil from
    // its pads passed the old self-check.
    PlaceFarMils = 1000;

    // How far up the mechanical layers a pair tidy looks. Altium allows 1024,
    // and checking every combination of those is a million probes for a stack
    // that in practice stops in the low tens. The bound is reported back so a
    // pair above it is known to have been skipped rather than judged clean.
    MechScanLimit = 64;

    // Wire protocol version. Bumped whenever the request/response JSON shape
    // changes incompatibly. Python and Pascal must agree; mismatch returns
    // PROTOCOL_VERSION_MISMATCH on the Pascal side and raises on the Python
    // side. v2 introduced per-request IPC files, structured error.details,
    // and the protocol_version field itself.
    PROTOCOL_VERSION = 2;

    { Milliseconds during which SmartCompile reuses the previous DM_Compile    }
    { result instead of recompiling. Design-review snapshots fire 3-4 project  }
    { handlers back-to-back; each DM_Compile can be 5-10 s on a real design,   }
    { so bursts without this cache add up to 30-40 s of needless recompiles.   }
    COMPILE_CACHE_TTL_MS = 2000;

    CONFIG_FILE = 'mcp_config.json';

    // ISch_RobotManager SendMessage IDs (from Altium Schematic API docs).
    SCHM_PrimitiveRegistration = 1;
    SCHM_BeginModify           = 2;
    SCHM_EndModify             = 3;

    // DelphiScript does NOT predefine Delphi's MaxInt (raises "Undeclared
    // identifier" at runtime), so it is declared here. Used as a
    // "smallest seen so far" sentinel in board-statistics scans.
    //
    // NOT 2147483647. AD25 rejects that literal outright with "Invalid
    // constant" while compiling the script, which stops the loop before
    // it serves anything (issue #22, measured on AD25).
    //
    // 1e9 internal units is 100 inches, which is Altium's own maximum
    // board dimension, so a coordinate could in principle equal it. That
    // is acceptable here: the bounding-box scan carries its own Found
    // flag as the real guard, and the dimension sentinels it initialises
    // (track width, hole size, annular ring) are orders of magnitude
    // below it.
    MAX_INT = 1000000000;

Var
    WorkspaceDir : String;
    Running : Boolean;

    { Polling tunables, defaults below, overridden by mcp_config.json at      }
    { startup via LoadMCPConfig. Single source of truth: the config file.     }
    PollIntervalActiveMs : Integer;
    PollIntervalIdleMs   : Integer;
    IdleThreshold        : Integer;
    AutoShutdownMs       : Cardinal;
    YieldIterations      : Integer;
    YieldEveryNActive    : Integer;

    { SmartCompile cache state. Tick of the last DM_Compile and the Project    }
    { pointer it was run against, so we only skip when the SAME project was    }
    { compiled recently. Reset to 0 / Nil at startup (StartMCPPump; until      }
    { 2026.10.12.03 this comment said so and nothing did it).                  }
    LastCompileTick : Cardinal;
    { Documents the last save pass actually reached. See
      SaveOneDocByDocRef. Reset by App_SaveAll before each pass. }
    SaveAttempts : Integer;
    { What a save pass may LEAVE ALONE. Both False means every open document
      is written whether it reads modified or not, which is what every caller
      except App_SaveAll gets. App_SaveAll sets them for its own pass from
      include_libraries / only_modified and puts them back to False before it
      returns. SaveSkipped / SaveSkippedPaths record what the pass left alone,
      so a skip is reported rather than looking like a write. The paths are
      pipe-separated: EscapeJsonString is in Utils.pas, which builds AFTER
      this file, so App_SaveAll makes the JSON. See SaveOneDocByDocRef for
      the measurement that made skipping necessary. }
    SaveSkipCleanLibs : Boolean;
    SaveSkipCleanSheets : Boolean;
    SaveSkipped : Integer;
    SaveSkippedPaths : String;
    { THE BRIDGE'S OWN RECORD OF WHAT IT EDITED, because the modified flag
      cannot be the only witness. MEASURED 2026-10-10 (AD 26.10.1.5, bridge
      2026.10.10.01): after lib_set_footprint_height and after obj_modify
      on a free scratch sheet, the document read Modified False while its
      tab showed '*', so app_save_all's default (libraries) and
      only_modified (sheets) would have left real edits unwritten.
      MarkDocDirty puts the RESOLVED ABSOLUTE path of what it marks on it
      as KEY*AGE (DocPathKey and the file's age then; NEVER a bare name -
      see RecordBridgeEdit for the reviews that made both rules);
      SaveOneDocByDocRef and SaveBridgeRecordSweep write a recorded
      document even when its flag reads clean, and any write of the file,
      by any route, retires the entry.
      SaveBridgeRecordPaths / SaveBridgeRecordUnwritten report, per pass,
      what was written only because of the record and what was attempted
      that way without the file moving. }
    BridgeEditedPaths : String;
    SaveBridgeRecordPaths : String;
    SaveBridgeRecordUnwritten : String;
    { Written for the record within FileAge's 2 s tick (flag reads clean  }
    { after the save, age unmoved: probably written); record entries whose }
    { document was no longer open; DocPathKeys the current pass attempted. }
    SaveBridgeRecordSameTick : String;
    SaveBridgeRecordClosed : String;
    { Record entries whose file is OPEN under another spelling the lookup  }
    { cannot equate (the caller passed F:\ for a subst drive, 8.3 or UNC,  }
    { while the listing and FileName both say H:\): kept, never filed as   }
    { closed, and listed by app_save_all as bridge_record_unresolved.      }
    SaveBridgeRecordUnresolved : String;
    SaveAttemptedKeys : String;
    { True only while SaveAllDirty walks the workspace. The one-visit-per-  }
    { file early exit in SaveOneDocByDocRef applies only then: proj_save,   }
    { proj_close save=true and the other direct SaveProjectMembers callers  }
    { never reset SaveAttemptedKeys, so outside a walk the list still holds }
    { the LAST app_save_all's files, and an edit made since would be        }
    { silently left unwritten (review of 0c4f20e).                          }
    SaveWalkPass : Boolean;
    { Free documents the save walk could not reach: a bare DM_FullPath that  }
    { resolves to no free document (several listed documents carry the name, }
    { or only a project member or nothing answers it). Listed                }
    { by app_save_all as free_documents_not_reached, so a pass never skips   }
    { a document silently; a bridge-edited one is written by the record      }
    { sweep from its absolute key all the same.                              }
    SaveUnresolvedPaths : String;
    { Running number of the documents with no file name at all listed in    }
    { SaveUnresolvedPaths ('(unnamed document N)'), reset with that list.    }
    SaveUnnamedCount : Integer;
    { What the LAST MarkDocDirty call found, for the reply that claims
      marked_modified (MarkOutcomeJson in Utils.pas renders it: this unit
      builds before EscapeJsonString exists). MarkLastFocused is -1 when the
      focused document is not the one marked, else 0/1 for its flag. }
    MarkLastPath : String;
    MarkLastFile : String;
    FindDocVia : String;
    { The DocPathKeys of the project members (real projects only) that      }
    { carry the name DocNameCount was last asked about, '|'-separated. A    }
    { bare request comes only from a FREE document, so FindOpenServerDoc    }
    { never answers one with these files (review of c8ee69e).               }
    DocNameMemberKeys : String;
    MarkLastVia : String;
    MarkLastFound : Boolean;
    MarkLastModified : Boolean;
    MarkLastFocused : Integer;
    MarkLastError : String;
    { THE D1 DIAGNOSTIC (2026.10.11.02). The raw Modified value the mark     }
    { read right after SetModified(True), named by the two proven tests      }
    { alone (ModifiedFlagRaw / ModifiedFlagTests): '' when nothing was found. }
    MarkLastFlagRaw : String;
    MarkLastFlagTests : String;
    { True when the mark put an ABSOLUTE key on the record (the only kind  }
    { app_save_all can write from); a bare entry never sets it.            }
    MarkLastRecorded : Boolean;
    LastCompiledProject : IProject;

    { Silent cast-failure counter, incremented every time a defensive       }
    { Try/Except in an iteration helper swallows an interface cast that      }
    { ObjectId-checking should have ruled out. Surfaced via application.ping }
    { as cast_errors so a non-zero value at session end isn't invisible.     }
    CastErrorCount : Integer;

    { WHICH COMMAND RAISED, for the modal nobody can see the reply of.       }
    { Altium's script engine shows an exception as a modal 'press OK'       }
    { dialog BEFORE the surrounding Try/Except runs (measured: see          }
    { WriteFileContent and StrToFloatDef), and while it is up the engine is }
    { blocked. So the command and request in flight are recorded BEFORE     }
    { dispatch (CurrentCommand / CurrentRequestId), and every Except that   }
    { is not a fault-only backstop calls RecordCaughtException, which       }
    { counts the catch and stamps it with that command. A rise in           }
    { CaughtExceptionCount across a run means a modal fired somewhere, even }
    { one somebody clicked away. Reset by ResetCaughtExceptions at pump     }
    { start.                                                                }
    CurrentCommand : String;
    CurrentRequestId : String;
    CaughtExceptionCount : Integer;
    LastCaughtWhere : String;
    LastCaughtCommand : String;
    LastCaughtRequestId : String;
    LastCaughtStamp : String;

    { Client.GetProductVersion's major number, read once (AltiumMajorVersion). }
    { '' = not read yet; '-1' = unreadable; otherwise the digits.              }
    AltiumMajorCache : String;

    { Tracks the most recently created/selected library component so the    }
    { Lib_Add* primitive helpers can target it directly. SchLib's           }
    { CurrentSchComponent setter is a no-op in DelphiScript, assigning to  }
    { it does not move the editor's selection, so primitives that read     }
    { CurrentSchComponent end up attaching to whatever the editor was       }
    { showing before (typically Component_1, the default empty placeholder).}
    { Storing the reference here gives us a working "current target" the    }
    { primitive helpers can trust.                                           }
    LastCreatedLibComponent : ISch_Component;

    { The name that reference was known by, recorded when it was set.

      ASKING THE COMPONENT ITS OWN NAME IS NOT AN OPTION HERE.
      Measured on AD26: reading LibReference off THIS global raised
      "Undeclared identifier: LibReference", and the modal took the
      polling loop with it because undeclared identifiers are not
      catchable and the Try/Except around it did nothing. One caller sat
      on that dialog for over two minutes.

      The property itself is fine. ScanLibForComponent reads it off
      every component it walks, ten lines earlier in the same lookup,
      and has always worked. What differs is the reference: this one is
      held ACROSS commands, and the document it belongs to may have been
      closed, reopened or re-imported since. A member read on a
      component that no longer exists is reported as an undeclared
      identifier rather than as a missing object, which is why it reads
      as an API error and is not one.

      So the reference is cleared whenever this script reopens the
      library, and the name is compared against this recorded string
      rather than against anything read back off the component.

      A SECOND EXPLANATION, NOT RULED OUT (finding D-F2-1, 2026-10-12):
      until 2026.10.12.03 nothing assigned these four globals at pump
      start, and an unassigned DelphiScript global is Empty, which does
      NOT compare equal to Nil. A read of LibReference on this global in a
      session that had never set it would be the same box for that reason
      alone. StartMCPPump now assigns them (Nil / ''), and the rule above
      stays either way. }
    LastCreatedLibComponentName : String;

    { The library document LastCreatedLibComponent belongs to, recorded when  }
    { it is set: the path (SchLib.DocumentName) and the IServerDocument that  }
    { Client.GetDocumentByPath returned for it then. A pointer and a string,  }
    { so asking whether the held reference is still live never reads a       }
    { member off it (see LastCreatedLibComponentName for why it cannot).      }
    { Cleared wherever LastCreatedLibComponent is cleared.                    }
    LastCreatedLibDoc : IServerDocument;
    LastCreatedLibPath : String;

    { Re-entry guard for LookupLibComponent's last-resort reopen. A
      reopen re-reads the document and the retry looks the name up
      again; without this the retry could trigger another reopen. }
    RefreshingLib : Boolean;

{..............................................................................}
{ Initialise polling tunables to compile-time defaults. Called by the          }
{ dispatcher startup before LoadMCPConfig so a missing/corrupt config file    }
{ still leaves the loop with sane values.                                     }
{..............................................................................}

Procedure InitDefaultConfig(Dummy : Integer);
Begin
    PollIntervalActiveMs := 10;
    PollIntervalIdleMs   := 30;
    IdleThreshold        := 150;
    AutoShutdownMs       := 600000;  { 10 min }
    YieldIterations      := 5;
    YieldEveryNActive    := 5;
End;

{ THE LOGGER AND THE CATCH COUNTERS SIT AT THE TOP OF THE FIRST UNIT so    }
{ every routine in every .pas file, Main.pas's own included, can call     }
{ them (DelphiScript resolves strictly top-down and has no Forward).       }
{                                                                           }
{ Append a line to workspace/activity.log for performance profiling. The     }
{ polling loop uses this to record per-command timings, and handlers can     }
{ add their own sub-stage timings for bottleneck analysis. Silently           }
{ swallows IO errors, logging must not break a command.                      }
Procedure AppendLog(Line : String);
Var
    F : TextFile;
    LogPath : String;
Begin
    Try
        LogPath := WorkspaceDir + 'activity.log';
        AssignFile(F, LogPath);
        If FileExists(LogPath) Then Append(F) Else Rewrite(F);
        Try
            WriteLn(F, Line);
        Finally
            CloseFile(F);
        End;
    Except
        // Never raise from the logger
    End;
End;

Function FormatLogStamp(Dummy : Integer): String;
Begin
    Try
        Result := FormatDateTime('yyyy-mm-dd hh:nn:ss.zzz', Now);
    Except
        Result := '';
    End;
End;

Procedure RecordCastError(Where : String);
Begin
    Inc(CastErrorCount);
    AppendLog(FormatLogStamp(0) + ',0,_cast_error,' + Where);
End;

{ A Try/Except caught something that was not a genuine fault. Since the
  engine has ALREADY shown the exception as a modal by the time the Except
  runs, this is a record that a dialog fired, not a recovery: one counter,
  the place, and the command and request that were in flight (set by the
  dispatcher before it calls the handler). One activity.log row in the
  RecordCastError style, which the dashboard already tails. Where names the
  site as '<Function>:<what>'. }
Procedure RecordCaughtException(Where : String);
Begin
    Inc(CaughtExceptionCount);
    LastCaughtWhere := Where;
    LastCaughtCommand := CurrentCommand;
    LastCaughtRequestId := CurrentRequestId;
    LastCaughtStamp := FormatLogStamp(0);
    AppendLog(LastCaughtStamp + ',0,_caught_exception,' + Where + ','
        + CurrentCommand + ',' + CurrentRequestId);
End;

{ Start a session with nothing counted and no command in flight, never with }
{ whatever an uninitialised global holds. Called by StartMCPPump.           }
Procedure ResetCaughtExceptions(Dummy : Integer);
Begin
    CaughtExceptionCount := 0;
    LastCaughtWhere := '';
    LastCaughtCommand := '';
    LastCaughtRequestId := '';
    LastCaughtStamp := '';
    CurrentCommand := '';
    CurrentRequestId := '';
End;

{ The running Altium's major version (26 for '26.10.1.6'), read once and
  cached in AltiumMajorCache. -1 when the version cannot be read or does not
  start with a number.

  GATE RULE for callers: skip a version-specific member only when the result
  is >= 0 AND below the first version known to have it. -1 (unknown) must
  never suppress anything, so a build whose version string this cannot parse
  behaves exactly as it does today. The GetProductVersion read is the one
  App_Ping makes on every keep-alive, so it is fault-only on every supported
  build; its Except is the fault backstop, not a guard. }
Function AltiumMajorVersion(Dummy : Integer) : Integer;
Var
    Ver, Digits, Ch : String;
    I : Integer;
Begin
    If AltiumMajorCache = '' Then
    Begin
        Ver := '';
        Try
            Ver := Client.GetProductVersion;
        Except
            Ver := '';
        End;
        Ver := Trim(Ver);
        Digits := '';
        I := 1;
        While I <= Length(Ver) Do
        Begin
            Ch := Copy(Ver, I, 1);
            If (Ch < '0') Or (Ch > '9') Then Break;
            Digits := Digits + Ch;
            I := I + 1;
        End;
        { Four digits at most, so StrToInt below can never be handed a value }
        { that does not fit (and never raises: the string is digits only).   }
        If (Digits = '') Or (Length(Digits) > 4) Then
            AltiumMajorCache := '-1'
        Else
            AltiumMajorCache := Digits;
    End;
    Result := StrToInt(AltiumMajorCache);
End;

{..............................................................................}
{ Batch tool helpers                                                            }
{                                                                               }
{ New-generation batch tools use '~~' (double tilde) as the operation           }
{ separator and ';' as field separator within an operation. '~~' doesn't       }
{ appear in Altium object names, filters, or property strings, so it's         }
{ unambiguous even when a single operation's property list contains '|'.       }
{                                                                               }
{ Defined in Main.pas so Library.pas and Generic.pas can both use them,       }
{ the Altium project compiles files in DesignN order (Main â†’ ... â†’ Library â†’  }
{ ... â†’ Generic) and a callee must come earlier than its caller.               }
{..............................................................................}

Function NextBatchOp(Var Remaining : String) : String;
Var
    SepPos : Integer;
Begin
    Result := '';
    While Length(Remaining) > 0 Do
    Begin
        SepPos := Pos('~~', Remaining);
        If SepPos = 0 Then
        Begin
            Result := Remaining;
            Remaining := '';
            Exit;
        End;
        Result := Copy(Remaining, 1, SepPos - 1);
        Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
        If Result <> '' Then Exit;
    End;
End;

Function GetBatchField(Op : String; Key : String) : String;
Var
    Remaining, Field, FKey, FVal : String;
    SepPos, EqPos : Integer;
Begin
    Result := '';
    Remaining := Op;
    While Length(Remaining) > 0 Do
    Begin
        SepPos := Pos(';', Remaining);
        If SepPos = 0 Then
        Begin
            Field := Remaining;
            Remaining := '';
        End
        Else
        Begin
            Field := Copy(Remaining, 1, SepPos - 1);
            Remaining := Copy(Remaining, SepPos + 1, Length(Remaining));
        End;
        EqPos := Pos('=', Field);
        If EqPos > 0 Then
        Begin
            FKey := Copy(Field, 1, EqPos - 1);
            FVal := Copy(Field, EqPos + 1, Length(Field));
            If UpperCase(FKey) = UpperCase(Key) Then
            Begin
                Result := FVal;
                Exit;
            End;
        End;
    End;
End;

{..............................................................................}
{ SmartCompile                                                                  }
{                                                                               }
{ Thin wrapper over Project.DM_Compile that skips the compile when the SAME     }
{ project was compiled less than COMPILE_CACHE_TTL_MS ago.                      }
{..............................................................................}

{..............................................................................}
{ THE MODIFIED FLAG AS A REAL BOOLEAN (live 2026-10-11, AD 26.10.1.5).        }
{                                                                              }
{ After SetModified(True), IServerDocument.Modified read as NEITHER true NOR  }
{ false on a schematic sheet: 'If X' and 'If Not X' were both false. The tab  }
{ showed '*', every mark answered marked_modified:false, and the branches     }
{ that test the NEGATION never fired either (obj_modify's 'If Not Marked'     }
{ save_hint, MarkDocDirty's 'flag reads False' problem, app_close_document's  }
{ 'This bridge edited' message). A plain False cannot do that. The likely     }
{ value is a Boolean variant holding 1 rather than True's -1. Documents       }
{ nobody edited read a normal False; the PcbDoc / PcbLib flags read true.     }
{                                                                              }
{ ONLY THE TWO FORMS THAT RAN LIVE ON THIS VALUE. 'If X' and 'If Not X' were  }
{ both evaluated on the odd value AND on normal ones, live, with 0 caught     }
{ exceptions. Nothing else is: no compare of a Boolean with an integer or    }
{ with the True / False literals appears anywhere else in these units, and a  }
{ raise here would be a modal on every edit and every save walk. No Variant   }
{ routine either (VarType / VarIsNull / VarToStr have never run here, and an  }
{ undeclared identifier faults uncatchably inside the polling loop).          }
{                                                                              }
{ ModifiedFlagState answers, never raising:                                    }
{   0  clean: 'If Not X' holds (and a Nil document: nothing open holds edits) }
{   1  modified: 'If X' holds                                                 }
{   2  NEITHER holds - the field's odd value. Counted as modified: doubt      }
{      writes.                                                                 }
{   3  unreadable: the read (or one of the two tests) raised, counted.         }
{ ModifiedFlagOf is the Boolean every save, close and count gate uses: state  }
{ <> 0, so an odd or unreadable flag writes. A READ-BACK (MarkDocDirty) uses  }
{ the state itself, so a mark never claims a flag it could not read.          }
{ An unassigned (Empty) value reads False under 'If Not X' (Delphi converts   }
{ it to False) and so reads clean; a Null raises on conversion and reads 3.   }
{ DelphiScript locals are untyped, so Raw holds exactly what the property    }
{ returned: the live run showed MarkLastModified (declared Boolean) carrying }
{ the odd value straight through.                                            }
{..............................................................................}
Function ModifiedFlagState(Doc : IServerDocument) : Integer;
Var
    Raw : Boolean;
Begin
    Result := 0;
    If Doc = Nil Then Exit;
    Result := 3;
    Try
        Raw := Doc.Modified;
        If Raw Then Result := 1
        Else If Not Raw Then Result := 0
        Else Result := 2;
    Except
        Result := 3;
        RecordCaughtException('ModifiedFlagState:Modified');
    End;
End;

Function ModifiedFlagOf(Doc : IServerDocument) : Boolean;
Begin
    Result := ModifiedFlagState(Doc) <> 0;
End;

{ THE D1 DIAGNOSTIC: the raw flag the mark read, named by the TWO PROVEN     }
{ TESTS ONLY. No compare of any kind, at any value: a SchDoc reads the odd    }
{ value after EVERY SetModified(True), so a stage fenced to 'the odd value'   }
{ ran on every schematic mark (review of d198b65), and a Try is no guard -    }
{ the engine shows a raise as a modal BEFORE the Except runs. The numeric     }
{ detail (1? -1?) is not needed: no decision reads it, and 'neither test      }
{ held' already tells the field value apart from True and False.             }
{                                                                              }
{ ModifiedFlagRaw: 'true' / 'false' when 'If X' / 'If Not X' held, 'odd'     }
{ when neither did, 'unreadable' when the read or a test raised, '' with no  }
{ document.                                                                   }
Function ModifiedFlagRaw(Doc : IServerDocument) : String;
Var
    Raw : Boolean;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    Result := 'unreadable';
    Try
        Raw := Doc.Modified;
        If Raw Then Result := 'true'
        Else If Not Raw Then Result := 'false'
        Else Result := 'odd';
    Except
        Result := 'unreadable';
        RecordCaughtException('ModifiedFlagRaw:Modified');
    End;
End;

{ ModifiedFlagTests: 'if=1|0,not=1|0' from the two proven tests. A normal    }
{ True reads 'if=1,not=0', a normal False 'if=0,not=1', the field's odd      }
{ value 'if=0,not=0'; 'unreadable' when the read or a test raised.           }
Function ModifiedFlagTests(Doc : IServerDocument) : String;
Var
    Raw : Boolean;
    TIf, TNot : String;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    Result := 'unreadable';
    TIf := '0';
    TNot := '0';
    Try
        Raw := Doc.Modified;
        If Raw Then TIf := '1';
        If Not Raw Then TNot := '1';
    Except
        RecordCaughtException('ModifiedFlagTests:Modified');
        Exit;
    End;
    Result := 'if=' + TIf + ',not=' + TNot;
End;

{ Probe whether any logical document in the project has been modified by    }
{ an out-of-band edit (typically: user clicked in Altium's UI between MCP   }
{ calls). If so the cached DM_Compile is stale even if it's within the     }
{ TTL window, force a fresh recompile so subsequent queries see the       }
{ new netlist / component set.                                             }
Function ProjectHasDirtyDocs(Project : IProject) : Boolean;
Var
    I : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    Readable : Boolean;
Begin
    { UNREADABLE IS NOT CLEAN. This used to swallow the exception and carry
      on, so a project whose documents could not be reached at all reported
      nothing dirty and the cached netlist was reused. Reading nothing and
      there being nothing are different answers and only one of them means
      the cache is still good.

      A Nil ServerDoc is the exception to that and stays clean on purpose:
      it means the document is not open in the editor, and a closed
      document cannot be holding unsaved edits. Treating it as dirty would
      force a recompile on every call for any project with a closed sheet,
      which is most of them. }
    Result := False;
    If Project = Nil Then Exit;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try
            Doc := Project.DM_LogicalDocuments(I);
        Except
            { Written out rather than swallowed, so the next reader can see
              that the failure is handled by the Nil check below and not
              simply ignored. }
            Doc := Nil;
        End;
        If Doc = Nil Then
        Begin
            { The project structure would not answer for one of its own
              documents. Say dirty and recompile rather than guess. }
            Result := True;
            Exit;
        End;

        ServerDoc := Nil;
        Readable := False;
        Try
            ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
            Readable := True;
        Except
            Readable := False;
        End;

        If Not Readable Then
        Begin
            Result := True;
            Exit;
        End;

        If ServerDoc <> Nil Then
        Begin
            Try
                If ModifiedFlagOf(ServerDoc) Then
                Begin
                    Result := True;
                    Exit;
                End;
            Except
                { Could not read the flag, so it is not evidence of clean. }
                Result := True;
                Exit;
            End;
        End;
    End;
End;

Procedure SmartCompile(Project : IProject);
Begin
    If Project = Nil Then Exit;
    // Honour the TTL window only when nothing in the project has changed
    // since the last compile. An external UI edit invalidates the cache
    // immediately, so the next MCP call after the user clicked "Add part"
    // sees the new state instead of a 2-second-stale snapshot.
    If (Project = LastCompiledProject) And (LastCompileTick > 0) And
       ((GetTickCount - LastCompileTick) < COMPILE_CACHE_TTL_MS) And
       (Not ProjectHasDirtyDocs(Project)) Then
        Exit;
    Project.DM_Compile;
    LastCompiledProject := Project;
    LastCompileTick := GetTickCount;
End;

{ Resolve the post-compile document set. After Project.DM_Compile the          }
{ flattened netlist (with hierarchy resolved through sheet symbols and sheet  }
{ entries) lives on DM_PhysicalDocuments, NOT on DM_LogicalDocuments. The      }
{ source-side per-sheet Pin.DM_FlattenedNetName values are NOT unified across  }
{ hierarchy boundaries, only the physical-document pin reads carry the flat   }
{ project-wide net names. Reference: Altium SDK Connectivity.pas              }
{ (DM_PhysicalDocuments walked after DM_Compile to harvest pin nets).         }
{                                                                              }
{ Returns the physical-document count when populated (the standard post-      }
{ compile case), falls back to logical for projects that haven't compiled or  }
{ have a single sheet so the caller keeps working in degenerate cases.         }
Procedure GetCompiledDocs(Project : IProject; Var DocCount : Integer; Var UsePhysical : Boolean);
Begin
    DocCount := 0;
    UsePhysical := False;
    If Project = Nil Then Exit;
    Try DocCount := Project.DM_PhysicalDocumentCount; Except End;
    If DocCount > 0 Then
    Begin
        UsePhysical := True;
        Exit;
    End;
    Try DocCount := Project.DM_LogicalDocumentCount; Except End;
End;

Function GetCompiledDoc(Project : IProject; Idx : Integer; UsePhysical : Boolean) : IDocument;
Begin
    Result := Nil;
    If Project = Nil Then Exit;
    If UsePhysical Then
        Try Result := Project.DM_PhysicalDocuments(Idx); Except End
    Else
        Try Result := Project.DM_LogicalDocuments(Idx); Except End;
End;

Procedure InvalidateCompileCache(Dummy : Integer);
Begin
    LastCompileTick := 0;
    LastCompiledProject := Nil;
End;

{..............................................................................}
{ ISch_RobotManager.SendMessage helpers.                                        }
{..............................................................................}

Procedure SchBeginModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_BeginModify, Nil);
End;

Procedure SchEndModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_EndModify, Nil);
End;

Procedure SchRegisterObject(Container, Obj : ISch_BasicContainer);
Begin
    If (Container <> Nil) And (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(
            Container.I_ObjectAddress, Nil, SCHM_PrimitiveRegistration,
            Obj.I_ObjectAddress);
End;

{..............................................................................}
{ Mark one document dirty by path, and SAY WHETHER IT TOOK. IT DOES NOT WRITE. }
{                                                                              }
{ Deferred save is deliberate: marking is cheap, and app_save_all flushes at   }
{ a checkpoint. The hazard is the NAME. Called SaveDocByPath it produced a     }
{ comment in Generic.pas claiming it wrote to disk, and three tool docstrings  }
{ promising the caller a save. A caller who needs bytes on disk must call      }
{ app_save_all or proj_save.                                                   }
{                                                                              }
{ THE SILENT MARK, MEASURED 2026-10-10 (AD 26.10.1.5, bridge 2026.10.10.01).   }
{ After lib_set_footprint_height the library read Modified False on all three }
{ writes while its tab showed '*'; lib_save_library then reported             }
{ was_modified false; after obj_modify on a free scratch SchDoc the active    }
{ document read modified:false 14 s later with '*' in the title bar. Both     }
{ were FREE documents under a Temp path. The old procedure did               }
{ GetDocumentByPath, exited silently on Nil and swallowed SetModified, so    }
{ nothing could tell a mark that took from one that never found its          }
{ document. And for a free document the paths handlers pass are not always   }
{ the one GetDocumentByPath wants: DM_FullPath and ISch_Document.            }
{ DocumentName both return a BARE NAME for a free document (measured, see    }
{ ResolveLoadedDocPath), and ProcessActiveDoc passes DocumentName.           }
{                                                                              }
{ So the lookup is FindOpenServerDoc, which answers an absolute path only    }
{ with THAT file and a bare name with the focused document or the one listed }
{ document of that name - never a namesake (review 2026-10-11: the first     }
{ version marked the carrier sheet for an edit to its scratch copy). The     }
{ mark is written to the document found AND to the focused document when it  }
{ is the same file by key (the object whose tab shows '*'), and MarkDocDirty }
{ returns the flag READ BACK. What it found is left in the MarkLast* globals }
{ for MarkOutcomeJson (Utils.pas), which every reply that claims             }
{ marked_modified carries. The file also goes on the bridge's own record     }
{ (BridgeEditedPaths), which app_save_all writes even when the flag reads    }
{ clean: the flag is a witness, not the only one.                            }
{                                                                              }
{ MarkDocDirtyByPath stays a procedure (eighty call sites call it as a       }
{ statement) and wraps MarkDocDirty.                                          }
{..............................................................................}

Function LooksAbsolutePath(P : String) : Boolean;
Begin
    Result := (Copy(P, 2, 1) = ':') Or (Copy(P, 1, 2) = '\\');
End;

{ One spelling per file: trimmed, forward slashes turned back, upper case. }
Function DocPathKey(P : String) : String;
Begin
    Result := UpperCase(Trim(StringReplace(P, '/', '\', -1)));
End;

Function DocNameKey(P : String) : String;
Begin
    Result := UpperCase(ExtractFileName(Trim(StringReplace(P, '/', '\', -1))));
End;

Function ServerDocFileName(Doc : IServerDocument) : String;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    Try Result := Doc.FileName; Except Result := ''; End;
End;

{ The document behind the focused view: the object whose tab shows '*'. }
Function FocusedServerDoc(Dummy : Integer) : IServerDocument;
Var
    View : IServerDocumentView;
Begin
    Result := Nil;
    Try
        View := Client.GetCurrentView;
        If View <> Nil Then Result := View.OwnerDocument;
    Except
        Result := Nil;
    End;
End;

{ THE KEY ONE LISTED DOCUMENT COUNTS UNDER. A project member's DM_FullPath   }
{ is absolute and names its file. A free document's is often a BARE name    }
{ (measured, see ResolveLoadedDocPath), which proves nothing about which    }
{ file it is, so it counts as a file of its own: two free copies of one     }
{ name are two files, never one.                                            }
Function ListedDocKey(Full : String; I : Integer; J : Integer) : String;
Begin
    If LooksAbsolutePath(Trim(Full)) Then Result := DocPathKey(Full)
    Else Result := '?' + IntToStr(I) + ':' + IntToStr(J);
End;

{ HOW MANY LISTED DOCUMENTS CARRY THIS FILE NAME: every project's members   }
{ and the free documents, each file once. Counted from the LISTING, before  }
{ any lookup, because a free copy whose bare DM_FullPath does not resolve   }
{ is still a file with that name (review 2026-10-11: counting only what     }
{ resolved left the project's sheet as the "one" match for its scratch      }
{ copy). A closed project member counts too: refusing a guess costs a       }
{ reported miss, a wrong guess marks and writes somebody else's file.       }
{ The same pass leaves DocNameMemberKeys: the absolute keys of the members  }
{ of REAL projects (I >= 0) that carry the name, which FindOpenServerDoc    }
{ never returns for a bare request. A member is keyed by its listing AND by }
{ the FileName of the document open for it: IsProjectMemberFile compares   }
{ FileName keys, and under a subst drive (or 8.3 / UNC) the project lists   }
{ F:\... while the open document's FileName is H:\... (re-review of       }
{ a0c9da9: keyed by the listing alone, the focused carrier sheet passed as  }
{ "not a member", answered a bare request for its free copy, and a hand-    }
{ edited copy was skipped as unmodified - the c8ee69e defect again).        }
Function DocNameCount(FilePath : String) : Integer;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Member : IServerDocument;
    I, J, ProjCount : Integer;
    Full, WantName, Keys, Key, MemberFile : String;
Begin
    Result := 0;
    DocNameMemberKeys := '';
    WantName := DocNameKey(FilePath);
    If WantName = '' Then Exit;
    Keys := '';
    Workspace := Nil;
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    If Workspace = Nil Then Exit;
    ProjCount := 0;
    Try ProjCount := Workspace.DM_ProjectCount; Except ProjCount := 0; End;
    { I = -1 is the synthetic project that holds the free documents. }
    For I := -1 To ProjCount - 1 Do
    Begin
        Project := Nil;
        Try
            If I < 0 Then Project := Workspace.DM_FreeDocumentsProject
            Else Project := Workspace.DM_Projects(I);
        Except
            Project := Nil;
        End;
        If Project = Nil Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Nil;
            Try Doc := Project.DM_LogicalDocuments(J); Except Doc := Nil; End;
            If Doc = Nil Then Continue;
            Full := '';
            Try Full := Doc.DM_FullPath; Except Full := ''; End;
            If DocNameKey(Full) <> WantName Then Continue;
            Key := ListedDocKey(Full, I, J);
            If (I >= 0) And LooksAbsolutePath(Trim(Full)) Then
            Begin
                If Pos('|' + Key + '|', '|' + DocNameMemberKeys + '|') = 0 Then
                Begin
                    If DocNameMemberKeys <> '' Then DocNameMemberKeys := DocNameMemberKeys + '|';
                    DocNameMemberKeys := DocNameMemberKeys + Key;
                End;
                Member := Nil;
                Try Member := Client.GetDocumentByPath(Full); Except Member := Nil; End;
                MemberFile := ServerDocFileName(Member);
                If LooksAbsolutePath(Trim(MemberFile))
                    And (Pos('|' + DocPathKey(MemberFile) + '|', '|' + DocNameMemberKeys + '|') = 0) Then
                Begin
                    If DocNameMemberKeys <> '' Then DocNameMemberKeys := DocNameMemberKeys + '|';
                    DocNameMemberKeys := DocNameMemberKeys + DocPathKey(MemberFile);
                End;
            End;
            If Pos('|' + Key + '|', '|' + Keys + '|') > 0 Then Continue;
            If Keys <> '' Then Keys := Keys + '|';
            Keys := Keys + Key;
            Result := Result + 1;
        End;
    End;
End;

{ Is this open document one of the project members DocNameCount just listed }
{ (DocNameMemberKeys)? A document with no absolute FileName never is.        }
Function IsProjectMemberFile(Doc : IServerDocument) : Boolean;
Var
    Key : String;
Begin
    Result := False;
    If Doc = Nil Then Exit;
    Key := ServerDocFileName(Doc);
    If Not LooksAbsolutePath(Trim(Key)) Then Exit;
    Result := Pos('|' + DocPathKey(Key) + '|', '|' + DocNameMemberKeys + '|') > 0;
End;

{ Find the open IServerDocument for a path however the caller spelled it,    }
{ and NEVER ITS NAMESAKE.                                                     }
{                                                                              }
{ REVIEW 2026-10-11 of the first version: a free scratch copy of             }
{ multiviewer__MCU_UI.SchDoc has a bare DM_FullPath that usually does not    }
{ resolve, so the walk skipped it and returned the project's same-named      }
{ sheet as the one "name hit". obj_modify on the focused scratch copy then   }
{ marked the REAL carrier sheet and app_save_all wrote it. An absolute       }
{ request for a closed file name-matched an open free copy the same way, and }
{ DocIsModified on a closed project member read that copy's flag.            }
{                                                                              }
{ So the two kinds of request are answered by different rules:               }
{   ABSOLUTE  the exact lookup, accepted only when the document it returns   }
{             has THIS file's key as its FileName (an empty or bare FileName }
{             proves nothing), then a walk that accepts a listed document    }
{             only when its DM_FullPath or its own FileName IS the requested }
{             path (key equality, never a name match), then the focused      }
{             document on the same key equality.                             }
{   BARE      only a FREE document has a bare name (a project member's       }
{             DM_FullPath and DocumentName are absolute), so NO candidate    }
{             that is a member of a real project is ever accepted - not the  }
{             focused document, not the walk's, not the plain lookup's       }
{             (review of c8ee69e: with the carrier sheet focused, the free   }
{             copy's bare listing resolved to the sheet, so the save walk    }
{             skipped a hand-edited copy as "unmodified", and a bare mark    }
{             for an edit to the non-focused copy marked and recorded the    }
{             sheet). Then the FOCUSED document answers when its file name   }
{             matches (ProcessActiveDoc passes the current sheet's           }
{             DocumentName); otherwise the name must be carried by exactly   }
{             ONE listed document (DocNameCount, counted before any lookup)  }
{             and that document must resolve. Several, and the request is    }
{             refused as ambiguous_name.                                     }
{ FindDocVia says which route answered: path, walk, walk_name, focused,      }
{ ambiguous_name or none.                                                    }
Function FindOpenServerDoc(FilePath : String) : IServerDocument;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Cand, Focused : IServerDocument;
    I, J, ProjCount, NameHits : Integer;
    Full, Want, WantName, Got : String;
    Bare : Boolean;
Begin
    Result := Nil;
    FindDocVia := 'none';
    If Trim(FilePath) = '' Then Exit;
    Want := DocPathKey(FilePath);
    WantName := DocNameKey(FilePath);
    Bare := Not LooksAbsolutePath(Trim(FilePath));

    If Bare Then
    Begin
        { Counted FIRST: the same pass lists the project members a bare     }
        { request must never resolve to (DocNameMemberKeys).                 }
        NameHits := DocNameCount(FilePath);
        Focused := FocusedServerDoc(0);
        If (Focused <> Nil) And (DocNameKey(ServerDocFileName(Focused)) = WantName)
            And (Not IsProjectMemberFile(Focused)) Then
        Begin
            Result := Focused;
            FindDocVia := 'focused';
            Exit;
        End;
        If NameHits > 1 Then
        Begin
            FindDocVia := 'ambiguous_name';
            Exit;
        End;
    End
    Else
    Begin
        Try Result := Client.GetDocumentByPath(FilePath); Except Result := Nil; End;
        If Result <> Nil Then
        Begin
            Got := ServerDocFileName(Result);
            If DocPathKey(Got) <> Want Then
                Result := Nil;
        End;
        If Result <> Nil Then
        Begin
            FindDocVia := 'path';
            Exit;
        End;
    End;

    Workspace := Nil;
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    ProjCount := 0;
    If Workspace <> Nil Then
    Begin
        Try ProjCount := Workspace.DM_ProjectCount; Except ProjCount := 0; End;
    End;
    If Workspace <> Nil Then
    Begin
        For I := -1 To ProjCount - 1 Do
        Begin
            Project := Nil;
            Try
                If I < 0 Then Project := Workspace.DM_FreeDocumentsProject
                Else Project := Workspace.DM_Projects(I);
            Except
                Project := Nil;
            End;
            If Project = Nil Then Continue;
            For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                Doc := Nil;
                Try Doc := Project.DM_LogicalDocuments(J); Except Doc := Nil; End;
                If Doc = Nil Then Continue;
                Full := '';
                Try Full := Doc.DM_FullPath; Except Full := ''; End;
                If DocNameKey(Full) <> WantName Then Continue;
                Cand := Nil;
                Try Cand := Client.GetDocumentByPath(Full); Except Cand := Nil; End;
                If Cand = Nil Then Continue;
                Got := ServerDocFileName(Cand);
                If Bare Then
                Begin
                    { The one listed document of this name (NameHits <= 1).   }
                    { A bare lookup may still have resolved some other open   }
                    { file, so its own name must match too, and it must not   }
                    { be a project member.                                    }
                    If (DocNameKey(Got) = WantName) And (Not IsProjectMemberFile(Cand)) Then
                    Begin
                        Result := Cand;
                        FindDocVia := 'walk_name';
                        Exit;
                    End;
                End
                { ITS OWN FileName IS the request, or the LISTING is and the    }
                { document's FileName is an absolute spelling of the same name  }
                { (subst / 8.3 / UNC). An empty or bare FileName proves nothing }
                { (re-review of a0c9da9: the exact lookup rejected exactly that }
                { stranger and the walk accepted it on its listing alone).     }
                { Limit: a same-named file in ANOTHER folder with an absolute  }
                { FileName still passes the listing rule.                       }
                Else If (DocPathKey(Got) = Want)
                    Or ((DocPathKey(Full) = Want) And LooksAbsolutePath(Trim(Got))
                        And (DocNameKey(Got) = WantName)) Then
                Begin
                    Result := Cand;
                    FindDocVia := 'walk';
                    Exit;
                End;
            End;
        End;
    End;

    If Bare Then
    Begin
        { No listed document resolved: the plain lookup, on the same terms. }
        Try Result := Client.GetDocumentByPath(FilePath); Except Result := Nil; End;
        If (Result <> Nil) And ((DocNameKey(ServerDocFileName(Result)) <> WantName)
            Or IsProjectMemberFile(Result)) Then
            Result := Nil;
        If Result <> Nil Then FindDocVia := 'path';
        Exit;
    End;

    Focused := FocusedServerDoc(0);
    If (Focused <> Nil) And (DocPathKey(ServerDocFileName(Focused)) = Want) Then
    Begin
        Result := Focused;
        FindDocVia := 'focused';
    End;
End;

{..............................................................................}
{ THE BRIDGE'S RECORD OF WHAT IT EDITED (BridgeEditedPaths).                   }
{                                                                              }
{ One entry per FILE: KEY*AGE, pipe-separated, where KEY is the DocPathKey of  }
{ a RESOLVED ABSOLUTE path and AGE the file's FileAge when it was marked.      }
{ Review 2026-10-11 of the first version, two rules that came out of it:       }
{                                                                              }
{ * ABSOLUTE KEYS ONLY. It kept the caller's spelling, which for a free sheet  }
{   is a bare name, and a bare entry matched every file of that name: editing  }
{   a scratch copy put the clean carrier sheet on the record and              }
{   app_save_all only_modified rewrote it. NO BARE ENTRY AT ALL (review of    }
{   c8ee69e): one kept "when nothing resolved" carried no age worth checking, }
{   answered for the carrier sheet as soon as its scratch copy closed (the    }
{   2026-10-06 UniqueID churn again), survived a Ctrl+S on it, and could      }
{   never cause the intended file to be written. A mark that resolves nothing }
{   reports found:false with a save_hint instead.                             }
{ * THE ENTRY DIES WITH ANY WRITE. It was cleared only by app_save_all,        }
{   lib_save_library and a close, so the user's Ctrl+S, RefreshSchLibFromDisk, }
{   sch_reload_document and proj_close all left it behind and the next save-all}
{   rewrote a clean file (carrier.SchLib's mtime is the offline symbol cache's }
{   key - the 2026-10-06 harm). The age stamp covers every route, including    }
{   ones outside the bridge: once the file's age differs from the stamp,       }
{   something wrote it after the edit, and the entry is dropped on the read.   }
{   FileAge is a DOS time (2 s): a write in the same tick as the mark is not   }
{   seen, which errs toward one extra write, never toward a lost edit.         }
{..............................................................................}

Function FileAgeOrNone(Path : String) : Integer;
Begin
    Result := -1;
    If Not LooksAbsolutePath(Trim(Path)) Then Exit;
    Try Result := FileAge(Path); Except Result := -1; End;
End;

Function BridgeEntryKey(Entry : String) : String;
Var
    P : Integer;
Begin
    P := Pos('*', Entry);
    If P > 0 Then Result := Copy(Entry, 1, P - 1)
    Else Result := Entry;
End;

Function BridgeEntryStamp(Entry : String) : Integer;
Var
    P : Integer;
Begin
    P := Pos('*', Entry);
    If P > 0 Then Result := StrToIntDef(Copy(Entry, P + 1, Length(Entry) - P), -1)
    Else Result := -1;
End;

{ A record list without one key's entry. }
Function BridgeRecordWithout(List : String; Key : String) : String;
Var
    Remaining, Entry : String;
    P : Integer;
Begin
    Result := '';
    Remaining := List;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Entry := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Entry := Remaining;
            Remaining := '';
        End;
        If Entry = '' Then Continue;
        If BridgeEntryKey(Entry) = Key Then Continue;
        If Result <> '' Then Result := Result + '|';
        Result := Result + Entry;
    End;
End;

{ A pipe list of paths with one more path, once per file. }
Function PathListWith(List : String; Path : String) : String;
Begin
    Result := List;
    If Trim(Path) = '' Then Exit;
    If Pos('|' + DocPathKey(Path) + '|', '|' + DocPathKey(List) + '|') > 0 Then Exit;
    If Result <> '' Then Result := Result + '|';
    Result := Result + Path;
End;

{ Is this file on the record, with no write since the edit? A stale entry is }
{ dropped here, whichever route wrote the file.                             }
Function BridgeEditRecorded(FilePath : String) : Boolean;
Var
    Remaining, Entry, Key, Want : String;
    P : Integer;
Begin
    Result := False;
    If Trim(FilePath) = '' Then Exit;
    Want := DocPathKey(FilePath);
    Remaining := BridgeEditedPaths;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Entry := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Entry := Remaining;
            Remaining := '';
        End;
        Key := BridgeEntryKey(Entry);
        If Key = '' Then Continue;
        If Key <> Want Then Continue;
        If FileAgeOrNone(Key) = BridgeEntryStamp(Entry) Then
            Result := True
        Else
            BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, Key);
        Exit;
    End;
End;

{ Put a file on the record, stamped with its age NOW: a second edit after an }
{ outside save restamps it, so that edit is written too. An ABSOLUTE path   }
{ only; a bare name is refused here, whoever asks.                          }
Procedure RecordBridgeEdit(FilePath : String);
Var
    Key : String;
Begin
    Key := DocPathKey(FilePath);
    If Not LooksAbsolutePath(Key) Then Exit;
    BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, Key);
    If BridgeEditedPaths <> '' Then BridgeEditedPaths := BridgeEditedPaths + '|';
    BridgeEditedPaths := BridgeEditedPaths + Key + '*' + IntToStr(FileAgeOrNone(FilePath));
End;

{ Drop a file once it is on disk or gone: its own key only, so writing one   }
{ file never strips the entry of its namesake.                               }
Procedure ForgetBridgeEdit(FilePath : String);
Begin
    If Trim(FilePath) = '' Then Exit;
    BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, DocPathKey(FilePath));
End;

{ The record's file keys, without their stamps, for a reply. }
Function BridgeRecordKeys(Dummy : Integer) : String;
Var
    Remaining, Entry : String;
    P : Integer;
Begin
    Result := '';
    Remaining := BridgeEditedPaths;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Entry := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Entry := Remaining;
            Remaining := '';
        End;
        If BridgeEntryKey(Entry) = '' Then Continue;
        If Result <> '' Then Result := Result + '|';
        Result := Result + BridgeEntryKey(Entry);
    End;
End;

{ A CLOSE ENDS THE RECORD ENTRY. A closed document holds no edits for a      }
{ save-all to write, and the age stamp cannot see a close that wrote         }
{ nothing (proj_close save=false, a discarding sheet delete, a reload or a   }
{ library refresh whose save was declined): the entry would outlive the      }
{ close and the next save-all would rewrite the file reopened clean (review  }
{ 2026-10-11). Each pipe-separated path whose document is no longer open is  }
{ dropped; one still open keeps its entry (a close that did not take).       }
Procedure ForgetBridgeEditsIfClosed(PathList : String);
Var
    Remaining, OnePath : String;
    P : Integer;
Begin
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            OnePath := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            OnePath := Remaining;
            Remaining := '';
        End;
        If Trim(OnePath) = '' Then Continue;
        If FindOpenServerDoc(OnePath) = Nil Then ForgetBridgeEdit(OnePath);
    End;
End;

{ A project's member paths, pipe-separated, read BEFORE a close (the project }
{ object does not survive it).                                               }
Function ProjectMemberPaths(Project : IProject) : String;
Var
    J : Integer;
    Doc : IDocument;
    Full : String;
Begin
    Result := '';
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(J); Except Doc := Nil; End;
        If Doc = Nil Then Continue;
        Full := '';
        Try Full := Doc.DM_FullPath; Except Full := ''; End;
        If Trim(Full) = '' Then Continue;
        If Result <> '' Then Result := Result + '|';
        Result := Result + Full;
    End;
End;

{ The record's file keys that are IN PathList (pipe-separated paths, keyed by }
{ DocPathKey), for a reply about ONE project: proj_save / proj_close used     }
{ BridgeRecordKeys(0), the whole workspace's record, so a save of project A   }
{ listed project B's and free documents' pending edits as if this save had   }
{ left them unwritten (review of 498e5ca).                                   }
Function BridgeRecordKeysIn(PathList : String) : String;
Var
    Remaining, Key, Wanted : String;
    P : Integer;
Begin
    Result := '';
    If Trim(PathList) = '' Then Exit;
    Wanted := '|' + DocPathKey(PathList) + '|';
    Remaining := BridgeRecordKeys(0);
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Key := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Key := Remaining;
            Remaining := '';
        End;
        If Key = '' Then Continue;
        If Pos('|' + Key + '|', Wanted) = 0 Then Continue;
        If Result <> '' Then Result := Result + '|';
        Result := Result + Key;
    End;
End;

Function MarkDocDirty(FilePath : String) : Boolean;
Var
    ServerDoc, Focused : IServerDocument;
    DiskName, FocusedName : String;
    FocusedMod : Boolean;
    FlagState : Integer;
Begin
    Result := False;
    MarkLastPath := FilePath;
    MarkLastFile := '';
    MarkLastVia := 'none';
    MarkLastFound := False;
    MarkLastModified := False;
    MarkLastFocused := -1;
    MarkLastRecorded := False;
    MarkLastError := '';
    MarkLastFlagRaw := '';
    MarkLastFlagTests := '';
    If Trim(FilePath) = '' Then
    Begin
        MarkLastError := 'no path was given, so no document could be marked';
        Exit;
    End;
    { An absolute path names one file, so it goes on the record before the  }
    { lookup: a mark the lookup misses is then still written by app_save_all }
    { if the document is open, and dropped by it if it is not.               }
    If LooksAbsolutePath(Trim(FilePath)) Then
    Begin
        RecordBridgeEdit(FilePath);
        MarkLastRecorded := True;
    End;

    ServerDoc := FindOpenServerDoc(FilePath);
    MarkLastVia := FindDocVia;
    DiskName := '';
    If ServerDoc <> Nil Then
    Begin
        MarkLastFound := True;
        DiskName := ServerDocFileName(ServerDoc);
        MarkLastFile := DiskName;
        If LooksAbsolutePath(Trim(DiskName)) Then
        Begin
            RecordBridgeEdit(DiskName);
            MarkLastRecorded := True;
            { ONE KEY PER FILE. The caller's spelling went on before the     }
            { lookup; when it names this file another way (a subst drive, an }
            { 8.3 or UNC path) it is dropped, or the sweep would file it as  }
            { bridge_record_closed ("edit is lost") after the DiskName entry }
            { was written.                                                   }
            If LooksAbsolutePath(Trim(FilePath))
                And (DocPathKey(FilePath) <> DocPathKey(DiskName)) Then
                BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, DocPathKey(FilePath));
        End;
        Try
            ServerDoc.SetModified(True);
        Except
            MarkLastError := 'SetModified raised on the document found';
        End;
        { The D1 diagnostic, read before anything else touches the flag:   }
        { the two proven tests only, no compare (review of d198b65). The   }
        { Try is a fault-only backstop so a diagnostic can never abort a   }
        { mark whose edit is already made.                                 }
        Try
            MarkLastFlagRaw := ModifiedFlagRaw(ServerDoc);
            MarkLastFlagTests := ModifiedFlagTests(ServerDoc);
        Except
        End;
        { A READ-BACK: the state, not ModifiedFlagOf's doubt-writes Boolean. }
        { An odd value (2, the field's) is a mark that took; an unreadable   }
        { flag (3) is NOT, and the reply says so instead of claiming it.     }
        FlagState := ModifiedFlagState(ServerDoc);
        MarkLastModified := (FlagState = 1) Or (FlagState = 2);
        If (FlagState = 3) And (MarkLastError = '') Then
            MarkLastError := 'the modified flag could not be read';
        { The focused document, when it is THIS file by key: the object the  }
        { tab's '*' belongs to, which may not be the object the lookup       }
        { returned. Never a namesake - only the same absolute file.          }
        Focused := FocusedServerDoc(0);
        FocusedName := ServerDocFileName(Focused);
        If (Focused <> Nil) And (Trim(DiskName) <> '')
            And (DocPathKey(FocusedName) = DocPathKey(DiskName)) Then
        Begin
            Try Focused.SetModified(True); Except End;
            FlagState := ModifiedFlagState(Focused);
            FocusedMod := (FlagState = 1) Or (FlagState = 2);
            If FocusedMod Then MarkLastFocused := 1 Else MarkLastFocused := 0;
            If FocusedMod Then MarkLastModified := True;
            { The found object's flag was unreadable but the tab's reads      }
            { modified: say WHICH flag could not be read, or the reply       }
            { carries marked_modified true beside a bare "could not be read" }
            { (review of d198b65).                                           }
            If FocusedMod And (MarkLastError = 'the modified flag could not be read') Then
                MarkLastError := 'the found document''s modified flag could not '
                    + 'be read; the focused tab''s flag reads modified';
        End;
    End;
    { Nothing resolved from a bare name: NOTHING goes on the record (a bare }
    { entry names no file - see RecordBridgeEdit); the reply says found:    }
    { false and carries a save_hint.                                        }

    If Not MarkLastFound Then
        MarkLastError := 'no open document matches this path ('
            + MarkLastVia + '); nothing was marked'
    Else If (Not MarkLastModified) And (MarkLastError = '') Then
        MarkLastError := 'the document was found and marked but its modified '
            + 'flag reads False afterwards';
    Result := MarkLastModified;
End;

Procedure MarkDocDirtyByPath(FilePath : String);
Var
    Ignored : Boolean;
Begin
    Ignored := MarkDocDirty(FilePath);
End;

{ THE PATH TO MARK FOR A SHEET A HANDLER RESOLVED ITSELF. When the caller    }
{ named it by an ABSOLUTE path, that path: it names the file edited. Only    }
{ otherwise the sheet's DocumentName (absolute for a project member, a bare  }
{ name for a free document). Review of c8ee69e: sch_mirror_component        }
{ resolved its sheet from doc_path and marked SchDoc.DocumentName, a bare   }
{ name for the free copy, which with the carrier sheet focused marked and   }
{ recorded the carrier sheet.                                               }
Function SchDocMarkPath(DocPath : String; SchDoc : ISch_Document) : String;
Begin
    Result := DocPath;
    If LooksAbsolutePath(Trim(DocPath)) Then Exit;
    Result := '';
    If SchDoc = Nil Then Exit;
    Try Result := SchDoc.DocumentName; Except Result := ''; End;
End;

{ A BARE document path that several listed documents carry. A handler that  }
{ resolves its sheet with SchServer.GetSchDocumentByPath(bare) would edit an }
{ arbitrary namesake, so it refuses AMBIGUOUS_NAME before touching anything. }
Function AmbiguousBareDocName(DocPath : String) : Boolean;
Begin
    Result := False;
    If Trim(DocPath) = '' Then Exit;
    If LooksAbsolutePath(Trim(DocPath)) Then Exit;
    Result := DocNameCount(DocPath) > 1;
End;

{..............................................................................}
{ Read that same flag back. The counterpart to MarkDocDirty, and the one       }
{ definition of "unsaved" for every handler that reports it.                   }
{                                                                              }
{ A path with no resident IServerDocument reports False: a document that is    }
{ not open in the editor holds no unsaved edits. Treat a True as reliable and  }
{ a False as a floor, not a clean bill. App_SaveAll records that Modified does }
{ not always propagate from ProcessControl, which is why that handler measures }
{ file timestamps instead of trusting this.                                    }
{                                                                              }
{ It finds the document the way MarkDocDirty does (FindOpenServerDoc, which    }
{ never answers with a namesake: a closed project member reads False even     }
{ when a same-named free copy is open and modified) and also reads the        }
{ focused document when that is the same file by key.                         }
{..............................................................................}

Function DocIsModified(FilePath : String) : Boolean;
Var
    ServerDoc, Focused : IServerDocument;
    DiskName : String;
Begin
    Result := False;
    If Trim(FilePath) = '' Then Exit;
    ServerDoc := FindOpenServerDoc(FilePath);
    If ServerDoc = Nil Then Exit;
    Try
        If ModifiedFlagOf(ServerDoc) Then Result := True;
    Except
        Result := False;
    End;
    If Result Then Exit;
    DiskName := ServerDocFileName(ServerDoc);
    Focused := FocusedServerDoc(0);
    If (Focused <> Nil) And (Trim(DiskName) <> '')
        And (DocPathKey(ServerDocFileName(Focused)) = DocPathKey(DiskName)) Then
    Begin
        Try
            If ModifiedFlagOf(Focused) Then Result := True;
        Except
            Result := False;
        End;
    End;
End;

{..............................................................................}
{ CHECK FIRST, DO NOT CATCH. Non-raising pre-checks for the calls that raise  }
{ on an ordinary bad input. Altium's script engine shows an exception as a    }
{ modal BEFORE the surrounding Try/Except runs, so a Try around one of these  }
{ calls is not a guard. Each answers from the DM layer, the Client's own      }
{ document list or the file system, and none references PCBServer or         }
{ SchServer, so none can load (or fail to find) an editor server.             }
{..............................................................................}

{ The focused document's DM kind is Kind ('PCB', 'PCBLIB', 'SCH', 'SCHLIB',   }
{ ...; case-insensitive). False with no workspace or nothing focused. Use it  }
{ before a PCBServer.GetCurrentPCBLibrary / GetCurrentPCBBoard that no        }
{ successful board lookup precedes: merely referencing PCBServer when no PCB  }
{ document was opened this session is the uncatchable 'Undeclared            }
{ identifier' modal GetPCBBoardAnywhere documents.                           }
Function FocusedDocKindIs(Kind : String) : Boolean;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
Begin
    Result := False;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Doc := Workspace.DM_FocusedDocument;
    If Doc = Nil Then Exit;
    Result := UpperCase(Doc.DM_DocumentKind) = UpperCase(Kind);
End;

{ DOES THIS FILE LOOK LIKE SOMETHING Client.OpenDocument CAN LOAD? For a     }
{ document that is NOT open only (UnopenedFileUnloadable asks that first: an }
{ open document is answered from memory and its file is never read).        }
{ LIVE 2026-10-11 (bridge 2026.10.11.02, AD 26.10.1.5, probe P6b): a 0-byte  }
{ scratch.PcbDoc handed to OpenDocument('PCB', ...) put up Altium's OWN      }
{ modal 'Warning - File type not recognised.' - not an exception, so no      }
{ Except and no counter saw it - blocked the loop ~13 s, and after OK handed }
{ back a NON-Nil blank document: pcb_place_components placed its part into   }
{ it and app_get_preferences reported its settings as the board's.           }
{ False for: no file; a file that cannot be read; an empty file (nothing     }
{ decodes); and, for a .PcbDoc or .SchDoc, a head that is neither an OLE     }
{ compound file nor the ASCII record form. Every other type (.Pcb, .OutJob)  }
{ is judged on "readable and not empty" only.                                }
{ THE READ is ReadFileContent's, construct for construct (that function sits }
{ after the callers of this one in the unit): TStringList.LoadFromFile goes  }
{ through a TFileStream, so a locked or vanished file raises an ordinary    }
{ EFOpenError the Except takes, retried for a transient lock (Defender). NOT }
{ AssignFile / Reset: the RTL's EInOutError is a modal BEFORE the Except.    }
{ THE HEAD is text decoded through the ANSI code page, so the OLE magic      }
{ D0 CF 11 E0 A1 B1 1A E1 is not compared byte for byte (D0 CF decodes to one }
{ or two characters, depending on the code page). Its third byte, 0x11, is   }
{ ASCII and below every DBCS trail byte, so it decodes to Chr(17) in every   }
{ code page and lands within the first three characters: Chr(17) within the  }
{ first four is the OLE test. The ASCII form starts every record with '|'    }
{ ('|RECORD=' on a board, '|HEADER=' on a sheet). A file whose first byte is }
{ NUL may decode to nothing: no board or sheet starts with one.              }
{ CONSERVATIVE the other way too: a raise on every attempt is "cannot be     }
{ read" (Altium could not have read it either, and the box is the cost of   }
{ guessing wrong); Result is assigned from locals only, last (the            }
{ delphiscript_result_clobber note on ReadFileContent).                     }
{ KNOWN LIMIT: the OLE test proves only that the file is SOME compound      }
{ document, not that it is a board or a sheet. A .PcbLib, .SchDoc or .SchLib }
{ renamed .PcbDoc (or the reverse) passes and reaches OpenDocument, which is }
{ the same kind mismatch Altium answers with its own box (the               }
{ BoardPathLibraryRefusal case); .Pcb and .OutJob are judged on extension + }
{ "readable and not empty" only. Telling them apart would need the OLE      }
{ root directory's storage names - a binary-safe read with live precedent,  }
{ which TStringList is not. Not a regression: these opened the same way     }
{ before 498e5ca.                                                            }
{ NOT YET RUN LIVE ON A HEALTHY CLOSED BINARY FILE: ReadFileContent's live   }
{ precedent is small text files. RELEASE_VERIFICATION's 2026.10.11.03       }
{ re-check opens a closed healthy binary and ASCII .PcbDoc / .SchDoc first  }
{ (positive controls, before P6b): a BOARD_FILE_UNREADABLE or a failed      }
{ sheet on a healthy file means this read or the head test misjudged it.    }
Function FileLooksLoadable(Path : String) : Boolean;
Var
    SL : TStringList;
    Content, Head, Ext : String;
    Attempt : Integer;
    Ok, Looks : Boolean;
Begin
    Result := False;
    Looks := False;
    If Trim(Path) = '' Then Exit;
    If Not FileExists(Path) Then Exit;
    Content := '';
    Ok := False;
    Attempt := 0;
    While (Attempt < 12) And (Not Ok) Do
    Begin
        Inc(Attempt);
        SL := TStringList.Create;
        Try
            Try
                SL.LoadFromFile(Path);
                Content := SL.Text;
                Ok := True;
            Except
                Ok := False;
            End;
        Finally
            SL.Free;
        End;
        If Not Ok Then Sleep(15);
    End;
    Head := Copy(Content, 1, 8);
    If Ok And (Head <> '') Then
    Begin
        Ext := UpperCase(ExtractFileExt(Trim(Path)));
        If (Ext = '.PCBDOC') Or (Ext = '.SCHDOC') Then
            Looks := (Pos(Chr(17), Copy(Head, 1, 4)) > 0) Or (Copy(Head, 1, 1) = '|')
        Else
            Looks := True;
    End;
    Result := Looks;
End;

{ A document Client.OpenDocument can be handed without raising: one already  }
{ open (GetDocumentByPath answers, which covers an unsaved new document that }
{ has no file yet) or one on disk. '' is never present.                      }
Function DocumentPresent(Path : String) : Boolean;
Var
    Doc : IServerDocument;
Begin
    Result := False;
    If Trim(Path) = '' Then Exit;
    { Fault-only backstop (not counted), as every other lookup in this unit: }
    { this runs in front of OpenDocument calls that used to sit inside a     }
    { Try, so a lookup that raised on an odd path must not escape them now.  }
    Doc := Nil;
    Try Doc := Client.GetDocumentByPath(Path); Except Doc := Nil; End;
    If Doc <> Nil Then
        Result := True
    Else
        Result := FileExists(Path);
End;

{ A file OpenDocument must NOT be handed: not open (GetDocumentByPath answers }
{ Nil), on disk, and FileLooksLoadable says no (empty, unreadable, or not    }
{ the file type its extension claims). False whenever the question does not }
{ arise - open already (answered from memory, the file is never read), or   }
{ not on disk (DocumentPresent and the callers' own tests judge that) - so  }
{ a caller that adds this test can only skip an open that would have put up }
{ Altium's 'File type not recognised.' box and then a blank document.       }
Function UnopenedFileUnloadable(Path : String) : Boolean;
Var
    Doc : IServerDocument;
Begin
    Result := False;
    If Trim(Path) = '' Then Exit;
    Doc := Nil;
    Try Doc := Client.GetDocumentByPath(Path); Except Doc := Nil; End;
    If Doc <> Nil Then Exit;
    If Not FileExists(Path) Then Exit;
    Result := Not FileLooksLoadable(Path);
End;

{ Client.OpenDocument(Kind, Path) only when the document is present; Nil     }
{ otherwise, which is what every caller already does on an OpenDocument that }
{ returned Nil or raised. The Try stays as the backstop for a GENUINE fault  }
{ (a file that is there but will not load). A file present on disk that is  }
{ empty or not its claimed type answers Nil too, before the open: Altium's  }
{ answer to one is its own uncounted box and then a blank document (live    }
{ 2026-10-11, P6b; UnopenedFileUnloadable). Callers: proj_load_sheets       }
{ ('SCH', a 0-byte member is counted failed) and proj_run_outjob.           }
Function OpenDocumentIfPresent(Kind : String; Path : String) : IServerDocument;
Begin
    Result := Nil;
    If Not DocumentPresent(Path) Then Exit;
    If UnopenedFileUnloadable(Path) Then Exit;
    Try
        Result := Client.OpenDocument(Kind, Path);
    Except
        Result := Nil;
    End;
End;

{ Path names a PCB document that OpenDocument('PCB', Path) can take: a       }
{ .PcbDoc (or a legacy .Pcb) that is open or on disk. False sends the        }
{ callers down their existing 'ServerDoc = Nil' route (fall back / Continue).}
Function PcbPathOpenable(Path : String) : Boolean;
Var
    Ext : String;
Begin
    Result := False;
    Ext := UpperCase(ExtractFileExt(Trim(Path)));
    If (Ext <> '.PCBDOC') And (Ext <> '.PCB') Then Exit;
    Result := DocumentPresent(Path);
End;

{ Whether a PCB or PCB-library document is open now, told WITHOUT ever       }
{ referencing PCBServer (the reference itself is what raises when the PCB    }
{ editor server is not loaded). A project member counts only while it is     }
{ open (GetDocumentByPath answers: a member can be listed and closed); a     }
{ free document is open by definition, and its DM_FullPath is often a bare   }
{ name GetDocumentByPath misses, so its kind alone counts.                   }
{ NOT a test of server registration: a board opened and closed again earlier }
{ in the session leaves the server loaded and this False. A caller that      }
{ gates on it must accept losing that case.                                  }
Function PcbEditorDocOpen(Dummy : Integer) : Boolean;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SrvDoc : IServerDocument;
    Kind, Path : String;
    P, I : Integer;
Begin
    Result := False;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    For P := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(P);
        If Project = Nil Then Continue;
        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            Kind := UpperCase(Doc.DM_DocumentKind);
            If (Kind <> 'PCB') And (Kind <> 'PCBLIB') Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path = '' Then Continue;
            SrvDoc := Nil;
            Try SrvDoc := Client.GetDocumentByPath(Path); Except SrvDoc := Nil; End;
            If SrvDoc <> Nil Then
            Begin
                Result := True;
                Exit;
            End;
        End;
    End;
    Project := Nil;
    Try Project := Workspace.DM_FreeDocumentsProject; Except End;
    If Project = Nil Then Exit;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        Kind := UpperCase(Doc.DM_DocumentKind);
        If (Kind = 'PCB') Or (Kind = 'PCBLIB') Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{ Can PCBServer.GetCurrentPCBLibrary answer a library right now? Asked       }
{ WITHOUT naming PCBServer, whose mere reference raises (a modal) when the    }
{ PCB editor server has not loaded this session. CONSERVATIVE: False only on  }
{ evidence that the answer is Nil anyway -                                   }
{ * the focused document is known and is not a PcbLib (the call answers for  }
{   the FOCUSED library only), or                                           }
{ * no PcbLib or PcbDoc is open anywhere (nothing for it to answer).         }
{ A Nil DM focus alone is NOT evidence: DM_FocusedDocument reads Nil right   }
{ after a programmatic create or open while the library IS current, so that  }
{ case falls through to the PcbEditorDocOpen test and keeps today's call.    }
Function PcbLibMayBeCurrent(Dummy : Integer) : Boolean;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    Kind : String;
Begin
    Result := False;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
    Begin
        Kind := '';
        Try Kind := Doc.DM_DocumentKind; Except Kind := ''; End;
        If (Kind <> '') And (UpperCase(Kind) <> 'PCBLIB') Then Exit;
    End;
    Result := PcbEditorDocOpen(0);
End;

{ PcbLibMayBeCurrent for a handler that was given (or defaulted to) LibPath. }
{ Also True when a .PcbLib is OPEN AT LibPath (GetDocumentByPath answers),   }
{ whatever the DM focus says: an open PcbLib is a PCB-server document, so   }
{ the server is loaded and naming PCBServer cannot raise. That covers a DM  }
{ focus left STALE (not Nil) by a programmatic CloseObject / OpenObject -   }
{ the lib_reload_library case, RELEASE_VERIFICATION probe P32 - which      }
{ PcbLibMayBeCurrent alone would refuse although the lookup works today.   }
{ Never stricter than PcbLibMayBeCurrent, so swapping it in can only keep  }
{ a call that works today. LibPath '' (or not a .PcbLib) adds nothing.     }
Function PcbLibMayBeCurrentAt(LibPath : String) : Boolean;
Var
    SrvDoc : IServerDocument;
Begin
    Result := True;
    If UpperCase(ExtractFileExt(Trim(LibPath))) = '.PCBLIB' Then
    Begin
        SrvDoc := Nil;
        Try SrvDoc := Client.GetDocumentByPath(LibPath); Except SrvDoc := Nil; End;
        If SrvDoc <> Nil Then Exit;
    End;
    Result := PcbLibMayBeCurrent(0);
End;

{..............................................................................}
{ GetPCBBoardAnywhere - Focus-independent PCB board lookup.                    }
{..............................................................................}

Function GetPCBBoardAnywhere(Dummy : Integer): IPCB_Board;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    PrevView : IServerDocumentView;
    PrevDoc : IServerDocument;
    Path : String;
    I, P : Integer;
Begin
    Result := Nil;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;

    { Fast path: a PCB tab is focused, so the PCB editor server is loaded    }
    { and PCBServer.GetCurrentPCBBoard resolves. Gate on the focused doc's   }
    { kind, read through the DM layer (which never loads a server). If no    }
    { PcbDoc has been opened this session the PCB server module is not       }
    { registered, and merely REFERENCING PCBServer.GetCurrentPCBBoard raises }
    { an uncatchable "Undeclared identifier" modal (late-bound against the   }
    { absent server) that Try/Except cannot swallow and that hangs the       }
    { polling loop. So only touch PCBServer when a PCB is actually focused.  }
    Doc := Workspace.DM_FocusedDocument;
    If (Doc <> Nil) And (UpperCase(Doc.DM_DocumentKind) = 'PCB') Then
    Begin
        Result := PCBServer.GetCurrentPCBBoard;
        If Result <> Nil Then Exit;
    End;

    { Fallback: no PCB tab is focused (the user is on a schematic, a library }
    { or the script project). Walk EVERY project open in the workspace - not }
    { just the focused one, which is often the script project with no PcbDoc }
    { - find the first PcbDoc, and open it. Opening the document loads the   }
    { PCB server and moves focus to it; a non-nil ServerDoc from            }
    { OpenDocument('PCB', ...) proves the server is now registered, so the   }
    { GetCurrentPCBBoard read below is safe. The IPCB_Board pointer stays    }
    { valid after we restore the user's previous view.                       }
    PrevView := Nil;
    Try
        If (Client <> Nil) Then PrevView := Client.GetCurrentView;
    Except End;

    For P := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(P);
        If Project = Nil Then Continue;

        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path = '' Then Continue;
            If (UpperCase(Doc.DM_DocumentKind) <> 'PCB') And
               (Pos('.PCBDOC', UpperCase(Path)) <= 0) Then Continue;

            { CHECK FIRST: a project can list a board that is not on disk   }
            { (moved, renamed, a project copied without it), and handing     }
            { OpenDocument that dead path raised - a modal before the Except }
            { - on every read that reached this walk. An ABSOLUTE listing    }
            { that is neither open nor on disk cannot be opened, so it is    }
            { skipped with the same Continue a Nil ServerDoc takes. A bare   }
            { listing is not judged (GetDocumentByPath misses those) and     }
            { keeps today's attempt. The Except is counted, so a residual    }
            { raise shows in a live run.                                     }
            If LooksAbsolutePath(Path) And (Not DocumentPresent(Path)) Then Continue;
            { A LISTED BOARD THAT IS ON DISK BUT CANNOT BE ONE (0 bytes,      }
            { unreadable, not a board file) is skipped the same way: live    }
            { 2026-10-11 (P6b) OpenDocument put up Altium's own uncounted    }
            { 'File type not recognised.' box for a 0-byte listing, then     }
            { handed back a blank document whose settings app_get_preferences }
            { reported. Only a board that is NOT open is judged (an open one }
            { is answered from memory). The walk itself stays for the reads: }
            { opening a real board in a background tab is what 39.3 item 4   }
            { measured and passed (focus never left the Home page).          }
            If UnopenedFileUnloadable(Path) Then Continue;

            ServerDoc := Nil;
            Try
                ServerDoc := Client.OpenDocument('PCB', Path);
            Except
                RecordCaughtException('GetPCBBoardAnywhere:OpenDocument');
            End;
            If ServerDoc = Nil Then Continue;

            Try Client.ShowDocument(ServerDoc); Except End;
            Try Result := PCBServer.GetCurrentPCBBoard; Except End;
            If Result <> Nil Then Break;
        End;

        If Result <> Nil Then Break;
    End;

    { Restore the user's prior view so the focus blink is invisible.         }
    { CHECK FIRST: a view that is not a document's (the Home page, a viewer  }
    { or web tab) has no OwnerDocument, and ShowDocument(Nil) raised - a     }
    { modal before the Except - on any fallback that ran from such a view.   }
    { A Nil owner now restores nothing, which is what the Except left. The   }
    { owner READ keeps its Try, counted: whether it can raise on such a view }
    { is not measured (RELEASE_VERIFICATION section 39).                     }
    If (PrevView <> Nil) And (Client <> Nil) Then
    Begin
        PrevDoc := Nil;
        Try
            PrevDoc := PrevView.OwnerDocument;
        Except
            RecordCaughtException('GetPCBBoardAnywhere:OwnerDocument');
            PrevDoc := Nil;
        End;
        If PrevDoc <> Nil Then
            Try Client.ShowDocument(PrevDoc); Except End;
    End;
End;

{..............................................................................}
{ A board_path that names a PCB LIBRARY. Client.OpenDocument('PCB', <a       }
{ .PcbLib>) does not raise and does not answer Nil: Altium shows its OWN     }
{ modal 'Warning - File type not recognised.' inside the call (live          }
{ 2026-10-11, AD 26.10.1.5; the same at eec334c). That box is not an         }
{ exception, so no Except and no counter ever sees it, and the polling loop  }
{ waits on it. Falling back to another board instead would edit a board the  }
{ caller did not name, so the handlers that take board_path refuse first     }
{ with BOARD_PATH_IS_LIBRARY and this message; '' when the path is not a     }
{ library. By extension only: a library is the file type, open or not.       }
Function BoardPathLibraryRefusal(Path : String) : String;
Begin
    Result := '';
    If UpperCase(ExtractFileExt(Trim(Path))) <> '.PCBLIB' Then Exit;
    Result := 'board_path names a PCB library, not a board: ' + Path
        + '. Nothing was opened or changed. A library is edited with the lib_ '
        + 'tools (lib_create_footprint, lib_add_footprint_pad, '
        + 'lib_get_footprints and the rest), which take library_path; '
        + 'board_path takes a .PcbDoc.';
End;

{ A board_path that names a board FILE nothing can load: a .PcbDoc (or .Pcb) }
{ that is not open, is on disk, and is empty, unreadable, or not a compound/  }
{ ASCII file (UnopenedFileUnloadable; a compound file of another kind is NOT }
{ caught - FileLooksLoadable's known limit). Live 2026-10-11 (2026.10.11.02, }
{ probe P6b): a 0-byte scratch.PcbDoc reached OpenDocument('PCB', ...),     }
{ Altium put up its OWN uncounted 'Warning - File type not recognised.' box,}
{ and after OK handed back a NON-Nil blank document that                    }
{ pcb_place_components then placed its part into. Falling back to another   }
{ board would edit a board the caller did not name, so the handlers that    }
{ take board_path refuse first with BOARD_FILE_UNREADABLE and this message; }
{ '' when the path is not such a file (an open board, a missing path - the  }
{ fallback's case - or a loadable file).                                    }
Function BoardPathFileRefusal(Path : String) : String;
Var
    Ext : String;
Begin
    Result := '';
    Ext := UpperCase(ExtractFileExt(Trim(Path)));
    If (Ext <> '.PCBDOC') And (Ext <> '.PCB') Then Exit;
    If Not UnopenedFileUnloadable(Path) Then Exit;
    Result := 'board_path names a file that cannot be loaded as a board: '
        + Path + ' is empty, could not be read, or is neither a compound '
        + '(OLE) nor an ASCII Altium file. Nothing was opened or changed. Altium answers '
        + 'such a file with its own "File type not recognised." box and then '
        + 'a blank document, which an edit would have landed in. Check the '
        + 'path, or name a board that is open (pcb_focus_board).';
End;

{ ResolvePCBBoard - get a SPECIFIC board by path when several PcbDocs are     }
{ open. GetPCBBoardAnywhere returns the focused/current board, which is the   }
{ WRONG one when the caller meant a different (e.g. newly created) board.     }
{ PCBServer.GetPCBBoardByPath is undeclared on this build, so we open + show  }
{ the target (which focuses it) and then read GetCurrentPCBBoard. Empty path  }
{ falls back to GetPCBBoardAnywhere.                                          }
{..............................................................................}

Function ResolvePCBBoard(Path : String) : IPCB_Board;
Var
    ServerDoc : IServerDocument;
    Ext : String;
Begin
    Result := Nil;
    If Path = '' Then
    Begin
        Result := GetPCBBoardAnywhere(0);
        Exit;
    End;
    { CHECK FIRST, then open. A board_path that names no PCB-server         }
    { document (a .SchDoc, a .txt) or an absolute path that is neither open }
    { nor on disk takes the fallback below without calling OpenDocument:    }
    { the call raised on such a path (a modal before the Except) or handed  }
    { back a non-PCB document, after which reading PCBServer could be the   }
    { uncatchable 'Undeclared identifier' GetPCBBoardAnywhere describes.    }
    { CONSERVATIVE: the skip is the same GetPCBBoardAnywhere(0) a Nil       }
    { ServerDoc takes, and a bare name is not judged (it keeps today's      }
    { attempt), so only an open that could not have produced a board is    }
    { skipped.                                                              }
    { A .PcbLib NEVER reaches OpenDocument('PCB', ...): Altium answers it   }
    { with its own 'File type not recognised.' message box, which is not an }
    { exception (live 2026-10-11; see BoardPathLibraryRefusal). The         }
    { handlers refuse such a path before calling this; here it answers Nil  }
    { rather than the fallback, because the fallback would edit a DIFFERENT }
    { board than the one named.                                             }
    Ext := UpperCase(ExtractFileExt(Trim(Path)));
    If Ext = '.PCBLIB' Then Exit;
    If ((Ext <> '.PCBDOC') And (Ext <> '.PCB')) Or
       (LooksAbsolutePath(Path) And (Not DocumentPresent(Path))) Then
    Begin
        Result := GetPCBBoardAnywhere(0);
        Exit;
    End;
    { A board FILE that is on disk and cannot be loaded (0 bytes,           }
    { unreadable, not a board file) never reaches OpenDocument either: live }
    { 2026-10-11 (P6b) Altium answered a 0-byte .PcbDoc with its own        }
    { uncounted box and then a blank document that got edited. The handlers }
    { refuse it first (BoardPathFileRefusal, BOARD_FILE_UNREADABLE); here   }
    { it answers Nil, never the fallback, because the fallback would edit a }
    { DIFFERENT board than the one named.                                   }
    If UnopenedFileUnloadable(Path) Then Exit;
    ServerDoc := Nil;
    Try
        ServerDoc := Client.OpenDocument('PCB', Path);
    Except
        RecordCaughtException('ResolvePCBBoard:OpenDocument');
    End;
    If ServerDoc = Nil Then
    Begin
        { Path didn't resolve to a loadable PcbDoc; fall back rather than    }
        { silently returning Nil and erroring the whole call.                }
        Result := GetPCBBoardAnywhere(0);
        Exit;
    End;
    Try Client.ShowDocument(ServerDoc); Except End;
    Try Result := PCBServer.GetCurrentPCBBoard; Except End;
    If Result = Nil Then Result := GetPCBBoardAnywhere(0);
End;

{..............................................................................}
{ GetPCBBoardForMutation - the board an EDIT is allowed to touch.              }
{                                                                              }
{ GetPCBBoardAnywhere WANDERS, and that is correct for a read. When no PcbDoc  }
{ is focused it walks every open project, opens the first board it finds,      }
{ reads it, and restores the previous view so the focus change is invisible.   }
{ For a query that is a convenience. For a DELETE it is a misfire: with a      }
{ library focused and two boards open, obj_delete would remove primitives from }
{ whichever board the walk reached first, and hide the fact that it had        }
{ switched documents to do it.                                                 }
{                                                                              }
{ So an edit gets a board only when the target is UNAMBIGUOUS: a PcbDoc is     }
{ focused, or exactly one is open anywhere. Two open and none focused is       }
{ refused, and the refusal names them, because picking one is a guess the      }
{ caller has to make rather than one this should make silently.                }
{                                                                              }
{ Why carries the reason when the Result is Nil.                               }
{..............................................................................}

Function GetPCBBoardForMutation(Var Why : String) : IPCB_Board;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Path, Candidates, OnePath : String;
    I, P, Found : Integer;
Begin
    Result := Nil;
    Why := '';

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Why := 'No workspace is open.';
        Exit;
    End;

    { A focused PcbDoc is unambiguous, whatever else is open. }
    Doc := Workspace.DM_FocusedDocument;
    If (Doc <> Nil) And (UpperCase(Doc.DM_DocumentKind) = 'PCB') Then
    Begin
        Result := PCBServer.GetCurrentPCBBoard;
        If Result <> Nil Then Exit;
    End;

    Found := 0;
    Candidates := '';
    OnePath := '';
    For P := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(P);
        If Project = Nil Then Continue;
        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path = '' Then Continue;
            If (UpperCase(Doc.DM_DocumentKind) <> 'PCB') And
               (Pos('.PCBDOC', UpperCase(Path)) <= 0) Then Continue;
            Found := Found + 1;
            OnePath := Path;
            If Candidates <> '' Then Candidates := Candidates + ', ';
            Candidates := Candidates + Path;
        End;
    End;

    If Found = 0 Then
    Begin
        Why := 'No PCB document is open, so there is nothing to edit.';
        Exit;
    End;

    If Found > 1 Then
    Begin
        Why := 'This edits a board, and ' + IntToStr(Found)
            + ' are open with none of them focused: ' + Candidates
            + '. Refusing rather than picking one. Focus the board you '
            + 'mean, or name it with board_path. A library being in front '
            + 'does NOT make this edit apply to the library.';
        Exit;
    End;

    { Exactly one board open. Opening it is safe because there is no other }
    { one it could have meant.                                             }
    Result := ResolvePCBBoard(OnePath);
    If Result = Nil Then
        Why := 'The only open board, ' + OnePath + ', could not be opened.';
End;

{ THE OPEN DOCUMENT BEHIND ONE LISTED DOCUMENT. A project member's absolute   }
{ DM_FullPath goes straight to GetDocumentByPath. A free document's is often  }
{ a BARE name, which the plain lookup has been seen not to resolve; review    }
{ 2026-10-11 found that the pass then never attempted the free scratch copy   }
{ or library the field run edited, silently. So a bare name goes through      }
{ FindOpenServerDoc (the focused document by name, else the one listed        }
{ document of that name - and NEVER a member of a real project, or a focused  }
{ carrier sheet would answer for its free copy: review of c8ee69e found the   }
{ copy then skipped as unmodified on the sheet's flag, its real edit dropped  }
{ silently), and when that refuses (ambiguous) or misses, the                 }
{ answer is Nil: NO GUESS. A plain GetDocumentByPath on a bare name can       }
{ return a namesake, and the walk would then file the namesake's write under  }
{ this listing's record entry. SaveOneDocByDocRef lists what it could not     }
{ reach (free_documents_not_reached); a bridge-edited one is written by       }
{ SaveBridgeRecordSweep from its absolute key.                                }
{ An absolute listing accepts the exact lookup's answer only on the walk's   }
{ terms (FindOpenServerDoc): its FileName is the listing, or an absolute     }
{ spelling of the same name. A document with an empty or bare FileName is a  }
{ stranger and answers Nil - app_save_all wrote such a stranger and filed    }
{ the write under the member's path (re-review of a0c9da9). Inlined rather   }
{ than calling FindOpenServerDoc, whose walk would make the pass quadratic.  }
Function ListedServerDoc(Doc : IDocument) : IServerDocument;
Var
    Full, Got : String;
Begin
    Result := Nil;
    If Doc = Nil Then Exit;
    Full := '';
    Try Full := Doc.DM_FullPath; Except Full := ''; End;
    If Trim(Full) = '' Then Exit;
    If LooksAbsolutePath(Trim(Full)) Then
    Begin
        Try Result := Client.GetDocumentByPath(Full); Except Result := Nil; End;
        If Result <> Nil Then
        Begin
            Got := ServerDocFileName(Result);
            If (DocPathKey(Got) <> DocPathKey(Full))
                And ((Not LooksAbsolutePath(Trim(Got)))
                    Or (DocNameKey(Got) <> DocNameKey(Full))) Then
                Result := Nil;
        End;
        Exit;
    End;
    Result := FindOpenServerDoc(Full);
End;

{ COULD A WRITE NOW LEAVE THIS AGE WHERE IT IS? FileAge is a DOS time with  }
{ 2 s resolution, so only a file last written inside the current tick (4 s  }
{ allowed, for the tick boundary and the save's own duration) can be written }
{ again without its age moving. A file last written longer ago than that     }
{ MOVES on any real write, so an age that stayed put there is a write that   }
{ did not happen, whatever the modified flag says. -1 (no file) never is.    }
Function AgeIsThisTick(Age : Integer) : Boolean;
Begin
    Result := False;
    If Age = -1 Then Exit;
    Try
        Result := Age >= DateTimeToFileDate(Now - (4.0 / 86400.0));
    Except
        Result := False;
    End;
End;

{ WRITE ONE DOCUMENT AND FILE THE OUTCOME. Shared by the walk and the record }
{ sweep. On disk only if the FILE moved; then the record lets go.            }
{ When it did not move and the write was made for the record (ByRecord):     }
{   bridge_record_same_tick   the file's previous write was inside the       }
{                             current 2 s FileAge tick (AgeIsThisTick) AND   }
{                             the flag reads clean after the save: probably  }
{                             written, its age just could not move. The      }
{                             entry stays, so the worst case is one more     }
{                             write by the next save-all.                    }
{   bridge_record_not_written anything else: the flag still reads modified   }
{                             (the editor declined - an active command does  }
{                             that), or the file was last written before     }
{                             this tick, so a real write would have moved    }
{                             it. Review 2026-10-11: the flag alone cannot   }
{                             tell, because on the field documents it never  }
{                             registered at all, so every declined save read }
{                             clean and would have been called written.      }
{ Returns whether the file moved.                                           }
Function WriteDocAndNote(ServerDoc : IServerDocument; DiskPath : String;
    DocPath : String; InRecord : Boolean; ByRecord : Boolean) : Boolean;
Var
    AgeBefore, AgeAfter : Integer;
    Moved, StillMod : Boolean;
Begin
    Result := False;
    If ServerDoc = Nil Then Exit;
    SaveAttempts := SaveAttempts + 1;
    SaveAttemptedKeys := PathListWith(SaveAttemptedKeys, DocPathKey(DiskPath));
    AgeBefore := -1;
    Try AgeBefore := FileAge(DiskPath); Except AgeBefore := -1; End;
    Try ServerDoc.SetModified(True); Except End;
    Try ServerDoc.DoFileSave(''); Except End;
    AgeAfter := -1;
    Try AgeAfter := FileAge(DiskPath); Except AgeAfter := -1; End;
    Moved := (AgeAfter <> -1) And (AgeAfter <> AgeBefore);
    Result := Moved;
    If Not InRecord Then Exit;
    If Moved Then
    Begin
        ForgetBridgeEdit(DocPath);
        ForgetBridgeEdit(DiskPath);
    End;
    If Not ByRecord Then Exit;
    If Moved Then
        SaveBridgeRecordPaths := PathListWith(SaveBridgeRecordPaths, DiskPath)
    Else
    Begin
        StillMod := True;
        Try StillMod := ModifiedFlagOf(ServerDoc); Except StillMod := True; End;
        If StillMod Or (Not AgeIsThisTick(AgeBefore)) Then
            SaveBridgeRecordUnwritten := PathListWith(SaveBridgeRecordUnwritten, DiskPath)
        Else
            SaveBridgeRecordSameTick := PathListWith(SaveBridgeRecordSameTick, DiskPath);
    End;
End;

{ THE SAVE WALKS NEVER TOUCH A SCRIPT UNIT (live 2026-10-11, bridge         }
{ 2026.10.11.02). Workspace.DM_Projects holds the bridge's OWN script       }
{ project, Altium_API.PrjScr, and Altium reopens a script unit (Library.pas }
{ that run) after a script error: every save-all reply listed               }
{ ...\EDA Agent\scripts\Library.pas in skipped_unmodified, and a save-all   }
{ WITHOUT only_modified would have run SetModified(True) + DoFileSave on it }
{ (WriteDocAndNote), rewriting the running bridge's source. Script units    }
{ also padded documents_seen and still_dirty.                               }
{ IsScriptProject: a project whose file is a .PrjScr. SaveAllDirty,         }
{ SaveProjectMembers, CountDirtyDocuments and WorkspaceDocPaths skip it.    }
Function IsScriptProject(Project : IProject) : Boolean;
Var
    Path : String;
Begin
    Result := False;
    If Project = Nil Then Exit;
    Path := '';
    Try Path := Project.DM_ProjectFullPath; Except Path := ''; End;
    Result := UpperCase(ExtractFileExt(Trim(Path))) = '.PRJSCR';
End;

{ IsSaveableDesignDoc: every document EXCEPT A SCRIPT UNIT (review of        }
{ 498e5ca). The first N2 fix was an allow-list of five kinds (SCH, SCHLIB,   }
{ PCB, PCBLIB, OUTPUTJOB); it stopped the script-unit write N2 measured, but }
{ it also dropped every OTHER kind from the walks: an open ActiveBOM .BomDoc }
{ (often reading modified just from being open), a Draftsman .PcbDwf, a       }
{ harness or a text file was no longer written, listed or counted. So        }
{ app_save_all answered still_dirty 0 with one still dirty, and proj_close   }
{ save=true closed with it dirty - Altium's OWN "Save changes?" prompt,      }
{ which blocks the polling loop uncounted (fatal class 2). Before 498e5ca    }
{ the walk wrote those documents, and it does again.                         }
{ IsScriptUnitDoc is the deny test, narrowed to what N2 measured: the kind   }
{ EditScriptDS (Library.pas read it live), or a script / form extension     }
{ (.pas .bas .vbs .js .tcl .dfm) whatever the kind reads. A script PROJECT   }
{ (.PrjScr) is skipped whole by IsScriptProject before any of its members   }
{ is looked at.                                                              }
Function IsScriptUnitDoc(Doc : IDocument) : Boolean;
Var
    Kind, Path, Ext : String;
Begin
    Result := False;
    If Doc = Nil Then Exit;
    Kind := '';
    Try Kind := UpperCase(Trim(Doc.DM_DocumentKind)); Except Kind := ''; End;
    If Kind = 'EDITSCRIPTDS' Then
    Begin
        Result := True;
        Exit;
    End;
    Path := '';
    Try Path := Doc.DM_FullPath; Except Path := ''; End;
    Ext := UpperCase(ExtractFileExt(Trim(Path)));
    Result := (Ext = '.PAS') Or (Ext = '.BAS') Or (Ext = '.VBS') Or (Ext = '.JS')
        Or (Ext = '.TCL') Or (Ext = '.DFM');
End;

{ What the save walks write, list and count: anything that is not a script   }
{ unit. A script unit never reaches WriteDocAndNote, skipped_unmodified,     }
{ free_documents_not_reached, documents_seen or still_dirty.                 }
Function IsSaveableDesignDoc(Doc : IDocument) : Boolean;
Begin
    Result := False;
    If Doc = Nil Then Exit;
    Result := Not IsScriptUnitDoc(Doc);
End;

{ Save every modified IServerDocument the workspace knows about, both     }
{ project-attached docs and free-floating docs (libraries opened           }
{ standalone, scratch docs). Free docs live inside the synthetic           }
{ DM_FreeDocumentsProject which we iterate like any normal project.        }
Procedure SaveOneDocByDocRef(Doc : IDocument);
Var
    ServerDoc : IServerDocument;
    WasModified, IsLib, Skip, InRecord, ByRecord, Moved : Boolean;
    Ext, DiskPath, DocPath : String;
Begin
    If Doc = Nil Then Exit;
    { Before ANY lookup, write or listing: a script unit is never written }
    { and never listed (IsSaveableDesignDoc = not IsScriptUnitDoc).       }
    If Not IsSaveableDesignDoc(Doc) Then Exit;
    Try
        { Only a document OPEN in the editor has an IServerDocument. A
          closed project member returns Nil and is skipped, correctly: it
          cannot be holding unsaved edits. SaveAttempts counts the ones
          actually reached, so a pass that wrote nothing because nothing
          was open is not confused with one the editor declined. }
        ServerDoc := ListedServerDoc(Doc);
        { A bare listing nothing resolved is an OPEN free document this walk  }
        { cannot reach (a closed project member is absolute and is skipped   }
        { above by being Nil). Said, not skipped silently (review 2026-10-11).}
        If ServerDoc = Nil Then
        Begin
            DocPath := '';
            Try DocPath := Doc.DM_FullPath; Except DocPath := ''; End;
            If (Trim(DocPath) <> '') And (Not LooksAbsolutePath(Trim(DocPath))) Then
                SaveUnresolvedPaths := PathListWith(SaveUnresolvedPaths, DocPath);
        End;
        { Unconditional flush, Modified flag does not always propagate from   }
        { ProcessControl.PostProcess to the IServerDocument layer in newer    }
        { Altium builds, so checking it can leave docs unsaved.               }
        {                                                                     }
        { THE FLUSH IS NOT A NO-OP ON A CLEAN DOCUMENT. SetModified(True)     }
        { makes it a real write. MEASURED 2026-10-06 (AD 26.10.1.5): with     }
        { nothing edited, app_save_all saw 45 documents, attempted 29 and     }
        { wrote 29. Writing carrier.SchLib moved its mtime, which is the key  }
        { of the offline symbol cache, and invalidated a parallel tuning run; }
        { every SchDoc came back with fresh parameter UniqueIDs.              }
        {                                                                     }
        { So a pass may now skip a document that reads unmodified, but ONLY   }
        { when its caller asked (SaveSkipCleanLibs / SaveSkipCleanSheets).    }
        { A modified read is trusted; an unreadable flag counts as modified,  }
        { so doubt always writes. The cost is the edit paths whose flag does  }
        { not propagate (lib_add_footprint_tracks, lib_modify_footprint_pads  }
        { have read modified:false): a skipped library lost those edits       }
        { unless lib_save_library wrote it. Since 2026-10-11 a document the   }
        { bridge itself marked is on BridgeEditedPaths and is written anyway  }
        { (below), so the trade only bites edits made outside MarkDocDirty.   }
        If ServerDoc <> Nil Then
        Begin
            WasModified := False;
            Try WasModified := ModifiedFlagOf(ServerDoc); Except WasModified := True; End;
            DocPath := '';
            Try DocPath := Doc.DM_FullPath; Except DocPath := ''; End;
            Ext := '';
            Try Ext := UpperCase(ExtractFileExt(DocPath)); Except Ext := ''; End;
            IsLib := (Ext = '.SCHLIB') Or (Ext = '.PCBLIB');
            { THE BRIDGE'S RECORD OVERRIDES A CLEAN READ. MEASURED 2026-10-10: }
            { documents this bridge had just edited read Modified False with   }
            { '*' on their tabs, so the skip below would have dropped those    }
            { edits. A document on the record (marked by this bridge and not   }
            { written by any route since: BridgeEditRecorded checks the age     }
            { stamp) is written whatever its flag says, and reported in        }
            { written_by_bridge_record. Documents nobody edited are still left }
            { alone, which keeps the 2026-10-06 fix.                           }
            DiskPath := ServerDocFileName(ServerDoc);
            If Not LooksAbsolutePath(DiskPath) Then DiskPath := DocPath;
            { NO ABSOLUTE PATH, NO FILE TO WRITE. Neither the document's own  }
            { FileName nor its listing names a file, so DoFileSave('') could  }
            { only ask where to save (a Save As dialog) and no key can tell   }
            { two such documents apart. Listed in free_documents_not_reached, }
            { never written and never matched by key (review of 0c4f20e: an   }
            { empty key matched an empty attempted list by accident).         }
            If Not LooksAbsolutePath(Trim(DiskPath)) Then
            Begin
                If Trim(DocPath) <> '' Then
                    SaveUnresolvedPaths := PathListWith(SaveUnresolvedPaths, DocPath)
                Else If Trim(DiskPath) <> '' Then
                    SaveUnresolvedPaths := PathListWith(SaveUnresolvedPaths, DiskPath)
                Else
                Begin
                    { One entry PER DOCUMENT: PathListWith dedupes by key, so  }
                    { one shared placeholder collapsed every unnamed document  }
                    { into a single entry (review of d198b65).                 }
                    SaveUnnamedCount := SaveUnnamedCount + 1;
                    SaveUnresolvedPaths := PathListWith(SaveUnresolvedPaths,
                        '(unnamed document ' + IntToStr(SaveUnnamedCount) + ')');
                End;
                Exit;
            End;
            { ONE VISIT PER FILE PER PASS (live 2026-10-11). The walk reaches  }
            { a document once per listing: a real project's member and the   }
            { Free Documents listing of the same file (DM_Projects can hold   }
            { the free project too, which the walk then visits again), or two }
            { spellings of one path. The first visit wrote it; the second     }
            { read the now-clean flag and filed the file it had just written  }
            { under skipped_unmodified / libraries_skipped. A file this pass  }
            { has already attempted is not looked at again - inside a walk    }
            { ONLY (SaveWalkPass): a direct proj_save after an app_save_all   }
            { must write an edit made since, whatever the list still holds.  }
            If SaveWalkPass Then
            Begin
                If Pos('|' + DocPathKey(DiskPath) + '|', '|' + SaveAttemptedKeys + '|') > 0 Then Exit;
                If LooksAbsolutePath(Trim(DocPath))
                    And (Pos('|' + DocPathKey(DocPath) + '|', '|' + SaveAttemptedKeys + '|') > 0) Then Exit;
            End;
            InRecord := BridgeEditRecorded(DocPath) Or BridgeEditRecorded(DiskPath);
            ByRecord := False;
            Skip := False;
            If Not WasModified Then
            Begin
                If IsLib Then Skip := SaveSkipCleanLibs
                Else Skip := SaveSkipCleanSheets;
                If Skip And InRecord Then
                Begin
                    Skip := False;
                    ByRecord := True;
                End;
            End;
            { ONE ENTRY PER FILE. A library open in two projects, or in a     }
            { project and as a free document, is reached once per walk, and   }
            { was listed and counted twice. The pipe-wrapped compare cannot   }
            { match part of a longer path. Keyed by DocPathKey, on the        }
            { listing AND the file's own name: the carrier library was listed }
            { twice live, once per slash direction (2026-10-11).              }
            If Skip Then
            Begin
                If (Pos('|' + DocPathKey(DocPath) + '|', '|' + DocPathKey(SaveSkippedPaths) + '|') = 0)
                    And (Pos('|' + DocPathKey(DiskPath) + '|', '|' + DocPathKey(SaveSkippedPaths) + '|') = 0) Then
                Begin
                    SaveSkipped := SaveSkipped + 1;
                    If SaveSkippedPaths <> '' Then SaveSkippedPaths := SaveSkippedPaths + '|';
                    SaveSkippedPaths := SaveSkippedPaths + DocPath;
                End;
            End
            Else
                Moved := WriteDocAndNote(ServerDoc, DiskPath, DocPath, InRecord, ByRecord);
        End;
    Except End;
End;

Procedure SaveProjectMembers(Project : IProject);
Var
    J : Integer;
    ProjectServerDoc : IServerDocument;
Begin
    If Project = Nil Then Exit;
    { The bridge's own script project is never saved, by any route (a      }
    { proj_save with Altium_API.PrjScr focused included): IsScriptProject. }
    If IsScriptProject(Project) Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        SaveOneDocByDocRef(Project.DM_LogicalDocuments(J));
    // The project file itself, when it's a real on-disk project
    Try
        ProjectServerDoc := Client.GetDocumentByPath(Project.DM_ProjectFullPath);
        If ModifiedFlagOf(ProjectServerDoc) Then
            Try ProjectServerDoc.DoFileSave(''); Except End;
    Except End;
End;

{ CountDirtyInProject / CountDirtyDocuments - how many documents are STILL     }
{ unsaved. The only way to tell a save that worked from one Altium refused.    }
{                                                                              }
{ MEASURED on AD26: app_save_all returned saved:true while Altium was raising  }
{ "A command is currently active and save cannot be completed at this time"    }
{ once per dirty document. Every one of those saves was declined, and the tool }
{ still reported success, because it only checked that SaveAllDirty had not    }
{ raised. DoFileSave does not raise when the editor refuses.                   }
Function CountDirtyInProject(Project : IProject) : Integer;
Var
    J : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
Begin
    Result := 0;
    If Project = Nil Then Exit;
    If IsScriptProject(Project) Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If (Doc <> Nil) And IsSaveableDesignDoc(Doc) Then
        Begin
            Try
                ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
                If ModifiedFlagOf(ServerDoc) Then
                    Result := Result + 1;
            Except End;
        End;
    End;
End;

Function CountDirtyDocuments(Dummy : Integer): Integer;
Var
    Workspace : IWorkspace;
    I : Integer;
Begin
    Result := 0;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    { Script projects and units are skipped inside CountDirtyInProject. }
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
        Result := Result + CountDirtyInProject(Workspace.DM_Projects(I));
    Try
        Result := Result + CountDirtyInProject(Workspace.DM_FreeDocumentsProject);
    Except End;
End;

{ ResolveLoadedDocPath - turn a bare document name into an ABSOLUTE path.     }
{                                                                             }
{ DM_FullPath AND DocumentName BOTH RETURN A BARE BASENAME for a free         }
{ document, one that is open but not a member of any project. Anything that   }
{ feeds that string to WorkspaceManager:OpenObject / CloseObject, or to        }
{ CreateLibCompInfoReader, gets a silent no-op or a reader for the wrong      }
{ file, because those all want a real path.                                   }
{                                                                             }
{ MEASURED: app_get_active_document and lib_get_component_details both        }
{ reported "SWEEP_A.SchLib" for a document whose actual path is under the     }
{ scratch directory, while the same call given an explicit library_path       }
{ reported the full path. A reopen built on the basename did nothing at all   }
{ and the failure looked like a lookup bug.                                   }
{                                                                             }
{ Returns '' when no absolute path can be found, so a caller can REFUSE       }
{ rather than proceed with a string that will quietly do nothing.             }
Function MatchDocPathInProject(Project : IProject; Wanted : String) : String;
Var
    J : Integer;
    Doc : IDocument;
    Full : String;
Begin
    Result := '';
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc <> Nil Then
        Begin
            Full := '';
            Try Full := Doc.DM_FullPath; Except End;
            If LooksAbsolutePath(Full)
                And (UpperCase(ExtractFileName(Full)) = UpperCase(Wanted)) Then
            Begin
                Result := Full;
                Exit;
            End;
        End;
    End;
End;

Function ResolveLoadedDocPath(NameOrPath : String) : String;
Var
    Workspace : IWorkspace;
    Wanted : String;
    I : Integer;
Begin
    Result := '';
    If NameOrPath = '' Then Exit;
    If LooksAbsolutePath(NameOrPath) Then
    Begin
        Result := NameOrPath;
        Exit;
    End;

    Wanted := ExtractFileName(NameOrPath);
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;

    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Result := MatchDocPathInProject(Workspace.DM_Projects(I), Wanted);
        If Result <> '' Then Exit;
    End;

    { Free documents live in the synthetic FreeDocumentsProject, and they are }
    { precisely the ones that report a basename, so this is the branch that   }
    { usually answers.                                                        }
    Try
        Result := MatchDocPathInProject(Workspace.DM_FreeDocumentsProject, Wanted);
    Except End;
End;

{ DocFullPath - the absolute path of a document, never its bare name.         }
{                                                                             }
{ DM_FileName is the file NAME. MEASURED on AD 26.10.1.6: a project member    }
{ came back as "CloseSheet.SchDoc", and two handlers passed that on as        }
{ file_path. A caller got a path that was not one, and a modified lookup on   }
{ it could never resolve. DM_FullPath first, the name only as a last resort,  }
{ and a bare result resolved against the open projects.                       }
Function DocFullPath(Doc : IDocument) : String;
Var
    P, Resolved : String;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    P := '';
    Try P := Doc.DM_FullPath; Except P := ''; End;
    If P = '' Then
    Begin
        Try P := Doc.DM_FileName; Except P := ''; End;
    End;
    If (P <> '') And (Not LooksAbsolutePath(P)) Then
    Begin
        Resolved := ResolveLoadedDocPath(P);
        If Resolved <> '' Then P := Resolved;
    End;
    Result := P;
End;

{ THE DM DOCUMENT (IDocument) LISTED AT AN ABSOLUTE PATH, walking every open   }
{ project and then the free documents; Nil when none lists it. An           }
{ ISch_Document / IServerDocument is NOT an IDocument, and calling a DM_     }
{ member on one raises; this is how a handler holding an editor document     }
{ reaches its DM twin. Matching is by DocPathKey on the listed DM_FullPath,  }
{ and only an ABSOLUTE listing can match: a free document's bare listing     }
{ proves nothing about which file it is, and a bare Path is never guessed    }
{ at, so this can never answer with a namesake (review of c8ee69e). A miss   }
{ is Nil, which callers treat as 'no DM document', today's outcome.          }
Function FindDMDocumentByPath(Path : String) : IDocument;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Want, Full : String;
    I, J, N : Integer;
Begin
    Result := Nil;
    If Not LooksAbsolutePath(Trim(Path)) Then Exit;
    Want := DocPathKey(Path);
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    N := Workspace.DM_ProjectCount;
    For I := 0 To N Do
    Begin
        Project := Nil;
        If I < N Then
            Project := Workspace.DM_Projects(I)
        Else
        Begin
            Try Project := Workspace.DM_FreeDocumentsProject; Except End;
        End;
        If Project = Nil Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(J);
            If Doc = Nil Then Continue;
            Full := '';
            Try Full := Doc.DM_FullPath; Except End;
            If Not LooksAbsolutePath(Full) Then Continue;
            If DocPathKey(Full) = Want Then
            Begin
                Result := Doc;
                Exit;
            End;
        End;
    End;
End;

{..............................................................................}
{ Did the save actually write anything.                                        }
{                                                                              }
{ app_save_all used to answer this with CountDirtyDocuments, which walks the   }
{ workspace exactly the way SaveAllDirty does: same GetWorkspace, same         }
{ DM_Projects loop, same silent Exit when the workspace is Nil. So when the    }
{ enumeration came back empty the save wrote nothing AND the check counted     }
{ nothing, and zero was reported as success. A verifier that shares the        }
{ failure mode of the thing it verifies cannot catch it.                       }
{                                                                              }
{ It also read ServerDoc.Modified, which SaveOneDocByDocRef already documents  }
{ as unreliable: the flag does not always propagate from                       }
{ ProcessControl.PostProcess to the IServerDocument layer on newer builds.     }
{ MEASURED: dirty_doc_count 0 immediately after a wire placement and an entry  }
{ move, and 29 property edits lost on reload while app_save_all reported       }
{ saved:true throughout.                                                       }
{                                                                              }
{ FILE TIMESTAMPS ARE THE GROUND TRUTH. A document either got newer on disk    }
{ or it did not, and that answer does not depend on any Altium flag.           }
{..............................................................................}

{ THE FREE DOCUMENTS COUNT TOO (2026-10-11). This walked DM_Projects only,   }
{ while the save pass also writes the synthetic free-documents project. A     }
{ free document the pass wrote was never aged, so documents_written left it   }
{ out, and a pass that wrote ONLY free documents (the 2026-10-10 field case:  }
{ a scratch sheet and a library opened under a Temp path) would have read     }
{ "not one got newer on disk". A free document's DM_FullPath can be a bare    }
{ name, so its path is taken from the open document itself; one file listed   }
{ by two projects is aged once.                                               }
Function WorkspaceDocPaths(Dummy : Integer) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Found : IServerDocument;
    I, J, ProjCount : Integer;
    Path, Keys, Disk : String;
Begin
    Result := '';
    Keys := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    ProjCount := 0;
    Try ProjCount := Workspace.DM_ProjectCount; Except ProjCount := 0; End;
    For I := -1 To ProjCount - 1 Do
    Begin
        Project := Nil;
        Try
            If I < 0 Then Project := Workspace.DM_FreeDocumentsProject
            Else Project := Workspace.DM_Projects(I);
        Except
            Project := Nil;
        End;
        If Project = Nil Then Continue;
        { No script unit, as the save walk (IsScriptProject,            }
        { IsSaveableDesignDoc): a script unit is not seen or aged.      }
        If IsScriptProject(Project) Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Nil;
            Try Doc := Project.DM_LogicalDocuments(J); Except End;
            If Doc = Nil Then Continue;
            If Not IsSaveableDesignDoc(Doc) Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If (Path <> '') And (Not LooksAbsolutePath(Path)) Then
            Begin
                Found := FindOpenServerDoc(Path);
                Disk := ServerDocFileName(Found);
                If LooksAbsolutePath(Disk) Then Path := Disk;
            End;
            If Path = '' Then Continue;
            If Pos('|' + DocPathKey(Path) + '|', '|' + Keys + '|') > 0 Then Continue;
            If Keys <> '' Then Keys := Keys + '|';
            Keys := Keys + DocPathKey(Path);
            If Result <> '' Then Result := Result + '|';
            Result := Result + Path;
        End;
    End;
End;

{ File age for each pipe-separated path, as its own pipe-separated list.       }
{ A path that does not exist yet reports -1, which simply cannot match a       }
{ later age and therefore counts as written once it appears.                   }

Function AgesForPaths(PathList : String) : String;
Var
    Remaining, Path : String;
    P, Age : Integer;
Begin
    Result := '';
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Path := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Path := Remaining;
            Remaining := '';
        End;
        Age := -1;
        Try Age := FileAge(Path); Except Age := -1; End;
        If Result <> '' Then Result := Result + '|';
        Result := Result + IntToStr(Age);
    End;
End;

{ How many entries differ between two age lists of the same shape.             }

Function CountChangedAges(BeforeList : String; AfterList : String) : Integer;
Var
    RemA, RemB, A, B : String;
    P : Integer;
Begin
    Result := 0;
    RemA := BeforeList;
    RemB := AfterList;
    While (RemA <> '') And (RemB <> '') Do
    Begin
        P := Pos('|', RemA);
        If P > 0 Then
        Begin
            A := Copy(RemA, 1, P - 1);
            RemA := Copy(RemA, P + 1, Length(RemA) - P);
        End
        Else
        Begin
            A := RemA;
            RemA := '';
        End;

        P := Pos('|', RemB);
        If P > 0 Then
        Begin
            B := Copy(RemB, 1, P - 1);
            RemB := Copy(RemB, P + 1, Length(RemB) - P);
        End
        Else
        Begin
            B := RemB;
            RemB := '';
        End;

        If A <> B Then Result := Result + 1;
    End;
End;

Function CountPathEntries(PathList : String) : Integer;
Var
    Remaining : String;
    P : Integer;
Begin
    Result := 0;
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        Result := Result + 1;
        P := Pos('|', Remaining);
        If P > 0 Then
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P)
        Else
            Remaining := '';
    End;
End;

Procedure SaveAllDirty(Dummy : Integer);
Var
    Workspace : IWorkspace;
    I : Integer;
Begin
    SaveAttemptedKeys := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    SaveWalkPass := True;
    Try
        // Real projects + their member docs. Never the bridge's own script
        // project (IsScriptProject; SaveProjectMembers refuses it too).
        For I := 0 To Workspace.DM_ProjectCount - 1 Do
            If Not IsScriptProject(Workspace.DM_Projects(I)) Then
                SaveProjectMembers(Workspace.DM_Projects(I));

        // Free documents live inside the synthetic FreeDocumentsProject
        Try SaveProjectMembers(Workspace.DM_FreeDocumentsProject); Except End;
    Finally
        SaveWalkPass := False;
    End;
End;

{ WRITE WHAT THE RECORD HOLDS THAT THE WALK DID NOT REACH. Review 2026-10-11: }
{ the walk resolves a free document through its DM_FullPath, which can be a  }
{ bare name that resolves to nothing - and the 2026-10-10 field documents    }
{ were exactly such free documents, so the record alone changed nothing for  }
{ them. This runs after SaveAllDirty (App_SaveAll) and takes every ABSOLUTE   }
{ record entry the walk did not attempt and that no route has written since  }
{ the edit (BridgeEditRecorded, which drops stale entries):                  }
{   open      written from its absolute key, filed like any record write     }
{   not open  dropped and listed in bridge_record_closed: no document holds  }
{             the edit any more (closed by proj_close, proj_delete_sheet or  }
{             the user without a save), and a clean reopen must not be       }
{             rewritten from a stale entry. Its edit is not on disk.         }
{   open under another spelling  the lookup cannot equate the key with the  }
{             document's FileName (a subst F:\ key, the listing and FileName }
{             H:\), yet the file exists and Client.GetDocumentByPath(key)   }
{             answers: KEPT and listed in bridge_record_unresolved, never    }
{             called a lost edit (re-review of a0c9da9).                     }
{ Returns how many it wrote whose path AgedPaths (WorkspaceDocPaths) does not }
{ hold, so documents_written can count writes the timestamp list never saw.  }
Function SaveBridgeRecordSweep(AgedPaths : String) : Integer;
Var
    Remaining, Entry, Key, DiskPath : String;
    P : Integer;
    ServerDoc, Alive : IServerDocument;
Begin
    Result := 0;
    Remaining := BridgeEditedPaths;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Entry := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Entry := Remaining;
            Remaining := '';
        End;
        Key := BridgeEntryKey(Entry);
        If Not LooksAbsolutePath(Key) Then Continue;
        If Pos('|' + Key + '|', '|' + SaveAttemptedKeys + '|') > 0 Then Continue;
        If Not BridgeEditRecorded(Key) Then Continue;
        ServerDoc := FindOpenServerDoc(Key);
        If ServerDoc = Nil Then
        Begin
            Alive := Nil;
            If FileExists(Key) Then
                Try Alive := Client.GetDocumentByPath(Key); Except Alive := Nil; End;
            If Alive <> Nil Then
            Begin
                SaveBridgeRecordUnresolved := PathListWith(SaveBridgeRecordUnresolved, Key);
                Continue;
            End;
            ForgetBridgeEdit(Key);
            SaveBridgeRecordClosed := PathListWith(SaveBridgeRecordClosed, Key);
            Continue;
        End;
        DiskPath := ServerDocFileName(ServerDoc);
        If Not LooksAbsolutePath(Trim(DiskPath)) Then DiskPath := Key;
        If WriteDocAndNote(ServerDoc, DiskPath, Key, True, True) Then
        Begin
            If Pos('|' + Key + '|', '|' + DocPathKey(AgedPaths) + '|') = 0 Then
                Result := Result + 1;
        End;
    End;
End;

{..............................................................................}
{ Resolve workspace directory.                                                  }
{                                                                                }
{ DelphiScript has no access to environment variables, so the Python side       }
{ writes the absolute workspace path to a pointer file at a fixed location:    }
{     C:\ProgramData\eda-agent\workspace-path.txt                            }
{ This script reads that file. Python writes it at MCP-server startup and      }
{ whenever `eda-agent install-scripts` runs, so by the time this script      }
{ needs the workspace the pointer is always current.                            }
{                                                                                }
{ Fallback (pointer missing): C:\EDA Agent\workspace\                        }
{..............................................................................}

Function ResolveDefaultWorkspaceDir(Dummy : Integer): String;
Var
    PointerFile : String;
    F : TextFile;
    Line : String;
Begin
    Result := '';
    PointerFile := 'C:\ProgramData\eda-agent\workspace-path.txt';
    Try
        If FileExists(PointerFile) Then
        Begin
            AssignFile(F, PointerFile);
            Reset(F);
            Try
                If Not Eof(F) Then
                    ReadLn(F, Line);
            Finally
                CloseFile(F);
            End;
            // Trim CR/LF/space
            While (Length(Line) > 0) And ((Line[Length(Line)] = #13) Or (Line[Length(Line)] = #10) Or (Line[Length(Line)] = ' ')) Do
                Line := Copy(Line, 1, Length(Line) - 1);
            While (Length(Line) > 0) And (Line[1] = ' ') Do
                Line := Copy(Line, 2, Length(Line));
            If Line <> '' Then
                Result := Line;
        End;
    Except
        Result := '';
    End;
    If Result = '' Then
        Result := 'C:\EDA Agent\workspace\';
    // Ensure trailing backslash so path joins work consistently
    If Copy(Result, Length(Result), 1) <> '\' Then
        Result := Result + '\';
End;

{..............................................................................}
{ JSON Helper Functions                                                        }
{..............................................................................}

Function ReadFileContent(FilePath : String) : String;
Var
    SL : TStringList;
    Content : String;
    Attempt : Integer;
    Ok : Boolean;
Begin
    { Read via TStringList.LoadFromFile, NOT TextFile Reset/ReadLn. The RTL    }
    { Reset raises a low-level EInOutError on a sharing violation (I/O error   }
    { 32) which the Altium script engine intercepts with a modal BEFORE the    }
    { surrounding Try/Except can swallow it, stalling the polling loop.        }
    { LoadFromFile opens through a TFileStream and raises an ordinary VCL      }
    { EFOpenError that Try/Except catches cleanly -- no engine modal. The      }
    { lock is transient (Defender scanning the freshly renamed request file,   }
    { or a collision with Python's atomic replace), so retry briefly.          }
    { NOTE: stash the content in a LOCAL string and assign Result from it as }
    { the LAST statement. Assigning Result directly from SL.Text (an object  }
    { property) and then Exit-ing triggers the DelphiScript return-clobber    }
    { bug: the caller receives the FilePath argument instead of the file      }
    { content, so the dispatcher reads the request path as the command and    }
    { silently processes nothing. See delphiscript_result_clobber.            }
    Result := '';
    If Not FileExists(FilePath) Then Exit;
    Content := '';
    Attempt := 0;
    While Attempt < 12 Do
    Begin
        Inc(Attempt);
        Ok := False;
        SL := TStringList.Create;
        Try
            Try
                SL.LoadFromFile(FilePath);
                Content := SL.Text;
                Ok := True;
            Except
                Ok := False;
            End;
        Finally
            SL.Free;
        End;
        If Ok Then Break;
        Sleep(15);
    End;
    Result := Content;
End;

Procedure WriteFileContent(FilePath : String; Content : String);
Var
    SL : TStringList;
    Attempt : Integer;
    Ok : Boolean;
Begin
    { Write via TStringList.SaveToFile, NOT TextFile Rewrite/Write. Rewrite    }
    { raises a low-level EInOutError on a sharing violation (I/O error 32)     }
    { that the Altium script engine surfaces as a modal before the            }
    { surrounding Try/Except can swallow it, stalling the polling loop.        }
    { SaveToFile goes through a TFileStream and raises an ordinary VCL         }
    { exception that Try/Except catches cleanly -- no engine modal. The lock   }
    { is transient (Defender scan, or Python reading the response mid-write),  }
    { so retry briefly. Mirrors ReadFileContent and the proven sibling-MCP     }
    { idiom (OutputLines.Text := json; SaveToFile).                           }
    { CLEAR THE DESTINATION FIRST, with calls that cannot raise.

      MEASURED: EFCreateError, "Cannot create file ... because it is
      being used by another process", arrived as a modal and stalled the
      polling loop. The retry below cannot help with that, because the
      engine surfaces the exception before the surrounding Try/Except
      runs, so the first failure is already a modal. Same behaviour that
      defeated Try/Except around StrToFloat, same answer: stop the
      exception happening rather than trying to catch it.

      FileExists and DeleteFile return Booleans and do not raise, so this
      loop is safe no matter who holds the file. Only once the name is
      free is the raising create attempted.

      tmp + RenameFile would be the tidier fix and is ruled out: the
      sibling implementation in reference/CoAltium records that
      DelphiScript's RenameFile silently failed for some paths and the
      response never reached its final filename.

      This does not make a create infallible. A fresh name can still be
      grabbed between the check and the create, by a virus scanner most
      likely. It removes the reported case, which is a create against a
      name that is already there and already held. }
    Attempt := 0;
    While (Attempt < 40) And FileExists(FilePath) Do
    Begin
        Inc(Attempt);
        DeleteFile(FilePath);
        If FileExists(FilePath) Then Sleep(15);
    End;

    Attempt := 0;
    While Attempt < 12 Do
    Begin
        Inc(Attempt);
        Ok := False;
        SL := TStringList.Create;
        Try
            Try
                SL.Text := Content;
                SL.SaveToFile(FilePath);
                Ok := True;
            Except
                Ok := False;
            End;
        Finally
            SL.Free;
        End;
        If Ok Then Exit;
        { The destination may have reappeared, so clear it again before
          the next create rather than repeating the failing call. }
        If FileExists(FilePath) Then DeleteFile(FilePath);
        Sleep(15);
    End;
End;

Function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = ' ') Or (C = ':') Or (C = #9) Or (C = #10) Or (C = #13);
End;

Function IsDelimiter(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = '') Or (C = ',') Or (C = '}') Or (C = ']') Or (C = ' ') Or (C = #9) Or (C = #10) Or (C = #13);
End;

{ Hex digit to integer (0-15). Returns -1 for invalid input. }
Function HexDigitValue(Ch : String) : Integer;
Var
    O : Integer;
Begin
    Result := -1;
    If Length(Ch) <> 1 Then Exit;
    O := Ord(Ch[1]);
    If (O >= Ord('0')) And (O <= Ord('9')) Then Result := O - Ord('0')
    Else If (O >= Ord('a')) And (O <= Ord('f')) Then Result := O - Ord('a') + 10
    Else If (O >= Ord('A')) And (O <= Ord('F')) Then Result := O - Ord('A') + 10;
End;

{..............................................................................}
{ PctDecode - undo bridge/payload.py stamp_encode.                              }
{                                                                               }
{ The batch grammar has no escape syntax: ';' ends a field, '~~' ends an        }
{ operation and the first '=' splits a key from its value. So the Python side  }
{ used to SUBSTITUTE ';' with ',' in every value, which is how a Manufacturer   }
{ Part Number or Description containing ';' reached the part with ',' instead  }
{ (field report 2026-10-08). A tool that opts in sends value_encoding=pct1 and  }
{ escapes '%' ';' '~' '=' as %XX; this turns them back, AFTER the payload has  }
{ been split, so the text arrives exactly.                                      }
{                                                                               }
{ '%' followed by two hex digits becomes the character they name. Anything      }
{ else, a lone or trailing '%' included, is copied unchanged: an encoder that   }
{ never produced it cannot have meant an escape.                                }
{..............................................................................}

Function PctDecode(S : String) : String;
Var
    I, L, D1, D2 : Integer;
    Ch : String;
Begin
    Result := '';
    I := 1;
    L := Length(S);
    While I <= L Do
    Begin
        Ch := Copy(S, I, 1);
        D1 := -1;
        D2 := -1;
        If (Ch = '%') And (I + 2 <= L) Then
        Begin
            D1 := HexDigitValue(Copy(S, I + 1, 1));
            D2 := HexDigitValue(Copy(S, I + 2, 1));
        End;
        If (D1 >= 0) And (D2 >= 0) Then
        Begin
            Result := Result + Chr(D1 * 16 + D2);
            Inc(I, 3);
        End
        Else
        Begin
            Result := Result + Ch;
            Inc(I);
        End;
    End;
End;

Function UnescapeJsonString(S : String) : String;
Var
    I, L : Integer;
    Ch, NextCh, HexStr : String;
    Code, D0, D1, D2, D3 : Integer;
Begin
    // Char-by-char JSON unescape with full \uXXXX support. The naive
    // StringReplace cascade (\t -> tab, \n -> LF, ..., \\ -> \) is broken
    // for sequences like \\temp, handles escapes left-to-right so \\
    // collapses to \ before evaluating the following char.
    Result := '';
    I := 1;
    L := Length(S);
    While I <= L Do
    Begin
        Ch := Copy(S, I, 1);
        If (Ch = '\') And (I < L) Then
        Begin
            NextCh := Copy(S, I + 1, 1);
            If NextCh = '\' Then Begin Result := Result + '\'; Inc(I, 2); End
            Else If NextCh = 'n' Then Begin Result := Result + #10; Inc(I, 2); End
            Else If NextCh = 't' Then Begin Result := Result + #9; Inc(I, 2); End
            Else If NextCh = 'r' Then Begin Result := Result + #13; Inc(I, 2); End
            Else If NextCh = '"' Then Begin Result := Result + '"'; Inc(I, 2); End
            Else If NextCh = '/' Then Begin Result := Result + '/'; Inc(I, 2); End
            Else If NextCh = 'b' Then Begin Result := Result + #8; Inc(I, 2); End
            Else If NextCh = 'f' Then Begin Result := Result + #12; Inc(I, 2); End
            Else If NextCh = 'u' Then
            Begin
                // \uXXXX, 4 hex digits. Codepoints <= 255 are emitted as a
                // single ANSI byte (Pascal native). Higher codepoints can't
                // be represented in single-byte ANSI; replaced with '?' so
                // downstream string handling doesn't see truncated bytes.
                If I + 5 <= L Then
                Begin
                    HexStr := Copy(S, I + 2, 4);
                    D0 := HexDigitValue(Copy(HexStr, 1, 1));
                    D1 := HexDigitValue(Copy(HexStr, 2, 1));
                    D2 := HexDigitValue(Copy(HexStr, 3, 1));
                    D3 := HexDigitValue(Copy(HexStr, 4, 1));
                    If (D0 >= 0) And (D1 >= 0) And (D2 >= 0) And (D3 >= 0) Then
                    Begin
                        Code := (D0 * 4096) + (D1 * 256) + (D2 * 16) + D3;
                        If Code <= 255 Then
                            Result := Result + Chr(Code)
                        Else
                            Result := Result + '?';
                        Inc(I, 6);
                    End
                    Else
                    Begin
                        // Bad hex, keep literal
                        Result := Result + Ch + NextCh;
                        Inc(I, 2);
                    End;
                End
                Else
                Begin
                    Result := Result + Ch + NextCh;
                    Inc(I, 2);
                End;
            End
            Else
            Begin
                // Unknown escape, keep both chars literally
                Result := Result + Ch + NextCh;
                Inc(I, 2);
            End;
        End
        Else
        Begin
            Result := Result + Ch;
            Inc(I);
        End;
    End;
End;

Function ExtractJsonValue(Json : String; Key : String) : String;
Var
    StartPos, EndPos : Integer;
    SearchKey : String;
    BraceCount : Integer;
    BackslashCount, TempPos : Integer;
    InStr : Boolean;
    Ch : String;
Begin
    Result := '';
    SearchKey := '"' + Key + '"';
    StartPos := Pos(SearchKey, Json);
    If StartPos > 0 Then
    Begin
        StartPos := StartPos + Length(SearchKey);
        // Skip whitespace and colon
        While (StartPos <= Length(Json)) And IsWhitespaceOrColon(Json, StartPos) Do
            Inc(StartPos);

        If StartPos <= Length(Json) Then
        Begin
            If Copy(Json, StartPos, 1) = '"' Then
            Begin
                // String value
                Inc(StartPos);
                EndPos := StartPos;
                While (EndPos <= Length(Json)) Do
                Begin
                    If Copy(Json, EndPos, 1) = '"' Then
                    Begin
                        // Count consecutive backslashes before this quote
                        BackslashCount := 0;
                        TempPos := EndPos - 1;
                        While (TempPos >= StartPos) And (Copy(Json, TempPos, 1) = '\') Do
                        Begin
                            Inc(BackslashCount);
                            Dec(TempPos);
                        End;
                        // Even number of backslashes means quote is real
                        If (BackslashCount Mod 2) = 0 Then Break;
                    End;
                    Inc(EndPos);
                End;
                Result := UnescapeJsonString(Copy(Json, StartPos, EndPos - StartPos));
            End
            Else If (Copy(Json, StartPos, 1) = '{') Or
                    (Copy(Json, StartPos, 1) = '[') Then
            Begin
                // Container value (object or array). Depth-count braces AND
                // brackets together, skipping string literals so a '}' or
                // ']' inside a "string" can't close the container early.
                EndPos := StartPos;
                BraceCount := 0;
                InStr := False;
                While EndPos <= Length(Json) Do
                Begin
                    Ch := Copy(Json, EndPos, 1);
                    If InStr Then
                    Begin
                        If Ch = '"' Then
                        Begin
                            // Real close-quote only after an even run of '\'.
                            BackslashCount := 0;
                            TempPos := EndPos - 1;
                            While (TempPos >= StartPos) And
                                  (Copy(Json, TempPos, 1) = '\') Do
                            Begin
                                Inc(BackslashCount);
                                Dec(TempPos);
                            End;
                            If (BackslashCount Mod 2) = 0 Then InStr := False;
                        End;
                    End
                    Else
                    Begin
                        If Ch = '"' Then InStr := True
                        Else If (Ch = '{') Or (Ch = '[') Then Inc(BraceCount)
                        Else If (Ch = '}') Or (Ch = ']') Then Dec(BraceCount);
                    End;
                    Inc(EndPos);
                    If (Not InStr) And (BraceCount = 0) Then Break;
                End;
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End
            Else
            Begin
                // Number or other value
                EndPos := StartPos;
                While (EndPos <= Length(Json)) And (Not IsDelimiter(Json, EndPos)) Do
                    Inc(EndPos);
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End;
        End;
    End;
End;

{..............................................................................}
{ JSON envelope builders.                                                       }
{                                                                               }
{ All responses include protocol_version so the Python side can detect a       }
{ stale Pascal-side compile after a wire-format change. BuildErrorResponse     }
{ takes an optional structured `details` JSON value (pass '' to omit). The     }
{ Detailed variant lets handlers attach machine-readable failure context       }
{ (which item in a batch failed, what type was expected, etc).                 }
{..............................................................................}

Function BuildSuccessResponse(RequestId : String; Data : String) : String;
Begin
    If Data = '' Then
        Data := 'null';
    Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
              ',"id":"' + RequestId + '","success":true,"data":' +
              Data + ',"error":null}';
End;

{..............................................................................}
{ CROSS-DOCUMENT HINTS                                                        }
{                                                                              }
{ "No PCB library is active" is true and unhelpful. It says what is missing    }
{ and not that the SAME operation exists for the other document kind, so the   }
{ reasonable conclusion from it is that the capability is absent. That         }
{ conclusion has been drawn and reported more than once, and each time the     }
{ tool was there under the other namespace.                                    }
{                                                                              }
{ The split is the thing worth stating: lib_ acts on .PcbLib and .SchLib, pcb_ }
{ on .PcbDoc, sch_ and obj_ on .SchDoc. Attached here rather than at the 277   }
{ call sites so it cannot be right in some of them and stale in the rest.      }
{..............................................................................}

Function MessageNamesATool(Msg : String) : Boolean;
Begin
    Result := (Pos('pcb_', Msg) > 0) Or (Pos('lib_', Msg) > 0)
           Or (Pos('sch_', Msg) > 0) Or (Pos('proj_', Msg) > 0)
           Or (Pos('obj_', Msg) > 0) Or (Pos('app_', Msg) > 0)
           Or (Pos('run_', Msg) > 0);
End;

{ What IS focused, for a refusal that only says what is not. "No schematic   }
{ document is active" with a .SchLib in front of the editor sent a session   }
{ hunting for the call that had stolen focus, when naming the .SchLib would  }
{ have made the next move obvious.                                           }
{ File name only: a full path carries the user's name into every log. DM     }
{ calls only, which never load an editor server, so this cannot raise an     }
{ uncatchable undeclared-identifier modal from inside an error path.         }
Function FocusedDocumentNote(Dummy : Integer) : String;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    Name : String;
Begin
    Result := '';
    Try
        Workspace := GetWorkspace;
        If Workspace = Nil Then Exit;
        Doc := Workspace.DM_FocusedDocument;
        If Doc = Nil Then
        Begin
            Result := 'No document has editor focus.';
            Exit;
        End;
        Name := ExtractFileName(Doc.DM_FullPath);
        If Name <> '' Then
            Result := 'The focused document is ' + Name + ' ('
                    + Doc.DM_DocumentKind + ').';
    Except
        Result := '';
    End;
End;

{ Handler messages rarely end in a full stop, so a note appended to one ran  }
{ on, live: "No schematic document is active No document has editor focus."  }
Function WithFullStop(S : String) : String;
Var
    Last : String;
Begin
    Result := S;
    If S = '' Then Exit;
    Last := Copy(S, Length(S), 1);
    If (Last <> '.') And (Last <> '!') And (Last <> '?') Then
        Result := S + '.';
End;

Function CrossDocumentHint(ErrorCode : String) : String;
Begin
    Result := '';
    If (ErrorCode = 'NO_PCBLIB') Then
        Result := 'This is the LIBRARY tool and needs a .PcbLib. The same '
                + 'operation on an open board is in the pcb_ namespace.'
    Else If (ErrorCode = 'NO_SCHLIB') Then
        Result := 'This is the LIBRARY tool and needs a .SchLib. For a '
                + 'sheet, the sch_ and obj_ tools act on the open .SchDoc.'
    Else If (ErrorCode = 'NO_PCB') Or (ErrorCode = 'NO_BOARD') Then
        Result := 'This tool acts on an open .PcbDoc. To edit a footprint '
                + 'inside a .PcbLib, use the lib_ tools instead.'
    Else If (ErrorCode = 'NO_SCHDOC') Or (ErrorCode = 'NO_SCHEMATIC') Then
        Result := 'This tool acts on an open .SchDoc sheet. To edit a '
                + 'symbol inside a .SchLib, use the lib_ tools instead.';
End;

Function BuildErrorResponseDetailed(RequestId : String; ErrorCode : String;
                                    ErrorMsg : String; DetailsJson : String) : String;
Var
    EscMsg, Ch, HexDigits, Hint, Focus : String;
    I, O : Integer;
    Pointed : Boolean;
Begin
    { The generic hint goes on only when the handler has not already    }
    { pointed somewhere itself: a specific pointer beats the generic one }
    { and must not be doubled. Decided on the handler's own words,       }
    { BEFORE the focus note is added, because a file name such as        }
    { power_lib_parts.SchLib contains "lib_" and would read as a pointer.}
    Hint := CrossDocumentHint(ErrorCode);
    Pointed := MessageNamesATool(ErrorMsg);
    If Hint <> '' Then
    Begin
        { A fact, not a pointer, so it goes on every wrong-document refusal. }
        Focus := FocusedDocumentNote(0);
        If Focus <> '' Then
            ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Focus;
        If Not Pointed Then
            ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Hint;
    End;

    // Inline escape (EscapeJsonString is not yet declared in build order).
    // Must also \u00XX-escape control and non-ASCII bytes: one raw byte
    // >127 in an error message (an accented file path, say) makes the whole
    // response unparseable on the Python side and hides the real error.
    HexDigits := '0123456789abcdef';
    EscMsg := '';
    For I := 1 To Length(ErrorMsg) Do
    Begin
        Ch := Copy(ErrorMsg, I, 1);
        O := Ord(Ch[1]);
        If O = Ord('\') Then EscMsg := EscMsg + '\\'
        Else If O = Ord('"') Then EscMsg := EscMsg + '\"'
        Else If O = 13 Then EscMsg := EscMsg + '\r'
        Else If O = 10 Then EscMsg := EscMsg + '\n'
        Else If O = 9 Then EscMsg := EscMsg + '\t'
        Else If (O < 32) Or (O >= 128) Then
            EscMsg := EscMsg + '\u00'
                + Copy(HexDigits, ((O Shr 4) And $F) + 1, 1)
                + Copy(HexDigits, (O And $F) + 1, 1)
        Else
            EscMsg := EscMsg + Ch;
    End;
    If DetailsJson = '' Then
        Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
                  ',"id":"' + RequestId + '","success":false,"data":null,' +
                  '"error":{"code":"' + ErrorCode + '","message":"' + EscMsg +
                  '","details":null}}'
    Else
        Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
                  ',"id":"' + RequestId + '","success":false,"data":null,' +
                  '"error":{"code":"' + ErrorCode + '","message":"' + EscMsg +
                  '","details":' + DetailsJson + '}}';
End;

Function BuildErrorResponse(RequestId : String; ErrorCode : String; ErrorMsg : String) : String;
Begin
    Result := BuildErrorResponseDetailed(RequestId, ErrorCode, ErrorMsg, '');
End;

Procedure EnsureWorkspaceDir(Dummy : Integer);
Begin
    If WorkspaceDir = '' Then
        WorkspaceDir := ResolveDefaultWorkspaceDir(0);
    If Not DirectoryExists(WorkspaceDir) Then
        ForceDirectories(WorkspaceDir);
End;

{..............................................................................}
{ Per-request IPC helpers.                                                      }
{                                                                               }
{ Request files: request_<id>.json, Python writes them, Pascal scans the      }
{ workspace each polling cycle and processes the first one it finds.           }
{ Response files: response_<id>.json, Pascal writes them, Python polls for    }
{ the specific path matching its own request ID.                               }
{                                                                               }
{ Per-request files eliminate the stale-response race that the old single-     }
{ file scheme had: two concurrent callers (e.g. keep-alive + user tool) used   }
{ to step on each other's response.json. With one file per request, callers    }
{ poll only their own filename and never see another's payload.                }
{                                                                               }
{ The request ID embedded in the filename is restricted to UUID-shape chars    }
{ (alphanumeric, hyphen, underscore) by IsValidRequestId, anything else is    }
{ rejected so a malformed ID can't escape the workspace dir via path tricks.   }
{..............................................................................}

Function IsValidRequestId(Id : String) : Boolean;
Var
    I, O : Integer;
    Ch : String;
Begin
    Result := False;
    If (Length(Id) < 1) Or (Length(Id) > 64) Then Exit;
    For I := 1 To Length(Id) Do
    Begin
        Ch := Copy(Id, I, 1);
        O := Ord(Ch[1]);
        If Not (((O >= Ord('0')) And (O <= Ord('9'))) Or
                ((O >= Ord('a')) And (O <= Ord('z'))) Or
                ((O >= Ord('A')) And (O <= Ord('Z'))) Or
                (Ch = '-') Or (Ch = '_')) Then
            Exit;
    End;
    Result := True;
End;

Function RequestFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'request_' + RequestId + '.json';
End;

Function ResponseFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'response_' + RequestId + '.json';
End;

Function ProgressFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'progress_' + RequestId + '.json';
End;

{ Heartbeat protocol for long-running commands.                                }
{                                                                              }
{ DelphiScript is single-threaded: while a handler is executing, the polling  }
{ loop cannot respond to anything else. A 10-second Python deadline on a      }
{ legitimately-slow operation (large project compile, multi-document iterate, }
{ heavy emit pass) fires a false timeout. Heartbeat fixes that by writing a   }
{ short-lived marker file at the start of dispatch; the Python bridge treats  }
{ marker presence as "Altium is still working on it, keep waiting".           }
{                                                                              }
{ Ordering matters: write the response file BEFORE deleting the progress     }
{ file so there is never a moment where neither exists (which would let       }
{ Python's deadline-check race the cleanup and fire a false hard-timeout).    }
{ Command text as the heartbeat may carry it: every character outside     }
{ A-Z a-z 0-9 . _ - becomes '_', so it can sit between JSON quotes with no }
{ escaping (EscapeJsonString is in Utils.pas, which builds after this).   }
Function ProgressSafeText(S : String) : String;
Var
    I : Integer;
    Ch : String;
Begin
    Result := '';
    For I := 1 To Length(S) Do
    Begin
        Ch := Copy(S, I, 1);
        If ((Ch >= 'A') And (Ch <= 'Z')) Or ((Ch >= 'a') And (Ch <= 'z'))
           Or ((Ch >= '0') And (Ch <= '9')) Or (Ch = '.') Or (Ch = '_')
           Or (Ch = '-') Then
            Result := Result + Ch
        Else
            Result := Result + '_';
    End;
End;

{ The heartbeat NAMES THE COMMAND AND THE REQUEST. While a handler sits    }
{ behind an exception modal the script engine is blocked and cannot say    }
{ what it was doing, but this file is already on disk: get_altium_status   }
{ and the stuck-handler timeout read the command from it (Python side).    }
{ RequestId is charset-checked by IsValidRequestId, so it needs no mapping. }
Procedure StartProgress(RequestId : String; Command : String);
Var
    ProgressPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    ProgressPath := ProgressFilePath(RequestId);
    Try
        WriteFileContent(ProgressPath,
            '{"started_ms":' + IntToStr(GetTickCount)
            + ',"command":"' + ProgressSafeText(Command)
            + '","request_id":"' + RequestId + '"}');
    Except End;
End;

Procedure EndProgress(RequestId : String);
Var ProgressPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    ProgressPath := ProgressFilePath(RequestId);
    Try DeleteFile(ProgressPath); Except End;
End;

{ Wipe orphaned progress_*.json files at session start. A previous run that   }
{ crashed mid-handler would leave its progress marker behind; left untouched, }
{ Python could keep extending its deadline against a stale marker.            }
Procedure CleanupOrphanProgress(Dummy : Integer);
Var
    Files : TStringList;
    I : Integer;
Begin
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'progress_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try DeleteFile(Files[I]); Except End;
    Finally
        Files.Free;
    End;
End;

{ Pick up the next request. Python writes per-request file request_<id>.json.}
{ The script enumerates request_*.json files via FindFiles (the documented   }
{ Altium DelphiScript helper; SysUtils FindFirst is not exposed to scripts)  }
{ and processes the first one it finds.                                      }
{                                                                              }
{ Per-request files on both sides, request_<id>.json + response_<id>.json, }
{ eliminate any cross-caller race: each caller publishes to its own filename }
{ and polls only its own response file.                                      }
Function ScanForRequestFile(Var FilePath : String; Var RequestId : String) : Boolean;
Var
    Files : TStringList;
    I, NameLen : Integer;
    Name, IdPart : String;
Begin
    Result := False;
    FilePath := '';
    RequestId := '';

    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'request_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
        Begin
            Name := ExtractFileName(Files[I]);
            NameLen := Length(Name);
            // FindFiles can return uppercase filenames on Windows;
            // case-insensitive prefix/suffix check.
            If (NameLen >= 14) And
               (UpperCase(Copy(Name, 1, 8)) = 'REQUEST_') And
               (UpperCase(Copy(Name, NameLen - 4, 5)) = '.JSON') Then
            Begin
                IdPart := Copy(Name, 9, NameLen - 13);
                If IsValidRequestId(IdPart) Then
                Begin
                    RequestId := IdPart;
                    FilePath := WorkspaceDir + Name;
                    Result := True;
                    Exit;
                End;
            End;
        End;
    Finally
        Files.Free;
    End;
End;

{ Write the response file directly. Earlier versions did a tmp+RenameFile    }
{ for atomicity, but DelphiScript's RenameFile silently failed for some      }
{ paths and the response never reached the final filename. The Python side  }
{ tolerates a partially-written response (json.load raises, retried until    }
{ success) so direct write is acceptable.                                    }
Procedure WriteResponseFile(RequestId : String; JsonContent : String);
Var
    FinalPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    FinalPath := ResponseFilePath(RequestId);
    WriteFileContent(FinalPath, JsonContent);
End;

{ Wipe leftover request_*.json files at session start so a previous run's    }
{ orphan can't replay against this session. A crashed loop, or a client that }
{ abandoned a long sweep, leaves request files on disk; without this the     }
{ next session would answer them all before serving anything new -- and each }
{ answer would resolve against whatever document is focused NOW, not the one }
{ the caller meant.                                                          }
{                                                                              }
{ The purged count is logged, so an operator can tell "the loop is slow" from }
{ "the loop is draining a backlog".                                          }
Procedure CleanupOrphanRequests(Dummy : Integer);
Var
    Files : TStringList;
    I, Purged : Integer;
Begin
    Purged := 0;
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'request_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try
                { Counted only when the file went: DeleteFile answers False }
                { for one it could not remove, and the log line used to     }
                { count that too.                                            }
                If DeleteFile(Files[I]) Then Inc(Purged);
            Except End;
    Finally
        Files.Free;
    End;
    If Purged > 0 Then
        AppendLog(FormatLogStamp(0) + ',0,_purge_requests,count=' + IntToStr(Purged)
            + ',0,');
End;

{ Wipe leftover response_*.json files at session start. Python deletes its   }
{ own response file after reading it, but a client that timed out (or died)  }
{ leaves the response it never collected. These accumulate forever otherwise.}
{ Only safe at startup: no client can still be waiting on a response written }
{ by a session that has already ended.                                       }
{ Hidden from the Run Script dialog by its argument; Dummy is never read. }
Procedure CleanupOrphanResponses(Dummy : Integer);
Var
    Files : TStringList;
    I, Purged : Integer;
Begin
    Purged := 0;
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'response_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try
                { Counted only when the file went: DeleteFile answers False }
                { for one it could not remove, and the log line used to     }
                { count that too.                                            }
                If DeleteFile(Files[I]) Then Inc(Purged);
            Except End;
    Finally
        Files.Free;
    End;
    If Purged > 0 Then
        AppendLog(FormatLogStamp(0) + ',0,_purge_responses,count=' + IntToStr(Purged)
            + ',0,');
End;

{ Whether StrToInt(V) is safe for a config value, so LoadMCPConfig never     }
{ hands it anything that raises (the raise is a modal before its Except).    }
{ Main.pas builds before Utils.pas, so IsIntStr is not visible here. An      }
{ optional sign, then digits only, at most nine of them after leading zeros: }
{ nine digits always fit Int32, and every config value is far below that.    }
{ Leading blanks are allowed (StrToInt skips them); a trailing one is not    }
{ (StrToInt raises on it).                                                   }
Function CfgIntOk(V : String) : Boolean;
Var
    I, StartPos, Sig : Integer;
    Ch : String;
Begin
    Result := False;
    If V = '' Then Exit;
    StartPos := 1;
    While (StartPos <= Length(V)) And (Copy(V, StartPos, 1) = ' ') Do
        StartPos := StartPos + 1;
    If StartPos > Length(V) Then Exit;
    If (Copy(V, StartPos, 1) = '-') Or (Copy(V, StartPos, 1) = '+') Then
        StartPos := StartPos + 1;
    If StartPos > Length(V) Then Exit;
    Sig := 0;
    For I := StartPos To Length(V) Do
    Begin
        Ch := Copy(V, I, 1);
        If (Ch < '0') Or (Ch > '9') Then Exit;
        If (Sig > 0) Or (Ch <> '0') Then Sig := Sig + 1;
    End;
    Result := Sig <= 9;
End;

{..............................................................................}
{ Load runtime config from mcp_config.json. The file lives in the workspace   }
{ and is the single source of truth for polling tunables. Both Python and    }
{ Pascal read from it. Missing or corrupt config leaves the defaults set by  }
{ InitDefaultConfig in place.                                                 }
{..............................................................................}

Procedure LoadMCPConfig(Dummy : Integer);
Var
    ConfigPath, Content, V : String;
    N : Integer;
Begin
    { CHECK FIRST: each value reaches StrToInt only when CfgIntOk says it    }
    { cannot raise. A hand-edited 600000.0, 1e6 or 3000000000 used to raise  }
    { inside a Try, and the raise is a modal before the Except; such a value }
    { now leaves the compiled-in default, exactly what the empty Except did. }
    { Each Try stays as a fault-only backstop (not counted): should CfgIntOk }
    { ever admit something this engine's StrToInt rejects, the default stays }
    { and the exception never escapes into StartMCPPump.                     }
    ConfigPath := WorkspaceDir + CONFIG_FILE;
    If Not FileExists(ConfigPath) Then Exit;
    Content := ReadFileContent(ConfigPath);
    If Content = '' Then Exit;

    V := ExtractJsonValue(Content, 'poll_interval_active_ms');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalActiveMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'poll_interval_idle_ms');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalIdleMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'idle_threshold');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then IdleThreshold := N; Except End; End;

    V := ExtractJsonValue(Content, 'auto_shutdown_ms');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N >= 0 Then AutoShutdownMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_iterations');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then YieldIterations := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_every_n_active');
    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then YieldEveryNActive := N; Except End; End;
End;

{..............................................................................}
{ Wire-envelope validation. Verifies that an incoming request matches the     }
{ contract Python emits, non-empty id with valid filename chars, non-empty   }
{ command, and a present (possibly empty) params object. Returns '' on        }
{ success, or a short reason string for the dispatcher to surface as          }
{ MALFORMED_REQUEST. Per-command param validation is the handler's job;       }
{ this is the universal envelope check.                                       }
{..............................................................................}

Function ValidateRequestEnvelope(RequestId, Command : String) : String;
Begin
    Result := '';
    If Not IsValidRequestId(RequestId) Then
    Begin
        Result := 'invalid request id (must be 1-64 chars of A-Z a-z 0-9 _ -)';
        Exit;
    End;
    If Command = '' Then
    Begin
        Result := 'request missing required field: command';
        Exit;
    End;
End;

{ Request dispatch and the poll timer are in StatusForm.pas (the pump's     }
{ handler must live in the form's own unit); the StartMCPServer entry point }
{ stays in Dispatcher.pas, which compiles last.                             }
