
(() => {
  let returnTo=null;
  function sit(prop){
    const spots=prop.action?.spots||[];
    const free=spots.filter(s=>!HERE.some(p=>String(p.name).toLowerCase()!==String(ME).toLowerCase()&&p.action==='sit'&&Math.hypot((p.dx??p.x)-s.x,(p.dy??p.y)-s.y)<.8));
    if(!free.length){showTip(prop.label,'Every seat here is occupied.','',undefined,undefined);return false;}

    if(ACTION.name)finishAction();
    const origin={x:P.x,y:P.y,room:ROOM};
    if(hits(origin.x,origin.y)){
      const floor=[];
      for(let y=.5;y<R.rows.length;y+=.5)for(let x=.5;x<R.rows[0].length;x+=.5)if(!hits(x,y))floor.push({x,y});
      const nearest=floor.sort((a,b)=>Math.hypot(a.x-origin.x,a.y-origin.y)-Math.hypot(b.x-origin.x,b.y-origin.y))[0];
      if(!nearest)return false;
      Object.assign(origin,nearest);
    }
    const spot=free.slice().sort((a,b)=>Math.hypot(P.x-a.x,P.y-a.y)-Math.hypot(P.x-b.x,P.y-b.y))[0];
    P.x=spot.x;P.y=spot.y;P.dir=spot.dir||'down';hideTip();
    if(!playAction('sit',{hold:true})){P.x=origin.x;P.y=origin.y;return false;}
    returnTo=origin;centreCam(true);return true;
  }
  function stand(){
    if(returnTo&&returnTo.room===ROOM){P.x=returnTo.x;P.y=returnTo.y;TARGET=null;}
    returnTo=null;
  }
  window.commonsSeats={sit,stand};
})();
