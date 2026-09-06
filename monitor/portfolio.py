"""Auditable price observations and retrospective 100U strategy comparison."""
import json
import math
import time
from datetime import datetime,timezone,timedelta
from store import connect

def timestamp(value):
    if not isinstance(value,str): raise ValueError('observed_at 必须包含时区')
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('观测时间必须包含时区')
    if d.timestamp()>time.time()+300: raise ValueError('不能录入未来价格')
    return d.astimezone(timezone(timedelta(hours=8))).isoformat(),d.timestamp()

def number(value, name, low=0, high=float('inf')):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError('无效数值：'+name)
    return value

def identity(chain,ca):
    if not isinstance(chain,str) or not isinstance(ca,str) or len(ca)<32: raise ValueError('缺少完整链与CA')
    chain={'bnb':'bsc','rh':'robinhood','sol':'solana','eth':'ethereum'}.get(chain.lower(),chain.lower())
    return chain+':'+(ca.lower() if ca.startswith('0x') else ca)

def normalized_sample(sample):
    sample=dict(sample)
    sample['observed_at'],_=timestamp(sample['observed_at'])
    number(sample.get('price'),'price',1e-30)
    if not isinstance(sample.get('source'),str) or not sample['source']: raise ValueError('必须记录价格来源')
    for k in ('fomo_ratio_lower','fomo_ratio_upper'):
        if sample.get(k) is not None:number(sample[k],k,0,100)
    if sample.get('fomo_holders') is not None:number(sample['fomo_holders'],'fomo_holders',0)
    if sample.get('fomo_ratio_lower') is not None and sample.get('fomo_ratio_upper') is not None and sample['fomo_ratio_upper']<sample['fomo_ratio_lower']:
        raise ValueError('Fomo占比区间上下界矛盾')
    for key in ('confirmation','price_estimated','watch_eligible'):
        if key in sample and not isinstance(sample[key],bool):raise ValueError('标记必须为布尔值')
    if 'watch_eligible' in sample:
        if not isinstance(sample.get('watch_round'),str) or not sample['watch_round'].strip():
            raise ValueError('清单资格判断必须包含watch_round')
        if not isinstance(sample.get('eligibility_reason'),str) or not sample['eligibility_reason'].strip():
            raise ValueError('清单资格判断必须说明原因')
    sample.setdefault('confirmation',False)
    sample.setdefault('price_estimated',False)
    return sample

def register(meta):
    meta=dict(meta)
    tid=identity(meta.get('chain'),meta.get('ca'))
    if meta.get('verified') is not True or meta.get('source')!='windvane':raise ValueError('基线必须通过Windvane核验')
    number(meta.get('age_days'),'age_days',1,7)
    number(meta.get('market_cap'),'market_cap',200000,500000)
    number(meta.get('fomo_ratio_lower',meta.get('fomo_ratio')),'fomo_ratio_lower',15,100)
    if not meta.get('name'):raise ValueError('必须记录代币名称')
    if 'fomo_ratio_lower' not in meta:meta['fomo_ratio_lower']=meta['fomo_ratio']
    first=normalized_sample(meta)
    first['confirmation']=True
    meta['observed_at']=first['observed_at']
    with connect() as db:
        old=db.execute('SELECT metadata FROM tokens WHERE id=?',(tid,)).fetchone()
        if old:
            original=json.loads(old['metadata'])
            if original['observed_at']!=meta['observed_at'] or original['price']!=meta['price']:
                raise ValueError('已有首次基线，不允许覆盖')
            return False
        db.execute('INSERT INTO tokens VALUES (?,?)',(tid,json.dumps(meta,ensure_ascii=False)))
        db.execute('INSERT INTO samples VALUES (?,?,?)',(tid,first['observed_at'],json.dumps(first,ensure_ascii=False)))
    return True

def add_sample(sample):
    data=normalized_sample(sample)
    tid=identity(data.get('chain'),data.get('ca'))
    with connect() as db:
        token=db.execute('SELECT metadata FROM tokens WHERE id=?',(tid,)).fetchone()
        if not token:raise ValueError('请先登记首次合格基线')
        meta=json.loads(token['metadata'])
        if timestamp(data['observed_at'])[1]<timestamp(meta['observed_at'])[1]:raise ValueError('后续采样不能早于首次基线')
        old=db.execute('SELECT payload FROM samples WHERE token_id=? AND observed_at=?',(tid,data['observed_at'])).fetchone()
        if old:
            saved=json.loads(old['payload'])
            keys=('price','fomo_ratio_lower','fomo_ratio_upper','fomo_holders','source','price_estimated',
                  'watch_eligible','watch_round','eligibility_reason')
            if any(saved.get(k)!=data.get(k) for k in keys):raise ValueError('同一时刻已有不同观测，禁止覆盖历史')
            return False
        prior=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM samples WHERE token_id=?',(tid,))]
        confirmed=[timestamp(p['observed_at'])[1] for p in prior if p.get('confirmation')]
        if data['confirmation'] and any(abs(timestamp(data['observed_at'])[1]-t)<7200 for t in confirmed):data['confirmation']=False
        db.execute('INSERT INTO samples VALUES (?,?,?)',(tid,data['observed_at'],json.dumps(data,ensure_ascii=False)))
    return True

def fee_profile(chain):
    chain=chain.lower()
    if chain in ('sol','solana'):
        schedule='solana';label='按单笔金额分档';network='Solana网络费由Fomo承担'
        rule='<5U：0.10U；5–47.5U：2%；47.5–190U：0.95U；≥190U：0.5%'
    elif chain in ('bsc','bnb','robinhood','rh','base','monad','ethereum','eth'):
        schedule='evm';label='每笔0.5% + 网络费待核验';network='网络费未核验，未计入'
        rule='买入及每笔卖出各0.5%；网络费另计'
    else:
        raise ValueError('该链尚无已核验费用规则：'+chain)
    return dict(schedule=schedule,label=label,rule=rule,network=network,
                exclusions='代币买卖税、池费及价格冲击未核验，未计入；未应用邀请码折扣或特殊代币优惠',
                source='https://help.fomo.family/en/articles/14436214-trading-fees-on-fomo',
                checked_at='2026-09-06',basis='使用当前官方平台费规则回放，非历史成交实际费用',complete=False)

def watch_state(observations):
    """Three failed screening rounds hide a token without erasing history."""
    rounds={}
    for sample in observations:
        if isinstance(sample.get('watch_eligible'),bool) and sample.get('watch_round'):
            rounds[sample['watch_round']]=sample
    ordered=sorted(rounds.values(),key=lambda item:timestamp(item['observed_at'])[1])
    streak=0
    for sample in reversed(ordered):
        if sample['watch_eligible']: break
        streak+=1
    latest=ordered[-1] if ordered else None
    status='removed' if streak>=3 else 'dropping' if streak else 'active'
    return {'status':status,'ineligible_streak':streak,
            'reason':latest.get('eligibility_reason') if streak and latest else '',
            'last_round':latest.get('watch_round') if latest else None}

def archive_state(meta, observations, watch):
    """Return the first terminal archive trigger while preserving every observation."""
    confirmed=[sample for sample in observations if sample.get('confirmation')]
    lower_streak=0
    for previous,current in zip(confirmed,confirmed[1:]):
        lower_streak=lower_streak+1 if current['price']<previous['price'] else 0
        if lower_streak>=2:
            decline=(current['price']/meta['price']-1)*100
            return {'archived':True,'reason':'连续2个确认轮次收低，上升趋势未恢复',
                    'kind':'trend_break','archived_at':current['observed_at'],
                    'trigger_price':current['price'],'trigger_change_percent':decline}
    if watch['status']=='removed':
        return {'archived':True,'reason':watch['reason'] or '连续3轮不再符合筛选规则',
                'kind':'eligibility','archived_at':observations[-1]['observed_at'],
                'trigger_price':observations[-1]['price'],
                'trigger_change_percent':(observations[-1]['price']/meta['price']-1)*100}
    return {'archived':False}

def fomo_exit_state(observations):
    """A fully-below-15% interval is an immediate, persistent full-exit signal."""
    hit=next((sample for sample in observations
              if sample.get('fomo_ratio_upper') is not None
              and sample['fomo_ratio_upper']<15),None)
    if not hit:return {'triggered':False}
    return {'triggered':True,'reason':'Fomo 持仓占比确认低于15% · 清仓全部信号',
            'triggered_at':hit['observed_at'],'ratio_lower':hit.get('fomo_ratio_lower'),
            'ratio_upper':hit.get('fomo_ratio_upper'),'trigger_price':hit['price']}

def report(buy_fee=.06,sell_fee=.06,official=True):
    from simulation import simulate
    number(buy_fee,'buy_fee',0,.99);number(sell_fee,'sell_fee',0,.99)
    with connect() as db:
        tokens=[(r['id'],json.loads(r['metadata'])) for r in db.execute('SELECT * FROM tokens')]
        samples=[(r['token_id'],json.loads(r['payload'])) for r in db.execute('SELECT * FROM samples')]
        archives={r['token_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM archives')}
    rows=[];history=[]
    for tid,meta in tokens:
        observations=sorted([p for key,p in samples if key==tid],key=lambda p:timestamp(p['observed_at'])[1])
        profile=fee_profile(meta['chain'])
        result=simulate(observations,principal=100,buy_fee=buy_fee,sell_fee=sell_fee,fee_schedule=profile['schedule'] if official else None)
        latest=observations[-1]
        watch=watch_state(observations)
        archive_record=archives.get(tid)
        archive={k:v for k,v in archive_record.items() if k!='simulation'} if archive_record else archive_state(meta,observations,watch)
        if archive['archived'] and not archive_record:
            archive_record=dict(archive,simulation=result)
            with connect() as db:db.execute('INSERT OR IGNORE INTO archives VALUES (?,?)',(tid,json.dumps(archive_record,ensure_ascii=False)))
        if archive_record and archive_record.get('simulation'):result=archive_record['simulation']
        fomo_exit=fomo_exit_state(observations)
        row={'id':tid,'name':meta['name'],'chain':meta['chain'],'ca':meta['ca'],'entry':meta,
                     'latest':latest,'awaiting_sample':len(observations)==1,'stale':time.time()-timestamp(latest['observed_at'])[1]>7200,
                     'samples':observations,'simulation':result,'fees':profile,'watch':watch,'archive':archive,
                     'fomo_exit':fomo_exit}
        if archive['archived']: history.append(row)
        else: rows.append(row)
    if rows:
        newest=max(rows,key=lambda row:timestamp(row['entry']['observed_at'])[1])['id']
        for row in rows: row['latest_entry']=row['id']==newest
    rows.sort(key=lambda r:timestamp(r['latest']['observed_at'])[1],reverse=True)
    history.sort(key=lambda r:timestamp(r['archive']['archived_at'])[1],reverse=True)
    return {'principal':100,'fee_mode':'official_platform' if official else 'custom','buy_fee':buy_fee,'sell_fee':sell_fee,'tokens':rows,
            'history':history,
            'removed':[{'id':r['id'],'name':r['name'],'reason':r['archive']['reason']} for r in history],
            'scope':'依据已记录采样回放；非真实成交、非连续行情。未核验实际gas、税费与滑点。'}
