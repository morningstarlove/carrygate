# -*- coding: utf-8 -*-
"""
liqmap.py(P38 모형)·scripts/liqmap_screen.py(선별) 계산 검증 — 손으로 만든 가짜 봉, 인터넷 없음.

  python -m unittest tests.test_liqmap -v
"""
import os, sys, importlib.util, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import liqmap as LM  # noqa: E402

_spec = importlib.util.spec_from_file_location("liqmap_screen", os.path.join(ROOT, "scripts", "liqmap_screen.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

H = LM.BAR_SEC


def bar(i, h, l, c, oi, o=None):
    return {"t": i * H, "o": c if o is None else o, "h": h, "l": l, "c": c, "oi": oi}


def flat(i, p, oi):
    return bar(i, p, p, p, oi)


def seeded():
    """봉0 oi=100, 봉1 가격 100 에서 oi 110 → 묶음 8개(금액 각 250)."""
    st = LM.new_state()
    LM.step(st, flat(0, 100.0, 100.0))
    LM.step(st, bar(1, 102.0, 98.0, 100.0, 110.0))
    return st


class Model(unittest.TestCase):
    def test_liq_prices(self):
        self.assertAlmostEqual(LM.liq_prices(100, 10)[0], 90.5)
        self.assertAlmostEqual(LM.liq_prices(100, 10)[1], 109.5)
        self.assertAlmostEqual(LM.liq_prices(100, 100)[0], 99.5)
        self.assertAlmostEqual(LM.liq_prices(100, 100)[1], 100.5)

    def test_first_bar_only_records_oi(self):
        st = LM.new_state()
        r = LM.step(st, flat(0, 100.0, 100.0))
        self.assertEqual(st["cohorts"], [])
        self.assertEqual(st["prev_oi"], 100.0)
        self.assertEqual(r, {"swept_long": 0.0, "swept_short": 0.0})

    def test_oi_increase_makes_eight_cohorts(self):
        st = seeded()
        self.assertEqual(len(st["cohorts"]), 8)
        self.assertEqual(sorted(c["lev"] for c in st["cohorts"]), [10, 10, 25, 25, 50, 50, 100, 100])
        self.assertEqual(sum(1 for c in st["cohorts"] if c["side"] == "L"), 4)
        for c in st["cohorts"]:
            self.assertAlmostEqual(c["amt"], 250.0)   # (110-100)*100/4
            self.assertEqual(c["t"], H)
        liq100 = [c["liq"] for c in st["cohorts"] if c["lev"] == 100]
        self.assertAlmostEqual(sorted(liq100)[0], 99.5)
        self.assertAlmostEqual(sorted(liq100)[1], 100.5)

    def test_new_cohort_not_swept_in_own_bar(self):
        # 봉1 저가 98 <= 롱100배 청산가 99.5 이지만 자기 봉에서는 안 쓸림
        st = seeded()
        self.assertEqual(len(st["cohorts"]), 8)

    def test_next_bar_low_sweeps_long_boundary_inclusive(self):
        st = seeded()
        liq = LM.liq_prices(100.0, 100)[0]                # 부동소수점 그대로의 경계값
        r = LM.step(st, bar(2, 100.0, liq, 100.0, 110.0))
        self.assertAlmostEqual(r["swept_long"], 250.0)    # liq >= 저가(같음)
        self.assertEqual(r["swept_short"], 0.0)
        self.assertEqual(len(st["cohorts"]), 7)

    def test_next_bar_high_sweeps_short_boundary_inclusive(self):
        st = seeded()
        liq = LM.liq_prices(100.0, 100)[1]
        r = LM.step(st, bar(2, liq, 100.0, 100.0, 110.0))
        self.assertAlmostEqual(r["swept_short"], 250.0)   # liq <= 고가(같음)
        self.assertEqual(r["swept_long"], 0.0)
        self.assertEqual(len(st["cohorts"]), 7)

    def test_no_sweep_just_outside(self):
        st = seeded()
        r = LM.step(st, bar(2, 100.4, 99.6, 100.0, 110.0))
        self.assertEqual((r["swept_long"], r["swept_short"]), (0.0, 0.0))
        self.assertEqual(len(st["cohorts"]), 8)

    def test_oi_decrease_scales_all(self):
        st = seeded()
        LM.step(st, bar(2, 100.1, 99.9, 100.0, 55.0))
        self.assertEqual(len(st["cohorts"]), 8)
        for c in st["cohorts"]:
            self.assertAlmostEqual(c["amt"], 125.0)
        self.assertEqual(st["prev_oi"], 55.0)

    def test_oi_none_skips_and_keeps_prev(self):
        st = seeded()
        r = LM.step(st, bar(2, 100.0, 99.5, 100.0, None))
        self.assertAlmostEqual(r["swept_long"], 250.0)    # 쓸기는 한다
        self.assertEqual(st["prev_oi"], 110.0)
        LM.step(st, bar(3, 100.1, 99.9, 100.0, 110.0))    # 증감 0
        self.assertEqual(len(st["cohorts"]), 7)
        for c in st["cohorts"]:
            self.assertAlmostEqual(c["amt"], 250.0)

    def test_old_cohort_dropped_at_lookback(self):
        st = seeded()                                    # 묶음 t = 1*H
        edge = 1 + LM.LOOKBACK_H
        LM.step(st, flat(edge - 1, 100.0, 110.0))
        self.assertEqual(len(st["cohorts"]), 8)           # 335시간 지남: 유지
        LM.step(st, flat(edge, 100.0, 110.0))
        self.assertEqual(len(st["cohorts"]), 0)           # 336시간: 버림


class Asym(unittest.TestCase):
    def co(self, side, liq, amt):
        return {"t": 0, "side": side, "lev": 10, "liq": liq, "amt": amt}

    def test_boundaries(self):
        cs = [self.co("S", 100.0, 1), self.co("S", 110.0, 2), self.co("S", 110.5, 4),
              self.co("L", 100.0, 8), self.co("L", 90.0, 16), self.co("L", 89.9, 32)]
        r = LM.asymmetry(cs, 100.0, 0.10)
        self.assertEqual(r["up"], 2)          # px 와 같은 숏 제외, band 끝 포함, 밖 제외
        self.assertEqual(r["dn"], 16)         # px 와 같은 롱 제외, band 끝 포함
        self.assertAlmostEqual(r["a"], (2 - 16) / 18.0)

    def test_zero_sum_is_none(self):
        self.assertIsNone(LM.asymmetry([], 100.0)["a"])
        self.assertIsNone(LM.asymmetry([self.co("S", 150.0, 5)], 100.0)["a"])

    def test_call_thresholds(self):
        self.assertEqual(LM.call(0.2), 1)
        self.assertEqual(LM.call(-0.2), -1)
        self.assertEqual(LM.call(0.19), 0)
        self.assertEqual(LM.call(-0.19), 0)
        self.assertEqual(LM.call(None), 0)
        self.assertEqual(LM.call(1.0), 1)


class Daily(unittest.TestCase):
    def make(self):
        bars = [flat(0, 100.0, 100.0), flat(1, 100.0, 110.0), flat(2, 105.0, 110.0)]
        bars += [flat(i, 105.0, 110.0) for i in range(3, 24 * 20)]
        return bars

    def test_only_midnight_close_bars(self):
        snaps = LM.daily_snapshots(self.make())
        self.assertEqual(len(snaps), 20)
        for s in snaps:
            self.assertEqual(s["t"] % 86400, 0)
        self.assertEqual(snaps[0]["t"], 86400)

    def test_ready_after_lookback_bars(self):
        snaps = LM.daily_snapshots(self.make())
        # 봉 인덱스 i 의 스냅샷: 처리한 봉 수 i+1. 13번째 스냅샷(i=311) 미준비, 14번째(i=335) 준비
        self.assertFalse(snaps[12]["ready"])
        self.assertTrue(snaps[13]["ready"])
        self.assertEqual(snaps[13]["t"], 14 * 86400)

    def test_value_hand_computed(self):
        # 봉2 에서 105 로 올라 숏 100.5·101.5·103.5 (750) 쓸림. 남은 숏 109.5(250), 롱 99.5·98.5·96.5 (750)
        s = LM.daily_snapshots(self.make(), bands=(0.10,))[0]
        x = s["by_band"][0.10]
        self.assertEqual(s["px"], 105.0)
        self.assertAlmostEqual(x["up"], 250.0)
        self.assertAlmostEqual(x["dn"], 750.0)
        self.assertAlmostEqual(x["a"], -0.5)
        self.assertEqual(x["call"], -1)

    def test_collect_swept(self):
        bars = self.make()
        snaps, sw = LM.daily_snapshots(bars, collect_swept=True)
        self.assertEqual(len(sw), len(bars))
        self.assertEqual(len(snaps), 20)
        self.assertAlmostEqual(sw[2]["swept_short"], 750.0)
        self.assertEqual(sw[2]["swept_long"], 0.0)
        self.assertEqual(sw[2]["t"], 2 * H)

    def test_ready_counts_from_first_oi(self):
        bars = [flat(i, 100.0, None) for i in range(24)] + [flat(i, 100.0, 100.0) for i in range(24, 24 * 16)]
        snaps = LM.daily_snapshots(bars)
        # oi 첫 봉 = 24. 스냅샷 i=24k-1 의 처리 봉 수 = i-23. i=359 → 336 → 준비
        # 스냅샷 k(0부터)의 봉 i=24k+23. oi 첫 봉 24 → 처리 수 i-23 = 24k. k=13: 312 미준비, k=14: 336 준비
        self.assertFalse(snaps[13]["ready"])
        self.assertTrue(snaps[14]["ready"])


class Stats(unittest.TestCase):
    def test_t_and_halves(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(S.sd(xs), (5 / 3.0) ** 0.5)
        self.assertAlmostEqual(S.tstat(xs), 2.5 / (((5 / 3.0) ** 0.5) / 2))
        self.assertEqual(S.split_halves(xs), (1.5, 3.5))
        self.assertEqual(S.split_halves([1, 2, 3, 4, 5]), (1.5, 4.0))   # 앞 n//2=2일 / 나머지 3일
        self.assertIsNone(S.tstat([1.0]))
        self.assertIsNone(S.tstat([2.0, 2.0]))

    def test_correlations(self):
        self.assertAlmostEqual(S.pearson([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(S.pearson([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertAlmostEqual(S.spearman([1, 2, 3, 4], [1, 4, 9, 100]), 1.0)
        self.assertEqual(S.ranks([5, 5, 1]), [2.5, 2.5, 1.0])
        self.assertIsNone(S.pearson([1, 1, 1], [1, 2, 3]))

    def test_terciles(self):
        pairs = [(i, i / 100.0) for i in range(6)]
        q = S.tercile_means(pairs)
        self.assertEqual([x["n"] for x in q], [2, 2, 2])
        self.assertAlmostEqual(q[0]["mean_r_pct"], 0.5)
        self.assertAlmostEqual(q[2]["mean_r_pct"], 4.5)

    def test_perf_formula(self):
        self.assertAlmostEqual(S.perf_pct(1, 0.02), 1.90)
        self.assertAlmostEqual(S.perf_pct(-1, -0.01), 0.90)
        self.assertAlmostEqual(S.perf_pct(1, -0.01), -1.10)

    def test_contrarian_direction(self):
        f = S.dir_contrarian("rp14")
        base = {"c10": 1, "rp14": 0.05}
        self.assertEqual(f(base), -1)
        self.assertEqual(f({"c10": 1, "rp14": -0.05}), 1)
        self.assertIsNone(f({"c10": 1, "rp14": 0.0}))
        self.assertIsNone(f({"c10": 0, "rp14": 0.05}))      # 주 설정 신호 없는 행은 대조군도 없음
        self.assertIsNone(f({"c10": 1, "rp14": None}))
        self.assertEqual(S.dir_contrarian("rp24")({"c10": -1, "rp24": 0.01}), -1)

    def row(self, coin, t, c10, r24, rp14=0.1, c05=None, c20=None, r72=None):
        return {"coin": coin, "t": t, "c10": c10, "c05": c10 if c05 is None else c05, "c20": c10 if c20 is None else c20,
                "r24": r24, "r72": r72, "rp14": rp14, "rp24": rp14, "a10": 0.5 * c10, "px": 1.0}

    def test_eval_variant_daily_mean(self):
        rows = [self.row("A", 1, 1, 0.02), self.row("B", 1, -1, 0.01),     # 1.90, -1.10 → 0.40
                self.row("A", 2, 1, 0.03), self.row("B", 2, 0, 0.05)]      # 2.90 (B 신호 없음)
        s, daily = S.eval_variant(rows, S.dir_liq("10"), "r24")
        self.assertAlmostEqual(daily[1], 0.40)
        self.assertAlmostEqual(daily[2], 2.90)
        self.assertEqual(s["n_days"], 2)
        self.assertEqual(s["n_signals"], 3)
        self.assertAlmostEqual(s["hit_rate"], 2 / 3.0)
        self.assertAlmostEqual(s["mean"], 1.65)
        self.assertEqual((s["first_half"], s["second_half"]), (daily[1], daily[2]))
        self.assertAlmostEqual(s["gross_mean_pct"], 1.75)

    def test_missing_return_dropped(self):
        rows = [self.row("A", 1, 1, None), self.row("A", 2, 1, 0.02)]
        s, daily = S.eval_variant(rows, S.dir_liq("10"), "r24")
        self.assertEqual(list(daily), [2])

    def test_b14_uses_same_rows_and_diff(self):
        rows = [self.row("A", 1, 1, 0.02, rp14=0.1),      # B14 방향 -1 → 성적 -2.10
                self.row("A", 2, -1, 0.02, rp14=0.1)]     # 방향 -1 → 대조도 -1 → 같은 -2.10
        sm, dm = S.eval_variant(rows, S.dir_liq("10"), "r24")
        sb, db = S.eval_variant(rows, S.dir_contrarian("rp14"), "r24")
        d = S.diff_series(dm, db)
        self.assertAlmostEqual(d[1], 1.90 - (-2.10))
        self.assertAlmostEqual(d[2], 0.0)

    def test_judge(self):
        mk = lambda **k: dict({"t": 2.5, "mean": 0.5, "first_half": 0.4, "second_half": 0.6}, **k)
        good = S.judge(mk(), {"mean": 0.1}, {"mean": 0.3}, {"mean": 0.2})
        self.assertEqual(good["verdict"], "근거 있음")
        self.assertFalse(good["t_ge_3"])
        for bad in (S.judge(mk(t=1.9), {"mean": 0.1}, {"mean": 0.3}, {"mean": 0.2}),
                    S.judge(mk(second_half=-0.1), {"mean": 0.1}, {"mean": 0.3}, {"mean": 0.2}),
                    S.judge(mk(), {"mean": -0.1}, {"mean": 0.3}, {"mean": 0.2}),
                    S.judge(mk(), {"mean": 0.1}, {"mean": -0.3}, {"mean": 0.2})):
            self.assertEqual(bad["verdict"], "근거 없음")
        rev = S.judge(mk(t=-2.5, mean=-0.5, first_half=-0.4, second_half=-0.6), {"mean": 0.1}, {"mean": -0.3}, {"mean": -0.2})
        self.assertTrue(rev["reverse_t_le_minus2"])
        self.assertEqual(rev["verdict"], "근거 없음")

    def test_build_rows_returns_and_past(self):
        # 20일치 시간봉, 가격 = 100 + 시각 인덱스. 닫힌 시각 X 의 종가 조회 검증
        n = 24 * 20
        bars = [bar(i, 100.0 + i, 100.0 + i, 100.0 + i, 100.0) for i in range(n)]
        rows = S.build_rows("X", bars)
        r = rows[0]                                  # 첫 ready 스냅샷: 봉 i=335 닫힘 t=336*H
        self.assertEqual(r["t"], 336 * H)
        px = 100.0 + 335
        self.assertAlmostEqual(r["r24"], (100.0 + 359) / px - 1)
        self.assertAlmostEqual(r["rp24"], px / (100.0 + 311) - 1)
        self.assertIsNone(r["rp14"])                 # 336시간 전 닫힌 시각 = 0 인 봉 없음
        self.assertEqual(r["c10"], 0)
        # 끝쪽 스냅샷은 24시간 뒤 봉이 없어 r24 None
        self.assertIsNone(rows[-1]["r24"])


if __name__ == "__main__":
    unittest.main()
