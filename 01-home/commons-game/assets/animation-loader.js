
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.CommonsAnimationLoader=factory();
})(globalThis,function(){
  'use strict';
  function create({loadJSON,loadImage,concurrency=4}){
    const images=new Map(),manifests=new Map(),queue=[];
    let active=0;
    function pump(){
      queue.sort((a,b)=>a.priority-b.priority);
      while(active<concurrency&&queue.length){
        const next=queue.shift();active++;
        let finished=false;
        const finish=image=>{if(finished)return;finished=true;active--;next.resolve(image||null);pump();};
        try{loadImage(next.url,finish,next.priority===0?'high':'low');}catch(_){finish(null);}
      }
    }
    function image(url,priority){
      if(!images.has(url))images.set(url,new Promise(resolve=>{queue.push({url,priority,resolve});pump();}));
      return images.get(url);
    }
    function directions(src){

      const dirs=Array.isArray(src) ? {all:src}
        : (src?.directions||(Array.isArray(src?.frames)?{all:src.frames}:src?.frames)||{});
      return Object.fromEntries(Object.entries(dirs).filter(([,files])=>Array.isArray(files)&&files.length));
    }
    function record(base,manifest){
      if(!manifest||(!manifest.directions&&!manifest.actions))return null;
      const rec={frames:{},actions:{},tilesTall:manifest.tilesTall||2,cols:manifest.cols||9};
      const walks=Object.entries(manifest.directions||{}).filter(([,files])=>Array.isArray(files)&&files.length);
      for(const [name,src]of Object.entries(manifest.actions||{})){
        const files=directions(src);if(!Object.keys(files).length)continue;
        rec.actions[name.toLowerCase()]={frames:{},files,fps:Number(src?.fps)||8,hold:!!src?.hold,pending:{}};
      }
      if(!walks.length&&!Object.keys(rec.actions).length)return null;
      const walking=[];
      for(const [dir,files]of walks)rec.frames[dir]=new Array(files.length);

      for(const index of [0,1])for(const [dir,files]of walks){
        const selected=index===0?[0]:files.slice(1).map((_,i)=>i+1);
        for(const i of selected)walking.push(image(base+files[i],index).then(im=>{rec.frames[dir][i]=im;return !!im;}));
      }
      rec.walkReady=Promise.all(walking).then(loaded=>loaded.some(Boolean));
      function selected(spec,dir){return spec?.files[dir]?dir:spec?.files.down?'down':spec?.files.all?'all':null;}
      rec.hasAction=(name,dir)=>{
        const spec=rec.actions[String(name).toLowerCase()],key=selected(spec,dir);
        return !!key&&(!Object.hasOwn(spec.frames,key)||spec.frames[key].length>0);
      };
      rec.ensureAction=(name,dir)=>{
        const spec=rec.actions[String(name).toLowerCase()],key=selected(spec,dir);
        if(!key)return Promise.resolve(false);
        if(!spec.pending[key])spec.pending[key]=Promise.all(spec.files[key].map(file=>image(base+file,0)))
          // A missing middle frame must not move the clip's contact time.
          .then(frames=>{spec.frames[key]=frames.every(Boolean)?frames:[];return spec.frames[key].length>0;});
        return spec.pending[key];
      };
      return rec;
    }
    return {load(base){
      if(!manifests.has(base))manifests.set(base,new Promise(resolve=>{
        try{loadJSON(base+'manifest.json',manifest=>{try{resolve(record(base,manifest));}catch(_){resolve(null);}});}catch(_){resolve(null);}
      }));
      return manifests.get(base);
    }};
  }
  return {create};
});
