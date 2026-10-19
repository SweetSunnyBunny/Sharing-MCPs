

(() => {
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const open=el('button','Inventory');open.id='adventure-open';document.body.append(open);
  const petsOpen=el('button','🐾 Pets');petsOpen.id='pets-open';petsOpen.title='Pets and companions: feed, care, choose a follower or meet wildlife. Poke your animal in the room to pet them.';document.body.append(petsOpen);
  const petStatus=el('div');petStatus.id='pet-status';petStatus.setAttribute('role','status');petStatus.setAttribute('aria-live','polite');document.body.append(petStatus);
  const panel=el('section');panel.id='adventure-panel';panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');panel.setAttribute('aria-label','Inventory and activities');
  const head=el('header'),title=el('h2','Inventory and activities'),stats=el('span');stats.id='adventure-stats';
  const close=el('button','Close');head.append(title,stats,close);
  const tabs=el('nav');tabs.id='adventure-tabs';tabs.setAttribute('aria-label','Inventory sections');
  const content=el('div');content.id='adventure-content';const status=el('div');status.id='adventure-status';status.setAttribute('role','status');
  panel.append(head,tabs,content,status);document.body.append(panel);
  let state=null,active='Inventory',busy=false,library={tracks:{},assignments:{}},terminalName='',terminalCharacter='',terminalAmount='10',terminalResult=null,focusedCompanion='';
  const cardAffection=[];
  let petStatusTimer,returnControl=open,petting=false;
  const PETS_VIEW='Pets & Companions';

  function canonicalTab(tab){return tab==='Pets'||tab==='Companions'?PETS_VIEW:tab;}
  function updatePanelTitle(){const label=active===PETS_VIEW?PETS_VIEW:'Inventory and activities';title.textContent=label;panel.setAttribute('aria-label',label);}
  function selectTab(){
    for(const b of tabs.children){
      const selected=b.dataset.tab===active;b.setAttribute('aria-selected',String(selected));
      if(selected&&panel.style.display==='flex'){
        const edge=tabs.getBoundingClientRect(),item=b.getBoundingClientRect();
        if(item.left<edge.left)tabs.scrollLeft-=edge.left-item.left;
        else if(item.right>edge.right)tabs.scrollLeft+=item.right-edge.right;
      }
    }
  }
  function petNotice(message){clearTimeout(petStatusTimer);petStatus.textContent=message;petStatusTimer=setTimeout(()=>petStatus.textContent='',5000);}
  function credentials(){if(typeof ME==='undefined'||!ME||typeof MYKEY==='undefined'||!MYKEY)throw Error('Sign in to Commons first.');return {by:ME,key:MYKEY};}
  async function api(path,body){const c=credentials();let r;if(body===undefined){const q=new URLSearchParams({as:c.by,key:c.key});r=await fetch(path+(path.includes('?')?'&':'?')+q,{cache:'no-store'});}else r=await (window.commonsReliableFetch||fetch)(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,room:body.room||ROOM,...c})});const d=await r.json();if(!r.ok||d.error){const e=Error(d.error||'The request could not be completed.');e.status=r.status;throw e;}return d;}
  async function sync(){

    if(typeof R!=='undefined'&&R&&typeof P!=='undefined')await api('/api/here',{room:ROOM,x:P.x,y:P.y,facing:P.dir});
    state=await api('/api/game/state');
    stats.textContent=`${state.gold} gold · ${state.hp}/${state.max_hp} HP · Level ${state.level}`;
    window.commonsAdventureState=state;
    parent.postMessage({type:'commons-combat',active:!!state.dungeon?.data},location.origin);
    return state;
  }
  async function act(action,args={}){
    if(busy)return;busy=true;status.textContent='Working…';
    const requestId=crypto.randomUUID();
    try{
      const d=await api('/api/game/action',{action,args,request_id:requestId});state=d.state;window.commonsAdventureState=state;
      parent.postMessage({type:'commons-combat',active:!!state.dungeon?.data||action==='hunt'},location.origin);
      if(action==='hunt')setTimeout(()=>parent.postMessage({type:'commons-combat',active:!!state.dungeon?.data},location.origin),12000);
      status.textContent=d.result.message||d.result.combat_text||'Done.';stats.textContent=`${state.gold} gold · ${state.hp}/${state.max_hp} HP · Level ${state.level}`;
      window.commonsCompanions?.result(action,d.result,requestId);
      if(action.startsWith('companion-')||['feed','tame','buy'].includes(action))window.commonsCompanions?.refresh()?.catch(()=>{});
      render();if(d.result.job_id)waitForJob(d.result.job_id);return d.result;
    }catch(e){status.textContent=e.message+(pendingAction()?' Recover the unfinished action to check its outcome.':'');recovery();return {success:false,error:true,message:e.message};}
    finally{busy=false;}
  }
  async function waitForJob(id){try{const d=await api('/api/game/job?id='+encodeURIComponent(id));if(['pending','running'].includes(d.job.status)){setTimeout(()=>waitForJob(id),2000);return;}status.textContent=d.job.text||d.job.error;if(active==='Journal'){await sync();render();}}catch(e){status.textContent=e.message;}}
  function button(text,fn,parent=content){const b=el('button',text);b.type='button';b.onclick=()=>Promise.resolve(fn()).catch(e=>{status.textContent=e.message;});parent.append(b);return b;}
  function card(name,description,parent=content,imageName=name,isItem=false){const c=el('article',undefined,'adv-card');const src=isItem?window.commonsArt?.icon(imageName)||window.commonsArt?.item(imageName):window.commonsArt?.item(imageName);if(src){const img=el('img',undefined,'adv-item-art');img.src=src;img.alt='';img.width=80;img.height=80;img.onerror=()=>{const fallback=isItem&&window.commonsArt?.icon(imageName,{fallback:true});if(fallback&&img.src!==new URL(fallback,location.href).href)img.src=fallback;else img.remove();};c.append(img);}c.append(el('strong',name));if(description)c.append(el('p',description));parent.append(c);return c;}
  function choices(items,placeholder='Choose…'){const s=el('select');s.append(new Option(placeholder,''));for(const [value,label]of items)s.append(new Option(label,value));return s;}
  function input(placeholder){const i=el('input');i.placeholder=placeholder;i.setAttribute('aria-label',placeholder);return i;}
  function heading(text){content.append(el('h3',text));}
  function pendingAction(){return typeof ME!=='undefined'&&ME?window.commonsActionRetry?.controller(ME).read():null;}
  function recovery(){
    if(content.querySelector('[data-action-recover]'))return;
    const pending=pendingAction();if(!pending)return;
    const c=card('⏳ An unfinished action','The reply was interrupted. Recover the saved outcome before starting another action.');
    const b=button('Recover unfinished action',async()=>{
      if(pending.room!==ROOM){status.textContent='Return to '+pending.room+' to recover this action.';return;}
      if(pending.path==='/api/game/action')await act(pending.body.action,pending.body.args);
      else {const d=await api(pending.path,pending.body);status.textContent=d.message||d.result?.message||'Outcome recovered.';await sync();render();}
    },c);b.dataset.actionRecover='';
  }
  function actionChoice(row,parent=content){
    const b=button(row.label||row.action,async()=>{if(row.action==='craft'&&state.catalog.home_recipes?.[row.choices?.target]){const d=await api('/api/craft',{recipe:row.choices.target});status.textContent=d.message||'Prepared.';await sync();render();return d;}return act(row.action,row.choices||{});},parent);b.dataset.action=row.action;
    if(row.available===false||row.ready===false){b.disabled=true;b.title=row.reason||`Ready in ${row.ready_in||row.seconds_remaining||0} seconds.`;}
    if(row.ready_at&&row.seconds_remaining>0){b.dataset.readyAt=row.ready_at;b.dataset.readyLabel=b.textContent;b.dataset.readyBlocked=String(!!row.ready_blocked||!!row.route?.length||!!(row.destination&&row.destination!==state.room)||Object.values(row.requirements||{}).some(n=>n>0));}
    return b;
  }
  function routeChoice(row,parent){
    if(row.route?.length){parent.append(el('p','Path: '+row.route.map(step=>step.name).join(' → ')));button('Take the next path',()=>travel(row.route[0]),parent);}
  }
  function questStepCard(q,detailed=false){
    const description=[detailed&&q.description,detailed&&`Progress: ${q.progress}/${q.total}.`,q.step].filter(Boolean).join(' ');
    const c=card((q.icon||'📜')+' '+q.title,description);c.dataset.questStep=q.id||'';routeChoice(q,c);
    const hint=q.reason?el('p',q.reason,'adv-care-note'):null;
    if(hint){hint.dataset.questReadyHint='';c.append(hint);}
    const labels={hunt:'Hunt quest quarry',collect:'Collect quest supplies',gather:'Gather quest materials',tame:'Approach quest creature',hack:'Hack quest terminal','quest-complete':'Turn in quest','party-quest-complete':'Turn in party quest'};
    if(q.action){const b=actionChoice({...q,label:q.label||labels[q.action]||q.action.replaceAll('-',' ')},c);b.questReadyHint=hint;}
    return c;
  }
  function partyQuestHere(region=state.region){return !!state.party?.current_quest&&state.party.current_quest.world_id===region;}
  function questChains(){
    const rows=new Map((state.catalog.chains||[]).map(q=>[q.chain_id,q]));
    for(const q of state.guidance?.chains||[])rows.set(q.chain_id,{...rows.get(q.chain_id),...q});
    return [...rows.values()];
  }
  function questChainCard(q){
    const done=q.status==='completed',step=Math.min((q.current_step||0)+1,q.total_steps||1);
    const c=card((q.icon||'📚')+' '+q.name,[q.description,done?'Quest chain complete.':`Step ${step}/${q.total_steps||1}: ${q.current_quest?.title||'Next quest'}.`,q.given_by&&`Quest giver: ${q.given_by}.`].filter(Boolean).join(' '));
    c.dataset.questChain=q.chain_id;
    if(done)return c;
    const activeQuest=q.active||state.quest?.chain_id===q.chain_id||(state.party?.current_quest?.chain_id===q.chain_id);
    if(activeQuest){button('View current step',()=>{active='Continue';render();content.scrollTop=0;},c);return c;}
    routeChoice(q,c);
    const region=q.region||state.region,partyHeld=partyQuestHere(region),held=partyHeld||!!state.quest&&region===state.region;
    const localGiver=state.catalog.npcs.some(n=>String(n.name).toLowerCase()===String(q.given_by).toLowerCase());
    const ready=q.available===undefined?localGiver:q.available;
    const reason=partyHeld?'Finish your party’s quest in this region before starting a solo quest.':held?'Finish or abandon your current quest before starting another.':q.reason||(!localGiver?`Visit ${q.given_by} to ${q.status==='available'?'begin':'continue'} this chain.`:'Ready here.');
    actionChoice({...q,action:q.action||'quest-chain',choices:q.choices||{target:q.chain_id},label:q.label||(q.status==='available'?'Begin chain':'Continue chain'),available:ready&&!held,ready_blocked:held||q.ready_blocked,reason},c);
    if(reason&&(!ready||held))c.append(el('p',reason));
    return c;
  }
  function offeredQuestCard(q){
    const c=card((q.icon||'📜')+' '+q.title,q.description),partyHeld=partyQuestHere();
    if(!state.quest){
      const reason=partyHeld?'Finish your party’s quest in this region before starting a solo quest.':'';
      actionChoice({action:'quest-accept',label:'Accept',choices:{target:q.quest_id},available:!partyHeld,reason},c);
      if(reason)c.append(el('p',reason));
    }
    if(state.party&&!state.quest&&!state.party.current_quest)actionChoice({action:'party-quest-start',label:'Take on as a party',choices:{target:q.quest_id}},c);
    return c;
  }
  function continueAdventure(){
    heading('🧭 Continue your adventure');const g=state.guidance||{};
    for(const notice of g.notices||[])card('Your travelling kit',notice);
    for(const q of g.quests||[])questStepCard(q);
    const pendingChains=(g.chains||[]).filter(q=>!q.active&&q.status!=='completed'&&!(g.quests||[]).some(step=>step.id===q.current_quest?.quest_id));
    if(pendingChains.length){heading('Continue a quest chain');for(const q of pendingChains)questChainCard(q);}
    if(g.journey){const j=g.journey,c=card(j.icon+' '+j.title,`${j.progress}/${j.total} · ${j.step} ${j.reason||''}`);routeChoice(j,c);if(j.action)actionChoice({...j,label:j.label||j.step},c);button('Clear bookmark',()=>act('goal-clear'),c);}
    else {heading('Choose a first adventure');for(const j of g.journeys||[]){const c=card(j.icon+' '+j.title,j.steps.join(' → '));button('Bookmark this journey',()=>act('goal-pin',{target:j.id}),c);}}
    heading('Things you can do here');for(const row of g.next_actions||[]){const c=card(row.label,row.reason);actionChoice(row,c);}
    for(const reply of g.replies||[]){const c=card('A reply from '+reply.by,reply.text);button('Open room conversation',()=>{close.click();document.getElementById('convoBtn')?.click();},c);}
    for(const job of g.jobs||[]){const c=card('Conversation with '+job.npc,job.status);button('Read response',()=>waitForJob(job.id),c);}
    if(g.invitations?.length)button('See party invitations',()=>{active='Party';render();});
  }
  function projects(){
    heading('🤝 Shared projects');content.append(el('p',state.community?.instructions));
    for(const p of state.community?.projects||[]){
      const c=card(p.icon+' '+p.title,p.completed?p.completed_description:p.description);c.dataset.project=p.id;
      const effect=p.benefits||p.effects||{},benefits=[];if(effect.gathering_bonus)benefits.push('Gathering +'+effect.gathering_bonus);if(effect.repair_material_discount)benefits.push(Math.round(effect.repair_material_discount*100)+'% fewer repair materials');if(p.service)benefits.push(p.service.heal+' HP per share, one per person');if(benefits.length)c.append(el('p',(p.completed?'Benefit: ':'On completion: ')+benefits.join(' · ')+'.'));
      c.append(el('p',p.clue));routeChoice(p,c);
      for(const r of p.requirements)c.append(el('p',`${r.material}: ${r.contributed}/${r.required} · you carry ${r.owned} · your contribution ${r.mine}`));
      const material=choices(p.requirements.map(r=>[r.material,r.material]),'Choose material');material.setAttribute('aria-label','Project material');
      const quantity=input('Contribution quantity');quantity.type='number';quantity.min='1';quantity.step='1';quantity.value='1';
      if(!p.completed){c.append(material,quantity);const contribute=button('Contribute',()=>act('project-contribute',{target:p.id,material:material.value,quantity:Number(quantity.value)}),c);
        const withdraw=button('Withdraw my materials',()=>act('project-withdraw',{target:p.id,material:material.value,quantity:Number(quantity.value)}),c);
        function update(){const r=p.requirements.find(r=>r.material===material.value),n=Number(quantity.value),valid=Number.isSafeInteger(n)&&n>0;
          contribute.disabled=!p.can_contribute||!valid||!r||n>Math.min(r.remaining,r.owned);withdraw.disabled=!p.can_withdraw||!valid||!r||n>r.mine;}
        material.onchange=update;quantity.oninput=update;update();}
      if(p.contributors?.length)c.append(el('p','Made possible by '+p.contributors.map(person=>person.name||person).join(', ')+'.'));
      if(p.service){c.append(el('p',`${p.service.remaining} shares remain.${p.service.claimed?' You have enjoyed your share.':''}`));const b=button(p.service.icon+' '+p.service.title,()=>act('project-service',{target:p.id}),c);b.disabled=!p.service.available;}
    }
    if(!state.community?.projects?.length)content.append(el('p','Visit Home or an adventure region to find its noticeboard.'));
  }
  function fieldGuide(){
    const g=state.field_guide||{};heading('📖 Your field guide');content.append(el('p',g.selected_title?'Your title: '+g.selected_title:'Your discoveries belong here.'));
    for(const m of g.milestones||[]){const c=card(m.icon+' '+m.title,`${m.description} ${m.progress}/${m.required}${m.earned?' · earned':''}`);if(m.earned)actionChoice({action:'field-guide-title',label:'Use title: '+m.title,choices:{target:m.id}},c);}
    for(const a of g.title_actions||[])if(!(g.milestones||[]).some(m=>m.id===a.choices.target))actionChoice({...a,label:'Use title: '+a.label});
    for(const section of ['places','landmarks','animals','fish','recipes']){heading(section);for(const row of g[section]||[]){const details=[row.description||row.clue];if(section==='fish')details.push(`${row.catches||0} catches · personal record ${row.record_lb||0} lb`);if(section==='animals')details.push(`${row.hunted||0} hunted · ${row.tamed||0} bonded`);if(section==='recipes')details.push(`${row.completed||0} completed`);card((row.icon||'')+' '+(row.name||row.title||row.label||row.id),details.filter(Boolean).join(' · '));}}
  }
  function companionAbilities(){
    const a=state.companion_abilities||{};heading('Deliberate companion help');
    if(a.reason)content.append(el('p',a.reason));
    if(a.pending)card('Prepared help',`${a.pending.name}: +${a.pending.bonus} for ${a.pending.action}${a.pending.available?' here':' when you return with this companion'}.`);
    for(const row of a.actions||[]){const c=card((row.icon||'🐾')+' '+row.label,`One attempt${row.bonus?' · +'+row.bonus:''}${row.ready_in?' · ready in '+row.ready_in+' seconds':''}`);
      actionChoice({...row,ready_at:a.ready_at,seconds_remaining:row.ready_in},c);}
  }
  function travel(exit){const q=new URLSearchParams({scene:'1',room:exit.room});if(exit.arrival){q.set('ax',exit.arrival.x);q.set('ay',exit.arrival.y);}location.search=q;}
  function explore(){
    const selectedTerminal=state.catalog.hacking.find(t=>t.name===terminalName);
    if(selectedTerminal){terminalConsole(selectedTerminal);return;}
    terminalName='';
    for(const change of state.environment?.changes||[])card(change.icon+' '+change.title,change.description+' '+(change.contributors?.length?'Thanks to '+change.contributors.join(', ')+'.':''));
    heading(R?.name||state.room);button('Claim daily welcome',()=>act('daily'));content.append(el('p',R?.subtitle||'Choose a path or an activity.'));
    const grid=el('div',undefined,'adv-grid');content.append(grid);
    for(const ex of state.exits){const c=card(ex.label,'',grid);button('Take this path',()=>travel(ex),c);}
    if(state.weather)card(state.weather.name,state.weather.description);heading('Hunting');if(!state.catalog.hunts.length)content.append(el('p','Wild quarry lives in designated hunting grounds.'));
    for(const q of state.catalog.hunts){const c=card(q.name,`Difficulty ${q.difficulty}${q.health?' · '+q.health+' health':''}`);button('Hunt',()=>window.commonsField.start('hunt',{target:q.name}),c);}
    if(state.catalog.archery){heading('Archery');button('Shoot an end',()=>{close.click();document.getElementById('bowBtn').click();});}
    heading('Gathering');for(const r of state.catalog.gathering){const c=card(r.name,`Difficulty ${r.difficulty}${r.description?' · '+r.description:''}`,content,r.name,true);button('Gather',()=>act('gather',{target:r.name}),c);}
    if(state.catalog.fishing){heading('At the water');const bait=choices([['Worms','Worms'],['Live Minnows','Live Minnows'],['Magic Lure','Magic Lure']],'No bait');content.append(bait);button('Cast a line',()=>window.commonsField.start('fish',{bait:bait.value}));}
    if(state.held_fish){const c=card(state.held_fish.species,`${state.held_fish.lb} lb · waiting on your line`,content,state.held_fish.species,true);button('Keep',()=>act('keep'),c);button('Release',()=>act('release'),c);}
    if(state.catalog.hacking.length)heading('Terminals');
    for(const terminal of state.catalog.hacking){const c=creatureCard({...terminal,emoji:'\u{1f4bb}'},terminal.flavor);button('Open terminal',()=>{terminalName=terminal.name;terminalResult=null;render();content.scrollTop=0;},c);}
    if(R?.rest_station||['hearth','bedroom','nest'].includes(state.room))button('Rest and recover',()=>act('rest'));
  }
  function terminalOperations(terminal){
    return (terminal.operations||['recover']).map(operation=>typeof operation==='string'?operation:operation.mode||operation.id);
  }
  function terminalConsole(terminal){
    button('Back to Explore',()=>{terminalName='';render();content.scrollTop=0;});
    heading(terminal.name);
    const consoleCard=creatureCard({...terminal,emoji:'\u{1f4bb}'},terminal.flavor);
    consoleCard.classList.add('adv-terminal');consoleCard.dataset.terminal=terminal.name;
    const setup=state.catalog.terminal_setup||{},operations=terminalOperations(terminal);
    const balance=Number(setup.credits_balance??state.gold*2);
    const exchange=Number(setup.credits_per_gold??2);
    consoleCard.append(el('p',`Your balance: ${balance} credits · ${exchange} credits = 1 gold`,'adv-terminal-balance'));
    consoleCard.append(el('p',`Breach difficulty: ${terminal.difficulty}${setup.cooldown_seconds?' · '+setup.cooldown_seconds+' seconds between hacking attempts':''}`,'adv-care-note'));
    async function run(mode,extra={}){
      const result=await act('hack',{target:terminal.name,...(mode==='recover'?{}:{mode}),...extra});
      if(result){terminalResult={target:terminal.name,mode,...result};render();content.querySelector('.adv-terminal-result')?.scrollIntoView({block:'nearest'});}
    }
    if(operations.includes('recover'))button('Recover terminal cache',()=>run('recover'),consoleCard);
    const characters=state.catalog.terminal_characters||[];
    if(operations.some(mode=>['intel','steal','give'].includes(mode))){
      if(!characters.length)consoleCard.append(el('p','No character accounts are available on this network.'));
      const accountLabel=el('label','Character account','adv-terminal-label');
      const account=choices(characters.map(character=>[character.id,`${character.name} · ${character.kind==='npc'?'NPC':'Player'} · ${character.credits??0} credits`]),'Choose a character');
      account.setAttribute('aria-label','Character account');account.value=characters.some(c=>c.id===terminalCharacter)?terminalCharacter:'';
      accountLabel.append(account);consoleCard.append(accountLabel);
      const accountDetails=el('p',undefined,'adv-care-note');consoleCard.append(accountDetails);
      const controls=el('div',undefined,'adv-actions');consoleCard.append(controls);
      const read=operations.includes('intel')?button('Read intel',()=>run('intel',{character:account.value}),controls):null;
      let amount,steal,give;
      if(operations.some(mode=>['steal','give'].includes(mode))){
        const amountLabel=el('label','Amount in credits','adv-terminal-label');
        amount=input('Amount in credits');amount.type='number';amount.min='1';amount.step='1';amount.inputMode='numeric';amount.value=terminalAmount;
        amountLabel.append(amount);consoleCard.append(amountLabel);
        const transferControls=el('div',undefined,'adv-actions');consoleCard.append(transferControls);
        if(operations.includes('steal'))steal=button('Steal credits',()=>run('steal',{character:account.value,amount:Number(amount.value)}),transferControls);
        if(operations.includes('give'))give=button('Give credits',()=>run('give',{character:account.value,amount:Number(amount.value)}),transferControls);
      }
      function update(){
        terminalCharacter=account.value;if(amount)terminalAmount=amount.value;
        const chosen=characters.find(character=>character.id===account.value),quantity=Number(amount?.value),valid=Number.isSafeInteger(quantity)&&quantity>0;
        const stealLimit=chosen?Math.min(Number(chosen.steal_limit??chosen.credits),Number(terminal.steal_limit??Infinity)):0;
        if(read)read.disabled=!chosen;
        if(steal){steal.disabled=!chosen||!chosen.stealable||!valid||quantity>stealLimit;steal.title=chosen&&!chosen.stealable?chosen.steal_reason||'This account cannot be stolen from.':!valid?'Choose a positive whole number of credits.':quantity>stealLimit?`This terminal allows up to ${stealLimit} credits from that account.`:'';}
        if(give){give.disabled=!chosen||chosen.giveable===false||!valid||quantity>balance;give.title=quantity>balance?'You do not have enough credits.':!valid?'Choose a positive whole number of credits.':'';}
        accountDetails.textContent=chosen?`${chosen.name}: ${chosen.credits??0} credits.${chosen.stealable&&steal?' You can try to lift up to '+stealLimit+' credits.':''}${chosen.steal_reason?' '+chosen.steal_reason:''}`:'Choose a character to read their dossier or transfer credits.';
      }
      account.onchange=update;if(amount)amount.oninput=update;update();
    }
    if(terminalResult?.target===terminal.name){
      const result=card(terminalResult.success===false?'Access refused':'Terminal response',terminalResult.message,content);
      result.classList.add('adv-terminal-result');result.dataset.terminalMode=terminalResult.mode;
      result.setAttribute('role','region');result.setAttribute('aria-label',terminalResult.intel?'Character dossier':'Terminal response');
      if(terminalResult.intel){
        const intel=terminalResult.intel,details=el('dl',undefined,'adv-terminal-intel');
        const labels={name:'Character',kind:'Account type',role:'Role',description:'Dossier',location:'Last known place',room:'Last known place',region:'Region',level:'Level',hp:'HP',max_hp:'Maximum HP',credits:'Credits',competitive:'Competitive play',character_type:'Character type',form:'Current form',public_bio:'Public biography'};
        for(const [key,label]of Object.entries(labels)){
          const value=intel[key];if(value===undefined||value===null||value==='')continue;
          details.append(el('dt',label),el('dd',typeof value==='boolean'?(value?'Enabled':'Disabled'):String(value)));
        }
        result.append(details);
      }
      if(terminalResult.transfer){
        const transfer=terminalResult.transfer;
        const transferName=id=>id==='player:'+credentials().by.toLowerCase()?state.name:characters.find(character=>character.id===id)?.name||id;
        if(transfer.credits!==undefined)result.append(el('p',`${transfer.credits} credits transferred${transfer.from?' from '+transferName(transfer.from):''}${transfer.to?' to '+transferName(transfer.to):''}.`));
        if(transfer.actor_credits!==undefined)result.append(el('p',`Your balance: ${transfer.actor_credits} credits`));
        if(transfer.target_credits!==undefined)result.append(el('p',`Character balance: ${transfer.target_credits} credits`));
      }
      if(terminalResult.roll!==undefined)result.append(el('p',`Breach roll: ${terminalResult.roll}${terminalResult.bonus!==undefined?' + '+terminalResult.bonus:''}`,'adv-care-note'));
    }
  }
  function slotLabel(slot){return state.equipment_slots?.[slot]||({weapon:'Weapon',armor:'Torso',head:'Head',hands:'Hands',boots:'Feet',cloak:'Cloak',belt:'Belt',charm:'Charm',accessory:'Accessory'})[slot]||slot.replaceAll('_',' ');}
  function wearLabel(slot){return 'Wear '+(({armor:'armor',head:'headgear',hands:'gloves',boots:'boots'})[slot]||slotLabel(slot).toLowerCase());}
  function meter(c,label,current,max,kind='condition'){
    if(!Number.isFinite(Number(current))||!(Number(max)>0))return;
    const value=Math.max(0,Math.min(Number(max),Number(current)));
    const row=el('div',undefined,'adv-meter adv-meter-'+kind);row.dataset[kind]=String(value);row.dataset.max=String(max);
    row.append(el('span',`${label}: ${value}/${max}`));
    const bar=el('progress');bar.max=Number(max);bar.value=value;bar.setAttribute('aria-label',label);row.append(bar);c.append(row);
  }
  function conditionDetails(c,cap={},item={}){
    const current=cap.condition?.current??cap.condition?.durability??item.durability;
    const max=cap.condition?.max??cap.condition?.max_durability??item.max_durability;
    if(!(Number(max)>0))return;
    const description=Number(current)<=0?'Broken':Number(current)<Number(max)/2?'Damaged':Number(current)<Number(max)?'Worn':'Good condition';
    meter(c,'Condition — '+description,current,max);
  }
  function servingDetails(c,cap={},item={}){
    const remaining=cap.remaining??item.uses_left;
    const total=cap.uses_total??item.uses_total;
    if(remaining===undefined||!(Number(total)>0))return;
    const sum=cap.servings_left??remaining;
    const text=cap.units>1?`${sum} servings left in ${cap.units} carried portions · current portion ${remaining}/${total}`:`${remaining}/${total} servings left`;
    c.append(el('p',text,'adv-servings'));c.dataset.servings=String(sum);
  }
  function foodSources(){
    const foods=[];
    for(const [name,cap]of Object.entries(state.item_actions||{}))if(cap.feedable&&(cap.remaining??1)>0)foods.push({key:'bag:'+name,name,cap,args:{target:name}});
    for(const item of state.made||[]){const cap=state.made_actions?.[item.id];if(cap?.feedable&&!item.placed&&!cap.broken&&(cap.remaining??item.uses_left??1)>0)foods.push({key:'made:'+item.id,name:item.name||item.what,cap,args:{target:item.name||item.what,id:item.id}});}
    return foods;
  }
  function companionTarget(pet,index){return pet.instance_id||String(index+1);}
  function ownedPets(){return [...(state?.family_pets||[]),...(state?.companions||[])];}
  function foodPicker(c,pet,index){
    const foods=foodSources(),label=el('label','Food to share ', 'adv-care-choice');
    const select=choices(foods.map(food=>{const heal=food.cap.feed_heal??food.cap.heal;return [food.key,`${food.name} · ${food.cap.servings_left??food.cap.remaining??1} left${heal?' · up to '+heal+' HP':''}`];}),foods.length?'Choose food…':'No food carried');
    select.setAttribute('aria-label','Food for '+(pet.custom_name||pet.name));label.append(select);c.append(label);
    const feed=button('Feed',()=>{const food=foods.find(value=>value.key===select.value);if(food)return act('feed',{...food.args,companion:companionTarget(pet,index)});},c);
    feed.dataset.companionFeed=companionTarget(pet,index);select.dataset.companionFood=companionTarget(pet,index);
    feed.setAttribute('aria-label','Feed '+(pet.custom_name||pet.name));
    feed.disabled=true;select.onchange=()=>feed.disabled=!select.value;
  }
  function repairDetails(c,repair){
    if(!repair)return;
    const materials=Array.isArray(repair.materials)?repair.materials:Object.entries(repair.materials||{}).map(([name,required])=>({name,required}));
    if(materials.length)c.append(el('p','Repair needs: '+materials.map(m=>`${m.required??m.quantity??1} ${m.name}${m.owned!==undefined?' ('+m.owned+' carried)':''}`).join(' · '),'adv-repair-needs'));
    if(repair.available&&Object.keys(repair.spent||{}).length)c.append(el('p','Will use: '+Object.entries(repair.spent).map(([name,quantity])=>quantity+' '+name).join(' · '),'adv-repair-needs'));
    if(repair.reason&&!repair.available)c.append(el('p',repair.reason,'adv-repair-needs'));
  }
  function itemActions(c,name,cap={},item=null){
    const actions=el('div',undefined,'adv-actions');
    const args={target:name,...(item?{id:item.id}:{})};
    conditionDetails(c,cap,item||{});servingDetails(c,cap,item||{});
    if(cap.heal>0)c.append(el('p',`Restores up to ${cap.heal} HP.`));
    if(cap.usable){const b=button(item?'Use a serving':'Use',()=>act('use',args),actions);b.disabled=!!cap.broken;}
    if(cap.activatable&&item){
      const following=state.companions.some(pet=>pet.source_made_id===item.id&&(pet.is_active||pet.assistance?.active));
      const activate=button(following?'Following you':'Activate companion',()=>act('companion-activate',args),actions);activate.disabled=following;
      c.append(el('p','Activate this robot to walk together and use its help. Your other companions rest.'));
      c.append(el('p','When following: '+companionHelp(cap.assistance),'adv-companion-help'));
    }
    if(cap.slot){
      const ref=state.equipment_refs?.[cap.slot];
      const worn=ref?(item?ref.source===(item.source==='inventory'?'inventory':'made')&&ref.id===item.id:ref.source==='inventory'&&ref.name===name):state.equipment[cap.slot]===name;
      const b=button(worn?'Equipped':cap.slot==='weapon'?'Equip weapon':wearLabel(cap.slot),()=>act('equip',{...args,slot:cap.slot}),actions);
      b.disabled=worn||!!cap.broken||cap.equippable===false;
    }
    if(cap.feedable){
      const pets=ownedPets().map((pet,index)=>({pet,index})).filter(({pet})=>!(pet.is_robot??pet.care?.is_robot));
      if(pets.length){
        const select=choices(pets.map(({pet,index})=>[companionTarget(pet,index),(pet.custom_name||pet.name)+(pet.hp!==undefined?' · '+pet.hp+'/'+pet.max_hp+' HP':'')]),'Choose companion…');
        select.setAttribute('aria-label','Companion to feed '+name);const activePet=pets.find(({pet})=>pet.is_active);select.value=activePet?companionTarget(activePet.pet,activePet.index):'';actions.append(select);
        const feed=button('Feed companion',()=>act('feed',{...args,companion:select.value}),actions);feed.disabled=!select.value;select.onchange=()=>feed.disabled=!select.value;
      }
    }
    if(cap.tool==='fishing')c.append(el('p',cap.broken?'Repair before fishing.':item?.placed?'Put this fishing pole in your bag to fish.':'Used automatically when fishing while carried.'));
    if(cap.broken)c.append(el('p','Broken — repair before using.'));
    if(cap.repair||item?.max_durability&&(cap.slot||cap.tool||item.type==='companion'||cap.activatable!==undefined)){
      repairDetails(c,cap.repair);const b=button('Repair',()=>act('repair',args),actions);
      b.disabled=cap.repair?.available===false||Number(item?.durability??cap.condition?.current)>=Number(item?.max_durability??cap.condition?.max);
      if(cap.repair?.reason)b.title=cap.repair.reason;
    }
    if(item&&item.source!=='inventory'){
      if(item.placed){
        const room=item.placed.room;
        const collect=button(room===ROOM?'Put in bag':'Collect in '+(room||'its room'),()=>act('made-stow',args),actions);
        collect.disabled=room!==ROOM;
      }else button('Set down here',async()=>{await api('/api/place-made',{id:item.id,room:ROOM,x:P.x,y:P.y});status.textContent='Set down beside you.';await sync();render();},actions);
    }
    if(actions.children.length)c.append(actions);
  }
  function inventory(){
    button('Placed things and keepsakes',()=>{close.click();document.getElementById('mineBtn').click();});
    content.append(el('p','Food restores HP. Wear pieces in their own slots, and equip the sword or bow you want to use. Your other weapons stay in your bag; a carried fishing pole works automatically.'));
    heading('Your bag');
    const conditionItems=state.inventory_condition_items||[],conditionNames=new Set(conditionItems.map(item=>item.name));
    for(const [name,qty]of Object.entries(state.inventory)){if(conditionNames.has(name))continue;const c=card(name,`${qty} carried`,content,name,true);c.dataset.item=name;itemActions(c,name,state.item_actions?.[name]);}
    for(const item of conditionItems){const c=card(item.name,'Carried gear',content,item,true);c.dataset.item=item.name;c.dataset.itemId=item.id;itemActions(c,item.name,item.actions||state.inventory_condition_actions?.[item.id]||state.item_actions?.[item.name],{...item,source:'inventory'});}
    heading('Made things and equipment');
    for(const item of state.made){const name=item.name||item.what;const details=item.line||item.description||'';const cap=state.made_actions?.[item.id];const c=item.type==='companion'||cap?.activatable!==undefined?creatureCard({...item,emoji:item.emoji||'\u{1f916}'},details):card(name,details,content,item,true);c.dataset.item=name;c.dataset.itemId=item.id;itemActions(c,name,state.made_actions?.[item.id],item);}
    heading('Perishables');for(const item of state.perishables){const c=card(item.item,'',content,item.item,true);button('Process',()=>act('process',{target:item.item}),c);}
    heading('Worn equipment');for(const [slot,item]of Object.entries(state.equipment)){const c=card(slotLabel(slot),item||'Nothing equipped',content,item,!!item);c.dataset.equipmentSlot=slot;if(item){const ref=state.equipment_refs?.[slot],body=ref?.source==='inventory'?conditionItems.find(value=>value.id===ref.id):state.made.find(value=>value.id===ref?.id);if(body)conditionDetails(c,{},body);button('Unequip',()=>act('unequip',{target:slot}),c);}}
    heading('Give something');const person=input('Player name'),item=choices(Object.keys(state.inventory).map(k=>[k,k])),qty=input('Quantity');qty.type='number';qty.min='1';qty.value='1';content.append(person,item,qty);button('Give',()=>act('gift',{target:person.value,item:item.value,quantity:Number(qty.value)}));button('Give gold',()=>act('gift-gold',{target:person.value,quantity:Number(qty.value)}));
  }
  function crafting(){
    heading('Recipes you can make here');
    const search=input('Find a recipe');content.append(search);
    const list=el('div');content.append(list);
    function draw(){
      list.replaceChildren();let count=0;
      const query=search.value.toLowerCase();
      for(const [id,r]of Object.entries(state.catalog.recipes)){
        if(!r.name.toLowerCase().includes(query))continue;
        const c=card(r.name,r.description,list,r.name,true);
        c.append(el('p',Object.entries(r.materials||{}).map(([n,q])=>`${q} ${n}`).join(' · ')));
        button('Craft',()=>act('craft',{target:id}),c);count++;
      }
      for(const [id,r]of Object.entries(state.catalog.home_recipes||{})){
        if(!id.toLowerCase().includes(query))continue;
        const c=card(id,r.line,list,id,true);
        c.append(el('p',Object.entries(r.needs||{}).map(([n,q])=>q+' '+n).join(' · ')));
        button('Craft',async()=>{const d=await api('/api/craft',{recipe:id,room:ROOM});status.textContent=d.message||'Made.';await sync();render();},c);count++;
      }
      if(!count)list.append(el('p','No matching recipes here.'));
    }
    search.oninput=draw;draw();
  }
  function quests(){
    heading('Your next quest step');
    for(const q of state.guidance?.quests||[])questStepCard(q,true);
    if(state.quest)button('Abandon quest',()=>act('quest-abandon'));
    if(!state.quest&&!state.party?.current_quest)content.append(el('p','Speak to a quest giver or bookmark a first adventure in Continue.'));
    const chains=questChains();if(chains.length){heading('Quest chains');for(const q of chains)questChainCard(q);}
    if(state.catalog.quests.length){heading('Offered here');for(const q of state.catalog.quests)offeredQuestCard(q);}
  }
  function people(){heading('People here');for(const n of state.catalog.npcs){const c=card(n.name,n.role);const portrait=window.commonsArt?.npc(n.id)?.portrait;if(portrait){const img=el('img',undefined,'adv-item-art');img.src=portrait;img.alt='';img.onerror=()=>img.remove();c.prepend(img);}const words=el('textarea');words.placeholder=`Say something to ${n.name}…`;c.append(words);button('Talk',()=>act('npc-talk',{target:n.id,message:words.value}),c);if(n.id==='takoda'){button('Tell a campfire story',()=>act('tell-story',{target:n.id,message:words.value}),c);button('Listen to a tale',()=>act('hear-story',{target:n.id}),c);}button('Flirt',()=>act('flirt',{target:n.id}),c);if(n.backstory_available)button('Ask about their past',()=>act('backstory',{target:n.id}),c);if(n.story_available)button('Begin or continue story',()=>act('story-start',{target:n.id}),c);if(n.story_available)button('Take part in chapter',()=>act('story-respond',{target:n.id,message:words.value}),c);if(n.shop_available)button('Browse shop',async()=>{const result=await act('shop',{target:n.id});if(!result?.stock)return;content.replaceChildren();heading(n.name+' — shop');for(const service of result.services||[]){const row=card(service.name,service.inputs.join(', ')+' · '+service.price+' gold');button('Commission',()=>act('shop-service',{npc:n.id,target:service.id}),row);}for(const [name,price]of Object.entries(result.buy_prices||{})){if(!state.inventory[name])continue;const row=card(name,'Buys for '+price+' gold',content,name,true);button('Sell one',()=>act('sell',{npc:n.id,target:name}),row);}for(const s of result.stock){const row=s.companion_id?creatureCard(s,`${s.price} gold · ${s.description||''}`):card(s.name,`${s.price} gold - ${s.description||''}`,content,s.name,true);if(s.companion_id){row.dataset.adoptable=s.companion_id;row.append(el('p','When following: '+companionHelp(s.assistance),'adv-companion-help'));}button(s.companion_id?'Welcome companion':'Buy one',()=>act('buy',{npc:n.id,target:s.name}),row);if(state.inventory[s.name])button('Sell one',()=>act('sell',{npc:n.id,target:s.name}),row);}},c);}}
  function creatureIcon(pet){
    const icon=el('span',pet.emoji||(/droid|robot|bot/.test(pet.id||pet.companion_id||'')?'🤖':'🐾'),'adv-companion-icon');
    icon.dataset.companionIcon='';icon.setAttribute('role','img');icon.setAttribute('aria-label',(pet.name||'Companion')+' icon');
    return icon;
  }
  function companionHelp(assistance){
    if(!assistance)return 'Choose this companion to see its current help.';
    const signed=n=>(Number(n||0)>=0?'+':'')+Number(n||0);
    const parts=[`Taming ${signed(assistance.taming)}`,`hunting ${signed(assistance.hunting)}`];
    for(const key of ['attack','damage','gathering','fishing','luck','hacking'])if(assistance[key]>0)parts.push(key+' '+signed(assistance[key]));
    if(assistance.repair>0)parts.push('up to '+Math.min(50,assistance.repair*10)+'% fewer repair materials');
    if(assistance.healing>0)parts.push('up to '+assistance.healing+' HP once per encounter');
    return parts.join(' · ');
  }
  function creatureCard(pet,description){
    const c=card(pet.custom_name||pet.name,description,content,null);
    c.prepend(creatureIcon(pet));
    return c;
  }
  function cardPetHeart(id,result){
    if(!window.commonsCompanions?.claimCard(result))return;
    const c=[...content.querySelectorAll('[data-companion-id]')].find(row=>row.dataset.companionId===id);
    const icon=c?.querySelector('[data-companion-icon]');if(!icon)return;
    icon.scrollIntoView({block:'center'});
    const ns='http://www.w3.org/2000/svg',heart=document.createElementNS(ns,'svg'),path=document.createElementNS(ns,'path');
    heart.setAttribute('viewBox','0 0 32 32');heart.setAttribute('aria-hidden','true');
    path.setAttribute('d','M16 28C-3 16 2 1 16 9C30 1 35 16 16 28Z');path.setAttribute('fill','#ed70a9');heart.append(path);
    icon.style.position='relative';Object.assign(heart.style,{position:'absolute',width:'32px',height:'32px',left:'50%',top:'-14px',transform:'translate(-50%,0)',pointerEvents:'none'});icon.append(heart);
    cardAffection.push({companion:id,name:result.companion.name,expires:performance.now()+1600});while(cardAffection.length>12)cardAffection.shift();
    const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    heart.animate?.(reduced?[{opacity:1},{opacity:0}]:[{opacity:1,transform:'translate(-50%,0)'},{opacity:0,transform:'translate(-50%,-30px)'}],{duration:1600,fill:'forwards'});
    setTimeout(()=>heart.remove(),1600);
  }

  function releaseChoice(c,pet,id){
    if(pet.is_robot??pet.care?.is_robot){c.append(el('p',pet.release?.reason||'Robots stay with you; repair them or let them rest.','adv-care-note'));return;}
    const release=button('Release to the wild',()=>{
      release.hidden=true;
      const confirm=el('div',undefined,'adv-release-confirm');confirm.dataset.releaseChoice=id;
      const name=pet.custom_name||pet.name;
      confirm.append(el('p',`Release ${name}? This removes your bond, their given name and saved care. Befriending this species later starts a fresh friendship.`));
      if(pet.release?.befriend_locations?.length)confirm.append(el('p','You can meet this species again in '+pet.release.befriend_locations.join(', ')+'.'));
      const yes=button('Yes, release '+name,async()=>{const result=await act('companion-release',{target:id});if(!result?.success){confirm.remove();release.hidden=false;}},confirm);
      yes.dataset.releaseConfirm=id;
      const cancel=button('Keep '+name,()=>{confirm.remove();release.hidden=false;release.focus();},confirm);cancel.dataset.releaseCancel=id;
      c.append(confirm);cancel.focus();
    },c);
    release.dataset.companionRelease=id;release.disabled=pet.release?.available!==true;
    if(release.disabled){release.title=pet.release?.reason||'This friend cannot return to the wild.';c.append(el('p',release.title,'adv-care-note'));}
  }
  function petCareCard(pet,i,family=false){
    const id=companionTarget(pet,i),isActive=family||!!(pet.is_active??pet.assistance?.active??state.active_companion_index===i);
    const available=pet.assistance?.available!==false&&pet.care?.can_assist!==false;
    const description=family?'Your family pet.':`${pet.loyalty}% loyalty · ${pet.personality||'companion'}\n${isActive?'Following you':'Resting'}`;
    const c=creatureCard(pet,description);
    c.dataset.companionId=id;c.dataset.active=String(isActive);
    if(family)c.dataset.familyPet=pet.family_body||'true';else c.dataset.companionSlot=String(i+1);
    const petButton=button('💗 Pet',async()=>{
      focusedCompanion=id;
      const result=await act('companion-pet',{target:id});
      if(result?.success){
        if(window.commonsCompanions?.visible(result.companion))close.click();
        else cardPetHeart(id,result);
      }
    },c);petButton.dataset.companionPet=id;petButton.setAttribute('aria-label','Pet '+(pet.custom_name||pet.name));
    const care=pet.care||pet,isRobot=pet.is_robot??care.is_robot;
    meter(c,isRobot?'Robot HP':'HP',pet.hp??care.hp,pet.max_hp??care.max_hp,'health');
    if(isRobot){
      c.append(el('p','Repair with carried shiny bits or metal parts. Food is for your animal companions.','adv-care-note'));
      const materials=care.repair_materials||[],label=el('label','Repair material ', 'adv-care-choice');
      const select=choices(materials.filter(m=>(m.owned??m.quantity??0)>0).map(m=>[m.name,`${m.name} · ${m.owned??m.quantity} carried · up to ${m.heal_per_unit} HP each`]),materials.some(m=>(m.owned??m.quantity??0)>0)?'Choose material…':'No repair materials carried');
      select.setAttribute('aria-label','Repair material for '+(pet.custom_name||pet.name));label.append(select);c.append(label);
      const repair=button('Repair robot',()=>act('companion-repair',{target:id,material:select.value,quantity:1}),c);
      const hurt=Number(pet.hp??care.hp)<Number(pet.max_hp??care.max_hp),canRepair=care.repair_available!==false;
      repair.disabled=true;select.disabled=!hurt||!canRepair;select.onchange=()=>repair.disabled=!hurt||!canRepair||!select.value;
      if(!canRepair&&care.repair_reason)c.append(el('p',care.repair_reason,'adv-care-note'));
      if(!hurt)c.append(el('p','No repairs needed.','adv-care-note'));
      else if(!materials.some(m=>(m.owned??m.quantity??0)>0)&&!care.repair_reason)c.append(el('p','Find shiny bits, scrap or circuitry to restore this robot.','adv-care-note'));
    }else foodPicker(c,pet,i);
    if(!family){
      c.append(el('p',available?(isActive?'Helping now: ':'When following: ')+companionHelp(pet.assistance):pet.assistance?.reason||'Carry and repair this companion before walking together.','adv-companion-help'));
      const name=input('New name for '+(pet.custom_name||pet.name));c.append(name);
      button('Name',()=>act('companion-name',{target:id,name:name.value}),c);
      const walk=button(isActive?'Following you':'Walk together',()=>act('companion-follow',{target:id}),c);walk.dataset.companionFollow=id;walk.disabled=isActive||!available;if(!available)walk.title=pet.assistance?.reason||'This companion cannot follow yet.';
      releaseChoice(c,pet,id);
    }
    if(focusedCompanion===id){c.dataset.focused='true';c.style.outline='2px solid #bf6996';}
    return c;
  }
  async function petInRoom(identity){
    if(petting||busy)return;
    petting=true;
    petNotice('Petting '+(identity?.name||'your friend')+'…');
    try{
      let id=typeof identity==='string'?identity:identity?.instance_id||identity?.id;


      if(!id&&identity?.family_body){
        if(!state)await sync();
        id=ownedPets().find(pet=>pet.family_body===identity.family_body)?.instance_id;
        if(!id){await sync();id=ownedPets().find(pet=>pet.family_body===identity.family_body)?.instance_id;}
      }
      if(!id)throw Error('Your pet is still arriving. Try another poke in a moment.');
      const result=await act('companion-pet',{target:id});
      petNotice(result?.message||'Petting is already in progress.');
      return result;
    }catch(e){petNotice(e.message);return {success:false,error:true,message:e.message};}
    finally{petting=false;}
  }
  function companions(){
    heading('Your pets');
    content.append(el('p','Poke your animal in the room to pet them. Choose food below to share a serving and help them heal.'));
    for(const [i,pet]of (state.family_pets||[]).entries())petCareCard(pet,i,true);
    if(!state.family_pets?.length)content.append(el('p','Your family pets will appear here.'));
    heading('Your companions');
    content.append(el('p','Choose one companion to walk with you and help with hunting and taming. Your other companions rest.'));
    if(!state.companions.length)content.append(el('p','Approach a creature nearby or visit a companion shop to welcome someone.'));
    state.companions.forEach((pet,i)=>petCareCard(pet,i));
    const rest=button('Let your companion rest',()=>act('companion-rest'));
    rest.dataset.companionRest='';rest.disabled=!state.companions.some((pet,i)=>pet.is_active??pet.assistance?.active??state.active_companion_index===i);
    if(state.character_type==='all'){
      heading('Your adventure character');const type=choices(state.catalog.character_types.map(t=>[t,t.replaceAll('_',' ')]));
      content.append(type);button('Choose character type',()=>act('character-type',{target:type.value}));
    }
    heading('Creatures nearby');
    for(const animal of state.catalog.animals){
      const c=creatureCard(animal,animal.description||animal.tip);c.dataset.tameable=animal.id;
      c.append(el('p','When following: '+companionHelp(animal.assistance),'adv-companion-help'));
      button('Approach',()=>window.commonsField.approach(animal.id),c);
    }
    heading('Transformations');for(const [id,f]of Object.entries(state.catalog.forms)){const c=card(f.name,f.description);button('Take this form',()=>act('transform',{target:id}),c);}
  }
  function party(){heading('Adventure together');if(state.party){card('Your party',(state.party.members||[]).map(m=>typeof m==='object'?m.name||m.player_id:m).join(', '));button('Leave party',()=>act('party-leave'));}else button('Create party',()=>act('party-create'));
    const person=input('Invite player');content.append(person);button('Invite',()=>act('party-invite',{target:person.value}));
    for(const inv of state.party_invites||[]){const c=card('Invitation from '+inv.leader_name,'An adventure together');button('Join',()=>act('party-join',{target:inv.party_id}),c);button('Decline',()=>act('party-decline',{target:inv.party_id}),c);}
    heading('Dungeons');for(const d of state.catalog.dungeons){const c=card(d.name,`${d.description}\n${d.min_party}–${d.max_party} adventurers`);button('Enter with party',()=>act('dungeon-start',{target:d.dungeon_id}),c);}
    if(state.dungeon?.success){card('Current encounter',state.dungeon.message||'');}
    if(state.dungeon?.data){const roles=choices(Object.keys(state.catalog.roles).map(r=>[r,r]));content.append(roles);button('Choose role',()=>act('dungeon-role',{target:roles.value}));const role=state.dungeon.data.roles?.[credentials().by.toLowerCase()];const ability=choices((state.catalog.roles[role]?.abilities||[]).map(a=>[a,a])),ally=choices([...(state.party?.members||[]).map(m=>[m.player_id,m.name]),...(state.dungeon.data.active_room?.enemies||[]).map(e=>[e.name,e.name])],'Choose a target');content.append(ability,ally);button('Use ability',()=>act('dungeon-ability',{ability:ability.value,target:ally.value}));button('Recovery roll',()=>act('dungeon-recover'));button('Pass turn',()=>act('dungeon-pass'));}
    if(state.dungeon?.data){const enemy=choices((state.dungeon?.data?.active_room?.enemies||[]).map(e=>[e.name,e.name]),'Choose an enemy');content.append(enemy);button('Attack dungeon enemy',()=>act('dungeon-attack',{target:enemy.value}));button('Flee dungeon',()=>act('dungeon-flee'));}
    heading('Competitive play');button(state.competitive?'Turn off pickpocketing':'Opt into pickpocketing',()=>act('competitive',{enabled:!state.competitive}));
    if(state.region==='home')content.append(el('p','Home is a peaceful place. Duels and pickpocketing are available out in the wider world.'));
    else{const rival=input('Player here');content.append(rival);button('Offer a duel',()=>act('duel-challenge',{target:rival.value}));button('Try pickpocketing',()=>act('steal',{target:rival.value}));}
    for(const d of state.duels||[]){const c=card('Duel: '+d.players.join(' and '),d.status+' · '+Object.entries(d.hp).map(([n,h])=>n+': '+h+' HP').join(' · '));if(d.room!==state.room){c.append(el('p','Return to '+d.room+' to continue.'));continue;}if(d.status==='offered'&&d.players[1]===credentials().by.toLowerCase())button('Accept duel',()=>act('duel-accept',{duel:d.id}),c);if(d.status==='active'){if(d.turn===credentials().by.toLowerCase())button('Attack',()=>act('duel-attack',{duel:d.id}),c);else c.append(el('p',d.turn+' has the next turn.'));button('Yield',()=>act('duel-flee',{duel:d.id}),c);}}
    const wager=input('Gold to wager');wager.type='number';wager.min='1';content.append(wager);button('Play at the game table',()=>act('gamble',{amount:Number(wager.value)}));
    heading('World events');if(state.event){card(state.event.title,state.event.hook+'\n'+(state.event.resolved?'Concluded':state.event.stage));for(const m of (state.event.messages||[]).slice(-5))card(m.by,m.text);}button('Look for an event',()=>act('event-start'));const response=el('textarea');response.placeholder='Your action in the event…';content.append(response);button('Take part',()=>act('event-respond',{message:response.value}));
  }
  function journal(){heading(`Level ${state.level} · ${state.xp} XP`);content.append(el('p',`Achievements: ${state.achievements.join(', ')||'Your adventure is beginning.'}`));for(const entry of [...state.journal].reverse()){card(entry.action,entry.result?.message||entry.result?.combat_text||'',content);}}
  async function music(){heading('The music library');content.append(el('p','Enable music and adjust volume using the controls in the upper right.'));const data=await api('/api/game/music');library=data.library;parent.postMessage({type:'commons-music-library',library},location.origin);
    for(const t of Object.values(library.tracks)){const c=card(t.title,t.description);const audio=el('audio');audio.onplay=()=>parent.postMessage({type:'commons-voice',playing:true},location.origin);audio.onpause=audio.onended=()=>parent.postMessage({type:'commons-voice',playing:false},location.origin);audio.controls=true;audio.preload='none';audio.src=t.url;c.append(audio);}
    if(credentials().by.toLowerCase()!==window.COMMONS_CONFIG.adminName)return;
    const upload=el('input');upload.type='file';upload.accept='audio/mpeg,audio/ogg,audio/wav,audio/mp4';content.append(upload);button('Upload track',async()=>{const file=upload.files[0];if(!file)throw Error('Choose an audio file.');const c=credentials();const r=await fetch('/api/game/music/upload?'+new URLSearchParams({as:c.by,key:c.key,title:file.name}),{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});const d=await r.json();if(!r.ok)throw Error(d.error);status.textContent='Track added.';render();});
    heading('Assign music');const scope=choices([['room:'+ROOM,'This room'],...Object.entries(state.regions).map(([id,r])=>['region:'+id,r.name])]);const tracks=choices(Object.values(library.tracks).map(t=>[t.id,t.title]),'Silence');tracks.multiple=true;tracks.size=6;content.append(scope,tracks);button('Assign',async()=>{const d=await api('/api/game/music/assign',{scope:scope.value,tracks:[...tracks.selectedOptions].map(o=>o.value).filter(Boolean),silent:!tracks.value});parent.postMessage({type:'commons-music-library',library:d.library},location.origin);status.textContent='Music assignment saved.';});
  }
  function dungeonSession(){const d=state.dungeon_session;if(!d?.active)return;heading('Expedition pause and return');card(d.name,`${d.description} ${d.paused?'Paused.':'Turn: '+d.turn_name+'.'}`);for(const action of d.controls)actionChoice({action,label:action.replace('dungeon-',''),choices:{}},content);if(d.away.length)content.append(el('p','Stepped away: '+d.away.map(p=>p.name).join(', ')));}
  const views={Continue:continueAdventure,Explore:explore,Inventory:inventory,Crafting:crafting,Quests:quests,People:people,
    [PETS_VIEW]:()=>{companions();companionAbilities();},Projects:projects,'Field guide':fieldGuide,
    Party:()=>{party();dungeonSession();},Journal:journal,Music:music};
  for(const name of Object.keys(views)){const b=button(name,()=>{active=name;render();content.scrollTop=0;},tabs);b.dataset.tab=name;}
  function render(){updatePanelTitle();if(!state)return;content.replaceChildren();recovery();selectTab();if(active==='Inventory'){const g=state.guidance;const c=card('🧭 Continue your adventure',g?.journey?.step||g?.quests?.[0]?.step||g?.notices?.[0]||'Choose a goal, discover this place or help a shared project.');button('See next steps',()=>{active='Continue';render();},c);}Promise.resolve(views[active]()).catch(e=>status.textContent=e.message);}
  function updateReadyChoices(){
    for(const b of content.querySelectorAll('[data-ready-at]')){
      const remaining=Math.max(0,Math.ceil(Number(b.dataset.readyAt)-Date.now()/1000)),blocked=b.dataset.readyBlocked==='true';
      b.textContent=b.dataset.readyLabel+(remaining?` (${remaining}s)`:'');b.disabled=remaining>0||blocked;
      if(!blocked){const hint=remaining?`Ready in ${remaining} seconds.`:'Ready now.';b.title=remaining?hint:'';if(b.questReadyHint)b.questReadyHint.textContent=hint;}
    }
  }
  setInterval(updateReadyChoices,1000);
  document.addEventListener('commons-art-ready',()=>{if(panel.style.display==='flex')render();});
  const show=async(tab='Inventory')=>{if(window.commonsField?.busy())return;active=canonicalTab(tab);updatePanelTitle();content.scrollTop=0;open.hidden=true;petsOpen.hidden=true;document.dispatchEvent(new Event('commons-pause'));panel.style.display='flex';window.commonsAdventureOpen=true;close.focus();try{await sync();render();}catch(e){status.textContent=e.message;}};
  open.onclick=()=>{returnControl=open;show('Inventory');};
  petsOpen.onclick=()=>{returnControl=petsOpen;focusedCompanion='';show('Pets');};
  close.onclick=()=>{panel.style.display='none';open.hidden=false;petsOpen.hidden=false;window.commonsAdventureOpen=false;returnControl.focus();};
  document.addEventListener('keydown',e=>{
    if(panel.style.display!=='flex')return;
    if(e.key==='Escape'){e.preventDefault();close.click();}
    if(e.key==='Tab'){
      const fields=[...panel.querySelectorAll('button,input,select,textarea,a[href],audio[controls]')].filter(n=>!n.disabled&&n.getClientRects().length);
      const first=fields[0],last=fields[fields.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    }
  });
  const npcImages={};
  const authoredNPCs={};
  window.drawCommonsNPCs=(bodies)=>{for(const n of R?.props||[]){if(!n.game_npc)continue;
    const art=window.commonsArt?.npc(n.npc_art||n.game_npc);const artKey=n.npc_art||n.game_npc;
    if(n.art&&npcImages[n.art]===undefined){npcImages[n.art]=null;loadImg(n.art,im=>npcImages[n.art]=im);}
    if(art&&!authoredNPCs[artKey]){const record=authoredNPCs[artKey]={frames:[],art};art.frames.forEach((src,i)=>loadImg(src,im=>record.frames[i]=im));}
    if(n.npc_sheet&&npcImages[n.id]===undefined){npcImages[n.id]=null;loadImg('/'+n.npc_sheet,im=>npcImages[n.id]=im);}
    bodies.push({y:n.y,go:()=>{const x=(n.x+(n.w||1)/2)*T,y=(n.y+1)*T,im=npcImages[n.id];
      const authored=authoredNPCs[artKey],frame=authored?.frames[Math.floor(performance.now()/1000*(art?.fps||6))%(art?.frames.length||1)]||authored?.frames.find(Boolean);
      if(n.art&&npcImages[n.art]){const custom=npcImages[n.art];cx.drawImage(custom,n.x*T,(n.y+1-n.h)*T,n.w*T,n.h*T);}
      else if(frame){const [left,top,right,bottom]=art.crop;const height=2.65*T*CHARSCALE*(n.npc_scale||1),width=height*(right-left)/(bottom-top);cx.drawImage(frame,left,top,right-left,bottom-top,x-width/2,y-height,width,height);}
      else if(im){const f=n.npc_frame||64;cx.drawImage(im,0,0,f,f,x-32,y-64,64,64);}
      else{cx.fillStyle='#6f9f94';cx.beginPath();cx.ellipse(x,y-25,11,19,0,0,Math.PI*2);cx.fill();cx.fillStyle='#e7cfad';cx.beginPath();cx.arc(x,y-49,9,0,Math.PI*2);cx.fill();}
      cx.font='12px Georgia';cx.textAlign='center';cx.fillStyle='#fff0cf';cx.strokeStyle='#071b18';cx.lineWidth=4;cx.strokeText(n.label,x,y-(n.art?n.h*T:frame?2.65*T*CHARSCALE*(n.npc_scale||1):64)-7);cx.fillText(n.label,x,y-(n.art?n.h*T:frame?2.65*T*CHARSCALE*(n.npc_scale||1):64)-7);cx.textAlign='start';}});
  }};
  window.drawCommonsForm=()=>{if(!state||state.form==='human')return;cx.save();cx.strokeStyle='#c0daf4';cx.fillStyle='#ecddff';cx.globalAlpha=.75;cx.beginPath();cx.ellipse(P.x*T,P.y*T,23,10,0,0,Math.PI*2);cx.stroke();cx.font='11px Georgia';cx.textAlign='center';cx.fillText(state.form.replaceAll('_',' '),P.x*T,P.y*T-68);cx.restore();};
  window.drawCommonsCommunityChanges=bodies=>{for(const [index,change]of (state?.environment?.changes||[]).filter(c=>c.room===ROOM).slice(0,5).entries()){
    const anchor=(R?.props||[]).find(p=>/terminal|workbench|station|table/.test((p.id||'')+' '+(p.label||'')))||R?.spawn||{x:2,y:2};
    const x=anchor.x+(anchor.w||1)/2+index*.55,y=anchor.y+(anchor.h||1);
    bodies.push({y,go:()=>{cx.save();cx.textAlign='center';cx.font=`${T*.65}px sans-serif`;cx.fillText(change.icon||'🏮',x*T,(y-.2)*T);cx.font=`${Math.max(9,T*.18)}px sans-serif`;cx.fillStyle='#fff4cf';cx.shadowColor='#000';cx.shadowBlur=3;cx.fillText(change.title,x*T,(y+.1)*T);cx.restore();}});
  }};
  const announce=setInterval(async()=>{if(typeof R==='undefined'||!R||typeof ME==='undefined'||!ME)return;clearInterval(announce);parent.postMessage({type:'commons-room',room:ROOM,region:R.region||'home'},location.origin);try{await sync();const d=await api('/api/game/music');parent.postMessage({type:'commons-music-library',library:d.library},location.origin);}catch{}},400);
  window.commonsGame={act,sync,pet:petInRoom,close:()=>close.click(),open:show,people:()=>show('People'),
    companion:async(identity,care='pet')=>{
      returnControl=petsOpen;
      focusedCompanion=typeof identity==='string'?identity:identity?.instance_id||'';
      await show('Pets');
      if(!focusedCompanion&&identity?.family_body){focusedCompanion=ownedPets().find(p=>p.family_body===identity.family_body)?.instance_id||'';render();}
      const c=[...content.querySelectorAll('[data-companion-id]')].find(row=>row.dataset.companionId===focusedCompanion);
      c?.scrollIntoView({block:'nearest'});c?.querySelector(care==='feed'?'[data-companion-food]':'[data-companion-pet]')?.focus();
    },
    terminal:async target=>{terminalName=target||state?.catalog.hacking[0]?.name||'';terminalResult=null;await show('Explore');content.scrollTop=0;}};
  const enter=(tab)=>show(tab);
  const taming=document.getElementById('tameBtn');if(taming)taming.onclick=()=>enter('Companions');
  for(const id of ['rodBtn','huntBtn']){const b=document.getElementById(id);if(b)b.onclick=()=>enter('Explore');}
  const oldText=window.render_game_to_text;window.render_game_to_text=()=>JSON.stringify({...JSON.parse(oldText()),adventure:state?{gold:state.gold,hp:state.hp,region:state.region,inventory:state.inventory,equipment:state.equipment,form:state.form,quest:state.quest,dungeon:state.dungeon,guidance:state.guidance,field_guide:state.field_guide,community:state.community,companion_abilities:state.companion_abilities,dungeon_session:state.dungeon_session,pending_action:pendingAction()?{action:pendingAction().body.action,room:pendingAction().room}:null,tab:active,open:panel.style.display==='flex',...(terminalName&&active==='Explore'?{terminal:{name:terminalName,character:terminalCharacter,amount:terminalAmount,balance:state.catalog.terminal_setup?.credits_balance,result:terminalResult}}:{})}:null});
  const inventoryText=window.render_game_to_text;
  window.render_game_to_text=()=>{
    const data=JSON.parse(inventoryText());
    if(data.adventure)data.adventure.companions=state.companions.map((pet,index)=>({instance_id:companionTarget(pet,index),
      name:pet.custom_name||pet.name,family_body:pet.family_body||null,is_active:pet.is_active??pet.assistance?.active??false,hp:pet.hp,max_hp:pet.max_hp}));
    if(data.adventure)data.adventure.family_pets=(state.family_pets||[]).map(pet=>({instance_id:pet.instance_id,
      name:pet.custom_name||pet.name,family_body:pet.family_body,is_family_pet:true,hp:pet.hp,max_hp:pet.max_hp}));
    data.card_affection=cardAffection.filter(effect=>effect.expires>performance.now()).map(effect=>({companion:effect.companion,name:effect.name,remaining_ms:Math.round(effect.expires-performance.now())}));
    return JSON.stringify(data);
  };
})();
