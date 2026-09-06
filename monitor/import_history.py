"""One-time audited migration from original hit events and WOOD's Markdown history.
Does not edit original events/50U ledgers or create notification events.
"""
import json,re
from store import connect
from portfolio import register,add_sample
VALUES={
 'CAP':(.0004409,1583,None),'CRC':(.0002397,1604,None),
 'SURPLUS':(.0002475,320,31.6),'69ELEVEN':(.0002438,183,29.6),
 'DINO':(.0003021,2081,None),'PUMPCAT':(.0003168,801,None),
 'BIKETYSON':(.0002696,508,23.2),'IDIOT':(.0002192,110,18.1),
 '豹拉':(.000366,1135,None),'傻孩子':(.0003269,330,30.8),'QUBY':(.0002753,270,30.2)}

def main():
    with connect() as db: events=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM events')]
    count=0
    for p in events:
        if p['kind']!='hit':continue
        name=re.sub(r'^.*?新命中\s*','',p['text'].split('/')[0]).strip()
        if name not in VALUES:continue
        price,holders,upper=VALUES[name]
        p=dict(p,name=name,price=price,fomo_holders=holders,fomo_ratio_lower=p['fomo_ratio'],fomo_ratio_upper=upper,
               note='从首次命中原始日志迁移；价格为Windvane实测，市值保留原GMGN初筛值。原50U日志不改写。',
               price_estimated=False)
        p['observed_at']=re.sub(r' (\+\d\d:\d\d)$',r'\1',p['observed_at']).replace(' ','T',1)
        count+=register(p)
    ca='0x6635c082c14b4206751600d96fe50428815c9fa9'
    count+=register(dict(name='WOOD',chain='robinhood',ca=ca,observed_at='2026-09-06T00:20:00+08:00',price=.0003414,
      market_cap=341000,age_days=1,source='windvane',verified=True,fomo_ratio_lower=17.1,fomo_ratio_upper=18.6,fomo_holders=220,
      note='时间约00:20，首次完整核验。此前22:58未核验且价格估算，不作为本次100U回放入场。'))
    for t,price,source,estimated,extras in [
      ('08:00',.000908,'fomo',False,{}),('09:01',.0007213,'fomo',True,{}),
      ('11:05',.0007053,'windvane',False,{'fomo_ratio_lower':14.5,'fomo_ratio_upper':15.6,'fomo_holders':200,'confirmation':True}),
      ('11:15',.000722,'fomo',False,{})]:
        add_sample(dict(chain='robinhood',ca=ca,observed_at='2026-09-06T'+t+':00+08:00',price=price,source=source,
          price_estimated=estimated,note='WOOD原始日志已有观测；时间为原记录约值。' + ('按已确认1B供应量估算。' if estimated else ''),**extras))
    print('已迁移首次合格基线：',count)

if __name__=='__main__':main()
