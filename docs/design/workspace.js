const originals = [
  {name:'Firefox',version:'136.0',category:'Browsers',type:'Application',directory:'apps/browsers',description:'A fast, independent web browser.',testing:true,production:true,min:'10.15'},
  {name:'Chromium',version:'134.0',category:'Browsers',type:'Application',directory:'apps/browsers',description:'An open source browser project.',testing:true,production:false,min:'11.0'},
  {name:'Blender',version:'4.3.2',category:'Creative',type:'Application',directory:'apps/creative',description:'Tools for 3D creation and animation.',testing:true,production:false,min:'11.0'},
  {name:'Inkscape',version:'1.4',category:'Creative',type:'Application',directory:'apps/creative',description:'A vector graphics editor.',testing:true,production:true,min:'11.0'},
  {name:'IINA',version:'1.4.0',category:'Media',type:'Application',directory:'apps/media',description:'The modern video player for macOS.',testing:true,production:false,min:'10.15'},
  {name:'VLC',version:'3.0.21',category:'Media',type:'Application',directory:'apps/media',description:'An open source media player.',testing:true,production:true,min:'10.13'},
  {name:'Git',version:'2.48.1',category:'Utilities',type:'Package',directory:'tools',description:'Distributed version control tools.',testing:true,production:true,min:'11.0'},
  {name:'Keka',version:'1.4.8',category:'Utilities',type:'Application',directory:'apps/utilities',description:'A file archiver for macOS.',testing:true,production:false,min:'10.15'}
];
let records=structuredClone(originals), selected=0, grouping='category';
const el=id=>document.getElementById(id);
const path=r=>'pkgsinfo/'+r.directory+'/'+r.name.toLowerCase()+'-'+r.version+'.plist';
const changed=(a,b)=>['displayName','description','testing','production'].some(k=>(a[k]??a.name)!==(b[k]??b.name));
function dirty(){return records.filter((r,i)=>changed(r,originals[i])).length}
function updateStatus(){const n=dirty();el('draft-count').textContent=n?n+' draft '+(n===1?'change':'changes'):'No draft changes';el('change-count').textContent=n;el('status').textContent=n?'Draft updated in this browser. No files have been written.':'Sample workspace ready. No repository connected.'}
function choose(i){selected=i;renderList();renderEditor()}
function icon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('aria-hidden','true');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');
  use.setAttribute('href','#icon-'+name); svg.append(use); return svg;
}
function renderList() {
  const query=el('search').value.toLowerCase();
  el('package-list').replaceChildren(); el('package-rows').replaceChildren();
  const groups=new Map();
  records.forEach((r,i)=>{
    const row=document.createElement('tr'); row.className=i===selected?'selected':'';
    const cell=document.createElement('td'), button=document.createElement('button');
    button.textContent=r.name; button.addEventListener('click',()=>choose(i)); cell.append(button); row.append(cell);
    [r.version,r.category].forEach(v=>{const td=document.createElement('td');td.textContent=v;row.append(td)});
    el('package-rows').append(row);
    if(![r.name,r.version,r.category,r.directory].join(' ').toLowerCase().includes(query))return;
    const group=r[grouping]; if(!groups.has(group))groups.set(group,[]); groups.get(group).push({r,i});
  });
  groups.forEach((items,name)=>{
    const heading=document.createElement('div'); heading.className='group';
    heading.append(icon(grouping==='directory'?'folder':'grid'),document.createTextNode(name));
    const count=document.createElement('small');count.textContent=items.length;heading.append(count);el('package-list').append(heading);
    items.forEach(({r,i})=>{
      const button=document.createElement('button');button.className='package';button.setAttribute('aria-pressed',String(i===selected));
      button.append(icon('package'),document.createTextNode(r.name));
      const version=document.createElement('span');version.textContent=r.version;button.append(version);
      button.addEventListener('click',()=>choose(i));el('package-list').append(button);
    });
  });
  if(!groups.size){const p=document.createElement('p');p.className='empty-search';p.textContent='No matching packages';el('package-list').append(p)}
}
function renderEditor(){const r=records[selected];el('package-name').textContent=r.name;el('record-name').value=r.name;el('installer-type').value=r.type==='Package'?'pkg':'copy_from_dmg';el('installer-location').value=r.directory+'/'+r.name+'-'+r.version+(r.type==='Package'?'.pkg':'.dmg');el('package-path').textContent=path(r);el('display-name').value=r.displayName??r.name;el('description').value=r.description;el('version').value=r.version;el('category').value=r.category;el('minimum-os').value=r.min;el('testing').checked=r.testing;el('production').checked=r.production;renderSamplePanels(r)}
function capture(){const r=records[selected];r.displayName=el('display-name').value;r.description=el('description').value;r.testing=el('testing').checked;r.production=el('production').checked;updateStatus()}
['display-name','description','testing','production'].forEach(id=>el(id).addEventListener('input',capture));
el('search').addEventListener('input',renderList);
el('layout').addEventListener('change',()=>document.body.classList.toggle('table-mode',el('layout').value==='table'));
function navigate(area){el('workspace-title').textContent=area==='packages'?'Packages':'Manifests';el('workspace-subtitle').textContent=area==='packages'?'8 pkginfo files':'1 sample manifest';el('packages-view').hidden=area!=='packages';el('manifests-view').hidden=area!=='manifests';['packages','manifests'].forEach(n=>{if(n===area)el(n+'-nav').setAttribute('aria-current','page');else el(n+'-nav').removeAttribute('aria-current')})}
el('packages-nav').addEventListener('click',()=>navigate('packages'));el('manifests-nav').addEventListener('click',()=>navigate('manifests'));
function review(){const lines=[];records.forEach((r,i)=>{if(!changed(r,originals[i]))return;lines.push(path(r));for(const k of ['displayName','description','testing','production']){const before=originals[i][k]??originals[i].name,after=r[k]??r.name;if(before!==after){lines.push('- '+k+': '+JSON.stringify(before));lines.push('+ '+k+': '+JSON.stringify(after))}}lines.push('')});el('diff').textContent=lines.length?lines.join('\n'):'No changes yet. Edit a package to explore the review flow.';el('review-summary').textContent=dirty()?dirty()+' modified sample '+(dirty()===1?'record':'records'):'Sample workspace · no files changed';el('discard').disabled=!dirty();el('review-dialog').showModal()}
document.querySelectorAll('.review').forEach(b=>b.addEventListener('click',review));['close-review','continue'].forEach(id=>el(id).addEventListener('click',()=>el('review-dialog').close()));el('discard').addEventListener('click',()=>{records=structuredClone(originals);renderList();renderEditor();updateStatus();el('review-dialog').close()});
document.querySelectorAll('[data-group]').forEach(button=>button.addEventListener('click',()=>{
  grouping=button.dataset.group;
  document.querySelectorAll('[data-group]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  renderList();
}));
const catalogSection=document.querySelector('#testing').closest('.editor-section');
const descriptionSection=document.querySelector('#description').closest('.editor-section');
descriptionSection.before(catalogSection);
document.querySelectorAll('.editor-section').forEach(section=>{
  const heading=section.querySelector('h3');
  const card=document.createElement('div');card.className='card-fields';
  [...section.childNodes].filter(node=>node!==heading).forEach(node=>card.append(node));
  section.append(card);
});
renderList();renderEditor();updateStatus();

function renderSamplePanels(record) {
  const application=record.type!=='Package';
  const section=(panel,title,rows)=>{
    const section=document.createElement('section');section.className='editor-section';
    const heading=document.createElement('h3');heading.textContent=title;
    const card=document.createElement('div');card.className='card-fields';
    rows.forEach(([label,value])=>{
      const dl=document.createElement('dl');dl.className='sample-value';
      const dt=document.createElement('dt');dt.textContent=label;
      const dd=document.createElement('dd');dd.textContent=value;dl.append(dt,dd);card.append(dl);
    });
    section.append(heading,card);panel.append(section);
  };
  for(const name of ['detection','install','uninstall','advanced']){
    const panel=el('panel-'+name);panel.replaceChildren();
    const note=document.createElement('p');note.className='sample-note';
    note.textContent='Read-only sample metadata for '+record.name+'. No commands or scripts are executed.';panel.append(note);
  }
  section(el('panel-detection'),'Install detection',application?[
    ['Method','Application bundle'],['Path','/Applications/'+record.name+'.app'],['Version',record.version]
  ]:[['Method','Package receipt'],['Package ID','org.example.'+record.name.toLowerCase()],['Version',record.version]]);
  section(el('panel-detection'),'Check scripts',[['Install check','Not configured'],['Version script','Not configured']]);
  section(el('panel-install'),'Installer',[
    ['Type',application?'copy_from_dmg':'pkg'],['Location',record.directory+'/'+record.name+'-'+record.version+(application?'.dmg':'.pkg')],['Destination',application?'/Applications':'Defined by installer']
  ]);
  section(el('panel-install'),'Installation behavior',[['Unattended install','Not specified'],['Blocking application',application?record.name+'.app':'None specified'],['Preinstall script','Not configured'],['Postinstall script','Not configured']]);
  section(el('panel-uninstall'),'Removal',[
    ['Uninstallable',application?'Yes':'Not specified'],['Method',application?'remove_copied_items':'Not configured'],['Target',application?'/Applications/'+record.name+'.app':'None specified']
  ]);
  section(el('panel-uninstall'),'Removal scripts',[['Preuninstall script','Not configured'],['Postuninstall script','Not configured']]);
  section(el('panel-advanced'),'Compatibility',[['Minimum macOS',record.min],['Architectures','arm64, x86_64'],['Restart action','Not specified'],['On demand','Not specified']]);
  section(el('panel-advanced'),'File',[['Relative path',path(record)],['Format','XML property list'],['Source','Invented sample record']]);
}
const editorTabs=[...document.querySelectorAll('[role="tab"]')];
function activateTab(tab,focus=false){
  editorTabs.forEach(button=>{
    const selected=button===tab;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
    el(button.getAttribute('aria-controls')).hidden=!selected;
  });
  if(focus)tab.focus();
}
editorTabs.forEach((tab,index)=>{
  tab.addEventListener('click',()=>activateTab(tab));
  tab.addEventListener('keydown',event=>{
    let next;
    if(event.key==='ArrowRight')next=(index+1)%editorTabs.length;
    if(event.key==='ArrowLeft')next=(index-1+editorTabs.length)%editorTabs.length;
    if(event.key==='Home')next=0;
    if(event.key==='End')next=editorTabs.length-1;
    if(next!==undefined){event.preventDefault();activateTab(editorTabs[next],true)}
  });
});
