

(() => {
  const make=(tag,text,parent)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(parent)parent.append(e);return e;};
  const dialog=make('dialog');dialog.id='field-dialog';dialog.setAttribute('aria-label','Field activity');
  document.body.append(dialog);
  const style=make('style');style.textContent=`#field-dialog{box-sizing:border-box;width:min(540px,calc(100vw - 24px));max-height:85vh;overflow:auto;background:#fbf1df;color:#483829;border:2px solid #b99969;border-radius:15px;padding:24px;font:16px/1.5 Georgia,serif}#field-dialog::backdrop{background:#07161377}#field-dialog h2{margin:0 0 12px}#field-dialog p{white-space:pre-wrap}#field-dialog button,#field-dialog select{font:inherit;padding:10px 14px;margin:6px 6px 0 0;border:1px solid #aa8859;border-radius:7px;background:#fff9ed;color:#523c28}#field-dialog .field-bonuses{padding:12px;background:#e8dbc4;border-radius:8px}#field-dialog li{margin:5px 0}`;document.head.append(style);
  style.textContent+='#field-dialog{margin:auto}#field-dialog p{margin:10px 0}#field-dialog .field-bonuses{padding:12px 12px 12px 30px}';
  style.textContent+='#field-dialog textarea{display:block;box-sizing:border-box;width:100%;min-height:72px;margin-top:6px;padding:9px;font:inherit;border:1px solid #aa8859;border-radius:7px;background:#fff9ed;color:#523c28}';
  let locked=false,animals=[],loadedRoom='',effect=null,ticks=0,lastResult=null,activeClip=null;
  const visible=a=>a.hiddenUntil<=Date.now();
  const animated=new Set(['animal-rabbit','animal-deer','animal-boar','animal-grouse','animal-wolf','animal-bear']);
  const symbols={'wild horse':'🐎','coyote pup':'🐕','coyote':'🐕','prairie dog':'🐿️','hawk':'🦅','rattlesnake':'🐍','pronghorn':'🦌','bison':'🦬','fox':'🦊','owl':'🦉','cat':'🐈','raven':'🐦‍⬛','squirrel':'🐿️','hedgehog':'🦔','badger':'🦡','bat':'🦇','pheasant':'🐦'};
  function close(){dialog.close();window.commonsAdventureOpen=false;document.getElementById('adventure-open')?.focus();}
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  function box(title){window.commonsGame.close();dialog.replaceChildren();make('h2',title,dialog);dialog.showModal();window.commonsAdventureOpen=true;}
  function button(label,fn){const b=make('button',label,dialog);b.onclick=fn;return b;}
  function note(text){make('p',text,dialog);}
  function signed(n){return Number(n)>=0?'+'+n:String(n);}
  function sources(rows){const ul=make('ul',undefined,dialog);ul.className='field-bonuses';for(const row of rows)make('li',row,ul);return ul;}
  function fishDetails(setup){
    const companion=setup.companion;
    const parts=[];for(const key of ['fishing','luck'])if(companion?.[key])parts.push(key+' '+signed(companion[key]));
    const help='Following companion'+(companion?.name?' — '+companion.name:' — none')+': '+signed(setup.bonuses.companion||0)+(parts.length?' ('+parts.join(', ')+')':'');
    return sources([setup.method+': '+signed(setup.bonuses.rod),'Bait'+(setup.bait?' — '+setup.bait:' — none')+': '+signed(setup.bonuses.bait),'Equipment: '+signed(setup.bonuses.equipment),'Luck: '+signed(setup.bonuses.luck),help,'Total bonus: '+signed(setup.total_bonus)]);
  }
  function huntDetails(s){
    const rows=[];
    for(const [key,value] of Object.entries(s.bonus_sources||{})){
      const label=key.replaceAll('_',' ');
      rows.push(value&&typeof value==='object'?`${value.name||label}: ${signed(value.attack||0)} attack${value.hunting?' (including '+signed(value.hunting)+' hunting)':''}, ${signed(value.damage||0)} damage`:`${label}: ${signed(value||0)}`);
    }
    rows.push(`Attack bonus: ${signed(s.attack_bonus??s.attack)}; damage: ${s.damage_die} ${signed(s.damage_bonus||0)}`);sources(rows);
  }
  function tameDetails(setup){
    const rows=[];
    const labels={form:'Form',active_buffs:'Active effects',status_effects:'Active effects',approach:'Approach',equipment:'Worn equipment',companion:'Companion'};
    for(const [key,value]of Object.entries(setup.bonus_sources||{})){
      if(value&&typeof value==='object')rows.push(`${value.name||'Companion'}: ${signed(value.taming||0)} taming`);
      else rows.push(`${labels[key]||key.replaceAll('_',' ')}: ${signed(value||0)}`);
    }
    rows.push('Total taming bonus: '+signed(setup.taming_bonus??setup.total_bonus??0));
    return sources(rows);
  }
  function huntingStyle(state){
    const weapon=state?.catalog.hunting_setup.weapon;
    if(weapon?.ammo_type==='arrows'||/\b(longbow|shortbow|crossbow|bow)\b/i.test(weapon?.name||''))return 'bow';
    return weapon?.type==='ranged'?'ranged':'melee';
  }
  function weaponCondition(state){
    const ref=state?.equipment_refs?.weapon;
    const item=ref?.source==='inventory'?state.inventory_condition_items?.find(value=>value.id===ref.id):state?.made?.find(value=>value.id===ref?.id);
    if(item&&item.max_durability>0)note(`${item.name||item.what} condition: ${item.durability}/${item.max_durability}.`);
  }
  function huntClip(style){
    if(style==='bow')return 'archery';
    const choices=style==='ranged'?['shoot','ranged','attack']:['attack','melee','sword'];
    return choices.find(name=>window.commonsActions.has?.(name))||'attack';
  }
  function water(p){

    return window.CommonsFishingAreas.contains(R,p);
  }
  function safe(x,y){return !companionSolid(x,y)&&!water({x,y})&&!R.props.some(p=>p.to&&x>=p.x-1&&x<=p.x+(p.w||1)+1&&y>=p.y-(p.h||1)&&y<=p.y+2);}
  function protectedAt(p){
    if(NIM.room===ROOM&&Math.hypot(NIM.x-p.x,NIM.y-p.y)<1.3)return true;
    if(ALF.room===ROOM&&Math.hypot(ALF.x-p.x,ALF.y-p.y)<1.1)return true;
    return Object.values(FOLLOWERS).some(f=>companionVisible(f)&&Math.abs(f.x-p.x)<.8&&p.y>f.y-1.5&&p.y<f.y+.4);
  }
  function seed(state){
    if(loadedRoom===ROOM)return;loadedRoom=ROOM;animals=[];
    const spots=[];for(let y=3;y<R.rows.length-2;y++)for(let x=3;x<R.rows[y].length-2;x++)if(safe(x+.5,y+.8))spots.push({x:x+.5,y:y+.8});
    const roster=state.catalog.hunts.filter(q=>q.name!=='bandit').map(q=>({...q,hunt:q.name}));
    for(const pet of state.catalog.animals){const q=roster.find(q=>q.name.toLowerCase()===pet.id.replaceAll('_',' '));if(q){q.tame=pet.id;q.emoji=pet.emoji;}else roster.push({name:pet.name,tame:pet.id,emoji:pet.emoji});}
    for(const [i,q] of roster.entries()){
      const start=(i*137+Math.floor(spots.length*.35))%spots.length;
      const pos=spots.slice(start).concat(spots.slice(0,start)).find(p=>animals.every(a=>Math.hypot(a.x-p.x,a.y-p.y)>3));if(!pos)continue;
      const key=companionAnimName(q.name);
      if((animated.has(key)||window.commonsAnimalSprites?.has(key))&&ANIM[key]===undefined){ANIM[key]=null;loadFolderAnim(key,rec=>ANIM[key]=rec);}
      animals.push({name:q.name,hunt:q.hunt,tame:q.tame,emoji:q.emoji,...pos,homeX:pos.x,homeY:pos.y,key,dir:'down',anim:0,hiddenUntil:0,index:i});
    }
  }
  async function meet(a){
    if(locked)return;
    if(!a.tame)return start('hunt',{target:a.hunt},a);
    locked=true;
    try{
      const state=await window.commonsGame.sync(),pet=state.catalog.animals.find(p=>p.id===a.tame);
      box('Meet '+a.name);
      if(pet){note(pet.tip||pet.description||'Approach patiently.');note('Taming difficulty: '+pet.difficulty+'.');
        if(state.catalog.taming_setup)tameDetails(state.catalog.taming_setup);
        note('Your form, worn equipment and active effects can help. Only the companion following you adds its help; the others rest.');
        const approachLabel=make('label','Your approach (optional)',dialog),words=make('textarea',undefined,approachLabel);
        words.placeholder='Describe what you do or say.';words.setAttribute('aria-label','Your approach');words.maxLength=4000;
        note('Your approach can add a separate bonus. The result shows the complete roll.');
        button('Try taming',async()=>{
          if(locked)return;locked=true;
          try{const r=await window.commonsGame.act('tame',{target:a.tame,words:words.value});
            lastResult=r;
            if(r?.success)a.hiddenUntil=Infinity;
            box(r?.success?'A new companion':'The approach');note((r?.message||'No result returned.').replaceAll('**',''));
            if(r?.bonus_sources)tameDetails(r);
            if(r?.roll!==undefined)note(`Roll ${r.roll} + bonus ${r.taming_bonus} = ${r.modified_roll} against ${r.difficulty}.`);
            if(r?.success){note('Open Companions to give them a name and walk together.');button('Your companions',()=>{close();window.commonsGame.open('Companions');});}
            button('Close',close);
          }finally{locked=false;}
        });}
      if(a.hunt)button('Hunt instead',()=>start('hunt',{target:a.hunt},a));
      button('Leave them be',close);
    }catch(e){box('Could not approach');note(e.message);button('Close',close);}finally{locked=false;}
  }
  function approach(id){
    const pet=window.commonsAdventureState?.catalog.animals.find(a=>a.id===id);
    return meet(animals.find(a=>a.tame===id)||{name:pet?.name||id.replaceAll('_',' '),tame:id});
  }
  async function start(action,args={},target=null){
    if(locked)return;locked=true;
    try{
      const state=await window.commonsGame.sync();seed(state);
      box(action==='hunt'?'Hunt '+args.target:'At the water');
      if(action==='fish'&&state.held_fish){resultBox(action,{success:true,catch:state.held_fish,message:'Your catch is waiting. Keep it or release it.'});return;}
      let bait;
      if(action==='fish'){
        const setup=state.catalog.fishing_setup;let breakdown=fishDetails(setup);
        const hints=make('div',undefined,dialog);
        const label=make('label','Bait for this attempt ',dialog);bait=make('select',undefined,label);bait.setAttribute('aria-label','Bait for this attempt');bait.append(new Option('No bait',''));
        for(const b of setup.baits.filter(b=>b.remaining>0))bait.append(new Option(`${b.name} (${b.remaining} uses, +${b.bonus})`,b.name));
        bait.value=args.bait||'';
        bait.onchange=()=>{
          const selected=setup.baits.find(b=>b.name===bait.value);
          const preview={...setup,bait:selected?.name,bonuses:{...setup.bonuses,bait:selected?.bonus||0}};
          preview.total_bonus=Object.values(preview.bonuses).reduce((a,b)=>a+b,0);
          const replacement=fishDetails(preview);breakdown.replaceWith(replacement);breakdown=replacement;
          hints.replaceChildren();for(const hint of setup.hints)if(!selected||!hint.includes('bait'))make('p',hint,hints);
        };bait.onchange();
        note('The roll and every bonus appear after the attempt. A natural 1 can still slip away; a natural 20 always catches.');
      }else{huntDetails(state.catalog.hunting_setup);weaponCondition(state);note('Only unnamed wild quarry can be hunted. Your actual equipped weapon, form and companion determine the result.');note('Equip a weapon in Inventory to use its attack and damage bonuses. Hunting equipment, your chosen form and your following companion can help too; the breakdown above shows what is helping now.');}
      button(action==='hunt'?(huntingStyle(state)==='melee'?'Begin the hunt':'Take the shot'):'Try fishing',()=>run(action,action==='fish'?{bait:bait.value}:args,target));
      button('Never mind',close);
    }catch(e){box('Could not begin');note(e.message);button('Close',close);}finally{locked=false;}
  }
  async function run(action,args,target){
    if(locked)return;locked=true;close();window.commonsGame.close();
    if(target){const dx=target.x-P.x,dy=target.y-P.y;P.dir=Math.abs(dx)>Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up');}
    TARGET=null;P.moving=false;
    let result,contactWork;
    const style=action==='hunt'?huntingStyle(window.commonsAdventureState):null;
    try{
      await new Promise(resolve=>{
        const contact=()=>{
          if(contactWork)return contactWork;

          effect={kind:action,style,at:performance.now(),from:{x:P.x,y:P.y-.6},to:target||{x:P.x+(P.dir==='left'?-2:P.dir==='right'?2:0),y:P.y+(P.dir==='up'?-2:2)},rod:!!window.commonsAdventureState?.catalog.fishing_setup.rod};
          const output=window.commonsGame.act(action,args);
          contactWork=Promise.all([output.then(r=>result=r),new Promise(r=>setTimeout(r,action==='fish'?1500:450))]);
          return contactWork;
        };
        activeClip=action==='hunt'?huntClip(style):window.commonsAdventureState?.catalog.fishing_setup.rod?'fishing':'hand-fishing';
        window.commonsActions.play(activeClip,{hold:false,contact,done:resolve});
      });
      if(action==='hunt'&&result?.success&&target?.name)target.hiddenUntil=Date.now()+30000;
      resultBox(action,result||{success:false,message:'The house did not return a result. Check your inventory before trying again.'});
    }catch(e){box('Could not finish');note(e.message);button('Close',close);}finally{locked=false;effect=null;activeClip=null;}
  }
  function resultBox(action,result){
    lastResult=result;box(action==='hunt'?'The hunt':result.catch?'On the line':result.setup&&!result.success?'The fish slipped away':'Fishing');
    note((result.message||'Done.').replaceAll('**','').replaceAll('`',''));
    if(result.setup){fishDetails(result.setup);note(`Roll ${result.roll} + bonus ${result.setup.total_bonus} = ${result.total} against ${result.difficulty}. ${result.reason}`);if(result.success)for(const h of result.setup.hints)note(h);}
    if(result.rolls){huntDetails(result.rolls);note(`Roll ${result.rolls.player_roll} + ${result.rolls.attack_bonus} = ${result.rolls.player_attack} against ${result.rolls.creature_ac}.`);if(result.rolls.damage!==undefined)note('Damage: '+result.rolls.damage);}
    for(const item of result.item_wear||[])note(`${item.name} condition: ${item.before} → ${item.after}/${item.max_durability}${item.broken?' — broken; repair it in Inventory':''}.`);
    if(action==='hunt'){
      const state=window.commonsAdventureState;if(state)note(`Your HP: ${state.hp}/${state.max_hp}.`);
      const injuries=result.companion_injuries||(result.companion_injury?[result.companion_injury]:[]);
      for(const injury of injuries)if(injury?.message)note(injury.message);
      if(!injuries.length){const pet=state?.companions.find(value=>value.is_active);if(pet?.hp!==undefined)note(`${pet.custom_name||pet.name}: ${pet.hp}/${pet.max_hp} HP.`);}
      if(!(result.item_wear||[]).some(item=>item.id===state?.equipment_refs?.weapon?.id))weaponCondition(state);
    }
    if(result.catch){note(`${result.catch.species} · ${result.catch.lb} lb`);for(const [label,act]of [['Keep it','keep'],['Let it go','release']])button(label,async()=>{if(locked)return;locked=true;try{const r=await window.commonsGame.act(act);resultBox('fish',r);}finally{locked=false;}});}
    button('Close',close);
  }
  function step(){
    ticks++;if(locked||dialog.open||window.commonsAdventureOpen)return;
    animals.forEach(a=>{if(!visible(a))return;const phase=(Math.floor(ticks/150)+a.index)%4,dx=[.009,0,-.009,0][phase],dy=[0,.009,0,-.009][phase];
      const x=a.x+dx,y=a.y+dy;if(safe(x,y)&&Math.hypot(x-a.homeX,y-a.homeY)<3){a.x=x;a.y=y;a.anim+=.09;a.dir=['right','down','left','up'][phase];}
    });
  }
  function bodies(list){for(const a of animals.filter(visible))list.push({y:a.y,go:()=>{
    const rec=ANIM[a.key];if(!rec||!drawFolderFrame(rec,a.dir,1+Math.floor(a.anim)%8,a.x*T,a.y*T,1)){cx.save();cx.font='30px "Segoe UI Emoji", sans-serif';cx.textAlign='center';cx.fillText(a.emoji||symbols[a.name.toLowerCase()]||'🐾',a.x*T,a.y*T);cx.restore();}
    cx.save();cx.font='11px Georgia';cx.textAlign='center';cx.fillStyle='#fff3d4';cx.strokeStyle='#17261a';cx.lineWidth=3;const text=a.name+' · '+(a.hunt&&a.tame?'hunt / tame':a.tame?'tame':'hunt'),y=a.y*T-(rec?rec.tilesTall*T*CHARSCALE:30)-5;cx.strokeText(text,a.x*T,y);cx.fillText(text,a.x*T,y);cx.restore();
  }});}
  function drawEffect(){if(!effect)return;const e=effect,t=Math.min(1,(performance.now()-e.at)/550),x=(e.from.x+(e.to.x-e.from.x)*t)*T,y=(e.from.y+(e.to.y-e.from.y)*t)*T;
    cx.save();cx.strokeStyle='#f9dba8';cx.lineWidth=2;cx.beginPath();
    if(e.kind==='hunt'){
      if(e.style==='melee'){const angle=-Math.PI*.75+t*Math.PI*1.5;cx.arc(e.from.x*T,e.from.y*T,22,angle-.7,angle);cx.stroke();}
      else{const dx=e.to.x-e.from.x,dy=e.to.y-e.from.y,len=Math.hypot(dx,dy)||1;cx.moveTo(x-dx/len*18,y-dy/len*18);cx.lineTo(x,y);cx.stroke();}
    }
    else{if(e.rod){cx.moveTo(e.from.x*T,e.from.y*T);cx.quadraticCurveTo((e.from.x*T+x)/2,Math.min(e.from.y*T,y)-24,x,y);cx.stroke();cx.fillStyle='#db6e49';cx.beginPath();cx.arc(x,y,4,0,Math.PI*2);cx.fill();}cx.strokeStyle='#d2ffff';cx.beginPath();cx.ellipse(x,y,5+(ticks%40)/3,3+(ticks%40)/6,0,0,Math.PI*2);cx.stroke();}cx.restore();
  }
  function tap(p){
    if(!window.commonsAdventureState||protectedAt(p))return false;
    const a=animals.find(a=>visible(a)&&Math.abs(p.x-a.x)<.8&&p.y<a.y+.35&&p.y>a.y-1.4);
    if(a){meet(a);return true;}
    if(window.commonsAdventureState.catalog.fishing&&water(p)){start('fish',{},p);return true;}return false;
  }

  window.commonsField={
    start,approach,tap,step,bodies,drawEffect,busy:()=>locked,
    state:()=>({
      ready:!!loadedRoom,busy:locked,animation:effect?.kind||null,
      clip:activeClip,attackStyle:effect?.style||null,dialog:dialog.open,result:lastResult,
      wildlife:animals.filter(visible).map(a=>({
        name:a.name,x:a.x,y:a.y,hunt:!!a.hunt,tame:a.tame||null,sprite:ANIM[a.key]?a.key:null
      })),
      fishing_zones:R?.fishing_zones||[],
      fishing_paint:R?.fishing_paint||[],
      fishing_water:R?window.CommonsFishingAreas.summary(R):null
    })
  };
  const oldText=window.render_game_to_text;window.render_game_to_text=()=>JSON.stringify({...JSON.parse(oldText()),field:window.commonsField.state()});
  const ready=setInterval(async()=>{if(!R||!ME)return;clearInterval(ready);try{seed(await window.commonsGame.sync());}catch(e){console.warn('Field activities:',e.message);}},500);
})();
