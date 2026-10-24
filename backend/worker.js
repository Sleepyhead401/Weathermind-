// Cloudflare Worker + KV: optional location registry for WeatherMind.
// Bind KV namespace as LOCATIONS, and set REGISTRY_ADMIN_TOKEN secret.
// Browser registration only after user explicitly presses the GPS button.
// Coordinates rounded to 0.02 degrees (~2 km); never store raw GPS fixes.
const cors={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type','Content-Type':'application/json','Cache-Control':'no-store'};
function snap(x){return Math.round(x/0.02)*0.02}
export default {
 async fetch(request,env){
  const url=new URL(request.url);
  if(request.method==='OPTIONS')return new Response(null,{headers:cors});
  if(url.pathname==='/register'&&request.method==='POST'){
   let data;try{data=await request.json()}catch{return new Response('Invalid JSON',{status:400,headers:cors})}
   const lat=Number(data.lat),lon=Number(data.lon);
   if(!Number.isFinite(lat)||!Number.isFinite(lon)||lat<24||lat>50||lon< -125||lon> -66)return new Response('US coordinates required',{status:400,headers:cors});
   const a=snap(lat),b=snap(lon),id=a.toFixed(2)+','+b.toFixed(2);
   await env.LOCATIONS.put(id,JSON.stringify({name:'GPS area '+id,lat:a,lon:b,updated:new Date().toISOString()}));
   return new Response(JSON.stringify({registered:true,area:id}),{headers:cors});
  }
  if(url.pathname==='/locations'&&request.method==='GET'){
   if(!env.REGISTRY_ADMIN_TOKEN||request.headers.get('Authorization')!=='Bearer '+env.REGISTRY_ADMIN_TOKEN)return new Response('Unauthorized',{status:401});
   const items=[];let cursor;
   do{
    const page=await env.LOCATIONS.list({cursor,limit:1000});
    for(const k of page.keys){const v=await env.LOCATIONS.get(k.name,'json');if(v)items.push(v)}
    cursor=page.list_complete?undefined:page.cursor;
   }while(cursor);
   return new Response(JSON.stringify(items),{headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  }
  return new Response('Not found',{status:404});
 }
};
