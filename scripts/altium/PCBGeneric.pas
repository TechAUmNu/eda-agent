{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ PCBGeneric.pas - PCB object primitives for the Altium integration bridge                  }
{ Parallel to Generic.pas but for PCBServer / IPCB_* objects.               }
{..............................................................................}

{ ObjectTypeFromStringPCB moved to Utils.pas: Library.pas builds BEFORE
  this file and needs it, and a call to a function defined later in the
  concatenation resolves to nothing at runtime. }

{..............................................................................}
{ PCB Property Getter, late-bound, returns '' on unsupported properties     }
{..............................................................................}

{ WHERE A PCB PRIMITIVE KEEPS ITS POSITION, WHICH IS NOT ONE MEMBER.        }
{                                                                            }
{ Reading Obj.x off the declared IPCB_Primitive worked for the types that     }
{ happen to publish it and raised "Undeclared identifier: x" for the rest.    }
{ That is not a tool error: the script engine shows it as a modal before any  }
{ Try/Except runs, so it stops the polling loop and the session has to be     }
{ restarted by hand. Reported from a live board by an obj_query for X on an   }
{ eTextObject.                                                                }
{                                                                            }
{ The mapping below uses only members this build already exercises elsewhere: }
{ Pad.x / Via.x / Comp.x in PCB.pas, Text.XLocation in Generic.pas and        }
{ Library.pas, Arc.XCenter and Fill.X1Location in PCB.pas.                    }
{                                                                            }
{ A track, a region and a polygon have no single position and are reported    }
{ as unreadable rather than answered with one end of themselves. Same reason  }
{ the schematic side gained SchObjectHasText: a type that lacks the member is }
{ told so, and the caller gets a reply instead of a stalled loop.             }
Function PCBPrimitivePos(Obj : IPCB_Primitive; WantY : Boolean;
                         Var Found : Boolean) : Integer;
Var
    Oid   : Integer;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Arc   : IPCB_Arc;
    Fill  : IPCB_Fill;
Begin
    Result := 0;
    Found := False;
    Oid := Obj.ObjectId;
    Try
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If WantY Then Result := Pad.y Else Result := Pad.x;
            Found := True;
        End
        Else If Oid = eViaObject Then
        Begin
            Via := Obj;
            If WantY Then Result := Via.y Else Result := Via.x;
            Found := True;
        End
        Else If Oid = eComponentObject Then
        Begin
            Comp := Obj;
            If WantY Then Result := Comp.y Else Result := Comp.x;
            Found := True;
        End
        { NO COMPONENT-BODY BRANCH, ON PURPOSE. It read Body.x / Body.y,     }
        { and IPCB_ComponentBody declares no x or y in the AD26 SDK (docs/  }
        { altium-delphiscript/03-pcb-interfaces.md 3.11; Body.Rotation       }
        { raised "Undeclared identifier" live on 26.9.1.9). The script       }
        { engine shows that as a MODAL before the Except below runs, so every }
        { obj_query / obj_modify of X or Y on a body popped a dialog and then }
        { came back Found=False. A body now falls through to Found=False     }
        { without the dialog: the same reply, 'unreadable', as before.       }
        Else If Oid = eTextObject Then
        Begin
            Txt := Obj;
            If WantY Then Result := Txt.YLocation Else Result := Txt.XLocation;
            Found := True;
        End
        Else If Oid = eArcObject Then
        Begin
            Arc := Obj;
            If WantY Then Result := Arc.YCenter Else Result := Arc.XCenter;
            Found := True;
        End
        Else If Oid = eFillObject Then
        Begin
            Fill := Obj;
            If WantY Then Result := Fill.Y1Location Else Result := Fill.X1Location;
            Found := True;
        End;
    Except
        Found := False;
    End;
End;

{ A ratsnest line's redundancy and connection mode. Each is read in a     }
{ function of its own: both come from the API reference and neither had   }
{ been called from a script here, and an identifier the script engine does }
{ not know halts the polling loop where no Try catches it. Apart, a build  }
{ without one fails only a query that asks for it.                         }
Function PCBConnRedundant(Conn : IPCB_Connection) : String;
Begin
    Result := BoolToJsonStr(Conn.IsRedundant);
End;

Function PCBConnMode(Conn : IPCB_Connection) : String;
Var
    M : Integer;
Begin
    M := Conn.Mode;
    Result := IntToStr(M);
End;

Function GetPCBProperty(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Track : IPCB_Track;
    Arc   : IPCB_Arc;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Rgn   : IPCB_Region;
    Poly  : IPCB_Polygon;
    Body  : IPCB_ComponentBody;
    Conn  : IPCB_Connection;
    Oid   : Integer;
    PosVal : Integer;
    PosFound : Boolean;
Begin
    Result := '';
    Try
        Oid := Obj.ObjectId;
        { Base IPCB_Primitive members, valid to read on ANY primitive. }
        If PropName = 'ObjectId'        Then Result := IntToStr(Oid)
        Else If (PropName = 'X') Or (PropName = 'Y') Then
        Begin
            PosVal := PCBPrimitivePos(Obj, PropName = 'Y', PosFound);
            If PosFound Then
                Result := FloatToJsonStr(CoordToMilsF(PosVal))   { sub-mil coordinates: local patch 2026-09-18 }
            Else
            Begin
                { A track has two ends and a region has an outline, so       }
                { answering with either would be a coordinate the caller     }
                { would then act on. Say it is not on this type instead.     }
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'Layer'      Then Result := GetLayerString(Obj.Layer)
        Else If PropName = 'Descriptor' Then Result := Obj.Descriptor
        Else If PropName = 'Selected'   Then Result := BoolToJsonStr(Obj.Selected)
        { 'Net.Name' is accepted as well as 'Net'. Designator.Text and
          Comment.Text are already accepted alongside their bare forms,
          so a caller who used one of those infers a dotted rule that
          held twice and failed here, silently, returning empty as
          though the copper had no net. Measured: a session concluded
          the bridge could not attribute copper to a net at all and
          stopped, when the property was simply spelled differently. }
        Else If (PropName = 'Net') Or (PropName = 'Net.Name') Then
        Begin
            If Obj.Net <> Nil Then Result := Obj.Net.Name;
        End
        { WHAT A PRIMITIVE BELONGS TO. The walk below takes every primitive  }
        { on the board, so a footprint's own tracks and a hatched pour's     }
        { tracks come through with the routing, on the same copper layers,   }
        { and a filter on Layer alone cannot keep to routing. These let it:  }
        { InComponent=false|InPolygon=false is free copper.                  }
        Else If PropName = 'InComponent' Then
        Begin
            Result := BoolToJsonStr(Obj.InComponent);
        End
        Else If PropName = 'InPolygon' Then
        Begin
            Result := BoolToJsonStr(Obj.InPolygon);
        End
        Else If PropName = 'IsKeepout' Then
        Begin
            Result := BoolToJsonStr(Obj.IsKeepout);
        End
        Else If PropName = 'Locked' Then
        Begin
            Result := BoolToJsonStr(Not Obj.Moveable);
        End
        Else If PropName = 'Component' Then
        Begin
            { The owning footprint's designator; empty for a free primitive. }
            If Obj.InComponent Then
            Begin
                If Obj.Component <> Nil Then Result := Obj.Component.Name.Text;
            End;
        End
        { Subtype members. A member the object actually held lacks is the     }
        { engine's "Undeclared identifier" halt, so each is asked only after  }
        { an ObjectId test (the typed local is decoration: the engine         }
        { resolves against the runtime object, not the declaration).          }
        { A ratsnest line has two ends the same way a track does. }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.X1)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.X1)); End;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Y1)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.Y1)); End;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.X2)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.X2)); End;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Y2)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.Y2)); End;
        End
        Else If PropName = 'Layer1' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := GetLayerString(Conn.Layer1); End;
        End
        Else If PropName = 'Layer2' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := GetLayerString(Conn.Layer2); End;
        End
        Else If PropName = 'IsRedundant' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := PCBConnRedundant(Conn); End;
        End
        Else If PropName = 'Mode' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := PCBConnMode(Conn); End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.Width)); End
            Else If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.LineWidth)); End;
        End
        Else If PropName = 'XCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.XCenter)); End;
        End
        Else If PropName = 'YCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.YCenter)); End;
        End
        Else If PropName = 'Radius' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.Radius)); End;
        End
        Else If PropName = 'StartAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(Arc.StartAngle); End;
        End
        Else If PropName = 'EndAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(Arc.EndAngle); End;
        End
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.HoleSize)); End
            Else If Oid = eViaObject Then Begin Via := Obj; Result := IntToStr(CoordToMils(Via.HoleSize)); End;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.TopXSize)); End;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.TopYSize)); End;
        End
        Else If PropName = 'TopShape' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(Pad.TopShape); End;
        End
        Else If PropName = 'Size' Then
        Begin
            If Oid = eViaObject Then Begin Via := Obj; Result := IntToStr(CoordToMils(Via.Size)); End;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := FloatToJsonStr(Comp.Rotation); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(Pad.Rotation); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(Txt.Rotation); End;
        End
        Else If PropName = 'Pattern' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Pattern; End;
        End
        Else If PropName = 'SourceDesignator' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceDesignator; End;
        End
        Else If PropName = 'Name' Then
        Begin
            { Component Name is an IPCB_Text; return its .Text, not the object }
            { (Dispatch->OleStr otherwise crashed EscapeJsonString via modal). }
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
        End
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text') Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
        End
        Else If (PropName = 'Comment') Or (PropName = 'Comment.Text') Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Comment.Text; End;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := Txt.Text; End;
        End
        { WRITABLE AND UNREADABLE IS THE SAME BUG IN THE OTHER DIRECTION.
          These three were added to the writer and not to this reader, so
          a caller could set a pour option and had no way to confirm it,
          which is the exact failure the writer was fixed for. Found by
          asking for them on a live board and being told they are not PCB
          properties. }
        Else If PropName = 'RemoveDead' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveDead); End;
        End
        Else If PropName = 'RemoveNarrowNecks' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveNarrowNecks); End;
        End
        Else If PropName = 'RemoveIslandsByArea' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveIslandsByArea); End;
        End
        Else If PropName = 'StandoffHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := IntToStr(CoordToMils(Body.StandoffHeight)); End;
        End
        Else If PropName = 'OverallHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := IntToStr(CoordToMils(Body.OverallHeight)); End;
        End
        { A REGION'S KIND IS WHAT MAKES IT A BOARD CUTOUT, and nothing
          here could see it. Reported from a live board as the flag not
          being reachable through this API; it is
            Property Kind : TRegionKind Read GetState_Kind
                                        Write SetState_Kind;
          and we had simply never exposed it.

          Returned as a word rather than an ordinal. The numbers are not
          documented anywhere this project can verify, and publishing an
          unverified number invites a caller to write it back. }
        Else If (PropName = 'Kind') Or (PropName = 'RegionKind') Then
        Begin
            If Oid = eRegionObject Then
            Begin
                Rgn := Obj;
                If Rgn.Kind = eRegionKind_BoardCutout Then Result := 'board_cutout'
                Else If Rgn.Kind = eRegionKind_Cutout Then Result := 'cutout'
                Else If Rgn.Kind = eRegionKind_Copper Then Result := 'copper'
                Else If Rgn.Kind = eRegionKind_NamedRegion Then Result := 'named_region'
                Else If Rgn.Kind = eRegionKind_Cavity Then Result := 'cavity'
                Else Result := 'unknown';
            End;
        End;
    Except
        { Counted for the live probe: IsRedundant and Mode on a ratsnest   }
        { line (PCBConnRedundant / PCBConnMode) have never been called from }
        { a script, and there is no way to ask first whether the engine     }
        { knows an identifier. If the count names them, drop the branch and }
        { the names from IsKnownPCBProperty / KnownPCBPropertyList.         }
        RecordCaughtException('GetPCBProperty:' + PropName);
        Result := '';
    End;
End;

{..............................................................................}
{ PCB Property Setter                                                        }
{..............................................................................}

{ A caller-supplied layer name reaching a primitive through obj_modify.       }
{ GetLayerFromString answered eTopLayer for every name it did not know, so    }
{ set="Layer=Internal Plane 1" MOVED the primitive to the top copper layer.   }
{ ResolveLayerId asks the board's own stack instead, and an unresolvable      }
{ name is refused rather than silently rounded to the top.                    }
{                                                                             }
{ Reports whether it applied, because the caller now has somewhere to put     }
{ that. ProcessActivePCBDoc still rejects the whole call up front via         }
{ UnresolvedLayerAssignment, before any object has been touched, which is     }
{ the stronger guarantee: a partly-applied batch is worse than a refused one. }
Function SetPrimitiveLayerByName(Obj : IPCB_Primitive; Value : String) : Boolean;
Var
    Lyr : TLayer;
Begin
    Result := False;
    Lyr := ResolveLayerId(GetPCBBoardAnywhere(0), Value);
    If Lyr <> eNoLayer Then
    Begin
        Obj.Layer := Lyr;
        Result := True;
    End;
End;

{ Returns: 1 = handled, 0 = unknown property name, -1 = write threw.        }
{                                                                           }
{ THIS USED TO BE A Procedure, and that is the whole bug. It reported       }
{ nothing, so a caller could not tell a property this build writes from one }
{ it has never heard of, and modify_objects answered with a match count     }
{ either way. Measured on a live board: setting RemoveDead on a polygon     }
{ came back matched:2 having written nothing, and the operator reasonably   }
{ concluded the property was not writable. It is:                           }
{ IPCB_Polygon declares                                                     }
{   Property RemoveDead : Boolean Read GetState_RemoveDead                  }
{                                 Write SetState_RemoveDead;                }
{ There was simply no case for it here.                                     }
{                                                                           }
{ The schematic writer was given this contract and the PCB one was not, so  }
{ the same class of silent failure survived on this side. Both now feed the }
{ one buffer in Utils.                                                       }
{                                                                           }
{ The error channel this adds is the one SetPrimitiveLayerByName above was   }
{ written without: an unresolvable layer name is now reported as a failed    }
{ write rather than left quietly unapplied.                                  }
Function SetPCBProperty(Obj : IPCB_Primitive; PropName : String; Value : String) : Integer;
Var
    Track : IPCB_Track;
    Pad   : IPCB_Pad;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Poly  : IPCB_Polygon;
    Body  : IPCB_ComponentBody;
    Rgn   : IPCB_Region;
    Oid   : Integer;
    Matched : Boolean;
    PosVal : Integer;
    PosFound : Boolean;
Begin
    Result := 0;
    Matched := True;
    Try
        Oid := Obj.ObjectId;
        { Base members, settable on any primitive. }
        { EVERY PRIMITIVE IS MOVED, NOT ASSIGNED.
          Writing x or y directly is not something this codebase has done
          successfully, and the one time it tried on a component body the
          PCB engine went down with an access violation. It was also
          unreachable for half the types, since x is not declared on all
          of them. MoveByXY is inherited from IPCB_Primitive,
          PCB_ReplicateLayout already calls it, and Lib_Link3DModel
          positions bodies with it, so it is the proven route, and taking
          the delta from PCBPrimitivePos makes one path serve every type
          that has a position at all. }
        If (PropName = 'X') Or (PropName = 'Y') Then
        Begin
            { Moved as a DELTA off wherever the primitive currently is, so   }
            { one shared path covers a pad, a via, a text and an arc without }
            { each needing its own writable member. A type with no single    }
            { position is refused rather than moved by one corner.           }
            PosVal := PCBPrimitivePos(Obj, PropName = 'Y', PosFound);
            If Not PosFound Then
            Begin
                { Reported as unreadable and NOT as a failure or an unknown
                  name: X is a real property spelled correctly, and the tail
                  of this function turns 0 into "unknown" and -1 into
                  "failed", either of which would send the caller looking
                  for a spelling mistake that is not there. }
                NotePropertyDiag('unreadable', PropName);
                Result := 1;
                Exit;
            End;
            If PropName = 'X' Then
                Obj.MoveByXY(MilsToCoord(StrToIntDef(Value, 0)) - PosVal, 0)
            Else
                Obj.MoveByXY(0, MilsToCoord(StrToIntDef(Value, 0)) - PosVal);
        End
        Else If PropName = 'Layer'    Then SetPrimitiveLayerByName(Obj, Value)
        Else If PropName = 'Selected' Then Obj.Selected := StrToBool(Value)
        { Subtype members: narrow to a typed local via ObjectId first. }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X1 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y1 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X2 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y2 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Width := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Comp.Rotation := StrToFloatDef(Value, 0); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Pad.Rotation := StrToFloatDef(Value, 0); End;
        End
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.HoleSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopXSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopYSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Txt.Text := Value; End;
        End

        { Polygon pour options. All three are declared on IPCB_Polygon with
          both a Read and a Write accessor, so they are settable; they were
          simply absent here. Setting one does NOT repour: the flags decide
          what the NEXT pour does, so pcb_repour_polygons has to follow. }
        { The body's own writable members, from its declared interface:
          Property StandoffHeight : TCoord Read GetStandoffHeight
                                           Write SetStandoffHeight;
          and the same shape for OverallHeight. Identifier is read-only
          and is deliberately absent. }
        Else If PropName = 'StandoffHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Body.StandoffHeight := MilsToCoord(StrToIntDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'OverallHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Body.OverallHeight := MilsToCoord(StrToIntDef(Value, 0)); End
            Else Matched := False;
        End
        { Turning a region INTO a board cutout, the other half of the
          read above. The five identifiers are attested: four independent
          scripts in reference/ compare against them, so they exist in
          DelphiScript. What none of them does is ASSIGN one, so the
          write is unproven in the way StandoffHeight is, and it is
          ranked accordingly in the release procedure.

          If/Else If rather than Case, because Case on an enum crashes
          the script engine here. }
        Else If (PropName = 'Kind') Or (PropName = 'RegionKind') Then
        Begin
            If Oid = eRegionObject Then
            Begin
                Rgn := Obj;
                If Value = 'board_cutout' Then Rgn.Kind := eRegionKind_BoardCutout
                Else If Value = 'cutout' Then Rgn.Kind := eRegionKind_Cutout
                Else If Value = 'copper' Then Rgn.Kind := eRegionKind_Copper
                Else If Value = 'named_region' Then Rgn.Kind := eRegionKind_NamedRegion
                Else If Value = 'cavity' Then Rgn.Kind := eRegionKind_Cavity
                Else Matched := False;
            End
            Else Matched := False;
        End
        Else If PropName = 'RemoveDead' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveDead := StrToBool(Value); End
            Else Matched := False;
        End
        Else If PropName = 'RemoveNarrowNecks' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveNarrowNecks := StrToBool(Value); End
            Else Matched := False;
        End
        Else If PropName = 'RemoveIslandsByArea' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveIslandsByArea := StrToBool(Value); End
            Else Matched := False;
        End
        Else Matched := False;

        If Matched Then Result := 1 Else Result := 0;
    Except
        { Counted for the live probe: the region Kind write (Kind /        }
        { RegionKind) is attested only as a comparison, never assigned by a }
        { reference script, and nothing can ask first whether it is         }
        { writable. If the count names it, drop the write branch (Matched   }
        { := False) and keep the read in GetPCBProperty.                    }
        RecordCaughtException('SetPCBProperty:' + PropName);
        Result := -1;
    End;

    If Result = 0 Then NotePropertyDiag('unknown', PropName)
    Else If Result = -1 Then NotePropertyDiag('failed', PropName);
End;

{..............................................................................}
{ PCB Filter / JSON / Apply, parallel to schematic versions                 }
{..............................................................................}

Function MatchesFilterPCB(Obj : IPCB_Primitive; FilterStr : String) : Boolean;
Var
    Remaining, Condition, PropName, Expected, Actual : String;
    PipePos, EqPos : Integer;
Begin
    Result := True;
    If FilterStr = '' Then Exit;
    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else Begin Condition := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Condition);
        If EqPos = 0 Then Continue;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + 1, Length(Condition));
        Actual := GetPCBProperty(Obj, PropName);
        If Actual <> Expected Then Begin Result := False; Exit; End;
    End;
End;

{..............................................................................}
{ IsKnownPCBProperty                                                           }
{                                                                              }
{ Whether GetPCBProperty has a branch for this name. It exists because that    }
{ getter returns '' for anything it does not recognise, which makes a          }
{ MISSPELLED property indistinguishable from one that is genuinely empty. That }
{ ambiguity has now cost three separate investigations, each concluding the    }
{ bridge could not do something it could: the caller sees blanks, believes the }
{ data is not there, and stops.                                                }
{                                                                              }
{ Kept next to the getter deliberately. A list that lives somewhere else       }
{ drifts the first time a branch is added, and a stale allow-list would reject }
{ a property that works, which is worse than the silence it replaces.          }
{..............................................................................}

Function IsKnownPCBProperty(PropName : String) : Boolean;
Begin
    { Kind, RegionKind, the three pour options and the two body heights  }
    { were answered by the getter and refused here, so a query for them  }
    { was told they do not exist: the lag the list below warns about.    }
    Result :=
        (PropName = 'ObjectId') Or (PropName = 'X') Or (PropName = 'Y') Or
        (PropName = 'Layer') Or (PropName = 'Descriptor') Or
        (PropName = 'Selected') Or (PropName = 'Net') Or
        (PropName = 'Net.Name') Or (PropName = 'X1') Or (PropName = 'Y1') Or
        (PropName = 'X2') Or (PropName = 'Y2') Or (PropName = 'Width') Or
        (PropName = 'Radius') Or (PropName = 'StartAngle') Or
        (PropName = 'EndAngle') Or (PropName = 'XCenter') Or
        (PropName = 'YCenter') Or (PropName = 'HoleSize') Or
        (PropName = 'Size') Or (PropName = 'TopShape') Or
        (PropName = 'TopXSize') Or (PropName = 'TopYSize') Or
        (PropName = 'Rotation') Or (PropName = 'Name') Or
        (PropName = 'Text') Or (PropName = 'Pattern') Or
        (PropName = 'Designator') Or (PropName = 'Designator.Text') Or
        (PropName = 'Comment') Or (PropName = 'Comment.Text') Or
        (PropName = 'SourceDesignator') Or
        (PropName = 'Kind') Or (PropName = 'RegionKind') Or
        (PropName = 'RemoveDead') Or (PropName = 'RemoveNarrowNecks') Or
        (PropName = 'RemoveIslandsByArea') Or
        (PropName = 'StandoffHeight') Or (PropName = 'OverallHeight') Or
        (PropName = 'InComponent') Or (PropName = 'Component') Or
        (PropName = 'InPolygon') Or (PropName = 'IsKeepout') Or
        (PropName = 'Locked') Or (PropName = 'Layer1') Or
        (PropName = 'Layer2') Or (PropName = 'IsRedundant') Or
        (PropName = 'Mode');
End;

Function UnknownPCBProperties(PropsStr : String) : String;
Var
    Remaining, PropName : String;
    CommaPos : Integer;
Begin
    Result := '';
    Remaining := PropsStr;
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            PropName := Trim(Copy(Remaining, 1, CommaPos - 1));
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else Begin PropName := Trim(Remaining); Remaining := ''; End;
        If (PropName <> '') And (Not IsKnownPCBProperty(PropName)) Then
        Begin
            If Result <> '' Then Result := Result + ', ';
            Result := Result + PropName;
        End;
    End;
End;

{ HIDDEN FROM THE RUN SCRIPT DIALOG BY ITS ARGUMENT.
  Altium lists only parameterless routines there, so this project puts
  fifty-five internal helpers in front of a user whose four real entry
  points are StartMCPServer, StopMCPServer, RunSelfTest and
  ShowStatusForm. A parameter is the only lever DelphiScript offers:
  there are no visibility modifiers and every unit in the project is
  scanned.

  Dummy is never read. It exists to change the arity and nothing else.
  Reported by a user as too many functions listed to find the right one. }
Function KnownPCBPropertyList(Dummy : Integer) : String;
Begin
    Result := 'ObjectId, X, Y, Layer, Descriptor, Selected, Net, X1, Y1, '
        + 'X2, Y2, Width, Radius, StartAngle, EndAngle, XCenter, YCenter, '
        + 'HoleSize, Size, TopShape, TopXSize, TopYSize, Rotation, Name, '
        + 'Text, Pattern, Designator, Comment, SourceDesignator, '
        { Everything the reader above answers. A list that lags the reader
          tells a caller a property does not exist when it does, which is
          how the pour flags were reported as unreachable. }
        + 'Kind, RemoveDead, RemoveNarrowNecks, RemoveIslandsByArea, '
        + 'StandoffHeight, OverallHeight, InComponent, Component, '
        + 'InPolygon, IsKeepout, Locked, Layer1, Layer2, IsRedundant, Mode';
End;

Function BuildObjectJsonPCB(Obj : IPCB_Primitive; PropsStr : String) : String;
Var
    Remaining, PropName, PropValue : String;
    CommaPos : Integer;
    First : Boolean;
Begin
    Result := '{';
    First := True;
    Remaining := PropsStr;
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin PropName := Copy(Remaining, 1, CommaPos - 1); Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining)); End
        Else Begin PropName := Remaining; Remaining := ''; End;
        PropValue := GetPCBProperty(Obj, PropName);
        If Not First Then Result := Result + ',';
        First := False;
        Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
    End;
    Result := Result + '}';
End;

Procedure ApplySetPropertiesPCB(Obj : IPCB_Primitive; SetStr : String);
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));
        SetPCBProperty(Obj, PropName, PropValue);
    End;
End;

{..............................................................................}
{ PCB Board iteration, query/modify/delete on active PCB                    }
{..............................................................................}

Function ProcessPCBBoardObjects(Board : IPCB_Board; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; Var TotalMatched : Integer; Limit : Integer) : String;
Var
    Iterator : IPCB_BoardIterator;
    Obj, FoundObj : IPCB_Primitive;
    ObjJson : String;
    First : Boolean;
    I : Integer;
    Victims : TInterfaceList;
Begin
    Result := '';
    First := (TotalMatched = 0);

    { EVERY PreProcess BELOW IS IN A Try/Finally, and that is not tidiness.   }
    {                                                                          }
    { An exception anywhere between PreProcess and PostProcess leaves Altium   }
    { believing a command is still running. From then on EVERY save of a PCB   }
    { document is refused with "A command is currently active and save cannot  }
    { be completed at this time", the editor offers to write a copy instead,   }
    { and NOTHING CLEARS IT: not restarting the polling loop, because the      }
    { state lives in the PCB server rather than the script, and not Escape in  }
    { the editor.                                                              }
    {                                                                          }
    { MEASURED on 2026-08-25: a PcbLib and its board went a whole day without  }
    { a successful save while SchLib documents beside them saved normally,     }
    { and the authored footprints existed only in memory.                      }
    {                                                                          }
    { The loop body calls MatchesFilterPCB, BuildObjectJsonPCB and             }
    { ApplySetPropertiesPCB, all of which touch caller-supplied property names }
    { on arbitrary primitives, so raising is an ordinary outcome here rather   }
    { than a remote possibility. AltiumScriptCentral ships a whole recovery    }
    { script for this symptom, which is a fair measure of how often it bites.  }
    { COLLECT, THEN REMOVE, the way Altium's own DeletePCBObjects example  }
    { does. Finding one match and restarting the walk after each removal   }
    { built a new iterator per object and walked again past everything the }
    { filter rejects, so taking the routing off a board walked the board   }
    { once per track. Nothing is removed while the iterator is live, and   }
    { the list is never Freed: releasing board-primitive refs through it   }
    { faults in oleaut32 (see PCB_SetTrackWidth). Items come back as the   }
    { base IPCB_Primitive, the type RemovePCBObject takes.                 }
    If Mode = 'delete' Then
    Begin
        Victims := TInterfaceList.Create;
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Obj := Iterator.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilterPCB(Obj, FilterStr) Then Victims.Add(Obj);
                Obj := Iterator.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
        PCBServer.PreProcess;
        Try
            For I := 0 To Victims.Count - 1 Do
            Begin
                FoundObj := Victims.Items[I];
                If FoundObj = Nil Then Continue;
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                    PCBM_BoardRegisteration, FoundObj.I_ObjectAddress);
                Board.RemovePCBObject(FoundObj);
                Inc(TotalMatched);
            End;
        Finally
            PCBServer.PostProcess;
        End;
        Exit;
    End;

    If Mode = 'modify' Then PCBServer.PreProcess;
    Try
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);

            Obj := Iterator.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                If (Limit > 0) And (TotalMatched >= Limit) Then Break;
                If MatchesFilterPCB(Obj, FilterStr) Then
                Begin
                    If Mode = 'query' Then
                    Begin
                        ObjJson := BuildObjectJsonPCB(Obj, PropsStr);
                        If Not First Then Result := Result + ',';
                        First := False;
                        Result := Result + ObjJson;
                    End
                    Else If Mode = 'modify' Then
                        ApplySetPropertiesPCB(Obj, SetStr);
                    Inc(TotalMatched);
                End;
                Obj := Iterator.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
    Finally
        If Mode = 'modify' Then PCBServer.PostProcess;
    End;
End;

{ The first Layer= assignment in a set string this board cannot resolve, or    }
{ '' when every one of them resolves. Checked before the iteration starts so   }
{ a bad name costs nothing rather than relocating half the matched objects.    }

Function UnresolvedLayerAssignment(Board : IPCB_Board; SetStr : String) : String;
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos > 0 Then
        Begin
            PropName := UpperCase(Trim(Copy(Assignment, 1, EqPos - 1)));
            PropValue := Trim(Copy(Assignment, EqPos + 1, Length(Assignment)));
            If (PropName = 'LAYER') And (PropValue <> '') Then
            Begin
                If ResolveLayerId(Board, PropValue) = eNoLayer Then
                Begin
                    Result := PropValue;
                    Exit;
                End;
            End;
        End;
    End;
End;

Function ProcessActivePCBDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    Board : IPCB_Board;
    TotalMatched : Integer;
    JsonItems, Why, BadLayer : String;
Begin
    { A READ MAY WANDER; AN EDIT MAY NOT.                                   }
    {                                                                        }
    { GetPCBBoardAnywhere opens the first board it can find when none is     }
    { focused, and hides the focus change afterwards. For a query that is    }
    { the focus-independent access this project advertises. For a delete it  }
    { is a misfire: with a library in front and two boards open, primitives  }
    { would be removed from whichever board the project walk reached first,  }
    { and nothing in the reply would say which.                              }
    {                                                                        }
    { There is no library-scoped primitive delete, so a caller working in a  }
    { PcbLib has no correct tool here and the wrong one used to look like    }
    { it worked.                                                             }
    If (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create') Then
    Begin
        Board := GetPCBBoardForMutation(Why);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'AMBIGUOUS_TARGET', Why);
            Exit;
        End;
    End
    Else
        Board := GetPCBBoardAnywhere(0);

    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    If SetStr <> '' Then
    Begin
        BadLayer := UnresolvedLayerAssignment(Board, SetStr);
        If BadLayer <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
                'Unknown layer name: ' + BadLayer + '. ' + BoardLayerNamesHint(Board));
            Exit;
        End;
    End;
    TotalMatched := 0;
    JsonItems := ProcessPCBBoardObjects(Board, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, TotalMatched, Limit);

    If (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create') Then
    Begin
        Board.GraphicalView_ZoomRedraw;
        MarkDocDirtyByPath(Board.FileName);
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) + '}')
    Else
        { matched counts what the FILTER selected, and said nothing about
          whether a property write landed. The tail reports that, the same
          way the schematic modify replies do. }
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched)
            + ModifyOutcomeJson(0) + '}');
End;
