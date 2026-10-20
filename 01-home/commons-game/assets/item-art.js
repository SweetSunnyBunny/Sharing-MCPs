

(() => {
  const normal=name=>String(name||'').toLowerCase().replace(/[^a-z0-9]/g,'');

  const raven='/assets/adventure/icons/Free%20-%20Raven%20Fantasy%20Icons/Separated%20Files/64x64/';
  const fallbackIcons=Object.freeze({
    item:raven+'fc1.png', weapon:raven+'fc1447.png', dagger:raven+'fc1443.png',
    spear:raven+'fc1493.png', axe:raven+'fc1467.png', firearm:raven+'fc1447.png',
    bow:'/item-art/longbow.png', ammo:'/item-art/arrow.png',
    armor:raven+'fc1922.png', head:raven+'fc1906.png', hands:raven+'fc1938.png',
    feet:'/item-art/hide_boots.png', cloak:'/item-art/warm_cloak.png',
    accessory:raven+'fc1848.png', ring:'/item-art/fry_touched_ring.png',
    bag:raven+'fc1.png', food:'/item-art/hearty_stew.png',
    drink:raven+'fc503.png', potion:'/item-art/greater_healing_potion.png',
    treat:'/item-art/animal_treats.png', fish:'/item-art/trout.png',
    rod:'/item-art/fishing_rod.png', bait:'/item-art/worms.png',
    herb:'/item-art/Herbs.png', flower:'/item-art/wildflower.png',
    fruit:'/item-art/apple.png', mushroom:'/item-art/forest_mushroom.png',
    meat:'/item-art/venison.png', hide:'/item-art/deer_hide.png',
    bone:'/item-art/small_bones.png', wood:'/item-art/oak-branch.png',
    stone:'/item-art/river_stone.png', metal:'/item-art/scrap_metal.png',
    cloth:raven+'fc205.png', gem:raven+'fc164.png', treasure:'/item-art/lucky_coin.png',
    map:'/item-art/world_map.png', scroll:'/item-art/starbeam_scroll.png',
    tool:'/item-art/multi-tool-hacking-device.png'
  });
  const itemName=value=>typeof value==='object'&&value!==null
    ? value.name||value.label||value.item||value.what||value.recipe||'' : value;
  function iconCategory(value){
    const row=typeof value==='object'&&value!==null?value:{};
    const name=String(itemName(value)||'').toLowerCase().replace(/[_-]/g,' ');
    const type=String(row.type||'').toLowerCase();
    const slot=String(row.equipment_slot||row.slot||'').toLowerCase();
    if(type==='companion_treat'||/\btreats?\b|birdseed|predator jerky/.test(name))return 'treat';
    if(/fishing (rod|pole)|\brod\b/.test(name))return 'rod';
    if(/\bbait\b|\bworms?\b|\blure\b/.test(name))return 'bait';
    if(/\barrows?\b|\bammo\b|ammunition|bullets?/.test(name))return 'ammo';
    if(/\b(longbow|shortbow|crossbow|bow)\b/.test(name))return 'bow';
    if(/\b(dagger|knife)\b/.test(name))return 'dagger';
    if(/\bspear\b/.test(name))return 'spear';
    if(/\b(greataxe|axe)\b/.test(name))return 'axe';
    if(/shotgun|rifle|pistol|revolver|blaster|\bgun\b/.test(name))return 'firearm';
    if(/sword|\bblade\b|\bkatana\b/.test(name))return 'weapon';
    if(/\b(cloak|cape)\b/.test(name))return 'cloak';
    if(/\b(boots?|shoes?|slippers?|greaves)\b/.test(name))return 'feet';
    if(/\b(helm|helmet|hat|hood)\b/.test(name))return 'head';
    if(/\b(gloves?|gauntlets?|bracers?)\b/.test(name))return 'hands';
    if(/\bring\b/.test(name))return 'ring';
    if(/charm|amulet|necklace|pendant|badge|talisman|\bbelt\b|quiver/.test(name))return 'accessory';
    if(/armou?r|breastplate|cuirass|chainmail|vest|jerkin/.test(name))return 'armor';
    if(/backpack|satchel|basket|pouch|\bbag\b/.test(name))return 'bag';
    if(type==='potion'||/potion|tonic|tincture|antidote|salve|elixir|\bbrew\b/.test(name))return 'potion';
    if(type==='food'||/stew|soup|\bpie\b|roast|toast|cookies?|breakfast|meal|rations?|sandwich|snax|bread|cheese|jam|off the fire/.test(name))return 'food';
    if(type==='drink'||/\b(tea|ale|beer|wine|coffee|cordial|water|milk)\b/.test(name))return 'drink';
    if(/\b(trout|salmon|carp|perch|bass|fish|catfish|sturgeon)\b|moonfish/.test(name))return 'fish';
    if(/\bmeat\b|venison|jerky/.test(name))return 'meat';
    if(/\b(pelt|hide|leather)\b/.test(name))return 'hide';
    if(/bones?|antler|tusk|fang/.test(name))return 'bone';
    if(/\bmushrooms?\b|chanterelles/.test(name))return 'mushroom';
    if(/\b(herbs?|mint|sage|thyme|rosemary|lavender|sorrel)\b|dried bundle/.test(name))return 'herb';
    if(/flowers?|bouquet|wreath|lily|lilies|rose|forget me not|daisy/.test(name))return 'flower';
    if(/berries|berry|currant|\b(apple|pear|plum|fruit|nuts?|hazelnuts|potato|roots?)\b/.test(name))return 'fruit';
    if(/firewood|wood|branch|\bstick\b|timber|log/.test(name))return 'wood';
    if(/stone|\brock\b|cairn/.test(name))return 'stone';
    if(/metal|ingot|iron|copper|ore|plating/.test(name))return 'metal';
    if(/thread|cloth|fabric|silk/.test(name))return 'cloth';
    if(/\bgem\b|gemstone|crystal/.test(name))return 'gem';
    if(/\bmap\b/.test(name))return 'map';
    if(/scroll|book|pass|letter/.test(name))return 'scroll';
    if(/coin|gold|shiny|shinies|keepsake|treasure/.test(name))return 'treasure';
    if(type==='tool'||/snare|trap|torch|rope|tool|sensor|circuit|component|device/.test(name))return 'tool';
    return ({weapon:'weapon',cloak:'cloak',feet:'feet',boots:'feet',head:'head',helmet:'head',
      hands:'hands',gloves:'hands',ring:'ring',accessory:'accessory',charm:'accessory',
      neck:'accessory',waist:'accessory',armor:'armor',body:'armor',chest:'armor'})[slot]||'item';
  }
  let catalog={items:{},npcs:{}};
  const api=window.commonsArt={
    item:name=>catalog.items[normal(name)]||null,
    icon:(value,options={})=>{
      if(!options.fallback){
        const custom=typeof value==='object'&&value!==null?value.art:null;
        const exact=custom||catalog.items[normal(itemName(value))];
        if(exact)return exact;
      }
      return fallbackIcons[iconCategory(value)];
    },
    iconCategory,
    fallbackIcons,
    npc:id=>catalog.npcs[id]||null,
    catalog:()=>catalog,
    ready:fetch('/assets/art-catalog.json?t='+Date.now(),{cache:'no-store'}).then(r=>{
      if(!r.ok)throw Error('Art catalog unavailable');return r.json();
    }).then(data=>{catalog=data;document.dispatchEvent(new Event('commons-art-ready'));}).catch(()=>{})
  };
  api.decoratePlaced=thing=>{
    if(!thing.art&&thing.made_id)thing.art=api.item(thing.label)||'';
    return thing;
  };
})();
