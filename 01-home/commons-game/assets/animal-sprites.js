
(() => {
  const names=new Set(['animal-cat','animal-fox','animal-owl','animal-snake','animal-bear','animal-bear-cub','animal-black-wolf']);
  const images=new Map();


  let catalog;
  function loadCatalog(){
    return catalog||(catalog=fetch('/assets/animal-atlas.json?v=20260927-2').then(r=>{
      if(!r.ok)throw Error('Animal atlas unavailable');return r.json();
    }).catch(()=>null));
  }
  function sheet(url){
    if(!images.has(url))images.set(url,new Promise(resolve=>loadImg(url,resolve)));
    return images.get(url);
  }
  window.commonsAnimalSprites={
    has:name=>names.has(name),
    load(name,done){
      loadCatalog().then(async data=>{
        const spec=data?.[name];if(!spec){done(null);return;}
        const frames={};
        for(const [dir,list] of Object.entries(spec.directions))
          frames[dir]=await Promise.all(list.map(async f=>({...f,image:await sheet(f.url)})));
        const complete=Object.values(frames).every(row=>row.every(f=>f.image));
        done(complete?{...spec,frames,actions:{},atlas:true}:null);
      }).catch(()=>done(null));
    },
    draw(rec,dir,index,x,y,alpha){
      const row=rec.frames[dir]||rec.frames.down;
      const frame=row?.[Math.max(0,Math.min(index,row.length-1))];
      if(!frame?.image)return false;
      const [sx,sy,sw,sh]=frame.rect,scale=rec.tilesTall*T*CHARSCALE/rec.height;
      const w=sw*scale,h=sh*scale;
      cx.save();cx.imageSmoothingEnabled=false;cx.globalAlpha=alpha??1;cx.translate(Math.round(x),Math.round(y));
      if(frame.flip)cx.scale(-1,1);
      cx.drawImage(frame.image,sx,sy,sw,sh,-w/2,-h,w,h);cx.restore();return true;
    }
  };
})();
