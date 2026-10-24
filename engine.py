"""WeatherMind: NOAA forecasts, observed verification and guarded residual learning."""
import json,math,os,pathlib,urllib.request,urllib.error
from datetime import datetime,timedelta,timezone
ROOT=pathlib.Path(__file__).parent
HISTORY=ROOT/'data/history.json';PUBLIC=ROOT/'docs/data.json'
AGENT=os.getenv('WEATHERMIND_USER_AGENT','WeatherMind educational project (github.com/Sleepyhead401/Weathermind-)')
FIELDS=('temperature','wind')
UNITS={'temperature':'°F','wind':'mph'}
def utc(s):return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
def api(url):
    req=urllib.request.Request(url,headers={'User-Agent':AGENT,'Accept':'application/geo+json'})
    with urllib.request.urlopen(req,timeout=35) as response:return json.load(response)
def f(c):return c*1.8+32
def mph(ms):return ms*2.2369362921
def locations():
    raw=os.getenv('WEATHERMIND_LOCATIONS')
    if raw:
        parsed=json.loads(raw)
        return [{'name':str(p.get('name','Location')),'lat':float(p['lat']),'lon':float(p['lon'])} for p in parsed]
    home={'name':'North Smithfield, RI','lat':float(os.getenv('WEATHERMIND_LAT','41.96')),'lon':float(os.getenv('WEATHERMIND_LON','-71.55'))}
    endpoint=os.getenv('WEATHERMIND_REGISTRY_URL','').rstrip('/')
    token=os.getenv('WEATHERMIND_REGISTRY_TOKEN','')
    if not endpoint or not token:return [home]
    try:
        req=urllib.request.Request(endpoint+'/locations',headers={'Authorization':'Bearer '+token,'User-Agent':AGENT})
        with urllib.request.urlopen(req,timeout=25) as response:remote=json.load(response)
        result=[home];seen={key(home)}
        for loc in remote[:80]:
            candidate={'name':str(loc.get('name','GPS area')),'lat':float(loc['lat']),'lon':float(loc['lon'])}
            if key(candidate) not in seen:result.append(candidate);seen.add(key(candidate))
        return result
    except Exception as error:
        print('Location registry unavailable; running home location only:',str(error)[:120])
        return [home]
def key(loc):return f"{loc['lat']:.3f},{loc['lon']:.3f}"
def baseline(period):
    out={}
    for field,source,conv in [('temperature','temperature',f),('wind','windSpeed',mph)]:
        raw=period.get(source,{})
        val=raw.get('value') if isinstance(raw,dict) else None
        if val is not None:out[field]=round(conv(float(val)),3)
    return out
def residual(rows,target,field):
    eligible=[r for r in rows if r.get('source')=='NWS' and r['target']<target and field in r.get('actual',{}) and field in r.get('base',{})]
    if len(eligible)<4:return 0.0
    weights=[];errors=[]
    for r in eligible:
        age=max(0,(utc(target)-utc(r['target'])).total_seconds()/86400)
        w=math.exp(-age/90)
        if utc(target).hour//6==utc(r['target']).hour//6:w*=1.8
        weights.append(w);errors.append(r['actual'][field]-r['base'][field])
    limit=10 if field=='temperature' else 12
    return max(-limit,min(limit,sum(w*e for w,e in zip(weights,errors))/sum(weights)*len(eligible)/(len(eligible)+12)))
def validate(rows,target,field):
    previous=sorted([r for r in rows if r.get('source')=='NWS' and r['target']<target and field in r.get('actual',{}) and field in r.get('base',{})],key=lambda r:r['target'])
    tests=[]
    for i,r in enumerate(previous):
        if i<4:continue
        p=r['base'][field]+residual(previous[:i],r['target'],field)
        tests.append((abs(p-r['actual'][field]),abs(r['base'][field]-r['actual'][field])))
    tests=tests[-40:]
    if len(tests)<12:return 0,{'status':'collecting_evidence','tested':len(tests)}
    learner=sum(a for a,b in tests)/len(tests);base=sum(b for a,b in tests)/len(tests)
    good=learner<base*.98
    return (residual(previous,target,field) if good else 0),{'status':'approved' if good else 'baseline_safer','tested':len(tests),'test_mae':round(learner,3),'baseline_test_mae':round(base,3)}
def targets(now):
    first=(now+timedelta(hours=6)).replace(minute=0,second=0,microsecond=0)
    return [('6h',first)]+[(str(d)+'d',(now+timedelta(days=d)).replace(minute=0,second=0,microsecond=0)) for d in range(1,8)]
def find_period(periods,target):
    for p in periods:
        if utc(p['startTime'])<=target<utc(p['endTime']):return p
    return None
def observed(stations):
    records=[]
    for url in stations[:3]:
        try:
            for feature in api(url+'/observations?limit=200').get('features',[]):
                p=feature.get('properties',{})
                try:timestamp=utc(p['timestamp'])
                except (KeyError,TypeError,ValueError):continue
                values={}
                for field,source,conv in [('temperature','temperature',f),('wind','windSpeed',mph)]:
                    raw=p.get(source) or {}
                    if raw.get('value') is not None:values[field]=round(conv(float(raw['value'])),3)
                if values:records.append((timestamp,values))
        except (urllib.error.URLError,ValueError,KeyError) as error:print('Station unavailable',str(error)[:100])
    return records
def verify(rows,records,now):
    for r in rows:
        if r.get('source')!='NWS' or not r.get('base'):continue
        target=utc(r['target'])
        if target>now-timedelta(minutes=45):continue
        candidates=[(abs((t-target).total_seconds()),v) for t,v in records if abs((t-target).total_seconds())<=2700]
        if not candidates:continue
        values=min(candidates,key=lambda x:x[0])[1]
        for field in r['base']:
            if field not in r.setdefault('actual',{}) and field in values:r['actual'][field]=values[field]
def manage_storage(state):
    limit=int(os.getenv('WEATHERMIND_HISTORY_LIMIT_MB','50'))*1024*1024
    cutoff=datetime.now(timezone.utc)-timedelta(days=90)
    for r in state['forecasts']:
        if r.get('base') and all(f in r.get('actual',{}) for f in r['base']) and utc(r['target'])<cutoff:r.pop('validation',None)
    def size():return len(json.dumps(state,separators=(',',':')).encode())
    removed=0
    if size()>limit*.9:
        eligible=sorted([r for r in state['forecasts'] if r.get('base') and all(f in r.get('actual',{}) for f in r['base'])],key=lambda r:r['target'])
        while size()>limit*.75 and eligible:
            state['forecasts'].remove(eligible.pop(0));removed+=1
    storage=state.setdefault('storage',{})
    storage.update({'limit_bytes':limit,'used_bytes_estimate':size(),'last_deleted_count':removed,'total_deleted':storage.get('total_deleted',0)+removed})
def main():
    HISTORY.parent.mkdir(parents=True,exist_ok=True);PUBLIC.parent.mkdir(parents=True,exist_ok=True)
    state=json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    state.setdefault('forecasts',[]);state.setdefault('lifetime',{})
    state['version']=5;state['source']='NWS';state['units']=UNITS
    now=datetime.now(timezone.utc);locs=locations();state['locations']=locs
    # Legacy forecasts remain archived but are not used to train the NOAA models.
    for loc in locs:
        loc_key=key(loc)
        rows=[r for r in state['forecasts'] if r.get('location_key')==loc_key and r.get('source')=='NWS']
        try:point=api(f"https://api.weather.gov/points/{loc['lat']:.4f},{loc['lon']:.4f}")['properties']
        except Exception as error:
            print('Skipping unsupported location:',loc_key,str(error)[:100]);continue
        hourly=api(point['forecastHourly'])['properties']['periods']
        stations=api(point['observationStations']).get('observationStations',[])
        verify(rows,observed(stations),now)
        for horizon,target in targets(now):
            target_str=target.isoformat(timespec='minutes')
            if any(r['target']==target_str and r.get('horizon')==horizon for r in rows):continue
            period=find_period(hourly,target)
            if not period:continue
            base=baseline(period)
            if not base:continue
            pred={};checks={}
            # Keep horizons separate for training and validation.
            history=[r for r in rows if r.get('horizon')==horizon]
            for field,value in base.items():
                delta,check=validate(history,target_str,field)
                pred[field]=round(max(0,value+delta) if field=='wind' else value+delta,3)
                checks[field]=check
            record={'source':'NWS','location_key':loc_key,'horizon':horizon,'created':now.isoformat(),'target':target_str,'base':base,'pred':pred,'actual':{},'validation':checks}
            state['forecasts'].append(record);rows.append(record)
    for r in state['forecasts']:
        if r.get('source')!='NWS':continue
        for field,actual in r.get('actual',{}).items():
            if field not in r.get('base',{}) or r.get('_counted_'+field):continue
            identity=r.get('location_key','unknown')+'|'+r.get('horizon','6h')+'|'+field
            total=state['lifetime'].setdefault(identity,{'count':0,'model_abs_error_sum':0,'baseline_abs_error_sum':0})
            total['count']+=1;total['model_abs_error_sum']+=abs(r['pred'][field]-actual);total['baseline_abs_error_sum']+=abs(r['base'][field]-actual)
            r['_counted_'+field]=True
    state['metrics']={}
    for identity,total in state['lifetime'].items():
        if not isinstance(total,dict) or 'count' not in total:continue
        n=total['count']
        state['metrics'][identity]={'verified':n,'model_mae':round(total['model_abs_error_sum']/n,3) if n else None,'baseline_mae':round(total['baseline_abs_error_sum']/n,3) if n else None}
    state['last_run']=now.isoformat()
    manage_storage(state)
    payload=json.dumps(state,separators=(',',':'))+'\n'
    HISTORY.write_text(payload);PUBLIC.write_text(payload)
    print('NOAA locations',len(locs),'forecast records',len(state['forecasts']))
if __name__=='__main__':main()
