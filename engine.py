"""WeatherMind NOAA/NWS six-hour forecast learner. No Open-Meteo requests."""
import json, math, os, pathlib, urllib.request, urllib.error, time
from datetime import datetime, timedelta, timezone
ROOT=pathlib.Path(__file__).parent
HISTORY=ROOT/'data/history.json'
PUBLIC=ROOT/'docs/data.json'
LAT=float(os.getenv('WEATHERMIND_LAT','41.96'))
LON=float(os.getenv('WEATHERMIND_LON','-71.55'))
AGENT=os.getenv('WEATHERMIND_USER_AGENT','WeatherMind (personal weather project; contact: github.com/Sleepyhead401/Weathermind-)')
FIELDS=('temperature','wind')
UNITS={'temperature':'°F','wind':'mph'}

def api(url):
    req=urllib.request.Request(url,headers={'User-Agent':AGENT,'Accept':'application/geo+json'})
    with urllib.request.urlopen(req,timeout=35) as response:
        return json.load(response)

def fahrenheit(c): return c*9/5+32
def mph(ms): return ms*2.2369362921
def utc(s): return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
def period_value(period,key):
    value=period.get(key,{}).get('value')
    if value is None:return None
    if key=='temperature':return fahrenheit(float(value))
    if key=='windSpeed':return mph(float(value))
    return None

def forecast_hour(periods,target):
    for p in periods:
        start=utc(p['startTime']);end=utc(p['endTime'])
        if start<=target<end:return p
    raise ValueError('No NOAA forecast period covers target')

def correction(rows,target,field):
    previous=[r for r in rows if r.get('source')=='NWS' and r['target']<target and field in r.get('actual',{}) and field in r.get('base',{})]
    if len(previous)<4:return 0
    weights=[];errors=[]
    for r in previous:
        age=(utc(target)-utc(r['target'])).total_seconds()/86400
        weight=math.exp(-max(0,age)/90)
        if utc(r['target']).hour//6==utc(target).hour//6:weight*=1.8
        weights.append(weight);errors.append(r['actual'][field]-r['base'][field])
    return max(-10,min(10,sum(w*e for w,e in zip(weights,errors))/sum(weights)*len(previous)/(len(previous)+12)))

def validate(rows,target,field):
    previous=sorted((r for r in rows if r.get('source')=='NWS' and r['target']<target and field in r.get('actual',{}) and field in r.get('base',{})),key=lambda r:r['target'])
    tests=[]
    for i,r in enumerate(previous):
        if i<4:continue
        predicted=r['base'][field]+correction(previous[:i],r['target'],field)
        tests.append((abs(predicted-r['actual'][field]),abs(r['base'][field]-r['actual'][field])))
    tests=tests[-40:]
    if len(tests)<12:return 0,{'status':'collecting_evidence','tested':len(tests)}
    learner=sum(x for x,y in tests)/len(tests)
    baseline=sum(y for x,y in tests)/len(tests)
    approved=learner<baseline*.98
    return (correction(previous,target,field) if approved else 0),{'status':'approved' if approved else 'baseline_safer','tested':len(tests),'test_mae':round(learner,3),'baseline_test_mae':round(baseline,3)}

def manage_storage(state):
    limit=int(os.getenv('WEATHERMIND_HISTORY_LIMIT_MB','50'))*1024*1024
    rows=state['forecasts']
    cutoff=datetime.now(timezone.utc)-timedelta(days=90)
    compacted=0
    for r in rows:
        if r.get('actual') and utc(r['target'])<cutoff:
            if r.pop('validation',None) is not None:compacted+=1
    def size():return len(json.dumps(state,separators=(',',':')).encode())
    deleted=0
    if size()>limit*.9:
        eligible=sorted((r for r in rows if all(k in r.get('actual',{}) for k in r.get('base',{})) and r.get('base')),key=lambda r:r['target'])
        while size()>limit*.75 and eligible:
            rows.remove(eligible.pop(0));deleted+=1
    storage=state.setdefault('storage',{})
    storage.update({'limit_bytes':limit,'used_bytes_estimate':size(),'last_compacted_count':compacted,'last_deleted_count':deleted,'total_deleted':storage.get('total_deleted',0)+deleted})
    if compacted or deleted:storage['last_cleanup']=datetime.now(timezone.utc).isoformat()

def main():
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    PUBLIC.parent.mkdir(parents=True,exist_ok=True)
    state=json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    if state.get('location')!=[LAT,LON]:
        # Preserve previous records rather than silently discarding them.
        state.setdefault('forecasts',[])
    state['version']=4;state['location']=[LAT,LON];state['source']='NWS'
    rows=state.setdefault('forecasts',[])
    now=datetime.now(timezone.utc)
    point=api(f'https://api.weather.gov/points/{LAT},{LON}')['properties']
    hourly=api(point['forecastHourly'])['properties']['periods']
    # Verify using actual station observations, not forecast or reanalysis.
    station_urls=api(point['observationStations'])['observationStations']
    observations=[]
    for station in station_urls[:4]:
        try:
            data=api(station+'/observations?limit=150')['features']
            observations.extend(data)
        except (urllib.error.URLError,ValueError,KeyError) as error:
            print('Station unavailable:',str(error)[:120])
    # Choose nearest observation to each target within 45 minutes.
    for row in rows:
        if row.get('source')!='NWS' or not row.get('base') or row.get('actual'):continue
        target=utc(row['target'])
        if target>now-timedelta(minutes=45):continue
        candidates=[]
        for obs in observations:
            p=obs.get('properties',{})
            try:
                t=utc(p['timestamp'])
                if abs((t-target).total_seconds())<=2700:candidates.append((abs((t-target).total_seconds()),p))
            except (ValueError,TypeError,KeyError):continue
        if not candidates:continue
        p=min(candidates,key=lambda x:x[0])[1]
        actual={}
        for field,obs_key,convert in [('temperature','temperature',fahrenheit),('wind','windSpeed',mph)]:
            value=p.get(obs_key,{}).get('value')
            if value is not None:actual[field]=round(convert(float(value)),3)
        if actual:row['actual']=actual;row['observation_time']=p.get('timestamp')
    target=(now+timedelta(hours=6)).replace(minute=0,second=0,microsecond=0)
    target_str=target.isoformat(timespec='minutes')
    if not any(r.get('source')=='NWS' and r['target']==target_str for r in rows):
        period=forecast_hour(hourly,target)
        base={}
        for field,key in [('temperature','temperature'),('wind','windSpeed')]:
            val=period_value(period,key)
            if val is not None:base[field]=round(val,3)
        if not base:raise ValueError('NWS forecast lacks supported numeric fields')
        pred={};checks={}
        for field,value in base.items():
            delta,check=validate(rows,target_str,field)
            pred[field]=round(max(0,value+delta) if field=='wind' else value+delta,3)
            checks[field]=check
        rows.append({'source':'NWS','created':now.isoformat(),'target':target_str,'base':base,'pred':pred,'actual':{},'validation':checks})
    # Count verified errors exactly once and retain cumulative summaries after pruning.
    totals=state.setdefault('lifetime',{})
    for row in rows:
        if row.get('source')!='NWS':continue
        for field,value in row.get('actual',{}).items():
            if field not in row.get('base',{}) or row.get('_counted_'+field):continue
            total=totals.setdefault(field,{'count':0,'model_abs_error_sum':0.0,'baseline_abs_error_sum':0.0})
            total['count']+=1
            total['model_abs_error_sum']+=abs(row['pred'][field]-value)
            total['baseline_abs_error_sum']+=abs(row['base'][field]-value)
            row['_counted_'+field]=True
    state['metrics']={}
    for field,total in totals.items():
        n=total['count']
        state['metrics'][field]={'verified':n,'model_mae':round(total['model_abs_error_sum']/n,3) if n else None,'baseline_mae':round(total['baseline_abs_error_sum']/n,3) if n else None}
    state['last_run']=now.isoformat();state['units']=UNITS
    manage_storage(state)
    payload=json.dumps(state,separators=(',',':'))+'\n'
    HISTORY.write_text(payload);PUBLIC.write_text(payload)
    print('NOAA forecast saved:',target_str,'records:',len(rows))
if __name__=='__main__':main()
