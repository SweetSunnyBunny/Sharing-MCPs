
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.CommonsCompanionAffection=factory();
})(globalThis,function(){
  function create({now=()=>performance.now(),reducedMotion=false,lifetime=1600,limit=12}={}){
    const effects=[],seen=new Map();
    function prune(){const time=now();while(effects.length&&time-effects[0].at>=lifetime)effects.shift();}
    function claim(identity,eventId){
      const key=String(identity.instance_id||identity.id||'')+':'+String(eventId||'');
      if(!eventId||seen.has(key))return false;
      seen.set(key,now());while(seen.size>128)seen.delete(seen.keys().next().value);
      return true;
    }
    function add(identity,anchor,eventId){
      prune();if(!anchor()||!claim(identity,eventId))return false;
      effects.push({identity:{...identity},anchor,at:now()});while(effects.length>limit)effects.shift();return true;
    }
    function visible(){prune();return effects.flatMap(effect=>{const anchor=effect.anchor();return anchor?[{...effect,anchor,age:now()-effect.at}]:[];});}
    function draw(ctx,tile){
      for(const effect of visible()){
        const progress=effect.age/lifetime,count=reducedMotion?1:3;
        for(let i=0;i<count;i++){
          const rise=reducedMotion?0:progress*tile*.8+i*tile*.14;
          const x=effect.anchor.x*tile+(reducedMotion?0:(i-1)*tile*.24),y=effect.anchor.y*tile-rise;
          const size=tile*(reducedMotion?.28:.22),alpha=Math.max(0,1-progress);
          ctx.save();ctx.globalAlpha=alpha;ctx.fillStyle=['#ff87b5','#f5b3d0','#ff679d'][i];
          ctx.translate(x,y);ctx.scale(size,size);ctx.beginPath();ctx.moveTo(0,.6);
          ctx.bezierCurveTo(-1,-.05,-.7,-.85,0,-.38);ctx.bezierCurveTo(.7,-.85,1,-.05,0,.6);
          ctx.fill();ctx.restore();
        }
      }
    }
    return {add,claim,draw,state:()=>visible().map(effect=>({companion:effect.identity.instance_id||effect.identity.id,
      name:effect.identity.name,family_body:effect.identity.family_body||null,x:effect.anchor.x,y:effect.anchor.y,
      remaining_ms:Math.max(0,Math.round(lifetime-effect.age)),reduced_motion:reducedMotion}))};
  }
  return {create};
});
