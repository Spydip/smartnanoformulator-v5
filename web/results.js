/* Summary-first SmartNanoFormulator results. Detailed records are fetched only on expansion. */
function resultStatusText(r){
  const direct=Number(r.direct_batch_count||0), related=Number(r.related_batch_count||0);
  if(direct) return 'Direct ingredient-linked batches are available in the supplied catalogue; this is evidence retrieval, not a carrier recommendation.';
  if(related) return 'No direct ingredient-linked batch was found; related same-class evidence is available and remains indirect.';
  if(r.design_hypotheses_available) return 'No direct drug-and-target match was retrieved. Exploratory formulation candidates are available instead of stopping at an empty result.';
  return 'No direct or same-class formulation batches were found for this request in the supplied catalogue.';
}
function resultCarrierRow(c){
  const direct=Number(c.direct_batches||0), related=Number(c.analogue_batches||0), context=Number(c.other_context_batches||0);
  const state=direct?'Direct batch evidence':related?'Related same-class evidence':'No direct or related batch evidence';
  const color=direct?'good':related?'warn':'neutral';
  return `<div class="summary-carrier-row"><div class="summary-carrier-name"><strong>${esc(c.carrier)}</strong>${badge(state,color)}</div><div class="summary-carrier-counts"><span>Direct ${fmt(direct)}</span><span>Related ${fmt(related)}</span><span>Context ${fmt(context)}</span></div></div>`;
}
function renderResultSummary(r){
  const organ=r.organ_context||{};
  const organExamples=(Number(organ.direct_examples||0)+Number(organ.same_class_examples||0)+Number(organ.other_ingredient_context_examples||0));
  const catalogueRows=(r.carrier_results||[]).reduce((n,c)=>n+Number(c.catalogue_context_batches||0),0);
  const updated=r.catalogue_updated_at?new Date(r.catalogue_updated_at).toLocaleDateString():'date not recorded';
  const status=resultStatusText(r);
  const llm=(state.meta||{}).llm||{};
  const aiReady=Boolean(llm.configured);
  const aiStatus=aiReady?(llm.is_local?`Free local AI · ${llm.model} · up to ${llm.daily_request_limit} requests/day`:`Optional API synthesis · ${llm.model} · daily cap ${llm.daily_request_limit} · monthly cap $${Number(llm.monthly_spend_cap_usd||0).toFixed(2)}`):`AI model ${llm.status||'not configured'} · install Ollama and run setup_free_ai.bat for free local AI`;
  return `${title('Analysis results',`${esc(r.input?.drug_name||'Selected ingredient')} · ${esc(r.input?.target_organ||'Target not specified')}`,`Analysis #${r.analysis_id} · ${esc(r.input?.route||'Route not specified')} · ${esc(r.input?.target_tissue||'Tissue not specified')}`)}
  <section class="result-summary" aria-labelledby="resultSummaryHeading">
    <div class="result-summary-head"><div><div class="page-kicker">Evidence summary · no carrier ranking</div><h2 id="resultSummaryHeading">${esc(status)}</h2><p>${esc(r.decision||'No decision summary is available.')}</p></div><a class="btn teal" href="/api/analysis/${encodeURIComponent(r.analysis_id)}/pdf">Download traceable PDF</a></div>
    <div class="result-counts" aria-label="Evidence counts">
      <div class="result-count direct"><span>Direct batch records</span><strong>${fmt(r.direct_batch_count)}</strong><small>Exact selected ingredient/form links</small></div>
      <div class="result-count related"><span>Related batch records</span><strong>${fmt(r.related_batch_count)}</strong><small>Same-class only; not a direct match</small></div>
      <div class="result-count context"><span>Other-ingredient context</span><strong>${fmt(r.other_context_batch_count)}</strong><small>Not drug-specific evidence</small></div>
    </div>
    <div class="result-carriers-head"><h3>Carrier evidence status</h3><span>Fixed catalogue order · not a performance ranking</span></div>
    <div class="summary-carrier-list">${(r.carrier_results||[]).map(resultCarrierRow).join('')||'<p class="help">No carriers were included in this analysis.</p>'}</div>
    <p class="result-scope">Catalogue scope: ${fmt(catalogueRows)} carrier–organ catalogue rows across selected systems · last catalogue update ${esc(updated)}. Separate organ-workbook examples: ${fmt(organ.direct_examples)} direct, ${fmt(organ.same_class_examples)} same-class, ${fmt(organ.other_ingredient_context_examples)} other-ingredient context${organExamples?'':' (none retrieved)'}. Organ examples are not batch-level formulation evidence.</p>
    ${r.design_hypotheses_available?`<div class="notice blue"><strong>Exploratory design is ready.</strong> ${fmt(r.design_candidate_count)} exploratory candidate concepts were generated because direct drug-and-target evidence was limited. They are hypotheses for testing, not published or validated formulations.</div>`:''}
    <div class="result-next-steps"><strong>Next steps</strong>${r.design_hypotheses_available?`<a href="#analysisDetails" data-open-result-section="design">Review AI formulation candidates</a>`:''}<a href="#analysisDetails" data-open-result-section="carriers">Review literature batch records</a><a href="#/methods">Read methods and limitations</a><a href="#/new">Start another analysis</a></div>
    <div class="result-actions"><button class="btn ghost" id="explainResultsButton" type="button" ${aiReady?'':'disabled'} aria-describedby="explainResultsStatus">${aiReady?'Explain these results':'Explain these results (AI unavailable)'}</button><span id="explainResultsStatus" class="help" role="status">${esc(aiStatus)}. Model synthesis runs only after this button is activated.${llm.is_local?' The local model runs on this computer with no API usage fee.':' External provider usage may incur charges.'}</span><a class="btn ghost" href="#/history">Saved history</a></div><div id="resultExplanation" aria-live="polite"></div>
  </section>
  <section class="result-detail-stack" id="analysisDetails" aria-label="Detailed analysis sections">
    ${r.design_hypotheses_available?resultDetailPanel('design','Exploratory formulation candidates and comparison'):''}
    ${resultDetailPanel('profile','Ingredient profile and property provenance')}
    ${resultDetailPanel('carriers','Batch records, compositions and study types')}
    ${resultDetailPanel('organ','Separate biological and organ-targeting context')}
    ${resultDetailPanel('references',`References (${fmt(r.references_count||0)})`)}
    ${resultDetailPanel('endpoints','Experimental endpoints, limitations and interpretation')}
  </section>${footer()}`;
}
function resultDetailPanel(section,label){
  return `<details class="result-detail" data-analysis-section="${esc(section)}"><summary>${esc(label)}</summary><div class="result-detail-content" data-detail-content><div class="help">Open to load this section from the saved analysis.</div></div></details>`;
}
function renderResultProfile(data){
  const labels={mw:['Molecular weight','g/mol'],logp:['LogP',''],pka:['pKa',''],solubility_mg_ml:['Aqueous solubility','mg/mL'],tpsa:['Topological polar surface area','Å²'],hbd:['Hydrogen bond donors',''],hba:['Hydrogen bond acceptors','']};
  const values=data.values_used||{}, sources=data.property_sources||{}, provenance=data.provenance||{};
  const rows=Object.entries(labels).map(([key,[label,unit]])=>{
    const src=sources[key]||{};const value=values[key];
    const shown=value===null||value===undefined?'Not reported':`${esc(value)}${unit?` ${unit}`:''}`;
    const origin=String(provenance[key]||'Not reported');const manual=origin.toLowerCase().startsWith('user-provided');
    const type=manual?'manual input':(src.observation_kind||((value===null||value===undefined)?'missing':'retrieved'));
    const source=manual?'User-provided':src.source_system?`${esc(src.source_system)}${src.source_id?` · ${esc(src.source_id)}`:''}`:'No exact-form source observation';
    const sourceValue=src.value===null||src.value===undefined?'None retrieved':`${esc(src.value)}${src.unit?` ${esc(src.unit)}`:''}`;
    const sourceRecord=src.source_record_id?`${esc(src.source_system||'Source')} · ${esc(src.source_id||'ID n/r')} · record ${esc(src.source_record_id)}`:manual?'No source record for the manual value':'Not available';
    const date=src.retrieved_at?new Date(src.retrieved_at).toLocaleDateString():'date not recorded';
    return [esc(label),shown,esc(type),source,sourceValue,sourceRecord,esc(date),esc(src.status||origin)];
  });
  const identity=data.drug_profile||{};
  return `<div class="panel"><h3 class="panel-title">Selected catalogue identity</h3><p class="help">${esc(identity.name||data.input?.drug_name||'Not reported')} · ingredient ID ${esc(identity.ingredient_id??data.input?.ingredient_id??'Not selected')}${identity.form_id||data.input?.form_id?` · form ID ${esc(identity.form_id||data.input.form_id)}`:''} · ${esc(data.drug_class||'class not available')}</p>${table(['Property','Value used / unit','Type','Source / entry','Source observation','Source record','Retrieved','Status / provenance'],rows)}<p class="help">A property is used only for the selected identity/form. User-entered values remain distinct from source observations; no parent molecular weight is inherited for an explicit form.</p></div>`;
}
function resultStudyType(c){return c.Evidence_Level||c.evidence_level||'Pending review / not reported';}
function renderResultCarriers(data){
  const carriers=data.carrier_results||[];
  return carriers.map(c=>{
    const candidates=c.candidates||[];
    const batchRows=candidates.map(x=>{
      const match=x.match_class==='direct'?'Direct ingredient-linked':x.match_class==='related_same_class'?'Related same-class':'Other context';
      return `<article class="candidate"><div class="candidate-top"><div><strong>${esc(x.Formulation_ID||x.formulation_id||'Batch ID not reported')} · ${esc(x.Drug||x.drug||'Drug not reported')}</strong><div class="meta-line">${esc(x.Paper_Title||x.paper_title||'Paper title not reported')} · ${esc(x.Year||x.year||'Year not reported')} · ${esc(x.Route||x.route||'Route not reported')}</div></div>${badge(match,match==='Direct ingredient-linked'?'good':match==='Related same-class'?'warn':'info')}</div><p><b>Study type:</b> ${esc(resultStudyType(x))}<br><b>Reported composition:</b> ${esc(textOr(x.composition||x.Stated_Ratio_Original))}<br><b><abbr title="Entrapment efficiency is the fraction of drug associated with the carrier; drug loading is the drug amount relative to the formulation basis.">Entrapment efficiency / drug loading</abbr>:</b> ${esc(textOr(x.Drug_Loading_EE_DL))}<br><b>Particle size / <abbr title="Polydispersity index: a measure of particle-size distribution breadth.">PDI</abbr> / <abbr title="Zeta potential is the electrokinetic potential near the particle surface; measurement medium and method matter.">zeta potential</abbr>:</b> ${esc(textOr(x.Size_PDI_Zeta))}<br><b>Source location:</b> ${esc(textOr(x.Evidence_Location||x.evidence_location))}<br><b>DOI / source:</b> ${esc(textOr(x.DOI||x.doi))}</p><p class="help"><b>Why matched:</b> ${esc(x.why_this_matched||'No match explanation supplied')} · Catalogue updated ${esc(c.catalogue_updated_at||'date not recorded')}</p></article>`;
    }).join('')||'<div class="empty">No direct or same-class batch records were retrieved for this carrier.</div>';
    const excipients=(c.excipients||[]).map(x=>`<span class="metric-chip">${esc(x.ingredient)} · ${esc(x.function||'role not reported')} · ${fmt(x.batches)} batches</span>`).join('')||'<p class="help">No composition observations were available for these matched batches.</p>';
    const ratios=(c.ratios||[]).length?table(['Ingredient','Reported unit','Minimum','Median','Maximum','Papers','Interpretation'],c.ratios.map(x=>[esc(x.ingredient),esc(x.unit||'Not reported'),esc(x.min??'Not reported'),esc(x.median??'Not reported'),esc(x.max??'Not reported'),fmt(x.papers),esc(x.note||'Descriptive only; not an optimized range.')])):'<p class="help">No numeric composition ranges were calculated from compatible source units.</p>';
    return `<section class="carrier-result"><div class="carrier-result-head"><div><h3>${esc(c.carrier)}</h3><div class="micro">${esc(c.evidence_basis||'No evidence basis reported')} · ${esc(c.status||'Status not reported')}</div></div>${badge('Evidence only · no ranking','neutral')}</div><div class="result-counts compact"><div class="result-count direct"><span>Direct</span><strong>${fmt(c.direct_batches)}</strong><small>${fmt(c.direct_papers)} papers</small></div><div class="result-count related"><span>Related same-class</span><strong>${fmt(c.analogue_batches)}</strong><small>${fmt(c.analogue_papers)} papers</small></div><div class="result-count context"><span>Other-ingredient context</span><strong>${fmt(c.other_context_batches)}</strong><small>${fmt(c.other_context_papers)} papers</small></div></div><h4 class="subsection">Retrieved batch records</h4>${batchRows}<h4 class="subsection">Ingredients observed (not compatibility recommendations)</h4><div class="metric-strip">${excipients}</div><h4 class="subsection">Descriptive reported quantities (not optimized)</h4>${ratios}</section>`;
  }).join('')||'<div class="empty">No carrier evidence rows are available.</div>';
}
function renderResultOrgan(data){return renderOrganContext(data.organ_context||{});}
function renderResultReferences(data){
  const refs=data.references||[];
  return refs.length?`<div class="reference-list">${refs.map(x=>`<div class="reference-item"><strong>${esc(x.title||'Title not reported')} (${esc(x.year||'Year not reported')})</strong><p>${esc(x.authors||'Authors not reported')} · ${esc(x.evidence_level||'Study type pending review')}</p><div class="meta-line">${esc(x.doi||'DOI not reported')} · ${refLink(x.doi,x.url,x.verification)} · ${esc(x.verification||'Verification status not reported')}</div></div>`).join('')}</div>`:'<div class="empty">No references were linked to retrieved records.</div>';
}
function renderResultDesign(data){
  const d=data.design_hypotheses||{};if(!d||!d.candidates?.length)return '<div class="empty">No exploratory formulation candidates were saved for this analysis.</div>';
  const interval=(x,lo,hi,digits=2)=>{
    const a=x?.[lo],b=x?.[hi];
    if(a===null||a===undefined||b===null||b===undefined)return 'Not available';
    return `${Number(a).toFixed(digits)}–${Number(b).toFixed(digits)}`;
  };
  const candidates=d.candidates.map((c,i)=>{
    const parts=(c.components||[]).map(x=>`<li>${esc(x)}</li>`).join('');
    const vars=(c.variables_to_screen||[]).map(x=>`<li>${esc(x)}</li>`).join('');
    const recipe=c.recipe?.components||[];
    const recipeTable=recipe.length?table(['Ingredient / component','Proposed amount','Notes'],recipe.map(x=>[esc(x.Component||x.component||'Not specified'),esc(x.Amount||x.amount||'Not specified'),esc(x.Note||x.note||'')])):'';
    const proposals=c.proposals||[];
    const proposalTable=proposals.length?table(['Batch','Modifier (%)','Lipid concentration (%)','Drug-to-lipid ratio','Size interval (nm)','PDI interval','Zeta interval (mV)','EE interval (%)','Model mode'],proposals.map(x=>[esc(x.batch??'—'),esc(x.modifier_pct??'—'),esc(x.lipid_conc_pct??'—'),esc(x.drug_to_lipid??'—'),esc(interval(x,'size_nm_low95','size_nm_high95',1)),esc(interval(x,'pdi_low95','pdi_high95',3)),esc(interval(x,'zeta_mv_low95','zeta_mv_high95',1)),esc(interval(x,'ee_pct_low95','ee_pct_high95',1)),esc(c.engine_mode==='prior_only'?'Prior-only · broad uncertainty':'Prior + GP')])):'';
    const ruleReasons=(c.rule_screening_reasons||[]).map(x=>`<li><strong>${esc(x.group||'Rule')} · ${esc(x.label||'')}</strong>${x.why?` — ${esc(x.why)}`:''}</li>`).join('');
    const recommended=Boolean(c.recommended_start);
    const ruleSummary=c.rule_screening_score===null||c.rule_screening_score===undefined?'No numerical carrier-rule screen available':`Rule-screening score ${Number(c.rule_screening_score).toFixed(2)} · ${c.route_alignment?'route supported in current rule set':'route fit not established'}`;
    const recipeMeta=c.recipe?`Scale: ${esc(c.recipe.scale??'not specified')} ${esc(c.recipe.scale_unit||'')}. ${esc(c.recipe.note||'')}`:'';
    return `<article class="candidate"><div class="candidate-top"><div><strong>${esc(c.carrier)} · Candidate ${esc(c.screening_rank??(i+1))}</strong><div class="meta-line">${esc(c.origin||'Exploratory design hypothesis')}</div></div><div>${recommended?badge('Recommended starting point to investigate','good'):''} ${badge(c.status==='requires_experimental_validation'?'Requires validation':'Draft','warn')}</div></div><p>${esc(c.rationale||'No rationale available.')}</p><p class="help"><strong>Screening:</strong> ${esc(ruleSummary)}. This prioritizes experimental investigation, not expected efficacy or proven performance.</p>${c.rule_screening_caveat?`<p class="help">${esc(c.rule_screening_caveat)}</p>`:''}${ruleReasons?`<details class="details"><summary>Why this carrier received this screening score</summary><ul>${ruleReasons}</ul></details>`:''}<div class="grid two"><div><h4 class="subsection">Possible formulation components</h4><ul>${parts||'<li>Not specified</li>'}</ul></div><div><h4 class="subsection">Variables to screen</h4><ul>${vars||'<li>Not specified</li>'}</ul></div></div>${recipeTable?`<h4 class="subsection">Starting recipe proposal</h4>${recipeTable}<p class="help">${recipeMeta} This is a proposed starting composition, not a validated standard operating procedure.</p>`:''}${proposalTable?`<h4 class="subsection">Design-space proposals</h4>${proposalTable}<p class="help">Ranges are 95% uncertainty intervals from the design engine. Prior-only results are broad prior estimates, not experimentally validated predictions. They should not be read as performance guarantees.</p>`:''}<p class="help">${esc(c.note||c.design_note||'Proposed concept only; verify ingredients, units and preparation details before laboratory use.')}${c.route_mapping_note?` ${esc(c.route_mapping_note)}`:''}</p></article>`;
  }).join('');
  const llmTitle=d.llm_citation_status==='hypothesis_only_no_literature_evidence'?'AI-generated exploratory hypothesis · no local evidence retrieved':'AI research synthesis · retrieved evidence';
  const llm=d.llm_answer?`<section class="panel"><h3 class="panel-title">${esc(llmTitle)} · ${esc(d.llm_model||'configured model')}</h3><div class="details-body">${esc(d.llm_answer)}</div>${d.llm_citation_status==='hypothesis_only_no_literature_evidence'?'<p class="notice blue">No literature records were retrieved for this exact request. The AI output is a hypothesis, not a published formulation or validated result.</p>':''}${d.llm_sources?.length?`<details class="details"><summary>Retrieved source records (${fmt(d.llm_sources.length)})</summary><div class="resolver-source-list">${d.llm_sources.map(x=>`<div class="resolver-source-row"><b>${esc(x.id||'Source')}</b> · ${esc(x.title||x.source_type||'Retrieved context')} · ${esc(x.doi||x.source_id||'Identifier not reported')}</div>`).join('')}</div></details>`:''}<p class="help">Citation check: ${esc(d.llm_citation_status||'not reported')} · ${esc(d.llm_status||'AI status not reported')}</p></section>`:`<div class="notice blue">${esc(d.llm_status==='disabled'?'AI language-model synthesis is not configured. Retrieved evidence and local design rules still provide exploratory candidates. Install Ollama and run setup_free_ai.bat to enable free local AI.':'AI narrative synthesis is unavailable. Check that Ollama is running and the selected model is installed; local rule-based candidates remain available.')}</div>`;
  const recommended=d.recommended_start?`<div class="notice blue"><strong>Suggested starting point: ${esc(d.recommended_start)}.</strong> This is the top result from the transparent carrier-rule screen, not a claim that it will perform best experimentally.</div>`:'';
  return `<div class="notice blue"><strong>${esc(d.trigger||'Exploratory formulation design')}</strong><br>${esc(d.evidence_boundary||'These candidates are not direct evidence or validated formulations.')}</div><div class="meta-line">Design mode: ${esc(d.mode||'Local hypothesis generation')} · ${fmt(d.candidates.length)} candidates</div>${recommended}<p class="help">${esc(d.ranking_method||'Candidate order is a preliminary screening aid. Review the evidence and validate experimentally.')}</p>${candidates}${llm}<p class="notice"><strong>Research use only.</strong> These concepts are starting points for expert review and laboratory testing. They do not establish safety, efficacy, targeting or clinical suitability.</p>`;
}
function renderResultEndpoints(data){
  const endpointRows=(data.endpoint_results||[]).map(x=>[esc(x.endpoint),esc(x.ml_status||'Not run'),esc(x.literature_status||'Not reported'),esc(x.note||'Not reported')]);
  const ai=(data.llm_explanation||'').trim();
  const aiSources=data.llm_sources||[];
  const aiSourceTable=aiSources.length?table(['Citation ID','Source type','Record ID','Paper / title','DOI (source record)'],aiSources.map(x=>[esc(x.id),esc(x.source_type||'Not reported'),esc(x.record_id||x.source_id||'Not reported'),esc(x.title||'Not reported'),esc(x.doi||'Not reported')])):'';
  return `<div class="panel"><h3 class="panel-title">Experimental and literature endpoint availability</h3>${table(['Endpoint','Prediction','Reported data','Interpretation'],endpointRows)}<p class="help">${esc(data.ml?.reason||'No executable predictive model is available.')}</p>${ai?`<details class="details"><summary>Saved AI interpretation · ${esc(data.llm_model||'model not reported')} · ${esc(data.llm_generated_at||'date not reported')}</summary><div class="details-body">${esc(ai)}</div>${aiSourceTable}<p class="help">Citation status: ${esc(data.llm_citation_status||'not reported')} · Usage log records token counts, estimated cost, latency and status.</p></details>`:'<p class="help">No AI interpretation has been stored for this analysis.</p>'}${data.gaps?.length?`<h4 class="subsection">Evidence gaps</h4><ul>${data.gaps.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<p class="notice blue">${esc(data.disclaimer||'Research-use only. Review primary sources and validate experimentally.')}</p></div>`;
}
async function wireResultDetails(analysisId){
  document.querySelectorAll('details[data-analysis-section]').forEach(node=>{
    node.addEventListener('toggle',async()=>{
      if(!node.open||node.dataset.loaded==='true'||node.dataset.loading==='true')return;
      node.dataset.loading='true';const content=node.querySelector('[data-detail-content]');content.innerHTML=loading();
      try{
        const section=node.dataset.analysisSection;
        const data=await api(`/api/analysis/${encodeURIComponent(analysisId)}/details?section=${encodeURIComponent(section)}`);
        const renderers={design:renderResultDesign,profile:renderResultProfile,carriers:renderResultCarriers,organ:renderResultOrgan,references:renderResultReferences,endpoints:renderResultEndpoints};
        content.innerHTML=renderers[section](data);node.dataset.loaded='true';
      }catch(error){content.innerHTML=`<div class="notice red">${esc(error.message)} <button type="button" class="btn ghost" data-retry-detail>Retry</button></div>`;content.querySelector('[data-retry-detail]')?.addEventListener('click',()=>{node.dataset.loading='false';node.dataset.loaded='false';node.open=false;node.open=true;});}
      finally{node.dataset.loading='false';}
    });
  });
  document.querySelectorAll('[data-open-result-section]').forEach(link=>link.addEventListener('click',event=>{
    event.preventDefault();const section=link.dataset.openResultSection;const panel=document.querySelector(`details[data-analysis-section="${CSS.escape(section)}"]`);if(panel){panel.open=true;panel.scrollIntoView({behavior:'smooth',block:'start'});}
  }));
}


async function wireResultExplanation(analysisId){
  const button=document.querySelector('#explainResultsButton');
  if(!button||button.disabled)return;
  button.addEventListener('click',async()=>{
    const output=document.querySelector('#resultExplanation');
    const status=document.querySelector('#explainResultsStatus');
    button.disabled=true;button.textContent='Checking limits and explaining…';
    output.innerHTML=loading();
    try{
      const result=await api(`/api/analysis/${encodeURIComponent(analysisId)}/explain`,{method:'POST',body:JSON.stringify({})});
      if(result.status==='success'&&result.answer){
        const sources=result.sources||[];
        output.innerHTML=`<section class="result-hero"><div class="page-kicker">Grounded AI explanation · ${esc(result.model||'model not reported')}</div><p style="white-space:pre-wrap">${esc(result.answer)}</p><p class="help">Generated ${esc(result.generated_at||'date not recorded')} · citation validation ${esc(result.citation_status||'not reported')} · estimated provider cost $${Number(result.usage?.estimated_cost_usd||0).toFixed(6)} · ${fmt(result.usage?.prompt_tokens)} input / ${fmt(result.usage?.completion_tokens)} output tokens</p><details class="details"><summary>Retrieved records supplied to the model (${fmt(sources.length)})</summary>${table(['Citation ID','Source type','Record ID','Title','DOI from source record'],sources.map(x=>[esc(x.id),esc(x.source_type||'Not reported'),esc(x.record_id||x.source_id||'Not reported'),esc(x.title||'Not reported'),esc(x.doi||'Not reported')]))}</details></section>`;
        status.textContent='Grounded explanation saved with validated source-record IDs. Review each cited record before relying on it.';
        document.querySelectorAll('details[data-analysis-section="endpoints"]').forEach(node=>{node.dataset.loaded='false';if(node.open){node.open=false;node.open=true;}});
      }else{
        output.innerHTML=`<div class="notice blue">No generated explanation returned (${esc(result.status||'not available')}). The existing non-AI evidence summary remains available.</div>`;
        status.textContent=`No generated text was returned: ${result.status||'not available'}.`;
      }
    }catch(error){output.innerHTML=`<div class="notice red">${esc(error.message)}</div>`;status.textContent='Explanation request failed; the non-AI result summary is unchanged.';}
    finally{button.disabled=false;button.textContent='Explain these results';}
  });
}
