
(() => {
  const frame=document.querySelector('#world'),play=document.querySelector('#play'),volume=document.querySelector('#volume'),label=document.querySelector('#track');

  let enabled=localStorage.getItem('commons-music-enabled')==='true',blocked=false;
  let library={tracks:{},assignments:{}},room='',region='home',selected='',token=0,duck=1,combat=false;
  let gain=Number(localStorage.getItem('commons-music-volume')??'.25');
  gain=Number.isFinite(gain)?Math.max(0,Math.min(1,gain)):.25;volume.value=String(gain*100);
  let current=new Audio(),outgoing=new Audio();
  const title=()=>{play.textContent=blocked?'♫ Tap to resume':enabled?'♫ Pause':'♫ Enable music';};title();
  function assignment(){return (combat&&library.assignments['region:combat'])||library.assignments['room:'+room]||library.assignments['region:'+region];}
  function choices(){const a=assignment();return a&&!a.silent?(a.tracks||[]).filter(id=>library.tracks[id]):[];}
  function choose(){const ids=choices();return ids.includes(selected)?selected:ids[0]||'';}
  async function resume(){if(!enabled||document.hidden||!selected)return;try{await current.play();blocked=false;}catch{blocked=true;}title();}
  async function change(forced){
    const next=forced===undefined?choose():forced;
    if(next===selected){current.loop=choices().length<=1;if(current.paused)await resume();return;}
    const seq=++token;outgoing.pause();[current,outgoing]=[outgoing,current];selected=next;
    const track=library.tracks[next];label.textContent=track?.title||'';
    current.removeAttribute('src');if(track)current.src=track.url;
    current.loop=choices().length<=1;current.volume=0;
    await resume();const start=performance.now(),oldVolume=outgoing.volume;
    function fade(now){if(seq!==token)return;const t=Math.min(1,(now-start)/2000);current.volume=t*gain*duck;outgoing.volume=oldVolume*(1-t);if(t<1)requestAnimationFrame(fade);else outgoing.pause();}
    requestAnimationFrame(fade);
  }
  for(const audio of [current,outgoing]){


    audio.preload='none';
    audio.addEventListener('ended',()=>{if(audio!==current)return;const ids=choices();if(ids.length)change(ids[(ids.indexOf(selected)+1)%ids.length]);});
    audio.addEventListener('error',()=>{if(audio===current&&selected)label.textContent='Track unavailable';});
  }
  play.onclick=async()=>{enabled=blocked?true:!enabled;blocked=false;localStorage.setItem('commons-music-enabled',String(enabled));title();if(enabled){await change();await resume();}else{current.pause();outgoing.pause();}};
  volume.oninput=()=>{gain=Number(volume.value)/100;localStorage.setItem('commons-music-volume',String(gain));current.volume=gain*duck;};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){current.pause();outgoing.pause();}else resume();});
  function sceneMessage(event){
    if(event.origin!==location.origin||event.source!==frame.contentWindow)return;
    const d=event.data;
    if(d?.type==='commons-room'){room=d.room;region=d.region||'home';history.replaceState(null,'','?room='+encodeURIComponent(room));change();}
    if(d?.type==='commons-music-library'){library=d.library;change();}
    if(d?.type==='commons-voice'){duck=d.playing?.3:1;current.volume=gain*duck;}
    if(d?.type==='commons-combat'){combat=!!d.active;change();}
  }
  window.addEventListener('message',sceneMessage);


  const startup=window.commonsSceneStartup;
  if(startup){
    window.removeEventListener('message',startup.capture);
    for(const event of startup.events)sceneMessage(event);
    delete window.commonsSceneStartup;
  }
  window.render_game_to_text=()=>JSON.stringify({music:{enabled,blocked,selected,room,region,volume:gain},world:JSON.parse(frame.contentWindow.render_game_to_text?.()||'{}')});
  window.advanceTime=ms=>frame.contentWindow.advanceTime?.(ms);
})();
