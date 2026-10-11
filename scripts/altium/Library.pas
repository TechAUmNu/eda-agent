{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Library.pas - Library management functions for the Altium integration bridge                }
{..............................................................................}

{ Return a component's first linked implementation (the footprint model in   }
{ the common case). Altium does not expose a "current implementation"         }
{ getter, GetState_CurrentImplementation is not a real method, implementations}
{ are reached only through the component's child-object iterator. Nil when    }
{ the component has no implementations.                                        }
Function GetFirstSchImplementation(Comp : ISch_Component) : ISch_Implementation;
Var
    ImplIter : ISch_Iterator;
Begin
    Result := Nil;
    If Comp = Nil Then Exit;
    Try
        ImplIter := Comp.SchIterator_Create;
        If ImplIter <> Nil Then
        Begin
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Result := ImplIter.FirstSchObject;
            Comp.SchIterator_Destroy(ImplIter);
        End;
    Except
    End;
End;

{ Build a JSON array of a component's models (implementations): the footprint  }
{ / SPICE / 3D links, each with model_name, model_type, is_current, and the    }
{ datafile_links that are the model's SOURCE (entity_name, file_kind,          }
{ location). Empty array when the component carries no models. Every property  }
{ read is guarded so a malformed link never drops the whole model list.        }
Function BuildImplementationsJson(Comp : ISch_Component) : String;
Var
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    ModelName, ModelType, LinksJson, Entity, FileKind, Loc, ModelsJson : String;
    IsCur, First, LinkFirst, UseLib : Boolean;
    J, LinkCount : Integer;
Begin
    Result := '[]';
    If Comp = Nil Then Exit;
    ImplIter := Comp.SchIterator_Create;
    If ImplIter = Nil Then Exit;
    ModelsJson := '[';
    First := True;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            ModelName := '';
            ModelType := '';
            IsCur := False;
            Try ModelName := Impl.ModelName; Except End;
            Try ModelType := Impl.ModelType; Except End;
            Try IsCur := Impl.IsCurrent; Except End;
            UseLib := True;
            Try UseLib := Impl.UseComponentLibrary; Except End;

            LinksJson := '[';
            LinkFirst := True;
            LinkCount := 0;
            Try LinkCount := Impl.DatafileLinkCount; Except LinkCount := 0; End;
            For J := 0 To LinkCount - 1 Do
            Begin
                Link := Nil;
                Try Link := Impl.DatafileLink[J]; Except End;
                If Link = Nil Then Continue;
                Entity := '';
                FileKind := '';
                Loc := '';
                Try Entity := Link.EntityName; Except End;
                Try FileKind := Link.FileKind; Except End;
                Try Loc := Link.Location; Except End;
                If Not LinkFirst Then LinksJson := LinksJson + ',';
                LinkFirst := False;
                LinksJson := LinksJson +
                    '{"entity_name":"' + EscapeJsonString(Entity) + '"' +
                    ',"file_kind":"' + EscapeJsonString(FileKind) + '"' +
                    ',"location":"' + EscapeJsonString(Loc) + '"}';
            End;
            LinksJson := LinksJson + ']';

            If Not First Then ModelsJson := ModelsJson + ',';
            First := False;
            ModelsJson := ModelsJson +
                '{"model_name":"' + EscapeJsonString(ModelName) + '"' +
                ',"model_type":"' + EscapeJsonString(ModelType) + '"' +
                ',"is_current":' + BoolToJsonStr(IsCur) +
                ',"use_component_library":' + BoolToJsonStr(UseLib) +
                ',"datafile_links":' + LinksJson + '}';

            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
    ModelsJson := ModelsJson + ']';
    Result := ModelsJson;
End;

{ Set the part ownership fields on a primitive so the lib editor knows     }
{ which part of the component it belongs to. Per Altium's official         }
{ createcomp_in_lib.pas reference, primitives without OwnerPartId /        }
{ OwnerPartDisplayMode are added to the component's collection but the    }
{ editor can't display them, symbols appear empty.                        }
Procedure SetOwnerPart(Obj : ISch_GraphicalObject; Component : ISch_Component);
Begin
    If Obj = Nil Then Exit;
    If Component <> Nil Then
    Begin
        { Component is GetTargetLibComponent's answer: the held reference,   }
        { now dropped before it can go stale (LastCreatedLibIsLive and the   }
        { ForgetLibComponentIf hooks), or the editor's CurrentSchComponent,  }
        { which may not carry its members (the Lib_GetPinList measurement). }
        { No member test exists that cannot itself raise, so a catch here is }
        { COUNTED for the live probe rather than guarded.                   }
        Try Obj.OwnerPartId := Component.CurrentPartID;
        Except RecordCaughtException('SetOwnerPart:CurrentPartID'); End;
        Try Obj.OwnerPartDisplayMode := Component.DisplayMode;
        Except RecordCaughtException('SetOwnerPart:DisplayMode'); End;
    End
    Else
    Begin
        Try Obj.OwnerPartId := 1; Except End;
        Try Obj.OwnerPartDisplayMode := 0; Except End;
    End;
End;

{..............................................................................}
{ THE HELD LIBRARY COMPONENT, AND WHEN IT IS DEAD.                             }
{                                                                              }
{ LastCreatedLibComponent outlives the command that set it, because the       }
{ polling loop does. When its library is closed, or the component is removed  }
{ (lib_delete_component, lib_move_components, an overwrite), the reference    }
{ points at nothing, and the next member read on it - LibReference,           }
{ CurrentPartID - raises. Altium shows that as a MODAL before any Except     }
{ runs (Main.pas measured one caller sitting on it for over two minutes), so  }
{ the cure is never to read a member off a reference that may be dead: these  }
{ decide with pointer and string compares only.                               }
{..............................................................................}

{ Which library a SchLib is, as a key: its absolute path when it reports one   }
{ or when its bare name is carried by exactly ONE listed document (never a    }
{ namesake guess: DocNameCount counts before ResolveLoadedDocPath answers);   }
{ otherwise the bare name itself, which names the file but never locates it.  }
{ '' when the library reports no name at all.                                 }
Function RememberedLibKey(SchLib : ISch_Lib) : String;
Var
    DocName, Full : String;
Begin
    Result := '';
    If SchLib = Nil Then Exit;
    DocName := '';
    Try DocName := SchLib.DocumentName; Except DocName := ''; End;
    DocName := Trim(DocName);
    If DocName = '' Then Exit;
    Result := DocName;
    If LooksAbsolutePath(DocName) Then Exit;
    If DocNameCount(DocName) = 1 Then
    Begin
        Full := ResolveLoadedDocPath(DocName);
        If LooksAbsolutePath(Full) Then Result := Full;
    End;
End;

{ Hold Comp as the component the Lib_Add* helpers target, recording WHICH      }
{ library it lives in (LastCreatedLibPath) and the document object open for   }
{ that library now (LastCreatedLibDoc), so liveness can later be asked        }
{ without touching the component.                                             }
Procedure RememberLibComponent(SchLib : ISch_Lib; Comp : ISch_Component; Name : String);
Begin
    LastCreatedLibComponent := Comp;
    LastCreatedLibComponentName := Name;
    LastCreatedLibPath := '';
    LastCreatedLibDoc := Nil;
    If Comp = Nil Then
    Begin
        LastCreatedLibComponentName := '';
        Exit;
    End;
    LastCreatedLibPath := RememberedLibKey(SchLib);
    { Fault-only backstop (not counted), the house pattern for this lookup: }
    { a raise leaves the document unrecorded, which LastCreatedLibIsLive    }
    { already treats as unknown (today's True).                             }
    If LooksAbsolutePath(LastCreatedLibPath) Then
        Try
            LastCreatedLibDoc := Client.GetDocumentByPath(LastCreatedLibPath);
        Except
            LastCreatedLibDoc := Nil;
        End;
End;

Procedure ForgetRememberedLibComponent(Dummy : Integer);
Begin
    LastCreatedLibComponent := Nil;
    LastCreatedLibComponentName := '';
    LastCreatedLibDoc := Nil;
    LastCreatedLibPath := '';
End;

{ Is SchLib the library the held component was recorded in? True whenever    }
{ either side is unknown, which is the answer every caller gave before the    }
{ library was recorded at all; False only when the two keys name different   }
{ files (absolute keys by path, otherwise by file name).                     }
Function SameLibAsRemembered(SchLib : ISch_Lib) : Boolean;
Var
    Key : String;
Begin
    Result := True;
    If LastCreatedLibPath = '' Then Exit;
    Key := RememberedLibKey(SchLib);
    If Key = '' Then Exit;
    If LooksAbsolutePath(Key) And LooksAbsolutePath(LastCreatedLibPath) Then
        Result := (DocPathKey(Key) = DocPathKey(LastCreatedLibPath))
    Else
        Result := (DocNameKey(Key) = DocNameKey(LastCreatedLibPath));
End;

{ Can the held component still be used? CONSERVATIVE: False only on evidence  }
{ that its library has gone, never on a doubt.                                }
{ * Nothing recorded, or only a bare name: True, today's answer.             }
{ * Client.GetDocumentByPath(absolute path) = Nil: the library is closed, and }
{   closing frees every component in it. False.                              }
{ * A DIFFERENT document object is open at that path now: the library was    }
{   closed and reopened (the RefreshSchLibFromDisk case, done from outside    }
{   the bridge). False - BUT ONLY when two lookups in a row return the SAME   }
{   object, i.e. when this build hands back stable document interfaces. If   }
{   it wraps each call afresh, identity proves nothing and the answer stays  }
{   True (today's). Needs-probe: the live run confirms which.                }
Function LastCreatedLibIsLive(Dummy : Integer) : Boolean;
Var
    DocNow, DocAgain : IServerDocument;
Begin
    Result := False;
    If LastCreatedLibComponent = Nil Then Exit;
    Result := True;
    If Not LooksAbsolutePath(LastCreatedLibPath) Then Exit;
    { Fault-only backstops (not counted). A lookup that RAISES is a doubt,  }
    { not evidence the library has gone, so the answer stays True (Exit    }
    { with Result True here; DocAgain Nil below leaves it True too).        }
    DocNow := Nil;
    Try DocNow := Client.GetDocumentByPath(LastCreatedLibPath); Except Exit; End;
    If DocNow = Nil Then
    Begin
        Result := False;
        Exit;
    End;
    If LastCreatedLibDoc = Nil Then Exit;
    If DocNow = LastCreatedLibDoc Then Exit;
    DocAgain := Nil;
    Try DocAgain := Client.GetDocumentByPath(LastCreatedLibPath); Except DocAgain := Nil; End;
    If DocAgain = DocNow Then Result := False;
End;

{ Drop the held component when Comp (about to be removed from SchLib) is it:  }
{ the same reference, or the same recorded name in the same library. Call it  }
{ BEFORE every RemoveSchComponent that can remove the held one: once removed, }
{ FindLibComponentInMemory would hand it back by name, and the first member   }
{ read on it would be the modal.                                              }
{ ORDER MATTERS: a component held from ANOTHER library is never the one being }
{ removed, so it is left alone without even a pointer compare; one whose     }
{ library has gone is dropped without one. The compare is made only against  }
{ a reference whose library is open here, so it never touches a freed one on }
{ the recorded-path route.                                                   }
Procedure ForgetLibComponentIf(SchLib : ISch_Lib; Comp : ISch_Component; Name : String);
Begin
    If LastCreatedLibComponent = Nil Then Exit;
    If Not SameLibAsRemembered(SchLib) Then Exit;
    If Not LastCreatedLibIsLive(0) Then
    Begin
        ForgetRememberedLibComponent(0);
        Exit;
    End;
    If ((Comp <> Nil) And (Comp = LastCreatedLibComponent))
        Or ((Name <> '') And (Name = LastCreatedLibComponentName)) Then
        ForgetRememberedLibComponent(0);
End;

{ Comp was just renamed to NewName (obj_modify LibReference). If it is the   }
{ held component, the recorded name must follow: FindLibComponentInMemory   }
{ step 3 matches the held one by that name, so a stale name would answer    }
{ the renamed symbol for its OLD name (a false NAME_EXISTS for a new symbol }
{ of that name) and TargetLibComponentName would name it wrongly. Same      }
{ order as ForgetLibComponentIf: a held reference whose library has gone is }
{ dropped without a compare, and only a live one is compared.               }
Procedure NoteLibComponentRenamed(Comp : ISch_GraphicalObject; NewName : String);
Begin
    If LastCreatedLibComponent = Nil Then Exit;
    If Not LastCreatedLibIsLive(0) Then
    Begin
        ForgetRememberedLibComponent(0);
        Exit;
    End;
    If Comp = Nil Then Exit;
    If Comp = LastCreatedLibComponent Then
        LastCreatedLibComponentName := NewName;
End;

{ Resolve the target component for a Lib_Add* primitive helper.             }
{                                                                              }
{ SchLib.CurrentSchComponent in DelphiScript reflects the editor's selected }
{ component, which doesn't update when we add a new component via           }
{ AddSchComponent (the setter is a no-op). Trusting it would attach        }
{ primitives to whatever the editor was showing first (usually the default  }
{ Component_1 placeholder), leaving every newly-created symbol empty.       }
{                                                                              }
{ Use the global LastCreatedLibComponent we set in Lib_CreateSymbol         }
{ instead, falling back to CurrentSchComponent only if nothing has been     }
{ created in this session.                                                  }
{                                                                              }
{ A held component whose library has gone is DROPPED here, before any caller  }
{ can read a member off it (LastCreatedLibIsLive), and the lookup falls       }
{ through exactly as it does when nothing was held. CurrentSchComponent is   }
{ read only off a schematic LIBRARY: an ISch_Sheet has no such member, and   }
{ reading it there was the modal (Lib_SplitPinFunctions with a sheet         }
{ focused); Nil instead is the callers' NO_COMPONENT reply.                  }
Function GetTargetLibComponent(SchLib : ISch_Lib) : ISch_Component;
Begin
    Result := Nil;
    If (LastCreatedLibComponent <> Nil) And (Not LastCreatedLibIsLive(0)) Then
        ForgetRememberedLibComponent(0);
    Result := LastCreatedLibComponent;
    If Result <> Nil Then Exit;
    If SchLib = Nil Then Exit;
    If SchLib.ObjectId <> eSchLib Then Exit;
    Result := SchLib.CurrentSchComponent;
End;

{ The NAME of the component GetTargetLibComponent would answer, without ever  }
{ reading it back off the held reference: the recorded name is what           }
{ FindLibComponentInMemory matches the held component by, so looking it up   }
{ resolves the same component while it lives (and nothing once it has gone). }
{ Only the editor's CurrentSchComponent is asked for LibReference, as today; }
{ that read is the MarkLibDirty probe, so a catch is counted.                 }
Function TargetLibComponentName(SchLib : ISch_Lib) : String;
Var
    Cur : ISch_Component;
Begin
    Result := '';
    If (LastCreatedLibComponent <> Nil) And (Not LastCreatedLibIsLive(0)) Then
        ForgetRememberedLibComponent(0);
    If LastCreatedLibComponent <> Nil Then
    Begin
        Result := LastCreatedLibComponentName;
        Exit;
    End;
    If SchLib = Nil Then Exit;
    If SchLib.ObjectId <> eSchLib Then Exit;
    Cur := SchLib.CurrentSchComponent;
    If Cur = Nil Then Exit;
    Try
        Result := Cur.LibReference;
    Except
        RecordCaughtException('TargetLibComponentName:CurrentSchComponent.LibReference');
        Result := '';
    End;
End;

{ Mark the focused SchLib doc dirty without an immediate full-file save.    }
{ DoFileSave on a multi-MB SchLib costs hundreds of milliseconds to seconds }
{ per call, so doing it from every singular mutation (lib_add_pin,          }
{ lib_set_component_description, lib_link_footprint, ...) made one-symbol-  }
{ at-a-time editing unusable. Mirror the project-side deferred-save pattern }
{ (perf_deferred_save): mutations only flag dirty, and `save_all` /         }
{ SaveAllDirty flushes the .SchLib to disk at a logical checkpoint. The     }
{ workspace's free-document save sweep already covers standalone libs, so   }
{ no save_all changes are needed.                                            }
Procedure MarkLibDirty(SchLib : ISch_Lib);
Var
    FullPath : String;
    ServerDoc : IServerDocument;
Begin
    If SchLib = Nil Then Exit;

    { EVERY DEFERRED SAVE PASSES THROUGH HERE, which is why the follow-up
      is noted here rather than in the forty-odd handlers that call it.
      The edit is real in memory and absent from disk until a save
      runs, and a caller who does not know that sees a tool that reported
      success and changed no file. lib_save_library is named, not
      app_save_all: that one rewrites every open sheet with fresh
      UniqueIDs, which churns the owner's version control. }
    NoteNextStep('This edit is in memory only. Run lib_save_library '
        + 'with the library path to write just this library, and check '
        + 'written_to_disk in the reply (app_save_all also rewrites '
        + 'every open sheet).');

    { MARK THE LIBRARY THAT WAS EDITED, NOT WHATEVER HAPPENS TO BE FOCUSED.
      This used to dirty Workspace.DM_FocusedDocument and ignore the SchLib
      it was handed. When the focused document was anything else -- a sheet,
      or another library -- the edited library was never flagged, so it was
      skipped by app_save_all, by SaveAllDirty, and by Altium's own
      File > Save, all of which correctly decline to write a clean document.

      MEASURED 2026-09-21: a component copied into a .SchLib read back in
      full through lib_get_component_details while the file on disk stayed
      byte-identical, 662016 bytes, with zero occurrences of the new name,
      across all three save routes. The edit was real and in memory; nothing
      had asked for it to be written.

      SchLib.DocumentName is the library's OWN path. Compare the helper
      directly below, which exists to catch this same wrong-library
      confusion when resolving one. }
    FullPath := '';
    Try FullPath := SchLib.DocumentName; Except End;
    If FullPath <> '' Then
    Begin
        ServerDoc := Client.GetDocumentByPath(FullPath);
        If ServerDoc <> Nil Then
            Try ServerDoc.SetModified(True); Except End
        Else
            { NO SILENT FALLBACK TO THE FOCUSED DOCUMENT. Marking a
              different document is what caused the defect above, and it
              cannot be distinguished from success afterwards. Say so
              instead. }
            NoteNextStep('The edited library is not open as a document, so '
                + 'it could not be flagged for saving and no save will '
                + 'write it. Open it first, then repeat the edit.');
    End
    Else
        NoteNextStep('The edited library did not report its own path, so it '
            + 'could not be flagged for saving. Verify the file on disk '
            + 'changed before relying on this edit.');
    { Force a SchLib editor redraw -- without this, primitives that were just }
    { committed (lines, rectangles, pins, polygons, arcs added by Lib_Add*)   }
    { are saved to memory + disk but the open lib editor window doesn't show  }
    { them until the user manually closes and reopens the symbol. The         }
    { SchLib editor renders the CURRENT COMPONENT, not the lib document, so   }
    { SchLib.GraphicallyInvalidate alone is insufficient. Invalidate the      }
    { component too, and process pending paint messages so the new state     }
    { surfaces immediately.                                                  }
    Try SchLib.GraphicallyInvalidate; Except End;
    { NEEDS PROBE, not guarded: the Lib_GetPinList measurement says a        }
    { component taken from CurrentSchComponent may not carry its methods,    }
    { and no test of that exists that cannot itself raise. The catch is     }
    { COUNTED so the live run shows whether every edit pops this modal.      }
    Try
        If SchLib.CurrentSchComponent <> Nil Then
            SchLib.CurrentSchComponent.GraphicallyInvalidate;
    Except
        RecordCaughtException('MarkLibDirty:CurrentSchComponent.GraphicallyInvalidate');
    End;
    Try Application.ProcessMessages; Except End;
End;

{ True when the resolved SchLib really IS the document at WantPath. Guards the  }
{ silent-wrong-library failure: WorkspaceManager:OpenObject can fail to focus   }
{ the target (bad path, file missing, doc not loadable), leaving the PREVIOUS   }
{ library current. That one is still a valid eSchLib, so without this check a   }
{ handler reads the WRONG library and labels the answer with the requested      }
{ path. Compares the full path, falling back to the file name so path-format    }
{ differences do not cause false negatives.                                     }
Function SchLibIsAtPath(SchLib : ISch_Lib; WantPath : String) : Boolean;
Var
    Actual : String;
Begin
    Result := False;
    If (SchLib = Nil) Or (WantPath = '') Then Exit;
    Actual := '';
    Try Actual := SchLib.DocumentName; Except End;
    If Actual = '' Then Exit;
    If UpperCase(Actual) = UpperCase(WantPath) Then
        Result := True
    Else If UpperCase(ExtractFileName(Actual)) = UpperCase(ExtractFileName(WantPath)) Then
        Result := True;
End;

{ Focus a SchLib by path (empty = the focused document) and return it, or Nil  }
{ when no schematic library resolves. Rewrites LibPath in place to the path     }
{ actually used, so callers can report it. Mirrors the inline open used by      }
{ Lib_CopyComponent / Lib_GetComponentDetails, factored out for the model-edit  }
{ handlers.                                                                      }
Function FocusSchLib(Var LibPath : String) : ISch_Lib;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    FocusedPath : String;
Begin
    Result := Nil;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then Exit;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    Result := SchServer.GetCurrentSchDocument;
    If (Result <> Nil) And (Result.ObjectId <> eSchLib) Then Result := Nil;
    { The open above can silently fail and leave the PREVIOUS library focused. }
    If (Result <> Nil) And (Not SchLibIsAtPath(Result, LibPath)) Then Result := Nil;
End;

{ ScanLibForComponent - find a symbol by LibReference by WALKING the document. }
{                                                                              }
{ GetState_SchComponentByLibRef IS NOT ENOUGH ON ITS OWN. It answers from the  }
{ library's index, and that index only knows the components the library was    }
{ LOADED with. A symbol created in this session is invisible to it until the   }
{ library has been saved and read back.                                        }
{                                                                              }
{ MEASURED on AD26 in a brand-new empty library:                               }
{   lib_create_symbol("CLEAN_SYM")     -> succeeded, and resolved by name      }
{                                         INSIDE the creating command          }
{   lib_batch_set_params("CLEAN_SYM")  -> no component with that libref        }
{   lib_link_footprint("CLEAN_SYM")    -> target component not found           }
{ while Component_1, which came with the file, resolved throughout. So authoring}
{ a symbol and then using it in the very next call could not work at all, and  }
{ the tools reported it as a missing component rather than as a library that   }
{ had not caught up.                                                           }
{                                                                              }
{ The walk is the same one ResolveLibComponent already used for its by-INDEX   }
{ path, and it is the idiom in CompRename2.pas: SchLibIterator_Create with an  }
{ eSchComponent filter, comparing LibReference byte for byte.                  }
Function ScanLibForComponent(SchLib : ISch_Lib; Name : String) : ISch_Component;
Var
    Iter : ISch_Iterator;
    LibComp : ISch_Component;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;
    Iter := SchLib.SchLibIterator_Create;
    If Iter = Nil Then Exit;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        LibComp := Iter.FirstSchObject;
        While LibComp <> Nil Do
        Begin
            If LibComp.LibReference = Name Then
            Begin
                Result := LibComp;
                Break;
            End;
            LibComp := Iter.NextSchObject;
        End;
    Finally
        SchLib.SchIterator_Destroy(Iter);
    End;
End;

{ RefreshSchLibFromDisk - save the library and READ IT BACK IN.               }
{                                                                             }
{ A component added this session is not findable by name until the document   }
{ has been reopened. Saving alone is NOT enough, and that is the part that    }
{ wastes people's time, because the file on disk is demonstrably correct      }
{ while every lookup keeps missing.                                           }
{                                                                             }
{ MEASURED, in this order, on AD26:                                           }
{   lib_create_symbol("RESOLVE_TEST")   succeeded, and the name resolved      }
{                                       INSIDE the creating command           }
{   app_save_all                        saved:true, still_dirty:0, and the    }
{                                       saved file contains RESOLVE_TEST      }
{   every by-name lookup                still missed                          }
{   CloseObject then OpenObject         the name resolves again               }
{                                                                             }
{ So the reopen is the step that matters and it is now part of the process    }
{ rather than something a caller has to know. Save first: closing a dirty     }
{ document either loses the edits or raises a prompt nothing here can answer. }
Function RefreshSchLibFromDisk(LibPath : String) : ISch_Lib;
Var
    ServerDoc : IServerDocument;
Begin
    Result := Nil;
    If LibPath = '' Then Exit;

    { A BASENAME HERE IS WORSE THAN NOTHING. DocumentName returns one for a  }
    { free document, and CloseObject / OpenObject given a bare name do       }
    { nothing at all, so the reopen appeared to run and changed nothing.     }
    { Resolve to an absolute path or give up honestly.                       }
    LibPath := ResolveLoadedDocPath(LibPath);
    If LibPath = '' Then Exit;

    { Flush this document only. }
    Try
        ServerDoc := Client.GetDocumentByPath(LibPath);
        If ServerDoc <> Nil Then
        Begin
            Try ServerDoc.SetModified(True); Except End;
            Try ServerDoc.DoFileSave(''); Except End;
        End;
    Except End;

    { The close below frees every component in this document, so the      }
    { reference held from creation stops pointing at anything. Dropping   }
    { it here is what keeps LookupLibComponent from handing a caller a    }
    { component that no longer exists.                                    }
    ForgetRememberedLibComponent(0);

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    RunProcess('WorkspaceManager:CloseObject');

    { Closed, it holds no edits: the reopen below reads the FILE, so a record }
    { entry left over from a flush the editor declined (or one in the same    }
    { 2 s FileAge tick) would make the next save-all rewrite the clean        }
    { library - the 2026-10-06 harm (review 2026-10-11, F4). Only if the      }
    { close took: CloseObject has been seen to act on another document.       }
    ForgetBridgeEditsIfClosed(LibPath);

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    RunProcess('WorkspaceManager:OpenObject');

    { THE LIBRARY THAT WAS REOPENED, OR NOTHING. This used to return        }
    { whatever schematic document was current afterwards, and reopening a   }
    { library does not always make it current. Live 2026-09-23: a lookup   }
    { into a new, empty library searched the library focused before it,    }
    { found the part there, and lib_move_components skipped it as already  }
    { present. SchLibIsAtPath exists for exactly this.                     }
    Try Result := SchServer.GetSchDocumentByPath(LibPath); Except Result := Nil; End;
    If Result = Nil Then
        Try Result := SchServer.GetCurrentSchDocument; Except End;
    If Not SchLibIsAtPath(Result, LibPath) Then
        Result := Nil;
End;

{ FindLibComponentInMemory - the index, the walk, and the symbol created or   }
{ renamed this session. NEVER REOPENS the library.                           }
{                                                                             }
{ Use it for "does this name already exist?" before an edit. The full        }
{ LookupLibComponent reopens on a miss, and a miss is the NORMAL answer to   }
{ that question: RefreshSchLibFromDisk saves the library, closes it and     }
{ opens it again, and every reference the caller is holding -- the library,  }
{ the component it is about to rename or copy -- then points into a closed  }
{ document. The edit lands on nothing, and each later save writes the       }
{ reopened library without it.                                              }
{                                                                             }
{ Live 2026-09-23 (AD 26.10.1.6, scratch library read back from disk after  }
{ every save): lib_rename_component and lib_copy_component both answered    }
{ verified:true while the file kept the old name and never gained the copy. }
{ lib_batch_rename, which does the same remove, rename and add but never    }
{ asks whether the new name exists, persisted.                              }
{                                                                             }
{ The cost is a name created this session and no longer the last one made: }
{ it can miss here, where the reopen would have found it.                   }
Function FindLibComponentInMemory(SchLib : ISch_Lib; Name : String) : ISch_Component;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;

    Try Result := SchLib.GetState_SchComponentByLibRef(Name); Except End;
    If Result <> Nil Then Exit;

    Result := ScanLibForComponent(SchLib, Name);
    If Result <> Nil Then Exit;

    { THE SYMBOL CREATED EARLIER IN THIS SESSION. Neither the index nor the  }
    { walk can see it, but the script still holds the reference it was given }
    { when it was made, and that outlives the command because the polling    }
    { loop does. This is the case that actually bites: author a symbol, then }
    { set a parameter or link a footprint on it in the very next call.       }
    { The name is compared against the one recorded at the time, NOT read }
    { back off the interface. See LastCreatedLibComponentName in Main for }
    { what a property read on a freed component does to the session.      }
    { And ONLY while it lives, in THIS library: a held component whose     }
    { library closed is dropped (its first member read would be the modal),  }
    { and one recorded in another library is not this library's - copying X }
    { from A to B used to find A's X here and answer NAME_EXISTS for B.     }
    If (LastCreatedLibComponent <> Nil) And
       (LastCreatedLibComponentName = Name) Then
    Begin
        If Not LastCreatedLibIsLive(0) Then
            ForgetRememberedLibComponent(0)
        Else If SameLibAsRemembered(SchLib) Then
            Result := LastCreatedLibComponent;
    End;
End;

{ LookupLibComponent - the index, then the walk, then a reopen.               }
{                                                                             }
{ Use this everywhere instead of calling GetState_SchComponentByLibRef.       }
{ The third step is the one that actually finds a symbol created earlier in   }
{ the same session, see RefreshSchLibFromDisk for what was measured.          }
{ RefreshingLib guards against re-entering: the retry must not be able to     }
{ trigger another reopen.                                                     }
{                                                                             }
{ THE REOPEN INVALIDATES WHAT THE CALLER HOLDS. Fine for a read. Before an    }
{ edit, ask FindLibComponentInMemory instead.                                 }
Function LookupLibComponent(SchLib : ISch_Lib; Name : String) : ISch_Component;
Var
    LibPath : String;
    Fresh : ISch_Lib;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;

    { Everything that costs nothing and does not disturb the editor, where a }
    { reopen changes focus and the current component.                        }
    Result := FindLibComponentInMemory(SchLib, Name);
    If Result <> Nil Then Exit;

    { Last resort: the document has not caught up with its own contents. }
    If RefreshingLib Then Exit;
    LibPath := '';
    Try LibPath := SchLib.DocumentName; Except End;
    If LibPath = '' Then Exit;

    RefreshingLib := True;
    Try
        Fresh := RefreshSchLibFromDisk(LibPath);
        If Fresh <> Nil Then
            Result := FindLibComponentInMemory(Fresh, Name);
    Finally
        RefreshingLib := False;
    End;
End;

{..............................................................................}
{ Component Type (ISch_Component.ComponentKind) by name.                        }
{                                                                              }
{ ONLY TWO MEMBERS OF TComponentKind ARE PROVEN BY NAME here:                  }
{ eComponentKind_NetTie_BOM and eComponentKind_NetTie_NoBOM, written by        }
{ Gen_SetNetTie. Spelling the other five as identifiers would put names in     }
{ the script that nothing in this codebase has executed, and an undeclared     }
{ identifier faults where Try/Except cannot reach and stops the polling loop.  }
{ So they are reached by ORDINAL, the way Pin.Electrical is written from       }
{ ElectricalOrdinal in SetSchProperty.                                         }
{                                                                              }
{ The order is Altium's declaration order: Standard, Mechanical, Graphical,    }
{ NetTie_BOM, NetTie_NoBOM, Standard_NoBOM, Jumper. Two pieces of evidence     }
{ back it rather than memory. Lib_SetComponentKind reads the two named         }
{ constants at run time and refuses the numbered kinds unless they are 3 and   }
{ 4. And a saved vendor sheet stores its title block, a Graphical part, as     }
{ ComponentKind=2. Standard_NoBOM (5) and Jumper (6) rest on the order alone   }
{ until a live read-back confirms them.                                        }
{..............................................................................}

Function ComponentKindName(Ordinal : Integer) : String;
Begin
    If Ordinal = 0 Then Result := 'standard'
    Else If Ordinal = 1 Then Result := 'mechanical'
    Else If Ordinal = 2 Then Result := 'graphical'
    Else If Ordinal = 3 Then Result := 'net_tie_bom'
    Else If Ordinal = 4 Then Result := 'net_tie_no_bom'
    Else If Ordinal = 5 Then Result := 'standard_no_bom'
    Else If Ordinal = 6 Then Result := 'jumper'
    Else If Ordinal < 0 Then Result := 'unreadable'
    Else Result := 'ordinal_' + IntToStr(Ordinal);
End;

{ The word Altium's Properties panel shows, folded: "Net Tie (In BOM)" and    }
{ "Standard (No BOM)" arrive as net_tie_in_bom and standard_no_bom. A bare    }
{ ordinal 0..6 is accepted too. -1 for anything else.                         }
Function ComponentKindOrdinal(KindText : String) : Integer;
Var
    N : String;
Begin
    N := LowerCase(Trim(KindText));
    N := StringReplace(N, ' ', '_', MkSet(rfReplaceAll));
    N := StringReplace(N, '-', '_', MkSet(rfReplaceAll));
    N := StringReplace(N, '(', '', MkSet(rfReplaceAll));
    N := StringReplace(N, ')', '', MkSet(rfReplaceAll));
    Result := -1;
    If N = 'standard' Then Result := 0
    Else If N = 'mechanical' Then Result := 1
    Else If N = 'graphical' Then Result := 2
    Else If (N = 'net_tie_bom') Or (N = 'net_tie_in_bom') Then Result := 3
    Else If (N = 'net_tie_no_bom') Or (N = 'net_tie') Then Result := 4
    Else If N = 'standard_no_bom' Then Result := 5
    Else If N = 'jumper' Then Result := 6
    Else If IsIntStr(N) Then
    Begin
        Result := StrToIntDef(N, -1);
        If (Result < 0) Or (Result > 6) Then Result := -1;
    End;
End;

{..............................................................................}
{ RefetchSchLib - the library at LibPath, taken again.                         }
{                                                                              }
{ LookupLibComponent falls back to RefreshSchLibFromDisk on an index miss,     }
{ which saves, CLOSES and reopens the library. Any ISch_Lib the caller took    }
{ before that lookup then points into a closed document, and the edit's       }
{ PreProcess / PostProcess / MarkLibDirty would act on it. Every new edit      }
{ handler re-takes the library by path after the lookup. Nil when the          }
{ document at LibPath is not a SchLib any more.                                }
{..............................................................................}

Function RefetchSchLib(LibPath : String) : ISch_Lib;
Begin
    Result := Nil;
    If LibPath = '' Then Exit;
    Try Result := SchServer.GetSchDocumentByPath(LibPath); Except Result := Nil; End;
    If Result = Nil Then
        Try Result := SchServer.GetCurrentSchDocument; Except Result := Nil; End;
    If Result <> Nil Then
        If Result.ObjectId <> eSchLib Then Result := Nil;
    If Not SchLibIsAtPath(Result, LibPath) Then Result := Nil;
End;

Function Lib_CreateSymbol(Params : String; RequestId : String) : String;
Var
    Name, DesignatorPrefix, Description : String;
    SchLib : ISch_Lib;
    Component, Verify : ISch_Component;
    PartCount, ActualParts : Integer;
Begin
    Name := ExtractJsonValue(Params, 'name');
    DesignatorPrefix := ExtractJsonValue(Params, 'designator_prefix');
    Description := ExtractJsonValue(Params, 'description');
    PartCount := StrToIntDef(ExtractJsonValue(Params, 'part_count'), 1);
    If PartCount < 1 Then PartCount := 1;

    If DesignatorPrefix = '' Then DesignatorPrefix := 'U';

    // Get the current schematic library
    If SchServer = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    // Create new component. Per Altium's createcomp_in_lib.pas reference,
    // CurrentPartID and DisplayMode must be set BEFORE adding primitives;
    // primitives carry OwnerPartId/OwnerPartDisplayMode that link them to
    // a specific part of the component. Without this scaffold, primitives
    // are added but the lib editor can't display them (symbol shows empty).
    Component := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    If Component <> Nil Then
    Begin
        Component.CurrentPartID := 1;
        Component.DisplayMode := 0;
        { Multi-part symbols (quad op-amp, dual gate, etc) need PartCount  }
        { set BEFORE pin / primitive add so each primitive's OwnerPartId    }
        { can address a real sub-part.                                      }
        Try Component.PartCount := PartCount; Except End;
        Component.LibReference := Name;
        Component.Designator.Text := DesignatorPrefix + '?';
        Component.ComponentDescription := Description;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SchLib.AddSchComponent(Component);
        { AddSchComponent overrides LibReference with an auto-generated      }
        { 'Component_<N>' on the second and later additions to the same     }
        { SchLib in one session. The pre-add assignment on line 119 sticks  }
        { only for the first symbol. Re-assign here so the caller's chosen  }
        { name is what survives to disk (and what ResolveLibRef will see).  }
        Component.LibReference := Name;
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        // Broadcast as a new component (source=nil, dest=c_BroadCast). This
        // is the pattern in Altium's createcomp_in_lib.pas, different from
        // the per-primitive SchRegisterObject(Container, Obj) which sends
        // from the container.
        Try
            SchServer.RobotManager.SendMessage(
                Nil, Nil, SCHM_PrimitiveRegistration,
                Component.I_ObjectAddress);
        Except End;

        { PartCount is re-asserted for the same reason LibReference is, just  }
        { above: AddSchComponent does not necessarily keep what was set on    }
        { the detached object.                                                }
        If PartCount > 1 Then
            Try Component.PartCount := PartCount; Except End;

        SchLib.CurrentSchComponent := Component;
        RememberLibComponent(SchLib, Component, Name);

        // Refresh the library editor view so the new component is visible.
        Try SchLib.GraphicallyInvalidate; Except End;

        MarkLibDirty(SchLib);

        { VERIFY. This used to return success with part_count ECHOED from the }
        { request. Measured on AD26: part_count=3 produced NO COMPONENT AT     }
        { ALL, and the reply still said success with part_count 3. The same    }
        { call with part_count=1 worked, so the failure is specific and silent.}
        { Resolving the symbol by its LibReference is the cheapest proof it    }
        { exists, and the count reported is the one READ BACK.                 }
        Verify := Nil;
        Try Verify := LookupLibComponent(SchLib, Name); Except End;

        If Verify = Nil Then
            Result := BuildSuccessResponse(RequestId,
                JsonObj(
                    JsonBool('success', False) + ',' +
                    JsonStr('name', Name) + ',' +
                    JsonInt('requested_part_count', PartCount) + ',' +
                    JsonStr('reason', 'the symbol does not resolve in the '
                        + 'library after being added. A part_count above 1 is '
                        + 'the known trigger: Altium wants a multi-part '
                        + 'component to carry primitives for each part. '
                        + 'Create it single-part, add pins with '
                        + 'owner_part_id, then raise the part count in the '
                        + 'library editor.')
                ))
        Else
        Begin
            ActualParts := PartCount;
            Try ActualParts := Verify.PartCount; Except End;
            Result := BuildSuccessResponse(RequestId,
                JsonObj(
                    JsonBool('success', True) + ',' +
                    JsonStr('name', Name) + ',' +
                    JsonInt('part_count', ActualParts) + ',' +
                    JsonBool('verified', True)
                ));
        End;
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create symbol');
End;

{ Lib_SetCurrentComponent: make a named component the "current" one in    }
{ the SchLib editor so subsequent SchIterator-based commands (modify_objects }
{ on ePin / eRectangle / eParameter via active_doc scope) target it. The    }
{ asymmetry this fixes: GetState_SchComponentByLibRef is a read-only fetch  }
{ that does NOT update the editor's selection -- without this command, the  }
{ SchLib editor stays pointed at whatever was last manually clicked (or     }
{ the first component on load), so modify_objects silently hits the wrong  }
{ component when the caller thinks they switched.                          }
{ Switch the active SchLib's current component to the named symbol and      }
{ return it (Nil on any failure: no SchServer, no active SchLib, or no      }
{ component with that lib-ref). Shared by Lib_SetCurrentComponent and the   }
{ lib_component scope handling in the generic primitives, so a caller can   }
{ target a library symbol without a separate set_current_component round-   }
{ trip.                                                                      }
{ CurrentLibPartId - which part the SchLib editor is DISPLAYING.              }
{                                                                             }
{ Deliberately read from the document, not from Component.CurrentPartID.      }
{ Those two disagree: the component property accepts any value, while the     }
{ document reports what is on screen, and the iterator follows the document.  }
{ Returns -1 when the document does not answer, so a caller can tell "part 1" }
{ from "no idea".                                                             }
Function CurrentLibPartId(SchLib : ISch_Lib) : Integer;
Begin
    Result := -1;
    Try Result := SchLib.GetState_CurrentSchComponentPartId; Except End;
End;

{ StepLibComponentPartTo - move the editor's displayed part to Target.        }
{                                                                             }
{ Assigning CurrentPartID does not move it; the editor's own command does.    }
{ Stepping is the only route found in working code (two independent scripts   }
{ in reference/ drive SCH:NextComponentPart and check the document's part id  }
{ after each step), so it is used here rather than a property that reports    }
{ success and changes nothing.                                                }
{                                                                             }
{ ONE STEP, NOT A SEARCH, and this is the second version of this function.   }
{ The first walked NextComponentPart until the DOCUMENT reported the target.  }
{ That walk is unnecessary, because the command's destination is determined:  }
{                                                                             }
{   Component.CurrentPartID := K   sets the property and does NOT move the    }
{                                  displayed part.                            }
{   SCH:NextComponentPart          moves the display to CurrentPartID + 1     }
{                                  and syncs the property to where it landed. }
{                                                                             }
{ So parking the property one below the target and stepping once arrives at   }
{ the target directly. Reported against a 4-part TPS23881B on AD 26.8.1.31    }
{ (GH #11), where it was verified by prediction rather than observation: with }
{ the display on part 4 and CurrentPartID set to 1, the model says the step   }
{ lands on part 2, and it did, returning exactly the 17 pins part 2 holds.    }
{                                                                             }
{ WHY THE WALK HAD TO GO, and it is not tidying. The walk was gated on a      }
{ readback it could not count on: GetState_CurrentSchComponentPartId is       }
{ DECLARED on that build but returns -1 at runtime, and the guard treated     }
{ "cannot read" as "nothing to check" and returned True WITHOUT STEPPING AT   }
{ ALL. So a query scoped to part 3 was answered about whatever part happened  }
{ to be displayed, reporting success, which is the exact defect the walk was  }
{ added to stop. A guard that passes in precisely the case it exists to catch }
{ is worse than no guard, because the caller stops looking.                   }
{                                                                             }
{ Target is always >= 2 here: the caller only steps when a part was named,    }
{ and part 1 is where selecting the component already leaves the editor.      }
{ That matters, because the step cannot REACH part 1 (CurrentPartID clamps    }
{ at 1, so stepping from it goes to 2) and SCH:PrevComponentPart does not     }
{ exist; Altium accepts the unknown process name and does nothing.            }
{                                                                             }
{ VERIFIED TWO WAYS, and it now fails closed. The document's part id is still }
{ preferred. When it cannot be read, CurrentPartID is read back INSTEAD, and  }
{ that readback is meaningful only because of the order above: we parked it   }
{ at Target - 1 ourselves, so if the step did nothing it still reads          }
{ Target - 1, and only a step that actually moved makes it read Target. That  }
{ is why the property is trusted here and nowhere else, and why the old note  }
{ against reading it does not apply: it is not being asked what is displayed, }
{ it is being asked whether the command ran.                                  }
Function StepLibComponentPartTo(SchLib : ISch_Lib; Component : ISch_Component;
    Target : Integer) : Boolean;
Var
    Count, Seen, Parked : Integer;
Begin
    Result := False;

    Count := 1;
    Try Count := Component.PartCount; Except End;
    If Count < 1 Then Count := 1;

    { A symbol with one part has nowhere to go and nothing to verify. }
    If (Count <= 1) And (Target <= 1) Then
    Begin
        Result := True;
        Exit;
    End;

    Parked := Target - 1;
    If Parked < 1 Then Parked := 1;
    Try Component.CurrentPartID := Parked; Except End;

    ResetParameters;
    RunProcess('SCH:NextComponentPart');

    Seen := CurrentLibPartId(SchLib);
    If Seen >= 0 Then
    Begin
        Result := (Seen = Target);
        Exit;
    End;

    { Document silent. Did the command move the property off where we put it? }
    Seen := -1;
    Try Seen := Component.CurrentPartID; Except End;
    Result := (Seen = Target);
End;

{ DisplayedPartByPins - which part the SchLib editor is showing, judged by    }
{ the pins the iterator actually yields.                                       }
{                                                                              }
{ This is the SAME iterator a lib_component query answers from, so it tests   }
{ exactly what the caller is about to receive rather than a proxy for it.    }
{ Needed because the document's own part id is declared but returns -1 on    }
{ AD 26.8.1.31, which is the build GH #11 reported from.                     }
{                                                                              }
{ Returns the single OwnerPartId seen on a part-specific pin, 0 when only    }
{ shared (OwnerPartId 0) pins or no pins are visible, and -1 when pins from  }
{ more than one part appear, which should not happen and is not trusted.     }
Function DisplayedPartByPins(SchLib : ISch_Lib) : Integer;
Var
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    Owner, Seen : Integer;
Begin
    Result := 0;
    Seen := 0;
    Iter := SchLib.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePin));
        Pin := Iter.FirstSchObject;
        While Pin <> Nil Do
        Begin
            Owner := 0;
            Try Owner := Pin.OwnerPartId; Except End;
            If Owner > 0 Then
            Begin
                If Seen = 0 Then
                Begin
                    Seen := Owner;
                End
                Else If Seen <> Owner Then
                Begin
                    Seen := -1;
                End;
            End;
            Pin := Iter.NextSchObject;
        End;
    Finally
        SchLib.SchIterator_Destroy(Iter);
    End;
    Result := Seen;
End;

{ PartOneEvidence - 1 when the editor provably shows part 1, -1 when it       }
{ provably shows some other part, 0 when nothing present can tell.            }
{                                                                              }
{ PINS ONLY. This used to ask the document's own part id first, and that is   }
{ not evidence here: ReachLibPartOne assigns CurrentPartID := 1 just before   }
{ asking. Live on AD 26.10.1.6 (2026-09-23), @1 was accepted while the editor }
{ still showed part 3 and the pins this iterator yields were part 3's; the    }
{ document's answer was the only thing that could have passed it.            }
Function PartOneEvidence(SchLib : ISch_Lib) : Integer;
Var
    Seen : Integer;
Begin
    Seen := DisplayedPartByPins(SchLib);
    If Seen = 1 Then
    Begin
        Result := 1;
    End
    Else If Seen = 0 Then
    Begin
        Result := 0;
    End
    Else
    Begin
        Result := -1;
    End;
End;

{ A COMPONENT READER ON A FILE THAT IS NOT THERE. CreateLibCompInfoReader /  }
{ ReadAllComponentInfo read the library FILE (Lib_GetComponents), and the    }
{ bridge authors libraries unsaved, so an open .SchLib often has no file     }
{ yet; SafeSchLibPath passes a missing .SchLib through unchanged and the     }
{ reader then raises, which Altium shows as a modal before any Except runs.  }
{ True only for an ABSOLUTE path that is not on disk: '' (an IntLib with no  }
{ source found) and a relative path keep their existing routes. A caller    }
{ that sees True proceeds exactly as for a reader with no components, which }
{ is all a reader of a missing file can yield.                              }
Function LibFileMissing(LibPath : String) : Boolean;
Var
    P : String;
Begin
    P := Trim(SafeSchLibPath(LibPath));
    Result := (P <> '') And LooksAbsolutePath(P) And (Not FileExists(P));
End;

{ OtherLibComponentName - the name of any component in this library other     }
{ than Name, or '' when there is none.                                        }
{                                                                              }
{ CreateLibCompInfoReader, not SchIterator: an eSchComponent iterator returns }
{ nothing at all on a SchLib, because each symbol is its own internal sheet   }
{ rather than a component placed on the library's canvas.                    }
Function OtherLibComponentName(SchLib : ISch_Lib; Name : String) : String;
Var
    Reader : ILibCompInfoReader;
    Info : IComponentInfo;
    I, N : Integer;
    DocName : String;
Begin
    Result := '';
    Reader := Nil;
    DocName := '';
    Try DocName := SchLib.DocumentName; Except DocName := ''; End;
    { Candidates come from the FILE; a library not saved yet has none, the  }
    { same answer as an empty reader (ReachLibPartOne's existing note).     }
    If LibFileMissing(DocName) Then Exit;
    Try Reader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(DocName)); Except End;
    If Reader = Nil Then Exit;
    Try
        Try Reader.ReadAllComponentInfo; Except End;
        N := 0;
        Try N := Reader.NumComponentInfos; Except End;
        For I := 0 To N - 1 Do
        Begin
            Info := Reader.ComponentInfos[I];
            If Info <> Nil Then
            Begin
                If Info.CompName <> Name Then
                Begin
                    Result := Info.CompName;
                    Break;
                End;
            End;
        End;
    Finally
        Try SchServer.DestroyCompInfoReader(Reader); Except End;
    End;
End;

{ ReachLibPartOne - make part 1 the displayed part, and prove it.              }
{                                                                              }
{ Part 1 cannot be reached by stepping: SCH:NextComponentPart moves to       }
{ CurrentPartID + 1, CurrentPartID clamps at 1, so a step from it lands on 2, }
{ and SCH:PrevComponentPart does not exist. What DOES reset the display to    }
{ part 1 is selecting a DIFFERENT component and then reselecting this one;    }
{ reassigning the same component leaves the display where it was. Measured   }
{ 2026-09-19 on a purpose-built 4-part symbol, and confirmed independently    }
{ in GH #11 on a 4-part part.                                                 }
{                                                                              }
{ The bounce is only done when part 1 is not already provably showing, so a  }
{ lookup that is already right moves nothing.                                }
{                                                                              }
{ After the bounce, success means no pin from another part is visible. That  }
{ is the property that matters: the #11 defect was answering about part 4    }
{ when part 1 was asked for, and a query cannot do that while the iterator   }
{ shows nothing from part 4. A part 1 carrying only shared pins reads as     }
{ "nothing can tell", which after the measured reset is accepted.            }
{                                                                              }
{ A LIBRARY WITH ONE COMPONENT HAS NOTHING TO BOUNCE OFF. There is then no    }
{ known way back to part 1 once the display has left it, and this says so    }
{ rather than answering about whichever part is showing.                     }
Function ReachLibPartOne(SchLib : ISch_Lib; Component : ISch_Component;
    Name : String) : Boolean;
Var
    Count, Evidence : Integer;
    OtherName : String;
    Other : ISch_Component;
Begin
    Result := False;

    Count := 1;
    Try Count := Component.PartCount; Except End;
    If Count <= 1 Then
    Begin
        Result := True;
        Exit;
    End;

    If PartOneEvidence(SchLib) = 1 Then
    Begin
        Result := True;
        Exit;
    End;

    OtherName := OtherLibComponentName(SchLib, Name);
    If OtherName = '' Then
    Begin
        { "Saved" is deliberate: candidates are read from the file on disk,
          so a component created this session and not yet saved is not one. }
        NoteNextStep('Part 1 of ' + Name + ' cannot be reached: the display '
            + 'is on another part, and the saved library holds no other '
            + 'component to reselect from, which is the only known way back '
            + 'to part 1. A component created this session counts once the '
            + 'library is saved. Otherwise select part 1 by hand in the '
            + 'library editor.');
        Exit;
    End;

    Other := Nil;
    { Through the wrapper, never the raw index: the index only knows   }
    { what the library was LOADED with. The name came from the file on  }
    { disk, so this resolves at the wrapper's first step and never     }
    { reaches its close-and-reopen last resort.                         }
    Other := LookupLibComponent(SchLib, OtherName);
    If Other = Nil Then Exit;

    { The editor acts on a selection when it processes its messages, not  }
    { when the property is assigned. The measured reset was three separate }
    { calls with the UI running between them; done back to back inside one }
    { handler, the display stayed on part 3 (live, 2026-09-23).            }
    Try SchLib.CurrentSchComponent := Other; Except End;
    Try Application.ProcessMessages; Except End;
    Try SchLib.CurrentSchComponent := Component; Except End;
    Try Application.ProcessMessages; Except End;
    Try Component.CurrentPartID := 1; Except End;

    Evidence := PartOneEvidence(SchLib);
    Result := (Evidence >= 0);
    If Not Result Then
        NoteNextStep('Part 1 of ' + Name + ' was not reached: after '
            + 'reselecting the component the editor still shows pins from '
            + 'another part.');
End;

{ SelectLibComponentPart - focus a library symbol and make PART PartId the    }
{ active one. A SchLib iterator only ever yields the CURRENT part's           }
{ primitives, so on a multi-part symbol every query, modify and delete sees   }
{ part 1 alone unless the caller can move the part pointer. PartId <= 0 keeps }
{ the historical part-1 behaviour.                                            }
Function SelectLibComponentPart(Name : String; PartId : Integer) : ISch_Component;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Target, Count : Integer;
Begin
    Result := Nil;
    If (Name = '') Or (SchServer = Nil) Then Exit;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then Exit;

    Component := LookupLibComponent(SchLib, Name);
    If Component = Nil Then Exit;

    SchLib.CurrentSchComponent := Component;
    RememberLibComponent(SchLib, Component, Name);

    { Reset PartID + DisplayMode so subsequent Lib_AddSymbol* calls write     }
    { their primitives onto a VISIBLE normal-mode part. Without this, after a }
    { fresh SchLib reopen Component.CurrentPartID can be 0 (no part) and      }
    { AddSchObject silently succeeds but the primitive lands on an invisible  }
    { bucket -- explains the "line added with success but no eLine in         }
    { query_objects" behaviour observed on a live symbol. The reset stays,    }
    { and the only                                                            }
    { change is WHICH part it selects when the caller asks for one.           }
    Target := 1;
    If PartId > 1 Then
    Begin
        Count := 1;
        Try Count := Component.PartCount; Except End;
        { PartCount can read high by one on some symbols; clamp rather than  }
        { refuse, and never below 1.                                          }
        If (Count > 0) And (PartId <= Count) Then
            Target := PartId
        Else
            Target := PartId;   { let Altium reject an out-of-range id }
    End;
    Try Component.CurrentPartID := Target; Except End;
    Try Component.DisplayMode := 0; Except End;

    { Assigning CurrentPartID is NOT enough, and this is the whole bug        }
    { reported in GH #11 against a 4-part TPS23881B: the property takes the   }
    { value, the editor's part spinner does not move, and the SchLib iterator }
    { follows the DISPLAYED part. So every query returned part 1 while the    }
    { scope said part 3, and with the spinner moved by hand the suffix was    }
    { ignored outright. Nothing errored either way.                           }
    {                                                                          }
    { The displayed part is moved by the editor's own command, not by a       }
    { property. StepLibComponentPartTo parks CurrentPartID one below the      }
    { target and issues one SCH:NextComponentPart, which lands on the target  }
    { and syncs the property to it; see the note on that function for why it  }
    { no longer walks, and for the readback that used to fail open.           }
    { NIL RATHER THAN THE WRONG PART. Returning the component when the
      editor never reached the requested part is exactly what GH #11
      reported: a query scoped to part 3 answered about part 1 and
      nothing said so. Nil makes the scope resolve to NOT_FOUND, which
      is a caller can act on. }
    {
      ONLY WHEN A PART WAS ACTUALLY ASKED FOR. This gate used to read
      `If Target > 0`, and Target is never below 1, so the step-and-verify
      ran on EVERY lookup including the plain by-name one. When the editor
      reported a part id that was readable but not 1, the walk failed and
      this returned Nil, so a component that demonstrably existed came back
      as "not found".

      MEASURED: lib_link_footprint and lib_batch_rename both refused
      SWEEP_SYM_A and SWEEP_SYM_C while lib_get_component_details and
      lib_get_pin_list resolved the same names in the same session, because
      those two go straight to GetState_SchComponentByLibRef and never step.

      The GH #11 protection is about a caller asking for part 3 and being
      answered about part 1. That only arises when a part was named, which
      is what PartId > 1 means. A single-part lookup has nothing to verify.
    }
    If PartId > 1 Then
    Begin
        If Not StepLibComponentPartTo(SchLib, Component, Target) Then
        Begin
            { THE DISPLAY HAS MOVED EVEN THOUGH THE TARGET WAS NOT REACHED.
              Stepping happens before the check, so a part that cannot be
              reached still leaves the editor somewhere other than where it
              started, and this build cannot read back where that is. Left
              unsaid it compounds badly: the caller does not know which part
              is showing, and scope @1 is the one suffix that cannot be
              trusted to return to part 1. Reported GH #11, 2026-09-22. }
            NoteNextStep('Part ' + IntToStr(Target) + ' was not reached, and '
                + 'the displayed part has moved. Select a different '
                + 'component and reselect this one to return to part 1; a '
                + 'suffixed scope cannot reliably do it.');
            Result := Nil;
            Exit;
        End;
    End;

    { EXPLICIT PART 1, and only explicit. PartId 0 is the plain lookup with no
      suffix, which every lib_ tool reaches through SelectLibComponent and
      which must stay exactly as it was: when the step-and-verify once ran on
      every lookup, lib_link_footprint and lib_batch_rename refused
      components that demonstrably existed. PartId 1 now means "@1 was
      written", and that is a request for part 1 that has to be honoured
      rather than answered about whichever part happens to be displayed.
      Reported GH #11, 2026-09-22: with the editor on part 3, @1 returned
      part 3's pins. }
    If PartId = 1 Then
    Begin
        If Not ReachLibPartOne(SchLib, Component, Name) Then
        Begin
            Result := Nil;
            Exit;
        End;
    End;

    Try SchLib.GraphicallyInvalidate; Except End;
    Result := Component;
End;

Function SelectLibComponent(Name : String) : ISch_Component;
Begin
    { 0, NOT 1. Zero is the plain lookup and takes the historical   }
    { path unchanged; 1 now means an explicit @1 and is verified.  }
    Result := SelectLibComponentPart(Name, 0);
End;

Function Lib_SetCurrentComponent(Params : String; RequestId : String) : String;
Var
    Name : String;
    Component : ISch_Component;
Begin
    Name := ExtractJsonValue(Params, 'name');
    If Name = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_NAME', 'name is required');
        Exit;
    End;

    Component := SelectLibComponent(Name);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found in active library: ' + Name);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"name":"' + EscapeJsonString(Name) + '"}');
End;

Function Lib_AddPin(Params : String; RequestId : String) : String;
Var
    Designator, Name, ElecType : String;
    X, Y, Length, Rotation : Integer;
    Hidden : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Pin : ISch_Pin;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Name := ExtractJsonValue(Params, 'name');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Length := StrToIntDef(ExtractJsonValue(Params, 'length'), 200);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    ElecType := ExtractJsonValue(Params, 'electrical_type');
    Hidden := ExtractJsonValue(Params, 'hidden') = 'true';

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
    If Pin <> Nil Then
    Begin
        Pin.Designator := Designator;
        Pin.Name := Name;
        Pin.Location.X := MilsToCoord(X);
        Pin.Location.Y := MilsToCoord(Y);
        Pin.PinLength := MilsToCoord(Length);
        Pin.Orientation := Rotation Div 90;
        Pin.IsHidden := Hidden;

        { The shared parser, not an inline chain: the batch path        }
        { (Lib_AddPins) already used StrToPinElectrical, and this       }
        { path's inline copy was case- and underscore-SENSITIVE with    }
        { no aliases, so 'Input' made a passive pin here and an input   }
        { pin there. One vocabulary, stated once, in Utils.pas.         }
        Pin.Electrical := StrToPinElectrical(ElecType);

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Pin, Component);
        Component.AddSchObject(Pin);
        SchRegisterObject(Component, Pin);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pin');
End;

Function Lib_AddSymbolRectangle(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    FillColorStr, BorderColorStr : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Rect : ISch_Rectangle;
    Loc : TLocation;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    FillColorStr := ExtractJsonValue(Params, 'fill_color');
    BorderColorStr := ExtractJsonValue(Params, 'border_color');

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    If Rect <> Nil Then
    Begin
        { Read-modify-write the TLocation record; direct `.X := value` on the }
        { Location property is a write to a record COPY and is silently       }
        { discarded (the rect retains its default 0,0 / 500,500 from the      }
        { factory). Same fix is applied in Lib_AddSymbolLine and Generic.pas. }
        Loc := Rect.Location;
        Loc.X := MilsToCoord(X1);
        Loc.Y := MilsToCoord(Y1);
        Rect.Location := Loc;
        Loc := Rect.Corner;
        Loc.X := MilsToCoord(X2);
        Loc.Y := MilsToCoord(Y2);
        Rect.Corner := Loc;

        { Colours are OPTIONAL and only touched when supplied, so a caller }
        { that sends neither gets exactly the outline it got before.       }
        {                                                                   }
        { IsSolid is the reason fill_color did nothing: it was pinned False }
        { here, so an AreaColor would never have been drawn. A supplied     }
        { fill therefore turns the rectangle solid as well, which is what   }
        { lib_create_ic_symbol has been asking for all along by sending     }
        { Altium's pale-yellow body colour and getting a hollow box.        }
        { -1 is the tool's documented "no fill" sentinel and is what the   }
        { parameter DEFAULTS to, so it arrives on nearly every call.       }
        { Treating any non-empty value as a fill would have turned every   }
        { symbol rectangle solid in colour -1.                              }
        Rect.IsSolid := False;
        If BorderColorStr <> '' Then
            Try Rect.Color := StrToIntDef(BorderColorStr, 0); Except End;
        If (FillColorStr <> '') And (StrToIntDef(FillColorStr, -1) >= 0) Then
            Try
                Rect.AreaColor := StrToIntDef(FillColorStr, 0);
                Rect.IsSolid := True;
            Except End;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Rect, Component);
        Component.AddSchObject(Rect);
        SchRegisterObject(Component, Rect);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create rectangle');
End;

Function Lib_AddSymbolLine(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, Width : Integer;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Line : ISch_Line;
    Loc : TLocation;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1);
    If Width < 0 Then Width := 0;
    If Width > 3 Then Width := 3;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
    If Line <> Nil Then
    Begin
        { Read-modify-write -- direct `Line.Location.X := value` writes to a }
        { record copy and is silently discarded, leaving the line at its     }
        { default 0,0 / 0,0 (zero-length, invisible, not added to the        }
        { component). Confirmed broken when 12 lib_add_symbol_line           }
        { calls all reported success but no eLine objects were on the symbol. }
        Loc := Line.Location;
        Loc.X := MilsToCoord(X1);
        Loc.Y := MilsToCoord(Y1);
        Line.Location := Loc;
        Loc := Line.Corner;
        Loc.X := MilsToCoord(X2);
        Loc.Y := MilsToCoord(Y2);
        Line.Corner := Loc;
        Line.LineWidth := Width;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Line, Component);
        Component.AddSchObject(Line);
        SchRegisterObject(Component, Line);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        { Force the lib editor to redraw -- without this, primitives are    }
        { committed but not visible until the user closes and reopens the   }
        { symbol. Same fix applied to other lib_add_symbol_* helpers.       }
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create line');
End;

Function Lib_CreateFootprint(Params : String; RequestId : String) : String;
Var
    Name, Description : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
Begin
    Name := ExtractJsonValue(Params, 'name');
    Description := ExtractJsonValue(Params, 'description');

    { PcbLibMayBeCurrent (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway, so the reply below comes without the modal. }
    PcbLib := Nil;
    If PcbLibMayBeCurrent(0) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PCBServer.CreatePCBLibComp;
    If Footprint <> Nil Then
    Begin
        Footprint.Name := Name;
        Footprint.Description := Description;

        PcbLib.RegisterComponent(Footprint);
        PcbLib.CurrentComponent := Footprint;

        Result := BuildSuccessResponse(RequestId, '{"success":true,"name":"' + EscapeJsonString(Name) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create footprint');
End;

{ FootprintOriginX / Y - the footprint's own origin, in board coordinates.    }
{                                                                             }
{ EVERY AUTHORING CALL HERE MUST OFFSET BY THIS. A PcbLib footprint does not  }
{ sit at the board coordinate origin: PCBServer.CreatePCBLibComp leaves it at }
{ Altium's library origin, measured at 50000,50000 mils on AD26. The add      }
{ handlers used to write MilsToCoord(X) straight into Pad.X, which is an      }
{ ABSOLUTE board coordinate, so a pad asked for at -50 mils landed 50050 mils }
{ from the footprint it belonged to.                                          }
{                                                                             }
{ MEASURED both ways. A footprint authored by these tools reported its pads   }
{ at x_mm -1271.27 where -1.27 was asked for, and placing it on a board gave  }
{ a component with a 52452 x 50144 mil bounding box, a part over four feet    }
{ across. A hand-authored footprint from a real library reads 0.0 and 2.54,   }
{ which is what relative-to-origin looks like and what the tools claim to     }
{ take.                                                                       }
{                                                                             }
{ Reading the origin rather than forcing it to zero also fixes footprints     }
{ that were imported with an origin of their own.                             }
Function FootprintOriginX(Footprint : IPCB_LibComponent) : TCoord;
Begin
    Result := 0;
    If Footprint = Nil Then Exit;
    Try Result := Footprint.X; Except End;
End;

Function FootprintOriginY(Footprint : IPCB_LibComponent) : TCoord;
Begin
    Result := 0;
    If Footprint = Nil Then Exit;
    Try Result := Footprint.Y; Except End;
End;

{ ResolveLayerName - Human name for any TLayer, including the mechanical      }
{ layers above 16 that GetLayerString cannot map (it returns 'Unknown' for    }
{ them, and a modern Altium build encodes those as large ordinals such as     }
{ 0x04000012). Falls back to the board's own layer stack, which names every   }
{ layer the document actually has. Returns '' if nothing can name it, so the  }
{ caller can fall back to the raw ordinal.                                    }
{ (Moved up from beside Lib_GetLibraryGeometry so the add handlers below can   }
{ echo a resolved layer: DelphiScript has no forward declarations.)            }
Function ResolveLayerName(Board : IPCB_Board; Lyr : TLayer) : String;
Var
    LayerObj : IPCB_LayerObject_V7;
    Stack : IPCB_LayerStack_V7;
Begin
    Result := '';
    Try Result := GetLayerString(Lyr); Except End;
    If (Result <> '') And (Result <> 'Unknown') Then Exit;
    Result := '';
    If Board = Nil Then Exit;
    Try
        Stack := Board.LayerStack_V7;
        If Stack <> Nil Then
        Begin
            LayerObj := Stack.LayerObject_V7[Lyr];
            If LayerObj <> Nil Then Result := LayerObj.Name;
        End;
    Except
        Result := '';
    End;
End;

{..............................................................................}
{ ResolveLibFootprint - the footprint a footprint-authoring call writes into.  }
{                                                                              }
{ ONE RESOLVER FOR EVERY ADD HANDLER. Before this, only Lib_AddFootprintText   }
{ could name its footprint; pads, tracks and arcs wrote into whatever          }
{ PcbLib.CurrentComponent happened to be, so the one reliable way to aim a     }
{ track at a footprint was to stamp a text on it first.                        }
{                                                                              }
{ Params read here, so every caller accepts them without repeating the code:   }
{   library_path   - optional .PcbLib; focused first, and VERIFIED focused     }
{                    afterwards, the way Lib_DeleteFootprintPrimitives does,   }
{                    because an OpenObject that silently fails leaves the      }
{                    previous library in front and the write would land there. }
{   component_name - optional footprint name (footprint_name is accepted as   }
{                    the same thing). Empty means the library's current one.  }
{                                                                              }
{ A NAMED FOOTPRINT IS MADE CURRENT, AND THE SWITCH IS VERIFIED. Every add     }
{ handler registers its primitive with the footprint AND with PcbLib.Board,    }
{ and Lib_SetDesignator records why the second matters here:                   }
{ Board.AddPCBObject attaches to the library's CURRENT component. A footprint  }
{ that is named but not current would get the primitive through               }
{ Footprint.AddPCBObject while the board attached the same object to whichever }
{ footprint is current: one primitive split across two footprints. So the     }
{ switch is checked by reading CurrentComponent back, and a switch that did   }
{ not take refuses the write.                                                  }
{ Lib_AddFootprintText used SetState_CurrentComponent inside a silent Try and  }
{ never checked it; this uses the property write Lib_SetDesignator and         }
{ Lib_CreateFootprint use, with the same read-back Lib_SetDesignator has.      }
{                                                                              }
{ MAKING A FOOTPRINT CURRENT CAN MOVE ITS ORIGIN (Lib_SetDesignator: "the      }
{ create path makes the component current, which relocates it"). Callers read }
{ FootprintOriginX / Y AFTER this returns, never before.                       }
{                                                                              }
{ Returns Nil with ErrCode and Why filled when nothing resolves. Interfaces    }
{ come back through the Result and strings through Var, the shape              }
{ GetPCBBoardForMutation and FocusSchLib already use.                          }
{..............................................................................}
Function ResolveLibFootprint(Params : String; Var ErrCode : String; Var Why : String) : IPCB_LibComponent;
Var
    LibPath, FocusedPath, FpWanted, FpName : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
Begin
    Result := Nil;
    ErrCode := '';
    Why := '';
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'component_name');
    If FpWanted = '' Then FpWanted := ExtractJsonValue(Params, 'footprint_name');

    If LibPath <> '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace = Nil Then
        Begin
            ErrCode := 'NO_WORKSPACE';
            Why := 'No workspace';
            Exit;
        End;
        FocusedPath := '';
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
        If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
        Begin
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', LibPath);
            RunProcess('WorkspaceManager:OpenObject');
            FocusedPath := '';
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
            If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
            Begin
                ErrCode := 'WRONG_DOCUMENT_FOCUSED';
                Why := 'Asked for ' + LibPath + ' but the focused document is "'
                    + FocusedPath + '". Nothing was added.';
                Exit;
            End;
        End;
    End;

    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        ErrCode := 'NO_PCBLIB';
        Why := 'No PCB library is active';
        Exit;
    End;

    If FpWanted = '' Then
    Begin
        Target := PcbLib.CurrentComponent;
        If Target = Nil Then
        Begin
            ErrCode := 'NO_FOOTPRINT';
            Why := 'No footprint is selected (pass component_name to choose one)';
            Exit;
        End;
        Result := Target;
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        ErrCode := 'FOOTPRINT_NOT_FOUND';
        Why := 'Footprint not found in library: ' + FpWanted;
        Exit;
    End;

    If PcbLib.CurrentComponent <> Target Then
    Begin
        Try PcbLib.CurrentComponent := Target; Except End;
        If PcbLib.CurrentComponent <> Target Then
        Begin
            ErrCode := 'SELECT_FAILED';
            Why := 'Could not make footprint "' + FpWanted + '" current, so '
                + 'nothing was added: the library board attaches a new '
                + 'primitive to its CURRENT footprint, and adding anyway '
                + 'would split it across two footprints.';
            Exit;
        End;
    End;
    Result := Target;
End;

{ Focus a .PcbLib by path for a WRITE and prove it is the one in front.        }
{                                                                              }
{ Defined up here, beside ResolveLibFootprint, so every PcbLib write can use  }
{ it: the pad editors, and Lib_RenameFootprints, which comes before them in  }
{ this file. DelphiScript has no forward declarations.                        }
{                                                                              }
{ The verify-after-open half is Lib_DeleteFootprintPrimitives's: an open that  }
{ silently fails leaves the PREVIOUS library focused, and a write would land   }
{ in it while reporting the path that was asked for. Returns Nil with ErrCode  }
{ and ErrMsg set; rewrites LibPath to the path actually used.                  }
Function FocusPcbLibForEdit(Var LibPath : String; Var ErrCode : String; Var ErrMsg : String) : IPCB_Library;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    FocusedPath : String;
Begin
    Result := Nil;
    ErrCode := '';
    ErrMsg := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        ErrCode := 'NO_WORKSPACE';
        ErrMsg := 'No workspace';
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        ErrCode := 'NO_LIBRARY';
        ErrMsg := 'No library is active and library_path was not supplied';
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ErrCode := 'WRONG_DOCUMENT_FOCUSED';
        ErrMsg := 'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was changed.';
        Exit;
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    Result := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then Result := PCBServer.GetCurrentPCBLibrary;
    If Result = Nil Then
    Begin
        ErrCode := 'NO_PCBLIB';
        ErrMsg := 'Focused ' + LibPath + ' but it is not a PCB library. These '
            + 'tools edit a footprint inside a .PcbLib; a placed pad on a '
            + 'board is a pcb_ tool''s job.';
    End;
End;

{..............................................................................}
{ RegisterLibPrimitive - the registration every footprint primitive needs.     }
{                                                                              }
{ The same four calls Lib_AddFootprintPad carries inline, IN THE SAME ORDER,   }
{ and for the same measured reason: Footprint.AddPCBObject alone leaves an     }
{ object that every read reports and the save discards. It has to be added to }
{ the library's backing Board as well, and the registration broadcast to both. }
{ Factored out so a new primitive kind cannot be written with three of the    }
{ four.                                                                        }
{                                                                              }
{ 'both' is BOTH ADDS, THEN BOTH BROADCASTS: footprint add, board add,        }
{ footprint broadcast, board broadcast. That is the order pads, tracks and    }
{ arcs were measured persisting with, and the one Lib_AddFootprintPad(s) and   }
{ Lib_Link3DModel still carry inline. An earlier version of this helper       }
{ interleaved them (add, broadcast, add, broadcast): every call proven, the   }
{ ORDER never run, under every track, arc, text, fill and region add. A review }
{ caught it; keep the order.                                                   }
{                                                                              }
{ Mode selects which half runs, and exists ONLY to settle one live question:   }
{ text was measured persisting twice after one call (2026-10-03), while pads  }
{ and tracks registered the same way persist once. 'both' is the default and  }
{ the proven pattern; 'footprint' and 'board' let one live session test which }
{ registration a text needs without redeploying the script. Every handler but  }
{ Lib_AddFootprintText passes 'both'.                                          }
{                                                                              }
{ Prim is the base type. Passing an IPCB_Fill or IPCB_Text in is an upcast,    }
{ which BindPrimitiveToNet in PCB.pas already does with every primitive kind.  }
{..............................................................................}
Procedure RegisterLibPrimitive(PcbLib : IPCB_Library; Footprint : IPCB_LibComponent; Prim : IPCB_Primitive; Mode : String);
Begin
    If Mode = 'both' Then
    Begin
        Footprint.AddPCBObject(Prim);
        PcbLib.Board.AddPCBObject(Prim);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
    End
    Else If Mode = 'footprint' Then
    Begin
        { Diagnostic half only (registration=footprint). }
        Footprint.AddPCBObject(Prim);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
    End
    Else If Mode = 'board' Then
    Begin
        { Diagnostic half only (registration=board). }
        PcbLib.Board.AddPCBObject(Prim);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
    End;
End;

{..............................................................................}
{ LibTextAddresses - every text object in a footprint, as '|addr|addr|'.       }
{                                                                              }
{ A membership string, the shape Lib_GetLibraryGeometry's SeenKeys uses, so no }
{ TStringList has to be created and freed. Yields counts what the group        }
{ iterator handed back INCLUDING repeats: a primitive added this session is    }
{ yielded twice until the library is reloaded (measured for pads, 15b5a27),    }
{ and the count is how a reply can say so instead of hiding it.                }
{..............................................................................}
Function LibTextAddresses(Footprint : IPCB_LibComponent; Var Yields : Integer) : String;
Var
    GrpIter : IPCB_GroupIterator;
    Txt : IPCB_Text;
    Addr : String;
Begin
    Result := '|';
    Yields := 0;
    If Footprint = Nil Then Exit;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            Inc(Yields);
            Addr := '';
            Try Addr := IntToStr(Txt.I_ObjectAddress); Except End;
            If (Addr <> '') And (Pos('|' + Addr + '|', Result) = 0) Then
                Result := Result + Addr + '|';
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
End;

{..............................................................................}
{ REMOVING PRIMITIVES FROM A PCBLIB FOOTPRINT: ONE RULE FOR EVERY DELETE.      }
{                                                                              }
{ Four paths remove library primitives: lib_delete_footprint_primitives,       }
{ lib_delete_footprint_pads, lib_link_3d_model replace=true (Body3D_RemoveAll) }
{ and the duplicate cleanup in lib_add_footprint_text. They share one rule.   }
{                                                                              }
{ 1. THE FOOTPRINT HALF IS THE DEFAULT, AND THE ONLY HALF THAT HAS RUN LIVE.   }
{    Footprint.RemovePCBObject on a primitive a still-open group iterator has  }
{    just yielded, one match per pass (the measured shape, recorded in        }
{    Lib_DeleteFootprintPrimitives). Each caller keeps that call INLINE, in   }
{    the shape that ran, so the default path makes the Altium calls the base  }
{    build made and no others.                                                 }
{                                                                              }
{ 2. THE LIBRARY-BOARD HALF IS OPT-IN (remove_from_board=true) and runs only   }
{    through LibBoardRemoveEntries below. UNMEASURED: no live session has run  }
{    it. It exists because of the owner's report (2026-10-03) that a           }
{    generated footprint "ignores primitive deletes until the library is       }
{    reloaded", and the recipe "delete, reload, delete again". That fits an    }
{    object whose library-board registration outlives a footprint-only         }
{    removal and is written back by the save. It fits other causes too, and a  }
{    removal sequence nobody has watched does not become the default of a      }
{    delete tool that worked. RELEASE_VERIFICATION step 24 settles it with one }
{    delete, save and reload; the board_removal reply object is what it reads. }
{                                                                              }
{ 3. THE FOOTPRINT IS MADE CURRENT ONLY FOR THE BOARD HALF, verified, and      }
{    before anything is removed (LibMakeCurrentVerified). The library board   }
{    holds the CURRENT footprint's primitives, so walking it for a footprint  }
{    that is not current would find nothing and report that as a clean       }
{    board. A footprint-only removal leaves CurrentComponent alone, as the    }
{    base build did. lib_link_3d_model and lib_add_footprint_text make the    }
{    footprint current anyway, because they ADD to it.                        }
{                                                                              }
{ 4. THE BOARD HALF NEVER TOUCHES AN OBJECT THE FOOTPRINT STILL LISTS, and     }
{    hands RemovePCBObject only what a fresh board iterator has just yielded  }
{    by address. The footprint is finished with an object before the board    }
{    sees it, so the second footprint entry of a double-registered primitive  }
{    is never handed back after the board released it. The first version of  }
{    the board removal interleaved the two (footprint, board, then the        }
{    footprint's second entry), and a review flagged exactly that.            }
{..............................................................................}

{ Make Footprint the library's current one and READ THE SWITCH BACK: the same  }
{ property write and comparison ResolveLibFootprint and Lib_SetDesignator      }
{ make. False means the switch did not take, and the caller refuses.           }
Function LibMakeCurrentVerified(PcbLib : IPCB_Library; Footprint : IPCB_LibComponent) : Boolean;
Begin
    Result := False;
    If (PcbLib = Nil) Or (Footprint = Nil) Then Exit;
    If PcbLib.CurrentComponent <> Footprint Then
    Begin
        Try PcbLib.CurrentComponent := Footprint; Except End;
    End;
    Try Result := (PcbLib.CurrentComponent = Footprint); Except Result := False; End;
End;

{ LibParkCurrentElsewhere - make some OTHER footprint current, verified, so a  }
{ footprint-only removal runs on a footprint that is NOT the current one.      }
{                                                                              }
{ WHY (2026-10-04, the block above Lib_DeleteFootprintPads). The removal that  }
{ came back after the save ran on the CURRENT footprint (the add had made it   }
{ current); the removal that persisted ran after a reload, which opens         }
{ whichever footprint Altium chooses, so probably on a footprint that was not  }
{ current. The library board holds the current footprint's primitives (rule    }
{ 3 below), and a save that rebuilds the current footprint from the board's    }
{ copy would write a footprint-only removal back. The tool's reload_first      }
{ makes every doomed primitive disk-loaded; this reproduces the other half     }
{ of the condition that persisted. Opt-in per request (park_current, sent by   }
{ the Python side only after its reload_first ran), never with                 }
{ remove_from_board, whose board half needs the footprint current.             }
{                                                                              }
{ Every member is shipped: the LibraryIterator walk is ResolveLibFootprint's,  }
{ the interface comparison is LibMakeCurrentVerified's, and the switch is      }
{ LibMakeCurrentVerified itself. False when the library holds no other         }
{ footprint or the switch did not take; the caller removes anyway and says     }
{ so (target_current_during_removal), and the tool's on-disk check is what     }
{ settles whether the removal stuck.                                           }
Function LibParkCurrentElsewhere(PcbLib : IPCB_Library; Target : IPCB_LibComponent) : Boolean;
Var
    Iter : IPCB_LibraryIterator;
    Footprint, Other : IPCB_LibComponent;
Begin
    Result := False;
    If (PcbLib = Nil) Or (Target = Nil) Then Exit;
    Other := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            If Footprint <> Target Then
            Begin
                Other := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Other = Nil Then Exit;
    Result := LibMakeCurrentVerified(PcbLib, Other);
End;

{ Record one removed object for the board half, as 'oid:addr|', once per      }
{ address. Oid is the primitive's ObjectId read into an Integer, the way      }
{ PCB_Unroute reads it, so the board walk can filter on its kind. The        }
{ ':' in front of the address keeps 1234 from matching inside 11234.          }
Function LibAddRemovalEntry(Entries : String; Oid : Integer; Addr : String) : String;
Begin
    Result := Entries;
    If Addr = '' Then Exit;
    If Pos(':' + Addr + '|', Entries) = 0 Then
        Result := Entries + IntToStr(Oid) + ':' + Addr + '|';
End;

{ Does the library BOARD list the object at this address? A READ-ONLY walk in }
{ the board iterator shape ProcessPCBBoardObjects uses on a PcbDoc: the three  }
{ filters, then compare addresses. Board is PcbLib.Board, an IPCB_Board, so    }
{ every member here is one the PcbDoc paths already call.                     }
Function LibBoardListsAddress(Board : IPCB_Board; Oid : Integer; Addr : String) : Boolean;
Var
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    ThisAddr : String;
Begin
    Result := False;
    If (Board = Nil) Or (Addr = '') Then Exit;
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(Oid));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            ThisAddr := '';
            Try ThisAddr := IntToStr(Obj.I_ObjectAddress); Except End;
            If ThisAddr = Addr Then
            Begin
                Result := True;
                Break;
            End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;
End;

{ LibBoardRemoveEntries - THE OPT-IN BOARD HALF. UNMEASURED (rule 2 above).   }
{                                                                              }
{ For each recorded object the footprint no longer lists:                     }
{   * a fresh board iterator walks to it BY ADDRESS and stops there;           }
{   * it is removed WHILE THAT ITERATOR IS OPEN, and the iterator destroyed    }
{     after. The PcbDoc paths destroy first and remove after                   }
{     (PCB_DeleteInvalidObjects), which is proven on a board, but these are   }
{     LIBRARY primitives, and for those Lib_DeleteFootprintPrimitives          }
{     measured that an iterator's destroy can leave what it yielded dangling.  }
{     Removing before the destroy is safe under either behaviour; destroying  }
{     first is safe under only one;                                            }
{   * the removal itself is PCB_DeleteObject's: broadcast, then RemovePCBObject;}
{   * at most two removals per object, in case the board lists it twice the   }
{     way the footprint does, then a read-only walk says whether it is gone.  }
{                                                                              }
{ Skip is a '|addr|addr|' set of objects the footprint STILL lists. Those are }
{ counted and never touched (rule 4). MadeCurrent false runs nothing: the     }
{ board would not hold the footprint's primitives (rule 3). The caller owns   }
{ PreProcess / PostProcess.                                                    }
{                                                                              }
{ Returns the board_removal JSON object for the reply. Removed is handed back }
{ too, so the caller knows whether the library changed.                       }
Function LibBoardRemoveEntries(Board : IPCB_Board; Entries : String; Skip : String;
                               MadeCurrent : Boolean; Var Removed : Integer) : String;
Var
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Rest, Token, Addr, ThisAddr, NotRun : String;
    BarPos, ColonPos, Oid, Pass, Objects, Listed, Still, Skipped, Calls : Integer;
    Hit, Saw, Took : Boolean;
Begin
    Removed := 0;
    Objects := 0;
    Listed := 0;
    Still := 0;
    Skipped := 0;
    Calls := 0;
    NotRun := '';
    If Board = Nil Then NotRun := 'no library board'
    Else If Not MadeCurrent Then NotRun := 'footprint not current';
    Rest := Entries;
    While Rest <> '' Do
    Begin
        BarPos := Pos('|', Rest);
        If BarPos = 0 Then Break;
        Token := Copy(Rest, 1, BarPos - 1);
        Rest := Copy(Rest, BarPos + 1, Length(Rest));
        ColonPos := Pos(':', Token);
        If ColonPos = 0 Then Continue;
        Oid := StrToIntDef(Copy(Token, 1, ColonPos - 1), -1);
        Addr := Copy(Token, ColonPos + 1, Length(Token));
        If (Oid < 0) Or (Addr = '') Then Continue;
        Objects := Objects + 1;
        If NotRun <> '' Then Continue;
        If Pos('|' + Addr + '|', Skip) > 0 Then
        Begin
            Skipped := Skipped + 1;
            Continue;
        End;
        Saw := False;
        Took := False;
        For Pass := 1 To 2 Do
        Begin
            Hit := False;
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(Oid));
                Iter.AddFilter_LayerSet(AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                Obj := Iter.FirstPCBObject;
                While Obj <> Nil Do
                Begin
                    ThisAddr := '';
                    Try ThisAddr := IntToStr(Obj.I_ObjectAddress); Except End;
                    If ThisAddr = Addr Then
                    Begin
                        Hit := True;
                        Break;
                    End;
                    Obj := Iter.NextPCBObject;
                End;
                If Hit And (Obj <> Nil) Then
                Begin
                    Saw := True;
                    Try
                        PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                            c_Broadcast, PCBM_BoardRegisteration, Obj.I_ObjectAddress);
                        Board.RemovePCBObject(Obj);
                        Took := True;
                        Calls := Calls + 1;
                    Except
                        Hit := False;
                    End;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;
            If Not Hit Then Break;
        End;
        If Saw Then Listed := Listed + 1;
        If Took Then Removed := Removed + 1;
        If LibBoardListsAddress(Board, Oid, Addr) Then Still := Still + 1;
    End;
    Result := JsonObj(JsonBool('requested', True) + ','
        + JsonBool('made_current', MadeCurrent) + ','
        + JsonStr('not_run', NotRun) + ','
        + JsonInt('objects', Objects) + ','
        + JsonInt('listed_before', Listed) + ','
        + JsonInt('removed', Removed) + ','
        + JsonInt('remove_calls', Calls) + ','
        + JsonInt('still_listed', Still) + ','
        + JsonInt('skipped_still_in_footprint', Skipped));
End;

{ LibBoardStillListsCount - how many recorded objects ('oid:addr|' entries,   }
{ LibAddRemovalEntry's shape) the LIBRARY BOARD still lists. READ-ONLY: one   }
{ LibBoardListsAddress walk per entry, nothing removed, so it can follow     }
{ a delete, opt-in or not. The board iterator it uses ran live in              }
{ LibBoardRemoveEntries on 2026-10-04 (board_removal listed_before 1), on a    }
{ pad of the CURRENT footprint; that is the only case it is run for.           }
{                                                                              }
{ WHY A DELETE REPORTS IT. Measured 2026-10-04 on carrier.PcbLib: a pad added }
{ in the same session and removed from its footprint (still_listed 0) was    }
{ written back by the next save, and listed in the footprint again after it. }
{ A removed object the board still holds is the candidate for that, and this }
{ count is what lets one live call show whether the board still holds it:   }
{ 0 here with the pad back in the file would point somewhere else. Only the   }
{ CURRENT footprint's primitives are on the board (rule 3), so for a         }
{ footprint that is not current this is 0 by construction. The callers         }
{ therefore run it ONLY while the footprint is current, and report null        }
{ otherwise: a 0 that cannot be anything else is not evidence, and the walk    }
{ would otherwise meet objects the footprint has just released (a review,      }
{ 2026-10-04, flagged that wider exposure).                                    }
Function LibBoardStillListsCount(Board : IPCB_Board; Entries : String) : Integer;
Var
    Rest, Token, Addr : String;
    BarPos, ColonPos, Oid : Integer;
Begin
    Result := 0;
    If Board = Nil Then Exit;
    Rest := Entries;
    While Rest <> '' Do
    Begin
        BarPos := Pos('|', Rest);
        If BarPos = 0 Then Break;
        Token := Copy(Rest, 1, BarPos - 1);
        Rest := Copy(Rest, BarPos + 1, Length(Rest));
        ColonPos := Pos(':', Token);
        If ColonPos = 0 Then Continue;
        Oid := StrToIntDef(Copy(Token, 1, ColonPos - 1), -1);
        Addr := Copy(Token, ColonPos + 1, Length(Token));
        If (Oid < 0) Or (Addr = '') Then Continue;
        If LibBoardListsAddress(Board, Oid, Addr) Then Result := Result + 1;
    End;
End;

{..............................................................................}
{ RemoveLibTextByAddress - take ONE text object out of a footprint: the        }
{ duplicate cleanup of Lib_AddFootprintText.                                   }
{                                                                              }
{ FOOTPRINT ONLY, by rule 1 above. This used to remove from the library board }
{ as well, automatically, whenever a duplicate was found: the unmeasured      }
{ sequence on a path nobody asked to run. The board half now runs only when   }
{ the caller passed remove_from_board, through LibBoardRemoveEntries, and     }
{ Entries records what this removed for it.                                   }
{                                                                              }
{ Removed WHILE THE ITERATOR IS OPEN, after the walk has stopped at the match: }
{ Lib_DeleteFootprintPrimitives measured that GroupIterator_Destroy releases   }
{ what the iterator handed out, so removing after the destroy dereferenced a   }
{ dangling object inside ScriptingSystem.DLL. The walk never resumes after the }
{ removal, so the skip-entries hazard of removing mid-walk cannot arise.       }
{ Up to two passes, each with a fresh iterator: a text added this session is  }
{ listed twice until a reload, one listing per pass.                          }
{                                                                              }
{ BRACKETED with PCBM_BeginModify / PCBM_EndModify on the footprint, as every  }
{ other footprint removal here is. The first version had no bracket, and      }
{ Lib_SetDesignator measured that an unbracketed footprint edit can be        }
{ dropped when the component is re-serialised on save.                        }
{                                                                              }
{ The caller owns PreProcess / PostProcess.                                     }
{..............................................................................}
Function RemoveLibTextByAddress(Footprint : IPCB_LibComponent; Addr : String; Var Entries : String) : Boolean;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    ThisAddr : String;
    Pass, Oid : Integer;
    Hit : Boolean;
Begin
    Result := False;
    If (Footprint = Nil) Or (Addr = '') Then Exit;
    PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
        c_Broadcast, PCBM_BeginModify, c_NoEventData);
    Try
        For Pass := 1 To 2 Do
        Begin
            Hit := False;
            GrpIter := Footprint.GroupIterator_Create;
            Try
                GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                Prim := GrpIter.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    ThisAddr := '';
                    Try ThisAddr := IntToStr(Prim.I_ObjectAddress); Except End;
                    If ThisAddr = Addr Then
                    Begin
                        Hit := True;
                        Break;
                    End;
                    Prim := GrpIter.NextPCBObject;
                End;
                If Hit And (Prim <> Nil) Then
                Begin
                    Oid := -1;
                    Try Oid := Prim.ObjectId; Except Oid := -1; End;
                    Try
                        Footprint.RemovePCBObject(Prim);
                        Result := True;
                        Entries := LibAddRemovalEntry(Entries, Oid, Addr);
                    Except
                        Hit := False;
                    End;
                End;
            Finally
                Footprint.GroupIterator_Destroy(GrpIter);
            End;
            If Not Hit Then Break;
        End;
    Finally
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_EndModify, c_NoEventData);
    End;
End;

{..............................................................................}
{ PAD SHAPE ON EVERY COPPER LAYER                                              }
{                                                                              }
{ THE THROUGH-HOLE ROUNDRECT BUG. The add handlers wrote a rounded rectangle   }
{ with SetState_StackShapeOnLayer(Pad.Layer, ...). For an SMD pad Pad.Layer is }
{ a copper layer and that works. For a drilled pad it is eMultiLayer, which is }
{ not an entry of the pad stack at all, and Altium IGNORES an out-of-stack     }
{ layer without raising. MEASURED 2026-10-03 on the multiviewer carrier        }
{ library: header pin 1 asked for as roundrect read back as a plain round pad  }
{ from both the saved file and lib_get_pad_geometry. The fix writes the stack  }
{ entries that exist: top (the simple-mode entry TopShape reads back), bottom, }
{ the first inner layer, and every inner layer the library's own stack holds.  }
{                                                                              }
{ Shapes are passed as CONSTANTS, never held in a variable: every proven call  }
{ in this codebase hands the enum literal to the setter, and an Integer given  }
{ to a TShape parameter has not been exercised here.                           }
{..............................................................................}

{ The read-back word for a TShape ordinal. Library.pas compiles before PCB.pas }
{ so PCB.pas's LmShapeName is not reachable from here; same mapping, the       }
{ vocabulary the modify tool accepts.                                          }
Function PadShapeWord(S : Integer) : String;
Begin
    Result := 'round';
    If S = eRectangular Then Result := 'rectangular';
    If S = eOctagonal Then Result := 'octagonal';
    If S = eRoundedRectangular Then Result := 'roundrect';
End;

{ A caller's shape word folded to the four the pad stack has, or '' when it is }
{ none of them. 'roundrectangle' is accepted because it is what                }
{ lib_get_pad_geometry reports, so a read can be fed straight back.            }
Function CanonicalPadShape(Shape : String) : String;
Var
    W : String;
Begin
    Result := '';
    W := LowerCase(Trim(Shape));
    If (W = 'round') Or (W = 'rounded') Or (W = 'circle') Or (W = 'oval') Then
        Result := 'round'
    Else If (W = 'rectangular') Or (W = 'rect') Or (W = 'rectangle') Then
        Result := 'rectangular'
    Else If (W = 'octagonal') Or (W = 'oct') Or (W = 'octagon') Then
        Result := 'octagonal'
    Else If (W = 'roundrect') Or (W = 'rounded_rectangular')
         Or (W = 'roundrectangle') Or (W = 'rounded_rectangle') Then
        Result := 'roundrect';
End;

{ One copper entry of a pad's stack, by canonical shape word. The caller       }
{ passes copper layers only; eMultiLayer is the silent no-op this exists for. }
Procedure SetStackShapeWord(Pad : IPCB_Pad; Lyr : TLayer; Canon : String; Pct : Integer);
Begin
    If Canon = 'roundrect' Then
    Begin
        Try Pad.SetState_StackShapeOnLayer(Lyr, eRoundedRectangular); Except End;
        Try Pad.SetState_StackCRPctOnLayer(Lyr, Pct); Except End;
    End
    Else If Canon = 'rectangular' Then
    Begin
        Try Pad.SetState_StackShapeOnLayer(Lyr, eRectangular); Except End;
    End
    Else If Canon = 'octagonal' Then
    Begin
        Try Pad.SetState_StackShapeOnLayer(Lyr, eOctagonal); Except End;
    End
    Else
    Begin
        Try Pad.SetState_StackShapeOnLayer(Lyr, eRounded); Except End;
    End;
End;

{ Set a pad's copper shape on every layer it has copper on, and READ IT BACK.  }
{                                                                              }
{ Returns, and the caller reports it rather than assuming success:            }
{   'confirmed'      TopShape (+ corner %) reads back as asked, and for a      }
{                    drilled pad so does the bottom stack entry               }
{   'top_only'       TopShape took; a drilled pad's bottom entry did not      }
{   'unconfirmed'    TopShape did not take by either route                    }
{ with '_via_top_shape' appended when only the second route (step 4) took.    }
{                                                                              }
{ Step 1 is exactly what the add handlers always did for the simple-mode       }
{ entry. Step 2 is the fix. Step 4 is a second route for roundrect only:       }
{ TopShape := eRoundedRectangular. Both identifiers are proven, the pairing    }
{ is not, which is why it runs only when the top entry did not read back.      }
Function SetPadCopperShape(Board : IPCB_Board; Pad : IPCB_Pad; Canon : String; Pct : Integer) : String;
Var
    LayerStack : IPCB_LayerStack_V7;
    Obj : IPCB_LayerObject_V7;
    Lyr : TLayer;
    IsTH, TopOk, BotOk : Boolean;
    Got, Via : String;
    P : Integer;
Begin
    Result := 'unconfirmed';
    P := Pct;
    If P < 0 Then P := 0;
    If P > 100 Then P := 100;
    IsTH := False;
    Try IsTH := (Pad.Layer = eMultiLayer); Except End;

    { 1. The simple-mode entry. }
    If Canon = 'roundrect' Then SetStackShapeWord(Pad, eTopLayer, Canon, P)
    Else If Canon = 'rectangular' Then Pad.TopShape := eRectangular
    Else If Canon = 'octagonal' Then Pad.TopShape := eOctagonal
    Else Pad.TopShape := eRounded;

    { 2. Every other copper entry. A drilled pad has copper top, bottom and    }
    {    on each inner layer; eMidLayer1 is written explicitly because a       }
    {    two-layer library stack lists no inner layer, and it is the entry a   }
    {    top/middle/bottom stack reads for ALL inner layers. A single-layer    }
    {    pad on the bottom gets its own entry as well as the top one.          }
    If IsTH Then
    Begin
        SetStackShapeWord(Pad, eBottomLayer, Canon, P);
        SetStackShapeWord(Pad, eMidLayer1, Canon, P);
        LayerStack := Nil;
        If Board <> Nil Then
        Begin
            Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
        End;
        If LayerStack <> Nil Then
        Begin
            Obj := Nil;
            Try Obj := LayerStack.FirstLayer; Except Obj := Nil; End;
            While Obj <> Nil Do
            Begin
                Lyr := eNoLayer;
                Try Lyr := Obj.LayerID; Except Lyr := eNoLayer; End;
                If (Lyr > eMidLayer1) And (Lyr < eBottomLayer) Then
                    SetStackShapeWord(Pad, Lyr, Canon, P);
                Try Obj := LayerStack.NextLayer(Obj); Except Obj := Nil; End;
            End;
        End;
    End
    Else If Pad.Layer <> eTopLayer Then
        SetStackShapeWord(Pad, Pad.Layer, Canon, P);

    { 3. Read back what a caller and the saved file will see. }
    Got := '';
    Try Got := PadShapeWord(Pad.TopShape); Except Got := ''; End;
    TopOk := (Got = Canon);
    If TopOk And (Canon = 'roundrect') Then
    Begin
        Try TopOk := (Pad.StackCRPctOnLayer[eTopLayer] = P); Except TopOk := False; End;
    End;

    { 4. Roundrect only, and only when the top entry did not take. UNVERIFIED }
    {    pairing of two proven identifiers; the read-back decides.            }
    Via := '';
    If (Not TopOk) And (Canon = 'roundrect') Then
    Begin
        Try Pad.TopShape := eRoundedRectangular; Except End;
        Try Pad.SetState_StackCRPctOnLayer(eTopLayer, P); Except End;
        Got := '';
        Try Got := PadShapeWord(Pad.TopShape); Except Got := ''; End;
        TopOk := (Got = Canon);
        If TopOk Then Via := '_via_top_shape';
    End;
    If Not TopOk Then Exit;

    BotOk := True;
    If IsTH Then
    Begin
        Got := '';
        Try Got := PadShapeWord(Pad.StackShapeOnLayer[eBottomLayer]); Except Got := ''; End;
        BotOk := (Got = Canon);
    End;
    If BotOk Then Result := 'confirmed' + Via
    Else Result := 'top_only' + Via;
End;

Function Lib_AddFootprintPad(Params : String; RequestId : String) : String;
Var
    Designator, Shape, LayerStr : String;
    X, Y, XSize, YSize, HoleSize : Double;
    CornerRadius : Integer;
    Rotation : Double;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Pad : IPCB_Pad;
    PadLayer : TLayer;
    ShapeHow, ErrCode, Why : String;
Begin
    ShapeHow := '';
    Designator := ExtractJsonValue(Params, 'designator');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    XSize := StrToFloatDef(ExtractJsonValue(Params, 'x_size'), 60);
    YSize := StrToFloatDef(ExtractJsonValue(Params, 'y_size'), 60);
    HoleSize := StrToFloatDef(ExtractJsonValue(Params, 'hole_size'), 0);
    Shape := ExtractJsonValue(Params, 'shape');
    LayerStr := ExtractJsonValue(Params, 'layer');
    Rotation := StrToFloatDef(ExtractJsonValue(Params, 'rotation'), 0);
    CornerRadius := StrToIntDef(ExtractJsonValue(Params, 'corner_radius'), 25);

    { The footprint through the shared resolver, like the track, arc, text,   }
    { fill and region handlers: component_name / library_path aim the call   }
    { at a named footprint (made current, switch read back); without them it }
    { is the library's current footprint, exactly as before.                 }
    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    { Layer FIRST (the rounded-rect setters below are layer-aware): a drilled  }
    { pad is through-hole (MultiLayer); a hole-less pad is SMD on a single      }
    { layer (the named layer, default Top). Resolved before PreProcess so an    }
    { unresolvable name ends the call rather than reaching the old eTopLayer    }
    { fallback and putting a silkscreen pad on top copper.                      }
    If HoleSize > 0 Then PadLayer := eMultiLayer
    Else If LayerStr = '' Then PadLayer := eTopLayer
    Else PadLayer := ResolveLayerId(PcbLib.Board, LayerStr);
    If PadLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;

    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    If Pad <> Nil Then
    Begin
        Pad.Name := Designator;
        Pad.X := FootprintOriginX(Footprint) + Round(MilsToCoord(1) * X);
        Pad.Y := FootprintOriginY(Footprint) + Round(MilsToCoord(1) * Y);
        Pad.TopXSize := Round(MilsToCoord(1) * XSize);
        Pad.TopYSize := Round(MilsToCoord(1) * YSize);
        Pad.HoleSize := Round(MilsToCoord(1) * HoleSize);
        Pad.Rotation := Rotation;

        Pad.Layer := PadLayer;

        { Shape. roundrect = the modern IPC default, written on EVERY copper }
        { layer and read back by SetPadCopperShape (Altium stores the radius }
        { as a %). Writing it on Pad.Layer alone did nothing for a drilled   }
        { pad: eMultiLayer is not a pad-stack entry.                          }
        If Shape = 'rectangular' Then Pad.TopShape := eRectangular
        Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
        Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
            ShapeHow := SetPadCopperShape(PcbLib.Board, Pad, 'roundrect', CornerRadius)
        Else Pad.TopShape := eRounded;

        Footprint.AddPCBObject(Pad);
        { The primitive must be registered with the library's backing Board as
          well as with the footprint, and the registration broadcast to both.
          Footprint.AddPCBObject alone is not enough: the object exists in the
          working copy, every read reports it, and the save discards it. That
          cost an afternoon on an LCSC import, where pads and tracks reported
          success and the footprint came back empty, and it was read as Altium
          refusing to author land patterns at all. Lib_AddFootprintText had the
          full sequence and was the only authoring call that survived a save. }
        PcbLib.Board.AddPCBObject(Pad);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);

        If ShapeHow <> '' Then
            Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"'
                + EscapeJsonString(Designator) + '","shape_status":"' + ShapeHow + '"}')
        Else
            Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pad');

    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);
End;

{ Batch pad authoring: same shape as Lib_AddPins but for PCB pads. Receives a }
{ `pads` array encoded with the ~~ / ; / = separators NextBatchOp expects,    }
{ creates every pad inside ONE PreProcess / PostProcess pair and ONE save, so }
{ a 64-pad QFP costs one IPC round-trip instead of 64. Per-pad fields:        }
{ designator, x, y, x_size, y_size, hole_size, shape, rotation (mirrors the   }
{ singular Lib_AddFootprintPad field set / defaults exactly).                 }
Function Lib_AddFootprintPads(Params : String; RequestId : String) : String;
Var
    PadsStr, Op, Remaining, Shape, LayerStr, BadLayers : String;
    OpCount, Added, Failed : Integer;
    X, Y, XSize, YSize, HoleSize : Double;
    CornerRadius : Integer;
    Rotation : Double;
    PadLayer : TLayer;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Pad : IPCB_Pad;
    PlatedStr, HoleTypeStr, MaskStr, PasteStr : String;
    SlotLen, HoleRot : Double;
    Cache : TPadCache;
    ShapeHow, ErrCode, Why : String;
    RRAsked, RRUnconfirmed : Integer;
Begin
    RRAsked := 0;
    RRUnconfirmed := 0;
    PadsStr := ExtractJsonValue(Params, 'pads');
    If PadsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pads is required');
        Exit;
    End;

    { The footprint through the shared resolver, like the track, arc, text,   }
    { fill and region handlers: component_name / library_path aim the call   }
    { at a named footprint (made current, switch read back); without them it }
    { is the library's current footprint, exactly as before.                 }
    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := PadsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
            XSize := StrToFloatDef(GetBatchField(Op, 'x_size'), 60);
            YSize := StrToFloatDef(GetBatchField(Op, 'y_size'), 60);
            HoleSize := StrToFloatDef(GetBatchField(Op, 'hole_size'), 0);
            Rotation := StrToFloatDef(GetBatchField(Op, 'rotation'), 0);
            Shape := GetBatchField(Op, 'shape');
            LayerStr := GetBatchField(Op, 'layer');
            CornerRadius := StrToIntDef(GetBatchField(Op, 'corner_radius'), 25);
            PlatedStr := LowerCase(GetBatchField(Op, 'plated'));
            HoleTypeStr := LowerCase(GetBatchField(Op, 'hole_type'));
            SlotLen := StrToFloatDef(GetBatchField(Op, 'slot_length'), 0);
            HoleRot := StrToFloatDef(GetBatchField(Op, 'hole_rotation'), 0);
            MaskStr := GetBatchField(Op, 'mask_expansion');
            PasteStr := GetBatchField(Op, 'paste_expansion');

            { Resolve the layer BEFORE creating anything. GetLayerFromString    }
            { answered eTopLayer for every name it did not know, so one         }
            { "Top Overlay" in a batch silently put that pad on top copper.     }
            If HoleSize > 0 Then PadLayer := eMultiLayer
            Else If LayerStr = '' Then PadLayer := eTopLayer
            Else PadLayer := ResolveLayerId(PcbLib.Board, LayerStr);
            If PadLayer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
            If Pad = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Pad.Name := GetBatchField(Op, 'designator');
            Pad.X := FootprintOriginX(Footprint) + Round(MilsToCoord(1) * X);
            Pad.Y := FootprintOriginY(Footprint) + Round(MilsToCoord(1) * Y);
            Pad.TopXSize := Round(MilsToCoord(1) * XSize);
            Pad.TopYSize := Round(MilsToCoord(1) * YSize);
            Pad.HoleSize := Round(MilsToCoord(1) * HoleSize);
            Pad.Rotation := Rotation;

            { Layer FIRST (roundrect setters are layer-aware): drilled ->       }
            { through-hole (MultiLayer); hole-less -> SMD on a single layer.    }
            Pad.Layer := PadLayer;

            If Shape = 'rectangular' Then Pad.TopShape := eRectangular
            Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
            Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
            Begin
                { Every copper layer, read back: see SetPadCopperShape. }
                ShapeHow := SetPadCopperShape(PcbLib.Board, Pad, 'roundrect', CornerRadius);
                Inc(RRAsked);
                If Pos('confirmed', ShapeHow) <> 1 Then Inc(RRUnconfirmed);
            End
            Else Pad.TopShape := eRounded;

            { patch 2: optional hole / plating / expansion features }
            If PlatedStr = 'false' Then Pad.Plated := False;
            If HoleTypeStr = 'slot' Then
            Begin
                Pad.HoleType := eSlotHole;
                Pad.HoleWidth := Round(MilsToCoord(1) * SlotLen);
                Pad.HoleRotation := HoleRot;
            End
            Else If HoleTypeStr = 'square' Then Pad.HoleType := eSquareHole;
            If (MaskStr <> '') Or (PasteStr <> '') Then
            Begin
                Cache := Pad.GetState_Cache;
                If MaskStr <> '' Then
                Begin
                    Cache.SolderMaskExpansionValid := eCacheManual;
                    Cache.SolderMaskExpansion := Round(MilsToCoord(1) * StrToFloatDef(MaskStr, 0));
                End;
                If PasteStr <> '' Then
                Begin
                    Cache.PasteMaskExpansionValid := eCacheManual;
                    Cache.PasteMaskExpansion := Round(MilsToCoord(1) * StrToFloatDef(PasteStr, 0));
                End;
                Pad.SetState_Cache := Cache;
            End;

            Footprint.AddPCBObject(Pad);
            { The primitive must be registered with the library's backing Board as
              well as with the footprint, and the registration broadcast to both.
              Footprint.AddPCBObject alone is not enough: the object exists in the
              working copy, every read reports it, and the save discards it. That
              cost an afternoon on an LCSC import, where pads and tracks reported
              success and the footprint came back empty, and it was read as Altium
              refusing to author land patterns at all. Lib_AddFootprintText had the
              full sequence and was the only authoring call that survived a save. }
            PcbLib.Board.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"roundrect_requested":' + IntToStr(RRAsked)
        + ',"roundrect_unconfirmed":' + IntToStr(RRUnconfirmed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Lib_AddFootprintTrack - one track on a PcbLib footprint.                     }
{                                                                              }
{ Coordinates and width are DECIMAL mils, parsed with StrToFloatDef and        }
{ converted with MilsToCoordF, the 1/10000-mil path PCB_PlaceTrack has used   }
{ since 66e9e62 and the pad handlers since 0266d07. StrToIntDef here           }
{ quantised every courtyard and assembly line to 1 mil (25.4 um), and a value  }
{ like 12.5 did not round, it came back as the DEFAULT, because StrToIntDef    }
{ cannot read a decimal point at all.                                          }
{                                                                              }
{ Params: x1, y1, x2, y2, width (mils), layer, component_name, library_path.  }
{..............................................................................}
Function Lib_AddFootprintTrack(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, Width : Double;
    LayerStr, LayerName, ErrCode, Why : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), 10);
    LayerStr := ExtractJsonValue(Params, 'layer');

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    { Empty -> silkscreen (the safe default); any named layer is honoured so   }
    { courtyard/assembly tracks can go on Mechanical layers, not just overlay. }
    If LayerStr = '' Then Layer := eTopOverlay
    Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        If Track = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create track');
            Exit;
        End;
        Track.X1 := FootprintOriginX(Footprint) + MilsToCoordF(X1);
        Track.Y1 := FootprintOriginY(Footprint) + MilsToCoordF(Y1);
        Track.X2 := FootprintOriginX(Footprint) + MilsToCoordF(X2);
        Track.Y2 := FootprintOriginY(Footprint) + MilsToCoordF(Y2);
        Track.Width := MilsToCoordF(Width);
        Track.Layer := Layer;
        RegisterLibPrimitive(PcbLib, Footprint, Track, 'both');
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);
    NoteNextStep('This edit is in memory only. Run lib_save_library '
        + 'with the library path to write just this library, and check '
        + 'written_to_disk in the reply (app_save_all also rewrites '
        + 'every open sheet).');
    { The RESOLVED layer, named the way the stack names it, so a renamed or }
    { Mechanical17+ layer does not echo as GetLayerString's 'Unknown'.     }
    LayerName := ResolveLayerName(PcbLib.Board, Layer);
    If LayerName = '' Then LayerName := LayerStr;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true'
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"layer":"' + EscapeJsonString(LayerName) + '"'
        + ',"pending_save":true}');
End;

{ Batch track authoring: same shape as Lib_AddFootprintPads. A `tracks` array }
{ (~~ / ; / = separators) -- silkscreen outline + assembly outline in one IPC }
{ round-trip instead of one-per-segment. Per-track fields: x1,y1,x2,y2,width, }
{ layer (TopOverlay default), all lengths in DECIMAL mils. The footprint is   }
{ chosen once for the whole batch by component_name / library_path.          }
Function Lib_AddFootprintTracks(Params : String; RequestId : String) : String;
Var
    TracksStr, Op, Remaining, LayerStr, BadLayers, ErrCode, Why : String;
    OpCount, Added, Failed : Integer;
    X1, Y1, X2, Y2, Width : Double;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    TracksStr := ExtractJsonValue(Params, 'tracks');
    If TracksStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'tracks is required');
        Exit;
    End;

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := TracksStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X1 := StrToFloatDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToFloatDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToFloatDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToFloatDef(GetBatchField(Op, 'y2'), 0);
            Width := StrToFloatDef(GetBatchField(Op, 'width'), 10);
            LayerStr := GetBatchField(Op, 'layer');
            { Empty -> silkscreen (the safe default); any named layer is        }
            { honoured so courtyard/assembly tracks can go on Mechanical layers.}
            If LayerStr = '' Then Layer := eTopOverlay
            Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
            If Track = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Track.X1 := FootprintOriginX(Footprint) + MilsToCoordF(X1);
            Track.Y1 := FootprintOriginY(Footprint) + MilsToCoordF(Y1);
            Track.X2 := FootprintOriginX(Footprint) + MilsToCoordF(X2);
            Track.Y2 := FootprintOriginY(Footprint) + MilsToCoordF(Y2);
            Track.Width := MilsToCoordF(Width);
            Track.Layer := Layer;
            RegisterLibPrimitive(PcbLib, Footprint, Track, 'both');
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);
    If Added > 0 Then
        NoteNextStep('This edit is in memory only. Run lib_save_library '
            + 'with the library path to write just this library, and check '
            + 'written_to_disk in the reply (app_save_all also rewrites '
            + 'every open sheet).');

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount)
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"pending_save":' + BoolToJsonStr(Added > 0) + '}');
End;

Function Lib_AddFootprintArc(Params : String; RequestId : String) : String;
Var
    { Every length is DOUBLE now, centre, radius and width alike: the same    }
    { StrToIntDef defect the angles had (see below) applied to a 12.5 mil      }
    { radius, which came back as the 100 mil default.                         }
    XCenter, YCenter, Radius, Width : Double;
    { Angles are DOUBLE and are read with StrToFloatDef below. They are     }
    { declared `float` on the Python side, so the wire carries "360.0" and  }
    { StrToIntDef returned its DEFAULT on every call. EndAngle came through }
    { as 0 whatever the caller asked for, so every arc this tool drew had a }
    { zero sweep while the call reported success. IPCB_Arc takes Doubles,   }
    { so a half degree need not be rounded away either.                     }
    StartAngle, EndAngle : Double;
    LayerStr, LayerName, ErrCode, Why : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Arc : IPCB_Arc;
    Layer : TLayer;
Begin
    XCenter := StrToFloatDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToFloatDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToFloatDef(ExtractJsonValue(Params, 'radius'), 100);
    StartAngle := StrToFloatDef(ExtractJsonValue(Params, 'start_angle'), 0.0);
    EndAngle := StrToFloatDef(ExtractJsonValue(Params, 'end_angle'), 360.0);
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), 10);
    LayerStr := ExtractJsonValue(Params, 'layer');

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    { Empty -> silkscreen (the safe default); any named layer is honoured so   }
    { pin-1 / assembly arcs can go on Mechanical layers, not just overlay.      }
    If LayerStr = '' Then Layer := eTopOverlay
    Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
        If Arc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc');
            Exit;
        End;
        Arc.XCenter := FootprintOriginX(Footprint) + MilsToCoordF(XCenter);
        Arc.YCenter := FootprintOriginY(Footprint) + MilsToCoordF(YCenter);
        Arc.Radius := MilsToCoordF(Radius);
        Arc.StartAngle := StartAngle;
        Arc.EndAngle := EndAngle;
        Arc.LineWidth := MilsToCoordF(Width);
        Arc.Layer := Layer;
        RegisterLibPrimitive(PcbLib, Footprint, Arc, 'both');
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);
    NoteNextStep('This edit is in memory only. Run lib_save_library '
        + 'with the library path to write just this library, and check '
        + 'written_to_disk in the reply (app_save_all also rewrites '
        + 'every open sheet).');
    { The RESOLVED layer, named the way the stack names it, so a renamed or }
    { Mechanical17+ layer does not echo as GetLayerString's 'Unknown'.     }
    LayerName := ResolveLayerName(PcbLib.Board, Layer);
    If LayerName = '' Then LayerName := LayerStr;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true'
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"layer":"' + EscapeJsonString(LayerName) + '"'
        + ',"pending_save":true}');
End;

{ Lib_AddFootprintText - Stamp a text primitive onto a PcbLib footprint.       }
{                                                                              }
{ Trap baked into this handler: in a PcbLib, Footprint.AddPCBObject on its    }
{ own does not register the new primitive properly with the placement editor }
{ -- the text shows up only after a save+reload. The working pattern is to  }
{ add to BOTH the footprint AND the underlying Board, then broadcast        }
{ PCBM_BoardRegisteration to both. RegisterLibPrimitive does exactly that.  }
{                                                                              }
{ DUPLICATE TEXT (measured 2026-10-03: one call, two identical texts). Three  }
{ things changed, each closing one way it can happen, and the reply now says  }
{ which happened so one live call settles the rest:                          }
{   1. The footprint is chosen through ResolveLibFootprint, which makes it     }
{      current with a VERIFIED property write. This handler used a silent     }
{      SetState_CurrentComponent, and Board.AddPCBObject attaches to whichever }
{      footprint is current, so a switch that did not take put one text into  }
{      two footprints.                                                         }
{   2. Every text object the call CREATED is counted by address (new_objects), }
{      and how often the group iterator yields it (group_yields; 2 is the      }
{      measured double registration, harmless, gone after a reload). A second }
{      NEW object carrying the same string is removed from the FOOTPRINT and   }
{      counted in duplicates_removed; the library board too only with         }
{      remove_from_board=true (see REMOVING PRIMITIVES FROM A PCBLIB          }
{      FOOTPRINT). NOTHING IS REMOVED unless the object this call created was }
{      read by address AND is listed by the footprint: an unread address left }
{      NewAddr empty, every new text with the string then looked like a       }
{      duplicate, and the cleanup would have taken the new text with them    }
{      while reporting success. duplicate_cleanup says which way it went.     }
{   3. registration = both | footprint | board selects which half of the      }
{      registration runs, so a live session can find the one that persists    }
{      exactly once without a redeploy. Default both, unchanged.              }
{ The other half of the 2026-10-03 symptom was READ-side: the footprint       }
{ policy audit reads Lib_GetLibraryGeometry, whose text walk did not           }
{ de-duplicate by address, so a double-registered text read back as two. It   }
{ does now.                                                                    }
{                                                                              }
{ Params:                                                                      }
{   text        (required) - the string to place                              }
{   x, y                   - DECIMAL mils, relative to the footprint origin   }
{   size                   - text height in mils (default 50)                 }
{   width                  - stroke width in mils (default 8)                 }
{   rotation               - degrees, default 0, fractions allowed            }
{   layer                  - layer name, default 'TopOverlay'                 }
{   use_ttfont=true|false  - default false (stroke font)                      }
{   mirror=true|false      - default false                                     }
{   library_path           - optional .PcbLib to focus first                  }
{   component_name         - optional footprint name; made current, verified  }
{   registration           - both (default) | footprint | board               }
{   remove_from_board      - 'true' also takes a removed duplicate off the    }
{                            library board (opt-in, unmeasured)                }
Function Lib_AddFootprintText(Params : String; RequestId : String) : String;
Var
    TextStr, LayerStr, FlagStr, RespJson, ErrCode, Why : String;
    MirrorStr, RegMode, BeforeAddrs, AfterAddrs, NewAddr, Rest, Addr : String;
    RawStr, ExtraAddrs, LayerName, Cleanup, Entries, BoardJson, StillAddrs : String;
    Mirror, UseTTFont, FromBoard, MadeCurrent : Boolean;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Text : IPCB_Text;
    GrpIter : IPCB_GroupIterator;
    Txt : IPCB_Text;
    Layer : TLayer;
    X, Y, Size, Width, Rotation : Double;
    BeforeYields, NewObjects, NewYields, DupFound, Removed, BarPos : Integer;
    StillYields, BoardRemoved : Integer;
Begin
    TextStr := ExtractJsonValue(Params, 'text');
    If TextStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text is required');
        Exit;
    End;
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    Size := StrToFloatDef(ExtractJsonValue(Params, 'size'), 50);
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), 8);
    Rotation := StrToFloatDef(ExtractJsonValue(Params, 'rotation'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then LayerStr := 'TopOverlay';
    FlagStr := ExtractJsonValue(Params, 'use_ttfont');
    UseTTFont := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    { Bottom-side text must be mirrored or it reads backwards on the      }
    { board. audit_find_mirrored_pcb_text reports exactly this pairing:    }
    { eBottomOverlay without MirrorFlag, and eTopOverlay with it.          }
    MirrorStr := ExtractJsonValue(Params, 'mirror');
    Mirror := (MirrorStr = 'true') Or (MirrorStr = 'True') Or (MirrorStr = '1');
    FromBoard := (LowerCase(Trim(ExtractJsonValue(Params, 'remove_from_board'))) = 'true');
    RegMode := LowerCase(Trim(ExtractJsonValue(Params, 'registration')));
    If RegMode = '' Then RegMode := 'both';
    If (RegMode <> 'both') And (RegMode <> 'footprint') And (RegMode <> 'board') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
            'registration must be both, footprint or board, not "' + RegMode + '"');
        Exit;
    End;

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Layer := ResolveLayerId(PcbLib.Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    { Every text object the footprint holds BEFORE the call, by address. What }
    { is not in this set afterwards is what this call created.               }
    BeforeAddrs := LibTextAddresses(Footprint, BeforeYields);

    PCBServer.PreProcess;
    Try
        Text := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
        If Text = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                'PCBObjectFactory returned Nil for eTextObject');
            Exit;
        End;
        { Relative to the footprint's own origin, read AFTER the resolver made
          it current. Board.XOrigin is a board-wide reference and would drop
          the text far from the footprint. }
        Text.XLocation := FootprintOriginX(Footprint) + MilsToCoordF(X);
        Text.YLocation := FootprintOriginY(Footprint) + MilsToCoordF(Y);
        Text.Layer := Layer;
        Text.UseTTFonts := UseTTFont;
        Text.UnderlyingString := TextStr;
        Text.Size := MilsToCoordF(Size);
        Text.Width := MilsToCoordF(Width);
        Try Text.MirrorFlag := Mirror; Except End;
        Try Text.Rotation := Rotation; Except End;
        RegisterLibPrimitive(PcbLib, Footprint, Text, RegMode);
    Finally
        PCBServer.PostProcess;
    End;

    NewAddr := '';
    Try NewAddr := IntToStr(Text.I_ObjectAddress); Except End;

    { What the call created: every text object whose address was not there   }
    { before. NewYields counts repeats, NewObjects distinct objects.          }
    { ExtraAddrs collects new objects that are NOT the one created here and   }
    { carry the same raw string: a genuine duplicate, the 2026-10-03 symptom. }
    NewObjects := 0;
    NewYields := 0;
    DupFound := 0;
    ExtraAddrs := '';
    AfterAddrs := '|';
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            Addr := '';
            Try Addr := IntToStr(Txt.I_ObjectAddress); Except End;
            If (Addr <> '') And (Pos('|' + Addr + '|', BeforeAddrs) = 0) Then
            Begin
                Inc(NewYields);
                If Pos('|' + Addr + '|', AfterAddrs) = 0 Then
                Begin
                    AfterAddrs := AfterAddrs + Addr + '|';
                    Inc(NewObjects);
                    RawStr := '';
                    Try RawStr := Txt.UnderlyingString; Except End;
                    If (Addr <> NewAddr) And (RawStr = TextStr) Then
                    Begin
                        ExtraAddrs := ExtraAddrs + Addr + '|';
                        Inc(DupFound);
                    End;
                End;
            End;
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;

    { Remove the duplicates, footprint only unless remove_from_board. Only }
    { objects that did not exist before this call and carry this call's    }
    { string can be here, so nothing the footprint already held is touched. }
    {                                                                       }
    { AND ONLY WHEN THE CREATED TEXT IS KNOWN TO SURVIVE. "Not the one     }
    { created here" is decided by NewAddr. If that read raised, NewAddr is }
    { '' and the created text itself passes the duplicate test; if the     }
    { footprint does not list the created text, there is no surviving copy }
    { to keep. Either way duplicates_found is still counted and reported,  }
    { and nothing is removed.                                              }
    Removed := 0;
    BoardRemoved := 0;
    Entries := '';
    BoardJson := JsonObj(JsonBool('requested', FromBoard));
    If ExtraAddrs = '' Then
        Cleanup := 'none'
    Else If NewAddr = '' Then
        Cleanup := 'skipped: the created text''s address could not be read'
    Else If Pos('|' + NewAddr + '|', AfterAddrs) = 0 Then
        Cleanup := 'skipped: the footprint does not list the created text'
    Else
    Begin
        Cleanup := 'removed';
        PCBServer.PreProcess;
        Try
            Rest := ExtraAddrs;
            While Rest <> '' Do
            Begin
                BarPos := Pos('|', Rest);
                If BarPos = 0 Then Break;
                Addr := Copy(Rest, 1, BarPos - 1);
                Rest := Copy(Rest, BarPos + 1, Length(Rest));
                If Addr = '' Then Continue;
                If Addr = NewAddr Then Continue;
                If RemoveLibTextByAddress(Footprint, Addr, Entries) Then Inc(Removed);
            End;
        Finally
            PCBServer.PostProcess;
        End;

        { The opt-in board half, after the footprint read-back so it never }
        { touches a text the footprint still lists.                        }
        If FromBoard And (Entries <> '') Then
        Begin
            StillAddrs := LibTextAddresses(Footprint, StillYields);
            MadeCurrent := LibMakeCurrentVerified(PcbLib, Footprint);
            PCBServer.PreProcess;
            Try
                BoardJson := LibBoardRemoveEntries(PcbLib.Board, Entries,
                    StillAddrs, MadeCurrent, BoardRemoved);
            Finally
                PCBServer.PostProcess;
            End;
        End;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);
    NoteNextStep('This edit is in memory only. Run lib_save_library '
        + 'with the library path to write just this library, and check '
        + 'written_to_disk in the reply (app_save_all also rewrites '
        + 'every open sheet).');
    LayerName := ResolveLayerName(PcbLib.Board, Layer);
    If LayerName = '' Then LayerName := LayerStr;

    RespJson :=
        '{"success":true' +
        ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"' +
        ',"text":"' + EscapeJsonString(TextStr) + '"' +
        ',"layer":"' + EscapeJsonString(LayerName) + '"' +
        ',"x":' + FloatToJsonStr(X) +
        ',"y":' + FloatToJsonStr(Y) +
        ',"registration":"' + RegMode + '"' +
        ',"new_objects":' + IntToStr(NewObjects) +
        ',"group_yields":' + IntToStr(NewYields) +
        ',"duplicates_found":' + IntToStr(DupFound) +
        ',"duplicates_removed":' + IntToStr(Removed) +
        ',"duplicate_cleanup":"' + EscapeJsonString(Cleanup) + '"' +
        ',"board_removal":' + BoardJson +
        ',"pending_save":true}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Lib_AddFootprintFills - rectangular fills on ANY layer of a PcbLib footprint.}
{                                                                              }
{ For what used to be board-level notes: stencil apertures on TopPaste (with   }
{ the pad's own paste suppressed by its paste_expansion), solder-mask openings }
{ on TopSolder, keep-outs. A fill is a rectangle and a rotation about its      }
{ centre; anything else is a region (Lib_AddFootprintRegions).                 }
{                                                                              }
{ The same members PCB_PlaceFill writes on a board (X1Location..Y2Location,    }
{ Rotation, Layer), the corners normalised so X1/Y1 is the low corner, plus    }
{ the RegisterLibPrimitive registration every footprint primitive needs.       }
{                                                                              }
{ LAYER IS REQUIRED PER FILL. Tracks default to silkscreen because a stray     }
{ silk line is harmless; a fill defaulted to anything would be copper or paste }
{ that nobody asked for, so an empty layer fails that one fill.               }
{                                                                              }
{ keepout=true on a fill NOT on KeepOutLayer writes IsKeepout, the layer-      }
{ specific keep-out. UNVERIFIED WRITE: IsKeepout is read in PCB.pas and        }
{ PCBGeneric.pas but written nowhere else, and the reply reads it back         }
{ (keepout_applied) so the first live call says whether it took. A fill ON     }
{ KeepOutLayer is a keep-out by its layer and needs no write.                  }
{                                                                              }
{ Params: fills (batch: x1,y1,x2,y2,layer,rotation,keepout; DECIMAL mils),    }
{         component_name, library_path.                                        }
{..............................................................................}
Function Lib_AddFootprintFills(Params : String; RequestId : String) : String;
Var
    FillsStr, Op, Remaining, LayerStr, BadLayers, FailReasons, ErrCode, Why : String;
    ItemStr, KeepStr : String;
    OpCount, Added, Failed, KeepAsked, KeepApplied : Integer;
    X1, Y1, X2, Y2, Rotation, Tmp : Double;
    WantKeepout, GotKeepout : Boolean;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Fill : IPCB_Fill;
    Layer : TLayer;
Begin
    FillsStr := ExtractJsonValue(Params, 'fills');
    If FillsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'fills is required');
        Exit;
    End;

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    KeepAsked := 0;
    KeepApplied := 0;
    BadLayers := '';
    FailReasons := '';
    Remaining := FillsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            ItemStr := 'fill ' + IntToStr(OpCount);
            X1 := StrToFloatDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToFloatDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToFloatDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToFloatDef(GetBatchField(Op, 'y2'), 0);
            Rotation := StrToFloatDef(GetBatchField(Op, 'rotation'), 0);
            LayerStr := GetBatchField(Op, 'layer');
            KeepStr := LowerCase(GetBatchField(Op, 'keepout'));
            WantKeepout := (KeepStr = 'true') Or (KeepStr = '1');

            If LayerStr = '' Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'layer is required');
                Continue;
            End;
            Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                AddFailReason(FailReasons, ItemStr, 'unknown layer ' + LayerStr);
                Continue;
            End;

            If X1 > X2 Then Begin Tmp := X1; X1 := X2; X2 := Tmp; End;
            If Y1 > Y2 Then Begin Tmp := Y1; Y1 := Y2; Y2 := Tmp; End;
            If (X1 = X2) Or (Y1 = Y2) Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'zero area');
                Continue;
            End;

            Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
            If Fill = Nil Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'PCBObjectFactory returned Nil');
                Continue;
            End;

            Fill.X1Location := FootprintOriginX(Footprint) + MilsToCoordF(X1);
            Fill.Y1Location := FootprintOriginY(Footprint) + MilsToCoordF(Y1);
            Fill.X2Location := FootprintOriginX(Footprint) + MilsToCoordF(X2);
            Fill.Y2Location := FootprintOriginY(Footprint) + MilsToCoordF(Y2);
            Fill.Rotation := Rotation;
            Fill.Layer := Layer;

            If WantKeepout And (Layer <> eKeepOutLayer) Then
            Begin
                Inc(KeepAsked);
                Try Fill.IsKeepout := True; Except End;
                GotKeepout := False;
                Try GotKeepout := Fill.IsKeepout; Except GotKeepout := False; End;
                If GotKeepout Then Inc(KeepApplied);
            End;

            RegisterLibPrimitive(PcbLib, Footprint, Fill, 'both');
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Added > 0 Then
    Begin
        MarkDocDirtyByPath(PcbLib.Board.FileName);
        NoteNextStep('This edit is in memory only. Run lib_save_library '
            + 'with the library path to write just this library, and check '
            + 'written_to_disk in the reply (app_save_all also rewrites '
            + 'every open sheet).');
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"failures":[' + FailReasons + ']'
        + ',"keepout_requested":' + IntToStr(KeepAsked)
        + ',"keepout_applied":' + IntToStr(KeepApplied)
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"pending_save":' + BoolToJsonStr(Added > 0) + '}');
End;

{..............................................................................}
{ NextCsvValue - take the next comma-separated value off the front of S.       }
{                                                                              }
{ The NextBatchOp shape (a Var String consumed from the front), for the one    }
{ batch field that carries a list: a region's pts=x1,y1,x2,y2,... Walking the }
{ string twice, once to count and once to fill the contour, needs no storage:  }
{ a fixed local array corrupts a Function's return slot in DelphiScript, and   }
{ this keeps TStringList housekeeping out of the region loop as well.          }
{..............................................................................}
Function NextCsvValue(Var S : String) : String;
Var
    CommaPos : Integer;
Begin
    CommaPos := Pos(',', S);
    If CommaPos > 0 Then
    Begin
        Result := Trim(Copy(S, 1, CommaPos - 1));
        S := Copy(S, CommaPos + 1, Length(S));
    End
    Else
    Begin
        Result := Trim(S);
        S := '';
    End;
End;

{..............................................................................}
{ Lib_AddFootprintRegions - polygonal regions on ANY layer of a footprint.     }
{                                                                              }
{ The outline goes in through the one region-authoring path this codebase has, }
{ PCB_PlaceRegion's: MainContour.Replicate, set Count, write the 1-based X[i]  }
{ and Y[i], SetOutlineContour. Every reader in PCB.pas and Generic.pas walks a }
{ contour as 1..Count, which is the indexing written here.                     }
{                                                                              }
{ THE CONTOUR IS READ BACK BEFORE THE REGION IS KEPT. A region whose outline   }
{ did not take is a degenerate object, so if MainContour.Count is not the     }
{ number of vertices sent, that region fails and is never registered.          }
{                                                                              }
{ kind, per region:                                                            }
{   copper   (default) a solid region on the layer: copper, paste aperture,    }
{            mask opening, mechanical shading. Kind is left as the factory's. }
{   cutout   a polygon-pour cutout: Kind := eRegionKind_Cutout. The same write }
{            obj_modify makes (PCBGeneric.pas), itself awaiting live proof in  }
{            RELEASE_VERIFICATION step 16; read back as kind_applied.          }
{   keepout  on KeepOutLayer (the default layer for this kind) a keep-out by   }
{            its layer, no write needed; on any other layer the layer-specific }
{            keep-out, IsKeepout := True, UNVERIFIED and read back as          }
{            keepout_applied.                                                  }
{ An unknown kind fails that region rather than falling back to copper.        }
{                                                                              }
{ Vertices arrive as one batch field, pts=x1,y1,x2,y2,... in DECIMAL mils     }
{ relative to the footprint origin, walked twice with NextCsvValue: once to    }
{ count and check, once to fill the contour.                                   }
{                                                                              }
{ Params: regions (batch: pts, layer, kind), component_name, library_path.    }
{..............................................................................}
Function Lib_AddFootprintRegions(Params : String; RequestId : String) : String;
Var
    RegionsStr, Op, Remaining, LayerStr, KindStr, PtsStr, Walk, Token : String;
    XTok, YTok, BadLayers, FailReasons, ErrCode, Why, ItemStr : String;
    OpCount, Added, Failed, K, N, NPts, Got, Vertices : Integer;
    KindAsked, KindApplied, KeepAsked, KeepApplied : Integer;
    GotKeepout, KindOk, AllNumbers : Boolean;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    Layer : TLayer;
Begin
    RegionsStr := ExtractJsonValue(Params, 'regions');
    If RegionsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'regions is required');
        Exit;
    End;

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Vertices := 0;
    KindAsked := 0;
    KindApplied := 0;
    KeepAsked := 0;
    KeepApplied := 0;
    BadLayers := '';
    FailReasons := '';
    Remaining := RegionsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            ItemStr := 'region ' + IntToStr(OpCount);
            KindStr := LowerCase(Trim(GetBatchField(Op, 'kind')));
            If KindStr = '' Then KindStr := 'copper';
            If (KindStr <> 'copper') And (KindStr <> 'cutout') And (KindStr <> 'keepout') Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr,
                    'unknown kind ' + KindStr + ' (copper, cutout or keepout)');
                Continue;
            End;

            LayerStr := GetBatchField(Op, 'layer');
            If (LayerStr = '') And (KindStr = 'keepout') Then LayerStr := 'KeepOutLayer';
            If LayerStr = '' Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'layer is required');
                Continue;
            End;
            Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                AddFailReason(FailReasons, ItemStr, 'unknown layer ' + LayerStr);
                Continue;
            End;

            { pts=x,y,x,y,... counted and checked first. A truncated list is    }
            { refused rather than padded with a zero, and so is a value that is  }
            { not a number: StrToFloatDef would quietly turn it into 0.          }
            PtsStr := GetBatchField(Op, 'pts');
            Walk := PtsStr;
            N := 0;
            AllNumbers := True;
            While Walk <> '' Do
            Begin
                Token := NextCsvValue(Walk);
                If Not IsFloatStr(Token) Then AllNumbers := False;
                N := N + 1;
            End;
            If Not AllNumbers Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'pts holds a value that is not a number');
                Continue;
            End;
            If (N Mod 2) <> 0 Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'pts holds an odd number of values');
                Continue;
            End;
            NPts := N Div 2;
            If NPts < 3 Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'at least 3 vertices are required');
                Continue;
            End;

            Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
            If Region = Nil Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'PCBObjectFactory returned Nil');
                Continue;
            End;
            Region.Layer := Layer;

            Contour := Region.MainContour.Replicate;
            Contour.Count := NPts;
            Walk := PtsStr;
            For K := 1 To NPts Do
            Begin
                XTok := NextCsvValue(Walk);
                YTok := NextCsvValue(Walk);
                Contour.X[K] := FootprintOriginX(Footprint)
                    + MilsToCoordF(StrToFloatDef(XTok, 0));
                Contour.Y[K] := FootprintOriginY(Footprint)
                    + MilsToCoordF(StrToFloatDef(YTok, 0));
            End;
            Region.SetOutlineContour(Contour);

            Got := -1;
            Try Got := Region.MainContour.Count; Except Got := -1; End;
            If Got <> NPts Then
            Begin
                Inc(Failed);
                AddFailReason(FailReasons, ItemStr, 'the outline holds ' + IntToStr(Got)
                    + ' of ' + IntToStr(NPts) + ' vertices after SetOutlineContour');
                Continue;
            End;

            If KindStr = 'cutout' Then
            Begin
                Inc(KindAsked);
                Try Region.Kind := eRegionKind_Cutout; Except End;
                KindOk := False;
                Try KindOk := (Region.Kind = eRegionKind_Cutout); Except KindOk := False; End;
                If KindOk Then Inc(KindApplied);
            End;
            If (KindStr = 'keepout') And (Layer <> eKeepOutLayer) Then
            Begin
                Inc(KeepAsked);
                Try Region.IsKeepout := True; Except End;
                GotKeepout := False;
                Try GotKeepout := Region.IsKeepout; Except GotKeepout := False; End;
                If GotKeepout Then Inc(KeepApplied);
            End;

            RegisterLibPrimitive(PcbLib, Footprint, Region, 'both');
            Vertices := Vertices + Got;
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Added > 0 Then
    Begin
        MarkDocDirtyByPath(PcbLib.Board.FileName);
        NoteNextStep('This edit is in memory only. Run lib_save_library '
            + 'with the library path to write just this library, and check '
            + 'written_to_disk in the reply (app_save_all also rewrites '
            + 'every open sheet).');
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount)
        + ',"vertices":' + IntToStr(Vertices)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"failures":[' + FailReasons + ']'
        + ',"kind_requested":' + IntToStr(KindAsked)
        + ',"kind_applied":' + IntToStr(KindApplied)
        + ',"keepout_requested":' + IntToStr(KeepAsked)
        + ',"keepout_applied":' + IntToStr(KeepApplied)
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"pending_save":' + BoolToJsonStr(Added > 0) + '}');
End;

{..............................................................................}
{ Lib_SetCurrentFootprint - make a named footprint the library's current one. }
{                                                                              }
{ The PcbLib twin of Lib_SetCurrentComponent, which only knows SchLibs. Until  }
{ this, the only way to aim the pad tools at an existing footprint was to      }
{ stamp a text on it with component_name and delete the text afterwards.      }
{ Everything is ResolveLibFootprint, so the switch is verified the same way   }
{ the add handlers verify it, and the reply carries the origin read AFTER the  }
{ switch, which is the frame every add handler writes in.                      }
{                                                                              }
{ Params: component_name (required; footprint_name accepted), library_path.   }
{..............................................................................}
Function Lib_SetCurrentFootprint(Params : String; RequestId : String) : String;
Var
    FpWanted, ErrCode, Why : String;
    Footprint : IPCB_LibComponent;
Begin
    FpWanted := ExtractJsonValue(Params, 'component_name');
    If FpWanted = '' Then FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'component_name is required: the footprint to make current');
        Exit;
    End;

    Footprint := ResolveLibFootprint(Params, ErrCode, Why);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, Why);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true'
        + ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"'
        + ',"origin_x_mils":' + FloatToJsonStr(CoordToMilsF(FootprintOriginX(Footprint)))
        + ',"origin_y_mils":' + FloatToJsonStr(CoordToMilsF(FootprintOriginY(Footprint)))
        + '}');
End;

{ Lib_GetFootprints - Enumerate every footprint in the active (or named)     }
{ PcbLib. Mirror of lib_get_components (SchLib) for PCB libraries. Uses the }
{ documented IPCB_LibraryIterator pattern.                                   }
{                                                                              }
{ Params:                                                                      }
{   library_path - optional .PcbLib to focus first; defaults to focused doc. }
Function Lib_GetFootprints(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, FpDescr, FpsJson, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    Count : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    FpsJson := '[';
    Count := 0;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            FpDescr := '';
            Try FpName := Footprint.Name; Except End;
            Try FpDescr := Footprint.Description; Except End;
            If Count > 0 Then FpsJson := FpsJson + ',';
            FpsJson := FpsJson +
                '{"name":"' + EscapeJsonString(FpName) + '"' +
                ',"description":"' + EscapeJsonString(FpDescr) + '"}';
            Inc(Count);
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    FpsJson := FpsJson + ']';

    RespJson :=
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"count":' + IntToStr(Count) +
        ',"footprints":' + FpsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetFootprintPads - Read the pad geometry of one footprint in the active }
{ (or named) PcbLib, for export (e.g. KiCad .kicad_mod). Coordinates are in   }
{ mils relative to the library origin (the footprint reference point), which  }
{ is the same frame Lib_AddFootprintPad writes in.                            }
{                                                                              }
{ Params:                                                                      }
{   footprint_name - optional; defaults to the library's current component.   }
{   library_path   - optional .PcbLib to focus first; defaults to focused doc.}
{                                                                              }
{ Response: name, pad_count, and a pads array; each pad carries name, x, y,   }
{   size_x, size_y, shape, layer, hole, rotation -- dimensions in mils, angles }
{   in degrees.                                                                }
Function Lib_GetFootprintPads(Params : String; RequestId : String) : String;
Var
    SeenPads : TStringList;
    PadAddr : String;
    LibPath, FocusedPath, FpWanted, FpName : String;
    ShapeStr, LayerStr, PadsJson, RespJson : String;
    PrimsJson, KindStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    XOrg, YOrg : TCoord;
    Count, PCount, BodyCount : Integer;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    { Pick the target footprint: by name if given, else the current one. }
    Target := Nil;
    If FpWanted = '' Then
        Target := PcbLib.CurrentComponent
    Else
    Begin
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If UpperCase(FpName) = UpperCase(FpWanted) Then
                Begin
                    Target := Footprint;
                    Break;
                End;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    FpName := '';
    Try FpName := Target.Name; Except End;
    { The footprint's own origin is the reference point, NOT Board.XOrigin:
      a PcbLib shares one board across every footprint, so a board-wide origin
      exported pads tens of thousands of mils away from where they belong. }
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPads := TStringList.Create;
    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            PadAddr := '';
            Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
            If (PadAddr <> '') And (SeenPads.IndexOf(PadAddr) >= 0) Then
            Begin
                Pad := GrpIter.NextPCBObject;
                Continue;
            End;
            If PadAddr <> '' Then SeenPads.Add(PadAddr);
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                { eRoundedRectangular is the PAD SHAPE. This used to test
                  eRoundRectangle, which is a SCHEMATIC object id. Both
                  identifiers exist, so nothing errored and the test simply
                  never matched: every rounded-rectangle pad read back as
                  round with corner_pct 0, including ones this same library
                  had just written with
                  SetState_StackShapeOnLayer(..., eRoundedRectangular). }
                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
                Else ShapeStr := 'round';
            Except End;

            LayerStr := 'top';
            Try
                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                Else LayerStr := 'top';
            Except End;

            If Count > 0 Then PadsJson := PadsJson + ',';
            PadsJson := PadsJson +
                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                ',"x":' + IntToStr(CoordToMils(Pad.X - XOrg)) +
                ',"y":' + IntToStr(CoordToMils(Pad.Y - YOrg)) +
                ',"size_x":' + IntToStr(CoordToMils(Pad.TopXSize)) +
                ',"size_y":' + IntToStr(CoordToMils(Pad.TopYSize)) +
                ',"shape":"' + ShapeStr + '"' +
                ',"layer":"' + LayerStr + '"' +
                ',"hole":' + IntToStr(CoordToMils(Pad.HoleSize)) +
                ',"rotation":' + FloatToJsonStr(Pad.Rotation) + '}';
            Inc(Count);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
        Try SeenPads.Free; Except End;
    End;
    PadsJson := PadsJson + ']';

    { Non-pad graphics: tracks / arcs / regions / fills by layer (for the
      silkscreen / assembly / courtyard policy checks), plus a count of 3D
      component bodies. Layer names come from GetLayerString, so the policy
      auditor sees 'TopOverlay', 'Mechanical13', etc. Only ObjectId / Layer
      are touched here -- both live on IPCB_Primitive -- so no interface
      narrowing is needed. }
    PrimsJson := '[';
    PCount := 0;
    BodyCount := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
            eRegionObject, eFillObject, eComponentBodyObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            If Prim.ObjectId = eComponentBodyObject Then
                Inc(BodyCount)
            Else
            Begin
                KindStr := 'track';
                If Prim.ObjectId = eArcObject Then KindStr := 'arc'
                Else If Prim.ObjectId = eRegionObject Then KindStr := 'region'
                Else If Prim.ObjectId = eFillObject Then KindStr := 'fill';
                LayerStr := '';
                Try LayerStr := GetLayerString(Prim.Layer); Except End;
                If PCount > 0 Then PrimsJson := PrimsJson + ',';
                PrimsJson := PrimsJson +
                    '{"kind":"' + KindStr + '"' +
                    ',"layer":"' + EscapeJsonString(LayerStr) + '"}';
                Inc(PCount);
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    PrimsJson := PrimsJson + ']';

    RespJson :=
        '{"name":"' + EscapeJsonString(FpName) + '"' +
        ',"pad_count":' + IntToStr(Count) +
        ',"pads":' + PadsJson +
        ',"primitives":' + PrimsJson +
        ',"bodies":' + IntToStr(BodyCount) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetLibraryGeometry - Dump the policy-relevant geometry of EVERY         }
{ footprint in one library pass, for the footprint-policy auditor.            }
{                                                                              }
{ Lib_GetFootprintPads answers one footprint per call and re-scans the whole  }
{ library to find it by name, so auditing an N-footprint library costs N IPC  }
{ round trips and O(N^2) iterator steps -- minutes on a 1000-part library.    }
{ This walks the LibraryIterator once and emits a compact record per          }
{ footprint. Only the fields the auditor reads are emitted: pads carry        }
{ name/shape/layer/hole (no coordinates), and primitives are DEDUPLICATED to  }
{ the distinct (kind, layer) pairs -- a footprint with 200 silk tracks emits  }
{ one entry, since the auditor only ever looks at the set of layers per role. }
{                                                                              }
{ Each footprint's geometry read is wrapped so one malformed footprint drops  }
{ out of the sweep instead of halting the polling loop.                       }
{                                                                              }
{ Params:                                                                      }
{   library_path - optional .PcbLib to focus first; defaults to focused doc.  }
{   offset       - index of the first footprint to emit (default 0).          }
{   limit        - max footprints to emit (default 250) -- bounds response    }
{                  size; page until offset+count >= total.                    }
{                                                                              }
{ Response: library_path, total, offset, count, and a footprints array whose  }
{   entries carry name, pads, primitives, texts, pad_center, bodies.          }
{   pad_center is the AVERAGE PAD CENTRE in mils, the reference a designator  }
{   should be centred on -- the library origin is arbitrary and unrelated.    }
Function Lib_GetLibraryGeometry(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName : String;
    ShapeStr, LayerStr, KindStr, PrimKey, SeenKeys : String;
    PadsJson, PrimsJson, TextsJson, FpsJson, RespJson : String;
    TxtStr, CenterJson : String;
    SeenAddrs, PrimAddr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    Txt : IPCB_Text;
    XOrg, YOrg : TCoord;
    Offset, Limit, Total, Emitted : Integer;
    PadN, PrimN, TextN, BodyCount, DesigCount : Integer;
    SumX, SumY, PadX, PadY, LayerId : Integer;
    CenterX, CenterY, TxtX, TxtY : TCoord;
    TRect : TCoordRect;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    Offset := StrToIntDef(ExtractJsonValue(Params, 'offset'), 0);
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 250);
    If Offset < 0 Then Offset := 0;
    If Limit <= 0 Then Limit := 250;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    FpsJson := '[';
    Total := 0;
    Emitted := 0;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            If (Total >= Offset) And (Emitted < Limit) Then
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                { Every coordinate below is relative to THIS FOOTPRINT'S OWN
                  origin (its reference point), not to Board.XOrigin. A PcbLib
                  shares one board across all footprints, and each footprint
                  sits wherever it was drawn, so a board-wide origin yields a
                  meaningless frame -- footprints read as tens of thousands of
                  mils apart when they are really all drawn about their own
                  reference point. }
                XOrg := 0;  YOrg := 0;
                Try XOrg := Footprint.X; Except End;
                Try YOrg := Footprint.Y; Except End;

                { Pads, and the running sum of their centres. The average pad
                  centre is the reference a designator should be centred on. }
                PadsJson := '[';
                PadN := 0;
                SumX := 0;
                SumY := 0;
                { DEDUPE BY OBJECT ADDRESS, as Lib_GetFootprintPads and
                  Lib_ProbeFootprint already do. A primitive added this
                  session is registered with the footprint AND the library
                  board, and until a reload the group iterator yields it
                  twice: measured as pad_count 6 for three pads. Without
                  this the policy audit counted every new pad twice and saw
                  every new text twice, which is how one text added by
                  lib_add_footprint_text read back as two (2026-10-03). }
                SeenAddrs := '|';
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Pad := GrpIter.FirstPCBObject;
                        While Pad <> Nil Do
                        Begin
                            PrimAddr := '';
                            Try PrimAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                            If (PrimAddr <> '') And (Pos('|' + PrimAddr + '|', SeenAddrs) > 0) Then
                            Begin
                                Pad := GrpIter.NextPCBObject;
                                Continue;
                            End;
                            If PrimAddr <> '' Then SeenAddrs := SeenAddrs + PrimAddr + '|';
                            ShapeStr := 'round';
                            Try
                                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
                                Else ShapeStr := 'round';
                            Except End;
                            LayerStr := 'top';
                            Try
                                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                                Else LayerStr := 'top';
                            Except End;
                            { Accumulate in native TCoord, not mils: summing
                              per-pad mils truncates each term and the average
                              lands up to a mil off the true centre. }
                            PadX := 0;
                            PadY := 0;
                            Try
                                PadX := Pad.X - XOrg;
                                PadY := Pad.Y - YOrg;
                            Except End;
                            SumX := SumX + PadX;
                            SumY := SumY + PadY;
                            If PadN > 0 Then PadsJson := PadsJson + ',';
                            PadsJson := PadsJson +
                                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                                ',"shape":"' + ShapeStr + '"' +
                                ',"layer":"' + LayerStr + '"' +
                                ',"hole":' + IntToStr(CoordToMils(Pad.HoleSize)) + '}';
                            Inc(PadN);
                            Pad := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                PadsJson := PadsJson + ']';

                { pad_center is mils (human-readable, what the policy compares);
                  pad_center_coord is the exact TCoord the writer must use, so
                  a re-centre does not introduce a mil of rounding error. }
                CenterJson := 'null';
                If PadN > 0 Then
                Begin
                    CenterX := SumX Div PadN;
                    CenterY := SumY Div PadN;
                    CenterJson := '{"x":' + IntToStr(CoordToMils(CenterX)) +
                                  ',"y":' + IntToStr(CoordToMils(CenterY)) +
                                  ',"coord_x":' + IntToStr(CenterX) +
                                  ',"coord_y":' + IntToStr(CenterY) + '}';
                End;

                { Distinct (kind, layer) pairs only -- SeenKeys is a delimited
                  membership string, so no TStringList is needed here. }
                PrimsJson := '[';
                PrimN := 0;
                BodyCount := 0;
                SeenKeys := '';
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                            eRegionObject, eFillObject, eComponentBodyObject));
                        Prim := GrpIter.FirstPCBObject;
                        While Prim <> Nil Do
                        Begin
                            If Prim.ObjectId = eComponentBodyObject Then
                                Inc(BodyCount)
                            Else
                            Begin
                                KindStr := 'track';
                                If Prim.ObjectId = eArcObject Then KindStr := 'arc'
                                Else If Prim.ObjectId = eRegionObject Then KindStr := 'region'
                                Else If Prim.ObjectId = eFillObject Then KindStr := 'fill';
                                LayerStr := '';
                                Try LayerStr := ResolveLayerName(PcbLib.Board, Prim.Layer); Except End;
                                LayerId := -1;
                                Try LayerId := Prim.Layer; Except End;
                                PrimKey := '|' + KindStr + '@' + IntToStr(LayerId) + '|';
                                If Pos(PrimKey, SeenKeys) = 0 Then
                                Begin
                                    SeenKeys := SeenKeys + PrimKey;
                                    If PrimN > 0 Then PrimsJson := PrimsJson + ',';
                                    PrimsJson := PrimsJson +
                                        '{"kind":"' + KindStr + '"' +
                                        ',"layer_id":' + IntToStr(LayerId) +
                                        ',"layer":"' + EscapeJsonString(LayerStr) + '"}';
                                    Inc(PrimN);
                                End;
                            End;
                            Prim := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                PrimsJson := PrimsJson + ']';

                { Text primitives: the designator string, the comment string,
                  and any free legend. A single-type filter means the iterator
                  hands back a narrowed IPCB_Text, so .Text / .Size / location
                  are reachable. Every read is guarded: these properties are
                  the version-sensitive part of this dump. }
                TextsJson := '[';
                TextN := 0;
                DesigCount := 0;
                SeenAddrs := '|';
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                        Txt := GrpIter.FirstPCBObject;
                        While Txt <> Nil Do
                        Begin
                            PrimAddr := '';
                            Try PrimAddr := IntToStr(Txt.I_ObjectAddress); Except End;
                            If (PrimAddr <> '') And (Pos('|' + PrimAddr + '|', SeenAddrs) > 0) Then
                            Begin
                                Txt := GrpIter.NextPCBObject;
                                Continue;
                            End;
                            If PrimAddr <> '' Then SeenAddrs := SeenAddrs + PrimAddr + '|';
                            { UnderlyingString is the RAW authored string. .Text
                              is the RENDERED one: with special-string conversion
                              on, '.Designator' renders as the component's actual
                              designator (empty in a library), so matching on
                              .Text silently misses real designators. }
                            TxtStr := '';
                            Try TxtStr := Txt.UnderlyingString; Except End;
                            If TxtStr = '' Then
                                Try TxtStr := Txt.Text; Except End;
                            KindStr := 'free';
                            If UpperCase(TxtStr) = '.DESIGNATOR' Then
                            Begin
                                KindStr := 'designator';
                                Inc(DesigCount);
                            End
                            Else If UpperCase(TxtStr) = '.COMMENT' Then KindStr := 'comment';
                            LayerStr := '';
                            Try LayerStr := ResolveLayerName(PcbLib.Board, Txt.Layer); Except End;
                            { Emit the raw TLayer ordinal too. If even the layer
                              stack cannot name the layer, the ordinal is still a
                              stable identity to group and compare by. }
                            LayerId := -1;
                            Try LayerId := Txt.Layer; Except End;
                            If TextN > 0 Then TextsJson := TextsJson + ',';
                            TextsJson := TextsJson +
                                '{"text":"' + EscapeJsonString(TxtStr) + '"' +
                                ',"kind":"' + KindStr + '"' +
                                ',"layer_id":' + IntToStr(LayerId) +
                                ',"layer":"' + EscapeJsonString(LayerStr) + '"';
                            Try
                                TextsJson := TextsJson +
                                    ',"height":' + IntToStr(CoordToMils(Txt.Size));
                            Except End;
                            { Report the text's BOUNDING-BOX CENTRE, not its
                              XLocation. XLocation is the anchor (a corner), so
                              centring on it leaves the string hanging half its
                              width off the part. Fall back to the anchor only
                              if the bounding rectangle is unavailable. }
                            TxtX := 0;
                            TxtY := 0;
                            Try
                                TxtX := Txt.XLocation;
                                TxtY := Txt.YLocation;
                            Except End;
                            Try
                                TRect := Txt.BoundingRectangle;
                                TxtX := (TRect.X1 + TRect.X2) Div 2;
                                TxtY := (TRect.Y1 + TRect.Y2) Div 2;
                            Except End;
                            { x/y and coord_x/coord_y are the BBOX CENTRE (what
                              "centred" means). anchor_x/anchor_y are XLocation,
                              the corner the writer actually assigns. Emitting
                              both lets the caller compute the exact anchor that
                              puts the bbox centre on target, with no bounding
                              box read at write time. }
                            Try
                                TextsJson := TextsJson +
                                    ',"x":' + IntToStr(CoordToMils(TxtX - XOrg)) +
                                    ',"y":' + IntToStr(CoordToMils(TxtY - YOrg)) +
                                    ',"coord_x":' + IntToStr(TxtX - XOrg) +
                                    ',"coord_y":' + IntToStr(TxtY - YOrg) +
                                    ',"anchor_x":' + IntToStr(Txt.XLocation - XOrg) +
                                    ',"anchor_y":' + IntToStr(Txt.YLocation - YOrg);
                            Except End;
                            TextsJson := TextsJson + '}';
                            Inc(TextN);
                            Txt := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                TextsJson := TextsJson + ']';

                If Emitted > 0 Then FpsJson := FpsJson + ',';
                FpsJson := FpsJson +
                    '{"name":"' + EscapeJsonString(FpName) + '"' +
                    ',"pads":' + PadsJson +
                    ',"primitives":' + PrimsJson +
                    ',"texts":' + TextsJson +
                    ',"pad_center":' + CenterJson +
                    ',"designator_count":' + IntToStr(DesigCount) +
                    ',"bodies":' + IntToStr(BodyCount) + '}';
                Inc(Emitted);
            End;
            Inc(Total);
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    FpsJson := FpsJson + ']';

    RespJson :=
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"offset":' + IntToStr(Offset) +
        ',"count":' + IntToStr(Emitted) +
        ',"footprints":' + FpsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetDesignator - Move / resize / create one footprint's .Designator.     }
{                                                                              }
{ The target layer is addressed by its raw TLayer ORDINAL (layer_id), not by  }
{ name: a house layer called 'Assembly Designator' or 'Mechanical 18' is not  }
{ in GetLayerFromString's table, and the ordinal is exactly what              }
{ Lib_GetLibraryGeometry already reported for that layer. Python decides the  }
{ target from the library's own majority; this handler only applies it.       }
{                                                                              }
{ Params:                                                                      }
{   footprint_name - required.                                                }
{   library_path   - optional .PcbLib to focus first.                         }
{   layer_id       - optional TLayer ordinal to move the designator to.       }
{   x, y           - optional native TCoord offsets from the footprint origin.}
{   height         - optional text height in mils.                            }
{   create         - 'true' to add a .Designator when the footprint has none; }
{                    refused outright if one already exists.                  }
{                                                                              }
{ Every field is optional and applied only when supplied, so one handler      }
{ serves a layer move, a re-centre, a resize, or a create.                    }
Function Lib_SetDesignator(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, TxtStr, RespJson, ChangedJson : String;
    XStr, YStr, HeightStr, LayerStr, CreateStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    SrvDoc : IServerDocument;
    Lyr : TLayer;
    XOrg, YOrg, TgtX, TgtY, CurCX, CurCY : TCoord;
    TRect : TCoordRect;
    NewX, NewY, NewH, LayerId, WidthMils : Integer;
    DoCreate, Created : Boolean;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'footprint_name is required');
        Exit;
    End;
    LibPath := ExtractJsonValue(Params, 'library_path');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    HeightStr := ExtractJsonValue(Params, 'height');
    LayerStr := ExtractJsonValue(Params, 'layer_id');
    CreateStr := ExtractJsonValue(Params, 'create');
    DoCreate := (CreateStr = 'true') Or (CreateStr = 'True') Or (CreateStr = '1');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    Board := PcbLib.Board;
    { Coordinates are relative to the footprint's own origin, matching what
      Lib_GetLibraryGeometry reports. Board.XOrigin is NOT that reference. }
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    { Locate the existing .Designator, if any. Match on UnderlyingString: .Text
      returns the RENDERED special string, so matching it misses real
      designators and a create would then add a DUPLICATE. }
    Desig := Nil;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
            If UpperCase(TxtStr) = '.DESIGNATOR' Then
            Begin
                Desig := Txt;
                Break;
            End;
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;

    If DoCreate And (Desig <> Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'ALREADY_HAS_DESIGNATOR',
            'Refusing to create: footprint already has a .Designator: ' + FpWanted);
        Exit;
    End;

    If (Desig = Nil) And (Not DoCreate) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DESIGNATOR',
            'Footprint has no .Designator and create was not requested: ' + FpWanted);
        Exit;
    End;

    LayerId := StrToIntDef(LayerStr, -1);
    NewX := StrToIntDef(XStr, 0);
    NewY := StrToIntDef(YStr, 0);
    NewH := StrToIntDef(HeightStr, 0);
    Created := False;
    ChangedJson := '';

    PCBServer.PreProcess;
    Try
        If Desig = Nil Then
        Begin
            { Creating: layer, position and height must all be supplied,
              otherwise the new string would land somewhere arbitrary. }
            If (LayerId < 0) Or (XStr = '') Or (YStr = '') Or (NewH <= 0) Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
                    'create requires layer_id, x, y and height');
                Exit;
            End;
            { Board.AddPCBObject attaches to the library's CURRENT component,
              so the target must be made current or the text lands elsewhere. }
            Try PcbLib.CurrentComponent := Target; Except End;
            If PcbLib.CurrentComponent <> Target Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                    'Could not make the target footprint current: ' + FpWanted);
                Exit;
            End;
            Desig := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
            If Desig = Nil Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                    'PCBObjectFactory returned Nil for eTextObject');
                Exit;
            End;
            Desig.UnderlyingString := '.Designator';
            WidthMils := NewH Div 5;
            If WidthMils < 1 Then WidthMils := 1;
            Desig.Width := MilsToCoord(WidthMils);
            Target.AddPCBObject(Desig);
            Board.AddPCBObject(Desig);
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
            Created := True;
            ChangedJson := ChangedJson + '"created",';
        End;

        { Bracket the mutation, or the edit lives in memory and is dropped when
          the component is re-serialised on save. }
        Try
            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                c_Broadcast, PCBM_BeginModify, c_NoEventData);
        Except End;

        If LayerId >= 0 Then
        Begin
            Lyr := LayerId;
            Try
                Desig.Layer := Lyr;
                ChangedJson := ChangedJson + '"layer",';
            Except End;
        End;
        { Size FIRST: resizing changes the bounding box the centring uses. }
        If NewH > 0 Then
        Begin
            Try
                Desig.Size := MilsToCoord(NewH);
                ChangedJson := ChangedJson + '"height",';
            Except End;
        End;
        If (XStr <> '') And (YStr <> '') Then
        Begin
            { Re-read the origin HERE: the create path makes the component
              current, which relocates it, so an earlier reading is stale. }
            Try XOrg := Target.X; Except End;
            Try YOrg := Target.Y; Except End;
            { x/y are the ANCHOR (XLocation) the caller wants, in TCoord
              relative to the footprint origin. The caller derives it from the
              anchor and bbox centre this script reports. }
            Try
                Desig.XLocation := XOrg + NewX;
                Desig.YLocation := YOrg + NewY;
                ChangedJson := ChangedJson + '"position",';
            Except End;
            { BoundingRectangle is CACHED and is not invalidated by assigning
              XLocation; without this the next read reports the OLD box. }
            Try Desig.GraphicallyInvalidate; Except End;
        End;

        Try
            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                c_Broadcast, PCBM_EndModify, c_NoEventData);
        Except End;
    Finally
        PCBServer.PostProcess;
    End;

    { Trim the trailing comma. }
    If ChangedJson <> '' Then
        ChangedJson := Copy(ChangedJson, 1, Length(ChangedJson) - 1);

    Try Board.ViewManager_FullUpdate; Except End;

    { Flag the server doc dirty so application.save_all flushes it. The write
      is deferred: nothing reaches disk until the caller explicitly saves. }
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"footprint":"' + EscapeJsonString(FpWanted) + '"' +
        ',"created":' + BoolToJsonStr(Created) +
        ',"changed":[' + ChangedJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ SplitNextTab - Pop the leading tab-delimited field off S, shortening S.     }
Function SplitNextTab(Var S : String) : String;
Var P : Integer;
Begin
    P := Pos(#9, S);
    If P = 0 Then
    Begin
        Result := S;
        S := '';
    End
    Else
    Begin
        Result := Copy(S, 1, P - 1);
        S := Copy(S, P + 1, Length(S) - P);
    End;
End;

{ Lib_ConvertDesignatorsToStroke - Turn every TrueType .Designator in the      }
{ library into a stroke-font one, in one pass.                                 }
{                                                                              }
{ A TrueType PCB text will not persist a position change set through           }
{ XLocation: the assignment reads back changed, then Altium recomputes the     }
{ position from the TT layout on reload and the move reverts. Bold/Italic are  }
{ TrueType-ONLY attributes (a stroke text cannot be bold), so a bold or italic }
{ designator is the reliable TrueType tell -- more reliable than UseTTFonts,   }
{ which imported footprints leave inconsistent.                                }
{                                                                              }
{ Conversion clears UseTTFonts, Bold and Italic. Each change is bracketed with }
{ PCBM_BeginModify / PCBM_EndModify so it registers, and Bold is READ BACK so a}
{ refusal is counted, not assumed.                                             }
{                                                                              }
{ Params: library_path - optional .PcbLib to focus first.                      }
{ Response: converted count, and the names it converted (up to 50).            }
Function Lib_ConvertDesignatorsToStroke(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, TxtStr, RespJson, NamesJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    SrvDoc : IServerDocument;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    IsTT : Boolean;
    Converted, Total : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Board := PcbLib.Board;
    Converted := 0;
    Total := 0;
    NamesJson := '';

    PCBServer.PreProcess;
    Try
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                Desig := Nil;
                GrpIter := Footprint.GroupIterator_Create;
                Try
                    GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                    Txt := GrpIter.FirstPCBObject;
                    While Txt <> Nil Do
                    Begin
                        TxtStr := '';
                        Try TxtStr := Txt.UnderlyingString; Except End;
                        If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
                        If UpperCase(TxtStr) = '.DESIGNATOR' Then
                        Begin
                            Desig := Txt;
                            Break;
                        End;
                        Txt := GrpIter.NextPCBObject;
                    End;
                Finally
                    Footprint.GroupIterator_Destroy(GrpIter);
                End;

                If Desig <> Nil Then
                Begin
                    { Bold/Italic are TrueType-only. UseTTFonts is a weaker
                      signal but included. Treat any of them as TrueType. }
                    IsTT := False;
                    Try If Desig.Bold Then IsTT := True; Except End;
                    Try If Desig.Italic Then IsTT := True; Except End;
                    Try If Desig.UseTTFonts Then IsTT := True; Except End;

                    If IsTT Then
                    Begin
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Except End;
                        Try Desig.UseTTFonts := False; Except End;
                        Try Desig.Bold := False; Except End;
                        Try Desig.Italic := False; Except End;
                        { An inverted / inverted-rectangle text has its geometry
                          driven by the rectangle, so its effective position is
                          not the plain anchor -- clearing these makes it a plain
                          stroke text whose XLocation is authoritative. }
                        Try Desig.Inverted := False; Except End;
                        Try Desig.UseInvertedRectangle := False; Except End;
                        Try Desig.GraphicallyInvalidate; Except End;
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_EndModify, c_NoEventData);
                        Except End;

                        { Read back: the text is stroke now iff it is no longer
                          bold or italic. }
                        IsTT := False;
                        Try If Desig.Bold Then IsTT := True; Except End;
                        Try If Desig.Italic Then IsTT := True; Except End;
                        If Not IsTT Then
                        Begin
                            Inc(Converted);
                            If Converted <= 50 Then
                            Begin
                                If NamesJson <> '' Then NamesJson := NamesJson + ',';
                                NamesJson := NamesJson + '"' +
                                    EscapeJsonString(FpName) + '"';
                            End;
                        End;
                    End;
                    Inc(Total);
                End;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"designators":' + IntToStr(Total) +
        ',"converted":' + IntToStr(Converted) +
        ',"names":[' + NamesJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetDesignators - Apply designator edits to MANY footprints in ONE       }
{ library pass.                                                                }
{                                                                              }
{ Lib_SetDesignator handles a single footprint and re-scans the whole library }
{ to find it by name, so repairing M footprints of N costs O(M*N) iterator    }
{ steps -- hundreds of thousands on a big library. This walks the             }
{ LibraryIterator once and applies whatever edit matches each footprint.      }
{                                                                              }
{ The edit list arrives as a FILE, not as JSON: ExtractJsonValue is a flat    }
{ key reader and cannot express an array. One edit per line, tab separated:   }
{                                                                              }
{   name <TAB> layer_id <TAB> x <TAB> y <TAB> height <TAB> create             }
{                                                                              }
{ An empty field means "leave this property alone". create is 1 or 0. Blank   }
{ lines are skipped. x and y are native TCoord offsets from the footprint's   }
{ OWN origin; height is mils. A create against a footprint that already has a }
{ .Designator is refused, never duplicated.                                   }
{                                                                              }
{ Params:                                                                      }
{   edits_path   - required; path of the tab-separated edit file.             }
{   library_path - optional .PcbLib to focus first.                           }
{                                                                              }
{ Response: applied, created, failed counts plus the names of footprints in   }
{ the edit file that the library does not contain.                            }
Function Lib_SetDesignators(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, EditsPath, FpName, TxtStr, RespJson, MissingJson : String;
    Line, WantName, LayerStr, XStr, YStr, HeightStr, CreateStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    SrvDoc : IServerDocument;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    Edits : TStringList;
    DoneKeys : String;
    Lyr : TLayer;
    XOrg, YOrg, TgtX, TgtY, CurCX, CurCY : TCoord;
    TRect : TCoordRect;
    ImmovableJson : String;
    I, NewX, NewY, NewH, LayerId, WidthMils : Integer;
    Applied, CreatedCount, FailedCount, MissingCount, RefusedCount : Integer;
    ImmovableCount : Integer;
    DoCreate, MovedOk : Boolean;
Begin
    EditsPath := ExtractJsonValue(Params, 'edits_path');
    If EditsPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'edits_path is required');
        Exit;
    End;
    If Not FileExists(EditsPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_EDITS',
            'Edit file not found: ' + EditsPath);
        Exit;
    End;
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Board := PcbLib.Board;

    Applied := 0;
    CreatedCount := 0;
    FailedCount := 0;
    MissingCount := 0;
    RefusedCount := 0;
    ImmovableCount := 0;
    MissingJson := '';
    ImmovableJson := '';

    DoneKeys := '';
    Edits := TStringList.Create;
    Try
        Edits.LoadFromFile(EditsPath);

        PCBServer.PreProcess;
        Try
            Iter := PcbLib.LibraryIterator_Create;
            Try
                Footprint := Iter.FirstPCBObject;
                While Footprint <> Nil Do
                Begin
                    FpName := '';
                    Try FpName := Footprint.Name; Except End;

                    { Per-footprint origin: the frame the geometry dump used. }
                    XOrg := 0;  YOrg := 0;
                    Try XOrg := Footprint.X; Except End;
                    Try YOrg := Footprint.Y; Except End;

                    { Does any edit line name this footprint? }
                    For I := 0 To Edits.Count - 1 Do
                    Begin
                        Line := Edits[I];
                        If Line = '' Then Continue;
                        WantName := SplitNextTab(Line);
                        If UpperCase(WantName) <> UpperCase(FpName) Then Continue;

                        DoneKeys := DoneKeys + '|' + UpperCase(WantName) + '|';
                        LayerStr := SplitNextTab(Line);
                        XStr := SplitNextTab(Line);
                        YStr := SplitNextTab(Line);
                        HeightStr := SplitNextTab(Line);
                        CreateStr := SplitNextTab(Line);
                        DoCreate := (CreateStr = '1');
                        LayerId := StrToIntDef(LayerStr, -1);
                        NewX := StrToIntDef(XStr, 0);
                        NewY := StrToIntDef(YStr, 0);
                        NewH := StrToIntDef(HeightStr, 0);

                        { Existing .Designator, if any. Match on UnderlyingString:
                          .Text is the RENDERED special string and misses real
                          designators, which is how a create became a DUPLICATE. }
                        Desig := Nil;
                        GrpIter := Footprint.GroupIterator_Create;
                        Try
                            GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                            Txt := GrpIter.FirstPCBObject;
                            While Txt <> Nil Do
                            Begin
                                TxtStr := '';
                                Try TxtStr := Txt.UnderlyingString; Except End;
                                If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
                                If UpperCase(TxtStr) = '.DESIGNATOR' Then
                                Begin
                                    Desig := Txt;
                                    Break;
                                End;
                                Txt := GrpIter.NextPCBObject;
                            End;
                        Finally
                            Footprint.GroupIterator_Destroy(GrpIter);
                        End;

                        { Belt and braces: a create request against a footprint
                          that already has a designator is refused outright, so
                          a detection miss can never duplicate the string. }
                        If DoCreate And (Desig <> Nil) Then
                        Begin
                            Inc(RefusedCount);
                            Break;
                        End;

                        If (Desig = Nil) And (Not DoCreate) Then
                        Begin
                            Inc(FailedCount);
                            Break;
                        End;

                        If Desig = Nil Then
                        Begin
                            If (LayerId < 0) Or (XStr = '') Or (YStr = '') Or (NewH <= 0) Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            { A PcbLib shares ONE board across every footprint,
                              and Board.AddPCBObject attaches the primitive to
                              the library's CURRENT component -- not to the
                              IPCB_LibComponent handed to Footprint.AddPCBObject.
                              Without making the target current first, every
                              create in a sweep piles onto one footprint. }
                            Try PcbLib.CurrentComponent := Footprint; Except End;
                            If PcbLib.CurrentComponent <> Footprint Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            Desig := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                            If Desig = Nil Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            Desig.UnderlyingString := '.Designator';
                            WidthMils := NewH Div 5;
                            If WidthMils < 1 Then WidthMils := 1;
                            Desig.Width := MilsToCoord(WidthMils);
                            Footprint.AddPCBObject(Desig);
                            Board.AddPCBObject(Desig);
                            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
                            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
                            Inc(CreatedCount);
                        End;

                        { Bracket the mutation so the server registers it. }
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Except End;

                        If LayerId >= 0 Then
                        Begin
                            Lyr := LayerId;
                            Try Desig.Layer := Lyr; Except End;
                        End;
                        { Size FIRST: resizing the text changes its bounding box,
                          so centring must be computed against the final size. }
                        If NewH > 0 Then
                            Try Desig.Size := MilsToCoord(NewH); Except End;
                        MovedOk := True;
                        If (XStr <> '') And (YStr <> '') Then
                        Begin
                            { Re-read the origin HERE: making a component current
                              (the create path does) relocates it, so an origin
                              captured earlier in the loop is stale and the text
                              lands at the board origin instead of on the part. }
                            Try XOrg := Footprint.X; Except End;
                            Try YOrg := Footprint.Y; Except End;
                            { x/y are the ANCHOR (XLocation) the caller wants, in
                              TCoord relative to the footprint origin. The caller
                              derived it from the anchor and bbox centre this
                              script reported, so no bounding box is read here --
                              a write-time bbox read proved unreliable. }
                            Try
                                Desig.XLocation := XOrg + NewX;
                                Desig.YLocation := YOrg + NewY;
                            Except End;
                            { READ BACK. Some designator texts silently refuse to
                              move (the assignment neither takes nor raises), and
                              reporting those as applied hides a failed repair
                              behind a success count. }
                            MovedOk := False;
                            Try
                                MovedOk := (Desig.XLocation = XOrg + NewX) And
                                           (Desig.YLocation = YOrg + NewY);
                            Except End;
                            If Not MovedOk Then
                            Begin
                                Inc(ImmovableCount);
                                If ImmovableCount <= 25 Then
                                Begin
                                    If ImmovableJson <> '' Then
                                        ImmovableJson := ImmovableJson + ',';
                                    ImmovableJson := ImmovableJson + '"' +
                                        EscapeJsonString(FpName) + '"';
                                End;
                            End;
                            { BoundingRectangle is CACHED and is NOT invalidated
                              by assigning XLocation. Without this the next read
                              returns the box from the text's OLD position, so a
                              caller centring on the bbox diverges every pass. }
                            Try Desig.GraphicallyInvalidate; Except End;
                        End;

                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_EndModify, c_NoEventData);
                        Except End;

                        If MovedOk Then Inc(Applied) Else Inc(FailedCount);
                        Break;
                    End;
                    Footprint := Iter.NextPCBObject;
                End;
            Finally
                PcbLib.LibraryIterator_Destroy(Iter);
            End;
        Finally
            PCBServer.PostProcess;
        End;

        { Edit lines naming a footprint this library does not contain. }
        For I := 0 To Edits.Count - 1 Do
        Begin
            Line := Edits[I];
            If Line = '' Then Continue;
            WantName := SplitNextTab(Line);
            If Pos('|' + UpperCase(WantName) + '|', DoneKeys) > 0 Then Continue;
            If MissingCount < 25 Then
            Begin
                If MissingJson <> '' Then MissingJson := MissingJson + ',';
                MissingJson := MissingJson + '"' + EscapeJsonString(WantName) + '"';
            End;
            Inc(MissingCount);
        End;
    Finally
        Edits.Free;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"applied":' + IntToStr(Applied) +
        ',"created":' + IntToStr(CreatedCount) +
        ',"failed":' + IntToStr(FailedCount) +
        ',"refused":' + IntToStr(RefusedCount) +
        ',"immovable_count":' + IntToStr(ImmovableCount) +
        ',"immovable":[' + ImmovableJson + ']' +
        ',"missing_count":' + IntToStr(MissingCount) +
        ',"missing":[' + MissingJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ReloadLibrary - Close and reopen a PcbLib so its in-memory caches are    }
{ rebuilt from disk.                                                           }
{                                                                              }
{ IPCB_Text.BoundingRectangle is populated when the document loads and is NOT  }
{ refreshed by assigning XLocation or Size (GraphicallyInvalidate does not do  }
{ it either). After a write, every text's rectangle in that session reports    }
{ the text's OLD extent, so anything that centres on the bounding box computes }
{ its correction from stale geometry and moves the text by a wrong delta.      }
{                                                                              }
{ Closing the document drops those caches; the next OpenObject re-reads from   }
{ disk. Caller MUST save first -- this does not save, and a dirty close would  }
{ either lose the edits or raise a prompt.                                     }
{                                                                              }
{ Params: library_path (required).                                             }
Function Lib_ReloadLibrary(Params : String; RequestId : String) : String;
Var
    LibPath : String;
    PcbLib : IPCB_Library;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'library_path is required');
        Exit;
    End;
    If Not FileExists(LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'Library not found on disk: ' + LibPath);
        Exit;
    End;

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    Try RunProcess('WorkspaceManager:CloseObject'); Except End;
    Try Application.ProcessMessages; Except End;

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    Try RunProcess('WorkspaceManager:OpenObject'); Except End;
    Try Application.ProcessMessages; Except End;

    { PcbLibMayBeCurrentAt, not PcbLibMayBeCurrent: the close and reopen    }
    { above are programmatic, and the DM focus can stay on whatever was     }
    { focused BEFORE the call (stale, not Nil). The library reopened at     }
    { LibPath is what proves the server loaded (RELEASE_VERIFICATION P32).  }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Library did not reopen: ' + LibPath);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"reloaded":true,"library_path":"' + EscapeJsonString(LibPath) + '"}');
End;

{ Lib_ProbeDesignator - Diagnostic. Dump the RAW geometry of one footprint's   }
{ .Designator and of the footprint itself, so the caller can see what          }
{ BoundingRectangle actually measures. Read-only.                              }
{                                                                              }
{ Params: footprint_name (required), library_path (optional).                  }
Function Lib_ProbeDesignator(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, TxtStr, RespJson, TextsJson : String;
    TextN : Integer;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    Pad : IPCB_Pad;
    TRect, FRect : TCoordRect;
    PadMinX, PadMinY, PadMaxX, PadMaxY : TCoord;
    PadN : Integer;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PcbLib');
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'Not found: ' + FpWanted);
        Exit;
    End;

    { Pad extents, as an independent yardstick for "how big is this part". }
    PadN := 0;
    PadMinX := 0;  PadMinY := 0;  PadMaxX := 0;  PadMaxY := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            If PadN = 0 Then
            Begin
                PadMinX := Pad.X;  PadMaxX := Pad.X;
                PadMinY := Pad.Y;  PadMaxY := Pad.Y;
            End
            Else
            Begin
                If Pad.X < PadMinX Then PadMinX := Pad.X;
                If Pad.X > PadMaxX Then PadMaxX := Pad.X;
                If Pad.Y < PadMinY Then PadMinY := Pad.Y;
                If Pad.Y > PadMaxY Then PadMaxY := Pad.Y;
            End;
            Inc(PadN);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;

    Desig := Nil;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If UpperCase(TxtStr) = '.DESIGNATOR' Then
            Begin
                Desig := Txt;
                Break;
            End;
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    If Desig = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DESIGNATOR', 'No .Designator');
        Exit;
    End;

    { Assign the whole record; writing individual fields of an uninitialised
      local raises in DelphiScript. }
    TRect := Desig.BoundingRectangle;
    FRect := Target.BoundingRectangle;

    { Every text in the footprint, with the properties that could plausibly make
      one refuse to persist a move: layer, rotation, mirror, lock/selection, and
      whether it is the component's own designator object rather than a free
      text. Dump them rather than theorise about them. }
    TextsJson := '[';
    TextN := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If TextN > 0 Then TextsJson := TextsJson + ',';
            TextsJson := TextsJson +
                '{"underlying":"' + EscapeJsonString(TxtStr) + '"';
            Try TextsJson := TextsJson +
                ',"text":"' + EscapeJsonString(Txt.Text) + '"'; Except End;
            Try TextsJson := TextsJson +
                ',"layer_id":' + IntToStr(Txt.Layer); Except End;
            Try TextsJson := TextsJson +
                ',"x":' + IntToStr(Txt.XLocation) +
                ',"y":' + IntToStr(Txt.YLocation); Except End;
            Try TextsJson := TextsJson +
                ',"rotation":' + FloatToJsonStr(Txt.Rotation); Except End;
            Try TextsJson := TextsJson +
                ',"mirror":' + BoolToJsonStr(Txt.MirrorFlag); Except End;
            Try TextsJson := TextsJson +
                ',"selected":' + BoolToJsonStr(Txt.Selected); Except End;
            Try TextsJson := TextsJson +
                ',"moveable":' + BoolToJsonStr(Txt.Moveable); Except End;
            Try TextsJson := TextsJson +
                ',"is_designator":' + BoolToJsonStr(Txt.IsDesignator); Except End;
            Try TextsJson := TextsJson +
                ',"use_ttfonts":' + BoolToJsonStr(Txt.UseTTFonts); Except End;
            { The REAL font-type discriminator: TextKind is the enum
              (0=stroke, 1=TrueType, 2=BarCode), UseTTFonts is only a legacy
              flag. Read the raw ordinal plus the TT font details so a TrueType
              designator is unambiguous. }
            Try TextsJson := TextsJson +
                ',"text_kind":' + IntToStr(Txt.TextKind); Except End;
            Try TextsJson := TextsJson +
                ',"font_name":"' + EscapeJsonString(Txt.FontName) + '"'; Except End;
            Try TextsJson := TextsJson +
                ',"bold":' + BoolToJsonStr(Txt.Bold); Except End;
            Try TextsJson := TextsJson +
                ',"italic":' + BoolToJsonStr(Txt.Italic); Except End;
            Try TextsJson := TextsJson +
                ',"inverted":' + BoolToJsonStr(Txt.Inverted); Except End;
            { The full font/kind property set (from AssemblyTextPrep's
              CopyTextFormatFromTo). One of these -- not Bold -- is what makes
              the position revert; probe both a working and a failing designator
              to see which differs. }
            Try TextsJson := TextsJson +
                ',"font_id":' + IntToStr(Txt.FontID); Except End;
            Try TextsJson := TextsJson +
                ',"use_inv_rect":' + BoolToJsonStr(Txt.UseInvertedRectangle); Except End;
            Try TextsJson := TextsJson +
                ',"ttf_w":' + IntToStr(Txt.TTFTextWidth); Except End;
            Try TextsJson := TextsJson +
                ',"ttf_h":' + IntToStr(Txt.TTFTextHeight); Except End;
            Try TextsJson := TextsJson +
                ',"inv_rect_w":' + IntToStr(Txt.InvRectWidth); Except End;
            Try TextsJson := TextsJson +
                ',"inv_rect_h":' + IntToStr(Txt.InvRectHeight); Except End;
            TextsJson := TextsJson + '}';
            Inc(TextN);
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    TextsJson := TextsJson + ']';

    RespJson :=
        '{"footprint":"' + EscapeJsonString(FpWanted) + '"' +
        ',"texts":' + TextsJson +
        ',"fp_x":' + IntToStr(Target.X) +
        ',"fp_y":' + IntToStr(Target.Y) +
        ',"fp_rect":[' + IntToStr(FRect.X1) + ',' + IntToStr(FRect.Y1) + ',' +
                         IntToStr(FRect.X2) + ',' + IntToStr(FRect.Y2) + ']' +
        ',"pad_count":' + IntToStr(PadN) +
        ',"pad_rect":[' + IntToStr(PadMinX) + ',' + IntToStr(PadMinY) + ',' +
                          IntToStr(PadMaxX) + ',' + IntToStr(PadMaxY) + ']' +
        ',"desig_anchor":[' + IntToStr(Desig.XLocation) + ',' +
                              IntToStr(Desig.YLocation) + ']' +
        ',"desig_rect":[' + IntToStr(TRect.X1) + ',' + IntToStr(TRect.Y1) + ',' +
                            IntToStr(TRect.X2) + ',' + IntToStr(TRect.Y2) + ']' +
        ',"desig_size":' + IntToStr(Desig.Size) +
        ',"desig_width":' + IntToStr(Desig.Width) +
        ',"desig_text":"' + EscapeJsonString(Desig.Text) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

Function Lib_LinkFootprint(Params : String; RequestId : String) : String;
Var
    FootprintName, ComponentName, ReplaceStr, SetCurStr, MT, RespJson : String;
    Replace, MakeCurrent, OtherCurrent, IsCur, NewIsCurrent : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Impl, Impl2, Found : ISch_Implementation;
    ImplIter : ISch_Iterator;
    Guard, CurCount, Demoted : Integer;
Begin
    FootprintName := ExtractJsonValue(Params, 'footprint_name');
    ComponentName := ExtractJsonValue(Params, 'component_name');
    ReplaceStr := ExtractJsonValue(Params, 'replace');
    Replace := (ReplaceStr = '') Or (ReplaceStr = 'true') Or (ReplaceStr = 'True') Or (ReplaceStr = '1');
    { '' or 'auto': current only when no other footprint model is. 'true':
      take over, demoting the others. 'false': add it non-current. }
    SetCurStr := LowerCase(Trim(ExtractJsonValue(Params, 'set_current')));
    If SetCurStr = '' Then SetCurStr := 'auto';
    If (SetCurStr <> 'auto') And (SetCurStr <> 'true') And (SetCurStr <> 'false') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'set_current must be true, false or auto, got: ' + SetCurStr);
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    { Resolve the target by component_name so the link lands on the intended
      symbol -- the handler previously ignored it and always used the
      last-created component. Fall back to last-created/selected when empty. }
    If ComponentName <> '' Then
        Component := SelectLibComponent(ComponentName)
    Else
        Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'Target component not found (component_name) or nothing selected');
        Exit;
    End;

    { Attach the footprint model via Component.AddSchImplementation -- the
      dedicated factory that creates, owns AND registers the implementation in
      one call. The old SchObjectFactory(eImplementation) + Component.AddSchObject
      path is WRONG for models: ISch_Implementation is not an ISch_GraphicalObject,
      so on AD26 both SetOwnerPart (writing OwnerPartId) and AddSchObject raise a
      modal "Undeclared identifier" that Try/Except cannot catch and WEDGE the
      bridge. }
    SchServer.ProcessControl.PreProcess(SchLib, '');

    { replace=true (default): drop existing footprint (PCBLIB) implementations   }
    { first so re-linking replaces instead of appending a duplicate (the append  }
    { behaviour is what bloated components with duplicate models).                }
    If Replace Then
    Begin
        Guard := 1000;
        While Guard > 0 Do
        Begin
            Found := Nil;
            ImplIter := Component.SchIterator_Create;
            Try
                ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                Impl2 := ImplIter.FirstSchObject;
                While Impl2 <> Nil Do
                Begin
                    MT := '';
                    Try MT := Impl2.ModelType; Except End;
                    If MT = cDocKind_PcbLib Then Begin Found := Impl2; Break; End;
                    Impl2 := ImplIter.NextSchObject;
                End;
            Finally
                Component.SchIterator_Destroy(ImplIter);
            End;
            If Found = Nil Then Break;
            Try Component.RemoveSchImplementation(Found); Except End;
            Dec(Guard);
        End;
    End;

    { WHICH FOOTPRINT IS CURRENT IS A CHOICE, NOT A SIDE EFFECT.
      MEASURED (owner, 2026-10-04): replace=false added a second footprint
      model and left BOTH flagged current, because this handler set the
      new one current and Altium does not clear the others by itself. So
      first find out whether another footprint model already is. }
    OtherCurrent := False;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl2 := ImplIter.FirstSchObject;
        While Impl2 <> Nil Do
        Begin
            MT := '';
            Try MT := Impl2.ModelType; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                IsCur := False;
                Try IsCur := Impl2.IsCurrent; Except End;
                If IsCur Then OtherCurrent := True;
            End;
            Impl2 := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;

    If SetCurStr = 'true' Then MakeCurrent := True
    Else If SetCurStr = 'false' Then MakeCurrent := False
    Else MakeCurrent := Not OtherCurrent;

    { Taking over: demote the others BEFORE the add, so nothing below has
      to tell the new implementation apart from the old ones. }
    Demoted := 0;
    If MakeCurrent And OtherCurrent Then
    Begin
        ImplIter := Component.SchIterator_Create;
        Try
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Impl2 := ImplIter.FirstSchObject;
            While Impl2 <> Nil Do
            Begin
                MT := '';
                Try MT := Impl2.ModelType; Except End;
                If UpperCase(MT) = 'PCBLIB' Then
                Begin
                    IsCur := False;
                    Try IsCur := Impl2.IsCurrent; Except End;
                    If IsCur Then
                    Begin
                        SchBeginModify(Impl2);
                        Try Impl2.IsCurrent := False; Except End;
                        SchEndModify(Impl2);
                        Inc(Demoted);
                    End;
                End;
                Impl2 := ImplIter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(ImplIter);
        End;
    End;

    Impl := Component.AddSchImplementation;
    If Impl <> Nil Then
    Begin
        Try Impl.ClearAllDatafileLinks; Except End;
        Impl.ModelName := FootprintName;
        Impl.ModelType := cDocKind_PcbLib;
        Try Impl.IsCurrent := MakeCurrent; Except End;
        { A footprint implementation MUST carry a datafile link (entity name =   }
        { the footprint) or the compiler reports "Missing Component Models".     }
        { Bind by name: entity = footprint, EMPTY location. Never pass a full    }
        { .PcbLib path as the location -- a path blocks/wedges AD26; empty is    }
        { safe and is what a self-contained package wants.                       }
        Try Impl.AddDataFileLink(FootprintName, '', 'PCBLib'); Except End;
    End;

    SchServer.ProcessControl.PostProcess(SchLib, 'Link footprint');

    If Impl = Nil Then
    Begin
        MarkLibDirty(SchLib);
        Result := BuildErrorResponse(RequestId, 'LINK_FAILED', 'Failed to link footprint');
        Exit;
    End;

    { Read back: what is current now, as Altium holds it. }
    NewIsCurrent := False;
    Try NewIsCurrent := Impl.IsCurrent; Except End;
    CurCount := 0;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl2 := ImplIter.FirstSchObject;
        While Impl2 <> Nil Do
        Begin
            MT := '';
            Try MT := Impl2.ModelType; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                IsCur := False;
                Try IsCur := Impl2.IsCurrent; Except End;
                If IsCur Then Inc(CurCount);
            End;
            Impl2 := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"footprint":"' + EscapeJsonString(FootprintName) + '"'
        + ',"replaced":' + BoolToJsonStr(Replace)
        + ',"set_current":"' + SetCurStr + '"'
        + ',"is_current":' + BoolToJsonStr(NewIsCurrent)
        + ',"demoted":' + IntToStr(Demoted)
        + ',"current_footprint_count":' + IntToStr(CurCount)
        + ',"models":' + BuildImplementationsJson(Component) + '}';
    If (NewIsCurrent <> MakeCurrent) Or (CurCount > 1) Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'CURRENT_NOT_APPLIED',
            'The footprint was linked, but the current-model flags did not '
            + 'land as asked: is_current reads ' + BoolToJsonStr(NewIsCurrent)
            + ' and ' + IntToStr(CurCount) + ' footprint models are current. '
            + 'lib_set_current_model chooses one explicitly.', RespJson);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ 3D BODIES: helpers shared by Lib_Link3DModel, Lib_GetFootprintBodies and    }
{ PCB_Place3DBody. PCB.pas builds after this file, so it can call them; they  }
{ live here because the footprint handlers below need them first.             }
{                                                                              }
{ WHAT IS PROVEN AND WHAT IS NOT.                                             }
{                                                                              }
{ Every IPCB_ComponentBody member used here is already used by shipped code:  }
{ the factory, ModelFactory_FromFilename, SetState_FromModel, the Model       }
{ write, AddPCBObject, MoveByXY, StandoffHeight, OverallHeight, Layer and     }
{ BoundingRectangle (LmComponentsSection reads the last four on a body).      }
{ Body.Model is READ here for the first time. The script binding declares it  }
{ Read GetModel Write SetModel, and the write is what Lib_Link3DModel has     }
{ always done, so the identifier exists; only the read is new.                }
{                                                                              }
{ The IPCB_Model members are NOT exercised by any shipped code: SetState,     }
{ GetState, ModelType, FileName and Rotation. They are attested three ways,   }
{ read 2026-10-04 from the installed AD26 (26.10.1.5):                        }
{   * ScriptingSystem.dll carries IPCB_Model's script method list,            }
{     GetFileName, GetState, SetState, GetEmbed, SetEmbed, ..., with the      }
{     signature RotX, RotY, RotZ : Double and Dz : Integer for both, every    }
{     GetState parameter flagged OUT;                                         }
{   * its property table lists FileName (GetFileName, read only), ModelType   }
{     (T3DModelType), Origin (TCoordPoint) and Rotation (TAngle);             }
{   * Altium.SDK.Interfaces.dll declares the same IPCB_Model member for       }
{     member, GetState(ref RotX, ref RotY, ref RotZ, ref Dz).                 }
{ The PcbLib file stores exactly these fields per body as MODEL.3D.ROTX,      }
{ ROTY, ROTZ and DZ, beside MODEL.2D.X, 2D.Y and 2D.ROTATION (Origin and      }
{ Rotation), and MODEL.MODELTYPE 0 = extruded, 2 = cylinder in a real IPC     }
{ generated library, matching the enum order Extruded, Generic, Cylinder,     }
{ Sphere.                                                                     }
{                                                                              }
{ That makes an undeclared identifier unlikely, not impossible, and it cannot }
{ be caught. So each unproven member sits in a function of its own and is     }
{ reached only when the caller asked for what it gives: a plain link with no  }
{ rotation, and a body list without model_state=true, touch none of them. A   }
{ fault in one would stop the polling loop, not just the call, so every one   }
{ of these reads stays OPT-IN until RELEASE_VERIFICATION step 5 has passed on }
{ a live session.                                                              }
{                                                                              }
{ MEASURED LIVE 2026-10-04 (AD26, bridge 2026.10.04.9, carrier.PcbLib,        }
{ footprint ZZ_BRIDGE_TEST, rotation_x 90 / rotation_z 45):                   }
{   * SetState RAN and PERSISTED: the saved ComponentBody record reads        }
{     MODEL.3D.ROTX=90.000 MODEL.3D.ROTZ=45.000, and the body's box came back }
{     as the 92.7 mil square the turned 2.5 x 2.0 mm crystal should give.     }
{     SetState is a proven member now (Body3D_ApplyModelState).               }
{   * GetState RAN without faulting and RETURNED ZEROS: model_state read      }
{     (x 0, y 0, z 0, dz_mils 0) beside a file holding 90 and 45, both in the  }
{     link reply and in lib_get_footprint_bodies(model_state=true). An OUT     }
{     parameter of an Altium method does not come back through a DelphiScript }
{     local. It is no longer called: a read that cannot return a value is     }
{     reported as UNAVAILABLE (Body3D_RotationReadbackJson), never as a       }
{     false mismatch, and the saved file is the proof (lib_read_pcblib_file). }
{   * ModelType / FileName / Rotation ran under model_state=true without a    }
{     fault (the reply carried them).                                         }
{   * Where the model LANDS: with no move, the STEP's own origin sits at      }
{     ABSOLUTE (0, 0) on the library board, not at the footprint origin. The  }
{     footprint origin read (50000, 50000) mils, and after a move of (10.5,   }
{     -20) the saved record held MODEL.2D.X=-49989.5mil MODEL.2D.Y=-50020mil, }
{     while every other body in that library (126 generator bodies) stores    }
{     MODEL.2D.X/Y=0mil: the file is relative to the footprint origin, and    }
{     the model had been put 50,000 mil away from its footprint.              }
{   * Z: a generic (STEP) body is placed in Z by MODEL.3D.DZ, the Dz argument }
{     of SetState. Writing StandoffHeight stored STANDOFFHEIGHT=3mil beside   }
{     DZ=0 and an OVERALLHEIGHT that was exactly the turned model's own top,  }
{     i.e. the model was not lifted. Altium-authored libraries on this        }
{     machine agree: the AD26 example MiniPC.PcbLib stores generic bodies     }
{     with MODEL.3D.DZ=787.4016mil / STANDOFFHEIGHT=-184.0551mil and          }
{     DZ=39.3701mil / STANDOFFHEIGHT=-40.3543mil, i.e. DZ is the placement    }
{     and the two heights are the extent Altium DERIVES from the model.       }
{                                                                              }
{ Body.x and Body.y are deliberately NOT used. Neither IPCB_ComponentBody nor }
{ its ancestors IPCB_Region and IPCB_Primitive declare a position in the AD26 }
{ SDK. Position comes from BoundingRectangle, which IPCB_Primitive declares.  }
{..............................................................................}

{ Four corners as x1, y1, x2, y2 plus width and height, in mils measured from }
{ (OrgX, OrgY). Plain coordinates rather than a TCoordRect parameter, because }
{ nothing here passes a record to a script function and this is not the place }
{ to find out whether it works.                                               }
Function Body3D_RectJson(X1 : TCoord; Y1 : TCoord; X2 : TCoord; Y2 : TCoord;
                         OrgX : TCoord; OrgY : TCoord) : String;
Begin
    Result := JsonObj(
        JsonFloat('x1', CoordToMilsF(X1 - OrgX)) + ',' +
        JsonFloat('y1', CoordToMilsF(Y1 - OrgY)) + ',' +
        JsonFloat('x2', CoordToMilsF(X2 - OrgX)) + ',' +
        JsonFloat('y2', CoordToMilsF(Y2 - OrgY)) + ',' +
        JsonFloat('width', CoordToMilsF(X2 - X1)) + ',' +
        JsonFloat('height', CoordToMilsF(Y2 - Y1)));
End;

{ A body's bounding rectangle in board coordinates. Ok is false when the read }
{ raised or the rectangle is empty, which is what a body with no geometry     }
{ reports, and an empty box is no basis for aligning anything.                }
{                                                                              }
{ The whole record is assigned before any field is read: reading or writing a }
{ field of a never-assigned TCoordRect local is an undeclared-identifier      }
{ fault (see PCB_CreateRoom).                                                 }
Procedure Body3D_ReadRect(Body : IPCB_ComponentBody; Var X1 : TCoord;
                          Var Y1 : TCoord; Var X2 : TCoord; Var Y2 : TCoord;
                          Var Ok : Boolean);
Var
    BR : TCoordRect;
Begin
    Ok := False;
    X1 := 0;
    Y1 := 0;
    X2 := 0;
    Y2 := 0;
    If Body = Nil Then Exit;
    Try
        BR := Body.BoundingRectangle;
        X1 := BR.X1;
        Y1 := BR.Y1;
        X2 := BR.X2;
        Y2 := BR.Y2;
        Ok := (X2 > X1) Or (Y2 > Y1);
    Except
        Ok := False;
    End;
End;

{ The extent of every pad on a footprint, in board coordinates. This is the   }
{ cross-check for the frame: a body box and a pad box reported in the same    }
{ frame show at once whether the model sits on its land, without opening the  }
{ 3D view. Duplicate yields of one pad do not change a min/max, so no dedupe. }
Procedure Body3D_PadsRect(Footprint : IPCB_LibComponent; Var X1 : TCoord;
                          Var Y1 : TCoord; Var X2 : TCoord; Var Y2 : TCoord;
                          Var Count : Integer);
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    BR : TCoordRect;
Begin
    Count := 0;
    X1 := 0;
    Y1 := 0;
    X2 := 0;
    Y2 := 0;
    If Footprint = Nil Then Exit;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Try
                BR := Prim.BoundingRectangle;
                If Count = 0 Then
                Begin
                    X1 := BR.X1;
                    Y1 := BR.Y1;
                    X2 := BR.X2;
                    Y2 := BR.Y2;
                End
                Else
                Begin
                    If BR.X1 < X1 Then X1 := BR.X1;
                    If BR.Y1 < Y1 Then Y1 := BR.Y1;
                    If BR.X2 > X2 Then X2 := BR.X2;
                    If BR.Y2 > Y2 Then Y2 := BR.Y2;
                End;
                Count := Count + 1;
            Except End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
End;

{ Distinct 3D bodies on a footprint. Deduplicated by object address: a        }
{ primitive added this session is registered with the footprint AND the       }
{ library board, and until a reload the group iterator yields it twice (the   }
{ measured pad_count 6 for three pads, see Lib_GetFootprintPads).             }
Function Body3D_CountBodies(Footprint : IPCB_LibComponent) : Integer;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Seen : TStringList;
    Addr : String;
Begin
    Result := 0;
    If Footprint = Nil Then Exit;
    Seen := TStringList.Create;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Addr := '';
            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
            If (Addr = '') Or (Seen.IndexOf(Addr) < 0) Then
            Begin
                If Addr <> '' Then Seen.Add(Addr);
                Result := Result + 1;
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
        Try Seen.Free; Except End;
    End;
End;

{ The layer of the first 3D body on a footprint, or eNoLayer when it has none. }
{ replace=true puts the new model where the placeholder was: the IPC          }
{ generator draws its bodies on a mechanical layer of its choosing, and a      }
{ model left on the factory default would not follow the footprint's layer    }
{ pairing when the part is flipped to the bottom.                             }
Function Body3D_FirstBodyLayer(Footprint : IPCB_LibComponent) : TLayer;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
Begin
    Result := eNoLayer;
    If Footprint = Nil Then Exit;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
        Prim := GrpIter.FirstPCBObject;
        If Prim <> Nil Then
        Begin
            Try Result := Prim.Layer; Except Result := eNoLayer; End;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
End;

{ Take every 3D body off one footprint: the IPC generator's extruded          }
{ placeholders, or a previous STEP that is being replaced.                    }
{                                                                              }
{ THE MEASURED SHAPE OF Lib_DeleteFootprintPrimitives, NOT AN IMPROVEMENT ON  }
{ IT. One match per pass from a fresh iterator; the match is removed while    }
{ that iterator is still open, and NextPCBObject is never called on it again. }
{ Collecting into a TInterfaceList first faulted the engine, and destroying   }
{ the iterator before removing left the primitive dangling and took the       }
{ scripting system down. Both are recorded there.                             }
{                                                                              }
{ The caller owns PreProcess and PostProcess. This only brackets the footprint }
{ with BeginModify and EndModify, as the delete handler does.                 }
{                                                                              }
{ A body added this session can be listed twice (see Body3D_CountBodies), and }
{ each listing needs its own removal, so the same address may be removed     }
{ twice in a row. A third sighting means RemovePCBObject is silently refusing }
{ it, and the loop stops there rather than spinning to its bound.             }
{                                                                              }
{ Returns the number of RemovePCBObject calls that returned; Distinct is the  }
{ number of different bodies they removed.                                    }
{                                                                              }
{ FOOTPRINT ONLY, under the shared rule (REMOVING PRIMITIVES FROM A PCBLIB     }
{ FOOTPRINT, beside RemoveLibTextByAddress). Entries records each removed     }
{ body for the opt-in board half, which Lib_Link3DModel runs only when asked. }
Function Body3D_RemoveAll(Footprint : IPCB_LibComponent;
                          Var Distinct : Integer; Var Entries : String) : Integer;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Seen : TStringList;
    Addr, LastAddr : String;
    Found : Boolean;
    I, SameRun, Oid : Integer;
Begin
    Result := 0;
    Distinct := 0;
    If Footprint = Nil Then Exit;
    Seen := TStringList.Create;
    LastAddr := '';
    SameRun := 0;
    Try
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BeginModify, c_NoEventData);
        For I := 1 To 200 Do
        Begin
            Found := False;
            GrpIter := Footprint.GroupIterator_Create;
            Try
                GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                Prim := GrpIter.FirstPCBObject;
                If Prim <> Nil Then
                Begin
                    Addr := '';
                    Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                    If Addr = LastAddr Then SameRun := SameRun + 1
                    Else SameRun := 1;
                    LastAddr := Addr;
                    If SameRun <= 2 Then
                    Begin
                        Oid := -1;
                        Try Oid := Prim.ObjectId; Except Oid := -1; End;
                        Try
                            Footprint.RemovePCBObject(Prim);
                            Result := Result + 1;
                            Found := True;
                            If (Addr <> '') And (Seen.IndexOf(Addr) < 0) Then
                            Begin
                                Seen.Add(Addr);
                                Entries := LibAddRemovalEntry(Entries, Oid, Addr);
                            End;
                        Except
                            Found := False;
                        End;
                    End;
                End;
            Finally
                Footprint.GroupIterator_Destroy(GrpIter);
            End;
            If Not Found Then Break;
        End;
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_EndModify, c_NoEventData);
    Finally
        Distinct := Seen.Count;
        Try Seen.Free; Except End;
    End;
End;

{ Every distinct 3D body address on a footprint, as '|addr|addr|': the read-  }
{ back that tells the board half which removed bodies the footprint still    }
{ lists. Body3D_CountBodies's walk, keeping the addresses.                   }
Function Body3D_BodyAddresses(Footprint : IPCB_LibComponent) : String;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Addr : String;
Begin
    Result := '|';
    If Footprint = Nil Then Exit;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Addr := '';
            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
            If (Addr <> '') And (Pos('|' + Addr + '|', Result) = 0) Then
                Result := Result + Addr + '|';
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
End;

{ The model behind a body. The first READ of Body.Model in this codebase; the }
{ identifier is the one every placement here already writes.                  }
Function Body3D_GetModel(Body : IPCB_ComponentBody) : IPCB_Model;
Begin
    Result := Nil;
    If Body = Nil Then Exit;
    Try
        Result := Body.Model;
    Except
        Result := Nil;
    End;
End;

{ THE ROTATION AND THE Z OFFSET, written on the MODEL. SetState is PROVEN     }
{ LIVE for the rotation (2026-10-04, block comment above: ROTX/ROTZ persisted }
{ in the saved file).                                                          }
{                                                                              }
{ The arguments are RotX, RotY, RotZ in degrees and Dz in internal units:     }
{ the names come from the script binding's own signature for this method, and }
{ they are the MODEL.3D.ROTX, ROTY, ROTZ and DZ a saved PcbLib stores per     }
{ body. Dz WAS left at 0 because the Z offset went through StandoffHeight,    }
{ and that was measured not to lift a STEP model (block comment above), so   }
{ lib_link_3d_model's offset_z is now the Dz written here, the field          }
{ Altium's own libraries use. pcb_place_3d_body still passes 0: its           }
{ standoff_height route is a board placement this change does not touch.     }
{ The Dz half is the same call with a non-zero fourth argument; whether it    }
{ lands is read from the saved file (MODEL.3D.DZ), RELEASE_VERIFICATION 31.   }
{                                                                              }
{ Called BEFORE the body is added and before SetState_FromModel, so the body  }
{ derives its outline (and its standoff / overall heights) from the placed   }
{ model. AutoSTEPplacer.pas sets the model state before the model is         }
{ attached, and a setter reaching for state an owning board provides is what }
{ crashed ADVPCB.DLL in PCB_Place3DBody; this writes the model, not the body, }
{ so it does not need an owner.                                               }
Function Body3D_ApplyModelState(Model : IPCB_Model; RotX : Double;
                                RotY : Double; RotZ : Double; Dz : TCoord) : Boolean;
Begin
    Result := False;
    If Model = Nil Then Exit;
    Try
        Model.SetState(RotX, RotY, RotZ, Dz);
        Result := True;
    Except
        Result := False;
    End;
End;

{ The model's kind, as the T3DModelType ordinal, or -1. UNVERIFIED read. The  }
{ ordinals are not guessed: the binding's enum lists Extruded, Generic,       }
{ Cylinder, Sphere in that order, and an IPC generated PcbLib stores          }
{ MODEL.MODELTYPE=0 beside EXTRUDED.MINZ/MAXZ and 2 beside CYLINDER.RADIUS.   }
{ Compared as numbers so no enum identifier is needed at all.                 }
Function Body3D_ModelTypeId(Model : IPCB_Model) : Integer;
Begin
    Result := -1;
    If Model = Nil Then Exit;
    Try
        Result := Model.ModelType;
    Except
        Result := -1;
    End;
End;

{ "generic" is Altium's word for an imported model file: STEP, Parasolid or   }
{ SolidWorks. It is the kind lib_link_3d_model creates.                       }
Function Body3D_ModelTypeName(TypeId : Integer) : String;
Begin
    If TypeId = 0 Then Result := 'extruded'
    Else If TypeId = 1 Then Result := 'generic'
    Else If TypeId = 2 Then Result := 'cylinder'
    Else If TypeId = 3 Then Result := 'sphere'
    Else Result := 'unknown';
End;

{ Kind, source file and planar rotation of a model, as JSON fields without    }
{ braces. UNVERIFIED reads (ModelType, FileName, Rotation).                   }
{                                                                              }
{ planar_rotation is the model's own Rotation property, which the binding     }
{ pairs with Origin: on the evidence of the file layout it is                  }
{ MODEL.2D.ROTATION, a different number from the 3D rotation about Z. It is   }
{ reported so a live read can show which of the two a rotation landed in.     }
Function Body3D_ModelInfoJson(Model : IPCB_Model) : String;
Var
    TypeId : Integer;
    FileStr : String;
    Planar : Double;
Begin
    TypeId := Body3D_ModelTypeId(Model);
    FileStr := '';
    Planar := 0;
    If Model <> Nil Then
    Begin
        Try FileStr := Model.FileName; Except FileStr := ''; End;
        Try Planar := Model.Rotation; Except Planar := 0; End;
    End;
    Result := JsonStr('type', Body3D_ModelTypeName(TypeId)) + ',' +
              JsonInt('type_id', TypeId) + ',' +
              JsonStr('file', FileStr) + ',' +
              JsonFloat('planar_rotation', Planar);
End;

{ The model's 3D rotation READ BACK: UNAVAILABLE, said so.                    }
{                                                                              }
{ This used to call Model.GetState with four OUT parameters. MEASURED LIVE     }
{ 2026-10-04: the call returns without faulting and leaves every local at 0,  }
{ while the saved file holds MODEL.3D.ROTX=90 / ROTZ=45. An OUT parameter of  }
{ an Altium method does not come back through a DelphiScript local, so the   }
{ read reported (0, 0, 0) and rotation_readback_matches=false on a rotation   }
{ that had been applied and saved: a false failure, from a read that cannot  }
{ return a value at all. No other member of the binding returns the 3D        }
{ rotation (Rotation is the planar MODEL.2D.ROTATION), so nothing replaces    }
{ it here. The proof of a rotation is the saved file: lib_save_library, then  }
{ lib_read_pcblib_file, whose bodies carry MODEL.3D.ROTX/Y/Z and DZ.          }
{                                                                              }
{ Touches no IPCB_Model member, so it is safe on every path. ForBoard picks    }
{ the verify_with hint: lib_read_pcblib_file reads only a .PcbLib, so a free   }
{ body on a board (PCB_Place3DBody) gets a hint it can follow.                 }
Function Body3D_RotationReadbackJson(ForBoard : Boolean) : String;
Var
    VerifyHint : String;
Begin
    If ForBoard Then
        VerifyHint := 'look at it with obj_switch_view 3d. A board body stores '
            + 'MODEL.3D.ROTX/ROTY/ROTZ like a library one, but '
            + 'lib_read_pcblib_file reads only .PcbLib files'
    Else
        VerifyHint := 'lib_save_library, then lib_read_pcblib_file: the body''s '
            + 'model.rot_x/rot_y/rot_z/dz_mils are MODEL.3D.ROTX/ROTY/ROTZ/DZ '
            + 'as saved';
    Result := JsonObj(JsonBool('available', False) + ',' +
        JsonStr('reason', 'IPCB_Model.GetState returns its OUT parameters as 0 '
            + 'in DelphiScript (measured 2026-10-04), so the 3D rotation cannot '
            + 'be read back in memory') + ',' +
        JsonStr('verify_with', VerifyHint));
End;

{ The proven half of a body's description, as JSON fields without braces:     }
{ layer, standoff, overall height and bounding box in mils from (OrgX, OrgY). }
Function Body3D_BodyFieldsJson(Body : IPCB_ComponentBody; Board : IPCB_Board;
                               OrgX : TCoord; OrgY : TCoord) : String;
Var
    X1, Y1, X2, Y2, Standoff, Overall : TCoord;
    HaveRect : Boolean;
    LayerName : String;
Begin
    Standoff := 0;
    Overall := 0;
    LayerName := '';
    Try Standoff := Body.StandoffHeight; Except Standoff := 0; End;
    Try Overall := Body.OverallHeight; Except Overall := 0; End;
    Try LayerName := ResolveLayerName(Board, Body.Layer); Except LayerName := ''; End;
    Body3D_ReadRect(Body, X1, Y1, X2, Y2, HaveRect);
    Result := JsonStr('layer', LayerName) + ',' +
        JsonFloat('standoff_height_mils', CoordToMilsF(Standoff)) + ',' +
        JsonFloat('overall_height_mils', CoordToMilsF(Overall)) + ',';
    If HaveRect Then
        Result := Result + JsonRaw('bbox',
            Body3D_RectJson(X1, Y1, X2, Y2, OrgX, OrgY))
    Else
        Result := Result + JsonNull('bbox');
End;

{ May ModelFactory_FromFilename be handed this file? A DENY-list, so it is   }
{ CONSERVATIVE: False only for a file that is certainly not a 3D model (no   }
{ extension, or text, document, data and image formats), which the factory   }
{ answers Nil for anyway - the Model = Nil reply, MODEL_LOAD_FAILED, is what }
{ the callers give on False. Every other extension goes to the factory as    }
{ it did before this build: which formats Altium's loader takes (.wrl, .igs, }
{ .stl ...) is probe P7, and an allow-list here would have refused any it    }
{ does take, changing a call that succeeds today. A format the factory       }
{ raises on is caught and COUNTED at the call site, not refused here.        }
Function Model3DExtMayLoad(Path : String) : Boolean;
Var
    Ext : String;
Begin
    Ext := UpperCase(ExtractFileExt(Trim(Path)));
    Result := (Ext <> '') And (Ext <> '.') And (Ext <> '.TXT') And (Ext <> '.PDF')
        And (Ext <> '.CSV') And (Ext <> '.JSON') And (Ext <> '.PNG')
        And (Ext <> '.JPG');
End;

{ Lib_Link3DModel - attach a 3D STEP model to a PcbLib FOOTPRINT.              }
{                                                                              }
{ A 3D model in Altium is geometry that lives on the footprint as an          }
{ IPCB_ComponentBody, NOT a name-reference on the schematic symbol. The STEP  }
{ is loaded with ModelFactory_FromFilename and the body added to the          }
{ footprint (canonical AutoSTEPplacer pattern; same PCB object-factory family }
{ as Lib_AddFootprintPad). The previous schematic-side version attached a     }
{ 'PCB3DModel' implementation -- a wrong ModelType (the constant is           }
{ 'PCB3DLib') -- and passed the path to AddDataFileLink, which blocks on AD26.}
{                                                                              }
{ Params: component_name (footprint; empty = current footprint),              }
{         model_path (.step/.stp file), library_path (optional .PcbLib to     }
{         focus first, checked after the open), offset_x/offset_y/offset_z    }
{         (mils), rotation_x/rotation_y/rotation_z (degrees, on the MODEL),   }
{         replace ('true' removes the footprint's existing bodies first),     }
{         align ('none' | 'center' | 'pads'), layer (optional; with replace  }
{         and no layer the new body takes the replaced body's layer),         }
{         remove_from_board ('true' with replace also takes the replaced     }
{         bodies off the library board: the opt-in, unmeasured half of the   }
{         shared removal rule beside RemoveLibTextByAddress).                }
{                                                                              }
{ align: 'none' puts the STEP's OWN ORIGIN on the FOOTPRINT origin plus      }
{ (offset_x, offset_y): what Altium's library editor does when a STEP body is }
{ placed, and what a KiCad or maker model authored about its footprint origin }
{ needs. It used to move the body by the offsets from wherever the factory    }
{ put it, and the factory puts the model origin at ABSOLUTE (0, 0) on the     }
{ library board, not at the footprint origin (MEASURED 2026-10-04, see the 3D }
{ block comment): on a footprint whose origin reads (50000, 50000) mils the   }
{ model landed 50,000 mil from its pads, and the old self-check, which        }
{ compared the moved box with the pre-move box plus the move, passed it. The  }
{ model origin cannot be read (IPCB_Model.Origin is not proven), so the move  }
{ is (footprint origin + offsets) - (0, 0), the measured landing point, and   }
{ the placement check below catches a build that lands it anywhere else.      }
{ 'center' puts the centre of the body's bounding box on the footprint origin }
{ plus the offsets, 'pads' on the centre of the pads plus the offsets. Those  }
{ two are computed from the box READ AFTER the add, in absolute coordinates,  }
{ so where the model landed never affected them.                              }
{                                                                              }
{ offset_z is the model's Z offset, MODEL.3D.DZ, written through SetState's   }
{ Dz together with the rotation, before the body derives its heights. It used }
{ to set StandoffHeight, which was measured not to lift a STEP model.         }
{                                                                              }
{ EVERYTHING IS READ BACK. The reply carries the body's box, standoff and     }
{ overall height in the footprint's own frame, the pads' box in the same      }
{ frame, the box the move should have produced, and the PLACEMENT CHECK: the  }
{ gap between the body's box and the pads' box (the footprint origin when    }
{ there are no pads), in the footprint frame. A gap above PlaceFarMils, or a  }
{ box that cannot be read, is placement_ok=false with the reason, and the    }
{ Python wrapper turns that into ok=false. The 3D rotation cannot be read     }
{ back in memory (GetState returns zeros, measured), so rotation_readback is  }
{ 'unavailable' and the saved file is the proof (lib_read_pcblib_file). A box }
{ that did not change across a move is flagged bbox_readback_stale:           }
{ BoundingRectangle is CACHED on texts and nothing yet says whether a body's  }
{ is.                                                                          }
{                                                                              }
{ replace=true also reports replaced.board_still_listed: how many of the      }
{ removed bodies the LIBRARY BOARD still lists (a read-only walk, the        }
{ LibBoardListsAddress shape that ran live in LibBoardRemoveEntries on        }
{ 2026-10-04). See Lib_DeleteFootprintPads for why that number matters: a     }
{ primitive the board still holds is one the save can write back.            }
Function Lib_Link3DModel(Params : String; RequestId : String) : String;
Var
    ModelPath, ComponentName, FpName, LibPath, FocusedPath, AlignStr : String;
    LayerStr, LayerSource : String;
    Failure, FailCode, PadsJson, ExpectJson : String;
    AppliedJson, ReplacedJson, FrameJson, RotReqJson, MoveJson : String;
    RemovedEntries, BoardJson, PlaceJson, PlaceWhy, RefKind, ExpectFileJson : String;
    BoardStillJson, PlaceBasis, PlaceOkJson : String;
    OffX, OffY, OffZ, RotX, RotY, RotZ : Double;
    WantRotation, WantModelState, DidModelState, DidMove, Replace : Boolean;
    FromBoard, MadeCurrent, PlaceOk, PlaceKnown, HavePlaceBox, CurrentAtRemove : Boolean;
    HaveRect, HaveNewRect, Stale, Aligned, DidLayer : Boolean;
    WantLayer : TLayer;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Iter : IPCB_LibraryIterator;
    Body : IPCB_ComponentBody;
    Model : IPCB_Model;
    OrgX, OrgY, DX, DY : TCoord;
    X1, Y1, X2, Y2, NX1, NY1, NX2, NY2, PX1, PY1, PX2, PY2 : TCoord;
    RX1, RY1, RX2, RY2, GapX, GapY, Gap, BX1, BY1, BX2, BY2 : TCoord;
    PadCount, Removed, RemovedDistinct, Remaining, BoardRemoved, BoardStill : Integer;
Begin
    ModelPath := ExtractJsonValue(Params, 'model_path');
    ComponentName := ExtractJsonValue(Params, 'component_name');
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    Replace := (LowerCase(ExtractJsonValue(Params, 'replace')) = 'true');
    FromBoard := Replace
        And (LowerCase(Trim(ExtractJsonValue(Params, 'remove_from_board'))) = 'true');
    AlignStr := LowerCase(Trim(ExtractJsonValue(Params, 'align')));
    If AlignStr = '' Then AlignStr := 'none';
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    { Parse as FLOAT. These are declared `float` on the Python side, so       }
    { pydantic turns an argument of 25 into 25.0 and the wire carries "25.0". }
    { StrToIntDef cannot read that and returned its default, which once made  }
    { every offset arrive as 0 while the reply looked honest.                 }
    OffX := StrToFloatDef(ExtractJsonValue(Params, 'offset_x'), 0.0);
    OffY := StrToFloatDef(ExtractJsonValue(Params, 'offset_y'), 0.0);
    OffZ := StrToFloatDef(ExtractJsonValue(Params, 'offset_z'), 0.0);
    RotX := StrToFloatDef(ExtractJsonValue(Params, 'rotation_x'), 0.0);
    RotY := StrToFloatDef(ExtractJsonValue(Params, 'rotation_y'), 0.0);
    RotZ := StrToFloatDef(ExtractJsonValue(Params, 'rotation_z'), 0.0);
    WantRotation := (RotX <> 0) Or (RotY <> 0) Or (RotZ <> 0);
    { The model's state is written when a rotation OR a Z offset is asked    }
    { for: offset_z is MODEL.3D.DZ, the fourth argument of the same call.     }
    WantModelState := WantRotation Or (OffZ <> 0);

    { Refused before anything is touched, so a typo cannot half-apply. }
    If (AlignStr <> 'none') And (AlignStr <> 'center') And (AlignStr <> 'pads') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_ALIGN',
            'align must be "none" (the STEP origin on the footprint origin, '
            + 'plus the offsets), "center" (box centre on the footprint '
            + 'origin) or "pads" (box centre on the pads). Got: ' + AlignStr);
        Exit;
    End;

    If (ModelPath = '') Or (Not FileExists(ModelPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MODEL_FILE',
            'model_path is empty or the file does not exist: ' + ModelPath);
        Exit;
    End;

    { The library by PATH when one is named, and CHECKED after the open, the }
    { way Lib_DeleteFootprintPrimitives does: an edit aimed at a library     }
    { that never came to the front lands in whichever one did.              }
    If LibPath <> '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
            Exit;
        End;
        FocusedPath := '';
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
        If UpperCase(FocusedPath) <> UpperCase(LibPath) Then
        Begin
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', LibPath);
            RunProcess('WorkspaceManager:OpenObject');
            FocusedPath := '';
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
        End;
        If UpperCase(FocusedPath) <> UpperCase(LibPath) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
                'Asked for ' + LibPath + ' but the focused document is "'
                + FocusedPath + '". Nothing was changed.');
            Exit;
        End;
    End;

    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB library is active (a 3D body attaches to a PcbLib footprint). '
            + 'Pass library_path, or use pcb_place_3d_body for a board.');
        Exit;
    End;

    { A named layer is resolved against the library's own stack before    }
    { anything is created. GetLayerFromString answers TopLayer for every   }
    { name it does not know, and a 3D body put on copper is a quiet wrong. }
    WantLayer := eNoLayer;
    LayerSource := 'default';
    If LayerStr <> '' Then
    Begin
        WantLayer := ResolveLayerId(PcbLib.Board, LayerStr);
        If WantLayer = eNoLayer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
                'Unknown layer name: ' + LayerStr + '. '
                + BoardLayerNamesHint(PcbLib.Board));
            Exit;
        End;
        LayerSource := 'argument';
    End;

    { Select the footprint by name, otherwise use the current one. }
    If ComponentName <> '' Then
    Begin
        Footprint := Nil;
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                If Footprint.Name = ComponentName Then Break;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
        If Footprint = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
                'Footprint not found in library: ' + ComponentName);
            Exit;
        End;
        { MADE CURRENT AND READ BACK, as ResolveLibFootprint does. The body is }
        { registered with PcbLib.Board as well as the footprint, and the board }
        { attaches it to the CURRENT component, so a switch that silently did }
        { not take would put the model on two footprints. The unchecked       }
        { SetState_CurrentComponent this used is what the text tool dropped   }
        { for the same reason.                                                }
        If PcbLib.CurrentComponent <> Footprint Then
        Begin
            Try PcbLib.CurrentComponent := Footprint; Except End;
            If PcbLib.CurrentComponent <> Footprint Then
            Begin
                Result := BuildErrorResponse(RequestId, 'SELECT_FAILED',
                    'Could not make footprint "' + ComponentName + '" current, '
                    + 'so nothing was added: the library board attaches a new '
                    + 'body to its CURRENT footprint.');
                Exit;
            End;
        End;
    End
    Else
        Footprint := PcbLib.CurrentComponent;

    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'No footprint selected (pass component_name to choose one)');
        Exit;
    End;
    FpName := '';
    Try FpName := Footprint.Name; Except End;

    { The frame every coordinate in the reply is measured in. Read AFTER the }
    { footprint is made current: making a component current relocates it,   }
    { and an origin read before that is stale (see Lib_SetDesignators).     }
    OrgX := FootprintOriginX(Footprint);
    OrgY := FootprintOriginY(Footprint);

    { The board half of a replace needs the footprint current (rule 3 beside }
    { RemoveLibTextByAddress). It already is, by the switch above or by      }
    { being the current one; this reads that back before anything goes.    }
    MadeCurrent := False;
    If FromBoard Then MadeCurrent := LibMakeCurrentVerified(PcbLib, Footprint);
    RemovedEntries := '';
    BoardRemoved := 0;
    BoardStill := 0;
    BoardStillJson := 'null';
    CurrentAtRemove := False;
    BoardJson := JsonObj(JsonBool('requested', False));

    Failure := '';
    FailCode := '';
    DidModelState := False;
    DidMove := False;
    Aligned := False;
    DidLayer := False;
    HaveRect := False;
    Removed := 0;
    RemovedDistinct := 0;
    Remaining := 0;
    PadCount := 0;
    DX := 0;
    DY := 0;
    X1 := 0;
    Y1 := 0;
    X2 := 0;
    Y2 := 0;
    PX1 := 0;
    PY1 := 0;
    PX2 := 0;
    PY2 := 0;
    Body := Nil;
    Model := Nil;

    { A file that is certainly not a model gets the Model = Nil reply BEFORE }
    { the transaction opens (Model3DExtMayLoad, a deny-list: every other     }
    { format still reaches the loader, as before).                          }
    If Not Model3DExtMayLoad(ModelPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MODEL_LOAD_FAILED',
            'Could not load a 3D model from ' + ModelPath
            + '. Nothing was removed or added.');
        Exit;
    End;

    { NO Exit BETWEEN PreProcess AND PostProcess. A failure is recorded and  }
    { the remaining steps are skipped, so the Finally always closes the      }
    { transaction; an open one blocks every later PCB save until restart.    }
    PCBServer.PreProcess;
    Try
        Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
        If Body = Nil Then
        Begin
            FailCode := 'CREATE_FAILED';
            Failure := 'PCBObjectFactory returned Nil for eComponentBodyObject';
        End;

        If Failure = '' Then
        Begin
            { Counted, not guarded: whether the loader raises on a format  }
            { it cannot read (rather than answering Nil) is probe P7. A     }
            { raise is now the Model = Nil reply below, and counted.        }
            Model := Nil;
            Try
                Model := Body.ModelFactory_FromFilename(ModelPath, False);
            Except
                RecordCaughtException('Lib_Link3DModel:ModelFactory_FromFilename');
                Model := Nil;
            End;
            If Model = Nil Then
            Begin
                FailCode := 'MODEL_LOAD_FAILED';
                Failure := 'Could not load a 3D model from ' + ModelPath
                    + '. Nothing was removed or added.';
            End;
        End;

        If Failure = '' Then
        Begin
            { ROTATION AND Z OFFSET FIRST, on the MODEL, and only when one  }
            { was asked for. A link with neither runs exactly the sequence  }
            { that has always run: factory, load, SetState_FromModel,       }
            { assign, add.                                                  }
            If WantModelState Then
                DidModelState := Body3D_ApplyModelState(Model, RotX, RotY, RotZ,
                    MilsToCoordF(OffZ));
            Body.SetState_FromModel;
            Body.Model := Model;

            { REPLACE ONLY NOW THAT THE NEW MODEL HAS LOADED. Removing the   }
            { placeholders first would leave a footprint with no body at    }
            { all whenever the STEP turned out unreadable.                  }
            If Replace Then
            Begin
                If WantLayer = eNoLayer Then
                Begin
                    WantLayer := Body3D_FirstBodyLayer(Footprint);
                    If WantLayer <> eNoLayer Then LayerSource := 'replaced body';
                End;
                Removed := Body3D_RemoveAll(Footprint, RemovedDistinct, RemovedEntries);
                Remaining := Body3D_CountBodies(Footprint);
                { The opt-in board half, after the footprint read-back and   }
                { before the new body is added, never on a body the          }
                { footprint still lists (rule 4).                            }
                If FromBoard Then
                    BoardJson := LibBoardRemoveEntries(PcbLib.Board, RemovedEntries,
                        Body3D_BodyAddresses(Footprint), MadeCurrent, BoardRemoved);
                { What the library board still holds of the removed bodies, }
                { read-only, before the new body joins it, and only while   }
                { the footprint is current (it is here, by the switch       }
                { above; read back, the same comparison as                  }
                { LibMakeCurrentVerified): for a footprint that is not      }
                { current the count is 0 by construction (rule 3), so the   }
                { reply says null rather than a 0 that proves nothing.      }
                Try CurrentAtRemove := (PcbLib.CurrentComponent = Footprint); Except CurrentAtRemove := False; End;
                If CurrentAtRemove Then
                Begin
                    BoardStill := LibBoardStillListsCount(PcbLib.Board, RemovedEntries);
                    BoardStillJson := IntToStr(BoardStill);
                End;
            End;

            Footprint.AddPCBObject(Body);
            { Same registration the pad, track, arc and text paths need.   }
            { Without it the body lives in the working copy only, and the  }
            { save throws it away. Audit by the AddPCBObject call, not by  }
            { what the handler is called.                                  }
            PcbLib.Board.AddPCBObject(Body);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);

            { Layer after the add: a setter on an unowned body is what crashed }
            { ADVPCB.DLL in PCB_Place3DBody.                                    }
            If WantLayer <> eNoLayer Then
            Begin
                Try
                    Body.Layer := WantLayer;
                    DidLayer := True;
                Except End;
            End;

            { The move. 'none' is computed from the MEASURED landing point    }
            { of the model origin, absolute (0, 0), so the origin goes to the }
            { footprint origin plus the offsets; 'center' and 'pads' from the }
            { box as it stands after the add.                                 }
            Body3D_ReadRect(Body, X1, Y1, X2, Y2, HaveRect);
            If AlignStr = 'none' Then
            Begin
                DX := OrgX + MilsToCoordF(OffX);
                DY := OrgY + MilsToCoordF(OffY);
                Aligned := True;
            End
            Else If HaveRect Then
            Begin
                If AlignStr = 'center' Then
                Begin
                    DX := OrgX + MilsToCoordF(OffX) - (X1 + (X2 - X1) Div 2);
                    DY := OrgY + MilsToCoordF(OffY) - (Y1 + (Y2 - Y1) Div 2);
                    Aligned := True;
                End
                Else
                Begin
                    Body3D_PadsRect(Footprint, PX1, PY1, PX2, PY2, PadCount);
                    If PadCount > 0 Then
                    Begin
                        DX := (PX1 + (PX2 - PX1) Div 2) + MilsToCoordF(OffX)
                            - (X1 + (X2 - X1) Div 2);
                        DY := (PY1 + (PY2 - PY1) Div 2) + MilsToCoordF(OffY)
                            - (Y1 + (Y2 - Y1) Div 2);
                        Aligned := True;
                    End;
                End;
            End;

            If (DX <> 0) Or (DY <> 0) Then
            Begin
                Try
                    Body.MoveByXY(DX, DY);
                    DidMove := True;
                Except End;
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Failure <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, FailCode, Failure);
        Exit;
    End;

    { READ IT ALL BACK. }
    Body3D_ReadRect(Body, NX1, NY1, NX2, NY2, HaveNewRect);
    Stale := DidMove And HaveRect And HaveNewRect
        And (NX1 = X1) And (NY1 = Y1) And (NX2 = X2) And (NY2 = Y2);
    If HaveRect Then
        ExpectJson := Body3D_RectJson(X1 + DX, Y1 + DY, X2 + DX, Y2 + DY, OrgX, OrgY)
    Else
        ExpectJson := 'null';

    Body3D_PadsRect(Footprint, PX1, PY1, PX2, PY2, PadCount);
    If PadCount > 0 Then
        PadsJson := Body3D_RectJson(PX1, PY1, PX2, PY2, OrgX, OrgY)
    Else
        PadsJson := 'null';

    { THE PLACEMENT CHECK, in the FOOTPRINT frame. bbox_expected above only   }
    { says the move was applied as computed (box before the move plus the    }
    { move), which is true of a body put 50,000 mil from its footprint as     }
    { much as of a right one: that is how the misplaced body of 2026-10-04    }
    { passed. This compares the body's box with the footprint itself: the     }
    { gap between it and the pads' box, or the footprint origin when there   }
    { are no pads. 0 means they overlap. A model is drawn around its package, }
    { so a gap above PlaceFarMils (1000 mil = 25.4 mm) is a body that is not  }
    { on its footprint, whatever the offsets were.                            }
    If PadCount > 0 Then
    Begin
        RX1 := PX1;
        RY1 := PY1;
        RX2 := PX2;
        RY2 := PY2;
        RefKind := 'pads_bbox';
    End
    Else
    Begin
        RX1 := OrgX;
        RY1 := OrgY;
        RX2 := OrgX;
        RY2 := OrgY;
        RefKind := 'footprint_origin';
    End;
    { WHICH BOX IS MEASURED. Normally the box read back after the move. When }
    { that read is STALE (bbox_readback_stale: it did not change across a    }
    { move, i.e. a cached BoundingRectangle, a documented failure mode) it   }
    { is the PRE-move box, and for align=none that sits near absolute (0, 0), }
    { ~50,000 mil from a footprint whose origin is (50000, 50000): failing   }
    { on it would report a move that worked as a misplaced body (a review,   }
    { 2026-10-04, caught exactly that). So a stale read measures the        }
    { EXPECTED box instead (pre-move box plus the move). Far from the        }
    { footprint is still a failure, because then the move AS COMPUTED puts   }
    { the body off its footprint whatever the read says; near it is          }
    { UNVERIFIED (ok null), not passed, because only the read could show     }
    { the move landed. lib_read_pcblib_file on the saved file settles it.    }
    HavePlaceBox := False;
    PlaceBasis := 'none';
    BX1 := 0;
    BY1 := 0;
    BX2 := 0;
    BY2 := 0;
    If Stale Then
    Begin
        BX1 := X1 + DX;
        BY1 := Y1 + DY;
        BX2 := X2 + DX;
        BY2 := Y2 + DY;
        HavePlaceBox := True;
        PlaceBasis := 'bbox_expected';
    End
    Else If HaveNewRect Then
    Begin
        BX1 := NX1;
        BY1 := NY1;
        BX2 := NX2;
        BY2 := NY2;
        HavePlaceBox := True;
        PlaceBasis := 'bbox_readback';
    End;
    Gap := 0;
    PlaceOk := False;
    PlaceKnown := True;
    PlaceWhy := '';
    If HavePlaceBox Then
    Begin
        GapX := 0;
        If RX1 - BX2 > GapX Then GapX := RX1 - BX2;
        If BX1 - RX2 > GapX Then GapX := BX1 - RX2;
        GapY := 0;
        If RY1 - BY2 > GapY Then GapY := RY1 - BY2;
        If BY1 - RY2 > GapY Then GapY := BY1 - RY2;
        Gap := GapX;
        If GapY > Gap Then Gap := GapY;
        PlaceOk := (Gap <= MilsToCoordF(PlaceFarMils));
        If Not PlaceOk Then
            PlaceWhy := 'the body''s box (' + PlaceBasis + ') is '
                + FloatToJsonStr(CoordToMilsF(Gap))
                + ' mil from the footprint''s ' + RefKind + ' (limit '
                + IntToStr(PlaceFarMils) + ' mil): the model is not on its '
                + 'footprint. Compare body.bbox with frame.pads_bbox. To '
                + 'undo: lib_delete_footprint_primitives with object_type '
                + 'componentbody and library_path (it saves and reloads '
                + 'first, so the removal sticks), or link again with '
                + 'replace=true and corrected offsets.'
        Else If Stale Then
        Begin
            PlaceKnown := False;
            PlaceWhy := 'unverified: the bounding box read back did not change '
                + 'across the move (bbox_readback_stale), so the box measured '
                + 'is the EXPECTED one, which is on the footprint; only a '
                + 'fresh read can show the move landed. Save, then '
                + 'lib_read_pcblib_file(library_path, footprint_name): the '
                + 'body''s model.x_mils/y_mils are its stored placement.';
        End;
    End
    Else
        PlaceWhy := 'the body''s bounding box could not be read, so its '
            + 'placement is unverified. Save, lib_reload_library and read it '
            + 'with lib_get_footprint_bodies, or read the saved file with '
            + 'lib_read_pcblib_file.';
    If PlaceKnown Then
        PlaceOkJson := BoolToJsonStr(PlaceOk)
    Else
        PlaceOkJson := 'null';
    PlaceJson := JsonObj(JsonRaw('ok', PlaceOkJson) + ','
        + JsonStr('basis', PlaceBasis) + ','
        + JsonStr('reference', RefKind) + ','
        + JsonFloat('gap_mils', CoordToMilsF(Gap)) + ','
        + JsonInt('limit_mils', PlaceFarMils) + ','
        + JsonStr('problem', PlaceWhy));

    { What the saved ComponentBody record should hold, RELATIVE TO THE      }
    { FOOTPRINT ORIGIN as the file stores it. MODEL.2D.X/Y is the model       }
    { origin, known only for align=none (the other modes move a box whose     }
    { offset from the model origin depends on how the STEP was drawn).        }
    If AlignStr = 'none' Then
        ExpectFileJson := JsonFloat('model_2d_x_mils', OffX) + ','
            + JsonFloat('model_2d_y_mils', OffY)
    Else
        ExpectFileJson := JsonNull('model_2d_x_mils') + ','
            + JsonNull('model_2d_y_mils');
    ExpectFileJson := JsonObj(ExpectFileJson + ','
        + JsonFloat('model_3d_rotx', RotX) + ','
        + JsonFloat('model_3d_roty', RotY) + ','
        + JsonFloat('model_3d_rotz', RotZ) + ','
        + JsonFloat('model_3d_dz_mils', OffZ));

    AppliedJson := JsonBool('rotation', DidModelState And WantRotation) + ','
        + JsonBool('rotation_z', DidModelState And (RotZ <> 0)) + ','
        + JsonBool('z_offset', DidModelState And (OffZ <> 0)) + ','
        + JsonFloat('z_offset_mils', OffZ) + ','
        + JsonBool('standoff_height', False) + ','
        + JsonBool('offset_xy', DidMove) + ','
        + JsonStr('align', AlignStr) + ','
        + JsonBool('aligned', Aligned) + ','
        + JsonBool('layer', DidLayer) + ','
        + JsonStr('layer_source', LayerSource);
    RotReqJson := JsonObj(JsonFloat('x', RotX) + ',' + JsonFloat('y', RotY) + ','
        + JsonFloat('z', RotZ));
    ReplacedJson := JsonObj(JsonBool('requested', Replace) + ','
        + JsonInt('removed_bodies', RemovedDistinct) + ','
        + JsonInt('remove_calls', Removed) + ','
        + JsonInt('remaining_before_add', Remaining) + ','
        + JsonRaw('board_still_listed', BoardStillJson) + ','
        + JsonRaw('board_removal', BoardJson));
    FrameJson := JsonObj(JsonFloat('origin_x_mils', CoordToMilsF(OrgX)) + ','
        + JsonFloat('origin_y_mils', CoordToMilsF(OrgY)) + ','
        + JsonRaw('pads_bbox', PadsJson));
    MoveJson := JsonObj(JsonFloat('dx', CoordToMilsF(DX)) + ','
        + JsonFloat('dy', CoordToMilsF(DY)));

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonBool('success', True) + ','
        + JsonStr('footprint', FpName) + ','
        + JsonStr('model', ExtractFileName(ModelPath)) + ','
        + JsonStr('units', 'mils') + ','
        + JsonRaw('applied', JsonObj(AppliedJson)) + ','
        + JsonRaw('rotation_requested', RotReqJson) + ','
        + JsonNull('model_state') + ','
        + JsonRaw('rotation_readback', Body3D_RotationReadbackJson(False)) + ','
        + JsonNull('rotation_readback_matches') + ','
        + JsonRaw('expected_in_file', ExpectFileJson) + ','
        + JsonRaw('replaced', ReplacedJson) + ','
        + JsonRaw('frame', FrameJson) + ','
        + JsonRaw('body', JsonObj(Body3D_BodyFieldsJson(Body, PcbLib.Board, OrgX, OrgY))) + ','
        + JsonRaw('placement', PlaceJson) + ','
        + JsonRaw('placement_ok', PlaceOkJson) + ','
        + JsonRaw('bbox_expected', ExpectJson) + ','
        + JsonBool('bbox_readback_stale', Stale) + ','
        + JsonRaw('move_mils', MoveJson)));

    MarkDocDirtyByPath(PcbLib.Board.FileName);
    NoteNextStep('The PcbLib is changed in memory only; lib_save_library writes it. '
        + 'placement.ok is the check that the body sits on its footprint. After '
        + 'the save, lib_read_pcblib_file(library_path, footprint_name) shows the '
        + 'body as stored: model.x_mils/y_mils (= offset_x/offset_y for align=none), '
        + 'rot_x/rot_y/rot_z and dz_mils (= offset_z).');
End;

{ Lib_GetFootprintBodies - READ-ONLY list of the 3D bodies on a footprint.    }
{                                                                              }
{ WHY IT EXISTS. lib_link_3d_model added bodies and nothing could list them:  }
{ the policy dump reports a COUNT, so telling an IPC generator's extruded    }
{ placeholder from a linked STEP, or seeing where a model actually landed,    }
{ meant opening the 3D view and guessing.                                    }
{                                                                              }
{ Two shapes, chosen by footprint_name:                                       }
{   * one footprint: every body with layer, standoff, overall height and      }
{     bounding box in the footprint's own frame (mils), the pads' box in the  }
{     same frame as the cross-check, and with model_state the model's kind,  }
{     source file, planar rotation and 3D rotation read back;                 }
{   * empty: the whole library, paged by offset/limit, one line per footprint }
{     with its body count and, with model_state, the count of each kind. The  }
{     question it answers is "which footprints still carry placeholders".    }
{                                                                              }
{ The single-footprint form makes the footprint current so its frame is the  }
{ one lib_link_3d_model measures in, and puts the previous current footprint  }
{ back afterwards. The dispatcher restores the focused DOCUMENT.              }
{                                                                              }
{ MODEL READS ARE OPT-IN: model_state must be exactly 'true'. They go through }
{ IPCB_Model members no live call has run yet (see the 3D block comment       }
{ above), and an undeclared identifier there is not caught by any Try: it    }
{ stops the polling loop and the bridge needs a manual restart. A default-on  }
{ read would make the first ordinary call, and every body of every footprint }
{ in a sweep, the experiment. Without model_state the reply carries          }
{ everything the proven members give. Flip the default only after            }
{ RELEASE_VERIFICATION step 5 has passed live.                               }
{                                                                              }
{ Params: footprint_name (empty = sweep), library_path (optional),           }
{         model_state ('true' to read the models; anything else skips them),  }
{         offset, limit (sweep).                                               }
Function Lib_GetFootprintBodies(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, Addr : String;
    ItemsJson, BodyJson, ModelJson, PadsJson, FpsJson, CountsJson : String;
    WantModel, MadeCurrent : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target, SavedCurrent : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Body : IPCB_ComponentBody;
    Model : IPCB_Model;
    Seen : TStringList;
    OrgX, OrgY, PX1, PY1, PX2, PY2 : TCoord;
    BodyN, PadCount, Offset, Limit, Total, Emitted, TypeId : Integer;
    NExtruded, NGeneric, NCylinder, NSphere, NUnknown : Integer;
Begin
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    FpWanted := Trim(ExtractJsonValue(Params, 'footprint_name'));
    WantModel := (LowerCase(Trim(ExtractJsonValue(Params, 'model_state'))) = 'true');
    Offset := StrToIntDef(ExtractJsonValue(Params, 'offset'), 0);
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 250);
    If Offset < 0 Then Offset := 0;
    If Limit <= 0 Then Limit := 250;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If UpperCase(FocusedPath) <> UpperCase(LibPath) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
        FocusedPath := '';
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    End;
    { A read that answered about a library nobody asked for is the answer  }
    { that looks right: a sweep once returned twenty one identical replies, }
    { every one read off the same focused file.                             }
    If UpperCase(FocusedPath) <> UpperCase(LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '".');
        Exit;
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            LibPath + ' is not a PCB library. Bodies on a board are read with '
            + 'obj_query object_type=eComponentBodyObject.');
        Exit;
    End;

    { ---- the whole library, one line per footprint ---- }
    If FpWanted = '' Then
    Begin
        FpsJson := '';
        Total := 0;
        Emitted := 0;
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                If (Total >= Offset) And (Emitted < Limit) Then
                Begin
                    FpName := '';
                    Try FpName := Footprint.Name; Except End;
                    BodyN := 0;
                    NExtruded := 0;
                    NGeneric := 0;
                    NCylinder := 0;
                    NSphere := 0;
                    NUnknown := 0;
                    Seen := TStringList.Create;
                    Try
                        GrpIter := Footprint.GroupIterator_Create;
                        Try
                            GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                            Body := GrpIter.FirstPCBObject;
                            While Body <> Nil Do
                            Begin
                                Addr := '';
                                Try Addr := IntToStr(Body.I_ObjectAddress); Except End;
                                If (Addr = '') Or (Seen.IndexOf(Addr) < 0) Then
                                Begin
                                    If Addr <> '' Then Seen.Add(Addr);
                                    BodyN := BodyN + 1;
                                    If WantModel Then
                                    Begin
                                        TypeId := Body3D_ModelTypeId(Body3D_GetModel(Body));
                                        If TypeId = 0 Then NExtruded := NExtruded + 1
                                        Else If TypeId = 1 Then NGeneric := NGeneric + 1
                                        Else If TypeId = 2 Then NCylinder := NCylinder + 1
                                        Else If TypeId = 3 Then NSphere := NSphere + 1
                                        Else NUnknown := NUnknown + 1;
                                    End;
                                End;
                                Body := GrpIter.NextPCBObject;
                            End;
                        Finally
                            Footprint.GroupIterator_Destroy(GrpIter);
                        End;
                    Except End;
                    Try Seen.Free; Except End;

                    CountsJson := '';
                    If WantModel Then
                        CountsJson := ',' + JsonInt('extruded', NExtruded)
                            + ',' + JsonInt('generic', NGeneric)
                            + ',' + JsonInt('cylinder', NCylinder)
                            + ',' + JsonInt('sphere', NSphere)
                            + ',' + JsonInt('unknown', NUnknown);
                    If Emitted > 0 Then FpsJson := FpsJson + ',';
                    FpsJson := FpsJson + JsonObj(JsonStr('name', FpName) + ','
                        + JsonInt('bodies', BodyN) + CountsJson);
                    Emitted := Emitted + 1;
                End;
                Total := Total + 1;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;

        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonStr('library_path', LibPath) + ','
            + JsonInt('total', Total) + ','
            + JsonInt('offset', Offset) + ','
            + JsonInt('count', Emitted) + ','
            + JsonBool('model_state', WantModel) + ','
            + JsonRaw('footprints', '[' + FpsJson + ']')));
        Exit;
    End;

    { ---- one footprint, every body ---- }
    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    { The frame lib_link_3d_model measures in: current first, origin after. }
    SavedCurrent := Nil;
    Try SavedCurrent := PcbLib.CurrentComponent; Except SavedCurrent := Nil; End;
    { Made current with the property write and READ BACK, as every footprint }
    { handler here now is; the reply says whether the frame below is the one }
    { a current footprint has.                                               }
    Try PcbLib.CurrentComponent := Target; Except End;
    MadeCurrent := False;
    Try MadeCurrent := (PcbLib.CurrentComponent = Target); Except MadeCurrent := False; End;
    OrgX := FootprintOriginX(Target);
    OrgY := FootprintOriginY(Target);
    FpName := '';
    Try FpName := Target.Name; Except End;

    ItemsJson := '';
    BodyN := 0;
    Seen := TStringList.Create;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
        Body := GrpIter.FirstPCBObject;
        While Body <> Nil Do
        Begin
            Addr := '';
            Try Addr := IntToStr(Body.I_ObjectAddress); Except End;
            If (Addr <> '') And (Seen.IndexOf(Addr) >= 0) Then
            Begin
                Body := GrpIter.NextPCBObject;
                Continue;
            End;
            If Addr <> '' Then Seen.Add(Addr);
            BodyJson := JsonInt('index', BodyN) + ','
                + Body3D_BodyFieldsJson(Body, PcbLib.Board, OrgX, OrgY);
            If WantModel Then
            Begin
                Model := Body3D_GetModel(Body);
                If Model <> Nil Then
                Begin
                    { rotation is null and rotation_readback says why: the  }
                    { GetState read returned zeros live (2026-10-04), so a  }
                    { value here would be a false one. The saved file has   }
                    { it: lib_read_pcblib_file.                              }
                    ModelJson := JsonObj(Body3D_ModelInfoJson(Model) + ','
                        + JsonNull('rotation') + ','
                        + JsonRaw('rotation_readback', Body3D_RotationReadbackJson(False)));
                End
                Else
                    ModelJson := 'null';
                BodyJson := BodyJson + ',' + JsonRaw('model', ModelJson);
            End;
            If BodyN > 0 Then ItemsJson := ItemsJson + ',';
            ItemsJson := ItemsJson + JsonObj(BodyJson);
            BodyN := BodyN + 1;
            Body := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
        Try Seen.Free; Except End;
    End;

    Body3D_PadsRect(Target, PX1, PY1, PX2, PY2, PadCount);
    If PadCount > 0 Then
        PadsJson := Body3D_RectJson(PX1, PY1, PX2, PY2, OrgX, OrgY)
    Else
        PadsJson := 'null';

    If SavedCurrent <> Nil Then
    Begin
        Try PcbLib.CurrentComponent := SavedCurrent; Except End;
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('library_path', LibPath) + ','
        + JsonStr('footprint', FpName) + ','
        + JsonStr('units', 'mils') + ','
        + JsonRaw('frame', JsonObj(
            JsonFloat('origin_x_mils', CoordToMilsF(OrgX)) + ','
            + JsonFloat('origin_y_mils', CoordToMilsF(OrgY)) + ','
            + JsonBool('made_current', MadeCurrent) + ','
            + JsonRaw('pads_bbox', PadsJson))) + ','
        + JsonInt('body_count', BodyN) + ','
        + JsonBool('model_state', WantModel) + ','
        + JsonRaw('bodies', '[' + ItemsJson + ']')));
End;

Function Lib_GetComponents(Params : String; RequestId : String) : String;
Var
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    Impl : ISch_Implementation;
    Workspace : IWorkspace;
    Doc : IDocument;
    LibPath, Data, CompName, ParamList, WithParamsStr : String;
    ParamLower, ParamText, WithDesigStr, DefDesig : String;
    Mpn, Manufacturer, Datasheet, FootprintName : String;
    CompNum, I : Integer;
    First, WithParams, WithDesignator : Boolean;
Begin
    // Get library path from parameter or active document
    LibPath := ExtractJsonValue(Params, 'library_path');

    // Optional flag: dump parameters per component. Default is FALSE because
    // GetState_SchComponentByLibRef + parameter iterator runs O(N) and is the
    // bottleneck on large libraries (a 400+ component standard lib takes
    // tens of seconds with parameters on, sub-second without). Callers that
    // need parameters for a specific symbol should use lib_get_component_details.
    WithParamsStr := ExtractJsonValue(Params, 'with_parameters');
    WithParams := (WithParamsStr = 'true') Or (WithParamsStr = 'True') Or (WithParamsStr = '1');

    // Optional lean flag: emit each component's DEFAULT designator
    // (Component.Designator.Text, e.g. "U?" / "R?" / "IC?"). Like
    // with_parameters it must load the live symbol via
    // GetState_SchComponentByLibRef (the CompInfoReader fast path does NOT
    // expose the designator), but it skips parameter iteration so the
    // payload stays small -- intended for library-wide designator audits.
    WithDesigStr := ExtractJsonValue(Params, 'with_designator');
    WithDesignator := (WithDesigStr = 'true') Or (WithDesigStr = 'True') Or (WithDesigStr = '1');

    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then
            Begin
                // DM_FileName returns just the basename;
                // CreateLibCompInfoReader needs the full path or it
                // silently returns an empty reader (which is exactly the
                // bug that made lib_get_components always report 0).
                Try LibPath := Doc.DM_FullPath; Except End;
                If LibPath = '' Then LibPath := Doc.DM_FileName;
            End;
        End;
    End;

    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library path and no active document');
        Exit;
    End;

    { A RELATIVE PATH HERE PRODUCES A WRONG ANSWER, NOT AN ERROR.            }
    { CreateLibCompInfoReader wants a full path. Given a bare basename it    }
    { resolves against whatever directory it likes and hands back a reader   }
    { for something else, or a stale view, and the enumeration then looks    }
    { perfectly ordinary.                                                     }
    {                                                                         }
    { MEASURED: called with no library_path against a focused free-document  }
    { SchLib, this reported ONE component twice in a row while the same call }
    { WITH the full path reported both that were really there. The default   }
    { invocation is the one that under-reports, which is the worst way round.}
    { DM_FullPath returns a basename for a free document, which is how the   }
    { relative path got in.                                                   }
    If Not ((Copy(LibPath, 2, 1) = ':') Or (Copy(LibPath, 1, 2) = '\\')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'RELATIVE_LIBRARY_PATH',
            'The focused document reports only "' + LibPath + '", not a full '
            + 'path, and the component reader silently answers about the '
            + 'wrong file when given one. Pass library_path with the '
            + 'absolute .SchLib path.');
        Exit;
    End;

    // Use CreateLibCompInfoReader to enumerate components. ICompInfoReader is
    // a fast metadata reader, it returns CompName, AliasName, PartCount and
    // Description directly from the lib file without loading every symbol's
    // primitives, so the cheap path scales linearly with file IO.
    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
    If LibReader = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Failed to create library reader for: ' + LibPath);
        Exit;
    End;

    LibReader.ReadAllComponentInfo;
    CompNum := LibReader.NumComponentInfos;

    // Only navigate to live components when the caller asked for parameters,
    // otherwise we skip GetState_SchComponentByLibRef entirely.
    SchLib := Nil;
    If WithParams Or WithDesignator Then
        SchLib := SchServer.GetCurrentSchDocument;

    Data := '[';
    First := True;
    For I := 0 To CompNum - 1 Do
    Begin
        If Not First Then Data := Data + ',';
        First := False;
        CompInfo := LibReader.ComponentInfos[I];
        CompName := CompInfo.CompName;

        { index is the stable addressing key: pass it as component_index to  }
        { any lib tool that resolves a component, to reach names with bytes   }
        { that cannot be reproduced (embedded quotes / control chars).        }
        Data := Data + '{"index":' + IntToStr(I) +
            ',"name":"' + EscapeJsonString(CompName) + '"';
        Try Data := Data + ',"alias_name":"' + EscapeJsonString(CompInfo.AliasName) + '"'; Except End;
        Try Data := Data + ',"part_count":' + IntToStr(CompInfo.PartCount); Except End;
        Data := Data + ',"description":"' + EscapeJsonString(CompInfo.Description) + '"';

        // Slow path, opt-in via with_parameters=true.
        // Atomic-parts contract: when we're already paying for the live
        // component load, harvest mpn / manufacturer / datasheet from the
        // parameter set plus the current implementation's footprint model
        // name so the planner can populate Part directly from inventory.
        If WithParams Or WithDesignator Then
        Begin
            ParamList := '';
            Mpn := '';
            Manufacturer := '';
            Datasheet := '';
            FootprintName := '';
            DefDesig := '';
            If (SchLib <> Nil) And (SchLib.ObjectId = eSchLib) Then
            Begin
                Component := LookupLibComponent(SchLib, CompName);
                If (Component <> Nil) And WithDesignator Then
                    Try DefDesig := Component.Designator.Text; Except End;
                If (Component <> Nil) And WithParams Then
                Begin
                    ParamIterator := Component.SchIterator_Create;
                    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                    Param := ParamIterator.FirstSchObject;
                    While Param <> Nil Do
                    Begin
                        If ParamList <> '' Then ParamList := ParamList + ',';
                        ParamText := Param.Text;
                        ParamList := ParamList + '"' + EscapeJsonString(Param.Name) + '":"' + EscapeJsonString(ParamText) + '"';
                        // Capture atomic-parts fields by canonical Altium
                        // parameter names. LowerCase makes us tolerant of
                        // libs that capitalize "MPN" vs "Mpn", etc.
                        ParamLower := LowerCase(Param.Name);
                        If (Mpn = '') And ((ParamLower = 'manufacturer part number')
                            Or (ParamLower = 'manufacturerpartnumber')
                            Or (ParamLower = 'mpn')
                            Or (ParamLower = 'part number')
                            Or (ParamLower = 'partnumber')) Then
                            Mpn := ParamText;
                        If (Manufacturer = '') And ((ParamLower = 'manufacturer')
                            Or (ParamLower = 'mfr')
                            Or (ParamLower = 'mfg')) Then
                            Manufacturer := ParamText;
                        If (Datasheet = '') And ((ParamLower = 'datasheet')
                            Or (ParamLower = 'datasheeturl')
                            Or (ParamLower = 'datasheet url')
                            Or (ParamLower = 'componentlink1url')) Then
                            Datasheet := ParamText;
                        Param := ParamIterator.NextSchObject;
                    End;
                    Component.SchIterator_Destroy(ParamIterator);

                    // First implementation = the linked footprint model
                    // (see Lib_LinkFootprint, which writes Impl.ModelName).
                    // Nil when the symbol has zero implementations.
                    Impl := GetFirstSchImplementation(Component);
                    If Impl <> Nil Then
                        Try FootprintName := Impl.ModelName; Except End;
                End;
            End;
            If WithDesignator Then
                Data := Data + ',"designator":"' + EscapeJsonString(DefDesig) + '"';
            If WithParams Then
            Begin
                Data := Data + ',"parameters":{' + ParamList + '}';
                Data := Data + ',"mpn":"' + EscapeJsonString(Mpn) + '"';
                Data := Data + ',"manufacturer":"' + EscapeJsonString(Manufacturer) + '"';
                Data := Data + ',"datasheet":"' + EscapeJsonString(Datasheet) + '"';
                Data := Data + ',"footprint":"' + EscapeJsonString(FootprintName) + '"';
            End;
        End;
        Data := Data + '}';
    End;

    SchServer.DestroyCompInfoReader(LibReader);
    Data := Data + ']';

    Result := BuildSuccessResponse(RequestId, '{"count":' + IntToStr(CompNum) + ',"components":' + Data + '}');
End;

{ Lib_Search - case-insensitive substring search over all open SchLib docs. }
{ The previous implementation invoked Client:FindComponent, which only       }
{ pops the interactive Find Component panel and returns no data, so the     }
{ tool was unusable from an LLM. This handler enumerates SchLib members of  }
{ every workspace project plus the synthetic FreeDocumentsProject (where    }
{ standalone libraries live), opens an ILibCompInfoReader per file (fast,   }
{ no live-component load) and matches CompName / Description / AliasName   }
{ against the query.                                                         }
{                                                                              }
{ Params:                                                                     }
{   query        - substring (case-insensitive). Required.                   }
{   search_type  - 'all' (default) | 'name' | 'description' | 'parameters'. }
{                  'all' tests name + description + alias. 'parameters'     }
{                  also loads each candidate live (slow on big libs).        }
{   library_path - optional, restrict the search to a single .SchLib file.  }
{   limit        - max matches (default 100).                                }
{ Returns a JSON array of [name, alias_name, description, library_path,    }
{ part_count] per match.                                                    }
Function SearchOneLibrary(LibPath, Query, SearchType : String;
    SearchParams : Boolean; SchLib : ISch_Lib;
    Var ResultsJson : String; Var First : Boolean;
    Var Count : Integer; Limit : Integer) : Boolean;
Var
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    LowerQuery, CompName, AliasName, Description : String;
    LowerName, LowerAlias, LowerDesc : String;
    NumComps, I : Integer;
    Matched, MatchedParam : Boolean;
Begin
    Result := False;
    LowerQuery := LowerCase(Query);

    { A library with no file on disk (named but missing, or open and never  }
    { saved) contributes no matches, as an empty reader would.              }
    If LibFileMissing(LibPath) Then
    Begin
        Result := True;
        Exit;
    End;

    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
    If LibReader = Nil Then Exit;

    Try
        LibReader.ReadAllComponentInfo;
        NumComps := LibReader.NumComponentInfos;

        For I := 0 To NumComps - 1 Do
        Begin
            If Count >= Limit Then Break;

            CompInfo := LibReader.ComponentInfos[I];
            CompName := '';
            AliasName := '';
            Description := '';
            Try CompName := CompInfo.CompName; Except End;
            Try AliasName := CompInfo.AliasName; Except End;
            Try Description := CompInfo.Description; Except End;

            LowerName := LowerCase(CompName);
            LowerAlias := LowerCase(AliasName);
            LowerDesc := LowerCase(Description);

            Matched := False;
            If SearchType = 'name' Then
                Matched := Pos(LowerQuery, LowerName) > 0
            Else If SearchType = 'description' Then
                Matched := Pos(LowerQuery, LowerDesc) > 0
            Else
            Begin
                { 'all' / 'parameters' both check name + alias + description }
                { up front. parameters then drops to the slow path on miss. }
                Matched := (Pos(LowerQuery, LowerName) > 0)
                    Or (Pos(LowerQuery, LowerAlias) > 0)
                    Or (Pos(LowerQuery, LowerDesc) > 0);
            End;

            { Slow path, opt-in only via search_type='parameters'. Loads the }
            { live component and walks every parameter's name/value, that's }
            { what makes parameter-search expensive. }
            If (Not Matched) And SearchParams And (SchLib <> Nil) Then
            Begin
                Component := LookupLibComponent(SchLib, CompName);
                If Component <> Nil Then
                Begin
                    MatchedParam := False;
                    ParamIterator := Component.SchIterator_Create;
                    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                    Try
                        Param := ParamIterator.FirstSchObject;
                        While (Param <> Nil) And (Not MatchedParam) Do
                        Begin
                            If (Pos(LowerQuery, LowerCase(Param.Name)) > 0)
                                Or (Pos(LowerQuery, LowerCase(Param.Text)) > 0) Then
                                MatchedParam := True;
                            Param := ParamIterator.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ParamIterator);
                    End;
                    Matched := MatchedParam;
                End;
            End;

            If Matched Then
            Begin
                If Not First Then ResultsJson := ResultsJson + ',';
                First := False;
                ResultsJson := ResultsJson +
                    '{"name":"' + EscapeJsonString(CompName) +
                    '","alias_name":"' + EscapeJsonString(AliasName) +
                    '","description":"' + EscapeJsonString(Description) +
                    '","library_path":"' + EscapeJsonString(LibPath) +
                    '","part_count":' + IntToStr(CompInfo.PartCount) + '}';
                Inc(Count);
            End;
        End;
    Finally
        SchServer.DestroyCompInfoReader(LibReader);
    End;

    Result := True;
End;

Function Lib_Search(Params : String; RequestId : String) : String;
Var
    Query, SearchType, LibPathFilter : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    FocusedSchLib : ISch_Lib;
    DocPath, ResultsJson : String;
    I, J, Count, Limit : Integer;
    First, IsLib, SearchParams : Boolean;
Begin
    Query := ExtractJsonValue(Params, 'query');
    SearchType := ExtractJsonValue(Params, 'search_type');
    LibPathFilter := ExtractJsonValue(Params, 'library_path');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 100);

    If SearchType = '' Then SearchType := 'all';
    SearchParams := SearchType = 'parameters';

    If Query = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'query is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Parameter searches need the live component, which only the focused }
    { library exposes. Cache the focused SchLib so SearchOneLibrary can  }
    { pass it through without re-resolving on every match attempt.       }
    FocusedSchLib := Nil;
    If SearchParams Then
    Begin
        Try
            If (SchServer.GetCurrentSchDocument <> Nil)
                And (SchServer.GetCurrentSchDocument.ObjectId = eSchLib) Then
                FocusedSchLib := SchServer.GetCurrentSchDocument;
        Except End;
    End;

    ResultsJson := '';
    First := True;
    Count := 0;

    { Single-library mode short-circuits the workspace walk. }
    If LibPathFilter <> '' Then
        SearchOneLibrary(LibPathFilter, Query, SearchType, SearchParams,
            FocusedSchLib, ResultsJson, First, Count, Limit)
    Else
    Begin
        For I := 0 To Workspace.DM_ProjectCount - 1 Do
        Begin
            If Count >= Limit Then Break;
            Project := Workspace.DM_Projects(I);
            If Project = Nil Then Continue;
            For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                If Count >= Limit Then Break;
                Doc := Project.DM_LogicalDocuments(J);
                If Doc = Nil Then Continue;
                IsLib := False;
                Try
                    DocPath := Doc.DM_FullPath;
                    IsLib := (UpperCase(Doc.DM_DocumentKind) = 'SCHLIB')
                        Or (Pos('.SCHLIB', UpperCase(DocPath)) > 0);
                Except End;
                If Not IsLib Then Continue;
                SearchOneLibrary(DocPath, Query, SearchType, SearchParams,
                    FocusedSchLib, ResultsJson, First, Count, Limit);
            End;
        End;

        { Free documents (libraries opened standalone, not in any project) }
        Try
            Project := Workspace.DM_FreeDocumentsProject;
            If Project <> Nil Then
            Begin
                For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    If Count >= Limit Then Break;
                    Doc := Project.DM_LogicalDocuments(J);
                    If Doc = Nil Then Continue;
                    IsLib := False;
                    Try
                        DocPath := Doc.DM_FullPath;
                        IsLib := (UpperCase(Doc.DM_DocumentKind) = 'SCHLIB')
                            Or (Pos('.SCHLIB', UpperCase(DocPath)) > 0);
                    Except End;
                    If Not IsLib Then Continue;
                    SearchOneLibrary(DocPath, Query, SearchType, SearchParams,
                        FocusedSchLib, ResultsJson, First, Count, Limit);
                End;
            End;
        Except End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"query":"' + EscapeJsonString(Query) +
        '","search_type":"' + EscapeJsonString(SearchType) +
        '","count":' + IntToStr(Count) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Count >= Limit) +
        ',"results":[' + ResultsJson + ']}');
End;

{ Lib_GetComponentDetails - full inspection of one library component.        }
{ Returns metadata (name, description, part_count, alias_name) PLUS pins,    }
{ parameters, and full visual-style records for the designator, the comment, }
{ and every parameter (font_id, color, is_hidden, x, y, orientation,        }
{ justification). FontId can be expanded into a (name, size, bold, italic)  }
{ record by calling get_font_spec; we pass it through as an integer here    }
{ here so the cost stays on the caller when style detail isn't needed.       }
{                                                                              }
{ Pins/parameters require loading the live ISch_Component, which only the    }
{ SchLib editor can produce, so the target library must be the focused       }
{ SchServer document. If the caller passed a library_path that doesn't       }
{ match the focused doc, we open it via WorkspaceManager:OpenObject before   }
{ resolving. Saves are deferred (see MarkLibDirty), so opening doesn't       }
{ disturb in-flight edits on other libs.                                     }
{                                                                              }
{ Schema breaks vs the previous version (introduced two commits ago):        }
{   designator_prefix (str) -> designator (object: text, font_id, color,    }
{                              is_hidden, x, y, orientation, justification) }
{   pins[].font_id, color, label_hidden added                                }
{   comment (object) added                                                   }
{   parameter_styles (array, parallel to parameters dict) added              }

{ BuildLabelStyleJson reads visual-style props off any ISch_Label-derived    }
{ object (Designator, Comment, Parameter, NetLabel, ...) using late-bound   }
{ accessors. Each access is wrapped in Try/Except since not every property  }
{ is present on every ISch_Label subtype, and DelphiScript fails at runtime }
{ rather than compile time on a missing late-bound property.                 }
Function BuildLabelStyleJson(Lbl : ISch_Label; IncludeText : Boolean) : String;
Var
    Txt : String;
    FontId, ColorVal, OrientVal, JustVal, LocX, LocY : Integer;
    HiddenVal : Boolean;
Begin
    Txt := '';
    FontId := 0;
    ColorVal := 0;
    OrientVal := 0;
    JustVal := 0;
    LocX := 0;
    LocY := 0;
    HiddenVal := False;
    Try Txt := Lbl.Text; Except End;
    Try FontId := Lbl.FontId; Except End;
    Try ColorVal := Lbl.Color; Except End;
    Try HiddenVal := Lbl.IsHidden; Except End;
    Try LocX := CoordToMils(Lbl.Location.X); Except End;
    Try LocY := CoordToMils(Lbl.Location.Y); Except End;
    Try OrientVal := Lbl.Orientation; Except End;
    Try JustVal := Lbl.Justification; Except End;

    Result := '{';
    If IncludeText Then
        Result := Result + '"text":"' + EscapeJsonString(Txt) + '",';
    Result := Result +
        '"font_id":' + IntToStr(FontId) +
        ',"color":' + IntToStr(ColorVal) +
        ',"is_hidden":' + BoolToJsonStr(HiddenVal) +
        ',"x":' + IntToStr(LocX) +
        ',"y":' + IntToStr(LocY) +
        ',"orientation":' + IntToStr(OrientVal) +
        ',"justification":' + IntToStr(JustVal) + '}';
End;

{..............................................................................}
{ ResolveLibComponent - fetch a live ISch_Component from a focused SchLib by   }
{ name OR by a name-free integer index.                                        }
{                                                                              }
{ Why the index exists: every by-name fetch funnels through                    }
{ GetState_SchComponentByLibRef, an exact-string lookup, and there is no       }
{ positional accessor in the SchLib API. A LibReference that carries bytes a   }
{ caller cannot reproduce (an embedded '"', or a control char like #16/#17/#19 }
{ left by a broken import) is therefore unreachable, the exact name never      }
{ survives the round-trip back through the caller. component_index (>= 0) is   }
{ the position in ILibCompInfoReader order, the SAME order lib_get_components   }
{ emits. We read Altium's OWN copy of the name at that position and fetch by    }
{ it, so the odd bytes stay server-side. If the direct fetch still misses (an  }
{ Altium hash quirk on the odd bytes), fall back to a SchLibIterator walk with }
{ a byte-exact LibReference compare (Pascal string equality is control-char    }
{ safe). ResolvedName echoes the name we landed on so the caller can confirm   }
{ which entry index N was. On failure returns Nil and sets ErrCode/ErrMsg.     }
Function ResolveLibComponent(SchLib : ISch_Lib; LibPath, Params : String;
    Var ResolvedName : String; Var ErrCode : String; Var ErrMsg : String) : ISch_Component;
Var
    IdxStr, WantName : String;
    Idx, CompNum : Integer;
    LibReader : ILibCompInfoReader;
    Iter : ISch_Iterator;
    LibComp : ISch_Component;
Begin
    Result := Nil;
    ResolvedName := '';
    ErrCode := '';
    ErrMsg := '';

    IdxStr := ExtractJsonValue(Params, 'component_index');
    If IdxStr <> '' Then
    Begin
        If Not IsIntStr(IdxStr) Then
        Begin
            ErrCode := 'BAD_INDEX';
            ErrMsg := 'component_index must be a non-negative integer, got: ' + IdxStr;
            Exit;
        End;
        Idx := StrToIntDef(IdxStr, -1);
        If Idx < 0 Then
        Begin
            ErrCode := 'BAD_INDEX';
            ErrMsg := 'component_index must be >= 0';
            Exit;
        End;

        { Read Altium's own copy of the name at this position. }
        WantName := '';
        { No file on disk: no position exists, the reply an empty reader    }
        { gives.                                                             }
        If LibFileMissing(LibPath) Then
        Begin
            CompNum := 0;
            ErrCode := 'INDEX_OUT_OF_RANGE';
            ErrMsg := 'component_index ' + IntToStr(Idx) + ' out of range (0..'
                + IntToStr(CompNum - 1) + ')';
            Exit;
        End;
        LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
        If LibReader = Nil Then
        Begin
            ErrCode := 'READER_FAILED';
            ErrMsg := 'Failed to create library reader for: ' + LibPath;
            Exit;
        End;
        Try
            LibReader.ReadAllComponentInfo;
            CompNum := LibReader.NumComponentInfos;
            If Idx >= CompNum Then
            Begin
                ErrCode := 'INDEX_OUT_OF_RANGE';
                ErrMsg := 'component_index ' + IntToStr(Idx) + ' out of range (0..'
                    + IntToStr(CompNum - 1) + ')';
            End
            Else
                WantName := LibReader.ComponentInfos[Idx].CompName;
        Finally
            SchServer.DestroyCompInfoReader(LibReader);
        End;
        If ErrCode <> '' Then Exit;

        ResolvedName := WantName;
        { Fast path: feed Altium's exact bytes straight back. }
        Result := LookupLibComponent(SchLib, WantName);
        If Result <> Nil Then Exit;

        { Fallback: iterate live symbols, byte-exact LibReference compare. }
        Iter := SchLib.SchLibIterator_Create;
        If Iter <> Nil Then
        Begin
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                LibComp := Iter.FirstSchObject;
                While LibComp <> Nil Do
                Begin
                    If LibComp.LibReference = WantName Then
                    Begin
                        Result := LibComp;
                        Break;
                    End;
                    LibComp := Iter.NextSchObject;
                End;
            Finally
                SchLib.SchIterator_Destroy(Iter);
            End;
        End;
        If Result = Nil Then
        Begin
            ErrCode := 'COMPONENT_NOT_FOUND';
            ErrMsg := 'Component at index ' + IntToStr(Idx) + ' ("' + WantName
                + '") could not be loaded from ' + LibPath;
        End;
        Exit;
    End;

    { By-name path (default). }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
    Begin
        ErrCode := 'MISSING_PARAMS';
        ErrMsg := 'Provide component_name, or component_index for names with '
            + 'unreproducible bytes (quotes / control chars).';
        Exit;
    End;
    ResolvedName := WantName;
    Result := LookupLibComponent(SchLib, WantName);
    If Result = Nil Then
    Begin
        ErrCode := 'COMPONENT_NOT_FOUND';
        ErrMsg := 'Component not found in library: ' + WantName;
    End;
End;

Function Lib_GetComponentDetails(Params : String; RequestId : String) : String;
Var
    KindOrd : Integer;
    ComponentName, LibPath, FocusedPath : String;
    ResErrCode, ResErrMsg : String;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    PinIterator, ParamIterator : ISch_Iterator;
    Pin : ISch_Pin;
    Param : ISch_Parameter;
    CompNum, I, PinCount : Integer;
    Data, PinList, ParamList, StyleList, ElecStr : String;
    DesignatorJson, CommentJson, Description, AliasName : String;
    PartCount : Integer;
    PinLabelHidden : Boolean;
    First, FirstStyle, FoundInfo : Boolean;
Begin
    ComponentName := ExtractJsonValue(Params, 'component_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    If (ComponentName = '') And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Resolve the focused doc's path so we know whether to reopen. }
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
        Try FocusedPath := Doc.DM_FullPath; Except End;

    If LibPath = '' Then
        LibPath := FocusedPath;

    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    { Bring the requested library into focus when it isn't already. }
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    { Resolve the live symbol by name OR index. Index reaches components   }
    { whose LibReference carries unreproducible bytes. On success this sets }
    { ComponentName to Altium's own copy of the name, which the metadata    }
    { reader loop below then matches byte-for-byte.                          }
    Component := ResolveLibComponent(SchLib, LibPath, Params, ComponentName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    { Cheap metadata lookup via CompInfoReader: the live component carries }
    { LibReference / ComponentDescription too, but PartCount is on the    }
    { reader's IComponentInfo and not on ISch_Component, so we fetch it   }
    { here. }
    Description := '';
    AliasName := '';
    PartCount := 1;
    FoundInfo := False;
    { No file on disk (the normal read-back right after authoring an unsaved }
    { library): the no-reader route, description from the live component.   }
    LibReader := Nil;
    If Not LibFileMissing(LibPath) Then
        LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
    If LibReader <> Nil Then
    Begin
        Try
            LibReader.ReadAllComponentInfo;
            CompNum := LibReader.NumComponentInfos;
            For I := 0 To CompNum - 1 Do
            Begin
                CompInfo := LibReader.ComponentInfos[I];
                If CompInfo.CompName = ComponentName Then
                Begin
                    Try Description := CompInfo.Description; Except End;
                    Try AliasName := CompInfo.AliasName; Except End;
                    Try PartCount := CompInfo.PartCount; Except End;
                    FoundInfo := True;
                    Break;
                End;
            End;
        Finally
            SchServer.DestroyCompInfoReader(LibReader);
        End;
    End;

    { Component already resolved above (by name or index). }
    If Description = '' Then
        Try Description := Component.ComponentDescription; Except End;
    { Component Type, read with the same member Gen_SetNetTie writes. The
      raw ordinal rides beside the name so a reader can check the name
      table against what Altium's Properties panel shows. }
    KindOrd := -1;
    Try KindOrd := Component.ComponentKind; Except End;

    { Designator + Comment full-style records. The sub-objects ARE        }
    { ISch_Label-derived so they expose Text + FontId + Color + IsHidden  }
    { + Location + Orientation + Justification.                            }
    DesignatorJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
    Try DesignatorJson := BuildLabelStyleJson(Component.Designator, True); Except End;
    CommentJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
    Try CommentJson := BuildLabelStyleJson(Component.Comment, True); Except End;

    { Pin list. font_id / color come from each pin's own ISch_Pin object  }
    { (it inherits from ISch_GraphicalObject which carries both); pin     }
    { name and pin number share that font/color, separate-font handling   }
    { is not exposed cleanly from DelphiScript. label_hidden is the visual}
    { hide-pin-label flag, distinct from pin.IsHidden which hides the pin }
    { from the canvas entirely.                                            }
    PinList := '';
    First := True;
    PinCount := 0;
    PinIterator := Component.SchIterator_Create;
    PinIterator.AddFilter_ObjectSet(MkSet(ePin));
    Try
        Pin := PinIterator.FirstSchObject;
        While Pin <> Nil Do
        Begin
            If Not First Then PinList := PinList + ',';
            First := False;

            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
            Else ElecStr := 'passive';

            { Pin label visibility: ISch_Pin.ShowName / ShowDesignator are }
            { the real flags; combine into a single label_hidden when both }
            { are off so the LLM can flag "neither pin name nor number is }
            { drawn". font_id / color are NOT exposed on ISch_Pin in the   }
            { Schematic API at all (only on the ISch_Label family), so we }
            { intentionally omit them from pins[] rather than fake zeros. }
            PinLabelHidden := False;
            Try PinLabelHidden := (Not Pin.ShowName) And (Not Pin.ShowDesignator); Except End;

            PinList := PinList + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                '","name":"' + EscapeJsonString(Pin.Name) +
                '","electrical_type":"' + ElecStr +
                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                ',"orientation":' + IntToStr(Pin.Orientation) +
                ',"length":' + IntToStr(CoordToMils(Pin.PinLength)) +
                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                ',"label_hidden":' + BoolToJsonStr(PinLabelHidden) + '}';
            Inc(PinCount);

            Pin := PinIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(PinIterator);
    End;

    { Parameter dict (cheap lookups) plus parameter_styles array (visual). }
    { We iterate parameters once and build both shapes in lockstep so the  }
    { kth entry of parameter_styles always matches the kth iteration order.}
    ParamList := '';
    StyleList := '';
    First := True;
    FirstStyle := True;
    ParamIterator := Component.SchIterator_Create;
    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
    Try
        Param := ParamIterator.FirstSchObject;
        While Param <> Nil Do
        Begin
            If Not First Then ParamList := ParamList + ',';
            First := False;
            ParamList := ParamList + '"' + EscapeJsonString(Param.Name) +
                '":"' + EscapeJsonString(Param.Text) + '"';

            If Not FirstStyle Then StyleList := StyleList + ',';
            FirstStyle := False;
            StyleList := StyleList + '{"name":"' + EscapeJsonString(Param.Name) +
                '","value":"' + EscapeJsonString(Param.Text) + '","style":' +
                BuildLabelStyleJson(Param, False) + '}';

            Param := ParamIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ParamIterator);
    End;

    Data := '{"name":"' + EscapeJsonString(ComponentName) + '"';
    Data := Data + ',"library_path":"' + EscapeJsonString(LibPath) + '"';
    Data := Data + ',"designator":' + DesignatorJson;
    Data := Data + ',"comment":' + CommentJson;
    Data := Data + ',"description":"' + EscapeJsonString(Description) + '"';
    Data := Data + ',"component_kind":"' + ComponentKindName(KindOrd) + '"';
    Data := Data + ',"component_kind_ordinal":' + IntToStr(KindOrd);
    Data := Data + ',"alias_name":"' + EscapeJsonString(AliasName) + '"';
    Data := Data + ',"part_count":' + IntToStr(PartCount);
    Data := Data + ',"pin_count":' + IntToStr(PinCount);
    Data := Data + ',"pins":[' + PinList + ']';
    Data := Data + ',"parameters":{' + ParamList + '}';
    Data := Data + ',"parameter_styles":[' + StyleList + ']';
    Data := Data + ',"models":' + BuildImplementationsJson(Component) + '}';

    Result := BuildSuccessResponse(RequestId, Data);
End;

Function Lib_BatchSetParams(Params : String; RequestId : String) : String;
Var
    LibPath, BatchPath : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    NewParam : ISch_Parameter;
    FoundParam : ISch_Parameter;
    Workspace : IWorkspace;
    WDoc : IDocument;
    F : TextFile;
    Line, CompName, ParamName, ParamValue, FailReasons : String;
    PipePos1, PipePos2 : Integer;
    Updated, Created, Failed, LineNum : Integer;
Begin
    FailReasons := '';
    LibPath := ExtractJsonValue(Params, 'library_path');
    BatchPath := ExtractJsonValue(Params, 'batch_file');

    If BatchPath = '' Then
        BatchPath := WorkspaceDir + 'batch_params.txt';

    // Get library path from focused document if not provided
    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            WDoc := Workspace.DM_FocusedDocument;
            If WDoc <> Nil Then
                LibPath := WDoc.DM_FileName;
        End;
    End;

    // Open the library to make it the current SchServer document
    If LibPath <> '' Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    If Not FileExists(BatchPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BATCH_FILE', 'Batch file not found: ' + BatchPath);
        Exit;
    End;

    Updated := 0;
    Created := 0;
    Failed := 0;
    LineNum := 0;

    // Begin modification block for undo support
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        AssignFile(F, BatchPath);
        Reset(F);
        Try
            While Not EOF(F) Do
            Begin
                ReadLn(F, Line);
                Inc(LineNum);

                If Line = '' Then Continue;

                // Parse: CompName|ParamName|ParamValue
                PipePos1 := Pos('|', Line);
                If PipePos1 = 0 Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, Line, 'no | separator on the line');
                    Continue;
                End;
                CompName := Trim(Copy(Line, 1, PipePos1 - 1));
                Line := Copy(Line, PipePos1 + 1, Length(Line));
                PipePos2 := Pos('|', Line);
                If PipePos2 = 0 Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, CompName,
                        'the line has only one | separator, so there is no value');
                    Continue;
                End;
                ParamName := Trim(Copy(Line, 1, PipePos2 - 1));
                { ParamValue is the LAST field, which is exactly where a CRLF }
                { line ending left a stray carriage return. Without this trim }
                { the parameter was written with a CR glued to its value.     }
                ParamValue := Trim(Copy(Line, PipePos2 + 1, Length(Line)));

                Component := LookupLibComponent(SchLib, CompName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, CompName,
                        'no component with that library reference');
                    Continue;
                End;

                // Special case: Description is a component property, not a parameter
                If ParamName = 'Description' Then
                Begin
                    Component.ComponentDescription := ParamValue;
                    Inc(Updated);
                    Continue;
                End;

                // Special case: Designator is the component's DEFAULT
                // designator (Component.Designator.Text, a property on the
                // designator label sub-object) -- NOT a parameter. Mirrors
                // the Description case. Lets library designator audits
                // normalize defaults (e.g. "IC?" / "U3" -> "U?") through the
                // existing batch tool without a dedicated handler.
                If ParamName = 'Designator' Then
                Begin
                    If Component.Designator <> Nil Then
                    Begin
                        SchBeginModify(Component.Designator);
                        Component.Designator.Text := ParamValue;
                        SchEndModify(Component.Designator);
                        Inc(Updated);
                    End
                    Else
                        Inc(Failed);
                    Continue;
                End;

                // Find existing parameter
                FoundParam := Nil;
                ParamIterator := Component.SchIterator_Create;
                ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                Param := ParamIterator.FirstSchObject;
                While Param <> Nil Do
                Begin
                    If Param.Name = ParamName Then
                    Begin
                        FoundParam := Param;
                        Break;
                    End;
                    Param := ParamIterator.NextSchObject;
                End;
                Component.SchIterator_Destroy(ParamIterator);

                If FoundParam <> Nil Then
                Begin
                    SchBeginModify(FoundParam);
                    FoundParam.Text := ParamValue;
                    SchEndModify(FoundParam);
                    Inc(Updated);
                End
                Else
                Begin
                    NewParam := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    If NewParam <> Nil Then
                    Begin
                        NewParam.Name := ParamName;
                        NewParam.Text := ParamValue;
                        SetOwnerPart(NewParam, Component);
                        Component.AddSchObject(NewParam);
                        SchRegisterObject(Component, NewParam);
                        Inc(Created);
                    End
                    Else
                        Inc(Failed);
                End;
            End;
        Finally
            CloseFile(F);
        End;
    Finally
        // End modification block - commit changes
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);
    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"created":' + IntToStr(Created) +
        ',"failed":' + IntToStr(Failed) +
        ',"total_lines":' + IntToStr(LineNum) +
        ',"failures":[' + FailReasons + ']}');
End;

{..............................................................................}
{ Batch Rename Components                                                      }
{..............................................................................}

Function Lib_BatchRename(Params : String; RequestId : String) : String;
Var
    LibPath, BatchPath : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Workspace : IWorkspace;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    F : TextFile;
    Line, OldName, NewName, FailReasons : String;
    PipePos : Integer;
    Renamed, Failed, LineNum : Integer;
Begin
    FailReasons := '';
    LibPath := ExtractJsonValue(Params, 'library_path');
    BatchPath := ExtractJsonValue(Params, 'batch_file');
    If BatchPath = '' Then
        BatchPath := WorkspaceDir + 'batch_rename.txt';

    // Get library path from parameter or focused document
    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then
                LibPath := Doc.DM_FileName;
        End;
    End;

    // Focus the library document to make it the current SchServer document
    If LibPath <> '' Then
    Begin
        ServerDoc := Client.GetDocumentByPath(LibPath);
        If ServerDoc <> Nil Then
            Client.ShowDocument(ServerDoc)
        Else
        Begin
            // Not yet open, open it
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', LibPath);
            RunProcess('WorkspaceManager:OpenObject');
        End;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active. Provide library_path parameter.');
        Exit;
    End;

    If Not FileExists(BatchPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BATCH_FILE', 'Batch file not found: ' + BatchPath);
        Exit;
    End;

    Renamed := 0;
    Failed := 0;
    LineNum := 0;

    // Begin modification block
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        AssignFile(F, BatchPath);
        Reset(F);
        Try
            While Not EOF(F) Do
            Begin
                ReadLn(F, Line);
                Inc(LineNum);

                If Line = '' Then Continue;

                // Parse: OldName|NewName
                PipePos := Pos('|', Line);
                If PipePos = 0 Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, Line, 'no | separator on the line');
                    Continue;
                End;
                { Trim both. The batch file is written from Python and used to }
                { arrive CRLF-terminated, so ReadLn left the carriage return   }
                { glued to the LAST field: the rename then set a LibReference  }
                { ending in CR. Python no longer translates, and this trims    }
                { anyway, because a writer is easy to change back by accident. }
                OldName := Trim(Copy(Line, 1, PipePos - 1));
                NewName := Trim(Copy(Line, PipePos + 1, Length(Line)));

                If NewName = '' Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, OldName, 'the new name is empty');
                    Continue;
                End;

                Component := LookupLibComponent(SchLib, OldName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, OldName,
                        'no component with that library reference');
                    Continue;
                End;

                // Must remove and re-add to update the library's internal index
                SchLib.RemoveSchComponent(Component);
                Component.LibReference := NewName;
                SchLib.AddSchComponent(Component);
                { The held component survives a rename under its NEW name:   }
                { matching it by the old one would hand a renamed symbol     }
                { back for a name it no longer carries. Only one held in    }
                { THIS library and still live is compared at all (see       }
                { ForgetLibComponentIf for why the order matters).           }
                If (LastCreatedLibComponent <> Nil)
                    And SameLibAsRemembered(SchLib)
                    And LastCreatedLibIsLive(0)
                    And ((Component = LastCreatedLibComponent)
                        Or (OldName = LastCreatedLibComponentName)) Then
                    LastCreatedLibComponentName := NewName;
                Inc(Renamed);
            End;
        Finally
            CloseFile(F);
        End;
    Finally
        // End modification block - commit changes
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    SchLib.GraphicallyInvalidate;
    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"renamed":' + IntToStr(Renamed) +
        ',"failed":' + IntToStr(Failed) +
        ',"total_lines":' + IntToStr(LineNum) +
        ',"failures":[' + FailReasons + ']}');
End;

{..............................................................................}
{ Diff two SchLib files, reports components only in A, only in B, or both   }
{..............................................................................}

Function Lib_DiffLibraries(Params : String; RequestId : String) : String;
Var
    PathA, PathB : String;
    ReaderA, ReaderB : ILibCompInfoReader;
    NumA, NumB, I, J : Integer;
    NameA : String;
    FoundInB : Boolean;
    OnlyA, OnlyB, Common : String;
    CountA, CountB, CountCommon : Integer;
    First : Boolean;
Begin
    PathA := ExtractJsonValue(Params, 'library_a');
    PathB := ExtractJsonValue(Params, 'library_b');

    If (PathA = '') Or (PathB = '') Then
    Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'library_a and library_b are required'); Exit; End;

    ReaderA := SchServer.CreateLibCompInfoReader(SafeSchLibPath(PathA));
    If ReaderA = Nil Then Begin Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Cannot read library A'); Exit; End;
    ReaderA.ReadAllComponentInfo;
    NumA := ReaderA.NumComponentInfos;

    ReaderB := SchServer.CreateLibCompInfoReader(SafeSchLibPath(PathB));
    If ReaderB = Nil Then
    Begin
        SchServer.DestroyCompInfoReader(ReaderA);
        Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Cannot read library B');
        Exit;
    End;
    ReaderB.ReadAllComponentInfo;
    NumB := ReaderB.NumComponentInfos;

    OnlyA := '';  CountA := 0;
    OnlyB := '';  CountB := 0;
    Common := ''; CountCommon := 0;

    // Find components in A: check if each exists in B
    For I := 0 To NumA - 1 Do
    Begin
        NameA := ReaderA.ComponentInfos[I].CompName;
        FoundInB := False;
        For J := 0 To NumB - 1 Do
        Begin
            If ReaderB.ComponentInfos[J].CompName = NameA Then Begin FoundInB := True; Break; End;
        End;
        If FoundInB Then
        Begin
            If CountCommon > 0 Then Common := Common + ',';
            Common := Common + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountCommon);
        End
        Else
        Begin
            If CountA > 0 Then OnlyA := OnlyA + ',';
            OnlyA := OnlyA + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountA);
        End;
    End;

    // Find components only in B
    For I := 0 To NumB - 1 Do
    Begin
        NameA := ReaderB.ComponentInfos[I].CompName;
        FoundInB := False;
        For J := 0 To NumA - 1 Do
        Begin
            If ReaderA.ComponentInfos[J].CompName = NameA Then Begin FoundInB := True; Break; End;
        End;
        If Not FoundInB Then
        Begin
            If CountB > 0 Then OnlyB := OnlyB + ',';
            OnlyB := OnlyB + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountB);
        End;
    End;

    SchServer.DestroyCompInfoReader(ReaderA);
    SchServer.DestroyCompInfoReader(ReaderB);

    Result := BuildSuccessResponse(RequestId,
        '{"only_in_a":[' + OnlyA + '],"only_in_b":[' + OnlyB + '],"common":[' + Common + ']' +
        ',"count_a":' + IntToStr(NumA) + ',"count_b":' + IntToStr(NumB) +
        ',"only_a":' + IntToStr(CountA) + ',"only_b":' + IntToStr(CountB) +
        ',"shared":' + IntToStr(CountCommon) + '}');
End;

{..............................................................................}
{ Add an arc to the current library symbol                                    }
{ Params: x_center, y_center, radius, start_angle, end_angle, width          }
{..............................................................................}

Function Lib_AddSymbolArc(Params : String; RequestId : String) : String;
Var
    XCenter, YCenter, Radius, Width : Integer;
    { Same defect as Lib_AddFootprintArc: `float` on the Python side, so    }
    { StrToIntDef never read the wire value and every symbol arc was drawn  }
    { with a zero sweep.                                                    }
    StartAngle, EndAngle : Double;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Arc : ISch_Arc;
Begin
    XCenter := StrToIntDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToIntDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    StartAngle := StrToFloatDef(ExtractJsonValue(Params, 'start_angle'), 0.0);
    EndAngle := StrToFloatDef(ExtractJsonValue(Params, 'end_angle'), 360.0);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1);
    If Width < 0 Then Width := 0;
    If Width > 3 Then Width := 3;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Arc := SchServer.SchObjectFactory(eArc, eCreate_Default);
    If Arc <> Nil Then
    Begin
        Arc.Location := Point(MilsToCoord(XCenter), MilsToCoord(YCenter));
        Arc.Radius := MilsToCoord(Radius);
        Arc.StartAngle := StartAngle;
        Arc.EndAngle := EndAngle;
        Arc.LineWidth := Width;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Arc, Component);
        Component.AddSchObject(Arc);
        SchRegisterObject(Component, Arc);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc');
End;

{..............................................................................}
{ Add a polygon (filled shape) to the current library symbol                  }
{ Params: vertices (comma-separated x,y pairs: "x1,y1,x2,y2,x3,y3,...")     }
{..............................................................................}

Function Lib_AddSymbolPolygon(Params : String; RequestId : String) : String;
Var
    VerticesStr, Token : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Polygon : ISch_Polygon;
    Remaining : String;
    CommaPos, X, Y, I : Integer;
    { Parallel TStringLists of stringified coords. Fixed-size local arrays }
    { of any type corrupt the return slot, see                              }
    { [[delphiscript_fixed_string_array_bug]] - originally documented for  }
    { Array of String, now confirmed for Array of Integer/Double too.      }
    XValues, YValues : TStringList;
Begin
    VerticesStr := ExtractJsonValue(Params, 'vertices');

    If VerticesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'vertices parameter is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    XValues := TStringList.Create;
    YValues := TStringList.Create;
    Try
        Remaining := VerticesStr;
        While Remaining <> '' Do
        Begin
            CommaPos := Pos(',', Remaining);
            If CommaPos = 0 Then Break;
            Token := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
            X := StrToIntDef(Token, 0);

            CommaPos := Pos(',', Remaining);
            If CommaPos > 0 Then
            Begin
                Token := Copy(Remaining, 1, CommaPos - 1);
                Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
            End
            Else
            Begin
                Token := Remaining;
                Remaining := '';
            End;
            Y := StrToIntDef(Token, 0);

            XValues.Add(IntToStr(X));
            YValues.Add(IntToStr(Y));
        End;

        If XValues.Count < 3 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS', 'At least 3 vertices are required');
            Exit;
        End;

        Polygon := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
        If Polygon <> Nil Then
        Begin
            Polygon.VerticesCount := XValues.Count;
            Polygon.IsSolid := True;
            Polygon.LineWidth := eSmall;

            For I := 1 To XValues.Count Do
                Polygon.Vertex[I] := Point(
                    MilsToCoord(StrToIntDef(XValues[I-1], 0)),
                    MilsToCoord(StrToIntDef(YValues[I-1], 0)));

            SchServer.ProcessControl.PreProcess(SchLib, '');
            SetOwnerPart(Polygon, Component);
            Component.AddSchObject(Polygon);
            SchRegisterObject(Component, Polygon);
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

            MarkLibDirty(SchLib);
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"vertices":' + IntToStr(XValues.Count) + '}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create polygon');
    Finally
        YValues.Free;
        XValues.Free;
    End;
End;

{..............................................................................}
{ Set the description field on a library component                            }
{ Params: component_name, description                                         }
{..............................................................................}

Function Lib_SetComponentDescription(Params : String; RequestId : String) : String;
Var
    CompName, Description : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
Begin
    CompName := ExtractJsonValue(Params, 'component_name');
    Description := ExtractJsonValue(Params, 'description');

    If CompName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name parameter is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found: ' + CompName);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    SchBeginModify(Component);
    Component.ComponentDescription := Description;
    SchEndModify(Component);
    SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

    MarkLibDirty(SchLib);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"component":"' + EscapeJsonString(CompName) +
        '","description":"' + EscapeJsonString(Description) + '"}');
End;

{..............................................................................}
{ Lib_SetComponentKind - set a LIBRARY symbol's Component Type.                }
{                                                                              }
{ The placed-part twin is Gen_SetNetTie, which writes                          }
{ ISch_Component.ComponentKind on a component found on the active sheet. A     }
{ library symbol is the same interface reached through the library, so the    }
{ write is the same and only the lookup differs. Before this the only route   }
{ to a net-tie symbol was to place it and convert each instance, which left   }
{ the library saying Standard and every new placement wrong.                  }
{                                                                              }
{ Read back after the write and refused when the value did not stick, so a     }
{ success is the property as Altium holds it, not the request echoed.        }
{                                                                              }
{ Params: component_name (required), kind (required: standard, mechanical,    }
{         graphical, net_tie_bom, net_tie_no_bom, standard_no_bom, jumper, or  }
{         an ordinal 0..6), library_path (optional).                           }
{..............................................................................}
Function Lib_SetComponentKind(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, KindStr, Details : String;
    SchLib, Fresh : ISch_Lib;
    Component : ISch_Component;
    Want, Before, After, AnchorBom, AnchorNoBom : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    KindStr := ExtractJsonValue(Params, 'kind');
    If (CompName = '') Or (KindStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'component_name and kind are required');
        Exit;
    End;
    Want := ComponentKindOrdinal(KindStr);
    If Want < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_KIND',
            'Unknown component kind "' + KindStr + '". Use standard, '
            + 'mechanical, graphical, net_tie_bom, net_tie_no_bom, '
            + 'standard_no_bom or jumper.');
        Exit;
    End;

    { The two kinds written by NAME anchor the five written by number. If
      this build numbers the enum differently, the anchors move and the
      numbered kinds are refused rather than written as something else. }
    AnchorBom := eComponentKind_NetTie_BOM;
    AnchorNoBom := eComponentKind_NetTie_NoBOM;
    If ((AnchorBom <> 3) Or (AnchorNoBom <> 4)) And (Want <> 3) And (Want <> 4) Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'ENUM_LAYOUT_MISMATCH',
            'This Altium build numbers the net-tie kinds differently from the '
            + 'order the other kinds are written in, so '
            + ComponentKindName(Want) + ' was not written. The two net-tie '
            + 'kinds still work.',
            '{"net_tie_bom":' + IntToStr(AnchorBom)
            + ',"net_tie_no_bom":' + IntToStr(AnchorNoBom) + '}');
        Exit;
    End;

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;
    Fresh := RefetchSchLib(LibPath);
    If Fresh <> Nil Then SchLib := Fresh;

    Before := -1;
    Try Before := Component.ComponentKind; Except End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        SchBeginModify(Component);
        If Want = 3 Then Component.ComponentKind := eComponentKind_NetTie_BOM
        Else If Want = 4 Then Component.ComponentKind := eComponentKind_NetTie_NoBOM
        Else Component.ComponentKind := Want;
        SchEndModify(Component);
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Set component kind');
    End;

    After := -1;
    Try After := Component.ComponentKind; Except End;
    MarkLibDirty(SchLib);

    Details := '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"kind_requested":"' + ComponentKindName(Want) + '"'
        + ',"kind_before":"' + ComponentKindName(Before) + '"'
        + ',"kind_after":"' + ComponentKindName(After) + '"'
        + ',"ordinal_after":' + IntToStr(After) + '}';
    If After <> Want Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'KIND_NOT_APPLIED',
            'ComponentKind reads back as ' + ComponentKindName(After)
            + ' after writing ' + ComponentKindName(Want) + '.', Details);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, Details);
End;

{..............................................................................}
{ Get all pins of the current library component                               }
{ Returns designator, name, electrical type, x, y for each pin               }
{..............................................................................}

Function Lib_GetPinList(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    PinIterator : ISch_Iterator;
    Pin : ISch_Pin;
    JsonItems, ElecStr, WantName : String;
    First : Boolean;
    PinCount : Integer;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    { Resolve THROUGH the library, never straight off the editor's current
      component. Measured on a live library: SchIterator_Create on a component
      taken from CurrentSchComponent faults with "Undeclared identifier:
      SchIterator_Create", while the identical call works on a component
      fetched by lib-ref or returned from a SchLib iterator, which is how
      every other reader in this file gets one. DelphiScript narrows an
      interface at iterator-return; a component handed over any other way
      does not carry the methods, and the failure is a late-bound one that
      Try/Except cannot catch.

      component_name also lets a caller name the symbol rather than rely on
      whatever the editor has selected, so reading a symbol's pins stops
      depending on, and stops disturbing, the current selection. }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
        WantName := TargetLibComponentName(SchLib);

    If WantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No component is selected; pass component_name to name one');
        Exit;
    End;

    Component := LookupLibComponent(SchLib, WantName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in library: ' + WantName);
        Exit;
    End;

    JsonItems := '';
    First := True;
    PinCount := 0;

    PinIterator := Component.SchIterator_Create;
    PinIterator.AddFilter_ObjectSet(MkSet(ePin));

    Try
        Pin := PinIterator.FirstSchObject;
        While Pin <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;

            // Map electrical type to string. Altium uses eElectricIO for
            // bidirectional; eElectricBiDir is undeclared.
            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
            Else ElecStr := 'passive';

            JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                '","name":"' + EscapeJsonString(Pin.Name) +
                '","electrical_type":"' + ElecStr +
                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                ',"orientation":' + IntToStr(Pin.Orientation) +
                ',"length":' + IntToStr(CoordToMils(Pin.PinLength)) +
                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                ',"owner_part_id":' + IntToStr(Pin.OwnerPartId) + '}';
            Inc(PinCount);

            Pin := PinIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(PinIterator);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(PinCount) +
        ',"component":"' + EscapeJsonString(Component.LibReference) +
        '","pins":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Duplicate a component within or BETWEEN schematic libraries.                }
{                                                                              }
{ Params:                                                                      }
{   source_name (required) - lib_ref to copy                                  }
{   new_name              - lib_ref for the clone; defaults to source_name   }
{   source_library        - .SchLib to read from; defaults to focused doc    }
{   dest_library          - .SchLib to write to; defaults to source_library  }
{                           (omit / equal -> same-library duplicate, the     }
{                           original behaviour)                                }
{   overwrite=true|false  - if a component named new_name already exists in  }
{                           the destination, replace it; default false ->    }
{                           returns NAME_EXISTS                                }
{                                                                              }
{ Replicates the source while it is focused (so the clone inherits the       }
{ source's library context), then switches focus to the destination and     }
{ AddSchComponent there. Destination ends focused with the new component     }
{ selected. Save is deferred (MarkLibDirty only) per the project's perf-      }
{ deferred-save pattern.                                                       }
{..............................................................................}

Function Lib_CopyComponent(Params : String; RequestId : String) : String;
Var
    SourceLibPath, DestLibPath, FocusedPath, SourceName, NewName : String;
    OverwriteStr, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SourceLib, DestLib : ISch_Lib;
    SourceComp, NewComp, Existing, Verify : ISch_Component;
    Overwrite, SameLib, Overwrote : Boolean;
Begin
    SourceLibPath := ExtractJsonValue(Params, 'source_library');
    DestLibPath := ExtractJsonValue(Params, 'dest_library');
    SourceName := ExtractJsonValue(Params, 'source_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');

    If SourceName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'source_name is required');
        Exit;
    End;
    If NewName = '' Then NewName := SourceName;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If SourceLibPath = '' Then SourceLibPath := FocusedPath;
    If SourceLibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and source_library was not supplied');
        Exit;
    End;

    { Focus the source library so Replicate sees it in the right context. }
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(SourceLibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', SourceLibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SourceLib := SchServer.GetCurrentSchDocument;
    If (SourceLib = Nil) Or (SourceLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus source library at ' + SourceLibPath);
        Exit;
    End;
    SourceComp := LookupLibComponent(SourceLib, SourceName);
    If SourceComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Source component not found in ' + SourceLibPath + ': ' + SourceName);
        Exit;
    End;

    SameLib := (DestLibPath = '') Or (UpperCase(DestLibPath) = UpperCase(SourceLibPath));
    If SameLib Then DestLibPath := SourceLibPath;

    { Replicate while source is focused. The clone is free-floating until    }
    { AddSchComponent attaches it to the destination library.                 }
    NewComp := SourceComp.Replicate;
    If NewComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COPY_FAILED',
            'Replicate returned Nil for ' + SourceName);
        Exit;
    End;
    NewComp.LibReference := NewName;

    If SameLib Then
        DestLib := SourceLib
    Else
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', DestLibPath);
        RunProcess('WorkspaceManager:OpenObject');
        DestLib := SchServer.GetCurrentSchDocument;
        If (DestLib = Nil) Or (DestLib.ObjectId <> eSchLib) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                'Failed to focus destination library at ' + DestLibPath);
            Exit;
        End;
    End;

    Overwrote := False;
    { In memory only: a miss is the usual answer, and the reopening lookup   }
    { would close DestLib under us and the copy would land on nothing. See   }
    { FindLibComponentInMemory.                                             }
    Existing := FindLibComponentInMemory(DestLib, NewName);
    If Existing <> Nil Then
    Begin
        If Not Overwrite Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NAME_EXISTS',
                'A component named "' + NewName + '" already exists in '
                + DestLibPath + ' (pass overwrite=true to replace)');
            Exit;
        End;
        SchServer.ProcessControl.PreProcess(DestLib, '');
        { Drop the held reference first if this is it: once removed, the next    }
        { by-name lookup would hand it back and its first member read is the    }
        { modal (see ForgetLibComponentIf).                                     }
        ForgetLibComponentIf(DestLib, Existing, NewName);
        DestLib.RemoveSchComponent(Existing);
        SchServer.ProcessControl.PostProcess(DestLib, 'Edit');
        Overwrote := True;
    End;

    SchServer.ProcessControl.PreProcess(DestLib, '');
    DestLib.AddSchComponent(NewComp);
    { RE-ASSERT THE NAME. AddSchComponent overrides LibReference with an   }
    { auto-generated Component_<N> on the second and later additions to a  }
    { SchLib in one session, so the assignment made before the add does    }
    { not survive it. Lib_CreateSymbol hit this and re-asserts for exactly }
    { the same reason; the copy path did not, so the clone landed under an }
    { auto name and every later lookup of new_name missed it.              }
    NewComp.LibReference := NewName;
    SchServer.ProcessControl.PostProcess(DestLib, 'Edit');

    { REGISTER THE NEW COMPONENT, or it does not reach disk. Lib_CreateSymbol
      broadcasts this and persists; this path did not and did not, which is
      the whole difference between the two. Without the broadcast the symbol
      lives in the data model -- lib_get_component_details reads it back in
      full, and the copy reports verified -- while the document is never told
      anything was added, so every save route writes nothing.

      MEASURED 2026-09-21: a copied component read back correctly while the
      .SchLib stayed byte-identical at 662016 bytes across app_save_all,
      WorkspaceManager:SaveObject and Altium's own File > Save, with zero
      occurrences of the new name. Creating the same symbol from scratch
      grew the file, because that path registers.

      source=Nil, dest=Nil, broadcast: the new-component pattern from
      Altium's own createcomp_in_lib.pas, not the per-primitive
      SchRegisterObject(Container, Obj) which sends from the container. }
    Try
        SchServer.RobotManager.SendMessage(
            Nil, Nil, SCHM_PrimitiveRegistration,
            NewComp.I_ObjectAddress);
    Except End;

    DestLib.CurrentSchComponent := NewComp;
    RememberLibComponent(DestLib, NewComp, NewName);
    Try DestLib.GraphicallyInvalidate; Except End;
    MarkLibDirty(DestLib);

    { VERIFY, rather than reporting the issuing of the work. Measured on   }
    { AD26 at script 2026.08.25.6: this returned success:true while the    }
    { component count stayed flat and new_name resolved nowhere.           }
    Verify := Nil;
    Try Verify := LookupLibComponent(DestLib, NewName); Except End;
    If Verify = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('source_library', SourceLibPath) + ',' +
                JsonStr('dest_library', DestLibPath) + ',' +
                JsonStr('source', SourceName) + ',' +
                JsonStr('new_name', NewName) + ',' +
                JsonStr('reason', 'the copy was added but does not resolve '
                    + 'in the destination library afterwards, so nothing '
                    + 'was written under that name. Read the library back '
                    + 'before relying on this having worked.')
            ));
        Exit;
    End;

    { Stash the response in a local before assigning to Result -- the         }
    { DelphiScript last-String-arg clobber bug only bites here when the       }
    { JSON build calls a String helper, but the pattern is cheap insurance.   }
    RespJson :=
        '{"success":true' +
        ',"source_library":"' + EscapeJsonString(SourceLibPath) + '"' +
        ',"dest_library":"' + EscapeJsonString(DestLibPath) + '"' +
        ',"source":"' + EscapeJsonString(SourceName) + '"' +
        ',"new_name":"' + EscapeJsonString(NewName) + '"' +
        ',"same_library":' + BoolToJsonStr(SameLib) +
        ',"overwrote":' + BoolToJsonStr(Overwrote) +
        ',"verified":true}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Lib_AddPins - Bulk add pins to the currently-selected library component.     }
{ One PreProcess/PostProcess + one save for the whole batch, so adding 50      }
{ pins to a new IC symbol costs ~1x the overhead of adding one pin.           }
{ Params: pins = '~~'-separated list; each pin has key=value fields joined by  }
{         ';'. Fields: designator, name, x, y, length (mils), rotation        }
{         (0/90/180/270), electrical_type (input/output/bidirectional/        }
{         passive/power/open_collector/open_emitter/hiz), hidden (true/false),}
{         symbol_outer_edge / symbol_inner_edge (IEEE decoration name or      }
{         ordinal; 'dot' = inversion bubble, 'clock' = clock wedge),          }
{         show_name / show_designator (true/false; whether the pin's name     }
{         and number are drawn. Omit to leave at Altium's default).           }
{..............................................................................}

Function Lib_AddPins(Params : String; RequestId : String) : String;
Var
    PinsStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    Designator, Name, ElecType, HiddenStr, OwnerStr : String;
    OuterStr, InnerStr, ShowNameStr, ShowDesigStr : String;
    X, Y, Length, Rotation, OwnerPartId : Integer;
    Hidden, OwnerExplicit : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Loc : TLocation;
Begin
    PinsStr := ExtractJsonValue(Params, 'pins');
    If PinsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pins is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PinsStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            Designator := GetBatchField(Op, 'designator');
            Name := GetBatchField(Op, 'name');
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Length := StrToIntDef(GetBatchField(Op, 'length'), 200);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
            ElecType := GetBatchField(Op, 'electrical_type');
            HiddenStr := GetBatchField(Op, 'hidden');
            Hidden := (HiddenStr = 'true') Or (HiddenStr = '1');
            { Multi-part support: owner_part_id selects which sub-part      }
            { owns the pin. 0 = shared across ALL parts (e.g. the power     }
            { pins on a quad op-amp). Omit / empty = "current part" via   }
            { SetOwnerPart (single-part behaviour, original default).      }
            OwnerStr := GetBatchField(Op, 'owner_part_id');
            OwnerExplicit := OwnerStr <> '';
            OwnerPartId := StrToIntDef(OwnerStr, 0);
            { IEEE edge decorations: 'dot' on the outer edge is the inversion }
            { bubble of an active-low pin, 'clock' on the inner edge is the   }
            { wedge of a clock pin. Any TIeeeSymbol name or ordinal is        }
            { accepted; see StrToIeeeSymbol.                                  }
            OuterStr := GetBatchField(Op, 'symbol_outer_edge');
            InnerStr := GetBatchField(Op, 'symbol_inner_edge');
            { Whether the pin's name and number are DRAWN. Distinct from  }
            { 'hidden', which hides the whole pin: a resistor shows both  }
            { its pins and neither of their labels. Absent = leave alone. }
            ShowNameStr := GetBatchField(Op, 'show_name');
            ShowDesigStr := GetBatchField(Op, 'show_designator');

            Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
            If Pin = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Pin.Designator := Designator;
            Pin.Name := Name;
            { Location is a by-value record, read, mutate, write back.         }
            Loc := Pin.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            Pin.Location := Loc;
            Pin.PinLength := MilsToCoord(Length);
            Pin.Orientation := Rotation Div 90;
            Pin.IsHidden := Hidden;

            Pin.Electrical := StrToPinElectrical(ElecType);

            { Only written when the caller asked for a decoration. A fresh    }
            { pin already carries eNoSymbol on both edges, so skipping the     }
            { assignment keeps this bulk path (every symbol we author runs     }
            { through it) byte-identical to its previous behaviour whenever    }
            { the new fields are absent.                                       }
            If OuterStr <> '' Then
                Pin.Symbol_OuterEdge := StrToIeeeSymbol(OuterStr);
            If InnerStr <> '' Then
                Pin.Symbol_InnerEdge := StrToIeeeSymbol(InnerStr);

            If ShowNameStr <> '' Then
                Pin.ShowName := (ShowNameStr = 'true') Or (ShowNameStr = '1');
            If ShowDesigStr <> '' Then
                Pin.ShowDesignator :=
                    (ShowDesigStr = 'true') Or (ShowDesigStr = '1');

            If OwnerExplicit Then
            Begin
                { Explicit owner_part_id from caller (multi-part symbol).  }
                Try Pin.OwnerPartId := OwnerPartId; Except End;
                Try Pin.OwnerPartDisplayMode := 0; Except End;
            End
            Else
                SetOwnerPart(Pin, Component);

            Component.AddSchObject(Pin);
            SchRegisterObject(Component, Pin);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Lib_AddSymbolText - Bulk add body text to the current library symbol.       }
{ Same batch shape as Lib_AddPins: one PreProcess/PostProcess for the lot.    }
{ Params: texts = '~~'-separated list; fields joined by ';'. Fields: text,    }
{         x, y (mils), rotation (0/90/180/270), font_size, font_name, bold,   }
{         italic (true/false), owner_part_id.                                 }
{                                                                             }
{ The primitive is an ISch_Label, which is what Altium uses for free text on  }
{ a symbol. Its property set is the one BuildLabelStyleJson already reads     }
{ (Text / FontId / Location / Orientation / Justification), so nothing new is }
{ being assumed about the interface.                                          }
{                                                                             }
{ font_size is Altium's own font size, the number SchServer.FontManager       }
{ takes, NOT mils. No conversion is attempted here because the relationship   }
{ between the two is not documented anywhere this project can check, and a    }
{ guessed constant would silently resize every imported note.                 }
{..............................................................................}

Function Lib_AddSymbolText(Params : String; RequestId : String) : String;
Var
    TextsStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    Content, OwnerStr, FontName, BoldStr, ItalicStr : String;
    X, Y, Rotation, FontSize, OwnerPartId : Integer;
    OwnerExplicit, Bold, Italic : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Lbl : ISch_Label;
    Loc : TLocation;
    FontMgr : ISch_FontManager;
Begin
    TextsStr := ExtractJsonValue(Params, 'texts');
    If TextsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'texts is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    FontMgr := SchServer.FontManager;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := TextsStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Content := GetBatchField(Op, 'text');
            If Content = '' Then
            Begin
                { An empty string would place an invisible, unselectable }
                { primitive that only shows up as a stray object later.  }
                Inc(Failed);
                Continue;
            End;

            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
            FontSize := StrToIntDef(GetBatchField(Op, 'font_size'), 10);
            FontName := GetBatchField(Op, 'font_name');
            If FontName = '' Then FontName := 'Arial';
            BoldStr := GetBatchField(Op, 'bold');
            ItalicStr := GetBatchField(Op, 'italic');
            Bold := (BoldStr = 'true') Or (BoldStr = '1');
            Italic := (ItalicStr = 'true') Or (ItalicStr = '1');
            OwnerStr := GetBatchField(Op, 'owner_part_id');
            OwnerExplicit := OwnerStr <> '';
            OwnerPartId := StrToIntDef(OwnerStr, 0);

            Lbl := SchServer.SchObjectFactory(eLabel, eCreate_Default);
            If Lbl = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Lbl.Text := Content;
            { Location is a by-value record: read, mutate, write back. }
            Loc := Lbl.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            Lbl.Location := Loc;

            { Orientation is enum-typed. Assign the quarter-turn ordinal as }
            { a plain Integer, exactly as Lib_AddPins sets Pin.Orientation,  }
            { rather than naming a type this codebase cannot verify.        }
            Try
                Lbl.Orientation := (((Rotation Mod 360) + 360) Mod 360) Div 90;
            Except
            End;

            Try
                Lbl.FontId := FontMgr.GetFontID(FontSize, 0, False, Italic,
                                                Bold, False, FontName);
            Except
            End;

            If OwnerExplicit Then
            Begin
                Try Lbl.OwnerPartId := OwnerPartId; Except End;
                Try Lbl.OwnerPartDisplayMode := 0; Except End;
            End
            Else
                SetOwnerPart(Lbl, Component);

            Component.AddSchObject(Lbl);
            SchRegisterObject(Component, Lbl);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{ Batch line authoring: same shape as Lib_AddPins. Receives a `lines` array }
{ encoded with the ~~ / ; / = separators NextBatchOp expects, applies them  }
{ all inside one PreProcess / PostProcess pair, and triggers a single       }
{ MarkLibDirty (which now also handles graphical invalidate).               }
Function Lib_AddSymbolLines(Params : String; RequestId : String) : String;
Var
    LinesStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    X1, Y1, X2, Y2, Width : Integer;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Line : ISch_Line;
    Loc : TLocation;
Begin
    LinesStr := ExtractJsonValue(Params, 'lines');
    If LinesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'lines is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := LinesStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X1 := StrToIntDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToIntDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToIntDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToIntDef(GetBatchField(Op, 'y2'), 0);
            Width := StrToIntDef(GetBatchField(Op, 'width'), 1);
            If Width < 0 Then Width := 0;
            If Width > 3 Then Width := 3;

            Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
            If Line = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            { Read-modify-write the TLocation record -- see Lib_AddSymbolLine. }
            Loc := Line.Location;
            Loc.X := MilsToCoord(X1);
            Loc.Y := MilsToCoord(Y1);
            Line.Location := Loc;
            Loc := Line.Corner;
            Loc.X := MilsToCoord(X2);
            Loc.Y := MilsToCoord(Y2);
            Line.Corner := Loc;
            Line.LineWidth := Width;

            SetOwnerPart(Line, Component);
            Component.AddSchObject(Line);
            SchRegisterObject(Component, Line);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Lib_AuditStyles - bulk visual-style audit across every component in a       }
{ library. Walks SchLib.SchIterator with eSchComponent filter (live           }
{ components, no per-name GetState_SchComponentByLibRef lookup), and emits    }
{ the designator's full style record per component. Comment / parameter_     }
{ styles / pins are opt-in via flags so the default response stays compact.  }
{                                                                              }
{ Filter mode: when expect_designator_font_id and/or expect_designator_color }
{ are supplied, only components whose designator does NOT match the expected }
{ value go in the output. Without filters, every component is returned.       }
{                                                                              }
{ Params:                                                                     }
{   library_path                  - .SchLib path. Defaults to focused doc.   }
{   with_comment=true             - include comment style record per comp.  }
{   with_parameters=true          - include parameter_styles array per comp.}
{   with_pins=true                - include pins array per comp.             }
{   expect_designator_font_id=N   - filter: trim matches.                    }
{   expect_designator_color=N     - filter: trim matches.                    }
{   limit=5000                    - cap on emitted entries.                  }
{                                                                              }
{ Returns object with library_path, count, mismatch_count, limit, truncated, }
{ filter_applied, components:[...].                                          }
Function Lib_AuditStyles(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FlagStr : String;
    ExpFontIdStr, ExpColorStr : String;
    HasExpFontId, HasExpColor, FilterApplied : Boolean;
    WithComment, WithParameters, WithPins : Boolean;
    ExpFontId, ExpColor : Integer;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    PinIter, ParamIter : ISch_Iterator;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Param : ISch_Parameter;
    DesigLabel : ISch_Label;
    Limit, Count, MismatchCount, PinCount, NumComps, I : Integer;
    DesigFontId, DesigColor : Integer;
    DesigJson, CommentJson, PinList, StyleList, ElecStr, ResultsJson, Entry, CompName : String;
    PinLabelHidden : Boolean;
    First, FirstPin, FirstStyle, Mismatched : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    FlagStr := ExtractJsonValue(Params, 'with_comment');
    WithComment := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    FlagStr := ExtractJsonValue(Params, 'with_parameters');
    WithParameters := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    FlagStr := ExtractJsonValue(Params, 'with_pins');
    WithPins := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');

    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    ExpFontIdStr := ExtractJsonValue(Params, 'expect_designator_font_id');
    ExpColorStr := ExtractJsonValue(Params, 'expect_designator_color');
    HasExpFontId := ExpFontIdStr <> '';
    HasExpColor := ExpColorStr <> '';
    ExpFontId := StrToIntDef(ExpFontIdStr, 0);
    ExpColor := StrToIntDef(ExpColorStr, 0);
    FilterApplied := HasExpFontId Or HasExpColor;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
        Try FocusedPath := Doc.DM_FullPath; Except End;

    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    Count := 0;
    MismatchCount := 0;
    ResultsJson := '';
    First := True;

    { Enumerate via ILibCompInfoReader. The schematic SchIterator with        }
    { eSchComponent only walks components placed on a regular SchDoc, NOT    }
    { the symbol entries inside a SchLib. The CompInfoReader gives names    }
    { in document order; for each name we load the live ISch_Component via }
    { GetState_SchComponentByLibRef to read its designator/comment/parameter}
    { style records. This is the same pattern Lib_GetComponents uses.        }
    { No file on disk: nothing to enumerate, the same reply as an empty   }
    { reader. READER_FAILED stays for a reader that cannot be made.      }
    LibReader := Nil;
    If Not LibFileMissing(LibPath) Then
    Begin
        LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
        If LibReader = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'READER_FAILED',
                'Failed to create library reader for ' + LibPath);
            Exit;
        End;
    End;

    If LibReader <> Nil Then
    Try
        LibReader.ReadAllComponentInfo;
        NumComps := LibReader.NumComponentInfos;

        For I := 0 To NumComps - 1 Do
        Begin
            If Count >= Limit Then Break;

            CompInfo := LibReader.ComponentInfos[I];
            CompName := '';
            Try CompName := CompInfo.CompName; Except End;
            If CompName = '' Then Continue;

            Component := LookupLibComponent(SchLib, CompName);
            If Component = Nil Then Continue;

            { Read designator font_id / color via the typed ISch_Label local. }
            { Component.Designator returns ISch_Designator which IS an        }
            { ISch_Label, so the assignment + late-bound property reads       }
            { resolve cleanly at compile time.                                  }
            DesigLabel := Nil;
            DesigFontId := 0;
            DesigColor := 0;
            Try DesigLabel := Component.Designator; Except End;
            If DesigLabel <> Nil Then
            Begin
                Try DesigFontId := DesigLabel.FontId; Except End;
                Try DesigColor := DesigLabel.Color; Except End;
            End;

            Mismatched := False;
            If HasExpFontId And (DesigFontId <> ExpFontId) Then Mismatched := True;
            If HasExpColor And (DesigColor <> ExpColor) Then Mismatched := True;

            { Skip when filter is on and the component matches the expected }
            { style. Without filters, every component is emitted.            }
            If (Not FilterApplied) Or Mismatched Then
            Begin

                DesigJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
                If DesigLabel <> Nil Then
                    Try DesigJson := BuildLabelStyleJson(DesigLabel, True); Except End;

                Entry := '{"name":"' + EscapeJsonString(CompName) +
                    '","designator":' + DesigJson +
                    ',"mismatched":' + BoolToJsonStr(Mismatched);

                If WithComment Then
                Begin
                    CommentJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
                    Try CommentJson := BuildLabelStyleJson(Component.Comment, True); Except End;
                    Entry := Entry + ',"comment":' + CommentJson;
                End;

                If WithPins Then
                Begin
                    PinList := '';
                    FirstPin := True;
                    PinCount := 0;
                    PinIter := Component.SchIterator_Create;
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Try
                        Pin := PinIter.FirstSchObject;
                        While Pin <> Nil Do
                        Begin
                            If Not FirstPin Then PinList := PinList + ',';
                            FirstPin := False;

                            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
                            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
                            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
                            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
                            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
                            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
                            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
                            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
                            Else ElecStr := 'passive';

                            PinLabelHidden := False;
                            Try PinLabelHidden := (Not Pin.ShowName) And (Not Pin.ShowDesignator); Except End;

                            PinList := PinList + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                                '","name":"' + EscapeJsonString(Pin.Name) +
                                '","electrical_type":"' + ElecStr +
                                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                                ',"orientation":' + IntToStr(Pin.Orientation) +
                                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                                ',"label_hidden":' + BoolToJsonStr(PinLabelHidden) + '}';
                            Inc(PinCount);

                            Pin := PinIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(PinIter);
                    End;
                    Entry := Entry + ',"pin_count":' + IntToStr(PinCount) +
                        ',"pins":[' + PinList + ']';
                End;

                If WithParameters Then
                Begin
                    StyleList := '';
                    FirstStyle := True;
                    ParamIter := Component.SchIterator_Create;
                    ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                    Try
                        Param := ParamIter.FirstSchObject;
                        While Param <> Nil Do
                        Begin
                            If Not FirstStyle Then StyleList := StyleList + ',';
                            FirstStyle := False;
                            StyleList := StyleList + '{"name":"' + EscapeJsonString(Param.Name) +
                                '","value":"' + EscapeJsonString(Param.Text) +
                                '","style":' + BuildLabelStyleJson(Param, False) + '}';
                            Param := ParamIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ParamIter);
                    End;
                    Entry := Entry + ',"parameter_styles":[' + StyleList + ']';
                End;

                Entry := Entry + '}';

                If Not First Then ResultsJson := ResultsJson + ',';
                First := False;
                ResultsJson := ResultsJson + Entry;

                If Mismatched Then Inc(MismatchCount);
                Inc(Count);
            End;
        End;
    Finally
        SchServer.DestroyCompInfoReader(LibReader);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"count":' + IntToStr(Count) +
        ',"mismatch_count":' + IntToStr(MismatchCount) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Count >= Limit) +
        ',"filter_applied":' + BoolToJsonStr(FilterApplied) +
        ',"components":[' + ResultsJson + ']}');
End;

{..............................................................................}
{ Lib_SetLabelFormat - bulk OR single-component label-style writer.            }
{                                                                              }
{ Sets any subset of (font_id, color, is_hidden, orientation, justification) }
{ on a target ISch_Label (designator, comment, or one named parameter) for    }
{ either one component (component_name supplied) or every component in the   }
{ library (component_name omitted). Symmetric counterpart to                  }
{ lib_audit_styles' filtering: when only_mismatched is true (default), the   }
{ handler skips components whose target label already matches every          }
{ specified field, so re-runs after partial application stay idempotent.     }
{                                                                              }
{ The whole edit batch is wrapped in ProcessControl.PreProcess /              }
{ PostProcess('Edit') so Altium's undo stack records it as one step. Each    }
{ label modification is bracketed by SchBeginModify / SchEndModify on the    }
{ ISch_Label so the SchServer broadcasts a refresh for that primitive.      }
{ MarkLibDirty fires once at the end; saves are deferred per the project-    }
{ side perf_deferred_save pattern.                                            }
{                                                                              }
{ Params (any combination of style fields, omitted ones are left untouched): }
{   library_path                  - .SchLib path. Defaults to focused doc.   }
{   component_name                - optional, single-component mode.         }
{   target=designator|comment|parameter:<name>  (default 'designator')      }
{   font_id, color, is_hidden, orientation, justification - new style values }
{   only_mismatched=true|false    (default true) - skip already-compliant   }
{   limit=5000                    - cap on processed components in bulk     }
{                                                                              }
{ Returns object: library_path, target, scope, total, modified,              }
{ already_compliant, missing_target, failed, limit, truncated.              }
Procedure ResolveTargetLabel(Component : ISch_Component; Target : String;
    Var Lbl : ISch_Label; Var Found : Boolean);
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
    ParamName : String;
Begin
    Lbl := Nil;
    Found := False;

    If Target = 'designator' Then
    Begin
        Try Lbl := Component.Designator; Found := (Lbl <> Nil); Except End;
    End
    Else If Target = 'comment' Then
    Begin
        Try Lbl := Component.Comment; Found := (Lbl <> Nil); Except End;
    End
    Else If Pos('parameter:', Target) = 1 Then
    Begin
        ParamName := Copy(Target, 11, Length(Target) - 10);
        If ParamName = '' Then Exit;
        Iter := Component.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Try
            Param := Iter.FirstSchObject;
            While Param <> Nil Do
            Begin
                If Param.Name = ParamName Then
                Begin
                    Lbl := Param;
                    Found := True;
                    Break;
                End;
                Param := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    End;
End;

Function ApplyLabelFormat(Lbl : ISch_Label;
    HasFontId : Boolean; NewFontId : Integer;
    HasColor : Boolean; NewColor : Integer;
    HasIsHidden : Boolean; NewIsHidden : Boolean;
    HasOrientation : Boolean; NewOrientation : Integer;
    HasJustification : Boolean; NewJustification : Integer;
    OnlyMismatched : Boolean) : Integer;
{ Returns 1 if modified, 0 if compliant (skipped), -1 if the write itself     }
{ raised (counted as failed by the caller).                                   }
Var
    Compliant : Boolean;
Begin
    Result := 0;
    If Lbl = Nil Then Exit;

    If OnlyMismatched Then
    Begin
        Compliant := True;
        If HasFontId Then
            Try If Lbl.FontId <> NewFontId Then Compliant := False; Except End;
        If Compliant And HasColor Then
            Try If Lbl.Color <> NewColor Then Compliant := False; Except End;
        If Compliant And HasIsHidden Then
            Try If Lbl.IsHidden <> NewIsHidden Then Compliant := False; Except End;
        If Compliant And HasOrientation Then
            Try If Lbl.Orientation <> NewOrientation Then Compliant := False; Except End;
        If Compliant And HasJustification Then
            Try If Lbl.Justification <> NewJustification Then Compliant := False; Except End;
        If Compliant Then Exit;
    End;

    Try
        SchBeginModify(Lbl);
        If HasFontId Then Lbl.FontId := NewFontId;
        If HasColor Then Lbl.Color := NewColor;
        If HasIsHidden Then Lbl.IsHidden := NewIsHidden;
        If HasOrientation Then Lbl.Orientation := NewOrientation;
        If HasJustification Then Lbl.Justification := NewJustification;
        SchEndModify(Lbl);
        Result := 1;
    Except
        { NEEDS PROBE, not guarded. An out-of-enum Orientation (TRotationBy90 }
        { is 0..3, and the Python docstring offers 0/90/180/270) or          }
        { Justification (0..8) is the suspected raise, but whether the write  }
        { raises or silently stores the ordinal is not measured, and a range  }
        { refusal would turn the second into a failure. Counted instead.      }
        RecordCaughtException('ApplyLabelFormat:write');
        Result := -1;
    End;
End;

Function Lib_SetLabelFormat(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, Target, CompName, FlagStr : String;
    HasFontId, HasColor, HasIsHidden, HasOrientation, HasJustification : Boolean;
    NewFontId, NewColor, NewOrientation, NewJustification : Integer;
    NewIsHidden, OnlyMismatched, Found : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    Lbl : ISch_Label;
    Limit, Total, Modified, AlreadyCompliant, MissingTarget, Failed, NumComps, I, ApplyResult : Integer;
    Scope : String;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    Target := ExtractJsonValue(Params, 'target');
    If Target = '' Then Target := 'designator';
    CompName := ExtractJsonValue(Params, 'component_name');

    HasFontId := ExtractJsonValue(Params, 'font_id') <> '';
    NewFontId := StrToIntDef(ExtractJsonValue(Params, 'font_id'), 0);
    HasColor := ExtractJsonValue(Params, 'color') <> '';
    NewColor := StrToIntDef(ExtractJsonValue(Params, 'color'), 0);
    HasIsHidden := ExtractJsonValue(Params, 'is_hidden') <> '';
    NewIsHidden := False;
    FlagStr := ExtractJsonValue(Params, 'is_hidden');
    If (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1') Then NewIsHidden := True;
    HasOrientation := ExtractJsonValue(Params, 'orientation') <> '';
    NewOrientation := StrToIntDef(ExtractJsonValue(Params, 'orientation'), 0);
    HasJustification := ExtractJsonValue(Params, 'justification') <> '';
    NewJustification := StrToIntDef(ExtractJsonValue(Params, 'justification'), 0);

    FlagStr := ExtractJsonValue(Params, 'only_mismatched');
    OnlyMismatched := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');

    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    If (Not HasFontId) And (Not HasColor) And (Not HasIsHidden)
        And (Not HasOrientation) And (Not HasJustification) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOTHING_TO_SET',
            'At least one of font_id / color / is_hidden / orientation / justification must be supplied');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    Total := 0;
    Modified := 0;
    AlreadyCompliant := 0;
    MissingTarget := 0;
    Failed := 0;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        If CompName <> '' Then
        Begin
            { Single-component mode. }
            Scope := 'single';
            Component := LookupLibComponent(SchLib, CompName);
            If Component = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
                    'Component not found in library: ' + CompName);
                Exit;
            End;
            Total := 1;
            ResolveTargetLabel(Component, Target, Lbl, Found);
            If Not Found Then
                Inc(MissingTarget)
            Else
            Begin
                ApplyResult := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
                    HasColor, NewColor, HasIsHidden, NewIsHidden,
                    HasOrientation, NewOrientation, HasJustification, NewJustification,
                    OnlyMismatched);
                If ApplyResult = 1 Then Inc(Modified)
                Else If ApplyResult = 0 Then Inc(AlreadyCompliant)
                Else Inc(Failed);
            End;
        End
        Else
        Begin
            { Bulk mode: walk library via CompInfoReader, same enumeration as }
            { Lib_GetComponents and Lib_AuditStyles.                            }
            Scope := 'bulk';
            { No file on disk: nothing to enumerate, the same reply as an empty   }
            { reader. READER_FAILED stays for a reader that cannot be made.      }
            LibReader := Nil;
            If Not LibFileMissing(LibPath) Then
            Begin
                LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
                If LibReader = Nil Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'READER_FAILED',
                        'Failed to create library reader for ' + LibPath);
                    Exit;
                End;
            End;
            If LibReader <> Nil Then
            Try
                LibReader.ReadAllComponentInfo;
                NumComps := LibReader.NumComponentInfos;

                For I := 0 To NumComps - 1 Do
                Begin
                    If Total >= Limit Then Break;
                    CompInfo := LibReader.ComponentInfos[I];
                    CompName := '';
                    Try CompName := CompInfo.CompName; Except End;
                    If CompName = '' Then Continue;
                    Component := LookupLibComponent(SchLib, CompName);
                    If Component = Nil Then Continue;
                    Inc(Total);

                    ResolveTargetLabel(Component, Target, Lbl, Found);
                    If Not Found Then
                    Begin
                        Inc(MissingTarget);
                        Continue;
                    End;

                    ApplyResult := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
                        HasColor, NewColor, HasIsHidden, NewIsHidden,
                        HasOrientation, NewOrientation, HasJustification, NewJustification,
                        OnlyMismatched);
                    If ApplyResult = 1 Then Inc(Modified)
                    Else If ApplyResult = 0 Then Inc(AlreadyCompliant)
                    Else Inc(Failed);
                End;
            Finally
                SchServer.DestroyCompInfoReader(LibReader);
            End;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    If Modified > 0 Then MarkLibDirty(SchLib);

    Try SchLib.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"target":"' + EscapeJsonString(Target) + '"' +
        ',"scope":"' + EscapeJsonString(Scope) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"modified":' + IntToStr(Modified) +
        ',"already_compliant":' + IntToStr(AlreadyCompliant) +
        ',"missing_target":' + IntToStr(MissingTarget) +
        ',"failed":' + IntToStr(Failed) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Total >= Limit) + '}');
End;

{ Lib_SetLabelFormats - apply N (target, style) ops in one library walk.      }
{                                                                              }
{ Same semantics as Lib_SetLabelFormat but processes a list of ops in a       }
{ single IPC round-trip, with one library focus and one walk. Five sequential }
{ set_label_format calls (one per parameter target) collapse into one trip,   }
{ which is the dominant cost on large libraries (each call carries the IPC,  }
{ workspace lookup, doc-focus check and CompInfoReader walk).                  }
{                                                                              }
{ Wire format for `ops` matches the project-wide NextBatchOp grammar: ops    }
{ are separated by ~~ and per-op fields by `;`, key=value. Each op may set    }
{ target, font_id, color, is_hidden, orientation, justification. The global  }
{ only_mismatched flag applies to every op.                                   }
{                                                                              }
{ Returns object: library_path, scope ("single"|"bulk"), total, limit,        }
{ truncated, ops array each with target, modified, already_compliant,         }
{ missing_target, failed.                                                     }

Function ApplyOpToComponent(Component : ISch_Component; Target : String;
    FontIdStr, ColorStr, IsHiddenStr, OrientationStr, JustificationStr : String;
    OnlyMismatched : Boolean) : Integer;
{ Returns 0=already_compliant, 1=modified, 2=missing_target, 3=failed.        }
Var
    HasFontId, HasColor, HasIsHidden, HasOrientation, HasJustification : Boolean;
    NewFontId, NewColor, NewOrientation, NewJustification : Integer;
    NewIsHidden, Found : Boolean;
    Lbl : ISch_Label;
    AR : Integer;
Begin
    HasFontId := FontIdStr <> '';
    NewFontId := StrToIntDef(FontIdStr, 0);
    HasColor := ColorStr <> '';
    NewColor := StrToIntDef(ColorStr, 0);
    HasIsHidden := IsHiddenStr <> '';
    NewIsHidden := (IsHiddenStr = 'true') Or (IsHiddenStr = 'True') Or (IsHiddenStr = '1');
    HasOrientation := OrientationStr <> '';
    NewOrientation := StrToIntDef(OrientationStr, 0);
    HasJustification := JustificationStr <> '';
    NewJustification := StrToIntDef(JustificationStr, 0);

    ResolveTargetLabel(Component, Target, Lbl, Found);
    If Not Found Then
    Begin
        Result := 2;
        Exit;
    End;
    AR := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
        HasColor, NewColor, HasIsHidden, NewIsHidden,
        HasOrientation, NewOrientation, HasJustification, NewJustification,
        OnlyMismatched);
    If AR = 1 Then Result := 1
    Else If AR = 0 Then Result := 0
    Else Result := 3;
End;

Function Lib_SetLabelFormats(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, CompName, FlagStr : String;
    OpsStr, OpStr, Remaining, CurCompName, Target : String;
    OnlyMismatched : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    Limit, Total, NumComps, I, J, NumOps, OpResult : Integer;
    Scope, OpsJson, RespJson : String;
    AnyModified : Boolean;
    { Parallel TStringLists -- DelphiScript Function locals with fixed-size  }
    { arrays silently corrupt Result, so we use TStringList per the project's }
    { existing pattern. Counter fields hold IntToStr(N).                       }
    OpTargets, OpFontIds, OpColors, OpIsHidden,
        OpOrientation, OpJustification : TStringList;
    OpModified, OpAlready, OpMissing, OpFailed : TStringList;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    OpsStr := ExtractJsonValue(Params, 'ops');
    If OpsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'ops is required (non-empty ~~-separated list)');
        Exit;
    End;
    FlagStr := ExtractJsonValue(Params, 'only_mismatched');
    OnlyMismatched := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    { Workspace + library focus checks BEFORE allocating TStringLists so an  }
    { early-exit error path does not leak.                                    }
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    OpTargets := TStringList.Create;
    OpFontIds := TStringList.Create;
    OpColors := TStringList.Create;
    OpIsHidden := TStringList.Create;
    OpOrientation := TStringList.Create;
    OpJustification := TStringList.Create;
    OpModified := TStringList.Create;
    OpAlready := TStringList.Create;
    OpMissing := TStringList.Create;
    OpFailed := TStringList.Create;
    Try
        NumOps := 0;
        Remaining := OpsStr;
        While True Do
        Begin
            OpStr := NextBatchOp(Remaining);
            If OpStr = '' Then Break;
            Target := GetBatchField(OpStr, 'target');
            If Target = '' Then Target := 'designator';
            OpTargets.Add(Target);
            OpFontIds.Add(GetBatchField(OpStr, 'font_id'));
            OpColors.Add(GetBatchField(OpStr, 'color'));
            OpIsHidden.Add(GetBatchField(OpStr, 'is_hidden'));
            OpOrientation.Add(GetBatchField(OpStr, 'orientation'));
            OpJustification.Add(GetBatchField(OpStr, 'justification'));
            OpModified.Add('0');
            OpAlready.Add('0');
            OpMissing.Add('0');
            OpFailed.Add('0');
            Inc(NumOps);
        End;

        If NumOps = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOTHING_TO_SET',
                'ops parsed to zero entries');
            Exit;
        End;

        Total := 0;
        SchServer.ProcessControl.PreProcess(SchLib, '');
        Try
            If CompName <> '' Then
            Begin
                Scope := 'single';
                Component := LookupLibComponent(SchLib, CompName);
                If Component = Nil Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
                        'Component not found in library: ' + CompName);
                    Exit;
                End;
                Total := 1;
                For J := 0 To NumOps - 1 Do
                Begin
                    OpResult := ApplyOpToComponent(Component, OpTargets[J],
                        OpFontIds[J], OpColors[J], OpIsHidden[J],
                        OpOrientation[J], OpJustification[J], OnlyMismatched);
                    If OpResult = 0 Then
                        OpAlready[J] := IntToStr(StrToIntDef(OpAlready[J], 0) + 1)
                    Else If OpResult = 1 Then
                        OpModified[J] := IntToStr(StrToIntDef(OpModified[J], 0) + 1)
                    Else If OpResult = 2 Then
                        OpMissing[J] := IntToStr(StrToIntDef(OpMissing[J], 0) + 1)
                    Else
                        OpFailed[J] := IntToStr(StrToIntDef(OpFailed[J], 0) + 1);
                End;
            End
            Else
            Begin
                Scope := 'bulk';
                { No file on disk: nothing to enumerate, the same reply as an empty   }
                { reader. READER_FAILED stays for a reader that cannot be made.      }
                LibReader := Nil;
                If Not LibFileMissing(LibPath) Then
                Begin
                    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
                    If LibReader = Nil Then
                    Begin
                        Result := BuildErrorResponse(RequestId, 'READER_FAILED',
                            'Failed to create library reader for ' + LibPath);
                        Exit;
                    End;
                End;
                If LibReader <> Nil Then
                Try
                    LibReader.ReadAllComponentInfo;
                    NumComps := LibReader.NumComponentInfos;
                    For I := 0 To NumComps - 1 Do
                    Begin
                        If Total >= Limit Then Break;
                        CompInfo := LibReader.ComponentInfos[I];
                        CurCompName := '';
                        Try CurCompName := CompInfo.CompName; Except End;
                        If CurCompName = '' Then Continue;
                        Component := LookupLibComponent(SchLib, CurCompName);
                        If Component = Nil Then Continue;
                        Inc(Total);

                        For J := 0 To NumOps - 1 Do
                        Begin
                            OpResult := ApplyOpToComponent(Component, OpTargets[J],
                                OpFontIds[J], OpColors[J], OpIsHidden[J],
                                OpOrientation[J], OpJustification[J], OnlyMismatched);
                            If OpResult = 0 Then
                                OpAlready[J] := IntToStr(StrToIntDef(OpAlready[J], 0) + 1)
                            Else If OpResult = 1 Then
                                OpModified[J] := IntToStr(StrToIntDef(OpModified[J], 0) + 1)
                            Else If OpResult = 2 Then
                                OpMissing[J] := IntToStr(StrToIntDef(OpMissing[J], 0) + 1)
                            Else
                                OpFailed[J] := IntToStr(StrToIntDef(OpFailed[J], 0) + 1);
                        End;
                    End;
                Finally
                    SchServer.DestroyCompInfoReader(LibReader);
                End;
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
        End;

        AnyModified := False;
        For J := 0 To NumOps - 1 Do
            If StrToIntDef(OpModified[J], 0) > 0 Then AnyModified := True;
        If AnyModified Then MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;

        OpsJson := '[';
        For J := 0 To NumOps - 1 Do
        Begin
            If J > 0 Then OpsJson := OpsJson + ',';
            OpsJson := OpsJson +
                '{"target":"' + EscapeJsonString(OpTargets[J]) + '"' +
                ',"modified":' + OpModified[J] +
                ',"already_compliant":' + OpAlready[J] +
                ',"missing_target":' + OpMissing[J] +
                ',"failed":' + OpFailed[J] + '}';
        End;
        OpsJson := OpsJson + ']';

        { Stash the full JSON in a local before assigning to Result -- the    }
        { DelphiScript last-arg clobber bug only bites on String returns      }
        { with a String arg, but the pattern is cheap insurance.              }
        RespJson :=
            '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
            ',"scope":"' + EscapeJsonString(Scope) + '"' +
            ',"total":' + IntToStr(Total) +
            ',"limit":' + IntToStr(Limit) +
            ',"truncated":' + BoolToJsonStr(Total >= Limit) +
            ',"ops":' + OpsJson + '}';
        Result := BuildSuccessResponse(RequestId, RespJson);
    Finally
        OpTargets.Free;
        OpFontIds.Free;
        OpColors.Free;
        OpIsHidden.Free;
        OpOrientation.Free;
        OpJustification.Free;
        OpModified.Free;
        OpAlready.Free;
        OpMissing.Free;
        OpFailed.Free;
    End;
End;

{ Lib_ExtractIntLib - Extract .SchLib and .PcbLib sources from an .IntLib.   }
{                                                                              }
{ An Altium .IntLib (integrated library) packages compiled symbol and        }
{ footprint libraries together; the editor's "Extract Sources" command       }
{ writes the underlying .SchLib + .PcbLib back to disk so they can be        }
{ inspected or modified. This handler opens the .IntLib, runs the extract   }
{ process, and then probes the conventional output locations (Altium drops  }
{ them into a sibling folder named after the IntLib base name) to report    }
{ which files were produced.                                                  }
{                                                                              }
{ Params:                                                                      }
{   intlib_path (required) - path to the .IntLib file                       }
{                                                                              }
{ Returns: process_run, sch_lib_path/sch_lib_found, pcb_lib_path/found.     }
Function Lib_ExtractIntLib(Params : String; RequestId : String) : String;
Var
    IntLibPath, BaseName, ParentDir, ExtractDir : String;
    SchLibPath, PcbLibPath : String;
    FoundSch, FoundPcb : Boolean;
    Workspace : IWorkspace;
    RespJson : String;
Begin
    IntLibPath := ExtractJsonValue(Params, 'intlib_path');
    If IntLibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'intlib_path is required');
        Exit;
    End;
    If Not FileExists(IntLibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FILE_NOT_FOUND',
            'IntLib not found: ' + IntLibPath);
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Open the IntLib in Altium's integrated-library editor so the      }
    { extract command has a current document to act on.                  }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', IntLibPath);
    RunProcess('WorkspaceManager:OpenObject');

    { Run Altium's source-extraction. Try/Except won't catch a bad       }
    { process name (DelphiScript can't), so we detect outcome by         }
    { probing for the resulting files instead.                            }
    ResetParameters;
    AddStringParameter('FileName', IntLibPath);
    RunProcess('IntegratedLibrary:ExtractSources');

    { Where Altium writes the extracted files: a sibling folder named    }
    { after the IntLib base name, containing <Base>.SchLib + .PcbLib.    }
    BaseName := ChangeFileExt(ExtractFileName(IntLibPath), '');
    ParentDir := ExtractFilePath(IntLibPath);
    ExtractDir := ParentDir + BaseName + '\';
    SchLibPath := ExtractDir + BaseName + '.SchLib';
    PcbLibPath := ExtractDir + BaseName + '.PcbLib';
    FoundSch := FileExists(SchLibPath);
    FoundPcb := FileExists(PcbLibPath);

    { Fallback locations: same directory as the IntLib (some Altium       }
    { versions drop sources beside the IntLib instead of in a sub-dir).  }
    If Not FoundSch Then
    Begin
        SchLibPath := ParentDir + BaseName + '.SchLib';
        FoundSch := FileExists(SchLibPath);
    End;
    If Not FoundPcb Then
    Begin
        PcbLibPath := ParentDir + BaseName + '.PcbLib';
        FoundPcb := FileExists(PcbLibPath);
    End;

    RespJson :=
        '{"intlib_path":"' + EscapeJsonString(IntLibPath) + '"' +
        ',"extract_dir":"' + EscapeJsonString(ExtractDir) + '"' +
        ',"sch_lib_path":"' + EscapeJsonString(SchLibPath) + '"' +
        ',"sch_lib_found":' + BoolToJsonStr(FoundSch) +
        ',"pcb_lib_path":"' + EscapeJsonString(PcbLibPath) + '"' +
        ',"pcb_lib_found":' + BoolToJsonStr(FoundPcb) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_UpdateFootprintHeightsFrom3D                                            }
{                                                                              }
{ Walk every footprint in the active PCB Library and propagate the maximum   }
{ 3D-body OverallHeight up to the footprint's Height field. Footprint.Height }
{ drives the placement-collision DRC rule that prevents tall components       }
{ from getting placed under shorter ones or under an enclosure overhang --   }
{ but libraries shipped without explicit heights default to 0, which         }
{ effectively disables that check.                                            }
{                                                                              }
{ mode = 'raise' (default) updates only when the 3D model is TALLER than the }
{ current Footprint.Height. That protects a manually-set "I know this part   }
{ is 5mm despite the model being 3mm" value, and it is the right default.     }
{                                                                              }
{ mode = 'match' also LOWERS a height to the model. Raising alone cannot fix  }
{ the opposite fault, and an over-tall height is the more damaging of the two: }
{ a footprint claiming 50mm when the part is 3mm fails placement-collision    }
{ DRC against everything near it and blocks placements that are actually fine, }
{ where a too-low height merely fails to catch a real collision.              }
{                                                                              }
{ NEITHER MODE WRITES ZERO. A footprint with no 3D body yields no measurement, }
{ and writing the 0 that implies would silently disable the very DRC rule this }
{ handler exists to arm, across every part a library never modelled. Those are }
{ counted and named as without_model instead, which is a finding worth having. }
Function Lib_UpdateFootprintHeightsFrom3D(Params : String; RequestId : String) : String;
Var
    CurLib : IPCB_Library;
    LibIter : IPCB_LibraryIterator;
    Footprint, SavedCurrent : IPCB_LibComponent;
    GrIter : IPCB_GroupIterator;
    Body : IPCB_ComponentBody;
    Updated, Inspected, Lowered, NoModel : Integer;
    Items, FpName, Mode, NoModelNames, SaveNote : String;
    First, FirstNoModel, ShouldWrite : Boolean;
    OldH, NewH : TCoord;
Begin
    Mode := LowerCase(Trim(ExtractJsonValue(Params, 'mode')));
    If Mode = '' Then Mode := 'raise';
    If (Mode <> 'raise') And (Mode <> 'match') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_MODE',
            'mode must be "raise" (only increase a height, the default) or '
            + '"match" (also lower a height to the model). Got: ' + Mode);
        Exit;
    End;
    { PcbLibMayBeCurrent (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway, so the reply below comes without the modal. }
    CurLib := Nil;
    If PcbLibMayBeCurrent(0) Then CurLib := PCBServer.GetCurrentPCBLibrary;
    If CurLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB Library document focused');
        Exit;
    End;

    SavedCurrent := CurLib.CurrentComponent;
    Updated := 0;
    Inspected := 0;
    Lowered := 0;
    NoModel := 0;
    Items := '';
    NoModelNames := '';
    First := True;
    FirstNoModel := True;

    LibIter := CurLib.LibraryIterator_Create;
    Try
        LibIter.SetState_FilterAll;
        Footprint := LibIter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            Try
                Inc(Inspected);
                NewH := 0;
                GrIter := Footprint.GroupIterator_Create;
                Try
                    GrIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                    Body := GrIter.FirstPCBObject;
                    While Body <> Nil Do
                    Begin
                        Try
                            If Body.OverallHeight > NewH Then NewH := Body.OverallHeight;
                        Except End;
                        Body := GrIter.NextPCBObject;
                    End;
                Finally
                    Footprint.GroupIterator_Destroy(GrIter);
                End;

                OldH := Footprint.Height;
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                If NewH <= 0 Then
                Begin
                    { No 3D body, so nothing measured. Never written to 0. }
                    Inc(NoModel);
                    If Not FirstNoModel Then NoModelNames := NoModelNames + ',';
                    FirstNoModel := False;
                    NoModelNames := NoModelNames + '"'
                        + EscapeJsonString(FpName) + '"';
                End
                Else
                Begin
                    If Mode = 'match' Then ShouldWrite := (NewH <> OldH)
                    Else ShouldWrite := (NewH > OldH);
                    If ShouldWrite Then
                    Begin
                        Footprint.Height := NewH;
                        Inc(Updated);
                        If NewH < OldH Then Inc(Lowered);
                        If Not First Then Items := Items + ',';
                        First := False;
                        Items := Items + JsonObj(
                            JsonStr('name', FpName) + ',' +
                            JsonFloat('old_height_mm', CoordToMM(OldH)) + ',' +
                            JsonFloat('new_height_mm', CoordToMM(NewH))
                        );
                    End;
                End;
            Except End;
            Footprint := LibIter.NextPCBObject;
        End;
    Finally
        CurLib.LibraryIterator_Destroy(LibIter);
    End;

    { Restore the originally-focused footprint and mark the lib dirty. }
    If SavedCurrent <> Nil Then CurLib.CurrentComponent := SavedCurrent;
    If Updated > 0 Then
    Begin
        Try
            CurLib.Board.ViewManager_FullUpdate;
            { No SaveDoc here -- libraries should be reviewed by hand    }
            { before saving since this rewrites height data project-wide.}
        Except End;
    End;

    { UNSETTLED: these writes may not reach the save. lib_set_footprint_   }
    { height lost 51 of 55 heights on save (2026-10-10) when it wrote      }
    { Height on footprints it never switched to afterwards, which is what  }
    { this sweep still does. Until RELEASE_VERIFICATION 37.2's sweep check  }
    { settles it, the reply says so instead of implying the heights are    }
    { ready to save.                                                       }
    If Updated > 0 Then
        SaveNote := 'UNCONFIRMED THAT THESE HEIGHTS REACH THE FILE. This sweep '
            + 'writes Height on footprints without switching to each one '
            + 'afterwards, and lib_set_footprint_height lost 51 of 55 such '
            + 'writes on save (2026-10-10). After lib_save_library, read two '
            + 'of the items back from the file (lib_read_pcblib_file); if '
            + 'they are not there, set them with lib_set_footprint_height, '
            + 'which runs the switch that kept them.'
    Else
        SaveNote := 'Nothing was written, so there is nothing to save.';

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('mode', Mode) + ',' +
            JsonInt('inspected', Inspected) + ',' +
            JsonInt('updated', Updated) + ',' +
            JsonInt('lowered', Lowered) + ',' +
            JsonInt('without_model', NoModel) + ',' +
            JsonRaw('without_model_names', '[' + NoModelNames + ']') + ',' +
            JsonStr('without_model_note',
                'These have no 3D body, so no height could be measured and '
                + 'none was written. Their Height is whatever it already was, '
                + 'and a 0 there leaves placement-collision DRC disabled for '
                + 'that part.') + ',' +
            JsonBool('heights_confirmed_on_save', False) + ',' +
            JsonStr('save_note', SaveNote) + ',' +
            JsonRaw('items', '[' + Items + ']')
        ));
End;


{..............................................................................}
{ Lib_SetFootprintHeight                                                       }
{                                                                              }
{ Write Footprint.Height directly, up or down, on one named footprint or on    }
{ the library's current component.                                             }
{                                                                              }
{ The sweep above can only derive a height from a 3D body, which leaves two    }
{ cases it cannot serve: a part with no model, and a part whose model is       }
{ wrong. Before this there was no setter at all, so a footprint carrying an    }
{ absurd height could be read but not corrected from here.                     }
{                                                                              }
{ Zero is ACCEPTED but is not a neutral value: it disables the                 }
{ placement-collision rule for that footprint rather than relaxing it. The     }
{ reply says so when it is written, because "cleared the height" and "turned   }
{ off the check" are the same edit and only one of them sounds harmless.       }
{                                                                              }
{ THE WRITE MUST REACH THE SAVE (live, 2026-10-10, bridge 2026.10.08.01, the   }
{ 3D-link pass on the carrier library). MEASURED: 55 footprints had their      }
{ height set by this handler, each reply said changed:true, and after          }
{ lib_save_library 51 of the 55 HEIGHT values were gone from the file;         }
{ re-running with each footprint made current again before the save kept      }
{ every one. NOT MEASURED: which four survived the first save was not          }
{ recorded - probably the footprints that were current at some point after    }
{ their write. The working hypothesis behind the recipe: the library board    }
{ holds the current footprint and the save writes what it holds, so a         }
{ property set on a footprint that is never switched to after the edit is not }
{ written. The old code set Height on whatever the walk found and restored    }
{ the previous current footprint, never switching to the edited one.          }
{                                                                              }
{ So the handler now does the measured recipe itself, on EVERY path: the      }
{ write is bracketed PCBM_BeginModify / PCBM_EndModify (as                     }
{ Lib_SetFootprintDescription brackets its write), then the library SWITCHES  }
{ TO the footprint (verified): a footprint that was not current is made        }
{ current and the caller's current one restored; one that was ALREADY current }
{ (footprint_name empty, the default, or naming it) is parked on another      }
{ footprint first (LibParkCurrentElsewhere) and switched back, because a      }
{ make-current on the current footprint is a no-op and the recipe would never }
{ happen. The height is read back AFTER the switches - if a switch reloads    }
{ the footprint from its stored copy, a read before it would report a value   }
{ already gone - and changed comes from that final read (the pre-switch read  }
{ is reported too). The library DOCUMENT is marked modified                   }
{ (MarkDocDirtyByPath), which the old code never did. Reply: pending_save is  }
{ true only when the height changed AND the switch happened (a change without }
{ the switch is the case measured lost on save), written_to_disk false,       }
{ marked_modified as read back, made_current_after_write, and save_note says  }
{ what to do when the recipe did not run.                                     }
{..............................................................................}

Function Lib_SetFootprintHeight(Params : String; RequestId : String) : String;
Var
    CurLib : IPCB_Library;
    LibIter : IPCB_LibraryIterator;
    Footprint, Target, SavedCurrent : IPCB_LibComponent;
    FpWanted, HeightStr, FpName, Note, LibFile, SaveNote : String;
    HeightMM : Double;
    OldH, NewH, GotH, GotBefore : TCoord;
    Changed, MadeCurrent, Restored, Marked, WasCurrent, Parked, Pending : Boolean;
    MarkJson : String;
Begin
    FpWanted := Trim(ExtractJsonValue(Params, 'footprint_name'));
    HeightStr := Trim(ExtractJsonValue(Params, 'height_mm'));

    If HeightStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'height_mm is required');
        Exit;
    End;

    { StrToFloatDef, not StrToFloat in a Try. A raising conversion here
      halts on break-on-exception in the Script IDE even though the
      handler would have swallowed it, which is how a bad parameter
      turns into a stopped debugger. The sentinel doubles as the
      rejection for a negative value. }
    HeightMM := StrToFloatDef(HeightStr, -1);
    If HeightMM < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_HEIGHT',
            'height_mm must be a non-negative number in millimetres. Got: '
            + HeightStr);
        Exit;
    End;

    { PcbLibMayBeCurrent (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway, so the reply below comes without the modal. }
    CurLib := Nil;
    If PcbLibMayBeCurrent(0) Then CurLib := PCBServer.GetCurrentPCBLibrary;
    If CurLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB Library document focused');
        Exit;
    End;

    SavedCurrent := CurLib.CurrentComponent;
    Target := Nil;

    If FpWanted = '' Then Target := SavedCurrent
    Else
    Begin
        LibIter := CurLib.LibraryIterator_Create;
        Try
            LibIter.SetState_FilterAll;
            Footprint := LibIter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If UpperCase(FpName) = UpperCase(FpWanted) Then
                Begin
                    Target := Footprint;
                    Break;
                End;
                Footprint := LibIter.NextPCBObject;
            End;
        Finally
            CurLib.LibraryIterator_Destroy(LibIter);
        End;
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'No footprint named ' + FpWanted + ' in the focused library. '
            + 'Call lib_get_footprints to see what it holds.');
        Exit;
    End;

    FpName := '';
    Try FpName := Target.Name; Except End;
    OldH := Target.Height;
    NewH := MMToCoord(HeightMM);
    PCBServer.PreProcess;
    Try
        Try
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_BeginModify, c_NoEventData);
        Except End;
        Target.Height := NewH;
        Try
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_EndModify, c_NoEventData);
        Except End;
    Finally
        PCBServer.PostProcess;
    End;
    GotBefore := OldH;
    Try GotBefore := Target.Height; Except End;

    { SWITCH TO the footprint AFTER the write: the step that made the field  }
    { values reach the file. Already current: park elsewhere, switch back.   }
    { Not current: make it current, then hand the caller's one back.         }
    WasCurrent := SavedCurrent = Target;
    Parked := False;
    If WasCurrent Then
    Begin
        Parked := LibParkCurrentElsewhere(CurLib, Target);
        Restored := LibMakeCurrentVerified(CurLib, Target);
        MadeCurrent := Parked And Restored;
    End
    Else
    Begin
        MadeCurrent := LibMakeCurrentVerified(CurLib, Target);
        Restored := True;
        If SavedCurrent <> Nil Then
            Restored := LibMakeCurrentVerified(CurLib, SavedCurrent);
    End;
    Try CurLib.Board.ViewManager_FullUpdate; Except End;

    { The read that counts: after the switches. }
    GotH := OldH;
    Try GotH := Target.Height; Except End;
    Changed := GotH <> OldH;
    Pending := Changed And MadeCurrent;

    LibFile := '';
    Try LibFile := CurLib.Board.FileName; Except End;
    { Marked is what MarkDocDirty READ BACK, and mark says what it found.  }
    { MEASURED 2026-10-10: marked_modified read false on all three writes  }
    { while the library tab showed '*' - the old mark could not say        }
    { whether it had found the document at all.                            }
    MarkJson := 'null';
    If Changed Then
    Begin
        Marked := MarkDocDirty(LibFile);
        MarkJson := MarkOutcomeJson(0);
    End
    Else
        Marked := DocIsModified(LibFile);

    If (Not Changed) And (GotBefore <> OldH) Then
        SaveNote := 'The height read back as written straight after the write '
            + 'but as the OLD value after the footprint switch, so the switch '
            + 'discarded it. Nothing is pending; report this (the recipe '
            + 'this handler runs does not hold on this Altium).'
    Else If Not Changed Then
        SaveNote := 'Nothing changed in memory, so there is nothing to save.'
    Else If WasCurrent And (Not Parked) Then
        SaveNote := 'The footprint was already current and the library holds '
            + 'no other footprint to switch away to, so the switch after the '
            + 'write (the step that kept heights on save) could not run. '
            + 'Whether the save keeps this height is unmeasured: run '
            + 'lib_save_library, then read the height back from the file '
            + '(lib_read_pcblib_file).'
    Else If Not MadeCurrent Then
        SaveNote := 'The height is set in memory but the library could not '
            + 'switch to the footprint after the write, and heights written '
            + 'without that switch were measured lost on save. Make it '
            + 'current (lib_set_current_footprint) before lib_save_library, '
            + 'then read the height back from the file.'
    Else If Not Marked Then
        SaveNote := 'The height is set in memory but the library document '
            + 'did not read back as modified (mark says what was found). '
            + MarkSaveHint(False, True)
    Else
        SaveNote := 'The library is modified in memory and NOT saved. Run '
            + 'lib_save_library with the library path and check '
            + 'written_to_disk in its reply.';

    Note := '';
    If NewH = 0 Then
        Note := 'A height of 0 disables the placement-collision rule for '
              + 'this footprint rather than relaxing it, so nothing will be '
              + 'flagged against it however tall the real part is.';

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('name', FpName) + ',' +
            JsonFloat('old_height_mm', CoordToMM(OldH)) + ',' +
            JsonFloat('new_height_mm', CoordToMM(NewH)) + ',' +
            JsonFloat('read_back_height_mm', CoordToMM(GotH)) + ',' +
            JsonFloat('read_back_before_switch_mm', CoordToMM(GotBefore)) + ',' +
            JsonBool('changed', Changed) + ',' +
            JsonBool('was_current', WasCurrent) + ',' +
            JsonBool('parked_elsewhere_first', Parked) + ',' +
            JsonBool('made_current_after_write', MadeCurrent) + ',' +
            JsonBool('current_restored', Restored) + ',' +
            JsonBool('marked_modified', Marked) + ',' +
            JsonRaw('mark', MarkJson) + ',' +
            JsonBool('pending_save', Pending) + ',' +
            JsonBool('written_to_disk', False) + ',' +
            JsonBool('saved', False) + ',' +
            JsonStr('save_note', SaveNote) + ',' +
            JsonStr('note', Note)
        ));
End;


{..............................................................................}
{ Lib_SplitPinFunctions - parse each pin's slash-delimited name into its       }
{ function list (the alt-function popup). A pin named "PA0/TX/CTS" becomes     }
{ name "PA0" with functions TX, CTS. Operates on the current library symbol.   }
{..............................................................................}
Function Lib_SplitPinFunctions(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    Processed : Integer;
    WantName : String;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;
    { RESOLVED THROUGH THE LIBRARY, never straight off CurrentSchComponent:  }
    { SchIterator_Create on a component taken from there raised 'Undeclared  }
    { identifier' (measured, Lib_GetPinList), as a modal. The name comes     }
    { from TargetLibComponentName and FindLibComponentInMemory resolves it   }
    { (never the reopening lookup: SchLib is edited below). On a SHEET there }
    { is no library to look in, and GetTargetLibComponent answers the held   }
    { component as before, or Nil instead of the raising member read.       }
    Component := Nil;
    If SchLib.ObjectId = eSchLib Then
    Begin
        WantName := TargetLibComponentName(SchLib);
        If WantName <> '' Then
            Component := FindLibComponentInMemory(SchLib, WantName);
    End
    Else
        Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No current library component');
        Exit;
    End;

    Processed := 0;
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        Iter := Component.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            While Pin <> Nil Do
            Begin
                Try
                    Pin.SetState_FunctionsFromName;
                    Processed := Processed + 1;
                Except End;
                Pin := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, '');
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"pins_processed":' + IntToStr(Processed) + '}');
End;

{..............................................................................}
{ Lib_SetPinOwnerPart - reassign named pins of a multi-part symbol to a       }
{ sub-part, or to Part Zero (owner_part_id=0) so ONE pin is shared by the     }
{ whole package rather than redrawn at every sub-part origin. Part Zero is    }
{ Altium's documented placement for a multi-part component's supply pins.     }
{ Params: component_name (optional, defaults to the editor's current symbol), }
{         pin_designators (required, comma-separated), owner_part_id (req).   }
{..............................................................................}
Function Lib_SetPinOwnerPart(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    WantName, WantPins, OwnerStr, Haystack, Changed : String;
    OwnerId, ChangedCount, PartTotal : Integer;
    First : Boolean;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    WantPins := ExtractJsonValue(Params, 'pin_designators');
    { "3, 12" would otherwise build ',3, 12,' and match nothing, returning }
    { count 0 as if the pins did not exist.                                }
    WantPins := StringReplace(WantPins, ' ', '', MkSet(rfReplaceAll));
    OwnerStr := ExtractJsonValue(Params, 'owner_part_id');
    If (WantPins = '') Or (OwnerStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'pin_designators and owner_part_id are required');
        Exit;
    End;
    OwnerId := StrToIntDef(OwnerStr, -1);
    If OwnerId < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'owner_part_id must be 0 or greater');
        Exit;
    End;

    { Resolve through the library, never off the editor's current component:  }
    { SchIterator_Create is undeclared on a component fetched that way.       }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
        WantName := TargetLibComponentName(SchLib);
    If WantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No component is selected; pass component_name to name one');
        Exit;
    End;

    { LookupLibComponent, not the raw index. GetState_SchComponentByLibRef
      asks an index the library only builds when it LOADS, so a symbol
      created earlier in this same session is invisible to it: author a
      symbol, then move its pins to a sub-part in the very next call, and
      the second call reports COMPONENT_NOT_FOUND for a symbol that is
      plainly there. The helper tries the index, then walks the document,
      then the reference the script still holds from creation. }
    Component := LookupLibComponent(SchLib, WantName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in library: ' + WantName);
        Exit;
    End;

    { An OwnerPartId above the symbol's part count is accepted by the       }
    { assignment but maps to no displayable part, so the pin silently       }
    { disappears from every sub-part view. Refuse rather than corrupt. Part }
    { Zero is always legal. PartCount reads high by one on some symbols,    }
    { which only makes this bound permissive, never wrong.                  }
    PartTotal := 0;
    Try PartTotal := Component.PartCount; Except End;
    If PartTotal < 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PART_COUNT',
            'Could not read PartCount for ' + WantName + '; refusing to '
            + 'assign an unvalidated owner_part_id');
        Exit;
    End;
    If OwnerId > PartTotal Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'owner_part_id ' + IntToStr(OwnerId) + ' exceeds PartCount '
            + IntToStr(PartTotal) + ' for ' + WantName);
        Exit;
    End;

    Haystack := ',' + WantPins + ',';
    ChangedCount := 0;
    Changed := '';
    First := True;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        Iter := Component.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            While Pin <> Nil Do
            Begin
                If Pos(',' + Pin.Designator + ',', Haystack) > 0 Then
                Begin
                    Try
                        Pin.OwnerPartId := OwnerId;
                        If Not First Then Changed := Changed + ',';
                        First := False;
                        Changed := Changed + '"' + EscapeJsonString(Pin.Designator) + '"';
                        ChangedCount := ChangedCount + 1;
                    Except End;
                End;
                Pin := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, '');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"component":"' + EscapeJsonString(WantName) +
        '","owner_part_id":' + IntToStr(OwnerId) +
        ',"pins_changed":[' + Changed + ']' +
        ',"count":' + IntToStr(ChangedCount) + '}');
End;

{..............................................................................}
{ BatchFieldPresent - True when Op carries Key at all, even with an empty      }
{ value. GetBatchField returns '' for both "name=" and "no name field", and an }
{ EDIT has to tell them apart: absent leaves the property alone, empty clears  }
{ it. Same parse as GetBatchField (fields on ';', key on the first '=').       }
{..............................................................................}
Function BatchFieldPresent(Op : String; Key : String) : Boolean;
Var
    Remaining, Field, FKey : String;
    SepPos, EqPos : Integer;
Begin
    Result := False;
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
            If UpperCase(FKey) = UpperCase(Key) Then
            Begin
                Result := True;
                Exit;
            End;
        End;
    End;
End;

{ True when S is a word the shared electrical chain understands.               }
{ StrToPinElectrical falls back to passive for ANY unknown word, which is the  }
{ right default for authoring and the wrong one for an edit: a typo would turn }
{ a pin passive and report success. Asked of the chain itself rather than of a }
{ second word list, so the vocabulary is still stated once, in Utils.pas.     }
Function PinElectricalWordKnown(S : String) : Boolean;
Var
    LS : String;
Begin
    LS := LowerCase(Trim(S));
    LS := StringReplace(LS, '_', '', MkSet(rfReplaceAll));
    Result := (StrToPinElectrical(S) <> eElectricPassive)
        Or (LS = 'passive') Or (LS = 'eelectricpassive');
End;

{ One library pin as read back after an edit. Same vocabulary as             }
{ lib_get_pin_list, so a read can be fed straight into the next write.        }
Function LibPinStateJson(Pin : ISch_Pin) : String;
Var
    ElecStr : String;
    ShowN, ShowD, Hid : Boolean;
    Owner, Mode, LenMils : Integer;
Begin
    ElecStr := '';
    ShowN := False;
    ShowD := False;
    Hid := False;
    Owner := -1;
    Mode := -1;
    LenMils := 0;
    Try ElecStr := PinElectricalToStr(Pin.Electrical); Except End;
    If ElecStr = 'io' Then ElecStr := 'bidirectional';
    Try ShowN := Pin.ShowName; Except End;
    Try ShowD := Pin.ShowDesignator; Except End;
    Try Hid := Pin.IsHidden; Except End;
    Try Owner := Pin.OwnerPartId; Except End;
    Try Mode := Pin.OwnerPartDisplayMode; Except End;
    Try LenMils := CoordToMils(Pin.PinLength); Except End;
    Result := '{"designator":"' + EscapeJsonString(Pin.Designator) + '"'
        + ',"name":"' + EscapeJsonString(Pin.Name) + '"'
        + ',"electrical_type":"' + ElecStr + '"'
        + ',"show_name":' + BoolToJsonStr(ShowN)
        + ',"show_designator":' + BoolToJsonStr(ShowD)
        + ',"hidden":' + BoolToJsonStr(Hid)
        + ',"length":' + IntToStr(LenMils)
        + ',"owner_part_id":' + IntToStr(Owner)
        + ',"display_mode":' + IntToStr(Mode) + '}';
End;

{..............................................................................}
{ Lib_SetPinProperties - edit existing pins of a LIBRARY symbol in place.      }
{                                                                              }
{ The only route to a pin-name fix used to be deleting and recreating the      }
{ whole symbol, which loses its parameters, models and graphics. Library-scope }
{ obj_modify matched pins but could not renumber one: its Designator write     }
{ went to the component-text helper, which ignores pins.                       }
{                                                                              }
{ Pins are reached through the COMPONENT's own iterator, which yields every    }
{ part's pins at once (the same walk lib_get_pin_list reads), so a pin on      }
{ part 3 needs no @3 scope. owner_part_id narrows an edit to one part.        }
{                                                                              }
{ Each op names the pin by its CURRENT designator and lists only the fields   }
{ to change. Two passes per op: the first counts matches and refuses a        }
{ renumber that would collide with another pin or that is ambiguous (two      }
{ pins with this number on the same part and display mode); the second        }
{ writes and reads every changed pin back. Ops apply in order, so op N sees   }
{ the renumbers made by ops before it.                                        }
{                                                                              }
{ Params: component_name (required), library_path (optional), pins (required, }
{   '~~'-separated ops, ';'-separated key=value fields): designator           }
{   (required), owner_part_id, name, new_designator, electrical_type,        }
{   show_name, show_designator, hidden, length (mils).                         }
{..............................................................................}
Function Lib_SetPinProperties(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, PinsStr, Remaining, Op : String;
    Desig, NewDesig, NewName, ElecStr, ShowNameStr, ShowDesigStr : String;
    HiddenStr, LenStr, OwnerStr, Reason, SeenKeys, Key : String;
    PinsJson, OpsJson, Details : String;
    HasName, Ambiguous, FirstPin, Verified : Boolean;
    OwnerFilter, Matched, Clashes, OpCount, OkOps, FailedOps : Integer;
    PinsChanged, PinOwner, PinMode, WantLen : Integer;
    SchLib, Fresh : ISch_Lib;
    Component : ISch_Component;
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    PinsStr := ExtractJsonValue(Params, 'pins');
    If (CompName = '') Or (PinsStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'component_name and pins are required');
        Exit;
    End;

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;
    Fresh := RefetchSchLib(LibPath);
    If Fresh <> Nil Then SchLib := Fresh;

    OpsJson := '';
    OpCount := 0;
    OkOps := 0;
    FailedOps := 0;
    PinsChanged := 0;
    Remaining := PinsStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            Inc(OpCount);

            Desig := GetBatchField(Op, 'designator');
            NewDesig := GetBatchField(Op, 'new_designator');
            HasName := BatchFieldPresent(Op, 'name');
            NewName := GetBatchField(Op, 'name');
            ElecStr := GetBatchField(Op, 'electrical_type');
            ShowNameStr := LowerCase(GetBatchField(Op, 'show_name'));
            ShowDesigStr := LowerCase(GetBatchField(Op, 'show_designator'));
            HiddenStr := LowerCase(GetBatchField(Op, 'hidden'));
            LenStr := GetBatchField(Op, 'length');
            OwnerStr := GetBatchField(Op, 'owner_part_id');

            OwnerFilter := -1;
            If OwnerStr <> '' Then OwnerFilter := StrToIntDef(OwnerStr, -1);
            WantLen := StrToIntDef(LenStr, 0);

            Reason := '';
            If Desig = '' Then Reason := 'MISSING_DESIGNATOR'
            Else If (OwnerStr <> '') And (OwnerFilter < 0) Then Reason := 'BAD_OWNER_PART_ID'
            Else If (ElecStr <> '') And (Not PinElectricalWordKnown(ElecStr)) Then Reason := 'BAD_ELECTRICAL_TYPE'
            Else If (LenStr <> '') And ((Not IsIntStr(LenStr)) Or (WantLen <= 0)) Then Reason := 'BAD_LENGTH';

            { Pass 1: count matches, find renumber collisions and ambiguity. }
            Matched := 0;
            Clashes := 0;
            Ambiguous := False;
            SeenKeys := '|';
            If Reason = '' Then
            Begin
                Iter := Component.SchIterator_Create;
                Try
                    Iter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := Iter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        PinOwner := -1;
                        PinMode := -1;
                        Try PinOwner := Pin.OwnerPartId; Except End;
                        Try PinMode := Pin.OwnerPartDisplayMode; Except End;
                        If Pin.Designator = Desig Then
                        Begin
                            If (OwnerFilter < 0) Or (PinOwner = OwnerFilter) Then
                            Begin
                                Inc(Matched);
                                Key := IntToStr(PinOwner) + ':' + IntToStr(PinMode) + '|';
                                If Pos('|' + Key, SeenKeys) > 0 Then Ambiguous := True
                                Else SeenKeys := SeenKeys + Key;
                            End;
                        End
                        Else If (NewDesig <> '') And (Pin.Designator = NewDesig) Then
                            Inc(Clashes);
                        Pin := Iter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(Iter);
                End;

                If Matched = 0 Then Reason := 'PIN_NOT_FOUND'
                Else If (NewDesig <> '') And (NewDesig <> Desig) And (Clashes > 0) Then Reason := 'NEW_DESIGNATOR_EXISTS'
                Else If (NewDesig <> '') And (NewDesig <> Desig) And Ambiguous Then Reason := 'AMBIGUOUS_DESIGNATOR';
            End;

            { Pass 2: write, then read every changed pin back. }
            PinsJson := '';
            FirstPin := True;
            Verified := True;
            If Reason = '' Then
            Begin
                Iter := Component.SchIterator_Create;
                Try
                    Iter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := Iter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        PinOwner := -1;
                        Try PinOwner := Pin.OwnerPartId; Except End;
                        If (Pin.Designator = Desig) And ((OwnerFilter < 0) Or (PinOwner = OwnerFilter)) Then
                        Begin
                            Try
                                SchBeginModify(Pin);
                                If HasName Then Pin.Name := NewName;
                                If ElecStr <> '' Then Pin.Electrical := StrToPinElectrical(ElecStr);
                                If ShowNameStr <> '' Then Pin.ShowName := (ShowNameStr = 'true') Or (ShowNameStr = '1');
                                If ShowDesigStr <> '' Then Pin.ShowDesignator := (ShowDesigStr = 'true') Or (ShowDesigStr = '1');
                                If HiddenStr <> '' Then Pin.IsHidden := (HiddenStr = 'true') Or (HiddenStr = '1');
                                If LenStr <> '' Then Pin.PinLength := MilsToCoord(WantLen);
                                If (NewDesig <> '') And (NewDesig <> Desig) Then Pin.Designator := NewDesig;
                                SchEndModify(Pin);
                            Except
                                Reason := 'WRITE_RAISED';
                            End;
                            Inc(PinsChanged);
                            If HasName And (Pin.Name <> NewName) Then Verified := False;
                            If (ElecStr <> '') And (Pin.Electrical <> StrToPinElectrical(ElecStr)) Then Verified := False;
                            If (LenStr <> '') And (CoordToMils(Pin.PinLength) <> WantLen) Then Verified := False;
                            If (NewDesig <> '') And (Pin.Designator <> NewDesig) Then Verified := False;
                            If Not FirstPin Then PinsJson := PinsJson + ',';
                            FirstPin := False;
                            PinsJson := PinsJson + LibPinStateJson(Pin);
                        End;
                        Pin := Iter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(Iter);
                End;
            End;

            If (Reason = '') And Verified Then Inc(OkOps) Else Inc(FailedOps);
            If OpsJson <> '' Then OpsJson := OpsJson + ',';
            OpsJson := OpsJson + '{"designator":"' + EscapeJsonString(Desig) + '"'
                + ',"matched":' + IntToStr(Matched)
                + ',"applied":' + BoolToJsonStr(Reason = '')
                + ',"verified":' + BoolToJsonStr((Reason = '') And Verified)
                + ',"reason":"' + Reason + '"'
                + ',"pins":[' + PinsJson + ']}';
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Set pin properties');
    End;

    If PinsChanged > 0 Then MarkLibDirty(SchLib);

    Details := '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"total":' + IntToStr(OpCount)
        + ',"applied":' + IntToStr(OkOps)
        + ',"failed":' + IntToStr(FailedOps)
        + ',"pins_changed":' + IntToStr(PinsChanged)
        + ',"ops":[' + OpsJson + ']}';
    If PinsChanged = 0 Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'NO_PIN_CHANGED',
            'No pin was changed; each op in details says why.', Details);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, Details);
End;

{..............................................................................}
{ Lib_InstallLibrary / Lib_UninstallLibrary - register or unregister a library }
{ (.IntLib / .SchLib / .PcbLib) with the environment's Available Libraries.    }
{..............................................................................}
Function Lib_InstallLibrary(Params : String; RequestId : String) : String;
Var
    Path : String;
    Ok : Boolean;
Begin
    Path := ExtractJsonValue(Params, 'library_path');
    If Path = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;
    Ok := False;
    { An ABSOLUTE path with no file behind it cannot be installed; asking    }
    { raised (a modal) and the reply was installed:false. Answer that        }
    { without asking. A relative path goes ahead as before.                  }
    If Not (LooksAbsolutePath(Trim(Path)) And (Not FileExists(Trim(Path)))) Then
        Try
            IntegratedLibraryManager.InstallLibrary(Path);
            Ok := True;
        Except
            RecordCaughtException('Lib_InstallLibrary:InstallLibrary');
        End;
    Result := BuildSuccessResponse(RequestId,
        '{"installed":' + BoolToJsonStr(Ok) + ',"library_path":"'
        + EscapeJsonString(Path) + '"}');
End;

{ Could Path be an installed library? False only when the installed list was  }
{ READ and no entry carries Path's file name - the case UnInstallLibrary has  }
{ nothing to act on and raises. An unreadable list or entry answers True, so  }
{ the call goes ahead exactly as before. A file-NAME match, not a path match, }
{ so a path spelled differently from the stored one is never refused.         }
Function InstalledLibNameKnown(Path : String) : Boolean;
Var
    I, N : Integer;
    P, Want : String;
    Unread : Boolean;
Begin
    Result := True;
    Want := DocNameKey(Path);
    If Want = '' Then Exit;
    N := -1;
    Try N := IntegratedLibraryManager.InstalledLibraryCount; Except N := -1; End;
    If N < 0 Then Exit;
    For I := 0 To N - 1 Do
    Begin
        P := '';
        Unread := False;
        Try P := IntegratedLibraryManager.InstalledLibraryPath(I); Except Unread := True; End;
        If Unread Or (DocNameKey(P) = Want) Then Exit;
    End;
    Result := False;
End;

Function Lib_UninstallLibrary(Params : String; RequestId : String) : String;
Var
    Path : String;
    Ok : Boolean;
Begin
    Path := ExtractJsonValue(Params, 'library_path');
    If Path = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;
    Ok := False;
    { Not installed (no installed entry even carries the file name): the   }
    { call raised and the reply was uninstalled:false. Answer that without  }
    { asking (InstalledLibNameKnown).                                       }
    If InstalledLibNameKnown(Path) Then
        Try
            IntegratedLibraryManager.UnInstallLibrary(Path);
            Ok := True;
        Except
            RecordCaughtException('Lib_UninstallLibrary:UnInstallLibrary');
        End;
    Result := BuildSuccessResponse(RequestId,
        '{"uninstalled":' + BoolToJsonStr(Ok) + ',"library_path":"'
        + EscapeJsonString(Path) + '"}');
End;

{..............................................................................}
{ Lib_GetInstalledLibraries - what the environment has installed.              }
{                                                                              }
{ install_library and uninstall_library have been here from the start and      }
{ nothing could report the result, so the only way to answer "what is          }
{ installed?" was to read the registry from outside Altium. lib_search only    }
{ walks SchLibs already open in the workspace, which is a different and much   }
{ smaller set.                                                                 }
{                                                                              }
{ TWO LISTS, NOT ONE, and they are not interchangeable. Installed* is what is  }
{ switched on for the current environment; Available* is every library known   }
{ to it. The TYPE is published only on the Available side, so the type of an   }
{ installed library is found by matching its path across, which is what the    }
{ published example scripts do.                                                }
{                                                                              }
{ The type ordinal is returned as an Integer and named separately rather than  }
{ compared against enum identifiers: an identifier this build does not declare }
{ faults at runtime as a modal the polling loop cannot catch, and the ordinals }
{ are stable where the names are not.                                          }
{ Params: with_counts (optional, "false" skips the per-library component count,}
{         which opens each library and is the expensive half).                 }
{..............................................................................}
Function LibTypeName(Ordinal : Integer) : String;
Begin
    { TLibraryType, in declaration order. }
    If Ordinal = 0 Then Result := 'integrated'
    Else If Ordinal = 1 Then Result := 'source'
    Else If Ordinal = 2 Then Result := 'datafile'
    Else If Ordinal = 3 Then Result := 'database'
    Else If Ordinal = 4 Then Result := 'none'
    Else If Ordinal = 5 Then Result := 'query'
    Else If Ordinal = 6 Then Result := 'design_items'
    Else Result := 'unknown';
End;

Function InstalledLibTypeOrdinal(LibPath : String) : Integer;
Var
    I, AvailCount : Integer;
Begin
    { -1 means the path is installed but absent from the Available list, }
    { which is a real state worth reporting rather than flattening to a  }
    { type name that would then be wrong.                                }
    Result := -1;
    AvailCount := 0;
    Try AvailCount := IntegratedLibraryManager.AvailableLibraryCount; Except End;
    For I := 0 To AvailCount - 1 Do
    Begin
        Try
            If IntegratedLibraryManager.AvailableLibraryPath(I) = LibPath Then
            Begin
                Result := IntegratedLibraryManager.AvailableLibraryType(I);
                Break;
            End;
        Except
        End;
    End;
End;

Function Lib_GetInstalledLibraries(Params : String; RequestId : String) : String;
Var
    JsonItems, LibPath, WithCounts : String;
    I, InstCount, AvailCount, TypeOrd, CompCount : Integer;
    First, WantCounts : Boolean;
Begin
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;

    WithCounts := ExtractJsonValue(Params, 'with_counts');
    WantCounts := (WithCounts <> 'false') And (WithCounts <> 'False') And (WithCounts <> '0');

    InstCount := 0;
    Try InstCount := IntegratedLibraryManager.InstalledLibraryCount; Except End;
    AvailCount := 0;
    Try AvailCount := IntegratedLibraryManager.AvailableLibraryCount; Except End;

    JsonItems := '';
    First := True;
    For I := 0 To InstCount - 1 Do
    Begin
        LibPath := '';
        Try LibPath := IntegratedLibraryManager.InstalledLibraryPath(I); Except End;
        If LibPath = '' Then Continue;

        TypeOrd := InstalledLibTypeOrdinal(LibPath);

        { GetComponentCount opens the library to answer, so it is the one }
        { expensive call here and the caller can decline it. -1 says not  }
        { asked, which is not the same as an empty library.               }
        CompCount := -1;
        { An installed library whose FILE has since gone: GetComponentCount  }
        { opens it, raised, and left -1. Leave -1 without asking. Entries    }
        { that are not plain files (relative or server paths) ask as before. }
        If WantCounts And (Not (LooksAbsolutePath(LibPath) And (Not FileExists(LibPath)))) Then
        Begin
            Try CompCount := IntegratedLibraryManager.GetComponentCount(LibPath); Except End;
        End;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"library_path":"' + EscapeJsonString(LibPath) + '"'
            + ',"file_name":"' + EscapeJsonString(ExtractFileName(LibPath)) + '"'
            + ',"library_type":"' + LibTypeName(TypeOrd) + '"'
            + ',"library_type_ordinal":' + IntToStr(TypeOrd)
            + ',"component_count":' + IntToStr(CompCount) + '}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"libraries":[' + JsonItems + ']'
        + ',"installed_count":' + IntToStr(InstCount)
        + ',"available_count":' + IntToStr(AvailCount)
        + ',"counts_included":' + BoolToJsonStr(WantCounts) + '}');
End;

{ Lib_DeleteComponent - remove one symbol from a schematic library (.SchLib).  }
{ Mirrors the overwrite path in Lib_CopyComponent: focus the lib, resolve the  }
{ component by LibReference, RemoveSchComponent, then mark the lib dirty for    }
{ deferred save. Deletes a single named part; hard error if the name is not    }
{ found (no silent no-op, no wildcard mass-delete).                            }
{ Params: component_name (required, the LibReference), library_path (optional, }
{         defaults to the focused document).                                   }
Function Lib_DeleteComponent(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, CompName, RespJson : String;
    ResErrCode, ResErrMsg : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component : ISch_Component;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    If (CompName = '') And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;
    Component := ResolveLibComponent(SchLib, LibPath, Params, CompName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    { Drop the held reference first if this is it: once removed, the next    }
    { by-name lookup would hand it back and its first member read is the    }
    { modal (see ForgetLibComponentIf).                                     }
    ForgetLibComponentIf(SchLib, Component, CompName);
    SchLib.RemoveSchComponent(Component);
    SchServer.ProcessControl.PostProcess(SchLib, 'Delete component');
    MarkLibDirty(SchLib);

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"deleted":"' + EscapeJsonString(CompName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RenameComponent - set one component's LibReference to a clean new_name.  }
{ The point of the tool: a symbol whose current name carries bytes a caller    }
{ cannot reproduce (an embedded '"', or a control char from a broken import)   }
{ is addressable by component_index, so this is the way to give such a part a  }
{ clean name and make every name-based tool reach it again.                     }
{ Rename technique matches Lib_BatchRename: RemoveSchComponent, set            }
{ LibReference, AddSchComponent, so the library's internal by-name index is    }
{ rebuilt on the new name.                                                      }
{ Params: new_name (required), plus component_index OR component_name to pick   }
{         the target, library_path (optional, focused default).                }
Function Lib_RenameComponent(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, OldName, NewName, RespJson : String;
    ResErrCode, ResErrMsg : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component, Existing : ISch_Component;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    NewName := ExtractJsonValue(Params, 'new_name');
    If NewName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'new_name is required');
        Exit;
    End;
    If (ExtractJsonValue(Params, 'component_name') = '')
        And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index to pick the target');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;

    Component := ResolveLibComponent(SchLib, LibPath, Params, OldName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    { Refuse to collide with an existing part. If new_name already resolves }
    { and it is a different object, the rename would create a duplicate.    }
    { In memory only: the reopening lookup would close SchLib under us, and }
    { the rename would land on a component in a closed document. See       }
    { FindLibComponentInMemory.                                             }
    Existing := FindLibComponentInMemory(SchLib, NewName);
    If (Existing <> Nil) And (Existing <> Component) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS',
            'A different component named "' + NewName + '" already exists in ' + LibPath);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        SchLib.RemoveSchComponent(Component);
        Component.LibReference := NewName;
        SchLib.AddSchComponent(Component);
        { RE-ASSERT AFTER THE ADD. AddSchComponent overrides LibReference }
        { with an auto-generated Component_<N> on the second and later    }
        { additions to a SchLib in one session, so setting it before the  }
        { add is not enough and the rename lands on a name nobody asked   }
        { for. Lib_CreateSymbol carries the same re-assertion.            }
        Component.LibReference := NewName;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Rename component');
    End;
    SchLib.GraphicallyInvalidate;
    RememberLibComponent(SchLib, Component, NewName);
    MarkLibDirty(SchLib);

    { VERIFY BOTH DIRECTIONS. A rename is only done if the new name       }
    { resolves AND the old one no longer does; checking just the first    }
    { would pass a copy, and checking neither is what reported success    }
    { while the old name was still sitting in the library.                }
    Existing := Nil;
    Try Existing := LookupLibComponent(SchLib, NewName); Except End;
    If Existing = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('library_path', LibPath) + ',' +
                JsonStr('old_name', OldName) + ',' +
                JsonStr('new_name', NewName) + ',' +
                JsonStr('reason', 'the component does not resolve under '
                    + 'new_name after the rename, so the library still '
                    + 'holds whatever it held before.')
            ));
        Exit;
    End;

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"old_name":"' + EscapeJsonString(OldName) + '"' +
        ',"new_name":"' + EscapeJsonString(NewName) + '"' +
        ',"verified":true}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_DeleteFootprint - remove one footprint from a PCB library (.PcbLib).     }
{ Finds the footprint by Name via a LibraryIterator (break BEFORE deleting to  }
{ avoid iterator invalidation), then RemoveComponent + DeRegisterComponent per }
{ the reference DeleteSelectedItemsInPcbLib pattern, inside PreProcess/         }
{ PostProcess, and saves the .PcbLib. Hard error if the name is not found.     }
{ Params: footprint_name (required), library_path (optional, focused default). }
Function Lib_DeleteFootprint(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    PCBServer.PreProcess;
    Try PcbLib.RemoveComponent(Target); Except End;
    Try PcbLib.DeRegisterComponent(Target); Except End;
    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);

    { SAY THAT THE WRITE IS DEFERRED. The footprint is gone from the
      in-memory library and the file on disk still contains it until a
      flush. Reported by a user who read success, reloaded, and found the
      footprint still there with an unchanged timestamp. The docstring is
      not enough: the reply is what a caller reads. }
    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"deleted":"' + EscapeJsonString(FpWanted) + '"' +
        ',"written_to_disk":false,"pending_save":true' +
        ',"note":"removed in memory and the library marked dirty. The file '
        + 'still contains it until lib_save_library flushes."}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RemoveModel - remove implementations (models) from a SchLib component by  }
{ ModelName, via Component.RemoveSchImplementation. Re-scans after each removal  }
{ to dodge iterator invalidation. keep_one=true keeps the first match and       }
{ removes the rest (dedup); keep_one=false removes every match.                 }
{ Params: component_name, model_name (required), keep_one (bool),               }
{         library_path (optional).                                              }
Function Lib_RemoveModel(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, ModelName, KeepStr, RespJson, CurName : String;
    KeepOne : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl, Found : ISch_Implementation;
    Matches, Threshold, Removed, Guard : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    ModelName := ExtractJsonValue(Params, 'model_name');
    KeepStr := ExtractJsonValue(Params, 'keep_one');
    KeepOne := (KeepStr = 'true') Or (KeepStr = 'True') Or (KeepStr = '1');
    If (CompName = '') Or (ModelName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name and model_name are required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Threshold := 0;
    If KeepOne Then Threshold := 1;
    Removed := 0;
    Guard := 1000;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    While Guard > 0 Do
    Begin
        Matches := 0;
        Found := Nil;
        ImplIter := Component.SchIterator_Create;
        Try
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Impl := ImplIter.FirstSchObject;
            While Impl <> Nil Do
            Begin
                CurName := '';
                Try CurName := Impl.ModelName; Except End;
                If CurName = ModelName Then
                Begin
                    Inc(Matches);
                    If Found = Nil Then Found := Impl;
                End;
                Impl := ImplIter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(ImplIter);
        End;

        If (Matches <= Threshold) Or (Found = Nil) Then Break;
        Try Component.RemoveSchImplementation(Found); Except End;
        Inc(Removed);
        Dec(Guard);
    End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Remove model');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"model_name":"' + EscapeJsonString(ModelName) + '"'
        + ',"removed":' + IntToStr(Removed)
        + ',"kept_one":' + BoolToJsonStr(KeepOne) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RenameFootprint - rename a footprint in a PcbLib (footprint.Name := new). }
{ Errors if the source name is missing or the new name already exists.          }
{ Params: footprint_name, new_name (required), library_path (optional).         }
Function Lib_RenameFootprint(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, NewName, CurName, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    Clash : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpName := ExtractJsonValue(Params, 'footprint_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    If (FpName = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name and new_name are required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Clash := False;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            CurName := '';
            Try CurName := Footprint.Name; Except End;
            If CurName = FpName Then Target := Footprint;
            If CurName = NewName Then Clash := True;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + LibPath + ': ' + FpName);
        Exit;
    End;
    If Clash Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS', 'A footprint named "' + NewName + '" already exists in ' + LibPath);
        Exit;
    End;

    PCBServer.PreProcess;
    Try Target.Name := NewName; Except End;
    PCBServer.PostProcess;
    Try PcbLib.Board.ViewManager_FullUpdate; Except End;
    Try PcbLib.RefreshView; Except End;
    MarkDocDirtyByPath(PcbLib.Board.FileName);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"footprint":"' + EscapeJsonString(FpName) + '"'
        + ',"new_name":"' + EscapeJsonString(NewName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RenameFootprints - rename MANY footprints of one PcbLib in ONE call.     }
{                                                                              }
{ The batch sibling of Lib_RenameFootprint, built from exactly its members:    }
{ the same LibraryIterator walk, the same Name read and write, the same view  }
{ refresh and dirty mark. The library is focused with FocusPcbLibForEdit,     }
{ the same open plus the post-open check the other PcbLib writes make, built  }
{ from the same workspace calls. Nothing on an Altium interface is used here  }
{ that shipped code does not already use, which is what makes it safe to run  }
{ before a live session has.                                                  }
{                                                                              }
{ WHY A BATCH. lib_ipc_batch_generate renames every footprint the IPC wizard   }
{ wrote with ',' for '.', and one round trip per name made that the slowest    }
{ part of a batch. One rename that fails must not abandon the rest, so each    }
{ op gets its own status rather than the whole call an error.                  }
{                                                                              }
{ READ BACK, NOT ASSUMED. Each written Name is read again and the status says  }
{ 'unchanged' when it did not take.                                            }
{                                                                              }
{ Params: library_path (optional; defaults to the focused library),           }
{         renames (required): '~~'-separated ops, each 'old=<name>;new=<name>'.}
{ Reply:  renamed, failed, total, results (old, new, status, read_back per  }
{         op), pending_save true / written_to_disk false: it MARKS, never   }
{         writes.                                                          }
{..............................................................................}
Function Lib_RenameFootprints(Params : String; RequestId : String) : String;
Var
    LibPath, RenamesStr, Remaining, Op, ErrCode, ErrMsg : String;
    OldName, NewName, CurName, Status, ReadBack, ItemsJson : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    Clash : Boolean;
    OpCount, Renamed, FailedCount : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    RenamesStr := ExtractJsonValue(Params, 'renames');
    If RenamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'renames is required: old=<name>;new=<name> ops separated by ~~');
        Exit;
    End;

    { Focused and then VERIFIED in front, as every other PcbLib write here    }
    { is: an open that silently fails leaves the previous library focused,  }
    { and the renames would land in it while the reply named this path.     }
    PcbLib := FocusPcbLibForEdit(LibPath, ErrCode, ErrMsg);
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, ErrMsg);
        Exit;
    End;

    ItemsJson := '';
    OpCount := 0;
    Renamed := 0;
    FailedCount := 0;
    Remaining := RenamesStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            Inc(OpCount);
            OldName := GetBatchField(Op, 'old');
            NewName := GetBatchField(Op, 'new');
            Status := '';
            ReadBack := '';

            If (OldName = '') Or (NewName = '') Then
                Status := 'missing_field'
            Else If OldName = NewName Then
                Status := 'same_name'
            Else
            Begin
                { One walk per op, as Lib_RenameFootprint does. A rename    }
                { earlier in the batch changes what later ops see, so the   }
                { library is read fresh each time rather than cached.       }
                Target := Nil;
                Clash := False;
                Iter := PcbLib.LibraryIterator_Create;
                Try
                    Footprint := Iter.FirstPCBObject;
                    While Footprint <> Nil Do
                    Begin
                        CurName := '';
                        Try CurName := Footprint.Name; Except End;
                        If CurName = OldName Then Target := Footprint;
                        If CurName = NewName Then Clash := True;
                        Footprint := Iter.NextPCBObject;
                    End;
                Finally
                    PcbLib.LibraryIterator_Destroy(Iter);
                End;

                If Target = Nil Then
                    Status := 'not_found'
                Else If Clash Then
                    Status := 'name_exists'
                Else
                Begin
                    Try Target.Name := NewName; Except End;
                    Try ReadBack := Target.Name; Except ReadBack := ''; End;
                    If ReadBack = NewName Then
                        Status := 'renamed'
                    Else
                        Status := 'unchanged';
                End;
            End;

            If Status = 'renamed' Then
                Inc(Renamed)
            Else
                Inc(FailedCount);
            If ItemsJson <> '' Then ItemsJson := ItemsJson + ',';
            ItemsJson := ItemsJson
                + '{"old":"' + EscapeJsonString(OldName) + '"'
                + ',"new":"' + EscapeJsonString(NewName) + '"'
                + ',"status":"' + Status + '"'
                + ',"read_back":"' + EscapeJsonString(ReadBack) + '"}';
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try PcbLib.Board.ViewManager_FullUpdate; Except End;
    Try PcbLib.RefreshView; Except End;
    If Renamed > 0 Then MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"renamed":' + IntToStr(Renamed)
        + ',"failed":' + IntToStr(FailedCount)
        + ',"total":' + IntToStr(OpCount)
        + ',"pending_save":true,"written_to_disk":false'
        + ',"results":[' + ItemsJson + ']}');
End;

{ Lib_SaveLibrary - write ONE open library to disk, and report what landed.    }
{                                                                              }
{ app_save_all writes EVERY open document, and every save rewrites the open    }
{ SchDocs and PcbDocs with fresh parameter UniqueIDs (measured on a live       }
{ project, 2026-10-02), so saving one library that way puts churn into every   }
{ open sheet. This saves the one IServerDocument at library_path.              }
{                                                                              }
{ The calls are SaveOneDocByDocRef's, unchanged: SetModified(True) then        }
{ DoFileSave(''), the unconditional flush that Main.pas explains (the Modified }
{ flag does not always propagate from ProcessControl, so a save gated on it    }
{ can skip a dirty document). Only Client and IServerDocument are touched, so  }
{ this is safe for a SchLib and does not need the PCB server loaded.           }
{                                                                              }
{ DoFileSave does not raise when the editor declines, so the reply carries     }
{ the evidence instead of a verdict: FileAge before and after, and Modified    }
{ before and after. FileAge has two-second resolution, so the Python side      }
{ adds its own nanosecond mtime and decides.                                   }
{                                                                              }
{ Params: library_path (required).                                            }
{..............................................................................}
Function Lib_SaveLibrary(Params : String; RequestId : String) : String;
Var
    LibPath : String;
    ServerDoc : IServerDocument;
    AgeBefore, AgeAfter : Integer;
    WasModified, StillModified, ExistsAfter, BridgeEdited : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'library_path is required');
        Exit;
    End;

    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(LibPath); Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'Not open in Altium, so it holds no unsaved edits: ' + LibPath);
        Exit;
    End;

    WasModified := False;
    Try WasModified := ModifiedFlagOf(ServerDoc); Except WasModified := False; End;
    { was_modified is the flag, which read false after real height edits  }
    { (2026-10-10). bridge_edited says whether THIS bridge marked the file }
    { since it was last written, which the flag cannot be trusted to say. }
    BridgeEdited := BridgeEditRecorded(LibPath)
        Or BridgeEditRecorded(ServerDocFileName(ServerDoc));
    AgeBefore := -1;
    Try AgeBefore := FileAge(LibPath); Except AgeBefore := -1; End;

    Try ServerDoc.SetModified(True); Except End;
    Try ServerDoc.DoFileSave(''); Except End;

    AgeAfter := -1;
    Try AgeAfter := FileAge(LibPath); Except AgeAfter := -1; End;
    StillModified := False;
    Try StillModified := ModifiedFlagOf(ServerDoc); Except StillModified := False; End;
    ExistsAfter := FileExists(LibPath);
    If (AgeAfter <> -1) And (AgeAfter <> AgeBefore) Then
    Begin
        ForgetBridgeEdit(LibPath);
        ForgetBridgeEdit(ServerDocFileName(ServerDoc));
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"was_modified":' + BoolToJsonStr(WasModified)
        + ',"bridge_edited":' + BoolToJsonStr(BridgeEdited)
        + ',"still_modified":' + BoolToJsonStr(StillModified)
        + ',"file_exists":' + BoolToJsonStr(ExistsAfter)
        + ',"file_age_before":' + IntToStr(AgeBefore)
        + ',"file_age_after":' + IntToStr(AgeAfter)
        + ',"file_age_changed":' + BoolToJsonStr(AgeBefore <> AgeAfter) + '}');
End;

{ Look up a rename in a ';'-separated 'old=new' map string; Default if absent.  }
Function LookupRename(MapStr, OldName, Default : String) : String;
Var
    Remaining, Pair, K, V : String;
    SemiPos, EqPos : Integer;
Begin
    Result := Default;
    Remaining := MapStr;
    While Remaining <> '' Do
    Begin
        SemiPos := Pos(';', Remaining);
        If SemiPos > 0 Then
        Begin
            Pair := Copy(Remaining, 1, SemiPos - 1);
            Remaining := Copy(Remaining, SemiPos + 1, Length(Remaining));
        End
        Else
        Begin
            Pair := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('=', Pair);
        If EqPos > 0 Then
        Begin
            K := Copy(Pair, 1, EqPos - 1);
            V := Copy(Pair, EqPos + 1, Length(Pair));
            If K = OldName Then
            Begin
                Result := V;
                Exit;
            End;
        End;
    End;
End;

{ Lib_SetModelName - set a single implementation's ModelName (footprint         }
{ reference) on a SchLib component. Targets the model whose current ModelName =  }
{ model_name; if model_name is empty, the current (IsCurrent) model, else the    }
{ first. A targeted spot-edit; the bulk rebuild is Lib_NormalizeImplementations. }
{ Params: component_name, new_model_name (required), model_name (optional),      }
{         library_path (optional).                                              }
Function Lib_SetModelName(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, NewName, OldName, CurName, RespJson : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl, Target, FirstImpl : ISch_Implementation;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    NewName := ExtractJsonValue(Params, 'new_model_name');
    OldName := ExtractJsonValue(Params, 'model_name');
    If (CompName = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name and new_model_name are required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Target := Nil;
    FirstImpl := Nil;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            If FirstImpl = Nil Then FirstImpl := Impl;
            CurName := '';
            Try CurName := Impl.ModelName; Except End;
            If OldName <> '' Then
            Begin
                If CurName = OldName Then Begin Target := Impl; Break; End;
            End
            Else
            Begin
                Try If Impl.IsCurrent Then Target := Impl; Except End;
                If Target <> Nil Then Break;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    If (Target = Nil) And (OldName = '') Then Target := FirstImpl;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MODEL_NOT_FOUND',
            'No matching model on ' + CompName + ' in ' + LibPath);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try Target.ModelName := NewName; Except End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Set model name');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"new_model_name":"' + EscapeJsonString(NewName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Lib_SetCurrentModel - choose which of a symbol's models is the current one.  }
{                                                                              }
{ Exactly one model of each type should be current: it is the footprint a     }
{ placed part uses. The flag is per implementation and Altium does not keep   }
{ it exclusive, so a symbol can end up with two current footprints (measured, }
{ lib_link_footprint replace=false before its fix) or none. This sets         }
{ IsCurrent on the first model of model_type whose ModelName matches and      }
{ clears it on every other model of that type, then reads all of them back.   }
{ Models of other types are not touched.                                      }
{                                                                              }
{ Params: component_name, model_name (required), model_type (default         }
{         PCBLIB), library_path (optional).                                   }
{..............................................................................}
Function Lib_SetCurrentModel(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, ModelName, ModelType, MT, MN, Details : String;
    SchLib, Fresh : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Idx, MatchIdx, Matches, CurCount, Changed : Integer;
    IsCur, Want, TargetCur : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    ModelName := ExtractJsonValue(Params, 'model_name');
    ModelType := UpperCase(Trim(ExtractJsonValue(Params, 'model_type')));
    If ModelType = '' Then ModelType := 'PCBLIB';
    If (CompName = '') Or (ModelName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'component_name and model_name are required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;
    Fresh := RefetchSchLib(LibPath);
    If Fresh <> Nil Then SchLib := Fresh;

    { Pass 1: the target's position among models of this type. }
    MatchIdx := -1;
    Matches := 0;
    Idx := 0;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            MN := '';
            Try MT := Impl.ModelType; Except End;
            Try MN := Impl.ModelName; Except End;
            If UpperCase(MT) = ModelType Then
            Begin
                If MN = ModelName Then
                Begin
                    Inc(Matches);
                    If MatchIdx < 0 Then MatchIdx := Idx;
                End;
                Inc(Idx);
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    If MatchIdx < 0 Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'MODEL_NOT_FOUND',
            'No ' + ModelType + ' model named "' + ModelName + '" on '
            + CompName + '. The models it has are listed in details.',
            '{"models":' + BuildImplementationsJson(Component) + '}');
        Exit;
    End;

    { Pass 2: exactly one current of this type. }
    Changed := 0;
    Idx := 0;
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        ImplIter := Component.SchIterator_Create;
        Try
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Impl := ImplIter.FirstSchObject;
            While Impl <> Nil Do
            Begin
                MT := '';
                Try MT := Impl.ModelType; Except End;
                If UpperCase(MT) = ModelType Then
                Begin
                    Want := (Idx = MatchIdx);
                    IsCur := False;
                    Try IsCur := Impl.IsCurrent; Except End;
                    If IsCur <> Want Then
                    Begin
                        SchBeginModify(Impl);
                        Try Impl.IsCurrent := Want; Except End;
                        SchEndModify(Impl);
                        Inc(Changed);
                    End;
                    Inc(Idx);
                End;
                Impl := ImplIter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(ImplIter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Set current model');
    End;

    { Pass 3: read back. }
    CurCount := 0;
    TargetCur := False;
    Idx := 0;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            Try MT := Impl.ModelType; Except End;
            If UpperCase(MT) = ModelType Then
            Begin
                IsCur := False;
                Try IsCur := Impl.IsCurrent; Except End;
                If IsCur Then Inc(CurCount);
                If Idx = MatchIdx Then TargetCur := IsCur;
                Inc(Idx);
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    MarkLibDirty(SchLib);

    Details := '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"model_name":"' + EscapeJsonString(ModelName) + '"'
        + ',"model_type":"' + EscapeJsonString(ModelType) + '"'
        + ',"changed":' + IntToStr(Changed)
        + ',"duplicates":' + IntToStr(Matches - 1)
        + ',"current_count":' + IntToStr(CurCount)
        + ',"models":' + BuildImplementationsJson(Component) + '}';
    If (Not TargetCur) Or (CurCount <> 1) Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'CURRENT_NOT_APPLIED',
            'After the write ' + IntToStr(CurCount) + ' ' + ModelType
            + ' models read as current and the requested one reads '
            + BoolToJsonStr(TargetCur) + '.', Details);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, Details);
End;

{ Lib_NormalizeImplementations - whole-SchLib sweep that rebuilds every          }
{ component's models: read each implementation (type, name, is_current), remove  }
{ all of them, then re-add ONE fresh, name-only implementation per unique        }
{ (type, name), preferring is_current. A fresh implementation carries no stale   }
{ SourceLibraryName and no duplicate, and binds by ModelName the way            }
{ Lib_LinkFootprint does (no AddDataFileLink, which wedges AD26). rename_map     }
{ (';'-separated 'old=new') renames a model_name during the re-add unless        }
{ dedupe_only=true. Component enumeration is decoupled from modification (names  }
{ collected first) so the sweep never mutates a live iterator.                   }
{ Params: library_path (optional), rename_map (optional), dedupe_only (bool).    }
Function Lib_NormalizeImplementations(Params : String; RequestId : String) : String;
Var
    LibPath, RenameMap, DedupeStr, RespJson : String;
    DedupeOnly, IsCur, Cur, RemovedOne : Boolean;
    SchLib : ISch_Lib;
    CompIter, ImplIter, ScanIter : ISch_Iterator;
    Component : ISch_Component;
    Impl, Found : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    CompNames, AllKeys, AllCurs, SeenKeys : TStringList;
    ModelName, ModelType, Key, NewName, Nm, OldSrc, EntNm, SourceLib, UseLibStr : String;
    C, J, K, Guard, LinkCount : Integer;
    CompsTouched, DupsRemoved, SourcesCleared, LinksRepaired, SourcesSet : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    RenameMap := ExtractJsonValue(Params, 'rename_map');
    DedupeStr := ExtractJsonValue(Params, 'dedupe_only');
    DedupeOnly := (DedupeStr = 'true') Or (DedupeStr = 'True') Or (DedupeStr = '1');
    SourceLib := ExtractJsonValue(Params, 'source_library');
    UseLibStr := ExtractJsonValue(Params, 'use_component_library');

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;

    { Collect component names first, so modification never touches a live       }
    { component iterator. }
    CompNames := TStringList.Create;
    Try
        CompIter := SchLib.SchLibIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Component := CompIter.FirstSchObject;
            While Component <> Nil Do
            Begin
                Nm := '';
                Try Nm := Component.LibReference; Except End;
                If Nm <> '' Then CompNames.Add(Nm);
                Component := CompIter.NextSchObject;
            End;
        Finally
            SchLib.SchIterator_Destroy(CompIter);
        End;

        CompsTouched := 0;
        DupsRemoved := 0;
        SourcesCleared := 0;
        LinksRepaired := 0;
        SourcesSet := 0;
        SchServer.ProcessControl.PreProcess(SchLib, '');

        For C := 0 To CompNames.Count - 1 Do
        Begin
            Component := LookupLibComponent(SchLib, CompNames[C]);
            If Component = Nil Then Continue;

            { Bug 2: the stale origin string is a COMPONENT-level property. Clear }
            { it (and the target-file tag) here. }
            OldSrc := '';
            Try OldSrc := Component.SourceLibraryName; Except End;
            Try Component.SourceLibraryName := ''; Except End;
            Try Component.TargetFileName := '*'; Except End;
            If OldSrc <> '' Then Inc(SourcesCleared);

            { Capture (type|name, was-current) up front, so after dedupe the      }
            { survivor can be re-flagged current if any of its copies was. }
            AllKeys := TStringList.Create;
            AllCurs := TStringList.Create;
            Try
                ImplIter := Component.SchIterator_Create;
                Try
                    ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                    Impl := ImplIter.FirstSchObject;
                    While Impl <> Nil Do
                    Begin
                        ModelType := ''; ModelName := ''; IsCur := False;
                        Try ModelType := Impl.ModelType; Except End;
                        Try ModelName := Impl.ModelName; Except End;
                        Try IsCur := Impl.IsCurrent; Except End;
                        AllKeys.Add(ModelType + '|' + ModelName);
                        If IsCur Then AllCurs.Add('1') Else AllCurs.Add('0');
                        Impl := ImplIter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(ImplIter);
                End;

                { Dedupe IN PLACE: remove later duplicates, keep the FIRST of      }
                { each (type|name). Keeping the object preserves its parameters    }
                { and its MapAsString pin-map; only surplus copies go. Re-scan     }
                { each pass so the live iterator is never mutated. }
                Guard := 2000;
                RemovedOne := True;
                While RemovedOne And (Guard > 0) Do
                Begin
                    RemovedOne := False;
                    Found := Nil;
                    SeenKeys := TStringList.Create;
                    ScanIter := Component.SchIterator_Create;
                    Try
                        ScanIter.AddFilter_ObjectSet(MkSet(eImplementation));
                        Impl := ScanIter.FirstSchObject;
                        While Impl <> Nil Do
                        Begin
                            ModelType := ''; ModelName := '';
                            Try ModelType := Impl.ModelType; Except End;
                            Try ModelName := Impl.ModelName; Except End;
                            Key := ModelType + '|' + ModelName;
                            K := -1;
                            For J := 0 To SeenKeys.Count - 1 Do
                                If SeenKeys[J] = Key Then K := J;
                            If K >= 0 Then Begin Found := Impl; Break; End;
                            SeenKeys.Add(Key);
                            Impl := ScanIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ScanIter);
                        SeenKeys.Free;
                    End;
                    If Found <> Nil Then
                    Begin
                        Try Component.RemoveSchImplementation(Found); Except End;
                        Inc(DupsRemoved);
                        RemovedOne := True;
                        Dec(Guard);
                    End;
                End;

                { Finalize survivors (one per key now): restore IsCurrent, apply   }
                { rename (ModelName + its datafile entity), and repair a footprint  }
                { left with no datafile link. Parameters and MapAsString are       }
                { untouched, so they carry forward intact. }
                ImplIter := Component.SchIterator_Create;
                Try
                    ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                    Impl := ImplIter.FirstSchObject;
                    While Impl <> Nil Do
                    Begin
                        ModelType := ''; ModelName := '';
                        Try ModelType := Impl.ModelType; Except End;
                        Try ModelName := Impl.ModelName; Except End;
                        Key := ModelType + '|' + ModelName;

                        Cur := False;
                        For J := 0 To AllKeys.Count - 1 Do
                            If (AllKeys[J] = Key) And (AllCurs[J] = '1') Then Cur := True;
                        Try Impl.IsCurrent := Cur; Except End;

                        If (Not DedupeOnly) And (RenameMap <> '') Then
                        Begin
                            NewName := LookupRename(RenameMap, ModelName, ModelName);
                            If NewName <> ModelName Then
                            Begin
                                Try Impl.ModelName := NewName; Except End;
                                LinkCount := 0;
                                Try LinkCount := Impl.DatafileLinkCount; Except End;
                                For J := 0 To LinkCount - 1 Do
                                Begin
                                    Link := Nil;
                                    Try Link := Impl.DatafileLink[J]; Except End;
                                    If Link <> Nil Then
                                    Begin
                                        EntNm := '';
                                        Try EntNm := Link.EntityName; Except End;
                                        If EntNm = ModelName Then
                                            Try Link.EntityName := NewName; Except End;
                                    End;
                                End;
                                ModelName := NewName;
                            End;
                        End;

                        If UpperCase(ModelType) = 'PCBLIB' Then
                        Begin
                            LinkCount := 0;
                            Try LinkCount := Impl.DatafileLinkCount; Except End;
                            If LinkCount = 0 Then
                            Begin
                                Try Impl.AddDataFileLink(ModelName, '', 'PCBLib'); Except End;
                                LinkCount := 0;
                                Try LinkCount := Impl.DatafileLinkCount; Except End;
                                Inc(LinksRepaired);
                            End;
                            { Populate the source-library Location so the         }
                            { footprint embeds on compile (empty resolves by name }
                            { but never embeds). Only when source_library is set;  }
                            { existing Locations are otherwise left untouched.     }
                            If SourceLib <> '' Then
                            Begin
                                SchBeginModify(Impl);
                                For J := 0 To LinkCount - 1 Do
                                    Try Impl.DatafileLink[J].Location := SourceLib; Except End;
                                If UseLibStr = 'true' Then Try Impl.UseComponentLibrary := True; Except End;
                                If UseLibStr = 'false' Then Try Impl.UseComponentLibrary := False; Except End;
                                SchEndModify(Impl);
                                Inc(SourcesSet);
                            End;
                        End;

                        Impl := ImplIter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(ImplIter);
                End;

                Inc(CompsTouched);
            Finally
                AllKeys.Free;
                AllCurs.Free;
            End;
        End;

        SchServer.ProcessControl.PostProcess(SchLib, 'Normalize implementations');
        MarkLibDirty(SchLib);
    Finally
        CompNames.Free;
    End;

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"components_touched":' + IntToStr(CompsTouched)
        + ',"duplicates_removed":' + IntToStr(DupsRemoved)
        + ',"sources_cleared":' + IntToStr(SourcesCleared)
        + ',"links_repaired":' + IntToStr(LinksRepaired)
        + ',"sources_set":' + IntToStr(SourcesSet) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetModelSource - write the datafile-link Location (source-library ref) on }
{ a component's footprint models, so the footprint is EMBEDDABLE in a            }
{ self-contained compiled library. An empty Location resolves in the editor by  }
{ name but cannot embed on compile. Sets Location IN PLACE (never AddDataFileLink}
{ with a path, which wedges AD26). Targets PCBLIB models matching model_name if  }
{ given, else all. Optional use_component_library ('true'/'false') sets the      }
{ embed-vs-search flag. Verify the exact Location string + flag against a        }
{ known-good reference model before bulk use.                                    }
{ Params: component_name, source_library (required), model_name (optional),      }
{         use_component_library (optional), library_path (optional).             }
Function Lib_SetModelSource(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, SourceLib, ModelName, UseLibStr, RespJson, CurName, MT : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Updated, J, LinkCount : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    SourceLib := ExtractJsonValue(Params, 'source_library');
    ModelName := ExtractJsonValue(Params, 'model_name');
    UseLibStr := ExtractJsonValue(Params, 'use_component_library');
    If CompName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name is required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := LookupLibComponent(SchLib, CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Updated := 0;
    SchServer.ProcessControl.PreProcess(SchLib, '');
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            CurName := '';
            Try MT := Impl.ModelType; Except End;
            Try CurName := Impl.ModelName; Except End;
            If (UpperCase(MT) = 'PCBLIB') And ((ModelName = '') Or (CurName = ModelName)) Then
            Begin
                SchBeginModify(Impl);
                LinkCount := 0;
                Try LinkCount := Impl.DatafileLinkCount; Except End;
                If LinkCount = 0 Then
                Begin
                    Try Impl.AddDataFileLink(CurName, '', 'PCBLib'); Except End;
                    LinkCount := 0;
                    Try LinkCount := Impl.DatafileLinkCount; Except End;
                End;
                For J := 0 To LinkCount - 1 Do
                    Try Impl.DatafileLink[J].Location := SourceLib; Except End;
                If UseLibStr = 'true' Then Try Impl.UseComponentLibrary := True; Except End;
                If UseLibStr = 'false' Then Try Impl.UseComponentLibrary := False; Except End;
                SchEndModify(Impl);
                Inc(Updated);
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Set model source');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"component":"' + EscapeJsonString(CompName) + '"'
        + ',"source_library":"' + EscapeJsonString(SourceLib) + '"'
        + ',"models_updated":' + IntToStr(Updated) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ProbeFootprint - READ-ONLY dump of a PcbLib footprint's name-bearing      }
{ fields, to locate where an old name persists after a rename. On IPCB_LibComp   }
{ Name and Pattern are the SAME property (Name := new writes both); its only     }
{ metadata is Name, Description, Height. So a stale name lives in a child        }
{ primitive: this dumps every primitive's ObjectId and, for text objects, its    }
{ .Text, so the offending record is visible. Nothing is written.                 }
{ Params: footprint_name (required), library_path (optional).                    }
Function Lib_ProbeFootprint(Params : String; RequestId : String) : String;
Var
    SeenPrims : TStringList;
    PrimAddr : String;
    LibPath, FocusedPath, FpWanted, FpName, FpDescr, PrimsJson, RespJson, TxtVal : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Txt : IPCB_Text;
    HeightMils, PrimCount : Integer;
    PFirst : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then Begin Target := Footprint; Break; End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    FpName := '';
    FpDescr := '';
    HeightMils := 0;
    Try FpName := Target.Name; Except End;
    Try FpDescr := Target.Description; Except End;
    Try HeightMils := CoordToMils(Target.Height); Except End;

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPrims := TStringList.Create;
    PrimsJson := '[';
    PFirst := True;
    PrimCount := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            PrimAddr := '';
            Try PrimAddr := IntToStr(Prim.I_ObjectAddress); Except End;
            If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
            Begin
                Prim := GrpIter.NextPCBObject;
                Continue;
            End;
            If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
            Inc(PrimCount);
            TxtVal := '';
            If Prim.ObjectId = eTextObject Then
            Begin
                Txt := Prim;
                Try TxtVal := Txt.Text; Except End;
            End;
            If TxtVal <> '' Then
            Begin
                If Not PFirst Then PrimsJson := PrimsJson + ',';
                PFirst := False;
                PrimsJson := PrimsJson + '{"object_id":' + IntToStr(Prim.ObjectId)
                    + ',"text":"' + EscapeJsonString(TxtVal) + '"}';
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
        Try SeenPrims.Free; Except End;
    End;
    PrimsJson := PrimsJson + ']';

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"footprint":"' + EscapeJsonString(FpName) + '"'
        + ',"description":"' + EscapeJsonString(FpDescr) + '"'
        + ',"height_mils":' + IntToStr(HeightMils)
        + ',"primitive_count":' + IntToStr(PrimCount)
        + ',"texts":' + PrimsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_MoveComponents - bulk copy (+ optional delete) of components between two  }
{ SchLibs, the bulk analog of Lib_CopyComponent. Replicate carries the whole     }
{ symbol (pins, parameters, models). Focuses SOURCE once (Replicate needs source }
{ context) and addresses DEST by path via GetSchDocumentByPath, so there is no   }
{ per-component focus thrashing. Names arrive as a '~~'-separated explicit list   }
{ (the Python tool resolves any regex first). A name already in dest is skipped  }
{ unless overwrite. delete_from_source defaults true. Names collected up front,   }
{ so removing from source never mutates a live iterator.                         }
{ Params: source_library, dest_library, names ('~~'-sep, required), overwrite,   }
{         delete_from_source.                                                    }
Function Lib_MoveComponents(Params : String; RequestId : String) : String;
Var
    SourcePath, DestPath, NamesStr, Remaining, Name, OverwriteStr, DeleteStr, RespJson : String;
    Overwrite, DeleteFromSource : Boolean;
    SourceLib, DestLib : ISch_Lib;
    SourceComp, NewComp, Existing : ISch_Component;
    ServerDoc : IServerDocument;
    Moved, Skipped, Failed : Integer;
Begin
    SourcePath := ExtractJsonValue(Params, 'source_library');
    DestPath := ExtractJsonValue(Params, 'dest_library');
    NamesStr := ExtractJsonValue(Params, 'names');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    DeleteStr := ExtractJsonValue(Params, 'delete_from_source');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    DeleteFromSource := (DeleteStr = '') Or (DeleteStr = 'true') Or (DeleteStr = 'True') Or (DeleteStr = '1');

    If (SourcePath = '') Or (DestPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_library and dest_library are required');
        Exit;
    End;
    If NamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'names is required');
        Exit;
    End;
    If UpperCase(SourcePath) = UpperCase(DestPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_LIBRARY', 'source_library and dest_library are the same');
        Exit;
    End;

    { Open dest (load it), then focus source so Replicate has its context. }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', DestPath);
    RunProcess('WorkspaceManager:OpenObject');

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', SourcePath);
    RunProcess('WorkspaceManager:OpenObject');

    SourceLib := SchServer.GetCurrentSchDocument;
    If (SourceLib = Nil) Or (SourceLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'Failed to focus source library at ' + SourcePath);
        Exit;
    End;
    DestLib := SchServer.GetSchDocumentByPath(DestPath);
    If (DestLib = Nil) Or (DestLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'Failed to open destination library at ' + DestPath);
        Exit;
    End;

    Moved := 0;
    Skipped := 0;
    Failed := 0;
    Remaining := NamesStr;
    While True Do
    Begin
        Name := NextBatchOp(Remaining);
        If Name = '' Then Break;

        SourceComp := LookupLibComponent(SourceLib, Name);
        If SourceComp = Nil Then Begin Inc(Failed); Continue; End;

        { In memory only, or a miss reopens DestLib under the loop. See }
        { FindLibComponentInMemory.                                     }
        Existing := FindLibComponentInMemory(DestLib, Name);
        If (Existing <> Nil) And (Not Overwrite) Then Begin Inc(Skipped); Continue; End;

        NewComp := SourceComp.Replicate;
        If NewComp = Nil Then Begin Inc(Failed); Continue; End;
        NewComp.LibReference := Name;

        SchServer.ProcessControl.PreProcess(DestLib, '');
        If Existing <> Nil Then
        Begin
            ForgetLibComponentIf(DestLib, Existing, Name);
            Try DestLib.RemoveSchComponent(Existing); Except End;
        End;
        DestLib.AddSchComponent(NewComp);
        { REGISTER IT IN THE DESTINATION, or the move does not reach disk.
          Same defect as the copy path: the component is added to a library
          that is never told, so it reads back correctly and no save route
          writes it. Broadcast as a new component, the pattern from Altium's
          createcomp_in_lib.pas. }
        Try
            SchServer.RobotManager.SendMessage(
                Nil, Nil, SCHM_PrimitiveRegistration,
                NewComp.I_ObjectAddress);
        Except End;
        SchServer.ProcessControl.PostProcess(DestLib, 'Move component');

        If DeleteFromSource Then
        Begin
            SchServer.ProcessControl.PreProcess(SourceLib, '');
            ForgetLibComponentIf(SourceLib, SourceComp, Name);
            Try SourceLib.RemoveSchComponent(SourceComp); Except End;
            SchServer.ProcessControl.PostProcess(SourceLib, 'Move component');
        End;

        Inc(Moved);
    End;

    { Dirty both docs BY PATH (MarkLibDirty only dirties the focused doc). }
    Try
        ServerDoc := Client.GetDocumentByPath(DestPath);
        If ServerDoc <> Nil Then ServerDoc.SetModified(True);
    Except End;
    If DeleteFromSource Then
        Try
            ServerDoc := Client.GetDocumentByPath(SourcePath);
            If ServerDoc <> Nil Then ServerDoc.SetModified(True);
        Except End;

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourcePath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestPath) + '"'
        + ',"moved":' + IntToStr(Moved)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Can PCBServer.LoadPCBLibraryByPath be handed Path without raising on an     }
{ ordinary bad input? A .PcbLib that is not ABSOLUTE-and-missing. A missing  }
{ file or another document kind (a .SchLib passed by mistake) raised, as a   }
{ modal, and the handler then answered its NO_PCBLIB 'Failed to open ...'    }
{ reply; False sends it to that reply directly. GetPCBLibraryByPath (already }
{ open) is tried first by every caller, so an open unsaved library never     }
{ reaches this.                                                              }
Function PcbLibLoadable(Path : String) : Boolean;
Var
    P : String;
Begin
    P := Trim(Path);
    Result := False;
    If UpperCase(ExtractFileExt(P)) <> '.PCBLIB' Then Exit;
    If LooksAbsolutePath(P) And (Not FileExists(P)) Then Exit;
    Result := True;
End;

{ Lib_MoveFootprints - bulk copy (+ optional delete) of footprints between two  }
{ PcbLibs, the PcbLib analog of Lib_MoveComponents. Uses the canonical combine   }
{ pattern: DestLib.CreateNewComponent + Footprint.CopyTo(NewFP, eFullCopy) +     }
{ RegisterComponent, then RemoveComponent + DeRegisterComponent on the source.   }
{ Both libraries are resolved by path (GetPCBLibraryByPath, load if needed), so  }
{ neither needs to be focused. A name already in dest is skipped unless          }
{ overwrite. delete_from_source defaults true. Names arrive '~~'-separated.      }
{ Params: source_library, dest_library, names (required), overwrite,            }
{         delete_from_source.                                                    }
Function Lib_MoveFootprints(Params : String; RequestId : String) : String;
Var
    SourcePath, DestPath, NamesStr, Remaining, Name, OverwriteStr, DeleteStr, RespJson, FpName : String;
    Overwrite, DeleteFromSource : Boolean;
    SourceLib, DestLib : IPCB_Library;
    Footprint, NewFP, Existing, Fp : IPCB_LibComponent;
    Moved, Skipped, Failed, J : Integer;
Begin
    SourcePath := ExtractJsonValue(Params, 'source_library');
    DestPath := ExtractJsonValue(Params, 'dest_library');
    NamesStr := ExtractJsonValue(Params, 'names');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    DeleteStr := ExtractJsonValue(Params, 'delete_from_source');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    DeleteFromSource := (DeleteStr = '') Or (DeleteStr = 'true') Or (DeleteStr = 'True') Or (DeleteStr = '1');

    If (SourcePath = '') Or (DestPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_library and dest_library are required');
        Exit;
    End;
    If NamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'names is required');
        Exit;
    End;
    If UpperCase(SourcePath) = UpperCase(DestPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_LIBRARY', 'source_library and dest_library are the same');
        Exit;
    End;

    SourceLib := Nil;
    { NEEDS PROBE: whether GetPCBLibraryByPath raises for a library that is not }
    { loaded (the normal case for a destination). No guard that cannot lose an  }
    { open library exists, so a catch is counted.                               }
    Try SourceLib := PCBServer.GetPCBLibraryByPath(SourcePath);
    Except RecordCaughtException('Lib_MoveFootprints:GetPCBLibraryByPath'); End;
    If (SourceLib = Nil) And PcbLibLoadable(SourcePath) Then
        Try SourceLib := PCBServer.LoadPCBLibraryByPath(SourcePath); Except End;
    If SourceLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open source PCB library at ' + SourcePath);
        Exit;
    End;
    DestLib := Nil;
    Try DestLib := PCBServer.GetPCBLibraryByPath(DestPath);
    Except RecordCaughtException('Lib_MoveFootprints:GetPCBLibraryByPath'); End;
    If (DestLib = Nil) And PcbLibLoadable(DestPath) Then
        Try DestLib := PCBServer.LoadPCBLibraryByPath(DestPath); Except End;
    If DestLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open destination PCB library at ' + DestPath);
        Exit;
    End;

    Moved := 0;
    Skipped := 0;
    Failed := 0;
    PCBServer.PreProcess;
    Remaining := NamesStr;
    While True Do
    Begin
        Name := NextBatchOp(Remaining);
        If Name = '' Then Break;

        { Find the source footprint by name (index scan; not a live iterator). }
        Footprint := Nil;
        For J := 0 To SourceLib.ComponentCount - 1 Do
        Begin
            Fp := SourceLib.GetComponent(J);
            FpName := '';
            If Fp <> Nil Then Try FpName := Fp.Name; Except End;
            If FpName = Name Then Begin Footprint := Fp; Break; End;
        End;
        If Footprint = Nil Then Begin Inc(Failed); Continue; End;

        Existing := DestLib.GetComponentByName(Name);
        If (Existing <> Nil) And (Not Overwrite) Then Begin Inc(Skipped); Continue; End;
        If Existing <> Nil Then
        Begin
            Try DestLib.RemoveComponent(Existing); Except End;
            Try DestLib.DeRegisterComponent(Existing); Except End;
        End;

        NewFP := DestLib.CreateNewComponent;
        If NewFP = Nil Then Begin Inc(Failed); Continue; End;
        Try Footprint.CopyTo(NewFP, eFullCopy); Except End;
        Try NewFP.Name := Name; Except End;
        DestLib.RegisterComponent(NewFP);

        If DeleteFromSource Then
        Begin
            Try SourceLib.RemoveComponent(Footprint); Except End;
            Try SourceLib.DeRegisterComponent(Footprint); Except End;
        End;

        Inc(Moved);
    End;
    PCBServer.PostProcess;

    Try DestLib.Board.ViewManager_FullUpdate; Except End;
    Try DestLib.RefreshView; Except End;
    MarkDocDirtyByPath(DestLib.Board.FileName);
    If DeleteFromSource Then MarkDocDirtyByPath(SourceLib.Board.FileName);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourcePath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestPath) + '"'
        + ',"moved":' + IntToStr(Moved)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_CopyFootprint - copy ONE footprint (all pads/primitives) by name into a   }
{ PcbLib, optionally renaming, the footprint analog of Lib_CopyComponent. No     }
{ delete. Same-library copy requires a different new_name. Libraries resolve by  }
{ path (default source = focused, dest = source). A name already in dest errors  }
{ unless overwrite.                                                              }
{ Params: source_name (required), new_name (default source_name),               }
{         source_library (optional), dest_library (optional), overwrite.         }
Function Lib_CopyFootprint(Params : String; RequestId : String) : String;
Var
    SourceLibPath, DestLibPath, SourceName, NewName, OverwriteStr, RespJson, FpName : String;
    Overwrite, SameLib : Boolean;
    SourceLib, DestLib : IPCB_Library;
    Footprint, NewFP, Existing, Fp : IPCB_LibComponent;
    J : Integer;
Begin
    SourceLibPath := ExtractJsonValue(Params, 'source_library');
    DestLibPath := ExtractJsonValue(Params, 'dest_library');
    SourceName := ExtractJsonValue(Params, 'source_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    If SourceName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_name is required');
        Exit;
    End;
    If NewName = '' Then NewName := SourceName;

    { Source library: by path, else the focused PcbLib. }
    SourceLib := Nil;
    If SourceLibPath <> '' Then
    Begin
        Try SourceLib := PCBServer.GetPCBLibraryByPath(SourceLibPath);
        Except RecordCaughtException('Lib_CopyFootprint:GetPCBLibraryByPath'); End;
        If (SourceLib = Nil) And PcbLibLoadable(SourceLibPath) Then
            Try SourceLib := PCBServer.LoadPCBLibraryByPath(SourceLibPath); Except End;
    End
    { The focused PcbLib, asked only when one may be current            }
    { (PcbLibMayBeCurrent, Main.pas): a focused SchLib in a session with }
    { no PcbDoc / PcbLib open named PCBServer here, the uncatchable      }
    { modal. The NO_PCBLIB reply below is the false branch, unchanged.   }
    Else If PcbLibMayBeCurrent(0) Then
        SourceLib := PCBServer.GetCurrentPCBLibrary;
    If SourceLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No source PCB library (source_library not supplied and none focused)');
        Exit;
    End;
    If SourceLibPath = '' Then Try SourceLibPath := SourceLib.Board.FileName; Except End;

    { Destination library: default = source. }
    SameLib := (DestLibPath = '') Or (UpperCase(DestLibPath) = UpperCase(SourceLibPath));
    If SameLib Then
    Begin
        DestLib := SourceLib;
        DestLibPath := SourceLibPath;
        If NewName = SourceName Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SAME_NAME', 'Copying within the same library requires a different new_name');
            Exit;
        End;
    End
    Else
    Begin
        DestLib := Nil;
        Try DestLib := PCBServer.GetPCBLibraryByPath(DestLibPath);
        Except RecordCaughtException('Lib_CopyFootprint:GetPCBLibraryByPath'); End;
        If (DestLib = Nil) And PcbLibLoadable(DestLibPath) Then
            Try DestLib := PCBServer.LoadPCBLibraryByPath(DestLibPath); Except End;
        If DestLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open destination PCB library at ' + DestLibPath);
            Exit;
        End;
    End;

    Footprint := Nil;
    For J := 0 To SourceLib.ComponentCount - 1 Do
    Begin
        Fp := SourceLib.GetComponent(J);
        FpName := '';
        If Fp <> Nil Then Try FpName := Fp.Name; Except End;
        If FpName = SourceName Then Begin Footprint := Fp; Break; End;
    End;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + SourceLibPath + ': ' + SourceName);
        Exit;
    End;

    Existing := DestLib.GetComponentByName(NewName);
    If (Existing <> Nil) And (Not Overwrite) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS', 'A footprint named "' + NewName + '" already exists in ' + DestLibPath + ' (pass overwrite=true to replace)');
        Exit;
    End;

    PCBServer.PreProcess;
    If Existing <> Nil Then
    Begin
        Try DestLib.RemoveComponent(Existing); Except End;
        Try DestLib.DeRegisterComponent(Existing); Except End;
    End;
    NewFP := DestLib.CreateNewComponent;
    If NewFP = Nil Then
    Begin
        PCBServer.PostProcess;
        Result := BuildErrorResponse(RequestId, 'COPY_FAILED', 'CreateNewComponent returned Nil');
        Exit;
    End;
    Try Footprint.CopyTo(NewFP, eFullCopy); Except End;
    Try NewFP.Name := NewName; Except End;
    DestLib.RegisterComponent(NewFP);
    PCBServer.PostProcess;

    Try DestLib.Board.ViewManager_FullUpdate; Except End;
    Try DestLib.RefreshView; Except End;
    MarkDocDirtyByPath(DestLib.Board.FileName);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourceLibPath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestLibPath) + '"'
        + ',"source":"' + EscapeJsonString(SourceName) + '"'
        + ',"new_name":"' + EscapeJsonString(NewName) + '"'
        + ',"same_library":' + BoolToJsonStr(SameLib) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetPadGeometry - full-precision pad dump for the datasheet land-pattern }
{ audit. Every dimension is emitted in MILLIMETRES as a float (datasheets     }
{ dimension land patterns metrically; the integer-mil dump used by the policy }
{ auditor rounds a 0.65 mm pitch by 10 um, which a tolerance-based comparison }
{ against the datasheet cannot afford). Coordinates are relative to the       }
{ footprint's own origin. Per pad: centre, size, shape (with roundrect        }
{ corner percentage), rotation, hole (size / width / type / plated), layer,   }
{ and the paste / solder mask expansions with whether each is a manual        }
{ override (Cache.*Valid = eCacheManual) or rule-driven.                      }
{ Params: footprint_name (required), library_path (optional, focused).       }
Function Lib_GetPadGeometry(Params : String; RequestId : String) : String;
Var
    SeenPads : TStringList;
    PadAddr : String;
    LibPath, FocusedPath, FpWanted, FpName, FpDescr : String;
    ShapeStr, HoleStr, LayerStr, PadsJson, RespJson, ExpSrc : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    XOrg, YOrg : Integer;
    Count, CornerPct : Integer;
    ExpVal : Double;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then Begin Target := Footprint; Break; End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    FpName := '';
    FpDescr := '';
    Try FpName := Target.Name; Except End;
    Try FpDescr := Target.Description; Except End;
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPads := TStringList.Create;
    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            PadAddr := '';
            Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
            If (PadAddr <> '') And (SeenPads.IndexOf(PadAddr) >= 0) Then
            Begin
                Pad := GrpIter.NextPCBObject;
                Continue;
            End;
            If PadAddr <> '' Then SeenPads.Add(PadAddr);
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
                Else ShapeStr := 'round';
            Except End;

            CornerPct := 0;
            If ShapeStr = 'roundrectangle' Then
            Begin
                Try CornerPct := Pad.CRPercentage[eTopLayer]; Except End;
            End;

            HoleStr := 'round';
            Try
                If Pad.HoleType = eSquareHole Then HoleStr := 'square'
                Else If Pad.HoleType = eSlotHole Then HoleStr := 'slot'
                Else HoleStr := 'round';
            Except End;

            LayerStr := 'top';
            Try
                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                Else LayerStr := 'top';
            Except End;

            If Count > 0 Then PadsJson := PadsJson + ',';
            PadsJson := PadsJson +
                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                ',"x_mm":' + FloatToJsonStr(CoordToMM(Pad.X - XOrg)) +
                ',"y_mm":' + FloatToJsonStr(CoordToMM(Pad.Y - YOrg)) +
                ',"w_mm":' + FloatToJsonStr(CoordToMM(Pad.TopXSize)) +
                ',"h_mm":' + FloatToJsonStr(CoordToMM(Pad.TopYSize)) +
                ',"shape":"' + ShapeStr + '"' +
                ',"corner_pct":' + IntToStr(CornerPct) +
                ',"rotation":' + FloatToJsonStr(Pad.Rotation) +
                ',"hole_mm":' + FloatToJsonStr(CoordToMM(Pad.HoleSize));

            { Slot holes carry a second dimension. }
            ExpVal := 0;
            If HoleStr = 'slot' Then
            Begin
                Try ExpVal := CoordToMM(Pad.HoleWidth); Except End;
            End;
            PadsJson := PadsJson +
                ',"hole_w_mm":' + FloatToJsonStr(ExpVal) +
                ',"hole_type":"' + HoleStr + '"';

            Try
                PadsJson := PadsJson + ',"plated":' + BoolToJsonStr(Pad.Plated);
            Except
                PadsJson := PadsJson + ',"plated":true';
            End;
            PadsJson := PadsJson + ',"layer":"' + LayerStr + '"';

            { Paste expansion: value + manual-vs-rule source. }
            ExpVal := 0;
            ExpSrc := 'rule';
            Try
                If Pad.Cache.PasteMaskExpansionValid = eCacheManual Then
                Begin
                    ExpVal := CoordToMM(Pad.Cache.PasteMaskExpansion);
                    ExpSrc := 'manual';
                End;
            Except End;
            PadsJson := PadsJson +
                ',"paste_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"paste_expansion_source":"' + ExpSrc + '"';

            { Solder mask expansion: same pattern. }
            ExpVal := 0;
            ExpSrc := 'rule';
            Try
                If Pad.Cache.SolderMaskExpansionValid = eCacheManual Then
                Begin
                    ExpVal := CoordToMM(Pad.Cache.SolderMaskExpansion);
                    ExpSrc := 'manual';
                End;
            Except End;
            PadsJson := PadsJson +
                ',"mask_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"mask_expansion_source":"' + ExpSrc + '"}';

            Inc(Count);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
        Try SeenPads.Free; Except End;
    End;
    PadsJson := PadsJson + ']';

    RespJson :=
        '{"name":"' + EscapeJsonString(FpName) + '"' +
        ',"description":"' + EscapeJsonString(FpDescr) + '"' +
        ',"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"pad_count":' + IntToStr(Count) +
        ',"pads":' + PadsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ClearSourceLibrary - unpin every symbol in a SchLib from its source     }
{ provenance, the library-side sibling of the placed-component               }
{ clear_sch_source_library. When symbols were copied in from another library  }
{ (a vendor pack, a stock library) each carries SourceLibraryName /           }
{ TargetFileName pointing at the ORIGIN, and a stale DesignItemId; placing    }
{ them then re-links against a library that no longer exists. Per matching    }
{ component: clear SourceLibraryName, reset TargetFileName to '*', and sync   }
{ DesignItemId to the LibReference (each independently switchable). The       }
{ minimal fast path of what lib_normalize_implementations does as part of     }
{ its full model sweep. Deferred save via MarkLibDirty.                        }
{ Params: library_path (optional, focused default),                           }
{         component_names (optional comma list, empty = all),                 }
{         clear_target_file_name=true, sync_design_item_id=true.               }
Function Lib_ClearSourceLibrary(Params : String; RequestId : String) : String;
Var
    LibPath, NamesCsv, FlagStr, Nm, LibRef : String;
    SchLib : ISch_Lib;
    CompIter : ISch_Iterator;
    Component : ISch_Component;
    AllNames, WantNames : TStringList;
    ClearTarget, SyncId, WantAll : Boolean;
    C, Total, ClearedSrc, ClearedTgt, Synced : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    NamesCsv := ExtractJsonValue(Params, 'component_names');
    FlagStr := ExtractJsonValue(Params, 'clear_target_file_name');
    ClearTarget := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));
    FlagStr := ExtractJsonValue(Params, 'sync_design_item_id');
    SyncId := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;

    AllNames := TStringList.Create;
    WantNames := TStringList.Create;
    Try
        WantNames.CommaText := NamesCsv;
        WantAll := WantNames.Count = 0;

        { Two-phase walk (the normalize pattern): collect names via the   }
        { live iterator first, mutate by LibRef lookup after, so the      }
        { iterator never sees a component being modified under it.        }
        CompIter := SchLib.SchLibIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Component := CompIter.FirstSchObject;
            While Component <> Nil Do
            Begin
                Nm := '';
                Try Nm := Component.LibReference; Except End;
                If Nm <> '' Then
                Begin
                    If WantAll Or (WantNames.IndexOf(Nm) >= 0) Then
                        AllNames.Add(Nm);
                End;
                Component := CompIter.NextSchObject;
            End;
        Finally
            SchLib.SchIterator_Destroy(CompIter);
        End;

        Total := 0;
        ClearedSrc := 0;
        ClearedTgt := 0;
        Synced := 0;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        Try
            For C := 0 To AllNames.Count - 1 Do
            Begin
                Component := LookupLibComponent(SchLib, AllNames[C]);
                If Component = Nil Then Continue;
                Inc(Total);

                Try
                    If Component.SourceLibraryName <> '' Then
                    Begin
                        Component.SourceLibraryName := '';
                        Inc(ClearedSrc);
                    End;
                Except End;

                If ClearTarget Then
                Begin
                    Try
                        If Component.TargetFileName <> '*' Then
                        Begin
                            Component.TargetFileName := '*';
                            Inc(ClearedTgt);
                        End;
                    Except End;
                End;

                If SyncId Then
                Begin
                    Try
                        LibRef := Component.LibReference;
                        If (LibRef <> '') And (Component.DesignItemId <> LibRef) Then
                        Begin
                            Component.DesignItemId := LibRef;
                            Inc(Synced);
                        End;
                    Except End;
                End;
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
        End;

        SchLib.GraphicallyInvalidate;
        MarkLibDirty(SchLib);
    Finally
        AllNames.Free;
        WantNames.Free;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"cleared_source_library":' + IntToStr(ClearedSrc) +
        ',"cleared_target_file_name":' + IntToStr(ClearedTgt) +
        ',"synced_design_item_id":' + IntToStr(Synced) + '}');
End;

{..............................................................................}
{ Command Handler - must be at end                                             }
{..............................................................................}

{..............................................................................}
{ Lib_SetMechLayers - Name, enable and kind the mechanical layers of ONE      }
{ named library.                                                              }
{                                                                              }
{ Params: library_path, layers                                                }
{   layers is '~~' separated operations, ';' separated fields:                }
{     layer=Mechanical13;name=Courtyard;enabled=true;kind=Courtyard Top       }
{                                                                              }
{ WHY THIS EXISTS SEPARATELY FROM THE pcb_* LAYER TOOLS. Those read whatever  }
{ board is current. Pointing them at a particular library depends on the      }
{ focus actually moving, and when it does not they silently operate on the    }
{ previously focused library instead: a run across twenty one libraries came  }
{ back with twenty one identical answers because every call had re-read the   }
{ same file while reporting success.                                          }
{                                                                              }
{ So this takes the library by PATH, and refuses unless the document that     }
{ ended up focused is the one that was asked for. An operation on the wrong   }
{ library is worse than no operation, because it looks like it worked.        }
{..............................................................................}

{ Which mechanical layer in THIS request is being given a kind.        }
{                                                                       }
{ Scans the caller's own ops rather than the stack, because a paired    }
{ kind is joined to the partner the caller is setting now. Reading the  }
{ stack instead would find whatever already held the kind, which is a   }
{ different layer and a different intent.                                }

Function FindLayerForKindInOps(OpsStr : String; WantKind : Integer) : Integer;
Var
    Remaining, Op, LayerName, KindStr : String;
    Num : Integer;
Begin
    Result := -1;
    Remaining := OpsStr;
    While Trim(Remaining) <> '' Do
    Begin
        Op := NextBatchOp(Remaining);
        If Trim(Op) = '' Then Continue;
        KindStr := Trim(GetBatchField(Op, 'kind'));
        If KindStr = '' Then Continue;
        If MechKindFromString(KindStr) = WantKind Then
        Begin
            LayerName := Trim(GetBatchField(Op, 'layer'));
            Num := ParseMechLayerNumber(LayerName);
            If Num > 0 Then
            Begin
                Result := Num;
                Exit;
            End;
        End;
    End;
End;

{ Whether two mechanical layers are joined, in either order.                  }
{                                                                              }
{ PairDefined is order sensitive, so asking one way round reports no pair for  }
{ one that exists the other way round.                                         }

Function PairIsDefined(MechPairs : IPCB_MechanicalLayerPairs;
    L1 : TLayer; L2 : TLayer) : Boolean;
Begin
    Result := False;
    If MechPairs = Nil Then Exit;
    Try Result := MechPairs.PairDefined(L1, L2); Except Result := False; End;
    If Result Then Exit;
    Try Result := MechPairs.PairDefined(L2, L1); Except Result := False; End;
End;

{ Drop every pair joining two layers, however many there are.                 }
{                                                                              }
{ Returns how many removals it took, which is also how many duplicates were    }
{ present. Bounded, because a build where RemovePair does nothing would        }
{ otherwise spin.                                                              }

Function DrainMechPair(MechPairs : IPCB_MechanicalLayerPairs;
    L1 : TLayer; L2 : TLayer) : Integer;
Var
    Guard : Integer;
Begin
    Result := 0;
    Guard := 0;
    While PairIsDefined(MechPairs, L1, L2) And (Guard < 64) Do
    Begin
        Try MechPairs.RemovePair(L1, L2); Except End;
        Try MechPairs.RemovePair(L2, L1); Except End;
        Guard := Guard + 1;
        Result := Guard;
    End;
End;

{ Join two mechanical layers and give the PAIR its kind.                      }
{                                                                              }
{ A build KNOWN to predate mechanical layer kinds (AD17/AD18: Utils'          }
{ ReadMechKind records LayerObject.Kind faulting there). Board.MasterLayerStack, }
{ LayerObject.Kind and the layer-pair kind are the members it lacks, and each   }
{ raised as a modal. An unknown version (-1) is never 'known to predate', so   }
{ such a build behaves exactly as before (AltiumMajorVersion's gate rule).     }
Function MechKindsUnsupported(Dummy : Integer) : Boolean;
Var
    V : Integer;
Begin
    V := AltiumMajorVersion(0);
    Result := (V >= 0) And (V < 19);
End;

{ AddPair APPENDS WITHOUT CHECKING, so calling it for a pair that already      }
{ exists leaves a duplicate behind rather than returning the existing one.     }
{ Repeated sweeps over one library left fourteen pairs where four were         }
{ wanted, and the Layer Stack Manager shows every one. Existing pairs are      }
{ therefore drained first, which also yields the index: AddPair is the only    }
{ call that reports one, since PairDefined answers a boolean and LayerPair(i)  }
{ is noted as broken in the reference.                                         }
{                                                                              }
{ The TOP layer has to be the first argument.                                  }

Function JoinAndKindPair(MechPairs : IPCB_MechanicalLayerPairs;
    TopL : TLayer; BotL : TLayer; PairKind : Integer) : Boolean;
Var
    PairIdx, KindBack : Integer;
Begin
    Result := False;
    If MechPairs = Nil Then Exit;
    If PairKind < 0 Then Exit;

    DrainMechPair(MechPairs, TopL, BotL);
    { Still joined means the removals are not taking, and adding now would  }
    { only grow the duplicate count.                                        }
    If PairIsDefined(MechPairs, TopL, BotL) Then Exit;

    PairIdx := -1;
    Try PairIdx := MechPairs.AddPair(TopL, BotL); Except PairIdx := -1; End;
    If PairIdx < 0 Then Exit;

    { Not asked on a build without pair kinds: both raised there and left  }
    { KindBack -1, which reads as 'will not report', not as a refusal.     }
    KindBack := -1;
    If Not MechKindsUnsupported(0) Then
    Begin
        Try MechPairs.SetState_LayerPairKind(PairIdx) := PairKind; Except End;
        Try KindBack := MechPairs.LayerPairKind(PairIdx); Except KindBack := -1; End;
    End;
    { A build that will not report the pair kind back must not read as a    }
    { failure, so only a value that came back DIFFERENT counts as refused.  }
    Result := (KindBack = PairKind) Or (KindBack < 0);
End;

{ The name the same request asked for on a given mechanical layer.            }
{                                                                              }
{ Needed because joining a layer pair renames both layers to Altium's own Top  }
{ and Bottom keywords, so the partner's name has to be put back even though    }
{ its own operation may already have run.                                      }

Function FindNameForLayerInOps(OpsStr : String; WantLayer : Integer) : String;
Var
    Remaining, Op, LayerName : String;
Begin
    Result := '';
    Remaining := OpsStr;
    While Trim(Remaining) <> '' Do
    Begin
        Op := NextBatchOp(Remaining);
        If Trim(Op) = '' Then Continue;
        LayerName := Trim(GetBatchField(Op, 'layer'));
        If ParseMechLayerNumber(LayerName) = WantLayer Then
        Begin
            Result := Trim(GetBatchField(Op, 'name'));
            Exit;
        End;
    End;
End;

{ Apply mechanical layer operations to a board's stack.                       }
{                                                                              }
{ SHARED BY THE LIBRARY AND THE BOARD. A PcbLib carries an IPCB_Board and so   }
{ does a PcbDoc, and from here down nothing cares which it came from. Only     }
{ the resolution differs: a library is taken by path and verified, a board is  }
{ whichever one is open.                                                       }
{                                                                              }
{ Written as one function rather than two because the paired-kind handling is  }
{ the awkward part: drain the existing pairs, add with the top layer first,    }
{ write the kind to the pair index, release whatever else holds it, retry, and }
{ put the released kinds back if it still will not take. A second copy of that }
{ would drift from this one, and the drift would be silent.                    }
{                                                                              }
{ Where names the document in the messages, so a caller reading a refusal      }
{ knows which file it is about.                                                }

Function ApplyMechLayerOps(Board : IPCB_Board; OpsStr : String;
    TidyPairs : Boolean; Where : String; RequestId : String) : String;
Var
    Op : String;
    LayerName, NewName, EnabledStr, KindStr : String;
    ItemsJson, Problems : String;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    MasterStack : IPCB_MasterLayerStack;
    MechPairs : IPCB_MechanicalLayerPairs;
    OpsAll : String;
    PartnerKind, PartnerLayer : Integer;
    PartnerTLayer : TLayer;
    Paired : Boolean;
    PairKind, Restored : Integer;
    PairKindSet, HavePair : Boolean;
    HandledPairs, PairTag, OpReleased, Remaining : String;
    Justified : Boolean;
    TidiedJson : String;
    ScanA, ScanB, KindA, KindB, Drained, PairsRemoved : Integer;
    TLayerA, TLayerB : TLayer;
    TopTLayer, BotTLayer : TLayer;
    PartnerObj : IPCB_LayerObject_V7;
    PartnerName : String;
    MechObj, OtherMech : IPCB_MechanicalLayer;
    DisplacedJson : String;
    Scan, OtherKind : Integer;
    TargetLayer : TLayer;
    MechNumber : Integer;
    KindId, KindBack, Changed, FailedCount : Integer;
    WantEnabled, GotEnabled, First, DidSomething : Boolean;
    NameBack : String;
Begin
    If Trim(OpsStr) = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layers required, for example '
            + '"layer=Mechanical13;name=Courtyard;enabled=true"');
        Exit;
    End;

    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except End;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP',
            'Could not access the layer stack of ' + Where);
        Exit;
    End;

    { The master stack is where a writable Kind lives. Guarded and       }
    { optional: an older build without it still gets name and enable,    }
    { and the kind write then reports that it did not take rather than   }
    { failing the whole call.                                             }
    MasterStack := Nil;
    If Not MechKindsUnsupported(0) Then
        Try MasterStack := Board.MasterLayerStack; Except MasterStack := Nil; End;

    { Where layer PAIRS live. A paired kind cannot be set without one. }
    MechPairs := Nil;
    Try MechPairs := Board.MechanicalPairs; Except MechPairs := Nil; End;

    { Kept whole: a paired kind looks up its partner in the caller's }
    { own request, and the loop below consumes OpsStr as it goes.    }
    OpsAll := OpsStr;

    ItemsJson := '';
    DisplacedJson := '';
    { The two halves of a pair name the SAME pair kind, so whichever is }
    { reached first does the work and the other reads the outcome here. }
    { Keying on the op order instead would break on a request that      }
    { lists Bottom before Top.                                          }
    HandledPairs := '';
    { Off by default: a tidy REMOVES pairs, and a caller that only wanted }
    { to rename a layer should not have the stack rearranged underneath.  }
    TidiedJson := '';
    PairsRemoved := 0;
    First := True;
    Changed := 0;
    FailedCount := 0;

    PCBServer.PreProcess;
    Try
        While Trim(OpsStr) <> '' Do
        Begin
            Op := NextBatchOp(OpsStr);
            If Trim(Op) = '' Then Continue;

            LayerName := Trim(GetBatchField(Op, 'layer'));
            NewName := GetBatchField(Op, 'name');
            EnabledStr := LowerCase(Trim(GetBatchField(Op, 'enabled')));
            KindStr := Trim(GetBatchField(Op, 'kind'));
            Problems := '';
            DidSomething := False;

            { Resolves the whole V9 range, not just the legacy 1 to 16.  }
            { A real library keeps most of its named layers in the 17 to }
            { 28 band, and those were being refused outright.            }
            MechNumber := ParseMechLayerNumber(LayerName);
            If MechNumber < 0 Then
                TargetLayer := eNoLayer
            Else
                TargetLayer := MechLayerFromNumber(MechNumber);

            If TargetLayer = eNoLayer Then
            Begin
                If MechNumber > 16 Then
                    Problems := 'mechanical layer ' + IntToStr(MechNumber)
                        + ' could not be resolved. This Altium build may not '
                        + 'expose LayerUtils.MechanicalLayer, which is what '
                        + 'reaches layers above 16.'
                Else
                    Problems := 'not a mechanical layer: ' + LayerName;
                LayerObj := Nil;
            End
            Else
            Begin
                LayerObj := Nil;
                Try LayerObj := LayerStack.LayerObject_V7[TargetLayer]; Except LayerObj := Nil; End;
                If LayerObj = Nil Then
                    Problems := 'layer not present in the stack';
            End;

            If LayerObj <> Nil Then
            Begin
                { ENABLED. Four reference scripts read and write             }
                { MechanicalLayerEnabled through LayerObject_V7, so it is     }
                { reached that way rather than through the                    }
                { ILayer.MechanicalLayer indexer, which is what is actually   }
                { undeclared in this binding.                                 }
                If EnabledStr <> '' Then
                Begin
                    WantEnabled := (EnabledStr = 'true') Or (EnabledStr = '1');
                    Try LayerObj.MechanicalLayerEnabled := WantEnabled; Except End;
                    GotEnabled := Not WantEnabled;
                    Try GotEnabled := LayerObj.MechanicalLayerEnabled; Except End;
                    If GotEnabled <> WantEnabled Then
                        Problems := Problems + 'enabled did not take. '
                    Else
                        DidSomething := True;
                    { A layer nobody can see is enabled but useless, so the  }
                    { display flag follows the enable rather than being a    }
                    { second call the caller has to remember.                }
                    If WantEnabled Then
                        Try Board.LayerIsDisplayed[TargetLayer] := True; Except End;
                End;

                If NewName <> '' Then
                Begin
                    Try LayerObj.Name := NewName; Except End;
                    NameBack := '';
                    Try NameBack := LayerObj.Name; Except End;
                    If NameBack <> NewName Then
                        Problems := Problems + 'name did not take. '
                    Else
                        DidSomething := True;
                End;

                If KindStr <> '' Then
                Begin
                    KindId := MechKindFromString(KindStr);
                    If KindId < 0 Then
                        Problems := Problems + 'unknown kind: ' + KindStr + '. '
                    Else
                    Begin
                        { KIND LIVES ON A DIFFERENT OBJECT.               }
                        {                                                  }
                        { Name and MechanicalLayerEnabled take on the      }
                        { LayerObject_V7 this handler already holds, and   }
                        { Kind does not: measured on a real library, the   }
                        { write was accepted and the read back was         }
                        { unchanged on EVERY mechanical layer, including   }
                        { ones well below 16, so it was never a range      }
                        { problem.                                         }
                        {                                                  }
                        { LayerObject_V7 is the LEGACY accessor. The       }
                        { reference reaches a writable Kind through        }
                        { MasterLayerStack.GetMechanicalLayer(n), which    }
                        { returns IPCB_MechanicalLayer, and writes Kind on }
                        { that. The V7 write is kept only as a fallback    }
                        { for builds with no MasterLayerStack.             }
                        MechObj := Nil;
                        If MasterStack <> Nil Then
                            Try
                                MechObj := MasterStack.GetMechanicalLayer(MechNumber);
                            Except
                                MechObj := Nil;
                            End;

                        { A PAIRED KIND NEEDS A LAYER PAIR FIRST.        }
                        {                                                  }
                        { Altium refuses "Component Outline Top" on a     }
                        { layer that is not joined to the layer carrying   }
                        { "Component Outline Bottom". Measured: on one     }
                        { layer in one call, Fab Notes and Not Set applied }
                        { and Component Outline Top was refused, with      }
                        { kinds_displaced empty, so contention was never   }
                        { the cause.                                        }
                        {                                                  }
                        { The partner comes from the SAME request. A       }
                        { caller setting a Top and its Bottom together is  }
                        { the ordinary case, and joining them here saves a }
                        { second pass that would need the pair anyway.     }
                        { THE PAIR HOLDS THE PAIRED KIND, NOT THE LAYER.  }
                        {                                                  }
                        { Creating the pair was necessary and not          }
                        { sufficient: with the pair present and nothing    }
                        { else holding the kind, the layer write still     }
                        { read back unchanged and left the                 }
                        { Library/LayerKindMapping stream empty, whose     }
                        { entry count tracks the number of assigned kinds  }
                        { exactly. A paired concept is carried by the pair }
                        { under a DIFFERENT enum, so it is written with    }
                        { SetState_LayerPairKind against a pair index.     }
                        {                                                  }
                        { Whichever half is reached first does the work,   }
                        { and the other reads the outcome out of           }
                        { HandledPairs: both name the same pair, so a      }
                        { second attempt would rebuild it and discard the  }
                        { kind just written.                                }
                        PartnerKind := MechKindPartner(KindId);
                        PairKind := MechPairKindFromLayerKind(KindId);
                        PairTag := '|' + IntToStr(PairKind) + '|';
                        HavePair := False;
                        PairKindSet := (PairKind >= 0)
                            And (Pos(PairTag, HandledPairs) > 0);

                        If (PartnerKind >= 0) And (PairKind >= 0)
                            And (MechPairs <> Nil) And (Not PairKindSet) Then
                        Begin
                            PartnerLayer := FindLayerForKindInOps(
                                OpsAll, PartnerKind);
                            If PartnerLayer > 0 Then
                            Begin
                                PartnerTLayer := MechLayerFromNumber(PartnerLayer);
                                If PartnerTLayer <> eNoLayer Then
                                Begin
                                    If Pos(' Top', MechKindToString(KindId)) > 0 Then
                                    Begin
                                        TopTLayer := TargetLayer;
                                        BotTLayer := PartnerTLayer;
                                    End
                                    Else
                                    Begin
                                        TopTLayer := PartnerTLayer;
                                        BotTLayer := TargetLayer;
                                    End;
                                    HavePair := True;

                                    PairKindSet := JoinAndKindPair(
                                        MechPairs, TopTLayer, BotTLayer, PairKind);
                                    If PairKindSet Then
                                        HandledPairs := HandledPairs + PairTag;

                                End;
                            End;
                        End;

                        { Write first, and only hunt on FAILURE.          }
                        {                                                  }
                        { A KIND BELONGS TO ONE LAYER AT A TIME, so        }
                        { assigning one another layer already holds does   }
                        { not take. That is why the single kinds land and  }
                        { the paired Top/Bottom ones are refused: a        }
                        { library already carrying Top Assembly elsewhere  }
                        { leaves nothing to assign. The reference clears   }
                        { the previous holder first.                        }
                        {                                                  }
                        { Searching for that holder means probing the      }
                        { stack a layer at a time, so it runs only when    }
                        { the write did not take. On the ordinary path it  }
                        { costs nothing, and on this one it costs a scan   }
                        { that stops at the layer it finds.                 }
                        KindBack := -1;
                        If MechObj <> Nil Then
                        Begin
                            Try MechObj.Kind := KindId; Except End;
                            Try KindBack := MechObj.Kind; Except KindBack := -1; End;
                        End;

                        { Not for a kind the pair already accepted. The     }
                        { layer property always reads back unchanged for a  }
                        { paired kind, so hunting on that alone would clear }
                        { the kind off unrelated layers on every call.      }
                        {                                                  }
                        { The layer numbers released are kept, because a   }
                        { release that is not followed by an assignment    }
                        { leaves the library holding FEWER kinds than it    }
                        { started with, and that has to be undone.          }
                        OpReleased := '';
                        If (KindBack <> KindId) And (KindId > 0)
                            And (Not PairKindSet)
                            And (MasterStack <> Nil) And (MechObj <> Nil) Then
                        Begin
                            Scan := 1;
                            While (Scan <= 1024) And (KindBack <> KindId) Do
                            Begin
                                If Scan <> MechNumber Then
                                Begin
                                    OtherMech := Nil;
                                    Try
                                        OtherMech := MasterStack.GetMechanicalLayer(Scan);
                                    Except
                                        OtherMech := Nil;
                                    End;
                                    If OtherMech <> Nil Then
                                    Begin
                                        OtherKind := -1;
                                        Try OtherKind := OtherMech.Kind; Except OtherKind := -1; End;
                                        If OtherKind = KindId Then
                                        Begin
                                            Try OtherMech.Kind := 0; Except End;
                                            OpReleased := OpReleased
                                                + IntToStr(Scan) + ',';
                                            If DisplacedJson <> '' Then
                                                DisplacedJson := DisplacedJson + ',';
                                            DisplacedJson := DisplacedJson
                                                + '{"layer":"Mechanical' + IntToStr(Scan)
                                                + '","kind":"'
                                                + EscapeJsonString(MechKindToString(KindId))
                                                + '","released_for":"Mechanical'
                                                + IntToStr(MechNumber) + '"}';
                                            Try MechObj.Kind := KindId; Except End;
                                            Try KindBack := MechObj.Kind; Except KindBack := -1; End;
                                        End;
                                    End;
                                End;
                                Scan := Scan + 1;
                            End;
                        End;

                        { RETRY THE PAIR ONCE THE OLD HOLDER IS CLEAR.      }
                        {                                                  }
                        { A pair kind is exclusive the same way a layer     }
                        { kind is, so the first attempt is refused while    }
                        { another layer still carries it. Releasing that    }
                        { layer and stopping there is the worst of both:    }
                        { measured on two libraries, the release took, the  }
                        { assignment did not, and only an identical second  }
                        { call recovered. Retrying here closes that window  }
                        { inside the one call.                              }
                        If HavePair And (Not PairKindSet) And (OpReleased <> '') Then
                        Begin
                            PairKindSet := JoinAndKindPair(
                                MechPairs, TopTLayer, BotTLayer, PairKind);
                            If PairKindSet Then
                                HandledPairs := HandledPairs + PairTag;
                        End;

                        { AND PUT THE KINDS BACK IF IT STILL WILL NOT TAKE. }
                        { Ending a call with the kind on neither the old    }
                        { layer nor the new one is destructive, and it      }
                        { reads as a partial success rather than a refusal. }
                        If (Not PairKindSet) And (KindBack <> KindId)
                            And (OpReleased <> '') And (MasterStack <> Nil) Then
                        Begin
                            Restored := 0;
                            Remaining := OpReleased;
                            While Pos(',', Remaining) > 0 Do
                            Begin
                                Scan := StrToIntDef(
                                    Copy(Remaining, 1, Pos(',', Remaining) - 1), -1);
                                Remaining := Copy(Remaining, Pos(',', Remaining) + 1,
                                    Length(Remaining));
                                If Scan > 0 Then
                                Begin
                                    OtherMech := Nil;
                                    Try
                                        OtherMech := MasterStack.GetMechanicalLayer(Scan);
                                    Except
                                        OtherMech := Nil;
                                    End;
                                    If OtherMech <> Nil Then
                                    Begin
                                        Try OtherMech.Kind := KindId; Except End;
                                        Restored := Restored + 1;
                                    End;
                                End;
                            End;
                            If Restored > 0 Then
                                Problems := Problems + 'the kind was put back on '
                                    + IntToStr(Restored) + ' layer(s) it was '
                                    + 'released from, so nothing was lost. ';
                        End;

                        { Last resort: the legacy object, for a build with }
                        { no MasterLayerStack at all.                       }
                        { Not on a build known to have no kinds: the write   }
                        { and the read-back raised there, and KindBack stays  }
                        { -1, the 'kind did not take' report.                 }
                        If (KindBack <> KindId) And (MechObj = Nil)
                            And (Not MechKindsUnsupported(0)) Then
                        Begin
                            Try LayerObj.Kind := KindId; Except End;
                            KindBack := ReadMechKind(LayerObj);
                        End;

                        { PAIRING RENAMES BOTH LAYERS. Altium forces its    }
                        { own Top and Bottom keywords onto a layer the      }
                        { moment it joins a pair, which silently undoes the }
                        { name written earlier in this operation. Restored  }
                        { after the retry, since that rebuilds the pair and }
                        { would otherwise substitute the keyword again.     }
                        If HavePair Then
                        Begin
                            If NewName <> '' Then
                            Begin
                                Try LayerObj.Name := NewName; Except End;
                                NameBack := '';
                                Try NameBack := LayerObj.Name; Except End;
                                If NameBack <> NewName Then
                                    Problems := Problems
                                        + 'name did not survive pairing. ';
                            End;

                            { The partner keeps whatever name the same      }
                            { request asked for, rather than the keyword    }
                            { Altium substituted.                            }
                            PartnerName := FindNameForLayerInOps(
                                OpsAll, PartnerLayer);
                            If PartnerName <> '' Then
                            Begin
                                PartnerObj := Nil;
                                Try
                                    PartnerObj := LayerStack.LayerObject_V7[PartnerTLayer];
                                Except
                                    PartnerObj := Nil;
                                End;
                                If PartnerObj <> Nil Then
                                    Try PartnerObj.Name := PartnerName; Except End;
                            End;
                        End;

                        { A PAIRED KIND SUCCEEDS ON THE PAIR, so the layer  }
                        { property reading back unchanged is expected and  }
                        { is not the measure of whether it took.            }
                        If (KindBack = KindId) Or PairKindSet Then
                            DidSomething := True
                        Else
                        Begin
                            If MechObj = Nil Then
                                Problems := Problems + 'kind did not take, '
                                    + 'and MasterLayerStack.GetMechanicalLayer '
                                    + 'was unavailable, which is where a '
                                    + 'writable Kind lives. '
                            Else If (PartnerKind >= 0)
                                And (FindLayerForKindInOps(OpsAll, PartnerKind) < 0) Then
                                Problems := Problems + '"'
                                    + MechKindToString(KindId)
                                    + '" is one half of a pair, and a paired '
                                    + 'kind is held by the layer PAIR rather '
                                    + 'than by either layer. Assign "'
                                    + MechKindToString(PartnerKind)
                                    + '" to another mechanical layer in the '
                                    + 'SAME call so the two can be joined and '
                                    + 'the pair given the kind. '
                            Else If PartnerKind >= 0 Then
                                Problems := Problems + '"'
                                    + MechKindToString(KindId)
                                    + '" was refused as pair kind "'
                                    + MechPairKindToString(PairKind)
                                    + '" even with the pair present. '
                            Else
                                Problems := Problems + 'kind did not take; '
                                    + 'it reads back as "'
                                    + MechKindToString(KindBack) + '". ';
                        End;
                    End;
                End;

                If (EnabledStr = '') And (NewName = '') And (KindStr = '') Then
                    Problems := 'nothing asked for: give name, enabled or kind';
            End;

            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            ItemsJson := ItemsJson
                + '{"layer":"' + EscapeJsonString(LayerName) + '",'
                + '"changed":' + BoolToJsonStr(DidSomething And (Problems = '')) + ','
                + '"problem":';
            If Problems = '' Then
                ItemsJson := ItemsJson + 'null}'
            Else
                ItemsJson := ItemsJson + '"' + EscapeJsonString(Trim(Problems)) + '"}';

            If (Problems = '') And DidSomething Then
                Changed := Changed + 1
            Else
                FailedCount := FailedCount + 1;
        End;

        { STALE PAIRS OUTLIVE THE KINDS THAT JUSTIFIED THEM.               }
        {                                                                  }
        { Nothing removes a pair when a kind moves to different layers,    }
        { and until AddPair stopped being called on pairs that already     }
        { existed, every sweep appended another copy. One library reached  }
        { fourteen pairs where four were wanted, all of them visible in    }
        { the Layer Stack Manager, and no operation exposed here could     }
        { clear them.                                                      }
        {                                                                  }
        { A pair earns its place only when the two layers carry kinds that }
        { are each other's opposite side. Anything else is left over, so   }
        { it goes. Pairs the ops above just built are kept by that same    }
        { test rather than by remembering them.                            }
        If TidyPairs And (MechPairs <> Nil) And (MasterStack <> Nil) Then
        Begin
            PairsRemoved := 0;
            For ScanA := 1 To MechScanLimit Do
            Begin
                KindA := -1;
                OtherMech := Nil;
                Try OtherMech := MasterStack.GetMechanicalLayer(ScanA); Except OtherMech := Nil; End;
                If OtherMech <> Nil Then
                    Try KindA := OtherMech.Kind; Except KindA := -1; End;

                TLayerA := MechLayerFromNumber(ScanA);
                If TLayerA <> eNoLayer Then
                    For ScanB := ScanA + 1 To MechScanLimit Do
                    Begin
                        TLayerB := MechLayerFromNumber(ScanB);
                        If TLayerB <> eNoLayer Then
                            If PairIsDefined(MechPairs, TLayerA, TLayerB) Then
                            Begin
                                KindB := -1;
                                OtherMech := Nil;
                                Try OtherMech := MasterStack.GetMechanicalLayer(ScanB); Except OtherMech := Nil; End;
                                If OtherMech <> Nil Then
                                    Try KindB := OtherMech.Kind; Except KindB := -1; End;

                                { Both sides must be paired kinds AND be  }
                                { each other's partner. A pair whose two  }
                                { layers hold unrelated kinds is not a    }
                                { pair anyone asked for.                   }
                                Justified := (KindA > 0) And (KindB > 0)
                                    And (MechKindPartner(KindA) = KindB);
                                If Not Justified Then
                                Begin
                                    Drained := DrainMechPair(MechPairs, TLayerA, TLayerB);
                                    PairsRemoved := PairsRemoved + Drained;
                                    If Drained > 0 Then
                                    Begin
                                        If TidiedJson <> '' Then
                                            TidiedJson := TidiedJson + ',';
                                        TidiedJson := TidiedJson
                                            + '{"layers":["Mechanical' + IntToStr(ScanA)
                                            + '","Mechanical' + IntToStr(ScanB) + '"],'
                                            + '"removed":' + IntToStr(Drained) + '}';
                                    End;
                                End;
                            End;
                    End;
            End;
        End;

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    { NOTHING PARSED IS A FAILURE, not a successful run over no work.    }
    { A layers payload in the wrong shape produced no operations at all,  }
    { and this reported success having changed nothing. Across a sweep of }
    { twenty two libraries that read as twenty two successes.             }
    If (Changed = 0) And (FailedCount = 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OPERATIONS',
            'No layer operations were parsed from the layers parameter, so '
            + 'nothing was changed in ' + Where + '. Expected '
            + '"layer=<name>;name=<text>;enabled=<bool>;kind=<text>" with '
            + '"~~" between entries.');
        Exit;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    MarkDocDirtyByPath(Where);

    Result := BuildSuccessResponse(RequestId,
        '{"document":"' + EscapeJsonString(Where) + '",'
        + '"layers":[' + ItemsJson + '],'
        + '"changed":' + IntToStr(Changed) + ','
        + '"failed":' + IntToStr(FailedCount) + ','
        { Which layers gave up a kind so this one could take it. A kind }
        { that silently moved off another layer is a change the caller  }
        { did not ask for and has to be able to see.                     }
        + '"kinds_displaced":[' + DisplacedJson + '],'
        + '"pairs_removed":' + IntToStr(PairsRemoved) + ','
        + '"pairs_tidied":[' + TidiedJson + '],'
        { The tidy sweep stops at this layer number. A stale pair above }
        { it survives, and saying so is the difference between a bound  }
        { and a silent one.                                              }
        + '"pairs_scanned_to":' + IntToStr(MechScanLimit) + '}');
End;

{ The library half: take the library by PATH and refuse unless the document  }
{ that ended up focused is the one asked for.                                }
{                                                                             }
{ Acting on the wrong library is worse than not acting, because it looks like }
{ it worked. A sweep over twenty one libraries once returned twenty one       }
{ identical answers, every call having re-read the same focused file.         }

{ Delete primitives from ONE footprint in a PcbLib.                           }
{                                                                              }
{ THE LIBRARY HAD NO PRIMITIVE DELETE AT ALL. obj_delete and pcb_delete_object }
{ both resolve a BOARD, and when none is focused the board lookup opens the    }
{ first PcbDoc any open project holds. So a caller working in a footprint had  }
{ no correct tool, and the incorrect one removed primitives from a board they  }
{ had not named and did not report which.                                      }
{                                                                              }
{ Scoped three ways, all required to agree before anything is removed: the     }
{ LIBRARY by path, the FOOTPRINT by name, and the object type. A layer filter  }
{ narrows it further, which is the usual case: clear the silkscreen on one     }
{ footprint without touching its pads.                                         }
{                                                                              }
{ Pads are excluded unless include_pads is set. Deleting a pad changes the     }
{ part's connectivity rather than its drawing, and a caller clearing graphics  }
{ off a layer should not lose the pinout to a filter that was wider than they  }
{ realised.                                                                    }
{                                                                              }
{ Collect first, delete second. Removing objects while the group iterator is   }
{ walking them is how a traversal skips half the list, and a half-cleared      }
{ footprint looks like the filter was wrong.                                   }
{                                                                              }
{ FOOTPRINT ONLY BY DEFAULT, the removal this tool has always made.            }
{ remove_from_board=true adds the library-board half, which is unmeasured: the }
{ footprint is made current (verified) before anything goes, and the board is  }
{ swept after the footprint is finished. See REMOVING PRIMITIVES FROM A PCBLIB }
{ FOOTPRINT, beside RemoveLibTextByAddress, for the rule and why. The reply's  }
{ double_listed counts primitives added this session (listed twice): those are }
{ the ones a footprint-only delete has been seen to bring back after a save    }
{ and reload, and the note says what to do about it.                           }
{                                                                              }
{ MEASURED 2026-10-04 on lib_delete_footprint_pads, which removes call for    }
{ call the way this does: a primitive created this session and removed from   }
{ its footprint is written back by the save, and a disk-loaded one is not     }
{ (the full record is above Lib_DeleteFootprintPads). The tool therefore     }
{ saves and reloads first (reload_first, default on), then asks for            }
{ park_current, and this reply carries the same diagnostics as the pad         }
{ delete: target_was_current, parked_elsewhere,                                }
{ target_current_during_removal, board_still_listed (LibBoardStillListsCount,  }
{ read-only, null when the footprint was not current) and marked_modified      }
{ (marked through the given path and PcbLib.Board.FileName).                   }

Function Lib_DeleteFootprintPrimitives(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, ObjTypeStr, LayerStr : String;
    ConfirmStr, RemovedJson, PrimAddr, LastAddr, SeenAddrs, Entries : String;
    StillAddrs, BoardJson, NoteStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    GrpIter : IPCB_GroupIterator;
    Footprint, Target : IPCB_LibComponent;
    Prim : IPCB_Primitive;
    { Integer, not TObjectId. ObjectTypeFromStringPCB returns an Integer,
      and declaring the target as an enum type this build may not define
      the way the script expects is its own runtime fault. }
    ObjFilter : Integer;
    WantLayer, MatchLayer : TLayer;
    IncludePads, Found, FromBoard, MadeCurrent : Boolean;
    TargetWasCurrent, MarkedModified, ParkWanted, Parked, CurrentDuring : Boolean;
    MarkJson : String;
    Removed, I, Examined, PrimOid, SameRun, RemoveCalls, DoubleListed : Integer;
    StillListed, BoardRemoved, BoardStill : Integer;
    BoardStillJson : String;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    LayerStr := ExtractJsonValue(Params, 'layer');
    ConfirmStr := ExtractJsonValue(Params, 'confirm');
    IncludePads := (ExtractJsonValue(Params, 'include_pads') = 'true');
    FromBoard := (LowerCase(Trim(ExtractJsonValue(Params, 'remove_from_board'))) = 'true');
    ParkWanted := (LowerCase(Trim(ExtractJsonValue(Params, 'park_current'))) = 'true');

    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'footprint_name required. Deleting from whichever footprint the '
            + 'editor happens to show is the mistake this tool exists to '
            + 'avoid.');
        Exit;
    End;

    If ConfirmStr <> 'true' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CONFIRM_REQUIRED',
            'This removes primitives from footprint "' + FpWanted
            + '". Pass confirm=true once the object_type and layer are '
            + 'what you mean. Read them first with lib_probe_footprint.');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    { Verified AFTER the open, the same way lib_set_mech_layers does. A     }
    { delete aimed at a library that never came to the front would land in  }
    { whichever one did.                                                     }
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was deleted.');
        Exit;
    End;

    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Focused ' + LibPath + ' but it is not a PCB library. To '
            + 'delete primitives from a BOARD use pcb_delete_object. '
            + 'This tool edits a footprint inside a .PcbLib.');
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    { MADE CURRENT ONLY FOR THE BOARD HALF, verified, before anything is
      removed: the library board holds only the current footprint's
      primitives (rule 3 beside RemoveLibTextByAddress). A footprint-only
      delete leaves CurrentComponent alone, as this tool always did. }
    MadeCurrent := False;
    If FromBoard Then
    Begin
        MadeCurrent := LibMakeCurrentVerified(PcbLib, Target);
        If Not MadeCurrent Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SELECT_FAILED',
                'Could not make footprint "' + FpWanted + '" current, which '
                + 'remove_from_board needs. Nothing was deleted.');
            Exit;
        End;
    End;
    { Read, never written (see the measured note above the function). }
    TargetWasCurrent := False;
    Try TargetWasCurrent := (PcbLib.CurrentComponent = Target); Except TargetWasCurrent := False; End;
    { park_current: remove from a footprint that is NOT current, the condition }
    { of the delete that persisted (LibParkCurrentElsewhere, and the measured }
    { block above Lib_DeleteFootprintPads). Sent only after the tool's        }
    { reload_first ran, never honoured with remove_from_board.                }
    Parked := False;
    If ParkWanted And (Not FromBoard) And TargetWasCurrent Then
        Parked := LibParkCurrentElsewhere(PcbLib, Target);
    CurrentDuring := False;
    Try CurrentDuring := (PcbLib.CurrentComponent = Target); Except CurrentDuring := False; End;

    ObjFilter := ObjectTypeFromStringPCB(ObjTypeStr);
    If (ObjTypeStr <> '') And (ObjFilter = -1) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE',
            'Unknown PCB object type: ' + ObjTypeStr);
        Exit;
    End;

    WantLayer := eNoLayer;
    If LayerStr <> '' Then
    Begin
        WantLayer := GetLayerFromString(LayerStr);
        If WantLayer = eNoLayer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
                'Unknown layer name: ' + LayerStr);
            Exit;
        End;
    End;

    If (ObjTypeStr = '') And (LayerStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'TOO_BROAD',
            'Give object_type or layer. Emptying a whole footprint is not '
            + 'something to reach by leaving both filters off.');
        Exit;
    End;

    { ONE MATCH PER PASS, and never a primitive held outside its iterator.  }
    {                                                                        }
    { The first version collected the matches into a TInterfaceList and then }
    { deleted them. That crashes the engine with an access violation:        }
    { DelphiScript narrows an interface at ITERATOR RETURN, and a typed      }
    { local assigned from an untyped list element keeps the base dispatch,   }
    { so RemovePCBObject was handed something that was not usable as a       }
    { primitive.                                                             }
    {                                                                        }
    { Removing during a walk is not the alternative: that makes the          }
    { traversal skip entries and leaves a half-cleared footprint that reads  }
    { as a filter which was too narrow. So each pass takes a fresh iterator, }
    { stops at the FIRST match, closes the iterator, and removes it. A       }
    { footprint holds tens of primitives, so the repeated walk costs         }
    { nothing worth optimising.                                              }
    Removed := 0;
    Examined := 0;
    RemovedJson := '';
    RemoveCalls := 0;
    DoubleListed := 0;
    SameRun := 0;
    LastAddr := '';
    SeenAddrs := '|';
    Entries := '';

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
            c_Broadcast, PCBM_BeginModify, c_NoEventData);

        { Bounded, so a RemovePCBObject that silently refuses cannot spin  }
        { here forever re-finding the same primitive.                       }
        For I := 1 To 5000 Do
        Begin
            MatchLayer := eNoLayer;
            Found := False;
            GrpIter := Target.GroupIterator_Create;
            Try
                Prim := GrpIter.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    If I = 1 Then Examined := Examined + 1;
                    If ((ObjFilter = -1) Or (Prim.ObjectId = ObjFilter))
                       And ((WantLayer = eNoLayer) Or (Prim.Layer = WantLayer))
                       And (IncludePads Or (Prim.ObjectId <> ePadObject)) Then
                    Begin
                        MatchLayer := Prim.Layer;
                        Found := True;
                        Break;
                    End;
                    Prim := GrpIter.NextPCBObject;
                End;
                { REMOVE WHILE THE ITERATOR IS STILL OPEN.

                  This used to destroy the iterator first, on the reasoning
                  that a closed iterator cannot have its walk disturbed by
                  the removal. It cannot, but that was never the risk:
                  GroupIterator_Destroy releases what the iterator handed
                  out, so Prim was dangling by the time it was removed, and
                  the engine died on a null read inside
                  ScriptingSystem.DLL. An access violation in the DLL is
                  not something the Try below can catch, so the whole
                  polling loop went with it.

                  Removing here is safe because the walk STOPS at the first
                  match: the Break above means NextPCBObject is never called
                  again on this iterator, so the skip-entries problem that
                  the one-match-per-pass design exists to avoid cannot
                  arise. The outer loop takes a fresh iterator for the next
                  one. }
                { FOOTPRINT ONLY: the removal this tool has always made, and
                  the only one that has run live. The board half, when asked
                  for, runs after this loop has finished with every object.

                  A primitive added this session is listed twice until a
                  reload, so its address can come round again, and each pass
                  removes one listing; it is counted once in removed and once
                  in double_listed. A THIRD consecutive sighting of one
                  address means RemovePCBObject is returning without
                  removing, and the loop stops there instead of spinning to
                  its bound (Body3D_RemoveAll's guard). }
                If Found And (Prim <> Nil) Then
                Begin
                    PrimAddr := '';
                    Try PrimAddr := IntToStr(Prim.I_ObjectAddress); Except End;
                    PrimOid := -1;
                    Try PrimOid := Prim.ObjectId; Except PrimOid := -1; End;
                    If (PrimAddr <> '') And (PrimAddr = LastAddr) Then
                        SameRun := SameRun + 1
                    Else
                        SameRun := 1;
                    LastAddr := PrimAddr;
                    If SameRun > 2 Then
                        Found := False
                    Else
                    Begin
                        Try
                            Target.RemovePCBObject(Prim);
                            RemoveCalls := RemoveCalls + 1;
                            If (PrimAddr <> '')
                               And (Pos('|' + PrimAddr + '|', SeenAddrs) > 0) Then
                                DoubleListed := DoubleListed + 1
                            Else
                            Begin
                                If PrimAddr <> '' Then
                                    SeenAddrs := SeenAddrs + PrimAddr + '|';
                                Entries := LibAddRemovalEntry(Entries, PrimOid, PrimAddr);
                                Removed := Removed + 1;
                                If RemovedJson <> '' Then
                                    RemovedJson := RemovedJson + ',';
                                RemovedJson := RemovedJson + '"'
                                    + EscapeJsonString(GetLayerString(MatchLayer))
                                    + '"';
                            End;
                        Except
                            { It matched and would not go. Stop rather than
                              loop on it forever. }
                            Found := False;
                        End;
                    End;
                End;
            Finally
                Target.GroupIterator_Destroy(GrpIter);
            End;

            If Not Found Then Break;
        End;

        PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
            c_Broadcast, PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    { READ BACK: which removed objects does the footprint still list? A
      read-only walk with the members the loop above already uses. }
    StillAddrs := '|';
    StillListed := 0;
    If Entries <> '' Then
    Begin
        GrpIter := Target.GroupIterator_Create;
        Try
            Prim := GrpIter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Prim.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (Pos(':' + PrimAddr + '|', Entries) > 0)
                   And (Pos('|' + PrimAddr + '|', StillAddrs) = 0) Then
                Begin
                    StillAddrs := StillAddrs + PrimAddr + '|';
                    StillListed := StillListed + 1;
                End;
                Prim := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;
    End;

    { THE OPT-IN BOARD HALF, after the footprint is finished, never on an
      object the footprint still lists (rule 4). }
    BoardRemoved := 0;
    BoardJson := JsonObj(JsonBool('requested', False));
    If FromBoard Then
    Begin
        PCBServer.PreProcess;
        Try
            BoardJson := LibBoardRemoveEntries(PcbLib.Board, Entries,
                StillAddrs, MadeCurrent, BoardRemoved);
        Finally
            PCBServer.PostProcess;
        End;
    End;

    { What the LIBRARY BOARD still holds of the removed primitives, only      }
    { while the footprint is current (null otherwise: the board holds no     }
    { primitive of a footprint that is not current, rule 3).                  }
    BoardStill := 0;
    BoardStillJson := 'null';
    If (Entries <> '') And CurrentDuring Then
    Begin
        BoardStill := LibBoardStillListsCount(PcbLib.Board, Entries);
        BoardStillJson := IntToStr(BoardStill);
    End
    Else If CurrentDuring Then
        BoardStillJson := '0';

    MarkedModified := False;
    MarkJson := 'null';
    If (Removed > 0) Or (BoardRemoved > 0) Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        { Both paths are marked, as before: the board's own file name and   }
        { the caller's library_path. MarkedModified is what the marks READ  }
        { BACK, and mark reports the board's mark unless only the           }
        { library_path one took.                                            }
        MarkedModified := MarkDocDirty(PcbLib.Board.FileName);
        MarkJson := MarkOutcomeJson(0);
        If MarkDocDirty(LibPath) And (Not MarkedModified) Then
        Begin
            MarkedModified := True;
            MarkJson := MarkOutcomeJson(0);
        End;
    End;

    NoteStr := 'Removed in memory; lib_save_library writes it. Proof it '
        + 'persisted: lib_read_pcblib_file(library_path, footprint_name) '
        + 'reads the saved file with no Altium involved.';
    If (DoubleListed > 0) And (Not FromBoard) Then
        NoteStr := NoteStr + ' ' + IntToStr(DoubleListed) + ' of these were '
            + 'added this session (listed twice). A footprint-only delete of '
            + 'such a primitive has been seen to come back after save and '
            + 'reload: if one does, save, lib_reload_library and delete it '
            + 'again, or repeat with remove_from_board=true (unmeasured, see '
            + 'RELEASE_VERIFICATION step 24).';

    Result := BuildSuccessResponse(RequestId,
        '{"library":"' + EscapeJsonString(LibPath) + '",'
        + '"footprint":"' + EscapeJsonString(FpWanted) + '",'
        + '"removed":' + IntToStr(Removed) + ','
        + '"remove_calls":' + IntToStr(RemoveCalls) + ','
        + '"double_listed":' + IntToStr(DoubleListed) + ','
        + '"still_listed":' + IntToStr(StillListed) + ','
        + '"board_still_listed":' + BoardStillJson + ','
        + '"target_was_current":' + BoolToJsonStr(TargetWasCurrent) + ','
        + '"parked_elsewhere":' + BoolToJsonStr(Parked) + ','
        + '"target_current_during_removal":' + BoolToJsonStr(CurrentDuring) + ','
        + '"marked_modified":' + BoolToJsonStr(MarkedModified) + ','
        + '"mark":' + MarkJson + ','
        + '"examined":' + IntToStr(Examined) + ','
        + '"layers":[' + RemovedJson + '],'
        + '"pads_included":' + BoolToJsonStr(IncludePads) + ','
        + '"board_removal":' + BoardJson + ','
        + '"note":"' + EscapeJsonString(NoteStr) + '"}');
End;

{..............................................................................}
{ PAD EDITING INSIDE A PCBLIB FOOTPRINT                                        }
{                                                                              }
{ lib_modify_footprint_pads, lib_delete_footprint_pads and                     }
{ lib_set_footprint_description. Before these the only way to change ONE pad  }
{ of a saved footprint was to edit the .PcbLib OLE stream by hand (done        }
{ 2026-10-03 on the multiviewer carrier library: three shape bytes per pad,    }
{ library closed, backup kept), because lib_delete_footprint_primitives takes  }
{ every pad on a layer or none, and deleting and re-adding them loses per-pad  }
{ overrides such as a manual paste expansion.                                  }
{                                                                              }
{ TARGETS ARE RESOLVED ONCE, BEFORE ANYTHING CHANGES. One read-only walk       }
{ records every pad's address and designator in iterator order, deduplicated   }
{ by address: a pad added in this session is yielded TWICE until the library   }
{ is reloaded (measured, see Lib_GetPadGeometry). "index" is the n-th pad with }
{ that designator in that order, which is the order lib_get_pad_geometry       }
{ lists them in, and it keeps meaning the same pad after an earlier edit in    }
{ the same call renamed or removed one.                                        }
{                                                                              }
{ EACH TARGET IS THEN REACHED BY A FRESH WALK THAT STOPS ON IT, and is changed }
{ or removed while that iterator is still open. Both recorded lessons allow    }
{ exactly that: Lib_DeleteFootprintPrimitives measured that a primitive is     }
{ dangling once its GroupIterator is destroyed, and a walk that never advances }
{ after the change cannot skip entries.                                        }
{                                                                              }
{ EVERY CHANGE IS BRACKETED with PCBM_BeginModify / PCBM_EndModify on the pad  }
{ (Lib_SetDesignator: without it the edit lives in memory and is dropped when  }
{ the component is re-serialised on save) and READ BACK field by field.        }
{..............................................................................}

{ The footprint by name, case-insensitively, as Lib_DeleteFootprintPrimitives  }
{ and Lib_SetDesignator match it. Nil when absent.                             }
Function FindPcbLibFootprint(PcbLib : IPCB_Library; FpWanted : String) : IPCB_LibComponent;
Var
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    FpName : String;
Begin
    Result := Nil;
    If PcbLib = Nil Then Exit;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Result := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
End;

{ An angle folded into [0, 360), bounded so a wild value cannot spin here.    }
Function NormDeg(A : Double) : Double;
Var
    I : Integer;
Begin
    Result := A;
    For I := 1 To 64 Do
    Begin
        If Result >= 360 Then Result := Result - 360;
        If Result < 0 Then Result := Result + 360;
    End;
End;

{ One pad's state as a JSON object, in MILS relative to the footprint origin, }
{ which is what the modify tool takes. Every member read here is already read }
{ by Lib_GetPadGeometry or LmPadsSection; the per-layer block is what makes    }
{ the through-hole roundrect fix checkable in one call.                        }
Function PadStateJson(Pad : IPCB_Pad; XOrg : Integer; YOrg : Integer) : String;
Var
    S, Body, HoleStr, ModeStr, MaskSrc, PasteSrc : String;
    IsTH, Simple : Boolean;
    Cache : TPadCache;
    Pct : Integer;
    Val, MaskVal, PasteVal : Double;
Begin
    IsTH := False;
    Try IsTH := (Pad.Layer = eMultiLayer); Except End;
    HoleStr := 'round';
    Try
        If Pad.HoleType = eSquareHole Then HoleStr := 'square'
        Else If Pad.HoleType = eSlotHole Then HoleStr := 'slot';
    Except End;
    Pct := 0;
    Try Pct := Pad.StackCRPctOnLayer[eTopLayer]; Except Pct := 0; End;
    ModeStr := '-1';
    Try ModeStr := IntToStr(Pad.Mode); Except ModeStr := '-1'; End;
    Simple := False;
    Try Simple := (Pad.Mode = ePadMode_Simple); Except Simple := False; End;

    S := JsonStr('designator', Pad.Name)
        + ',' + JsonFloat('x_mil', CoordToMilsF(Pad.X - XOrg))
        + ',' + JsonFloat('y_mil', CoordToMilsF(Pad.Y - YOrg))
        + ',' + JsonFloat('x_size_mil', CoordToMilsF(Pad.TopXSize))
        + ',' + JsonFloat('y_size_mil', CoordToMilsF(Pad.TopYSize))
        + ',' + JsonStr('shape', PadShapeWord(Pad.TopShape))
        + ',' + JsonInt('corner_pct', Pct)
        + ',' + JsonFloat('rotation', Pad.Rotation)
        + ',' + JsonStr('layer', GetLayerString(Pad.Layer))
        + ',' + JsonFloat('hole_mil', CoordToMilsF(Pad.HoleSize))
        + ',' + JsonStr('hole_type', HoleStr);

    Val := 0;
    Try Val := CoordToMilsF(Pad.HoleWidth); Except Val := 0; End;
    S := S + ',' + JsonFloat('hole_width_mil', Val);
    Val := 0;
    Try Val := Pad.HoleRotation; Except Val := 0; End;
    S := S + ',' + JsonFloat('hole_rotation', Val);
    Try
        S := S + ',' + JsonBool('plated', Pad.Plated);
    Except
        S := S + ',' + JsonNull('plated');
    End;
    S := S + ',' + JsonRaw('mode', ModeStr) + ',' + JsonBool('mode_simple', Simple);

    If IsTH Then
    Begin
        Body := '';
        Try
            Body := JsonStr('shape', PadShapeWord(Pad.StackShapeOnLayer[eBottomLayer]))
                + ',' + JsonFloat('x_size_mil', CoordToMilsF(Pad.XStackSizeOnLayer[eBottomLayer]))
                + ',' + JsonFloat('y_size_mil', CoordToMilsF(Pad.YStackSizeOnLayer[eBottomLayer]))
                + ',' + JsonInt('corner_pct', Pad.StackCRPctOnLayer[eBottomLayer]);
        Except
            Body := '';
        End;
        S := S + ',' + JsonRaw('bottom', JsonObj(Body));
        Body := '';
        Try
            Body := JsonStr('shape', PadShapeWord(Pad.MidShape))
                + ',' + JsonFloat('x_size_mil', CoordToMilsF(Pad.MidXSize))
                + ',' + JsonFloat('y_size_mil', CoordToMilsF(Pad.MidYSize));
        Except
            Body := '';
        End;
        S := S + ',' + JsonRaw('mid', JsonObj(Body));
    End;

    MaskSrc := 'rule';
    PasteSrc := 'rule';
    MaskVal := 0;
    PasteVal := 0;
    Try
        Cache := Pad.GetState_Cache;
        If Cache.SolderMaskExpansionValid = eCacheManual Then
        Begin
            MaskSrc := 'manual';
            MaskVal := CoordToMilsF(Cache.SolderMaskExpansion);
        End;
        If Cache.PasteMaskExpansionValid = eCacheManual Then
        Begin
            PasteSrc := 'manual';
            PasteVal := CoordToMilsF(Cache.PasteMaskExpansion);
        End;
    Except End;
    S := S + ',' + JsonFloat('mask_expansion_mil', MaskVal)
        + ',' + JsonStr('mask_expansion_source', MaskSrc)
        + ',' + JsonFloat('paste_expansion_mil', PasteVal)
        + ',' + JsonStr('paste_expansion_source', PasteSrc);
    Result := JsonObj(S);
End;

{..............................................................................}
{ Lib_ModifyFootprintPads - change named pads of one PcbLib footprint.         }
{                                                                              }
{ Params: footprint_name (required), library_path (optional), edits (batch,    }
{ ~~ / ; / =). Per edit: designator (required; '' selects the unnamed pads),  }
{ index (0-based among the pads with that designator; REQUIRED when more than  }
{ one pad carries it, so a duplicate is never edited by guess), then any of:   }
{ x, y, x_size, y_size, hole_size, slot_length, mask_expansion,                }
{ paste_expansion (mils, sub-mil kept; the two expansions also take 'rule'),   }
{ rotation, hole_rotation (degrees), shape, corner_radius (percent), plated,   }
{ hole_type, layer (SMD only), new_designator, clear_designator.               }
{                                                                              }
{ Order inside one edit is fixed: drill and layer first (the shape setters are }
{ layer-aware, and a hole_size crossing zero turns a pad SMD or through-hole   }
{ exactly as the add tools do), then position, size, shape, hole form,         }
{ mask/paste, and the name last. Each edit is validated completely before      }
{ anything is written, so a bad field leaves that pad untouched.               }
{..............................................................................}
Function Lib_ModifyFootprintPads(Params : String; RequestId : String) : String;
Var
    LibPath, FpWanted, EditsStr, Remaining, Op, ErrCode, ErrMsg : String;
    Desig, IdxStr, Canon, ShapeStr, ShapeHow, LayerStr, NewName, ClearStr : String;
    XStr, YStr, XSStr, YSStr, HoleStr, RotStr, CRStr, PlatedStr, HoleTypeStr : String;
    SlotStr, HoleRotStr, MaskStr, PasteStr, CurShape, TxtName, BadField : String;
    TargetAddr, PadAddr, ItemJson, ResultsJson : String;
    ChangedJson, MismatchJson, WarnJson : String;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Cache : TPadCache;
    SnapAddrs, SnapNames, UsedAddrs : TStringList;
    XOrg, YOrg, WantIdx, ResIdx, Matches, Hit, I : Integer;
    OpCount, Applied, FailedCount, Pct : Integer;
    NewX, NewY, NewXS, NewYS, NewHole, NewRot, SlotLen, HoleRot : Double;
    NewLayer : TLayer;
    IsTH, WillBeTH, AnyChange, WantPlated : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    EditsStr := ExtractJsonValue(Params, 'edits');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'footprint_name is required. Editing whichever footprint the '
            + 'editor happens to show is the mistake this avoids.');
        Exit;
    End;
    If EditsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'edits is required');
        Exit;
    End;

    PcbLib := FocusPcbLibForEdit(LibPath, ErrCode, ErrMsg);
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, ErrMsg);
        Exit;
    End;
    Board := PcbLib.Board;
    Target := FindPcbLibFootprint(PcbLib, FpWanted);
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'Footprint not found in ' + LibPath + ': ' + FpWanted
            + '. lib_get_footprints lists what it holds.');
        Exit;
    End;
    { MAKE IT CURRENT, verified, BEFORE THE ORIGIN IS READ. x and y are     }
    { relative to Footprint.X/Y, and that is only the true reference for a  }
    { footprint the library has made current: lib_get_pad_geometry reads it }
    { off a non-current footprint and, for IPC generated ones, reported pin }
    { 1 at the origin while the saved file had the pads centred (owner's    }
    { fpcheck.py, 2026-10-02). Making a component current also relocates   }
    { it (Lib_SetDesignator), so the read below comes after. Same write and }
    { read-back as ResolveLibFootprint and Lib_SetDesignator.              }
    If PcbLib.CurrentComponent <> Target Then
    Begin
        Try PcbLib.CurrentComponent := Target; Except End;
        If PcbLib.CurrentComponent <> Target Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SELECT_FAILED',
                'Could not make footprint "' + FpWanted + '" current, so '
                + 'its origin cannot be trusted as the frame for x and y. '
                + 'Nothing was changed.');
            Exit;
        End;
    End;
    XOrg := FootprintOriginX(Target);
    YOrg := FootprintOriginY(Target);

    OpCount := 0;
    Applied := 0;
    FailedCount := 0;
    AnyChange := False;
    ResultsJson := '';

    SnapAddrs := TStringList.Create;
    SnapNames := TStringList.Create;
    UsedAddrs := TStringList.Create;
    Try
        { The snapshot: address and designator of every pad, once each. }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadAddr := '';
                Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                If (PadAddr <> '') And (SnapAddrs.IndexOf(PadAddr) < 0) Then
                Begin
                    TxtName := '';
                    Try TxtName := Pad.Name; Except End;
                    SnapAddrs.Add(PadAddr);
                    SnapNames.Add(TxtName);
                End;
                Pad := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        Remaining := EditsStr;
        PCBServer.PreProcess;
        Try
            While True Do
            Begin
                Op := NextBatchOp(Remaining);
                If Op = '' Then Break;
                OpCount := OpCount + 1;

                Desig := GetBatchField(Op, 'designator');
                IdxStr := Trim(GetBatchField(Op, 'index'));
                XStr := Trim(GetBatchField(Op, 'x'));
                YStr := Trim(GetBatchField(Op, 'y'));
                XSStr := Trim(GetBatchField(Op, 'x_size'));
                YSStr := Trim(GetBatchField(Op, 'y_size'));
                HoleStr := Trim(GetBatchField(Op, 'hole_size'));
                RotStr := Trim(GetBatchField(Op, 'rotation'));
                ShapeStr := Trim(GetBatchField(Op, 'shape'));
                CRStr := Trim(GetBatchField(Op, 'corner_radius'));
                PlatedStr := LowerCase(Trim(GetBatchField(Op, 'plated')));
                HoleTypeStr := LowerCase(Trim(GetBatchField(Op, 'hole_type')));
                SlotStr := Trim(GetBatchField(Op, 'slot_length'));
                HoleRotStr := Trim(GetBatchField(Op, 'hole_rotation'));
                MaskStr := LowerCase(Trim(GetBatchField(Op, 'mask_expansion')));
                PasteStr := LowerCase(Trim(GetBatchField(Op, 'paste_expansion')));
                LayerStr := Trim(GetBatchField(Op, 'layer'));
                NewName := GetBatchField(Op, 'new_designator');
                ClearStr := LowerCase(Trim(GetBatchField(Op, 'clear_designator')));

                ErrCode := '';
                ErrMsg := '';
                ItemJson := '';
                ChangedJson := '';
                MismatchJson := '';
                WarnJson := '';
                ShapeHow := '';
                Canon := '';
                WantIdx := -1;
                ResIdx := -1;
                Matches := 0;
                Hit := -1;
                TargetAddr := '';
                NewLayer := eNoLayer;
                Pct := 25;
                Pad := Nil;

                { ---- 1. validate every field; nothing is touched yet ---- }
                BadField := '';
                If (XStr <> '') And (Not IsFloatStr(XStr)) Then BadField := 'x';
                If (YStr <> '') And (Not IsFloatStr(YStr)) Then BadField := 'y';
                If (XSStr <> '') And (Not IsFloatStr(XSStr)) Then BadField := 'x_size';
                If (YSStr <> '') And (Not IsFloatStr(YSStr)) Then BadField := 'y_size';
                If (HoleStr <> '') And (Not IsFloatStr(HoleStr)) Then BadField := 'hole_size';
                If (RotStr <> '') And (Not IsFloatStr(RotStr)) Then BadField := 'rotation';
                If (SlotStr <> '') And (Not IsFloatStr(SlotStr)) Then BadField := 'slot_length';
                If (HoleRotStr <> '') And (Not IsFloatStr(HoleRotStr)) Then BadField := 'hole_rotation';
                If (CRStr <> '') And (Not IsIntStr(CRStr)) Then BadField := 'corner_radius';
                If (MaskStr <> '') And (MaskStr <> 'rule') And (Not IsFloatStr(MaskStr)) Then
                    BadField := 'mask_expansion';
                If (PasteStr <> '') And (PasteStr <> 'rule') And (Not IsFloatStr(PasteStr)) Then
                    BadField := 'paste_expansion';
                If BadField <> '' Then
                Begin
                    ErrCode := 'BAD_NUMBER';
                    ErrMsg := BadField + ' is not a number';
                End;

                NewX := StrToFloatDef(XStr, 0);
                NewY := StrToFloatDef(YStr, 0);
                NewXS := StrToFloatDef(XSStr, 0);
                NewYS := StrToFloatDef(YSStr, 0);
                NewHole := StrToFloatDef(HoleStr, 0);
                NewRot := StrToFloatDef(RotStr, 0);
                SlotLen := StrToFloatDef(SlotStr, 0);
                HoleRot := StrToFloatDef(HoleRotStr, 0);
                If CRStr <> '' Then Pct := StrToIntDef(CRStr, 25);

                If (ErrCode = '') And (((XSStr <> '') And (NewXS <= 0))
                    Or ((YSStr <> '') And (NewYS <= 0))) Then
                Begin
                    ErrCode := 'BAD_SIZE';
                    ErrMsg := 'x_size and y_size must be greater than zero';
                End;
                If (ErrCode = '') And (HoleStr <> '') And (NewHole < 0) Then
                Begin
                    ErrCode := 'BAD_SIZE';
                    ErrMsg := 'hole_size cannot be negative (0 makes the pad SMD)';
                End;
                If (ErrCode = '') And (SlotStr <> '') And (SlotLen <= 0) Then
                Begin
                    ErrCode := 'BAD_SIZE';
                    ErrMsg := 'slot_length must be greater than zero';
                End;
                If (ErrCode = '') And ((Pct < 0) Or (Pct > 100)) Then
                Begin
                    ErrCode := 'BAD_CORNER_RADIUS';
                    ErrMsg := 'corner_radius is a percentage, 0 to 100';
                End;
                If (ErrCode = '') And (ShapeStr <> '') Then
                Begin
                    Canon := CanonicalPadShape(ShapeStr);
                    If Canon = '' Then
                    Begin
                        ErrCode := 'BAD_SHAPE';
                        ErrMsg := 'unknown shape "' + ShapeStr
                            + '": use round, rectangular, octagonal or roundrect';
                    End;
                End;
                If (ErrCode = '') And (HoleTypeStr <> '') And (HoleTypeStr <> 'round')
                    And (HoleTypeStr <> 'square') And (HoleTypeStr <> 'slot') Then
                Begin
                    ErrCode := 'BAD_HOLE_TYPE';
                    ErrMsg := 'hole_type must be round, square or slot';
                End;
                If (ErrCode = '') And (PlatedStr <> '') And (PlatedStr <> 'true')
                    And (PlatedStr <> 'false') Then
                Begin
                    ErrCode := 'BAD_PLATED';
                    ErrMsg := 'plated must be true or false';
                End;
                If (ErrCode = '') And (LayerStr <> '') Then
                Begin
                    NewLayer := ResolveLayerId(Board, LayerStr);
                    If (NewLayer = eNoLayer) Or (NewLayer = eMultiLayer) Then
                    Begin
                        ErrCode := 'UNKNOWN_LAYER';
                        ErrMsg := 'layer "' + LayerStr + '" is not a single layer of '
                            + 'this library. ' + BoardLayerNamesHint(Board);
                    End;
                End;
                If (ErrCode = '') And (ClearStr = 'true') And (NewName <> '') Then
                Begin
                    ErrCode := 'BAD_DESIGNATOR';
                    ErrMsg := 'new_designator and clear_designator are exclusive';
                End;
                If (ErrCode = '') And (IdxStr <> '') Then
                Begin
                    If IsIntStr(IdxStr) Then WantIdx := StrToIntDef(IdxStr, -1);
                    If WantIdx < 0 Then
                    Begin
                        ErrCode := 'BAD_INDEX';
                        ErrMsg := 'index must be a whole number, 0 or more';
                    End;
                End;

                { ---- 2. resolve the target from the snapshot ---- }
                If ErrCode = '' Then
                Begin
                    For I := 0 To SnapNames.Count - 1 Do
                    Begin
                        If SnapNames[I] = Desig Then
                        Begin
                            If (Hit < 0) And ((WantIdx < 0) Or (Matches = WantIdx)) Then
                            Begin
                                Hit := I;
                                ResIdx := Matches;
                            End;
                            Matches := Matches + 1;
                        End;
                    End;
                    If Matches = 0 Then
                    Begin
                        ErrCode := 'PAD_NOT_FOUND';
                        ErrMsg := 'no pad named "' + Desig + '" in ' + FpWanted;
                    End
                    Else If (WantIdx < 0) And (Matches > 1) Then
                    Begin
                        ErrCode := 'AMBIGUOUS';
                        ErrMsg := IntToStr(Matches) + ' pads are named "' + Desig
                            + '". Pass index (0 to ' + IntToStr(Matches - 1)
                            + ', in lib_get_pad_geometry order) to pick one.';
                    End
                    Else If Hit < 0 Then
                    Begin
                        ErrCode := 'PAD_NOT_FOUND';
                        ErrMsg := 'index ' + IntToStr(WantIdx) + ' asked for, but only '
                            + IntToStr(Matches) + ' pad(s) are named "' + Desig + '"';
                    End;
                End;
                If ErrCode = '' Then
                Begin
                    TargetAddr := SnapAddrs[Hit];
                    If UsedAddrs.IndexOf(TargetAddr) >= 0 Then
                    Begin
                        ErrCode := 'DUPLICATE_TARGET';
                        ErrMsg := 'this pad is already edited earlier in the same '
                            + 'call; merge the two edits into one';
                    End
                    Else
                        UsedAddrs.Add(TargetAddr);
                End;

                { ---- 3. reach the pad, apply, read back ---- }
                If ErrCode = '' Then
                Begin
                    GrpIter := Target.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Pad := GrpIter.FirstPCBObject;
                        While Pad <> Nil Do
                        Begin
                            PadAddr := '';
                            Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                            If PadAddr = TargetAddr Then Break;
                            Pad := GrpIter.NextPCBObject;
                        End;

                        If Pad = Nil Then
                        Begin
                            ErrCode := 'PAD_NOT_FOUND';
                            ErrMsg := 'the pad resolved at the start of the call '
                                + 'was no longer in the footprint';
                        End
                        Else
                        Begin
                            IsTH := (Pad.Layer = eMultiLayer);
                            WillBeTH := IsTH;
                            If HoleStr <> '' Then WillBeTH := (NewHole > 0);
                            If (LayerStr <> '') And WillBeTH Then
                            Begin
                                ErrCode := 'LAYER_ON_DRILLED_PAD';
                                ErrMsg := 'layer applies to SMD pads only; a drilled '
                                    + 'pad is MultiLayer. Pass hole_size=0 with it to '
                                    + 'make the pad SMD.';
                            End;

                            If ErrCode = '' Then
                            Begin
                                Try
                                    Try
                                        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
                                            c_Broadcast, PCBM_BeginModify, c_NoEventData);
                                    Except End;
                                    Try
                                        { a. Drill and layer. }
                                        If HoleStr <> '' Then
                                        Begin
                                            Pad.HoleSize := MilsToCoordF(NewHole);
                                            AddChangedField(ChangedJson, 'hole_size');
                                            If (NewHole > 0) And (Not IsTH) Then
                                            Begin
                                                Pad.Layer := eMultiLayer;
                                                AddChangedField(ChangedJson, 'layer');
                                            End
                                            Else If (NewHole <= 0) And IsTH And (LayerStr = '') Then
                                            Begin
                                                Pad.Layer := eTopLayer;
                                                AddChangedField(ChangedJson, 'layer');
                                            End;
                                        End;
                                        If (LayerStr <> '') And (Not WillBeTH) Then
                                        Begin
                                            Pad.Layer := NewLayer;
                                            AddChangedField(ChangedJson, 'layer');
                                        End;

                                        { b. Position and rotation. }
                                        If XStr <> '' Then
                                        Begin
                                            Pad.X := XOrg + MilsToCoordF(NewX);
                                            AddChangedField(ChangedJson, 'x');
                                        End;
                                        If YStr <> '' Then
                                        Begin
                                            Pad.Y := YOrg + MilsToCoordF(NewY);
                                            AddChangedField(ChangedJson, 'y');
                                        End;
                                        If RotStr <> '' Then
                                        Begin
                                            Pad.Rotation := NewRot;
                                            AddChangedField(ChangedJson, 'rotation');
                                        End;

                                        { c. Size: every layer in a simple stack. }
                                        If XSStr <> '' Then
                                        Begin
                                            Pad.TopXSize := MilsToCoordF(NewXS);
                                            AddChangedField(ChangedJson, 'x_size');
                                        End;
                                        If YSStr <> '' Then
                                        Begin
                                            Pad.TopYSize := MilsToCoordF(NewYS);
                                            AddChangedField(ChangedJson, 'y_size');
                                        End;

                                        { d. Shape. No corner_radius given: a pad     }
                                        {    that is already roundrect keeps its own. }
                                        If Canon <> '' Then
                                        Begin
                                            If CRStr = '' Then
                                            Begin
                                                Try
                                                    If Pad.TopShape = eRoundedRectangular Then
                                                        Pct := Pad.StackCRPctOnLayer[eTopLayer];
                                                Except End;
                                            End;
                                            ShapeHow := SetPadCopperShape(Board, Pad, Canon, Pct);
                                            AddChangedField(ChangedJson, 'shape');
                                            If (Canon = 'roundrect') And (CRStr <> '') Then
                                                AddChangedField(ChangedJson, 'corner_radius');
                                        End
                                        Else If CRStr <> '' Then
                                        Begin
                                            CurShape := '';
                                            Try CurShape := PadShapeWord(Pad.TopShape); Except End;
                                            If CurShape = 'roundrect' Then
                                            Begin
                                                ShapeHow := SetPadCopperShape(Board, Pad, CurShape, Pct);
                                                AddChangedField(ChangedJson, 'corner_radius');
                                            End
                                            Else
                                                AddChangedField(WarnJson,
                                                    'corner_radius ignored: the pad is ' + CurShape
                                                    + ', not roundrect. Pass shape=roundrect with it.');
                                        End;

                                        { e. Hole form and plating. }
                                        If HoleTypeStr <> '' Then
                                        Begin
                                            If HoleTypeStr = 'slot' Then Pad.HoleType := eSlotHole
                                            Else If HoleTypeStr = 'square' Then Pad.HoleType := eSquareHole
                                            Else Pad.HoleType := eRoundHole;
                                            AddChangedField(ChangedJson, 'hole_type');
                                        End;
                                        If SlotStr <> '' Then
                                        Begin
                                            Pad.HoleWidth := MilsToCoordF(SlotLen);
                                            AddChangedField(ChangedJson, 'slot_length');
                                        End;
                                        If HoleRotStr <> '' Then
                                        Begin
                                            Pad.HoleRotation := HoleRot;
                                            AddChangedField(ChangedJson, 'hole_rotation');
                                        End;
                                        If PlatedStr <> '' Then
                                        Begin
                                            WantPlated := (PlatedStr = 'true');
                                            Pad.Plated := WantPlated;
                                            AddChangedField(ChangedJson, 'plated');
                                        End;

                                        { f. Mask and paste: the PCB_MakePasteGrid     }
                                        {    cache pattern, read FRESH so nothing set  }
                                        {    above is written back stale. 'rule' is    }
                                        {    eCacheInvalid, the value the paste-       }
                                        {    exclusion restore already writes.         }
                                        If (MaskStr <> '') Or (PasteStr <> '') Then
                                        Begin
                                            Cache := Pad.GetState_Cache;
                                            If MaskStr = 'rule' Then
                                                Cache.SolderMaskExpansionValid := eCacheInvalid
                                            Else If MaskStr <> '' Then
                                            Begin
                                                Cache.SolderMaskExpansionValid := eCacheManual;
                                                Cache.SolderMaskExpansion :=
                                                    MilsToCoordF(StrToFloatDef(MaskStr, 0));
                                            End;
                                            If PasteStr = 'rule' Then
                                                Cache.PasteMaskExpansionValid := eCacheInvalid
                                            Else If PasteStr <> '' Then
                                            Begin
                                                Cache.PasteMaskExpansionValid := eCacheManual;
                                                Cache.PasteMaskExpansion :=
                                                    MilsToCoordF(StrToFloatDef(PasteStr, 0));
                                            End;
                                            Pad.SetState_Cache := Cache;
                                            If MaskStr <> '' Then AddChangedField(ChangedJson, 'mask_expansion');
                                            If PasteStr <> '' Then AddChangedField(ChangedJson, 'paste_expansion');
                                        End;

                                        { g. The designator, last. }
                                        If ClearStr = 'true' Then
                                        Begin
                                            TxtName := '';
                                            Pad.Name := TxtName;
                                            AddChangedField(ChangedJson, 'designator');
                                        End
                                        Else If NewName <> '' Then
                                        Begin
                                            Pad.Name := NewName;
                                            AddChangedField(ChangedJson, 'designator');
                                        End;

                                        Try Pad.GraphicallyInvalidate; Except End;
                                    Finally
                                        Try
                                            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
                                                c_Broadcast, PCBM_EndModify, c_NoEventData);
                                        Except End;
                                    End;
                                Except
                                    ErrCode := 'APPLY_FAILED';
                                    ErrMsg := 'Altium raised part way through the edit. '
                                        + 'Fields already written stay written; see changed '
                                        + 'and the pad read-back.';
                                End;
                                If ChangedJson <> '' Then AnyChange := True;

                                { ---- read back every requested field ---- }
                                Try
                                    If (HoleStr <> '') And (Pad.HoleSize <> MilsToCoordF(NewHole)) Then
                                        AddChangedField(MismatchJson, 'hole_size');
                                    If (HoleStr <> '') And (NewHole > 0) And (Pad.Layer <> eMultiLayer) Then
                                        AddChangedField(MismatchJson, 'layer');
                                    If (HoleStr <> '') And (NewHole <= 0) And (Pad.Layer = eMultiLayer) Then
                                        AddChangedField(MismatchJson, 'layer');
                                    If (LayerStr <> '') And (Not WillBeTH) And (Pad.Layer <> NewLayer) Then
                                        AddChangedField(MismatchJson, 'layer');
                                    If (XStr <> '') And ((Pad.X - XOrg) <> MilsToCoordF(NewX)) Then
                                        AddChangedField(MismatchJson, 'x');
                                    If (YStr <> '') And ((Pad.Y - YOrg) <> MilsToCoordF(NewY)) Then
                                        AddChangedField(MismatchJson, 'y');
                                    If (RotStr <> '') And
                                       (Abs(NormDeg(Pad.Rotation) - NormDeg(NewRot)) > 0.001) Then
                                        AddChangedField(MismatchJson, 'rotation');
                                    If (XSStr <> '') And (Pad.TopXSize <> MilsToCoordF(NewXS)) Then
                                        AddChangedField(MismatchJson, 'x_size');
                                    If (YSStr <> '') And (Pad.TopYSize <> MilsToCoordF(NewYS)) Then
                                        AddChangedField(MismatchJson, 'y_size');
                                    If ShapeHow = 'unconfirmed' Then
                                        AddChangedField(MismatchJson, 'shape');
                                    If Pos('top_only', ShapeHow) = 1 Then
                                        AddChangedField(WarnJson,
                                            'TopShape took but the bottom stack entry did '
                                            + 'not read back the same; pad.mode and '
                                            + 'pad.bottom say whether that matters');
                                    If (HoleTypeStr = 'slot') And (Pad.HoleType <> eSlotHole) Then
                                        AddChangedField(MismatchJson, 'hole_type');
                                    If (HoleTypeStr = 'square') And (Pad.HoleType <> eSquareHole) Then
                                        AddChangedField(MismatchJson, 'hole_type');
                                    If (HoleTypeStr = 'round') And (Pad.HoleType <> eRoundHole) Then
                                        AddChangedField(MismatchJson, 'hole_type');
                                    If (SlotStr <> '') And (Pad.HoleWidth <> MilsToCoordF(SlotLen)) Then
                                        AddChangedField(MismatchJson, 'slot_length');
                                    If (HoleRotStr <> '') And
                                       (Abs(NormDeg(Pad.HoleRotation) - NormDeg(HoleRot)) > 0.001) Then
                                        AddChangedField(MismatchJson, 'hole_rotation');
                                    If (PlatedStr <> '') And (Pad.Plated <> (PlatedStr = 'true')) Then
                                        AddChangedField(MismatchJson, 'plated');
                                    If (MaskStr <> '') Or (PasteStr <> '') Then
                                    Begin
                                        Cache := Pad.GetState_Cache;
                                        If (MaskStr = 'rule') And
                                           (Cache.SolderMaskExpansionValid = eCacheManual) Then
                                            AddChangedField(MismatchJson, 'mask_expansion');
                                        If (MaskStr <> '') And (MaskStr <> 'rule') And
                                           ((Cache.SolderMaskExpansionValid <> eCacheManual) Or
                                            (Cache.SolderMaskExpansion <>
                                             MilsToCoordF(StrToFloatDef(MaskStr, 0)))) Then
                                            AddChangedField(MismatchJson, 'mask_expansion');
                                        If (PasteStr = 'rule') And
                                           (Cache.PasteMaskExpansionValid = eCacheManual) Then
                                            AddChangedField(MismatchJson, 'paste_expansion');
                                        If (PasteStr <> '') And (PasteStr <> 'rule') And
                                           ((Cache.PasteMaskExpansionValid <> eCacheManual) Or
                                            (Cache.PasteMaskExpansion <>
                                             MilsToCoordF(StrToFloatDef(PasteStr, 0)))) Then
                                            AddChangedField(MismatchJson, 'paste_expansion');
                                    End;
                                    If (ClearStr = 'true') And (Pad.Name <> '') Then
                                        AddChangedField(MismatchJson, 'designator');
                                    If (NewName <> '') And (Pad.Name <> NewName) Then
                                        AddChangedField(MismatchJson, 'designator');
                                    If ((XSStr <> '') Or (YSStr <> '')) And (Pad.Layer = eMultiLayer)
                                       And (Pad.Mode <> ePadMode_Simple) Then
                                        AddChangedField(WarnJson,
                                            'the pad stack is not simple, so x_size/y_size set the '
                                            + 'TOP layer only; compare pad.bottom and pad.mid');
                                Except
                                    AddChangedField(WarnJson, 'the read-back raised part way through');
                                End;
                            End;

                            { The pad was reached: report its state whatever happened. }
                            ItemJson := JsonStr('designator', Desig)
                                + ',' + JsonInt('index', ResIdx)
                                + ',' + JsonInt('matches', Matches)
                                + ',' + JsonBool('ok', (ErrCode = '') And (MismatchJson = ''))
                                + ',' + JsonRaw('changed', JsonArr(ChangedJson))
                                + ',' + JsonRaw('mismatched', JsonArr(MismatchJson))
                                + ',' + JsonRaw('warnings', JsonArr(WarnJson));
                            If ShapeHow <> '' Then
                                ItemJson := ItemJson + ',' + JsonStr('shape_status', ShapeHow);
                            If ErrCode <> '' Then
                                ItemJson := ItemJson + ',' + JsonStr('error', ErrCode)
                                    + ',' + JsonStr('message', ErrMsg);
                            ItemJson := ItemJson + ',' + JsonRaw('pad', PadStateJson(Pad, XOrg, YOrg));
                        End;
                    Finally
                        Target.GroupIterator_Destroy(GrpIter);
                    End;
                End;

                If ItemJson = '' Then
                    ItemJson := JsonStr('designator', Desig)
                        + ',' + JsonStr('index', IdxStr)
                        + ',' + JsonInt('matches', Matches)
                        + ',' + JsonBool('ok', False)
                        + ',' + JsonStr('error', ErrCode)
                        + ',' + JsonStr('message', ErrMsg);

                If (ErrCode = '') And (MismatchJson = '') Then Inc(Applied)
                Else Inc(FailedCount);
                If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
                ResultsJson := ResultsJson + JsonObj(ItemJson);
            End;
        Finally
            PCBServer.PostProcess;
        End;
    Finally
        Try SnapAddrs.Free; Except End;
        Try SnapNames.Free; Except End;
        Try UsedAddrs.Free; Except End;
    End;

    If AnyChange Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(LibPath);
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('library', LibPath) + ','
        + JsonStr('footprint', FpWanted) + ','
        + JsonInt('applied', Applied) + ','
        + JsonInt('failed', FailedCount) + ','
        + JsonInt('total', OpCount) + ','
        + JsonRaw('results', JsonArr(ResultsJson)) + ','
        + JsonBool('written_to_disk', False) + ','
        + JsonBool('pending_save', AnyChange) + ','
        + JsonStr('note', 'Changed in memory and the library marked dirty. '
            + 'lib_save_library writes it; lib_read_pcblib_file(library_path, '
            + 'footprint_name) after the save reads the pads from the saved '
            + 'file, with no Altium involved, and is the proof it persisted.')));
End;

{..............................................................................}
{ Lib_DeleteFootprintPads - remove named pads from one PcbLib footprint.       }
{                                                                              }
{ Params: footprint_name (required), library_path (optional), confirm=true    }
{ (required), pads (batch). Per selector: designator (required; '' selects    }
{ unnamed pads), and either index (0-based, as lib_modify_footprint_pads) or   }
{ all=true (every pad with that designator). A designator carried by more     }
{ than one pad with neither is REFUSED.                                        }
{                                                                              }
{ ALL OR NOTHING. Every selector must resolve before the first pad goes; one   }
{ that does not refuses the whole call, because a half-applied delete is      }
{ harder to reason about than one that did nothing (the obj_batch_delete      }
{ guard's rule).                                                               }
{                                                                              }
{ The removal is Lib_DeleteFootprintPrimitives's, call for call: walk with a   }
{ base IPCB_Primitive, stop on the match, Target.RemovePCBObject while the     }
{ iterator is still open, then destroy it. A pad added in this session is     }
{ listed twice until a reload, so each address gets up to two passes and the  }
{ reply says how many removals each took. Whether the removal reaches disk is  }
{ checked the only way it can be: save, reload, read (see the reply's note).  }
{                                                                              }
{ FOOTPRINT ONLY BY DEFAULT. remove_from_board=true adds the unmeasured        }
{ library-board half under the shared rule (REMOVING PRIMITIVES FROM A PCBLIB  }
{ FOOTPRINT, beside RemoveLibTextByAddress): the footprint is made current,    }
{ verified, before the snapshot, and the board is swept after the read-back,  }
{ never for a pad the footprint still lists.                                  }
{                                                                              }
{ MEASURED LIVE 2026-10-04 (AD26, bridge 2026.10.04.9, carrier.PcbLib,        }
{ throwaway footprint ZZ_BRIDGE_TEST): A PAD ADDED IN THIS SESSION CAME BACK.  }
{   1. lib_add_footprint_pads added pads 1, 2, '' and 3; other edits followed. }
{   2. delete pad 3: removed 1, pads_before 4, pads_after 3, still_listed 0,   }
{      double_listed 0. lib_save_library wrote the file. The saved file STILL }
{      HELD pad 3.                                                             }
{   3. delete again with remove_from_board=true: pads_before 4 (the save had  }
{      put pad 3 back in the footprint), removed 1, and the board half found  }
{      it (listed_before 1), removed it (remove_calls 2) and STILL LISTED it  }
{      (still_listed 1). The save wrote pad 3 again, now FIRST among the pads. }
{   4. lib_reload_library, then the same footprint-only delete: removed 1,    }
{      and after lib_save_library the file NO LONGER held pad 3. That save    }
{      reported was_modified FALSE: the delete had not left the document      }
{      reading modified (the save writes regardless).                         }
{ So a removal of a pad LOADED FROM DISK persists, and a removal of one this   }
{ session created does not, even across a save: the in-memory object is still }
{ the session-created one, registered with the footprint AND the library      }
{ board (Lib_AddFootprintPads, RegisterLibPrimitive), and the board's copy     }
{ survives both RemovePCBObject routes. No proven member removes that        }
{ registration (Board.RemovePCBObject was tried and is the measured failure). }
{ It is not known whether it matters that the footprint was the CURRENT one   }
{ in steps 2-3 (the add made it current) and probably not in step 4 (a reload }
{ opens whichever footprint Altium chooses).                                  }
{                                                                              }
{ THE FIX IS IN THE TOOL, not here: lib_delete_footprint_pads saves and       }
{ reloads the library FIRST (reload_first, default on), so every pad it       }
{ removes is a disk-loaded one, the case step 4 proved, and then asks for      }
{ park_current: a footprint that is current after the reopen is parked, by     }
{ making another footprint current (LibParkCurrentElsewhere), so the removal   }
{ also runs on a footprint that is NOT current, step 4's other condition. A    }
{ review (2026-10-04) pointed out that the evidence fits "the save rebuilds    }
{ the CURRENT footprint from the board's copy" as well as "session-created",   }
{ and reload_first alone fixes only the second. The tool then saves again and  }
{ reads the FILE (verify_on_disk), which settles it whichever theory holds.    }
{ This handler reports what one live call needs to tell the cases apart:       }
{   target_was_current   was the footprint current when the call arrived;      }
{   parked_elsewhere     park_current switched to another footprint;           }
{   target_current_during_removal  whether it was current while removed;       }
{   still_listed         removed pads the FOOTPRINT iterator still lists;    }
{   board_still_listed   removed pads the LIBRARY BOARD iterator still lists }
{                        (LibBoardStillListsCount, read-only), counted only    }
{                        while the footprint is current, null otherwise;       }
{   marked_modified      the document reads modified after the delete. It    }
{                        is marked through the path given AND through        }
{                        PcbLib.Board.FileName, the path every add handler   }
{                        marks (step 4 found the first alone not enough).    }
{ The persistence proof is the saved file: lib_save_library, then             }
{ lib_read_pcblib_file, which reads the .PcbLib with no Altium involved.      }
{..............................................................................}
Function Lib_DeleteFootprintPads(Params : String; RequestId : String) : String;
Var
    LibPath, FpWanted, PadsStr, Remaining, Op, ErrCode, ErrMsg, ConfirmStr : String;
    Desig, IdxStr, AllStr, PadAddr, TxtName, ItemJson, ResultsJson, RefusedJson : String;
    Entries, StillAddrs, BoardJson : String;
    PcbLib : IPCB_Library;
    Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    SnapAddrs, SnapNames, Doomed, AfterAddrs : TStringList;
    I, J, WantIdx, Matches, Picked, OpCount, Removed, DoubleListed : Integer;
    Removals, StillListed, PadsBefore, PadsAfter, PadOid, BoardRemoved : Integer;
    BoardStill : Integer;
    BoardStillJson : String;
    Found, TakeAll, FromBoard, MadeCurrent, TargetWasCurrent, MarkedModified : Boolean;
    MarkJson : String;
    ParkWanted, Parked, CurrentDuring : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    PadsStr := ExtractJsonValue(Params, 'pads');
    ConfirmStr := ExtractJsonValue(Params, 'confirm');
    FromBoard := (LowerCase(Trim(ExtractJsonValue(Params, 'remove_from_board'))) = 'true');
    ParkWanted := (LowerCase(Trim(ExtractJsonValue(Params, 'park_current'))) = 'true');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'footprint_name is required');
        Exit;
    End;
    If PadsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pads is required');
        Exit;
    End;
    If ConfirmStr <> 'true' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CONFIRM_REQUIRED',
            'This removes pads from footprint "' + FpWanted + '", which changes '
            + 'its pinout. Read them with lib_get_pad_geometry, then pass '
            + 'confirm=true.');
        Exit;
    End;

    PcbLib := FocusPcbLibForEdit(LibPath, ErrCode, ErrMsg);
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, ErrMsg);
        Exit;
    End;
    Target := FindPcbLibFootprint(PcbLib, FpWanted);
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    { Current only for the board half, and before the snapshot below so the }
    { addresses it records are the ones the current footprint holds.        }
    MadeCurrent := False;
    If FromBoard Then
    Begin
        MadeCurrent := LibMakeCurrentVerified(PcbLib, Target);
        If Not MadeCurrent Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SELECT_FAILED',
                'Could not make footprint "' + FpWanted + '" current, which '
                + 'remove_from_board needs. Nothing was removed.');
            Exit;
        End;
    End;
    { Read, never written: whether the footprint is the current one while its }
    { pads are removed (see the measured block above).                       }
    TargetWasCurrent := False;
    Try TargetWasCurrent := (PcbLib.CurrentComponent = Target); Except TargetWasCurrent := False; End;
    { park_current (sent only after the tool's reload_first ran): remove from  }
    { a footprint that is NOT current, the condition of the delete that       }
    { persisted (LibParkCurrentElsewhere). Never with remove_from_board, whose }
    { board half needs the footprint current. Before the snapshot, so every   }
    { address recorded below is read with the footprint already parked.       }
    Parked := False;
    If ParkWanted And (Not FromBoard) And TargetWasCurrent Then
        Parked := LibParkCurrentElsewhere(PcbLib, Target);
    CurrentDuring := False;
    Try CurrentDuring := (PcbLib.CurrentComponent = Target); Except CurrentDuring := False; End;

    OpCount := 0;
    Removed := 0;
    DoubleListed := 0;
    StillListed := 0;
    PadsAfter := 0;
    BoardRemoved := 0;
    BoardStill := 0;
    MarkedModified := False;
    MarkJson := 'null';
    ResultsJson := '';
    RefusedJson := '';
    Entries := '';
    StillAddrs := '|';
    BoardJson := JsonObj(JsonBool('requested', False));

    SnapAddrs := TStringList.Create;
    SnapNames := TStringList.Create;
    Doomed := TStringList.Create;
    AfterAddrs := TStringList.Create;
    Try
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadAddr := '';
                Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                If (PadAddr <> '') And (SnapAddrs.IndexOf(PadAddr) < 0) Then
                Begin
                    TxtName := '';
                    Try TxtName := Pad.Name; Except End;
                    SnapAddrs.Add(PadAddr);
                    SnapNames.Add(TxtName);
                End;
                Pad := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;
        PadsBefore := SnapAddrs.Count;

        { Resolve every selector first. Nothing is removed in this loop. }
        Remaining := PadsStr;
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            Desig := GetBatchField(Op, 'designator');
            IdxStr := Trim(GetBatchField(Op, 'index'));
            AllStr := LowerCase(Trim(GetBatchField(Op, 'all')));
            TakeAll := (AllStr = 'true');
            WantIdx := -1;
            ErrMsg := '';
            If (IdxStr <> '') And TakeAll Then
                ErrMsg := 'index and all are exclusive'
            Else If IdxStr <> '' Then
            Begin
                If IsIntStr(IdxStr) Then WantIdx := StrToIntDef(IdxStr, -1);
                If WantIdx < 0 Then ErrMsg := 'index must be a whole number, 0 or more';
            End;

            Matches := 0;
            For I := 0 To SnapNames.Count - 1 Do
                If SnapNames[I] = Desig Then Matches := Matches + 1;

            If ErrMsg = '' Then
            Begin
                If Matches = 0 Then
                    ErrMsg := 'no pad named "' + Desig + '"'
                Else If (WantIdx < 0) And (Not TakeAll) And (Matches > 1) Then
                    ErrMsg := IntToStr(Matches) + ' pads are named "' + Desig
                        + '"; pass index (0 to ' + IntToStr(Matches - 1)
                        + ') or all=true'
                Else If WantIdx >= Matches Then
                    ErrMsg := 'index ' + IntToStr(WantIdx) + ' asked for, but only '
                        + IntToStr(Matches) + ' pad(s) are named "' + Desig + '"';
            End;

            If ErrMsg <> '' Then
            Begin
                AddFailReason(RefusedJson, Desig, ErrMsg);
                Continue;
            End;

            Picked := 0;
            J := 0;
            For I := 0 To SnapNames.Count - 1 Do
            Begin
                If SnapNames[I] = Desig Then
                Begin
                    If TakeAll Or (WantIdx < 0) Or (J = WantIdx) Then
                    Begin
                        If Doomed.IndexOf(SnapAddrs[I]) < 0 Then
                        Begin
                            Doomed.Add(SnapAddrs[I]);
                            Picked := Picked + 1;
                        End;
                    End;
                    J := J + 1;
                End;
            End;
            ItemJson := JsonStr('designator', Desig) + ','
                + JsonStr('index', IdxStr) + ','
                + JsonBool('all', TakeAll) + ','
                + JsonInt('matches', Matches) + ','
                + JsonInt('selected', Picked);
            If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
            ResultsJson := ResultsJson + JsonObj(ItemJson);
        End;

        If RefusedJson <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SELECTOR_UNRESOLVED',
                'Nothing was removed: some selectors did not resolve to exactly '
                + 'the pads meant. ' + '[' + RefusedJson + ']');
            Exit;
        End;

        PCBServer.PreProcess;
        Try
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_BeginModify, c_NoEventData);
            For I := 0 To Doomed.Count - 1 Do
            Begin
                Removals := 0;
                For J := 1 To 2 Do
                Begin
                    Found := False;
                    GrpIter := Target.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Prim := GrpIter.FirstPCBObject;
                        While Prim <> Nil Do
                        Begin
                            PadAddr := '';
                            Try PadAddr := IntToStr(Prim.I_ObjectAddress); Except End;
                            If PadAddr = Doomed[I] Then
                            Begin
                                Found := True;
                                Break;
                            End;
                            Prim := GrpIter.NextPCBObject;
                        End;
                        { Removed while the iterator is OPEN: see the       }
                        { REMOVE WHILE THE ITERATOR IS STILL OPEN note in    }
                        { Lib_DeleteFootprintPrimitives.                     }
                        If Found And (Prim <> Nil) Then
                        Begin
                            PadOid := -1;
                            Try PadOid := Prim.ObjectId; Except PadOid := -1; End;
                            Try
                                Target.RemovePCBObject(Prim);
                                Removals := Removals + 1;
                                Entries := LibAddRemovalEntry(Entries, PadOid, Doomed[I]);
                            Except
                                Found := False;
                            End;
                        End;
                    Finally
                        Target.GroupIterator_Destroy(GrpIter);
                    End;
                    If Not Found Then Break;
                End;
                If Removals > 0 Then Removed := Removed + 1;
                If Removals > 1 Then DoubleListed := DoubleListed + 1;
            End;
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_EndModify, c_NoEventData);
        Finally
            PCBServer.PostProcess;
        End;

        { Read back: none of the removed addresses may still be listed. }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadAddr := '';
                Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                If (PadAddr <> '') And (AfterAddrs.IndexOf(PadAddr) < 0) Then
                Begin
                    AfterAddrs.Add(PadAddr);
                    If Doomed.IndexOf(PadAddr) >= 0 Then
                    Begin
                        StillListed := StillListed + 1;
                        StillAddrs := StillAddrs + PadAddr + '|';
                    End;
                End;
                Pad := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;
        PadsAfter := AfterAddrs.Count;
    Finally
        Try SnapAddrs.Free; Except End;
        Try SnapNames.Free; Except End;
        Try Doomed.Free; Except End;
        Try AfterAddrs.Free; Except End;
    End;

    { The opt-in board half, after the read-back (rule 4). }
    If FromBoard Then
    Begin
        PCBServer.PreProcess;
        Try
            BoardJson := LibBoardRemoveEntries(PcbLib.Board, Entries,
                StillAddrs, MadeCurrent, BoardRemoved);
        Finally
            PCBServer.PostProcess;
        End;
    End;

    { What the LIBRARY BOARD still holds of the removed pads, read-only, and }
    { only while the footprint is current: otherwise the board does not hold }
    { its primitives at all (rule 3), the count is 0 by construction, and    }
    { the reply says null instead of a 0 that proves nothing.                }
    BoardStillJson := 'null';
    If (Entries <> '') And CurrentDuring Then
    Begin
        BoardStill := LibBoardStillListsCount(PcbLib.Board, Entries);
        BoardStillJson := IntToStr(BoardStill);
    End
    Else If CurrentDuring Then
        BoardStillJson := '0';

    If (Removed > 0) Or (BoardRemoved > 0) Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        { Both paths are marked, as before: the board's own file name and   }
        { the caller's library_path. MarkedModified is what the marks READ  }
        { BACK, and mark reports the board's mark unless only the           }
        { library_path one took.                                            }
        MarkedModified := MarkDocDirty(PcbLib.Board.FileName);
        MarkJson := MarkOutcomeJson(0);
        If MarkDocDirty(LibPath) And (Not MarkedModified) Then
        Begin
            MarkedModified := True;
            MarkJson := MarkOutcomeJson(0);
        End;
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('library', LibPath) + ','
        + JsonStr('footprint', FpWanted) + ','
        + JsonInt('removed', Removed) + ','
        + JsonInt('selectors', OpCount) + ','
        + JsonRaw('results', JsonArr(ResultsJson)) + ','
        + JsonInt('pads_before', PadsBefore) + ','
        + JsonInt('pads_after', PadsAfter) + ','
        + JsonInt('still_listed', StillListed) + ','
        + JsonRaw('board_still_listed', BoardStillJson) + ','
        + JsonBool('target_was_current', TargetWasCurrent) + ','
        + JsonBool('parked_elsewhere', Parked) + ','
        + JsonBool('target_current_during_removal', CurrentDuring) + ','
        + JsonInt('double_listed', DoubleListed) + ','
        + JsonRaw('board_removal', BoardJson) + ','
        + JsonBool('marked_modified', MarkedModified) + ','
        + JsonRaw('mark', MarkJson) + ','
        + JsonBool('written_to_disk', False) + ','
        + JsonBool('pending_save', Removed > 0) + ','
        + JsonStr('note', 'Removed in memory and the library marked dirty. '
            + 'lib_save_library writes it. Proof it persisted: after the save, '
            + 'lib_read_pcblib_file(library_path, footprint_name) reads the '
            + 'saved .PcbLib with no Altium involved and must not list the '
            + 'pads. still_listed above must be 0; board_still_listed above 0 '
            + 'is a pad the library board still holds, which a save can write '
            + 'back (null: the footprint was not current, so the board does '
            + 'not hold its pads and there was nothing to count).')));
End;

{..............................................................................}
{ Lib_SetFootprintDescription - set Description on one or many footprints.    }
{                                                                              }
{ Params: library_path (optional), and EITHER footprint_name + description    }
{ (one footprint; an empty description clears it) OR edits_path (a file of    }
{ "name<TAB>description" lines in the system ANSI codepage, one per footprint, }
{ written by the Python tool the way lib_batch_rename writes its file).       }
{                                                                              }
{ The write is Lib_CreateFootprint's Footprint.Description, on an existing    }
{ footprint, inside the library walk the height sweep already writes in, and  }
{ bracketed with PCBM_BeginModify / PCBM_EndModify on the footprint as        }
{ Lib_DeleteFootprintPrimitives brackets its edits. Every write is READ BACK.  }
{..............................................................................}
Function Lib_SetFootprintDescription(Params : String; RequestId : String) : String;
Var
    LibPath, FpWanted, NewDesc, EditsPath, ErrCode, ErrMsg : String;
    Line, WantName, Want, OldDesc, GotDesc, FpName, Key : String;
    ResultsJson, MissingJson, MismatchJson : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    Edits, Names, Descs, Done : TStringList;
    I, Updated, Unchanged, Mismatched, Emitted, Missing : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    NewDesc := ExtractJsonValue(Params, 'description');
    EditsPath := ExtractJsonValue(Params, 'edits_path');

    If (FpWanted = '') And (EditsPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'give footprint_name (with description) or edits_path');
        Exit;
    End;
    If (FpWanted <> '') And (EditsPath <> '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'give footprint_name or edits_path, not both');
        Exit;
    End;
    If (EditsPath <> '') And (Not FileExists(EditsPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_EDITS',
            'Edit file not found: ' + EditsPath);
        Exit;
    End;

    PcbLib := FocusPcbLibForEdit(LibPath, ErrCode, ErrMsg);
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ErrCode, ErrMsg);
        Exit;
    End;

    Updated := 0;
    Unchanged := 0;
    Mismatched := 0;
    Missing := 0;
    Emitted := 0;
    ResultsJson := '';
    MissingJson := '';
    MismatchJson := '';

    Edits := TStringList.Create;
    Names := TStringList.Create;
    Descs := TStringList.Create;
    Done := TStringList.Create;
    Try
        If EditsPath <> '' Then
        Begin
            Edits.LoadFromFile(EditsPath);
            For I := 0 To Edits.Count - 1 Do
            Begin
                Line := Edits[I];
                If (Length(Line) > 0) And (Copy(Line, Length(Line), 1) = #13) Then
                    Line := Copy(Line, 1, Length(Line) - 1);
                If Line = '' Then Continue;
                WantName := SplitNextTab(Line);
                If WantName = '' Then Continue;
                Names.Add(WantName);
                Descs.Add(Line);
            End;
        End
        Else
        Begin
            Names.Add(FpWanted);
            Descs.Add(NewDesc);
        End;

        PCBServer.PreProcess;
        Try
            Iter := PcbLib.LibraryIterator_Create;
            Try
                Footprint := Iter.FirstPCBObject;
                While Footprint <> Nil Do
                Begin
                    FpName := '';
                    Try FpName := Footprint.Name; Except End;
                    For I := 0 To Names.Count - 1 Do
                    Begin
                        Key := UpperCase(Names[I]);
                        If Key <> UpperCase(FpName) Then Continue;
                        If Done.IndexOf(Key) >= 0 Then Continue;
                        Done.Add(Key);
                        Want := Descs[I];
                        OldDesc := '';
                        Try OldDesc := Footprint.Description; Except End;
                        GotDesc := OldDesc;
                        If OldDesc = Want Then
                            Unchanged := Unchanged + 1
                        Else
                        Begin
                            Try
                                PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                                    c_Broadcast, PCBM_BeginModify, c_NoEventData);
                            Except End;
                            Try Footprint.Description := Want; Except End;
                            Try
                                PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                                    c_Broadcast, PCBM_EndModify, c_NoEventData);
                            Except End;
                            GotDesc := '';
                            Try GotDesc := Footprint.Description; Except End;
                            If GotDesc = Want Then
                                Updated := Updated + 1
                            Else
                            Begin
                                Mismatched := Mismatched + 1;
                                AddFailReason(MismatchJson, FpName,
                                    'read back "' + GotDesc + '"');
                            End;
                        End;
                        If Emitted < 200 Then
                        Begin
                            If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
                            ResultsJson := ResultsJson + JsonObj(
                                JsonStr('footprint', FpName) + ','
                                + JsonStr('old', OldDesc) + ','
                                + JsonStr('read_back', GotDesc) + ','
                                + JsonBool('changed', OldDesc <> GotDesc));
                            Emitted := Emitted + 1;
                        End;
                        Break;
                    End;
                    Footprint := Iter.NextPCBObject;
                End;
            Finally
                PcbLib.LibraryIterator_Destroy(Iter);
            End;
        Finally
            PCBServer.PostProcess;
        End;

        For I := 0 To Names.Count - 1 Do
        Begin
            If Done.IndexOf(UpperCase(Names[I])) < 0 Then
            Begin
                Missing := Missing + 1;
                If Missing <= 100 Then
                Begin
                    If MissingJson <> '' Then MissingJson := MissingJson + ',';
                    MissingJson := MissingJson + '"' + EscapeJsonString(Names[I]) + '"';
                End;
            End;
        End;
    Finally
        Try Edits.Free; Except End;
        Try Names.Free; Except End;
        Try Descs.Free; Except End;
        Try Done.Free; Except End;
    End;

    If (Updated > 0) Or (Mismatched > 0) Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(LibPath);
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('library', LibPath) + ','
        + JsonInt('updated', Updated) + ','
        + JsonInt('unchanged', Unchanged) + ','
        + JsonInt('mismatched', Mismatched) + ','
        + JsonInt('missing_count', Missing) + ','
        + JsonRaw('missing', JsonArr(MissingJson)) + ','
        + JsonRaw('mismatches', JsonArr(MismatchJson)) + ','
        + JsonRaw('results', JsonArr(ResultsJson)) + ','
        + JsonBool('written_to_disk', False) + ','
        + JsonBool('pending_save', (Updated > 0) Or (Mismatched > 0))));
End;

Function Lib_SetMechLayers(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, OpsStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    OpsStr := ExtractJsonValue(Params, 'layers');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'library_path required, and no document is focused');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was changed: editing whichever '
            + 'library happens to be in front is how a sweep silently '
            + 'rewrites the same file twenty times.');
        Exit;
    End;

    { PcbLibMayBeCurrentAt (Main.pas): PCBServer is not named when the    }
    { answer is Nil anyway (a focused SchLib / SchDoc in a session with no }
    { PcbDoc or PcbLib open: the uncatchable modal), so the Nil reply      }
    { below comes without the dialog. A PcbLib open at LibPath always      }
    { passes, whatever a stale DM focus says.                              }
    PcbLib := Nil;
    If PcbLibMayBeCurrentAt(LibPath) Then PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Focused ' + LibPath + ' but it is not a PCB library. For a '
            + 'BOARD use pcb_set_mech_layers, which takes the open PcbDoc '
            + 'and reaches the same names, enables and kinds.');
        Exit;
    End;

    Board := Nil;
    Try Board := PcbLib.Board; Except Board := Nil; End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'The library has no board to carry a layer stack');
        Exit;
    End;

    Result := ApplyMechLayerOps(Board, OpsStr,
        ExtractJsonValue(Params, 'tidy_pairs') = 'true', LibPath, RequestId);
End;

{ Force a library_path onto a parameter object.                              }
{                                                                             }
{ ExtractJsonValue finds the FIRST occurrence of a key, so prepending is      }
{ enough to override one the caller supplied, and the original object is      }
{ left untouched rather than rewritten. A caller passing a single             }
{ library_path alongside a library list gets the list honoured, which is the  }
{ only reading that makes sense for a sweep.                                  }

Function MergeLibraryPath(Params : String; LibPath : String) : String;
Var
    Rest : String;
Begin
    Rest := Trim(Params);
    If (Rest = '') Or (Rest = '{}') Then
    Begin
        Result := '{"library_path":"' + EscapeJsonString(LibPath) + '"}';
        Exit;
    End;
    If Copy(Rest, 1, 1) = '{' Then
        Rest := Copy(Rest, 2, Length(Rest))
    Else
        Rest := Rest + '}';
    Result := '{"library_path":"' + EscapeJsonString(LibPath) + '",' + Rest;
End;

{ One field out of a handler's own response envelope.                        }

Function ResponseField(Response : String; Key : String) : String;
Begin
    Result := ExtractJsonValue(Response, Key);
End;

{ Which library actions only LOOK, and so must leave the active document  }
{ where they found it.                                                     }
{                                                                          }
{ Listed by name rather than inferred, because there is no property of a   }
{ handler this can read to tell reading from writing. The cost of the list }
{ going stale is a read that moves focus again, which is the bug it exists }
{ to prevent, so a new read-only handler belongs here on the day it is     }
{ written.                                                                 }
{                                                                          }
{ Writes are deliberately absent. Library authoring is a sequence of calls }
{ against a current component: lib_add_pins and the Lib_AddFootprint*      }
{ family read the focus the call before them left, so restoring it after a }
{ write would break the flow the bridge is built on. }
Function LibActionIsReadOnly(Action : String) : Boolean;
Begin
    Result := (Action = 'get_footprints')
           Or (Action = 'get_footprint_pads')
           Or (Action = 'get_library_geometry')
           Or (Action = 'get_component_details')
           Or (Action = 'get_pad_geometry')
           Or (Action = 'get_footprint_bodies')
           Or (Action = 'probe_footprint')
           Or (Action = 'probe_designator')
           Or (Action = 'audit_styles')
           Or (Action = 'search');
End;

Function HandleLibraryCommand(Action : String; Params : String; RequestId : String) : String;
Var
    SweepLibs, SweepAction, OnePath, OneParams, OneReply : String;
    ItemsJson, DataJson, ErrJson, OkStr : String;
    SavedFocus : String;
    BarPos, Succeeded, FailedCount : Integer;
    FirstItem : Boolean;
Begin
    { A sweep across several libraries in ONE call.                          }
    {                                                                         }
    { Sequentially from the caller's side, every library costs a round trip   }
    { plus the orchestration between them. The expensive part is opening and  }
    { saving each library, which this cannot avoid, but the round trips it    }
    { can: the whole sweep becomes one request.                               }
    {                                                                         }
    { Handled here rather than in its own function because DelphiScript has   }
    { no forward declarations, so a separate function could not call this     }
    { dispatcher. Direct recursion can.                                       }
    If Action = 'run_across' Then
    Begin
        SweepAction := Trim(ExtractJsonValue(Params, 'action'));
        SweepLibs := ExtractJsonValue(Params, 'libraries');

        If SweepAction = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
                'action required: the library command to run on each library');
            Exit;
        End;
        If SweepAction = 'run_across' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_ACTION',
                'run_across cannot sweep itself');
            Exit;
        End;
        If Trim(SweepLibs) = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
                'libraries required: full paths separated by "|". A sweep '
                + 'over no libraries would report a clean pass having done '
                + 'nothing.');
            Exit;
        End;

        ItemsJson := '';
        FirstItem := True;
        Succeeded := 0;
        FailedCount := 0;

        While Trim(SweepLibs) <> '' Do
        Begin
            BarPos := Pos('|', SweepLibs);
            If BarPos > 0 Then
            Begin
                OnePath := Trim(Copy(SweepLibs, 1, BarPos - 1));
                SweepLibs := Copy(SweepLibs, BarPos + 1, Length(SweepLibs));
            End
            Else
            Begin
                OnePath := Trim(SweepLibs);
                SweepLibs := '';
            End;
            If OnePath = '' Then Continue;

            OneParams := MergeLibraryPath(Params, OnePath);
            { A library that will not open must not abandon the rest of the  }
            { sweep. Its failure is recorded against its own name and the    }
            { loop carries on.                                                }
            OneReply := '';
            Try
                OneReply := HandleLibraryCommand(SweepAction, OneParams, RequestId);
            Except
                OneReply := '';
            End;

            If OneReply = '' Then
            Begin
                DataJson := 'null';
                ErrJson := '"the handler raised and returned nothing"';
                OkStr := 'false';
            End
            Else
            Begin
                DataJson := ResponseField(OneReply, 'data');
                If DataJson = '' Then DataJson := 'null';
                { The envelope's own success comes before any the payload    }
                { carries, and ExtractJsonValue takes the first match, so    }
                { this reads the envelope rather than a handler's own flag.  }
                If ResponseField(OneReply, 'success') = 'true' Then
                    OkStr := 'true'
                Else
                    OkStr := 'false';
                { Only on failure. "message" is a key inside the error       }
                { object, and a SUCCESSFUL payload carrying a field of that  }
                { name would otherwise be reported here as an error.         }
                ErrJson := 'null';
                If OkStr = 'false' Then
                Begin
                    ErrJson := ResponseField(OneReply, 'message');
                    If ErrJson = '' Then
                        ErrJson := 'null'
                    Else
                        ErrJson := '"' + EscapeJsonString(ErrJson) + '"';
                End;
            End;

            If OkStr = 'true' Then
                Succeeded := Succeeded + 1
            Else
                FailedCount := FailedCount + 1;

            If Not FirstItem Then ItemsJson := ItemsJson + ',';
            FirstItem := False;
            ItemsJson := ItemsJson
                + '{"library":"' + EscapeJsonString(OnePath) + '",'
                + '"success":' + OkStr + ','
                + '"data":' + DataJson + ','
                + '"error":' + ErrJson + '}';
        End;

        { Per library, never one aggregate flag. "17 of 20 worked" collapses }
        { into either a false clean or a false failure the moment it becomes }
        { a single boolean, and the caller cannot tell which library to fix. }
        Result := BuildSuccessResponse(RequestId,
            '{"action":"' + EscapeJsonString(SweepAction) + '",'
            + '"results":[' + ItemsJson + '],'
            + '"succeeded":' + IntToStr(Succeeded) + ','
            + '"failed":' + IntToStr(FailedCount) + ','
            + '"libraries":' + IntToStr(Succeeded + FailedCount) + '}');
        Exit;
    End;

    { Remember where the caller was looking, for the reads that are about to
      focus a library to answer. Captured HERE rather than inside each
      handler because they exit from several places apiece, and a restore
      that only runs on the success path leaves the focus moved on exactly
      the calls that already went wrong. }
    SavedFocus := '';
    If LibActionIsReadOnly(Action) Then SavedFocus := CurrentFocusedDocPath(0);

    Case Action Of
        'create_symbol':        Result := Lib_CreateSymbol(Params, RequestId);
        'add_pin':              Result := Lib_AddPin(Params, RequestId);
        'add_pins':             Result := Lib_AddPins(Params, RequestId);
        'add_symbol_text':      Result := Lib_AddSymbolText(Params, RequestId);
        'add_symbol_rectangle': Result := Lib_AddSymbolRectangle(Params, RequestId);
        'add_symbol_line':      Result := Lib_AddSymbolLine(Params, RequestId);
        'add_symbol_lines':     Result := Lib_AddSymbolLines(Params, RequestId);
        'create_footprint':     Result := Lib_CreateFootprint(Params, RequestId);
        'add_footprint_pad':    Result := Lib_AddFootprintPad(Params, RequestId);
        'add_footprint_pads':   Result := Lib_AddFootprintPads(Params, RequestId);
        'add_footprint_track':  Result := Lib_AddFootprintTrack(Params, RequestId);
        'add_footprint_tracks': Result := Lib_AddFootprintTracks(Params, RequestId);
        'add_footprint_arc':    Result := Lib_AddFootprintArc(Params, RequestId);
        'add_footprint_text':   Result := Lib_AddFootprintText(Params, RequestId);
        'add_footprint_fills':  Result := Lib_AddFootprintFills(Params, RequestId);
        'add_footprint_regions': Result := Lib_AddFootprintRegions(Params, RequestId);
        'set_current_footprint': Result := Lib_SetCurrentFootprint(Params, RequestId);
        'get_footprints':       Result := Lib_GetFootprints(Params, RequestId);
        'get_footprint_pads':   Result := Lib_GetFootprintPads(Params, RequestId);
        'get_library_geometry': Result := Lib_GetLibraryGeometry(Params, RequestId);
        'set_designator':       Result := Lib_SetDesignator(Params, RequestId);
        'set_designators':      Result := Lib_SetDesignators(Params, RequestId);
        'probe_designator':     Result := Lib_ProbeDesignator(Params, RequestId);
        'reload_library':       Result := Lib_ReloadLibrary(Params, RequestId);
        'convert_designators_to_stroke': Result := Lib_ConvertDesignatorsToStroke(Params, RequestId);
        'extract_intlib':       Result := Lib_ExtractIntLib(Params, RequestId);
        'link_footprint':       Result := Lib_LinkFootprint(Params, RequestId);
        'link_3d_model':        Result := Lib_Link3DModel(Params, RequestId);
        'get_footprint_bodies': Result := Lib_GetFootprintBodies(Params, RequestId);
        'set_mech_layers':      Result := Lib_SetMechLayers(Params, RequestId);
        'get_components':       Result := Lib_GetComponents(Params, RequestId);
        'search':               Result := Lib_Search(Params, RequestId);
        'get_component_details': Result := Lib_GetComponentDetails(Params, RequestId);
        'batch_set_params':    Result := Lib_BatchSetParams(Params, RequestId);
        'batch_rename':        Result := Lib_BatchRename(Params, RequestId);
        'diff_libraries':     Result := Lib_DiffLibraries(Params, RequestId);
        'add_symbol_arc':     Result := Lib_AddSymbolArc(Params, RequestId);
        'add_symbol_polygon': Result := Lib_AddSymbolPolygon(Params, RequestId);
        'set_component_description': Result := Lib_SetComponentDescription(Params, RequestId);
        'get_pin_list':       Result := Lib_GetPinList(Params, RequestId);
        'set_pin_owner_part': Result := Lib_SetPinOwnerPart(Params, RequestId);
        'set_pin_properties':   Result := Lib_SetPinProperties(Params, RequestId);
        'set_component_kind':   Result := Lib_SetComponentKind(Params, RequestId);
        'set_current_model':    Result := Lib_SetCurrentModel(Params, RequestId);
        'copy_component':     Result := Lib_CopyComponent(Params, RequestId);
        'move_components':    Result := Lib_MoveComponents(Params, RequestId);
        'move_footprints':    Result := Lib_MoveFootprints(Params, RequestId);
        'copy_footprint':     Result := Lib_CopyFootprint(Params, RequestId);
        'audit_styles':       Result := Lib_AuditStyles(Params, RequestId);
        'set_label_format':   Result := Lib_SetLabelFormat(Params, RequestId);
        'set_label_formats':  Result := Lib_SetLabelFormats(Params, RequestId);
        'set_current_component': Result := Lib_SetCurrentComponent(Params, RequestId);
        'update_footprint_heights_from_3d': Result := Lib_UpdateFootprintHeightsFrom3D(Params, RequestId);
        'set_footprint_height': Result := Lib_SetFootprintHeight(Params, RequestId);
        'split_pin_functions':  Result := Lib_SplitPinFunctions(Params, RequestId);
        'get_installed_libraries': Result := Lib_GetInstalledLibraries(Params, RequestId);
        'install_library':      Result := Lib_InstallLibrary(Params, RequestId);
        'uninstall_library':    Result := Lib_UninstallLibrary(Params, RequestId);
        'delete_component':     Result := Lib_DeleteComponent(Params, RequestId);
        'rename_component':     Result := Lib_RenameComponent(Params, RequestId);
        'delete_footprint':     Result := Lib_DeleteFootprint(Params, RequestId);
        'delete_footprint_primitives':
                                Result := Lib_DeleteFootprintPrimitives(Params, RequestId);
        'modify_footprint_pads': Result := Lib_ModifyFootprintPads(Params, RequestId);
        'delete_footprint_pads': Result := Lib_DeleteFootprintPads(Params, RequestId);
        'set_footprint_description':
                                Result := Lib_SetFootprintDescription(Params, RequestId);
        'remove_model':         Result := Lib_RemoveModel(Params, RequestId);
        'rename_footprint':     Result := Lib_RenameFootprint(Params, RequestId);
        'rename_footprints':    Result := Lib_RenameFootprints(Params, RequestId);
        'save_library':         Result := Lib_SaveLibrary(Params, RequestId);
        'set_model_name':       Result := Lib_SetModelName(Params, RequestId);
        'set_model_source':     Result := Lib_SetModelSource(Params, RequestId);
        'probe_footprint':      Result := Lib_ProbeFootprint(Params, RequestId);
        'get_pad_geometry':     Result := Lib_GetPadGeometry(Params, RequestId);
        'normalize_implementations': Result := Lib_NormalizeImplementations(Params, RequestId);
        'clear_source_library': Result := Lib_ClearSourceLibrary(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown library action: ' + Action);
    End;

    { Put the caller's document back. A no-op unless a read actually moved
      it, and it runs whether the handler succeeded or refused. }
    RestoreFocusedDoc(SavedFocus);
End;
