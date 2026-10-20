

(function(root){
  'use strict';
  function pointIn(points,p){
    if(!Array.isArray(points)||points.length<3)return false;
    let inside=false;
    for(let i=0,j=points.length-1;i<points.length;j=i++){
      const a=points[i],b=points[j];
      if((a[1]>p.y)!==(b[1]>p.y)&&p.x<(b[0]-a[0])*(p.y-a[1])/(b[1]-a[1])+a[0])inside=!inside;
    }
    return inside;
  }
  const paint={
    summary(room){
      const rows=room?.fishing_paint||[],text=rows.join('');
      return {painted_tiles:(text.match(/1/g)||[]).length,
        erased_tiles:(text.match(/0/g)||[]).length,
        has_overrides:/[01]/.test(text),
        inherited_zones:(room?.fishing_zones||[]).length,
        inherited_props:(room?.props||[]).filter(prop=>prop.fishable).length};
    },
    baseContains(room,p){
      if((room.fishing_zones||[]).some(z=>pointIn(z.points,p)))return true;
      if((room.props||[]).some(z=>z.fishable&&p.x>=z.x&&p.x<=z.x+(z.w||1)&&p.y>=z.y+1-(z.h||1)&&p.y<=z.y+1))return true;
      const tile=(room.legend||{})[room.rows[Math.floor(p.y)]?.[Math.floor(p.x)]];
      return !!(room.water&&tile&&/\b(water|river|lake|pond|stream|sea|ocean)\b/i.test(tile.name||'')&&!/bank|bridge/i.test(tile.name||''));
    },
    contains(room,p){
      if(!room||!p||![p.x,p.y].every(Number.isFinite))return false;
      const x=Math.floor(p.x),y=Math.floor(p.y),rows=room.rows||[];
      if(y<0||y>=rows.length||x<0||x>=rows[y].length)return false;
      const value=room.fishing_paint?.[y]?.[x];
      return value==='1'?true:value==='0'?false:this.baseContains(room,p);
    },

    paint(room,cells,value){
      if(!['1','0','.'].includes(value))throw new Error('Unknown fishing brush.');
      const rows=room.rows||[],before=room.fishing_paint;
      let mask=before?before.slice():rows.map(row=>'.'.repeat(row.length)),changed=false;
      for(const {x,y} of cells){
        if(!Number.isInteger(x)||!Number.isInteger(y)||y<0||y>=rows.length||x<0||x>=rows[y].length)continue;
        if(mask[y][x]===value)continue;
        mask[y]=mask[y].slice(0,x)+value+mask[y].slice(x+1);changed=true;
      }
      if(changed){
        room.fishing_paint=mask;
      }
      return changed;
    },


    stroke(from,to){
      if(!from||!to||![from.x,from.y,to.x,to.y].every(Number.isFinite))return [];
      const dx=to.x-from.x,dy=to.y-from.y,sx=Math.sign(dx),sy=Math.sign(dy);
      let x=Math.floor(from.x),y=Math.floor(from.y);
      const endX=Math.floor(to.x),endY=Math.floor(to.y),cells=[{x,y}];
      const deltaX=dx?1/Math.abs(dx):Infinity,deltaY=dy?1/Math.abs(dy):Infinity;
      let crossX=dx?((sx>0?x+1:x)-from.x)/dx:Infinity;
      let crossY=dy?((sy>0?y+1:y)-from.y)/dy:Infinity;
      while(x!==endX||y!==endY){
        if(x===endX)crossX=Infinity;
        if(y===endY)crossY=Infinity;
        if(crossX<crossY){x+=sx;crossX+=deltaX;}
        else if(crossY<crossX){y+=sy;crossY+=deltaY;}
        else{x+=sx;y+=sy;crossX+=deltaX;crossY+=deltaY;}
        cells.push({x,y});
      }
      return cells;
    }
  };
  if(typeof module!=='undefined'&&module.exports)module.exports=paint;
  else root.CommonsFishingAreas=paint;
})(globalThis);
