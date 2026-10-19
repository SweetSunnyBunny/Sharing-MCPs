

(function(root){
  const geometry = {
    rect(o){ return {x:o.x,y:o.y+1-(o.h||1),w:o.w||1,h:o.h||1}; },
    feet(o){ return {x:o.x+.5,y:o.y+1}; },
    storedFeet(p){ return {x:p.x-.5,y:p.y-1}; },
    footprint(room,x,y,body){


      const h=room.view==='top'?.28:body.h;
      return {x:x-body.w/2,y:y-h,w:body.w,h:h};
    },
    blocked(room,x,y,body){
      const b=this.footprint(room,x,y,body);

      const epsilon=1e-6;
      for(let ty=Math.floor(b.y+epsilon);ty<Math.ceil(b.y+b.h-epsilon);ty++){
        for(let tx=Math.floor(b.x+epsilon);tx<Math.ceil(b.x+b.w-epsilon);tx++){
          if(ty<0||ty>=room.rows.length||tx<0||tx>=room.rows[ty].length)return true;
          if(room.legend[room.rows[ty][tx]]?.solid)return true;
        }
      }
      return false;
    }
  };
  if(typeof module!=='undefined'&&module.exports)module.exports=geometry;
  else root.CommonsGeometry=geometry;
})(globalThis);
