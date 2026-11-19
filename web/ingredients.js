/* SmartNanoFormulator v5 ingredient catalogue UI.
   Loaded as a same-origin classic script so the existing app router can use it. */
function ingredientDisplayValue(value){
  if(value===null||value===undefined)return 'Not reported';
  if(typeof value==='object')return JSON.stringify(value).slice(0,240);
  return String(value);
}
function ingredientSafeUrl(value){try{const url=new URL(String(value||''),location.origin);return ['http:','https:'].includes(url.protocol)?url.href:'';}catch{return '';}}
function ingredientLink(value,label='Open source ↗'){const url=ingredientSafeUrl(value);return url?`<a class="small-link" href="${esc(url)}" target="_blank" rel="noopener">${label}</a>`:'';}
function ingredientStatusLabel(status){return ({
  ingredient_found_no_formulation_evidence:'Ingredient found · no linked formulation evidence',
  formulation_evidence_found:'Ingredient and formulation evidence found',
  synonym_match:'Source-listed synonym match',
  spelling_suggestion:'Possible spelling correction · select to confirm',
  form_match:'Specific chemical form candidate',
  ambiguous_candidates:'Ambiguous candidates · choose explicitly',
  ambiguous_form_requires_selection:'Ambiguous chemical form · selection required',
  external_candidates_pending_review:'External source candidates · review required',
  product_candidates_found:'Product records found · active ingredient not linked',
  ingredient_not_found:'Ingredient not found',
  not_found:'No source record found',
  source_errors:'Source lookup incomplete · check provider status'
})[status]||String(status||'Search result')}
function ingredientSourceLine(source,sourceId,when,url,licence){
  const date=when?new Date(when).toLocaleDateString():`retrieval date not available in legacy record`;
  return `<div class="resolver-source-row"><b>${esc(source||'Source not reported')}</b>${sourceId?` · ID ${esc(sourceId)}`:''} · ${esc(date)}${url?` · ${ingredientLink(url)}`:''}${licence?`<br>${esc(licence)}`:''}</div>`;
}
function ingredientPropertyRows(candidate,selected=false){
  const properties=candidate.properties||[];
  if(candidate.suppress_molecular_weight_until_selected&&!selected){
    return `<div class="notice blue"><strong>No generic molecular weight assigned.</strong> Select an exact source record/form first; any property belongs to that record only.</div>`;
  }
  if(!properties.length)return `<div class="help">No property observations are stored for this record.</div>`;
  const priority=['mw','molecular_weight','xlogp_computed','logp','pka_experimental','solubility_experimental','stability_experimental'];
  const summary=[...properties].sort((a,b)=>{
    const ai=priority.indexOf(a.code),bi=priority.indexOf(b.code);
    return (ai<0?999:ai)-(bi<0?999:bi);
  }).filter(p=>priority.includes(p.code)).slice(0,5);
  const chips=summary.map(p=>`<span class="metric-chip"><b>${esc(String(p.code).replaceAll('_',' '))}:</b> ${esc(ingredientDisplayValue(p.value))}${p.unit?` ${esc(p.unit)}`:''}<br><span class="help">${esc(p.kind||'observation kind n/r')} · ${esc(p.source||'Source not reported')}${p.retrieved_at?` · ${esc(new Date(p.retrieved_at).toLocaleDateString())}`:' · legacy date n/r'} · ${esc(p.review_status||'review n/r')}</span></span>`).join('');
  const ledger=properties.map(p=>{
    const value=ingredientDisplayValue(p.value);
    const unit=p.unit?` ${esc(p.unit)}`:'';
    const kind=p.kind?` · ${esc(p.kind)}`:'';
    const review=p.review_status?` · ${esc(p.review_status)}`:'';
    const date=p.retrieved_at?new Date(p.retrieved_at).toLocaleDateString():'retrieval date not present in v4 source record';
    return `<div class="resolver-source-row"><b>${esc(String(p.code||'property').replaceAll('_',' '))}:</b> ${esc(value)}${unit}${kind}<br>${esc(p.source||'Source not reported')}${p.source_id?` · ${esc(p.source_id)}`:''} · ${esc(date)}${review}${p.source_url?` · ${ingredientLink(p.source_url,'Source ↗')}`:''}${p.source_text?`<details><summary>Original source text</summary><div class="details-body">${esc(p.source_text)}</div></details>`:''}</div>`;
  }).join('');
  return `${chips?`<div class="metric-strip">${chips}</div>`:''}<details class="details"><summary>Full property provenance (${fmt(properties.length)} observations)</summary><div class="resolver-source-list">${ledger}</div></details>`;
}
function renderAnalysisPropertySources(result){
  const labels={mw:'Molecular weight',logp:'LogP',pka:'pKa',solubility_mg_ml:'Solubility'};
  const rows=Object.entries(labels).map(([key,label])=>{
    const source=result.property_sources?.[key],used=result.values_used?.[key],provenance=result.provenance?.[key]||'Provenance unavailable';
    if(!source)return `<div class="resolver-source-row"><b>${label}:</b> ${esc(provenance)}${used!=null?` · value used ${esc(used)}`:''}</div>`;
    const date=source.retrieved_at?new Date(source.retrieved_at).toLocaleString():'retrieval date not recorded in legacy source';
    return `<div class="resolver-source-row"><b>${label}:</b> value used ${esc(used??'n/r')}${source.value!=null?` · source value ${esc(ingredientDisplayValue(source.value))}${source.unit?` ${esc(source.unit)}`:''}`:''}<br>${esc(source.source_system||'Source not reported')}${source.source_id?` · ID ${esc(source.source_id)}`:''}${source.source_record_id?` · source record ${esc(source.source_record_id)}`:''} · ${esc(source.observation_kind||'type n/r')} · ${esc(date)} · ${esc(source.status||'status n/r')}${source.record_url?` · ${ingredientLink(source.record_url,'Open source ↗')}`:''}${source.licence_note?`<br>${esc(source.licence_note)}`:''}${source.source_text?`<details><summary>Source text</summary><div class="details-body">${esc(source.source_text)}</div></details>`:''}</div>`;
  }).join('');
  return `<details class="details"><summary>Selected property provenance</summary><div class="resolver-source-list">${rows}</div></details>`;
}
function ingredientLocalCard(c,query,context='catalogue',selected=false){
  const chips=(c.aliases||[]).slice(0,8).map(a=>`<span class="badge neutral">${esc(a)}</span>`).join('');
  const ids=(c.identifiers||[]).slice(0,8).map(x=>`${esc(x.identifier_system)} ${esc(x.identifier_type)}: ${esc(x.identifier_value)}`).join(' · ');
  const evidence=c.has_formulation_evidence?`${fmt(c.formulation_evidence_count)} exact linked formulation batch(es); this is catalogue evidence, not an efficacy conclusion.`:'No formulation batches are linked to this exact ingredient/form record.';
  const forms=(c.forms||[]).length?`<div class="resolver-source-row"><b>Recorded forms:</b> ${(c.forms||[]).map(f=>`${esc(f.form_name)} (${esc(f.review_status)})`).join(' · ')}</div>`:'';
  const action=selected?'':`<div class="resolver-actions"><button class="btn teal" type="button" data-select-candidate="${esc(c.candidate_key)}" data-query="${esc(query)}" ${context==='analysis'?'data-apply-analysis="1"':''}>${context==='analysis'?'Select for this analysis':'Select this record'}</button></div>`;
  return `<div class="resolver-card"><div class="candidate-top"><div><h3>${esc(c.name||c.canonical_name||'Ingredient candidate')}</h3><div class="meta-line">${c.entity_type==='form'?`Form of ${esc(c.canonical_name)} · `:''}${esc(c.category||'')} · ${esc(c.material_type||'')} ${c.match_type?`· ${esc(c.match_type.replaceAll('_',' '))}`:''}</div></div>${badge(c.review_status||'review status unavailable',c.review_status==='verified'?'good':'warn')}</div>${c.matched_alias&&c.match_type==='synonym'?`<p><b>Matched source-listed name:</b> ${esc(c.matched_alias)} → ${esc(c.canonical_name)}</p>`:''}<p><b>Formulation evidence:</b> ${esc(evidence)}</p>${ids?`<p><b>Identifiers:</b> ${ids}</p>`:''}${chips?`<div class="resolver-tags">${chips}</div>`:''}${forms}${ingredientPropertyRows(c,selected)}${action}</div>`;
}
function ingredientExternalCard(c,query,selected=false){
  const suppressed=!!c.suppress_molecular_weight_until_selected&&!selected;
  const mw=suppressed?'Not assigned until an exact form is selected':(c.molecular_weight!=null?`${esc(c.molecular_weight)} ${esc(c.molecular_weight_unit||'g/mol')} · source-reported for this CID`:'Not reported by this source');
  const synonyms=(c.synonyms||[]).slice(0,10).join(' · ');
  const selectable=c.selection_required!==false;
  return `<div class="resolver-card"><div class="candidate-top"><div><h3>${esc(c.name||'External compound candidate')}</h3><div class="meta-line">${esc(c.source||'Source')} ${c.cid?`· CID ${esc(c.cid)}`:''}${c.rxcui?`· RxCUI ${esc(c.rxcui)}`:''}${c.tty?`· TTY ${esc(c.tty)}`:''} · ${esc(c.review_status||'pending review')}</div></div>${badge('Source candidate · not verified','warn')}</div>${c.molecular_formula?`<p><b>Source-listed formula:</b> ${esc(c.molecular_formula)}</p>`:''}${c.molecular_weight!==undefined?`<p><b>Molecular weight:</b> ${mw}</p>`:''}${c.specificity_notice?`<p>${esc(c.specificity_notice)}</p>`:''}${synonyms?`<p><b>Source-listed names for this record:</b> ${esc(synonyms)}</p>`:''}<p><b>Identity:</b> ${selectable?'Choose this exact provider record to attach it; the result remains pending human review.':'This concept is not an ingredient term and cannot be selected as an ingredient.'}</p>${c.source_url?`<p>${ingredientLink(c.source_url,`Open ${esc(c.source||'source')} record ↗`)}</p>`:''}${selectable&&!selected?`<div class="resolver-actions"><button class="btn teal" type="button" data-select-candidate="${esc(c.candidate_key)}" data-query="${esc(query)}">Select this exact source record</button></div>`:''}</div>`;
}
function ingredientProductCard(p,query,selected=false){
  const active=ingredientDisplayValue(p.active_ingredient_text);
  const generic=ingredientDisplayValue(p.generic_name);
  return `<div class="resolver-card"><div class="candidate-top"><div><h3>${esc(p.name||p.label_title||p.brand_name||'Product label record')}</h3><div class="meta-line">${esc(p.source||'Source')} · ${esc(p.brand_name||'Brand not reported')}${p.source_id?` · record ${esc(p.source_id)}`:''}</div></div>${badge('Product · not an ingredient','info')}</div><p><b>Active-ingredient text as supplied by source:</b> ${active==='Not reported'?'Not reported in this search record; open the full source label.':esc(active)}</p>${generic!=='Not reported'?`<p><b>Generic-name text:</b> ${esc(generic)}</p>`:''}${p.label_title?`<p><b>Label title:</b> ${esc(p.label_title)}</p>`:''}<p><b>Relationship:</b> Product and active ingredient remain separate. No active ingredient is auto-linked.</p>${p.attribution?`<p class="help">${esc(p.attribution)}${p.licence_note?` · ${esc(p.licence_note)}`:''}</p>`:''}${p.source_url?`<p>${ingredientLink(p.source_url,'Open source record ↗')}</p>`:''}${!selected?`<div class="resolver-actions"><button class="btn ghost" type="button" data-select-candidate="${esc(p.candidate_key)}" data-query="${esc(query)}">Review this product record (no ingredient link)</button></div>`:''}</div>`;
}
function ingredientSelectionNotice(selection,query,context='catalogue'){
  if(selection.selected){
    const c=selection.selected;
    if(context==='analysis')applyIngredientToAnalysis(c,query);
    return `<div class="notice blue"><strong>${esc(c.name)} selected.</strong> ${esc(selection.message||'Review the source properties before use.')}</div>`+ingredientLocalCard(c,query,context,true);
  }
  if(selection.selected_product){
    return `<div class="notice blue"><strong>Product record selected; active ingredient unchanged.</strong> ${esc(selection.message||'No active ingredient link was created.')}</div>`+ingredientProductCard(selection.selected_product,query,true);
  }
  if(selection.candidate){
    return `<div class="notice blue"><strong>RxNorm concept selected.</strong> ${esc(selection.message||'No molecular weight was assigned.')}</div>${ingredientExternalCard(selection.candidate,query,true)}`;
  }
  return `<div class="notice blue">${esc(selection.message||'Selection recorded.')}</div>`;
}
function renderIngredientResults(result,query,context='catalogue'){
  const local=result.ingredient_candidates||[];
  const external=result.external_ingredient_candidates||[];
  const products=result.product_candidates||[];
  const sourceRows=(result.source_statuses||[]).map(s=>`<div class="resolver-source-row"><b>${esc(s.source||'Source')}</b> · ${esc(s.status||'status unavailable')}${s.record_count!==undefined?` · ${fmt(s.record_count)} records`:''}${s.retrieved_at?` · ${esc(new Date(s.retrieved_at).toLocaleString())}`:''}${s.cache_hit?' · cached':''}${s.error?`<br><span class="muted">${esc(s.error)}</span>`:''}${s.request_url?` · ${ingredientLink(s.request_url,'Request ↗')}`:''}</div>`).join('');
  let html=`<div class="notice blue"><strong>${esc(ingredientStatusLabel(result.status))}.</strong> ${esc(result.message||'Select a source-backed record only after reviewing its provenance.')}</div>`;
  if(local.length)html+=`<div class="subsection">Local ingredient and form candidates</div>${local.map(c=>ingredientLocalCard(c,query,context)).join('')}`;
  if(external.length)html+=`<div class="subsection">External compound/concept candidates · not yet selected</div>${external.map(c=>ingredientExternalCard(c,query)).join('')}`;
  if(products.length)html+=`<div class="subsection">Separate product and label records</div>${products.map(p=>ingredientProductCard(p,query)).join('')}`;
  if(sourceRows)html+=`<details class="details"><summary>Source checks and retrieval status</summary><div class="resolver-source-list">${sourceRows}</div></details>`;
  if(!local.length&&!external.length&&!products.length)html+=`<div class="empty">No ingredient or product record is linked to this search. Use external search to check source providers; a not-found result is not an evidence-of-absence conclusion.</div>`;
  if(context==='analysis'&&result.status==='ambiguous_form_requires_selection')html+=`<div class="notice blue"><a href="#/ingredients">Open Ingredient catalogue to search source-specific forms and labels →</a></div>`;
  return html;
}
function parseAutoPka(value){
  if(value===null||value===undefined||value==='')return null;
  const n=Number(value);if(Number.isFinite(n))return n;
  const m=String(value).match(/p\s*k\s*a\s*(?:=|:)?\s*(-?\d+(?:\.\d+)?)/i);return m?Number(m[1]):null;
}
function parseAutoSolubility(value){
  if(value===null||value===undefined||value==='')return null;
  const n=Number(value);if(Number.isFinite(n))return n;
  const m=String(value).replace(/,/g,'').match(/(\d+(?:\.\d+)?)\s*(mg\s*\/\s*mL|mg\s*\/\s*L|g\s*\/\s*L|g\s*\/\s*mL)/i);if(!m)return null;
  const amount=Number(m[1]),unit=m[2].replace(/\s/g,'').toLowerCase();return unit==='mg/l'?amount/1000:unit==='g/ml'?amount*1000:amount;
}
function setDrugAutoField(form,name,value){
  const input=form.elements.namedItem(name);if(!input||value===null||value===undefined||value==='')return;
  if(input.value&&input.dataset.autoValue!==input.value)return;
  input.value=String(value);input.dataset.autoValue=input.value;
}
function clearDrugAutoFields(form){
  for(const field of ['mw','logp','pka','solubility_mg_ml','tpsa','hbd','hba','stability']){
    const control=form.elements.namedItem(field);if(control?.dataset.autoValue&&control.value===control.dataset.autoValue)control.value='';if(control)delete control.dataset.autoValue;
  }
}
function applyIngredientToAnalysis(candidate,query,matched={}){
  const form=$('#analysisForm');if(!form)return;
  const name=form.elements.namedItem('drug_name');
  const canonical=candidate.entity_type==='form'?candidate.name:(candidate.canonical_name||candidate.name||query);
  if(name){name.value=canonical;name.dataset.selectedIdentityValue=canonical;}
  const ingredientId=form.elements.namedItem('ingredient_id'),formId=form.elements.namedItem('form_id');
  if(ingredientId)ingredientId.value=candidate.ingredient_id||'';if(formId)formId.value=candidate.form_id||'';
  const legacy=matched.legacy_profile||candidate.legacy_profile||{};
  const props={};(candidate.properties||[]).forEach(p=>{props[p.code]=p});
  const get=(...keys)=>{for(const key of keys){const p=props[key];if(p&&p.value!==undefined&&p.value!==null)return p.value;}return undefined;};
  setDrugAutoField(form,'mw',legacy.mw??get('mw','molecular_weight'));
  setDrugAutoField(form,'logp',legacy.logp??get('xlogp_computed','logp'));
  setDrugAutoField(form,'pka',parseAutoPka(legacy.pka_text??get('pka_experimental')));
  setDrugAutoField(form,'solubility_mg_ml',parseAutoSolubility(legacy.solubility_text??get('solubility_experimental')));
  setDrugAutoField(form,'tpsa',legacy.tpsa??get('tpsa'));
  setDrugAutoField(form,'hbd',legacy.hbd??get('hbd'));
  setDrugAutoField(form,'hba',legacy.hba??get('hba'));
  setDrugAutoField(form,'stability',legacy.stability_text??get('stability_experimental'));
  const source=$('#analysisDrugSource');
  if(source){const brandWarning=matched.brand_warning||candidate.brand_warning;const alias=matched.matched_alias||candidate.matched_alias||query;const filled=['mw','logp','pka','solubility_mg_ml','tpsa','hbd','hba'].filter(k=>form.elements.namedItem(k)?.value).length;source.innerHTML=`<span class="badge good">Selected ${esc(canonical)}</span> <span class="help">Search matched “${esc(alias)}”. ${filled} properties filled where available. Review source provenance and confirm the exact chemical form.</span>${brandWarning?`<div class="notice blue">${esc(brandWarning)}</div>`:''}`;}
}
async function selectDrugSuggestion(button,query,panel){
  const key=button.dataset.drugKey;button.disabled=true;button.querySelector('strong').textContent='Selecting…';
  try{
    const selection=await api('/api/ingredients/select',{method:'POST',body:JSON.stringify({candidate_key:key,query})});
    if(!selection.selected)throw new Error('The suggested ingredient could not be selected.');
    const matched=JSON.parse(button.dataset.drugMeta||'{}');
    applyIngredientToAnalysis({...selection.selected,legacy_profile:matched.legacy_profile},query,matched);
    panel.innerHTML='<div class="notice blue">Ingredient selected. Available physicochemical details were filled into the form; please check the exact product and form before continuing.</div>';
  }catch(error){button.disabled=false;const label=button.querySelector('strong');if(label)label.textContent=button.dataset.originalLabel||'Select ingredient';toast(error.message);}
}
function renderDrugSuggestionList(result,query){
  const rows=result.results||[];
  if(!rows.length)return `<div class="empty">No local match for “${esc(query)}”. You can search public chemistry and label databases, or continue with a manually entered name.</div><button class="btn ghost" type="button" data-search-external-drug>Search public ingredient sources</button>`;
  return `<div class="drug-suggestion-list">${rows.map((c,i)=>`<button type="button" class="drug-suggestion" data-drug-choice data-drug-key="${esc(c.candidate_key||'')}" data-original-label="${esc(c.name||c.canonical_name||query)}" data-drug-meta='${esc(JSON.stringify(c))}'><span><strong>${esc(c.display_name||c.name||c.canonical_name||query)}</strong><small>${esc(c.category||'Ingredient')} · ${esc(c.match_type||'catalogue match')}${c.legacy_profile?.mw?` · MW ${esc(c.legacy_profile.mw)} g/mol`:''}</small></span><span class="match-tag">${c.is_brand_alias?'Brand alias':'Select'}</span></button>`).join('')}</div><div class="drug-suggestion-foot">Choose the correct active ingredient. Brand families can include different strengths and combination products.</div>`;
}
function wireDrugLookup(){
  const input=$('#drug_name'),panel=$('#analysisDrugResolve');if(!input||!panel)return;
  const form=$('#analysisForm');let timer=null,seq=0,lastQuery='';
  const search=async()=>{
    const query=input.value.trim(),current=++seq;lastQuery=query;
    if(query.length<2){panel.innerHTML='';return;}
    panel.innerHTML='<div class="help">Searching medicines, supplements and synonyms…</div>';
    try{const result=await api('/api/drugs/search?q='+encodeURIComponent(query));if(current!==seq)return;panel.innerHTML=renderDrugSuggestionList(result,query);}
    catch(error){if(current===seq)panel.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}
  };
  input.addEventListener('input',()=>{
    const selected=form?.elements.namedItem('ingredient_id');
    if(selected?.value&&input.value!==input.dataset.selectedIdentityValue){selected.value='';const formId=form.elements.namedItem('form_id');if(formId)formId.value='';clearDrugAutoFields(form);delete input.dataset.selectedIdentityValue;const source=$('#analysisDrugSource');if(source)source.textContent='';}
    clearTimeout(timer);timer=setTimeout(search,180);
  });
  input.addEventListener('change',search);
  panel.addEventListener('click',async event=>{
    const choice=event.target.closest('[data-drug-choice]');
    if(choice){selectDrugSuggestion(choice,lastQuery||input.value.trim(),panel);return;}
    const external=event.target.closest('[data-search-external-drug]');
    if(external){external.disabled=true;external.textContent='Searching public sources…';try{const data=await api('/api/ingredients/suggest?q='+encodeURIComponent(input.value.trim())+'&external=1');panel.innerHTML=renderIngredientResults(data,input.value.trim(),'analysis');wireIngredientResultActions(panel,input.value.trim(),'analysis');}catch(error){panel.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}}
  });
}
async function ingredientPage(){
  setNav('ingredients');
  const catalogue=state.meta?.ingredient_catalogue||{};
  const categoryRows=Object.entries(catalogue.categories||{}).sort(([a],[b])=>a.localeCompare(b)).map(([name,item])=>[esc(name.replace(/_/g,' ')),fmt(item.total),fmt(item.verified),`${(Number(item.verified_share||0)*100).toFixed(1)}%`]);
  const categoryTable=categoryRows.length?table(['Category','Ingredient records','Human verified','Verified share'],categoryRows):'<div class="empty">Category statistics are not available.</div>';
  $('#view').innerHTML=title('Ingredient catalogue','Source-backed ingredient search','Resolve ingredient names locally first, then search public chemistry and label sources on demand. Every imported value stays attributed and pending review until a human verifies it.')+
  `<div class="grid two"><section class="panel"><div class="panel-title">Find a chemical, form, synonym or product</div><p class="panel-sub">Local exact names and spelling suggestions are separate from external results. Brand products remain distinct from active ingredients.</p><div class="resolver-toolbar"><input id="ingredientSearch" class="filter-input" placeholder="e.g. acetominophen, creatine, magnesium glycinate, Tylenol" autocomplete="off"><button id="ingredientLocalSearch" class="btn ghost" type="button">Search local library</button><button id="ingredientExternalSearch" class="btn teal" type="button">Search source providers</button></div><div id="ingredientSearchResults" class="resolver-results"><div class="empty">Start with a local search. External API requests run only when you choose them.</div></div><div class="resolver-actions"><button id="ingredientLiteratureSearch" class="btn ghost" type="button">Search PubMed + Europe PMC</button></div><div id="ingredientLiteratureResults" class="resolver-results"></div></section><aside><div class="side-note"><h3>Identity and evidence guardrails</h3><p>A spelling suggestion is never an automatic match. Salt, hydrate, chelate, mixture and product records are kept separate. No formulation evidence is claimed unless a batch is linked to that exact identity.</p></div><div class="side-note"><h3>Source quality</h3><p>PubChem/RxNorm identities are not clinical evidence. DailyMed, openFDA and NIH DSLD are label sources. PubMed and Europe PMC search results are literature leads, not proof of efficacy or formulation performance.</p></div><div class="notice blue"><strong>Review status.</strong> This prototype includes source-retrieved records, but the catalogue verification share remains explicit. Do not treat pending source data as human-verified.</div></aside></div>`+
  `<div class="section-head"><div><h2>Local catalogue snapshot</h2><p>Versioned seed plus additive records; source freshness is displayed per property.</p></div><span class="badge neutral">${fmt(catalogue.total??state.drugs.length)} ingredient records</span></div>
  <div class="grid four">${statCard('HUMAN VERIFIED',`${fmt(catalogue.verified||0)} / ${fmt(catalogue.total||0)}`,`${(Number(catalogue.verified_share||0)*100).toFixed(1)}% of ingredient records`,'✓')}${statCard('REVIEW QUEUE',fmt(catalogue.review_queue_items||0),'Unresolved names and candidates','⌕')}${statCard('DUPLICATE FLAGS',fmt(catalogue.pending_duplicate_flags||0),'Pending human review','⚑')}${statCard('PRODUCT RECORDS',fmt(catalogue.products||0),`${fmt(catalogue.product_ingredient_links||0)} explicit ingredient links; review separately`,'▤')}</div>
  <div class="notice blue"><strong>Catalogue scope.</strong> Ingredient catalogue imports add zero formulation batches. The existing batch count is ${fmt(catalogue.formulation_batches||0)}; source-retrieved and legacy-migrated identities remain unverified until reviewed.</div>
  <div class="panel"><div class="panel-title">Records by category and verification status</div>${categoryTable}</div>
  <div class="section-head"><div><h2>Browse ingredient records</h2><p>Click a record to prefill a local search.</p></div></div><div id="ingredientBrowse" class="grid three"></div>`+footer();
  $('#view').insertAdjacentHTML('afterbegin',`<div class="notice blue" role="status"><strong>Render Free data limit.</strong> The included Free setup rebuilds its database from the bundled seed at each restart; saved analyses and imports can be lost. Exported history is an archive, not an automatic restore. <a class="small-link" href="/api/export/history">Download history JSON</a> and keep it outside Render.</div>`);
  const input=$('#ingredientSearch'),results=$('#ingredientSearchResults'),literature=$('#ingredientLiteratureResults');
  const localSearch=async()=>{
    const query=input.value.trim();if(!query){results.innerHTML='<div class="notice blue">Enter a name to search the local catalogue.</div>';return;}
    results.innerHTML=loading();try{const data=await api('/api/ingredients/suggest?q='+encodeURIComponent(query));results.innerHTML=renderIngredientResults(data,query,'catalogue');wireIngredientResultActions(results,query,'catalogue');}catch(error){results.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}
  };
  const externalSearch=async()=>{
    const query=input.value.trim();if(!query){results.innerHTML='<div class="notice blue">Enter an ingredient, form or product first.</div>';return;}
    results.innerHTML=loading();try{const data=await api('/api/ingredients/suggest?q='+encodeURIComponent(query)+'&external=1');results.innerHTML=renderIngredientResults(data,query,'catalogue');wireIngredientResultActions(results,query,'catalogue');}catch(error){results.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}
  };
  $('#ingredientLocalSearch').addEventListener('click',localSearch);
  $('#ingredientExternalSearch').addEventListener('click',externalSearch);
  input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();localSearch();}});
  let timer=null;input.addEventListener('input',()=>{clearTimeout(timer);if(input.value.trim().length>=3)timer=setTimeout(localSearch,80);});
  $('#ingredientLiteratureSearch').addEventListener('click',async()=>{
    const query=input.value.trim();if(!query){literature.innerHTML='<div class="notice blue">Enter a name to search literature.</div>';return;}
    literature.innerHTML=loading();try{const data=await api('/api/ingredients/literature?q='+encodeURIComponent(query));
      literature.innerHTML=renderIngredientLiterature(data,query);
    }catch(error){literature.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}
  });
  const browse=$('#ingredientBrowse');
  try{const items=await api('/api/ingredients/library?limit=18');browse.innerHTML=items.map(i=>`<button class="organ-card" type="button" data-catalogue-name="${esc(i.preferred_name)}"><strong>${esc(i.preferred_name)}</strong><span>${esc(i.category)} · ${esc(i.material_type)} · ${esc(i.review_status)}</span></button>`).join('');
    browse.addEventListener('click',event=>{const item=event.target.closest('[data-catalogue-name]');if(!item)return;input.value=item.dataset.catalogueName;localSearch();});
  }catch(error){browse.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;}
}
function renderIngredientLiterature(data,query){
  const pubs=data.pubmed||[],epmc=data.europepmc||[];
  const card=item=>`<div class="resolver-card"><h3>${esc(item.title||'Title not available')}</h3><div class="meta-line">${esc(item.source||'Literature source')} · ${esc(item.authors||'Authors not reported')} · ${esc(item.journal||'Journal not reported')} ${item.year?`· ${esc(item.year)}`:''}${item.pmid?`· PMID ${esc(item.pmid)}`:''}${item.doi?`· DOI ${esc(item.doi)}`:''}</div>${item.abstract?`<details><summary>Abstract excerpt (source rights apply)</summary><p>${esc(String(item.abstract).slice(0,1200))}</p></details>`:''}${item.source_url?`<p>${ingredientLink(item.source_url,'Open source record ↗')}</p>`:''}<p class="help">${esc(item.licence_note||'Bibliographic metadata only; verify study type and full text at the source.')}</p></div>`;
  const statuses=(data.source_statuses||[]).map(s=>`<div class="resolver-source-row"><b>${esc(s.source)}</b> · ${esc(s.status)}${s.error?` · ${esc(s.error)}`:''}</div>`).join('');
  return `<div class="notice blue"><strong>Literature leads for “${esc(query)}”.</strong> Search results are not study-quality or efficacy assessments. Open and review the primary record.</div><div class="subsection">PubMed (${fmt(pubs.length)})</div>${pubs.length?pubs.map(card).join(''):'<div class="empty">No PubMed metadata returned.</div>'}<div class="subsection">Europe PMC (${fmt(epmc.length)})</div>${epmc.length?epmc.map(card).join(''):'<div class="empty">No Europe PMC record returned.</div>'}<details class="details"><summary>Literature provider status</summary><div class="resolver-source-list">${statuses}</div></details>`;
}
