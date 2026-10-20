

((root)=>{
  const stable=v=>JSON.stringify(v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(stable(v[k]))])):Array.isArray(v)?v.map(x=>JSON.parse(stable(x))):v);
  function create(storage,identity){
    const key='commons-pending-action:'+identity.toLowerCase();let memory=null,transient=false;
    const read=()=>{if(transient)return memory;try{const row=JSON.parse(storage.getItem(key)||'null');return row?.version===1?row:null;}catch{return memory;}};
    const save=row=>{memory=row;try{row?storage.setItem(key,JSON.stringify(row)):storage.removeItem(key);transient=false;}catch{transient=true;}};
    return {read,clear:()=>save(null),prepare(path,body,room){
      const safe={...body};delete safe.key;delete safe.by;delete safe.as;delete safe.password;delete safe.request_id;
      const pending=read();
      if(pending){
        if(pending.path!==path||pending.room!==room||stable(pending.body)!==stable(safe))throw Error('Check your unfinished action before starting another.');
        return {...safe,request_id:pending.id};
      }
      const row={version:1,path,body:safe,room,id:body.request_id||crypto.randomUUID(),created:Date.now()};save(row);
      return {...safe,request_id:row.id};
    }};
  }
  const paths=new Set(['/api/game/action','/api/craft','/api/share','/api/equip','/api/unequip','/api/forage','/api/rook','/api/loose','/api/howl','/api/verb','/api/say',
    '/api/cast','/api/creel','/api/hunt','/api/approach','/api/plant','/api/tend','/api/harvest','/api/name-companion','/api/follow-companion','/api/outfit','/api/place','/api/place-made']);
  const controllers=new Map();
  function controller(identity){if(!controllers.has(identity)){let storage;try{storage=root.localStorage;}catch{}controllers.set(identity,create(storage,identity));}return controllers.get(identity);}
  async function reliableFetch(path,options){
    if(!paths.has(path)||options?.method!=='POST')return root.fetch(path,options);
    const body=JSON.parse(options.body),identity=body.by||body.as;
    if(!identity||!body.key)return root.fetch(path,options);
    const control=controller(identity),request=control.prepare(path,body,body.room||'');
    const response=await root.fetch(path,{...options,body:JSON.stringify({...body,...request})});


    await response.clone().json();
    if(response.ok||response.status>=400&&response.status<500&&![401,403].includes(response.status))control.clear();
    return response;
  }
  const api={create,controller,fetch:reliableFetch};if(typeof module!=='undefined')module.exports=api;else {root.commonsActionRetry=api;root.commonsReliableFetch=reliableFetch;}
})(globalThis);
