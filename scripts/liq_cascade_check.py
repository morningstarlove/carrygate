"""청산 알림 매매법(SNS 글, 2026-11-18) 실데이터 측정. 제안함 P40. 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/liq_cascade/*.csv (TradingView 수집 2026-11-18, 열 t,o,h,l,c,v, t=UTC 초). 재현: python scripts/liq_cascade_check.py
청산 내역 자체는 과거치를 받을 수 없어(바이낸스는 실시간 흐름만 제공) "급락 + 거래량 급증 봉"을 대용 신호로 쓴다.
결과를 보기 전에 고정한 것:
  신호  : 신호 종목 봉의 (종가/시가−1) 이 직전 W봉 표준편차의 k배 이상 하락 & 거래량이 직전 W봉 평균의 m배 이상
          (k,m) = (2,2)·(3,3)·(4,3) 세 가지를 모두 보고한다. W = 하루치 봉 수. 신호 뒤 12봉은 새 신호 없음
  진입  : 신호 봉 다음 봉 시가 (봉이 닫힌 뒤에야 신호를 알 수 있으므로)
  청산  : H봉 뒤 종가. 장 마감·공백을 넘는 보유는 제외
  비용  : 왕복 0.10% (바이낸스 선물 시장가 0.05%×2, scalp.py VENUES). 주식도 같은 값으로 가정(실제는 더 큼, 개별주 숏 제약 별도)
  롱 전환: 같은 신호에서 롱 / 신호 봉 아래꼬리가 몸통보다 긴 경우만 롱
"""
import os
HERE=os.path.dirname(os.path.abspath(__file__));D=os.path.join(HERE,"..","data","liq_cascade")
import csv,sys,math,statistics as st
def load(n):
    r=list(csv.DictReader(open(os.path.join(D,n+'.csv'))));return {int(x['t']):{k:float(x[k]) for k in 'ohlcv'} for x in r}
def events(sig,ts,k,vm,W,side,sess=None):
    """side=-1: 급락(롱 청산 대용), +1: 급등. 직전 W봉 수익률 표준편차·평균거래량 기준. 신호 뒤 12봉 재진입 금지."""
    out=[];last=-99
    for i in range(W,len(ts)):
        b=sig[ts[i]];r=b['c']/b['o']-1
        win=[sig[ts[j]]['c']/sig[ts[j]]['o']-1 for j in range(i-W,i)]
        sd=st.pstdev(win);av=sum(sig[ts[j]]['v'] for j in range(i-W,i))/W
        if sd>0 and side*r>=k*sd and b['v']>=vm*av and i-last>12:
            out.append(i);last=i
    return out
def fwd(trd,ts,i,H,pos):
    """신호 봉 다음 봉 시가 진입, H봉 뒤 종가 청산. pos=+1 롱 -1 숏. 총수익 %"""
    if i+H>=len(ts): return None
    if ts[i+H]-ts[i]>(ts[1]-ts[0])*H*1.01: return None   # 장 마감·공백 넘는 보유 제외
    e=trd[ts[i+1]]['o'];x=trd[ts[i+H]]['c'];return pos*(x/e-1)*100
def row(name,rs,fee):
    rs=[x for x in rs if x is not None]
    if len(rs)<3: print('  %-34s n=%d'%(name,len(rs)));return
    m=sum(rs)/len(rs);sd=st.stdev(rs);t=m/(sd/math.sqrt(len(rs))) if sd else 0
    print('  %-34s n=%3d 총 %+.3f%% 순 %+.3f%% 승률(순) %3.0f%% t=%+.1f'%(name,len(rs),m,m-fee,100*sum(1 for x in rs if x>fee)/len(rs),t))
def run(signame,trdnames,W,fee,hs,label):
    sig=load(signame)
    for k,vm in ((2,2),(3,3),(4,3)):
        for tn in trdnames:
            trd=load(tn);ts=sorted(set(sig)&set(trd))
            ev=events(sig,ts,k,vm,W,-1)
            print('%s | 신호 %s 급락 ≥%dσ·거래량 ≥%d배 → 매매 %s (왕복 수수료 %.2f%%)'%(label,signame,k,vm,tn,fee))
            for H in hs:
                row('숏 %2d봉 보유'%H,[fwd(trd,ts,i,H,-1) for i in ev],fee)
            # 긴 아래꼬리(몸통보다 아래꼬리가 길다)에서 롱
            wick=[i for i in ev if (min(sig[ts[i]]['o'],sig[ts[i]]['c'])-sig[ts[i]]['l'])>abs(sig[ts[i]]['c']-sig[ts[i]]['o'])]
            for H in hs[1:3]:
                row('전체 롱 %2d봉'%H,[fwd(trd,ts,i,H,1) for i in ev],fee)
                row('아래꼬리 긴 봉만 롱 %2d봉'%H,[fwd(trd,ts,i,H,1) for i in wick],fee)
            up=events(sig,ts,k,vm,W,1)
            row('(대칭) 급등 뒤 롱 %d봉'%hs[1],[fwd(trd,ts,i,hs[1],1) for i in up],fee)
            # 같은 봉 안에서 얼마나 따라갔나 (베타)
            b=[(trd[ts[i]]['c']/trd[ts[i]]['o']-1)/(sig[ts[i]]['c']/sig[ts[i]]['o']-1) for i in ev]
            if b: print('  신호 봉 안 동반 하락 배수(중앙값) %.2f'%st.median(b))
def leadlag(a,b):
    A=load(a);B=load(b);ts=sorted(set(A)&set(B));step=ts[1]-ts[0]
    ra={ts[i]:A[ts[i]]['c']/A[ts[i-1]]['c']-1 for i in range(1,len(ts)) if ts[i]-ts[i-1]==step}
    rb={ts[i]:B[ts[i]]['c']/B[ts[i-1]]['c']-1 for i in range(1,len(ts)) if ts[i]-ts[i-1]==step}
    def corr(x,y):
        mx=sum(x)/len(x);my=sum(y)/len(y);return sum((p-mx)*(q-my) for p,q in zip(x,y))/math.sqrt(sum((p-mx)**2 for p in x)*sum((q-my)**2 for q in y))
    o=[]
    for lag in (0,1,2,3):
        p=[(ra[t],rb[t+lag*step]) for t in ra if t+lag*step in rb]
        o.append('%d봉 뒤 %+.3f'%(lag,corr(*zip(*p))))
    p=[(rb[t],ra[t+step]) for t in rb if t+step in ra];o.append('역방향 1봉 %+.3f'%corr(*zip(*p)))
    print('상관 %s → %s: '%(a,b)+' / '.join(o))
if __name__=='__main__':
    print('=== 코인 5분봉 (10/26~11/18, 23일) ===')
    run('BINANCE_BTCUSDTP_5m',['BINANCE_ETHUSDTP_5m','BINANCE_BTCUSDTP_5m'],288,0.10,[1,3,6,12],'5m')
    print('=== 코인 15분봉 (9/1~11/18, 78일) ===')
    run('BINANCE_BTCUSDTP_15m',['BINANCE_ETHUSDTP_15m'],96,0.10,[1,2,4,8],'15m')
    print('=== 미국 반도체 5분봉 (8/17~11/17, 정규장) ===')
    run('AMEX_SOXL_5m',['NASDAQ_MU_5m','NASDAQ_SNDK_5m'],78,0.10,[1,3,6,12],'5m')
    leadlag('BINANCE_BTCUSDTP_5m','BINANCE_ETHUSDTP_5m');leadlag('AMEX_SOXL_5m','NASDAQ_MU_5m');leadlag('AMEX_SOXL_5m','NASDAQ_SNDK_5m')
    print('=== 코인 1분봉 (11/12~11/18, 5.6일) ===')
    run('BINANCE_BTCUSDTP_1m',['BINANCE_ETHUSDTP_1m'],240,0.10,[1,3,5,15],'1m')
    leadlag('BINANCE_BTCUSDTP_1m','BINANCE_ETHUSDTP_1m')
    for n,H in (('BINANCE_ETHUSDTP_5m',12),('BINANCE_ETHUSDTP_15m',8),('BINANCE_ETHUSDTP_1m',15)):
        d=load(n);ts=sorted(d);r=[-(d[ts[i+H]]['c']/d[ts[i+1]]['o']-1)*100 for i in range(len(ts)-H)]
        print('기준선 %s 아무 때나 숏 %d봉 평균 총 %+.3f%%'%(n,H,sum(r)/len(r)))
