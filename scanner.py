import os,json
from datetime import datetime,time
from zoneinfo import ZoneInfo
import requests,numpy as np,pandas as pd,yfinance as yf

MIN_PRICE,MAX_PRICE=.50,5.00
UNIVERSE_SIZE=80
MIN_DAY_VOLUME=100_000
STATE_FILE='smart_radar_state.json'
SAUDI_TZ=ZoneInfo('Asia/Riyadh'); NY_TZ=ZoneInfo('America/New_York')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN'); CHAT=os.environ.get('TELEGRAM_CHAT_ID'); TOPIC=os.environ.get('TELEGRAM_TOPIC_ID')

def now_saudi(): return datetime.now(SAUDI_TZ)
def now_ny(): return datetime.now(NY_TZ)
def tg(msg):
    if not TOKEN or not CHAT: return False
    d={'chat_id':CHAT,'text':msg,'disable_web_page_preview':True}
    if TOPIC: d['message_thread_id']=TOPIC
    try:
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',data=d,timeout=15); r.raise_for_status(); return bool(r.json().get('ok'))
    except Exception as e: print('Telegram:',e); return False

def load():
    try:
        with open(STATE_FILE,encoding='utf-8') as f: x=json.load(f)
        return x if isinstance(x,dict) else {'alerts':{}}
    except: return {'alerts':{}}

def save(s):
    s['last_run']=now_saudi().isoformat()
    with open(STATE_FILE,'w',encoding='utf-8') as f: json.dump(s,f,ensure_ascii=False,indent=2)

def regular():
    t=now_ny().time(); return time(9,30)<=t<time(16,0)

def universe():
    q=yf.EquityQuery('and',[
        yf.EquityQuery('gt',['intradayprice',MIN_PRICE]),
        yf.EquityQuery('lt',['intradayprice',MAX_PRICE]),
        yf.EquityQuery('gt',['dayvolume',MIN_DAY_VOLUME]),
        yf.EquityQuery('eq',['region','us'])])
    try:
        z=yf.screen(q,sortField='daypercentchange',sortAsc=False,offset=0,size=UNIVERSE_SIZE)
        out=[]
        for a in z.get('quotes',[]):
            s=str(a.get('symbol','')).upper(); p=a.get('regularMarketPrice',a.get('intradayprice'))
            try:p=float(p)
            except:continue
            if s and MIN_PRICE<=p<=MAX_PRICE and all(c.isalnum() or c in '.-' for c in s): out.append(s)
        return list(dict.fromkeys(out))
    except Exception as e: print('Universe:',e); return []

def clean(x):
    if x is None or x.empty:return None
    if isinstance(x.columns,pd.MultiIndex): x=x.copy(); x.columns=x.columns.get_level_values(-1)
    x.columns=[str(c).title() for c in x.columns]
    need=['Open','High','Low','Close','Volume']
    if not all(c in x.columns for c in need):return None
    x=x[need].dropna(subset=['Open','High','Low','Close'])
    return x[~x.index.duplicated(keep='last')].sort_index()

def downloads(symbols):
    a,b={},{}
    for interval,period,target in [('5m','5d',a),('1h','60d',b)]:
        try:
            raw=yf.download(symbols,period=period,interval=interval,auto_adjust=False,prepost=True,progress=False,threads=True)
            for s in symbols:
                try:
                    if isinstance(raw.columns,pd.MultiIndex):
                        x=raw.xs(s,axis=1,level=-1) if s in raw.columns.get_level_values(-1) else raw[s]
                    else:x=raw
                    x=clean(x)
                    if x is not None:target[s]=x
                except:pass
        except Exception as e: print(interval,e)
    return a,b

def ny(x):
    x=x.copy(); idx=pd.DatetimeIndex(x.index)
    if idx.tz is None:idx=idx.tz_localize('UTC')
    x.index=idx.tz_convert(NY_TZ); return x

def rvol(x):
    v=x.Volume.astype(float); m=v.rolling(20).mean()
    return float(v.iloc[-1]/m.iloc[-1]) if len(v)>=20 and m.iloc[-1]>0 else 0

def rebound(s,df):
    if df is None or len(df)<40:return None
    x=ny(df).iloc[:-1]
    if len(x)<30:return None
    a=x.iloc[-1]; prev=x.iloc[-2]; p=float(a.Close); o=float(a.Open); h=float(a.High); l=float(a.Low)
    if not MIN_PRICE<=p<=MAX_PRICE:return None
    peak=float(x.High.tail(20).max()); drop=(peak-p)/peak*100 if peak else 0
    rng=max(h-l,1e-9); lw=min(o,p)-l; pos=(p-l)/rng; rv=rvol(x)
    score=sum([drop>=6,p>o,lw/rng>=.30,pos>=.65,p>float(prev.Close),rv>=1.2,float(x.Low.tail(5).iloc[-1])>=float(x.Low.tail(5).min())])
    if drop<6 or score<4:return None
    return {'symbol':s,'price':p,'drop':drop,'score':score,'rv':rv,'bar':x.index[-1].isoformat(),'absorb':rv>=1.2 and pos>=.65}

def hourly(s,df):
    if df is None or len(df)<30:return None
    x=ny(df).iloc[:-1]; c=x.Close.astype(float); streak=0; changes=[]
    for i in range(len(c)-1,0,-1):
        q=float(c.iloc[i-1]); p=float(c.iloc[i]); ch=(p-q)/q*100 if q else 0
        if ch>=.20:streak+=1; changes.append(ch)
        else:break
    if streak<2:return None
    start=max(0,len(c)-streak-1); sp=float(c.iloc[start]); p=float(c.iloc[-1]); total=(p-sp)/sp*100 if sp else 0
    return {'symbol':s,'price':p,'streak':streak,'total':total,'bar':x.index[-1].isoformat(),'prices':[float(v) for v in c.iloc[start:].tail(8)]}

def main():
    print('HASSAN SMART RADAR |',now_saudi())
    if not regular(): print('خارج السوق الأمريكي'); return
    state=load(); syms=universe(); print('مرشحون:',len(syms))
    if not syms: save(state); return
    d5,d1=downloads(syms); alerts=state.setdefault('alerts',{})
    for s in syms:
        try:
            r=rebound(s,d5.get(s))
            if r:
                k=f"{s}|ارتداد|{r['bar']}"
                if k not in alerts and tg(f"⤴️ بداية ارتداد\n\nالسهم: {s}\nالسعر: ${r['price']:.2f}\nالهبوط السابق: {r['drop']:.1f}%\nقوة الارتداد: {r['score']}/7\nامتصاص البيع: {'نعم ✅' if r['absorb'] else 'محتمل'}\nالحجم النسبي: {r['rv']:.2f}x\nالوقت: {now_saudi().strftime('%H:%M:%S')} 🇸🇦"):
                    alerts[k]=now_saudi().isoformat()
            h=hourly(s,d1.get(s))
            if h:
                k=f"{s}|صعود|{h['streak']}|{h['bar']}"
                if k not in alerts and tg(f"📈 صعود متواصل\n\nالسهم: {s}\nالسعر: ${h['price']:.2f}\nالساعات الصاعدة المتتالية: {h['streak']} 🟢\nالمسار: {' → '.join(f'${p:.2f}' for p in h['prices'])}\nإجمالي الصعود: {h['total']:.1f}%\nالوقت: {now_saudi().strftime('%H:%M:%S')} 🇸🇦"):
                    alerts[k]=now_saudi().isoformat()
        except Exception as e: print(s,e)
    if len(alerts)>3000:
        for k in list(alerts)[:500]: del alerts[k]
    save(state)

if __name__=='__main__':main()
