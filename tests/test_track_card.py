import json, os, tempfile, unittest, xml.dom.minidom

import track_card


def synth_bars(n=120, t0=1793471400):
    bars, px = [], 1.0
    for i in range(n):
        o = px
        c = px * (1 + (0.004 if i % 7 in (1, 2) else -0.003))
        bars.append({"t": t0 + 1800 * i, "o": o, "h": max(o, c) * 1.002, "l": min(o, c) * 0.998, "c": c, "v": 1000.0 + i})
        px = c
    return bars


class Wrap(unittest.TestCase):
    def test_wrap_keeps_text(self):
        text = "가나다 " * 30
        lines = track_card.wrap(text.strip(), 20)
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines).split(), text.split())


class Render(unittest.TestCase):
    def test_svg_valid_with_markers(self):
        bars = synth_bars()
        spec = {"title": "t", "subtitle": "s", "logic": ["a", "b"], "entry": "e", "exit": "x",
                "target": "g", "expected": "v", "prediction": "p", "risk": "r", "next_check": "n"}
        markers = [{"t": bars[10]["t"], "px": bars[10]["c"], "side": 1, "kind": "entry", "rule": "k", "color": "#000"},
                   {"t": bars[15]["t"], "px": bars[15]["c"], "side": 1, "kind": "exit", "rule": "k", "color": "#000", "pnl": 0.5}]
        svg = track_card.render(bars, spec, markers, [], {"k": {"trades": 1, "net_pct": 0.5, "name": "k"}})
        xml.dom.minidom.parseString(svg)
        self.assertIn("<polygon", svg)
        self.assertIn("<circle", svg)
        self.assertIn("+0.50%", svg)

    def test_preset_scalp_runs_rules(self):
        bars = synth_bars(400)
        spec = {"venue": "okx", "preset_tf": 30, "preset_strategies": ["vwap_fade_lim", "brk_20"]}
        tb, markers, lines, stats = track_card.run_preset(bars, spec)
        self.assertEqual(set(stats), {"vwap_fade_lim", "brk_20"})
        self.assertEqual(len(markers) % 2, 0)
        self.assertEqual([ln["name"] for ln in lines], ["VWAP(60봉)", "20봉 고가"])

    def test_cli_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            csv_p, spec_p, out_p = os.path.join(d, "b.csv"), os.path.join(d, "s.json"), os.path.join(d, "o.svg")
            with open(csv_p, "w") as fp:
                fp.write("t,o,h,l,c,v\n")
                for b in synth_bars(60):
                    fp.write("%d,%s,%s,%s,%s,%s\n" % (b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]))
            json.dump({"title": "x"}, open(spec_p, "w"))
            self.assertEqual(track_card.main(["--bars", csv_p, "--spec", spec_p, "--out", out_p]), 0)
            xml.dom.minidom.parse(out_p)


if __name__ == "__main__":
    unittest.main()
