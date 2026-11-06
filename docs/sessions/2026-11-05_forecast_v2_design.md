# 2026-11-05 예측 시스템 v2 설계 — 크기 예측 중심, 방향은 채점만

## 사용자 요청
"예측 시스템 발전시키는 쪽으로 설계해봐" (11/4 닮은 과거 예측 기각 P30 뒤).

## 한 일
1. 가격에서 계산되는 상황 변수 10개를 같은 자료(바이낸스 선물 12종목 2시간봉 307일)로 사전 선별 — 방향(IC·3분위 롱숏·시점 묶음 t)과 크기(|수익| 상관) 둘 다, 상·하반기 나눠서.
2. 선별에서 가장 좋아 보인 반전 변수 2개(r12·60봉 범위)를 **미리 정한** 규칙으로 만들어 시험(변동성 문지기 4가지, 롱만·숏만).
3. 설계서 `docs/FORECAST_PLAN.md` 작성, 제안함 P31 등록.

## 숫자
| 항목 | 결과 |
|---|---|
| 방향 IC 최고 | 60봉 범위 위치 −0.070, r12 −0.059 (둘 다 상·하반기 음수) |
| 그 반전 규칙 매매(비용 0.10%) | 2,783건 승률 53.2% 평균 **−4.8bp**, 상반 +10.9 → 하반 **−28.0**, 묶음 t −0.80 |
| 변동성 문지기 붙여도 | 고변동성만 −10.5bp(상반 +26 → 하반 −52), 저변동성만 −10.3bp |
| 상대강도 r12−BTC | IC 상반 −0.146 → 하반 +0.009 — 뒤집힘 |
| 요일·시각 | 상·하반 부호 바뀜 — 잡음 |
| **크기 예측 rv12** | 상위 3분위 \|12h 수익\| 2.15% vs 하위 1.58%, 비율 1.36, **상반 1.44 · 하반 1.41** |
| 변동성 예측에 보태는 변수 | 거래량 z 0.107, 변동성비 0.086 |

## 결론
- 방향은 가격만으로는 못 맞힌다(16변형 + 10변수 + 규칙 6개 모두). 크기(변동성)는 안정적으로 맞는다.
- v2 = ① 크기 예측기 ② 방향 예측기 3개(기준선 포함) 매일 채점만 ③ 60일 뒤 판정. 쓰임은 기존 트랙의 **포지션 크기 조절·문지기**(판정 PASS 뒤 제안함). 자세한 것은 설계서.

## 사용자 결정 대기
A 구현 시작(1세션, 그 뒤 60일 기록) / B 설계만 보관 / C 크기 조절만 기존 트랙 변형으로 바로 제안.

## Routine
변경 없음.

## 스크립트 (numpy 필요, 저장소 밖에서)
<details><summary>feature_screen.py</summary>

```python
# -*- coding: utf-8 -*-
"""예측 v2 설계용 사전 선별: 가격에서 바로 계산되는 '상황' 변수 10개가 12h 뒤 수익(방향)·크기(|수익|)를 얼마나 설명하는가.
규칙 고정: 2시간봉, H=6봉, 12시간 간격 평가, 변수별 3분위(종목별 과거 300봉 기준, 미래 정보 없음)."""
import json, sys, math
import numpy as np
S = sys.argv[1]
raw = json.load(open(S + "/bars_1h.json"))
H = 6; COST = 0.0010
grid = sorted({t - 3600 for b in raw.values() for t in b["t"] if (t // 3600) % 2 == 1})
gi = {t: k for k, t in enumerate(grid)}
names = [s.split(":")[1].replace("USDT.P", "") for s in raw]
n = len(names)
C = np.full((n, len(grid)), np.nan); V = np.full_like(C, np.nan); Hh = np.full_like(C, np.nan); Ll = np.full_like(C, np.nan)
for ci, b in enumerate(raw.values()):
    for t, o, h, l, c, v in zip(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]):
        if (t // 3600) % 2 == 1:
            k = gi[t - 3600]; C[ci, k] = c; V[ci, k] = (V[ci, k] if not np.isnan(V[ci, k]) else 0) + v
            Hh[ci, k] = h if np.isnan(Hh[ci, k]) else max(Hh[ci, k], h); Ll[ci, k] = l if np.isnan(Ll[ci, k]) else min(Ll[ci, k], l)
        else:
            k = gi.get(t)
            if k is not None:
                V[ci, k] = (0 if np.isnan(V[ci, k]) else V[ci, k]) + v; Hh[ci, k] = h; Ll[ci, k] = l
C, V, Hh, Ll = C[:, :-1], V[:, :-1], Hh[:, :-1], Ll[:, :-1]
T = C.shape[1]
R = np.concatenate([np.full((n, 1), np.nan), np.diff(np.log(C), axis=1)], axis=1)
FWD = np.full_like(C, np.nan); FWD[:, :T - H] = np.log(C[:, H:] / C[:, :T - H])
btc = names.index("BTC")

def roll_sum(x, w):
    out = np.full_like(x, np.nan); cs = np.nancumsum(x, axis=1)
    out[:, w:] = cs[:, w:] - cs[:, :-w]; return out
def roll_std(x, w):
    m = roll_sum(x, w) / w; m2 = roll_sum(x * x, w) / w; return np.sqrt(np.maximum(m2 - m * m, 0))
def roll_max(x, w):
    out = np.full_like(x, np.nan)
    for k in range(w, x.shape[1]): out[:, k] = np.nanmax(x[:, k - w + 1:k + 1], axis=1)
    return out
def roll_min(x, w):
    out = np.full_like(x, np.nan)
    for k in range(w, x.shape[1]): out[:, k] = np.nanmin(x[:, k - w + 1:k + 1], axis=1)
    return out

feat = {}
feat["r12 (24h 수익)"] = roll_sum(R, 12)
feat["r60 (5일 수익)"] = roll_sum(R, 60)
feat["r3 (6h 수익)"] = roll_sum(R, 3)
rv12 = roll_std(R, 12); rv60 = roll_std(R, 60)
feat["변동성비 rv12/rv60"] = rv12 / rv60
feat["거래량z (6봉/60봉)"] = (roll_sum(V, 6) / 6) / (roll_sum(V, 60) / 60)
feat["BTC r12"] = np.tile(roll_sum(R, 12)[btc], (n, 1))
feat["상대강도 r12−BTC"] = feat["r12 (24h 수익)"] - feat["BTC r12"]
feat["60봉 범위 위치"] = (C - roll_min(Ll, 60)) / (roll_max(Hh, 60) - roll_min(Ll, 60))
pv = roll_sum(C * V, 12) / roll_sum(V, 12); feat["VWAP12 이탈"] = np.log(C / pv)
feat["24h 전 같은 시각 수익 r(k−12,1)"] = np.concatenate([np.full((n, 12), np.nan), R[:, :-12]], axis=1)

ks = np.arange(400, T - H, H)
hour = (np.array(grid[:-1])[ks] // 3600) % 24
dow = ((np.array(grid[:-1])[ks] // 86400) + 4) % 7

def tstat(x): return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 else float("nan")

print("%-32s %7s %7s %7s | %8s %8s %8s | %8s" % ("변수", "IC", "IC상반", "IC하반", "3분위L-S bp", "묶음t", "적중", "|r| 상관"))
rows = []
for nm, F in feat.items():
    ic_rows = []; ls_rows = []; hit_rows = []; mag_rows = []
    for k in ks:
        # 종목별 과거 300봉 분위 (k 포함 안 함)
        hist = F[:, k - 300:k]; cur = F[:, k]; y = FWD[:, k]
        ok = ~np.isnan(cur) & ~np.isnan(y) & ~np.isnan(hist).all(axis=1)
        if ok.sum() < 6: continue
        lo = np.nanpercentile(hist[ok], 33.3, axis=1); hi = np.nanpercentile(hist[ok], 66.7, axis=1)
        c_ = cur[ok]; y_ = y[ok]
        top = c_ >= hi; bot = c_ <= lo
        ls = (y_[top].mean() if top.any() else 0) - (y_[bot].mean() if bot.any() else 0)
        ls_rows.append((k, ls, top.any() and bot.any()))
        ic_rows.append((k, c_, y_))
        mag_rows.append((c_, np.abs(y_)))
    allc = np.concatenate([r[1] for r in ic_rows]); ally = np.concatenate([r[2] for r in ic_rows]); allk = np.concatenate([[r[0]] * len(r[1]) for r in ic_rows])
    # 분위 기반 IC(스피어만 근사: 순위)
    def ic(c, y):
        return float(np.corrcoef(np.argsort(np.argsort(c)), np.argsort(np.argsort(y)))[0, 1])
    half = allk < np.median(allk)
    icv, ic1, ic2 = ic(allc, ally), ic(allc[half], ally[half]), ic(allc[~half], ally[~half])
    lsv = np.array([r[1] for r in ls_rows if r[2]])
    hit = float((np.sign(allc) == np.sign(ally)).mean())
    magc = float(np.corrcoef(np.concatenate([m[0] for m in mag_rows]), np.concatenate([m[1] for m in mag_rows]))[0, 1])
    print("%-32s %7.4f %7.4f %7.4f | %8.1f %8.2f %8.4f | %8.4f" % (nm, icv, ic1, ic2, lsv.mean() * 1e4, tstat(lsv), hit, magc))
    rows.append(dict(feature=nm, ic=round(icv, 4), ic_h1=round(ic1, 4), ic_h2=round(ic2, 4), ls_bp=round(float(lsv.mean() * 1e4), 1), ls_t=round(tstat(lsv), 2), hit=round(hit, 4), mag_corr=round(magc, 4)))

# 크기(변동성) 예측: rv12 3분위별 12h 뒤 |수익| 과 비용 초과 비율
print("\n변동성 예측 (rv12 분위 → 12h 뒤 |수익|):")
tops = []; bots = []; th1 = []; th2 = []
for i, k in enumerate(ks):
    hist = rv12[:, k - 300:k]; cur = rv12[:, k]; y = np.abs(FWD[:, k])
    ok = ~np.isnan(cur) & ~np.isnan(y)
    lo = np.nanpercentile(hist[ok], 33.3, axis=1); hi = np.nanpercentile(hist[ok], 66.7, axis=1)
    t_ = y[ok][cur[ok] >= hi]; b_ = y[ok][cur[ok] <= lo]
    tops += list(t_); bots += list(b_)
    (th1 if i < len(ks) / 2 else th2).append((t_.mean() if len(t_) else np.nan, b_.mean() if len(b_) else np.nan))
tops, bots = np.array(tops), np.array(bots)
print("  상위 분위 평균 |r| %.2f%%  하위 %.2f%%  비율 %.2f | 상반기 비율 %.2f 하반기 %.2f" % (tops.mean() * 100, bots.mean() * 100, tops.mean() / bots.mean(),
      np.nanmean([a for a, b in th1]) / np.nanmean([b for a, b in th1]), np.nanmean([a for a, b in th2]) / np.nanmean([b for a, b in th2])))
print("  |r| > 0.5%% 비율: 상위 %.0f%%  하위 %.0f%%" % ((tops > .005).mean() * 100, (bots > .005).mean() * 100))

# 시각 효과
print("\nUTC 시각(2h 봉 시작)별 12h 뒤 평균 수익 bp (상반/하반):")
for hr in sorted(set(hour)):
    m = hour == hr; y = FWD[:, ks[m]]; half = np.arange(m.sum()) < m.sum() / 2
    print("  %02d:00 KST %02d:00  전체 %6.1f  상반 %6.1f  하반 %6.1f  n=%d" % (hr, (hr + 9) % 24, np.nanmean(y) * 1e4, np.nanmean(y[:, half]) * 1e4, np.nanmean(y[:, ~half]) * 1e4, m.sum()))
print("\n요일별 (UTC):")
for d in range(7):
    m = dow == d; y = FWD[:, ks[m]]; half = np.arange(m.sum()) < m.sum() / 2
    print("  %d 전체 %6.1f 상반 %6.1f 하반 %6.1f" % (d, np.nanmean(y) * 1e4, np.nanmean(y[:, half]) * 1e4, np.nanmean(y[:, ~half]) * 1e4))
json.dump(rows, open(S + "/feature_screen.json", "w"), ensure_ascii=False, indent=1)
```
</details>
<details><summary>rule_check.py</summary>

```python
import sys, math, numpy as np
src=open(sys.argv[0].replace('rule_check.py','feature_screen.py')).read().split('ks = np.arange')[0]
exec(src)
ks = np.arange(400, T - H, H)
r12=feat["r12 (24h 수익)"]; rp=feat["60봉 범위 위치"]
def tstat(x): return float(x.mean()/(x.std(ddof=1)/math.sqrt(len(x))))
def test(label, gate):
    per=[]; cnt=0; trades=[]
    for k in ks:
        h1=r12[:,k-300:k]; h2=rp[:,k-300:k]; hv=rv12[:,k-300:k]
        ok=~np.isnan(r12[:,k])&~np.isnan(rp[:,k])&~np.isnan(FWD[:,k])&~np.isnan(rv12[:,k])
        lo1=np.nanpercentile(h1,33.3,axis=1); hi1=np.nanpercentile(h1,66.7,axis=1)
        lo2=np.nanpercentile(h2,33.3,axis=1); hi2=np.nanpercentile(h2,66.7,axis=1)
        hiv=np.nanpercentile(hv,66.7,axis=1); lov=np.nanpercentile(hv,33.3,axis=1)
        g = ok & (gate(rv12[:,k],hiv,lov))
        L = g&(r12[:,k]<=lo1)&(rp[:,k]<=lo2); Sh = g&(r12[:,k]>=hi1)&(rp[:,k]>=hi2)
        p=np.concatenate([FWD[L,k]-COST, -FWD[Sh,k]-COST])
        if len(p): per.append((k,p.mean())); trades+=list(p)
    per=np.array(per); tr=np.array(trades); half=per[:,0]<np.median(per[:,0])
    print("%-28s 거래 %5d 시점 %4d | 평균 %6.1fbp 승률 %.3f | 상반 %6.1f 하반 %6.1f | 묶음t %5.2f" % (label,len(tr),len(per),tr.mean()*1e4,(tr>0).mean(),per[half,1].mean()*1e4,per[~half,1].mean()*1e4,tstat(per[:,1])))
test("반전(r12·범위 하위/상위 3분위)", lambda v,hi,lo: np.ones_like(v,bool))
test("  + 고변동성만(rv12 상위)", lambda v,hi,lo: v>=hi)
test("  + 저변동성만(rv12 하위)", lambda v,hi,lo: v<=lo)
test("  + 중간 이상", lambda v,hi,lo: v>lo)
# 롱만 / 숏만
def test_side(label, side):
    per=[]; trades=[]
    for k in ks:
        h1=r12[:,k-300:k]; h2=rp[:,k-300:k]
        ok=~np.isnan(r12[:,k])&~np.isnan(rp[:,k])&~np.isnan(FWD[:,k])
        lo1=np.nanpercentile(h1,33.3,axis=1); hi1=np.nanpercentile(h1,66.7,axis=1)
        lo2=np.nanpercentile(h2,33.3,axis=1); hi2=np.nanpercentile(h2,66.7,axis=1)
        m = ok&(r12[:,k]<=lo1)&(rp[:,k]<=lo2) if side>0 else ok&(r12[:,k]>=hi1)&(rp[:,k]>=hi2)
        p=side*FWD[m,k]-COST
        if len(p): per.append((k,p.mean())); trades+=list(p)
    per=np.array(per); tr=np.array(trades); half=per[:,0]<np.median(per[:,0])
    print("%-28s 거래 %5d | 평균 %6.1fbp 승률 %.3f | 상반 %6.1f 하반 %6.1f | 묶음t %5.2f" % (label,len(tr),tr.mean()*1e4,(tr>0).mean(),per[half,1].mean()*1e4,per[~half,1].mean()*1e4,tstat(per[:,1])))
test_side("  롱만", 1); test_side("  숏만", -1)
print("기간 전체 12h 평균 수익(매수보유) %.1fbp" % (np.nanmean(FWD[:,ks])*1e4))
```
</details>
