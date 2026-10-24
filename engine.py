"""WeatherMind: four independently trained local residual forecasting models.
Baseline: Open-Meteo hourly forecast; verification: Open-Meteo historical gridded data.
All targets frozen before verification; model selection uses forward-only validation.
"""
import json, math, os, pathlib, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
ROOT=pathlib.Path(__file__).parent
HISTORY=ROOT/'data/history.json'; PUBLIC=ROOT/'docs/data.json'
LAT=float(os.getenv('WEATHERMIND_LAT') or '41.96'); LON=float(os.getenv('WEATHERMIND_LON') or '-71.55')
FIELDS={'temperature':'temperature_2m','rain':'precipitation','wind':'wind_speed_10m','pressure':'pressure_msl'}
UNITS={'temperature':'°F','rain':'mm','wind':'mph','pressure':'hPa'}

def api(host,params):
    url=host+'?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'WeatherMind-learning/3.0'}),timeout=40) as response:
        return json.load(response)

def hour_bucket(ts): return int(ts[11:13])//6

def adjustment(history, target, field):
    # This is a locally trained, recency-weighted conditional residual model.
    candidates=[r for r in history if r['target']<target and r['actual'].get(field) is not None]
    if len(candidates)<4:return 0.0
    season=int(target[5:7])//3; bucket=hour_bucket(target)
    weights=[]; residuals=[]
    for row in candidates:
        age=max(0,(datetime.fromisoformat(target)-datetime.fromisoformat(row['target'])).total_seconds()/86400)
        w=math.exp(-age/90)
        if int(row['target'][5:7])//3==season:w*=1.7
        if hour_bucket(row['target'])==bucket:w*=1.8
        weights.append(w);residuals.append(row['actual'][field]-row['base'][field])
    correction=sum(w*r for w,r in zip(weights,residuals))/sum(weights)
    correction*=len(candidates)/(len(candidates)+12)
    limit={'temperature':10,'rain':8,'wind':12,'pressure':8}[field]
    return max(-limit,min(limit,correction))

def validate(history,target,field):
    previous=sorted([r for r in history if r['target']<target and r['actual'].get(field) is not None],key=lambda r:r['target'])
    tests=[]
    for i,row in enumerate(previous):
        if i<4:continue
        corrected=row['base'][field]+adjustment(previous[:i],row['target'],field)
        if field=='rain':corrected=max(0,corrected)
        tests.append((abs(corrected-row['actual'][field]),abs(row['base'][field]-row['actual'][field])))
    tests=tests[-40:]
    if len(tests)<12:return 0.0,{'status':'collecting_evidence','tested':len(tests)}
    learner=sum(x for x,y in tests)/len(tests);base=sum(y for x,y in tests)/len(tests)
    approved=learner < base*0.98
    return (adjustment(previous,target,field) if approved else 0.0),{'status':'approved' if approved else 'baseline_safer','tested':len(tests),'test_mae':round(learner,3),'baseline_test_mae':round(base,3)}

def main():
    HISTORY.parent.mkdir(parents=True,exist_ok=True);PUBLIC.parent.mkdir(parents=True,exist_ok=True)
    state=json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    if state.get('location')!=[LAT,LON] or state.get('version')!=3:state={'version':3,'location':[LAT,LON],'forecasts':[]}
    rows=state['forecasts'];now=datetime.now(timezone.utc);hourly=','.join(FIELDS.values())
    # Archive often lags; incomplete verification remains pending and is retried later.
    due=[r for r in rows if r['target']<=(now-timedelta(hours=3)).isoformat(timespec='minutes') and len(r['actual'])<len(FIELDS)]
    for row in due:
        day=row['target'][:10]
        try:
            archive=api('https://archive-api.open-meteo.com/v1/archive',{'latitude':LAT,'longitude':LON,'start_date':day,'end_date':day,'hourly':hourly,'temperature_unit':'fahrenheit','wind_speed_unit':'mph','precipitation_unit':'mm','timezone':'UTC'})['hourly']
            # API timestamps omit UTC suffix; stored target uses +00:00.
            slot=row['target'][:16]
            index=archive['time'].index(slot)
            for key,api_field in FIELDS.items():
                value=archive.get(api_field,[])[index]
                if value is not None:row['actual'][key]=round(float(value),3)
        except Exception as error:
            print('Archive not ready',row['target'],str(error)[:100])
    forecast=api('https://api.open-meteo.com/v1/forecast',{'latitude':LAT,'longitude':LON,'hourly':hourly,'temperature_unit':'fahrenheit','wind_speed_unit':'mph','precipitation_unit':'mm','timezone':'UTC','forecast_days':3})['hourly']
    # One forecast per scheduled run (normally hourly), for 6h ahead, frozen at issuance.
    target=(now+timedelta(hours=6)).replace(minute=0,second=0,microsecond=0)
    target_str=target.isoformat(timespec='minutes')
    if not any(r['target']==target_str for r in rows):
        slot=target.strftime('%Y-%m-%dT%H:%M')
        idx=forecast['time'].index(slot)
        base={k:float(forecast[v][idx]) for k,v in FIELDS.items()}
        pred={};validation={}
        for field in FIELDS:
            delta,check=validate(rows,target_str,field)
            pred[field]=round(max(0,base[field]+delta) if field=='rain' else base[field]+delta,3)
            validation[field]=check
        rows.append({'created':now.isoformat(),'target':target_str,'base':base,'pred':pred,'actual':{},'validation':validation})
    # Keep training history indefinitely, without modifying frozen predictions.
    state['last_run']=now.isoformat();state['units']=UNITS
    state['metrics']={}
    for field in FIELDS:
        checked=[r for r in rows if field in r['actual']]
        state['metrics'][field]={'verified':len(checked),'model_mae':round(sum(abs(r['pred'][field]-r['actual'][field]) for r in checked)/len(checked),3) if checked else None,'baseline_mae':round(sum(abs(r['base'][field]-r['actual'][field]) for r in checked)/len(checked),3) if checked else None}
    HISTORY.write_text(json.dumps(state,indent=2)+'\n');PUBLIC.write_text(json.dumps(state,indent=2)+'\n')
    print('Forecasts',len(rows),'last target',target_str)
if __name__=='__main__':main()
