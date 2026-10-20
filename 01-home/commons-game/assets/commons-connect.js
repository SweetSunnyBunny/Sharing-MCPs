(() => {
const $=id=>document.getElementById(id);
let name=localStorage.getItem('commons-name')||'',key=localStorage.getItem('commons-key')||'';
const message=text=>$('message').textContent=text;
async function api(route,body={}){const r=await fetch('/api/agent/'+route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({by:name,key,...body})});const d=await r.json();if(!r.ok)throw Error(d.error||'Could not connect');return d;}
async function refresh(){
  $('identity').textContent=name?'New connection keys will control '+name+'.':'Open your invitation link first, or paste it here.';
  $('signin').hidden=!!(name&&key);$('create').disabled=!(name&&key);
  $('connections').replaceChildren();
  if(!name||!key)return;
  try{const d=await api('connections');if(!d.connections.length)$('connections').textContent='No active connections yet.';
  for(const c of d.connections){const row=document.createElement('div');row.className='row';const label=document.createElement('span');label.textContent=c.label;const b=document.createElement('button');b.className='secondary';b.textContent='Disconnect';b.onclick=async()=>{b.disabled=true;try{await api('revoke',{id:c.id});if($('new-key').dataset.id===c.id){$('new-key').hidden=true;$('key-output').value='';}await refresh();message('Disconnected '+c.label+'.');}catch(e){message(e.message);b.disabled=false;}};row.append(label,b);$('connections').append(row);}}
  catch(e){message(e.message);$('signin').hidden=false;}
}
$('sign').onclick=async()=>{try{const url=new URL($('invite').value);if(url.origin!==location.origin)throw Error('Use an invitation for this Commons website.');const n=url.searchParams.get('as'),k=url.searchParams.get('key');if(!n||!k)throw Error('The invitation needs a character name and key.');const oldName=name,oldKey=key;name=n;key=k;try{await api('connections');}catch(e){name=oldName;key=oldKey;throw e;}localStorage.setItem('commons-name',name);localStorage.setItem('commons-key',key);$('invite').value='';message('Invitation connected.');await refresh();}catch(e){message(e.message);}};
$('create').onclick=async()=>{$('create').disabled=true;try{const d=await api('connect',{label:$('label').value.trim()||name+' · AI companion'});$('new-key').dataset.id=d.id;$('key-output').value=d.token;$('key-output').type='password';$('new-key').hidden=false;message('Connection '+d.label+' is ready. It controls '+d.who+'. Use this key in your AI app’s connection settings or the Commons sign-in form.');await refresh();}catch(e){message(e.message);}finally{$('create').disabled=!(name&&key);}};
async function copy(text){try{await navigator.clipboard.writeText(text);message('Copied.');}catch{message('Copy was blocked by the browser. Select the value and copy it manually.');}}
$('copy-key').onclick=()=>copy($('key-output').value);$('copy-url').onclick=()=>copy($('endpoint').textContent);
$('copy-guide').onclick=()=>copy($('ai-setup-guide').textContent.trim());
$('show-key').onclick=()=>{const show=$('key-output').type==='password';$('key-output').type=show?'text':'password';$('show-key').textContent=show?'Hide key':'Show key';};
refresh();
})();
