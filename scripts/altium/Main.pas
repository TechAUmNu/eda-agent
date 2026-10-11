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
    SCRIPT_VERSION = '2026.10.11.01';

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
    { compiled recently. Reset to 0 / Nil at startup.                          }
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
      as KEY*AGE (DocPathKey and the file's age then; a bare name only when
      nothing resolved - see RecordBridgeEdit for the review that made both
      rules); SaveOneDocByDocRef and SaveBridgeRecordSweep write a recorded
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
    SaveAttemptedKeys : String;
    { Free documents the save walk could not reach: a bare DM_FullPath that  }
    { names no one open document (ListedServerDoc refuses to guess). Listed  }
    { by app_save_all as free_documents_not_reached, so a pass never skips   }
    { a document silently; a bridge-edited one is written by the record      }
    { sweep from its absolute key all the same.                              }
    SaveUnresolvedPaths : String;
    { What the LAST MarkDocDirty call found, for the reply that claims
      marked_modified (MarkOutcomeJson in Utils.pas renders it: this unit
      builds before EscapeJsonString exists). MarkLastFocused is -1 when the
      focused document is not the one marked, else 0/1 for its flag. }
    MarkLastPath : String;
    MarkLastFile : String;
    FindDocVia : String;
    MarkLastVia : String;
    MarkLastFound : Boolean;
    MarkLastModified : Boolean;
    MarkLastFocused : Integer;
    MarkLastError : String;
    { True when the mark put an ABSOLUTE key on the record (the only kind  }
    { app_save_all can write from); a bare entry never sets it.            }
    MarkLastRecorded : Boolean;
    LastCompiledProject : IProject;

    { Silent cast-failure counter, incremented every time a defensive       }
    { Try/Except in an iteration helper swallows an interface cast that      }
    { ObjectId-checking should have ruled out. Surfaced via application.ping }
    { as cast_errors so a non-zero value at session end isn't invisible.     }
    CastErrorCount : Integer;

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
      rather than against anything read back off the component. }
    LastCreatedLibComponentName : String;

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
                If ServerDoc.Modified Then
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
Function DocNameCount(FilePath : String) : Integer;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I, J, ProjCount : Integer;
    Full, WantName, Keys, Key : String;
Begin
    Result := 0;
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
            If Pos('|' + Key + '|', '|' + Keys + '|') > 0 Then Continue;
            If Keys <> '' Then Keys := Keys + '|';
            Keys := Keys + Key;
            Result := Result + 1;
        End;
    End;
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
{   ABSOLUTE  the exact lookup (rejected when the document it returns names  }
{             another absolute file), then a walk that accepts a listed      }
{             document only when its DM_FullPath or its own FileName IS the  }
{             requested path (key equality, never a name match), then the    }
{             focused document on the same key equality.                     }
{   BARE      what a handler acting on the CURRENT document passes           }
{             (ProcessActiveDoc's DocumentName), so the FOCUSED document     }
{             answers first when its file name matches; otherwise the name   }
{             must be carried by exactly ONE listed document (DocNameCount,  }
{             counted before any lookup) and that document must resolve.     }
{             Several, and the request is refused as ambiguous_name.         }
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
        Focused := FocusedServerDoc(0);
        If (Focused <> Nil) And (DocNameKey(ServerDocFileName(Focused)) = WantName) Then
        Begin
            Result := Focused;
            FindDocVia := 'focused';
            Exit;
        End;
        NameHits := DocNameCount(FilePath);
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
            If LooksAbsolutePath(Trim(Got)) And (DocPathKey(Got) <> Want) Then
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
                    { file, so its own name must match too.                   }
                    If DocNameKey(Got) = WantName Then
                    Begin
                        Result := Cand;
                        FindDocVia := 'walk_name';
                        Exit;
                    End;
                End
                Else If (DocPathKey(Full) = Want) Or (DocPathKey(Got) = Want) Then
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
        If (Result <> Nil) And (DocNameKey(ServerDocFileName(Result)) <> WantName) Then
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
{   app_save_all only_modified rewrote it. A bare entry is kept only when     }
{   nothing resolved, and it answers for a file only while exactly one listed }
{   document carries the name (DocNameCount).                                  }
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
        If LooksAbsolutePath(Key) Then
        Begin
            If Key <> Want Then Continue;
            If FileAgeOrNone(Key) = BridgeEntryStamp(Entry) Then
                Result := True
            Else
                BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, Key);
            Exit;
        End;
        If (Key = DocNameKey(FilePath)) And (DocNameCount(FilePath) = 1) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{ Put a file on the record, stamped with its age NOW: a second edit after an }
{ outside save restamps it, so that edit is written too.                    }
Procedure RecordBridgeEdit(FilePath : String);
Var
    Key : String;
Begin
    Key := DocPathKey(FilePath);
    If Key = '' Then Exit;
    BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, Key);
    If BridgeEditedPaths <> '' Then BridgeEditedPaths := BridgeEditedPaths + '|';
    BridgeEditedPaths := BridgeEditedPaths + Key + '*' + IntToStr(FileAgeOrNone(FilePath));
End;

{ Drop a file once it is on disk or gone. A bare entry for its name goes     }
{ with it only when the absolute entry went too, or when this is the one     }
{ listed file of that name - the same terms a bare entry answers on - so     }
{ writing one file never strips the entry protecting its namesake.           }
Procedure ForgetBridgeEdit(FilePath : String);
Var
    Key, Before : String;
Begin
    If Trim(FilePath) = '' Then Exit;
    Key := DocPathKey(FilePath);
    Before := BridgeEditedPaths;
    BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, Key);
    If LooksAbsolutePath(Key) And ((BridgeEditedPaths <> Before)
        Or (DocNameCount(FilePath) <= 1)) Then
        BridgeEditedPaths := BridgeRecordWithout(BridgeEditedPaths, DocNameKey(FilePath));
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

Function MarkDocDirty(FilePath : String) : Boolean;
Var
    ServerDoc, Focused : IServerDocument;
    DiskName, FocusedName : String;
    FocusedMod : Boolean;
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
        End;
        Try
            ServerDoc.SetModified(True);
        Except
            MarkLastError := 'SetModified raised on the document found';
        End;
        Try
            MarkLastModified := ServerDoc.Modified;
        Except
            MarkLastModified := False;
            If MarkLastError = '' Then MarkLastError := 'the modified flag could not be read';
        End;
        { The focused document, when it is THIS file by key: the object the  }
        { tab's '*' belongs to, which may not be the object the lookup       }
        { returned. Never a namesake - only the same absolute file.          }
        Focused := FocusedServerDoc(0);
        FocusedName := ServerDocFileName(Focused);
        If (Focused <> Nil) And (Trim(DiskName) <> '')
            And (DocPathKey(FocusedName) = DocPathKey(DiskName)) Then
        Begin
            Try Focused.SetModified(True); Except End;
            FocusedMod := False;
            Try FocusedMod := Focused.Modified; Except FocusedMod := False; End;
            If FocusedMod Then MarkLastFocused := 1 Else MarkLastFocused := 0;
            If FocusedMod Then MarkLastModified := True;
        End;
    End
    Else If Not LooksAbsolutePath(Trim(FilePath)) Then
        { Nothing resolved: the bare name is all there is to keep. }
        RecordBridgeEdit(FilePath);

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
        If ServerDoc.Modified Then Result := True;
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
            If Focused.Modified Then Result := True;
        Except
            Result := False;
        End;
    End;
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

            ServerDoc := Nil;
            Try ServerDoc := Client.OpenDocument('PCB', Path); Except End;
            If ServerDoc = Nil Then Continue;

            Try Client.ShowDocument(ServerDoc); Except End;
            Try Result := PCBServer.GetCurrentPCBBoard; Except End;
            If Result <> Nil Then Break;
        End;

        If Result <> Nil Then Break;
    End;

    { Restore the user's prior view so the focus blink is invisible. }
    If (PrevView <> Nil) And (Client <> Nil) Then
        Try Client.ShowDocument(PrevView.OwnerDocument); Except End;
End;

{..............................................................................}
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
Begin
    Result := Nil;
    If Path = '' Then
    Begin
        Result := GetPCBBoardAnywhere(0);
        Exit;
    End;
    ServerDoc := Nil;
    Try ServerDoc := Client.OpenDocument('PCB', Path); Except End;
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
{ document of that name), and when that refuses (ambiguous) or misses, the    }
{ answer is Nil: NO GUESS. A plain GetDocumentByPath on a bare name can       }
{ return a namesake, and the walk would then file the namesake's write under  }
{ this listing's record entry. SaveOneDocByDocRef lists what it could not     }
{ reach (free_documents_not_reached); a bridge-edited one is written by       }
{ SaveBridgeRecordSweep from its absolute key.                                }
Function ListedServerDoc(Doc : IDocument) : IServerDocument;
Var
    Full : String;
Begin
    Result := Nil;
    If Doc = Nil Then Exit;
    Full := '';
    Try Full := Doc.DM_FullPath; Except Full := ''; End;
    If Trim(Full) = '' Then Exit;
    If LooksAbsolutePath(Trim(Full)) Then
    Begin
        Try Result := Client.GetDocumentByPath(Full); Except Result := Nil; End;
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
        Try StillMod := ServerDoc.Modified; Except StillMod := True; End;
        If StillMod Or (Not AgeIsThisTick(AgeBefore)) Then
            SaveBridgeRecordUnwritten := PathListWith(SaveBridgeRecordUnwritten, DiskPath)
        Else
            SaveBridgeRecordSameTick := PathListWith(SaveBridgeRecordSameTick, DiskPath);
    End;
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
            Try WasModified := ServerDoc.Modified; Except WasModified := True; End;
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
            { match part of a longer path.                                    }
            If Skip Then
            Begin
                If Pos('|' + UpperCase(DocPath) + '|', '|' + UpperCase(SaveSkippedPaths) + '|') = 0 Then
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
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        SaveOneDocByDocRef(Project.DM_LogicalDocuments(J));
    // The project file itself, when it's a real on-disk project
    Try
        ProjectServerDoc := Client.GetDocumentByPath(Project.DM_ProjectFullPath);
        If (ProjectServerDoc <> Nil) And ProjectServerDoc.Modified Then
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
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc <> Nil Then
        Begin
            Try
                ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
                If (ServerDoc <> Nil) And ServerDoc.Modified Then
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
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Nil;
            Try Doc := Project.DM_LogicalDocuments(J); Except End;
            If Doc = Nil Then Continue;
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

    // Real projects + their member docs
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
        SaveProjectMembers(Workspace.DM_Projects(I));

    // Free documents live inside the synthetic FreeDocumentsProject
    Try SaveProjectMembers(Workspace.DM_FreeDocumentsProject); Except End;
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
{ Returns how many it wrote whose path AgedPaths (WorkspaceDocPaths) does not }
{ hold, so documents_written can count writes the timestamp list never saw.  }
Function SaveBridgeRecordSweep(AgedPaths : String) : Integer;
Var
    Remaining, Entry, Key, DiskPath : String;
    P : Integer;
    ServerDoc : IServerDocument;
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
Procedure StartProgress(RequestId : String);
Var
    ProgressPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    ProgressPath := ProgressFilePath(RequestId);
    Try
        WriteFileContent(ProgressPath,
            '{"started_ms":' + IntToStr(GetTickCount) + '}');
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
                DeleteFile(Files[I]);
                Inc(Purged);
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
                DeleteFile(Files[I]);
                Inc(Purged);
            Except End;
    Finally
        Files.Free;
    End;
    If Purged > 0 Then
        AppendLog(FormatLogStamp(0) + ',0,_purge_responses,count=' + IntToStr(Purged)
            + ',0,');
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
    ConfigPath := WorkspaceDir + CONFIG_FILE;
    If Not FileExists(ConfigPath) Then Exit;
    Content := ReadFileContent(ConfigPath);
    If Content = '' Then Exit;

    V := ExtractJsonValue(Content, 'poll_interval_active_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalActiveMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'poll_interval_idle_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalIdleMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'idle_threshold');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then IdleThreshold := N; Except End; End;

    V := ExtractJsonValue(Content, 'auto_shutdown_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N >= 0 Then AutoShutdownMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_iterations');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then YieldIterations := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_every_n_active');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then YieldEveryNActive := N; Except End; End;
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
