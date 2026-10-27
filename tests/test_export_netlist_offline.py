"""proj_export_netlist without Altium: the "?" rows of a multi-part component
and the line endings of the PADS file.

A multi-part component (a 216-ball MCU drawn as a supplies part on one sheet
and a GPIO part on another) comes back from project.get_nets once per placed
part. The pins of the other part carry the net name "?", and before this fix
the exporter wrote them out as a net called "?" that held every pin of the
component, so a netlist comparison saw each supply net split in two. The PADS
text also carries CRLF and was written in text mode, which on Windows doubles
the CR.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.project import NO_NET, netlist_nodes


def _pin(comp, pin, net, name=""):
    return {"component": comp, "pin": pin, "pin_name": name or pin, "net": net}


# U1 has two parts: A1/A2 are supplies (part 1), B1/B2 GPIO (part 2). Each
# part instance lists all four pins; the other part's pins come back as "?".
TWO_PART = [
    _pin("U1", "A1", "VDD"), _pin("U1", "A2", "GND"),
    _pin("U1", "B1", NO_NET), _pin("U1", "B2", NO_NET),
    _pin("U1", "A1", NO_NET), _pin("U1", "A2", NO_NET),
    _pin("U1", "B1", "LED"), _pin("U1", "B2", NO_NET),
    _pin("R1", "1", "LED"), _pin("R1", "2", "VDD"),
]


class TestNetlistNodes:
    def test_no_net_rows_dropped_when_pin_has_a_real_net(self):
        nodes, dropped = netlist_nodes(TWO_PART)
        nets = {(c, p): n for c, p, _pn, n in nodes if n != NO_NET}
        assert nets[("U1", "A1")] == "VDD"
        assert nets[("U1", "B1")] == "LED"
        # A1, A2 and B1 each had one "?" row next to a real net.
        assert dropped == 3

    def test_pin_seen_only_as_no_net_is_kept(self):
        # U1.B2 is on no net on either part: it stays, under "?".
        nodes, _ = netlist_nodes(TWO_PART)
        assert ("U1", "B2", "B2", NO_NET) in nodes
        assert [n for n in nodes if n[3] == NO_NET] == [("U1", "B2", "B2", NO_NET)]

    def test_exact_repeats_kept_once(self):
        nodes, dropped = netlist_nodes([_pin("R1", "1", "N1"), _pin("R1", "1", "N1")])
        assert nodes == [("R1", "1", "1", "N1")]
        assert dropped == 0

    def test_rows_missing_fields_skipped(self):
        nodes, _ = netlist_nodes([{"component": "R1", "pin": "1"}, _pin("", "1", "N1"),
                                  _pin("R2", "2", "N2")])
        assert nodes == [("R2", "2", "2", "N2")]

    def test_single_part_component_unchanged(self):
        pins = [_pin("C1", "1", "VDD"), _pin("C1", "2", "GND")]
        nodes, dropped = netlist_nodes(pins)
        assert nodes == [("C1", "1", "1", "VDD"), ("C1", "2", "2", "GND")]
        assert dropped == 0


def _tools(monkeypatch, pins):
    class FakeBridge:
        async def send_command_async(self, command, params=None, timeout=None):
            assert command == "project.get_nets"
            return {"pins": pins}

    monkeypatch.setattr("eda_agent.tools.project.get_bridge", lambda: FakeBridge())
    from eda_agent.tools import project

    tools = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn
            return decorator

    project.register_project_tools(DummyMcp())
    return tools


class TestExportFile:
    @pytest.mark.asyncio
    async def test_pads_has_no_no_net_signal_and_single_crlf(self, monkeypatch, tmp_path):
        tools = _tools(monkeypatch, TWO_PART)
        out = tmp_path / "n.net"
        result = await tools["proj_export_netlist"](output_path=str(out), net_format="pads")
        assert result["success"] is True
        assert result["no_net_rows_dropped"] == 3
        raw = out.read_bytes()
        assert b"\r\r\n" not in raw
        assert raw.count(b"\r\n") == len(raw.splitlines())
        text = raw.decode("utf-8")
        # Only the genuinely unconnected U1.B2 is left under "?".
        assert "*SIGNAL* ?\r\nU1.B2\r\n" in text
        assert "*SIGNAL* VDD\r\nR1.2 U1.A1\r\n" in text
        assert "*SIGNAL* LED\r\nR1.1 U1.B1\r\n" in text

    @pytest.mark.asyncio
    async def test_tabular_has_lf_rows(self, monkeypatch, tmp_path):
        tools = _tools(monkeypatch, TWO_PART)
        out = tmp_path / "n.csv"
        result = await tools["proj_export_netlist"](output_path=str(out), net_format="tabular")
        # 10 rows: 3 "?" rows beside a real net dropped, U1.B2 "?" kept once.
        assert result["node_count"] == 6
        raw = out.read_bytes()
        assert b"\r" not in raw
        assert raw.decode("utf-8").splitlines()[0] == "component,pin,pin_name,net"
