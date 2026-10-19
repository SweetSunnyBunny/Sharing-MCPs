
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root&&root.document)api.mount(root);
})(typeof window==='undefined'?null:window,function(){
  'use strict';
  const OUTDOORS=new Set(['hearth','garden','woods','roof','canyon-overlook',
    'dt-abandoned-mine','dt-campfire','dt-open-plains','dt-plains-north','dt-plains-north2',
    'dt-town-square','dusty-river','enchanted-forest-main','enchanted-forest-north',
    'enchanted-forest-right','fey-wilds-main','fey-wilds-north1','fey-wilds-north2',
    'saloon-district','village-square','wild-edge','wizards-tower-outside']);
  const THEMES={halloween:'🎃',christmas:'🎄',valentines:'💗',st_patricks:'☘️',easter:'🥚',birthday:'🎂'};


  const ANCHORS={
    hearth:[['garland',.654,.466,.125,.06],['wreath',.693,.552,.048,.07],['display',.650,.608,.060,.094]],
    kitchen:[['garland',.565,.030,.21,.065],['wreath',.903,.736,.040,.062],['display',.292,.368,.052,.084]],
    livingroom:[['garland',.370,.025,.19,.065],['wreath',.855,.150,.05,.075],['display',.155,.730,.065,.10]],
    bedroom:[['garland',.585,.060,.17,.06],['display',.045,.805,.052,.085]],
    bathroom:[['garland',.324,.030,.17,.06]],
    nest:[['garland',.660,.030,.17,.06]],
    studio:[['garland',.715,.028,.17,.06],['display',.032,.790,.050,.085]],
    'director-studio':[['garland',.035,.030,.17,.06],['display',.020,.820,.052,.085]],
    study:[['garland',.715,.028,.17,.06],['display',.065,.635,.052,.085]],
    'story-nest':[['garland',.040,.030,.17,.06],['display',.900,.780,.052,.085]],
    garden:[['garland',.415,.075,.17,.06],['display',.280,.055,.06,.10]],
    woods:[['garland',.426,.020,.15,.06],['display',.335,.035,.06,.10]],
    roof:[['garland',.415,.070,.17,.06],['display',.190,.770,.06,.10]],
    'the-crossroads':[['garland',.360,.120,.18,.065],['display',.436,.316,.055,.087]],
    'village-square':[['garland',.150,.315,.17,.06],['display',.760,.735,.065,.10]],
    'dt-town-square':[['garland',.310,.380,.16,.06],['display',.650,.735,.06,.10]]
  };
  function isOutdoors(room){
    for(const key of ['holiday_outdoors','outdoors','outdoor'])if(typeof room?.[key]==='boolean')return room[key];
    return OUTDOORS.has(room?.id);
  }
  function placements(room){
    const rows=room?.rows||[],width=Math.max(0,...rows.map(row=>row.length));
    if(!width||!rows.length)return [];
    const source=ANCHORS[room.id]||[['garland',.42,.025,.16,.06],['display',.04,.79,.052,.085]];
    return source.map(([kind,x,y,w,h])=>({kind,x,y,w,h}));
  }
  function frameFor(metadata,holiday,kind){
    const column=metadata?.columns?.indexOf(holiday),frame=metadata?.frames?.[kind];
    if(column===undefined||column<0||!frame)return null;
    return {...frame,x:column*metadata.cellWidth+frame.x};
  }

  function keepsakesFor(state,room,keepsakes){
    const holiday=state?.current?.id;if(!holiday||!room?.id)return [];
    return (keepsakes||[]).filter(row=>row&&row.holiday===holiday&&row.room===room.id&&row.art);
  }
  function drawKeepsakes(ctx,room,tile,state,keepsakes,images){
    let drawn=0;
    for(const row of keepsakesFor(state,room,keepsakes)){
      const image=images?.[row.id];if(!image)continue;
      const w=(row.w||1)*tile,h=(row.h||1)*tile,x=row.x*tile,y=(row.y+1)*tile-h;
      ctx.drawImage(image,x,y,w,h);drawn++;
    }
    return drawn;
  }
  function snowEnabled(state,room){return !!(state?.current?.id==='christmas'&&(state.current.snow??state.snow)&&isOutdoors(room));}
  function snowParticles(width,height,clock,reduced){
    const count=Math.min(220,Math.max(75,Math.round(width*height/7500))),time=reduced?0:clock/1000;
    return Array.from({length:count},(_,i)=>{
      const seed=(i*9301+49297)%233280,speed=11+(seed%13);
      return {x:((seed/233280*width)+Math.sin(time*.35+i)*7+width)%width,
        y:(((i*137+53)%height)+time*speed)%height,radius:1.1+(i%4)*.55,alpha:.45+(i%4)*.08};
    });
  }

  function replacedKinds(state,room,keepsakes){
    return new Set(keepsakesFor(state,room,keepsakes).map(row=>row.replaces).filter(Boolean));
  }
  function drawDecorations(ctx,room,tile,state,metadata,atlas,keepsakes){
    if(!state?.current||!atlas||!metadata)return 0;
    const width=Math.max(...room.rows.map(row=>row.length))*tile,height=room.rows.length*tile;
    const replaced=replacedKinds(state,room,keepsakes);
    const items=placements(room).filter(item=>!replaced.has(item.kind));ctx.save();ctx.globalAlpha=.96;
    for(const item of items){
      const frame=frameFor(metadata,state.current.id,item.kind);if(!frame)continue;
      const bx=item.x*width,by=item.y*height,bw=item.w*width,bh=item.h*height;
      if(item.kind==='garland'){

        const swags=Math.max(1,Math.round(bw/(bh*frame.w/frame.h))),each=bw/swags;
        for(let i=0;i<swags;i++)ctx.drawImage(atlas,frame.x,frame.y,frame.w,frame.h,bx+i*each,by,each,bh);
      }else ctx.drawImage(atlas,frame.x,frame.y,frame.w,frame.h,bx,by,bw,bh);
    }
    ctx.restore();return items.length;
  }
  function drawSnow(ctx,room,tile,state,clock,reduced,hidden){
    if(hidden||!snowEnabled(state,room))return 0;
    const width=Math.max(...room.rows.map(row=>row.length))*tile,height=room.rows.length*tile;
    ctx.save();ctx.fillStyle='#edf5ff';

    for(let i=0;i<14;i++){
      ctx.globalAlpha=.17;
      const x=(i*.071+.018)*width,y=i%2?height*.96:height*.035;
      ctx.beginPath();ctx.ellipse(x,y,width*.018,Math.max(2,tile*.10),0,0,Math.PI*2);ctx.fill();
    }
    const flakes=snowParticles(width,height,clock,reduced);
    for(const flake of flakes){ctx.globalAlpha=flake.alpha;ctx.beginPath();ctx.arc(flake.x,flake.y,flake.radius,0,Math.PI*2);ctx.fill();}
    ctx.restore();return flakes.length;
  }
  function mount(win){
    const doc=win.document;let state=null,metadata=null,atlas=null,inflight=null,revision=0,keepsakes=[];const keepsakeImages={};
    const motion=win.matchMedia('(prefers-reduced-motion: reduce)');
    const room=()=>typeof R!=='undefined'?R:null;
    const make=(tag,text,parent)=>{const node=doc.createElement(tag);if(text!==undefined)node.textContent=text;if(parent)parent.append(node);return node;};
    const style=make('style',undefined,doc.head);
    style.textContent=[
      '#holiday-dialog{background:#1c1517;color:#eadfcf;border:1px solid #91704b;border-radius:14px;padding:22px;width:min(550px,calc(100vw - 24px));max-height:85dvh;overflow:auto;font:15px Georgia,serif;touch-action:pan-y;margin:auto}',
      '#holiday-dialog::backdrop{background:#080608b0}#holiday-dialog h2{font-size:1.2rem;margin-bottom:12px}#holiday-dialog p{line-height:1.5;margin:10px 0}',
      '#holiday-dialog ul{padding-left:20px;margin:12px 0}#holiday-dialog li{margin:8px 0;line-height:1.4}#holiday-dialog .holiday-actions{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}',
      '#holiday-dialog button,#holiday-dialog select{background:#38272c;color:#fff0da;border:1px solid #91704b;border-radius:7px;padding:9px;font:inherit;cursor:pointer}#holiday-dialog button:disabled{opacity:.55;cursor:wait}',
      '#holiday-dialog .holiday-close{float:right}#holiday-dialog [aria-live]{min-height:1.5em;color:#e7c989}#holidayBtn{font-size:17px}'
    ].join('');
    const button=make('button','🗓',doc.getElementById('tools'));
    button.id='holidayBtn';button.title='Holidays and decorations';button.setAttribute('aria-label','Holidays and decorations');
    const dialog=make('dialog',undefined,doc.body);dialog.id='holiday-dialog';dialog.setAttribute('aria-labelledby','holiday-heading');
    let statusNode=null;
    function accept(next){
      const changed=JSON.stringify(state)!==JSON.stringify(next);state=next;
      button.textContent=THEMES[state?.current?.id]||'🗓';
      button.title=(state?.current?.name?state.current.name+' · ':'')+'Holidays and decorations';
      if(dialog.open&&changed)renderDialog();
    }
    async function refresh(){
      if(inflight)return inflight;
      const token=revision;
      const query=new URLSearchParams({room:typeof ROOM==='undefined'?'hearth':ROOM,as:typeof ME==='undefined'?'':ME,key:typeof MYKEY==='undefined'?'':MYKEY});
      inflight=win.fetch('/api/holidays?'+query,{cache:'no-store'}).then(async response=>{
        if(!response.ok)throw Error('The holiday calendar could not be loaded.');
        const next=await response.json();if(token===revision)accept(next);return state;
      }).catch(error=>{if(statusNode&&dialog.open)statusNode.textContent=error.message;return state;}).finally(()=>{inflight=null;});
      return inflight;
    }
    async function change(mode,holiday){
      const controls=[...dialog.querySelectorAll('button,select')];controls.forEach(node=>node.disabled=true);
      try{
        const response=await win.fetch('/api/holidays',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({by:ME,key:MYKEY,mode,...(holiday?{holiday}:{})})});
        const next=await response.json();if(!response.ok)throw Error(next.error||'The decoration setting could not be saved.');
        revision++;accept(next.holiday||next);
        statusNode.textContent=mode==='off'?'Decorations are off across the world.':mode==='hold'?'These decorations will stay until you change them.':'The holiday calendar is choosing decorations again.';
      }catch(error){statusNode.textContent=error.message;}
      finally{dialog.querySelectorAll('button,select').forEach(node=>node.disabled=false);}
    }
    function dates(entry){
      const format=value=>{const [year,month,day]=String(value||'').split('-').map(Number);return year&&month&&day?new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(Date.UTC(year,month-1,day))):value;};
      return format(entry.start)+' – '+format(entry.end);
    }
    function renderDialog(){
      dialog.replaceChildren();
      const close=make('button','Close',dialog);close.className='holiday-close';close.onclick=()=>dialog.close();
      const heading=make('h2','Holidays around the Commons',dialog);heading.id='holiday-heading';
      make('p',state?.current?THEMES[state.current.id]+' '+state.current.name+' decorations are up.':state?.mode==='off'?'Decorations are off.':'The world has a quiet stretch between holidays.',dialog);
      if(state?.current)make('p',state.current.description,dialog);
      if(state?.mode==='hold')make('p','Held until you choose another setting.',dialog);
      else if(state?.next)make('p','Next: '+state.next.name+' begins '+dates(state.next).split(' – ')[0]+'.',dialog);
      if(state?.can_manage){
        const actions=make('div',undefined,dialog);actions.className='holiday-actions';
        const auto=make('button','Use holiday calendar',actions);auto.onclick=()=>change('auto');
        const off=make('button','Take decorations down',actions);off.onclick=()=>change('off');
        const select=make('select',undefined,actions);select.setAttribute('aria-label','Holiday to keep up');
        for(const entry of state.calendar||[]){const option=make('option',entry.name,select);option.value=entry.id;}
        select.value=state.current?.id||state.scheduled?.id||'halloween';
        const hold=make('button','Keep these up',actions);hold.onclick=()=>change('hold',select.value);
      }
      statusNode=make('p','',dialog);statusNode.setAttribute('aria-live','polite');
      const list=make('ul',undefined,dialog);
      for(const entry of state?.calendar||[])make('li',THEMES[entry.id]+' '+entry.name+': '+dates(entry),list);
      make('p','Christmas brings gentle snow outdoors. Decorations follow the house calendar in America/Chicago.',dialog);
    }
    button.onclick=async()=>{await refresh();renderDialog();dialog.showModal();};
    dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
    const hook={refresh,
      drawDecorations:(ctx,r,tile)=>drawDecorations(ctx,r,tile,state,metadata,atlas,keepsakes)+drawKeepsakes(ctx,r,tile,state,keepsakes,keepsakeImages),
      drawSnow:(ctx,r,tile)=>drawSnow(ctx,r,tile,state,win.performance.now(),motion.matches,doc.hidden),
      state:()=>({...state,art_ready:!!atlas,decoration_count:state?.current&&atlas?placements(room()).filter(item=>!replacedKinds(state,room(),keepsakes).has(item.kind)).length:0,
        keepsakes_here:keepsakesFor(state,room(),keepsakes).map(row=>row.label||row.id),
        snow_visible:snowEnabled(state,room()),reduced_motion:motion.matches})};
    win.commonsHolidays=hook;
    const original=win.render_game_to_text;
    if(original)win.render_game_to_text=()=>JSON.stringify({...JSON.parse(original()),holiday:hook.state()});

    const CDN_ART='/art/';
    const loadArt=(path,onload)=>{
      const image=new win.Image();image.onload=()=>onload(image);
      if(typeof path==='string'&&path.startsWith('/art/')){
        image.onerror=()=>{image.onerror=null;image.src=path;};
        image.src=CDN_ART+path.slice(5);
      }else image.src=path;
    };
    win.fetch('/assets/holiday-art.json').then(response=>{if(!response.ok)throw Error('Holiday artwork is unavailable.');return response.json();}).then(value=>{
      metadata=value;if(metadata.image)loadArt(metadata.image,image=>{atlas=image;});
    }).catch(()=>{});
    win.fetch('/assets/holiday-keepsakes.json',{cache:'no-store'}).then(response=>{if(!response.ok)throw Error('No seasonal keepsakes.');return response.json();}).then(value=>{
      keepsakes=Array.isArray(value?.keepsakes)?value.keepsakes:[];
      for(const row of keepsakes){if(!row?.id||!row.art)continue;loadArt(row.art,image=>{keepsakeImages[row.id]=image;});}
    }).catch(()=>{});
    refresh();
    win.setInterval(()=>{if(!doc.hidden)refresh();},30000);
    doc.addEventListener('visibilitychange',()=>{if(!doc.hidden)refresh();});
  }
  return {isOutdoors,placements,frameFor,keepsakesFor,drawKeepsakes,snowEnabled,snowParticles,drawDecorations,drawSnow,mount};
});
