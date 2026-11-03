/* 日常支付：个人卡取学校候选；对公只使用同单已完整绑定的收款身份。 */
async function reimbursementPaymentsBuild(c) {
  'use strict';
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'PAYMENTS_API',requests,timeout:15000,allowed:['definition','gridDefinition','query','paymentCandidate','button','bankCheck','procedure']});
  const {moneyCents,decode}=ReimbursementHttpValues;
  if(!Array.isArray(c.payments)||!c.payments.length)throw Error('PAYMENTS_API_PAYMENT_LIST');
  const corporatePayments=c.payments.filter(p=>p.mode==='corporate_transfer');
  const corporate=corporatePayments.length>0;
  if(c.payments.some(p=>typeof p.name!=='string'||!p.name||moneyCents(p.amount)<=0n||
      (p.mode==='corporate_transfer'? p.expected_userid!==c.expected_userid||!/^\d{12,30}$/.test(p.bank_account?.replaceAll(' ','')||'')||
        typeof p.bank_name!=='string'||!p.bank_name||!Array.isArray(p.source_refs)||!p.source_refs.length||p.source_refs.some(r=>typeof r!=='string'||!r.trim())||Object.hasOwn(p,'person_id'):
        !['bank_transfer','corporate_card'].includes(p.mode)||!/^[A-Za-z0-9]+$/.test(p.person_id)))||
      new Set(c.payments.map(p=>p.mode==='corporate_transfer'?'corporate:'+p.bank_account.replaceAll(' ',''):p.mode+':'+p.person_id)).size!==c.payments.length||
      c.payments.reduce((sum,p)=>sum+moneyCents(p.amount),0n)!==moneyCents(c.expected_amount))throw Error('PAYMENTS_API_PAYMENT_SCOPE');
  const cards=c.payments.filter(p=>p.mode==='corporate_card'),bankPayments=c.payments.filter(p=>p.mode==='bank_transfer');
  if(corporate&&cards.length)throw Error('PAYMENTS_API_MIXED_CARD_UNADAPTED');
  if(cards.length>1||cards.some(p=>!/^\d{4}-\d{2}-\d{2}$/.test(p.transaction?.date||'')||
      typeof p.transaction?.merchant!=='string'||!p.transaction.merchant||!/^\d{4}$/.test(p.transaction.card_suffix||'')||
      !['A','B','C'].includes(p.transaction.search_period||'A')||moneyCents(p.transaction.search_amount||p.amount)<moneyCents(p.amount)))throw Error('PAYMENTS_API_CARD_TRANSACTION');
  for(const p of c.payments) if(Object.hasOwn(p,'remark') && (typeof p.remark!=='string' ||
      /[\x00-\x1f\x7f]/.test(p.remark) || Array.from(p.remark).reduce((n,ch)=>n+(ch.codePointAt(0)>255?2:1),0)>25))
    throw Error('PAYMENTS_API_REMARK');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'payments'});
  if(read.bill['t.status']!=='-99' || read.bill['t.lc_finish']!=='F') throw Error('PAYMENTS_API_NOT_DRAFT');
  const definition=await http.post('loadDefinition.action?type=W',{winno:'13',projid:'WF_YBX'});
  const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
  const projected=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,definition[k]]));
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(v=>v.toString(16).padStart(2,'0')).join('');
  if(hash!==c.payments_contract.definition_sha256) throw Error('PAYMENTS_API_DEFINITION_CHANGED');
  const win=definition.wins.find(w=>String(w.winno)==='13');
  const save=win?.func_btns.find(b=>b.btnguid===c.payments_contract.save_button);
  if(!save || save.pre_compute || save.jscontent || save.wf_enabled || save.hasIntf_beforeclick || save.hasIntf_afterclick)
    throw Error('PAYMENTS_API_SAVE_CONTRACT');
  const form=definition.funcs.find(f=>String(f.funcno)==='22');
  const lookup=form?.formFields.find(f=>f.fieldname==='t.sno')?.binding_data;
  if(lookup!=='@$df52e671fa8782a8f989c8f7c934542e|#22-t.sno#,#0-userinfo.userid#,#61-serialno#') throw Error('PAYMENTS_API_LOOKUP_CONTRACT');
  const original={},target={};
  for(const contract of c.contracts) {
    const no=contract.funcno,native=definition.funcs.find(f=>String(f.funcno)===no);
    const sections=read.sections.filter(s=>s.funcno===no && s.kind==='form');
    if(!native?.formFields || sections.length!==1) throw Error('PAYMENTS_API_FORM_BINDING');
    original[no]=sections[0].rows.map(row=>Object.fromEntries(native.formFields.map(field=>{
      const key=field.fieldname.replace(/^t\./,'').toUpperCase(),value=row[key];
      if(!Object.hasOwn(row,key) || value!==null && typeof value!=='string') throw Error('PAYMENTS_API_FIELD_BINDING:'+no+'/'+key);
      if(value?.includes('$|')) throw Error('PAYMENTS_API_FIELD_SEPARATOR');
      return [key,value??''];
    })));
    target[no]=original[no].map(row=>({...row}));
  }
  if(target['19']?.length!==1 || moneyCents(target['19'][0].BX_AMT)!==moneyCents(c.expected_amount) ||
      moneyCents(target['19'][0].CLR_AMT||'0')!==0n) throw Error('PAYMENTS_API_SUMMARY');
  // 完整保存过程会带上所有支付方式；拒绝静默覆盖其他非空支付或借款抵扣。
  for(const [no,rows] of Object.entries(target)) if(!['19',...(corporate?['24']:[]),...(bankPayments.length?['22']:[]),...(cards.length?['285']:[])].includes(no) &&
    !(no==='24'&&rows.every(row=>row.AMOUNT!==''&&moneyCents(row.AMOUNT)===0n)) && rows.some(row=>
      Object.entries(row).some(([key,value])=>value && !/^0(?:\.0{1,2})?$/.test(value) && !['IS_ENTRUST_BJ_LABORATORY'].includes(key))))
    throw Error('PAYMENTS_API_OTHER_PAYMENT_PRESENT:'+no);
  const current=target['22'].filter(row=>row.SNO || moneyCents(row.AMOUNT||'0')>0n);
  const bindings=[],cardBindings=[],corporateBindings=[];let cardSelection=null;
  if(cards.length){
    if(window.jQuery.UC.bindData('#0-GWKSELECT#')!=='N')throw Error('PAYMENTS_API_CARD_ROUTE_UNADAPTED');
    const contract=c.card_contract;
    if(!contract||contract.winno!=='584')throw Error('PAYMENTS_API_CARD_CONTRACT_REQUIRED');
    const d=await http.post('loadDefinition.action?type=W',{winno:'584',projid:'WF_YBX'});
    const p=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
    const digest=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(value)))))].map(n=>n.toString(16).padStart(2,'0')).join('');
    if(await digest(p)!==contract.definition_sha256)throw Error('PAYMENTS_API_CARD_DEFINITION_CHANGED');
    const raw=await http.post('loadDefinition.action?type=Q',{funcNo:'793',projid:'WF_YBX'});
    if(!Array.isArray(raw)||raw.length!==1||await digest(raw[0])!==contract.query_definition_sha256)throw Error('PAYMENTS_API_CARD_QUERY_CHANGED');
    const grid=d.funcs.find(f=>String(f.funcno)==='793');
    if(grid.tablenames!==contract.grid.tablenames||grid.multifields!=='t.real_amount,t.seq_no'||grid.needcount!==false)throw Error('PAYMENTS_API_CARD_GRID');
    for(const card of cards){
      const {data:query,set}=http.contextCopy();set('61-serialno',c.serial);set('792-t.no',card.person_id);set('792-t.pay_time',card.transaction.search_period||'A');
      const cents=moneyCents(card.transaction.search_amount||card.amount),fraction=String(cents%100n).padStart(2,'0').replace(/0+$/,'');
      set('792-t.pay_amt',String(cents/100n)+(fraction?'.'+fraction:''));set('793-COND','');set('793-CONDOBJ',[]);set('793-CURRPAGE',1);
      const reply=await http.post('commonQuery_doQuery.action?funcno=793&projid=WF_YBX',{_search:false,nd:Date.now(),page:1,rows:'10000',sidx:'',sord:'',userData:JSON.stringify(query),itemArr:'',sectionArr:''});
      const count=Number(reply?.records),pages=Number(reply?.total),page=Number(reply?.page),columns=raw[0].colModel.map(f=>f.name);
      if(!Number.isSafeInteger(count)||count<0||count>10000||!Array.isArray(reply.rows)||reply.rows.length!==count||!Number.isSafeInteger(pages)||pages<0||pages>1||![0,1].includes(page)||count>0&&(pages!==1||page!==1))throw Error('PAYMENTS_API_CARD_QUERY_INCOMPLETE');
      const all=reply.rows.map(item=>{
        if(!Array.isArray(item.cell)||item.cell.length!==columns.length)throw Error('PAYMENTS_API_CARD_COLUMNS');
        return Object.fromEntries(columns.map((k,i)=>[k,decode(item.cell[i])]));
      });
      if(new Set(all.map(r=>r['t.seq_no'])).size!==all.length)throw Error('PAYMENTS_API_CARD_DUPLICATE_SEQUENCE');
      const found=all.filter(r=>r['t.accname']===card.name&&r['t.pay_time'].startsWith(card.transaction.date)&&r['t.pay_addr']===card.transaction.merchant&&moneyCents(r['t.pay_amt'])===cents&&r['t.account'].endsWith(card.transaction.card_suffix));
      if(found.length!==1||!found[0]['t.seq_no'])throw Error('PAYMENTS_API_CARD_TRANSACTION_NOT_UNIQUE');
      const transaction=found[0],matches=target['285'].filter(r=>r.GWK_SEQ_NO===transaction['t.seq_no']);
      if(matches.length!==1){
        if(matches.length||target['285'].some(r=>r.GWK_SEQ_NO||r.GWK_BANKACNT||moneyCents(r.AMOUNT||'0')!==0n))throw Error('PAYMENTS_API_CARD_SELECTION_REQUIRED');
        if(!['','0'].includes(transaction['t.req_no']||'')||!['','0','F','N'].includes(transaction['t.selected']||'')||
           moneyCents(original['19'][0].WT_AMT||'0')<moneyCents(card.amount))throw Error('PAYMENTS_API_CARD_TRANSACTION_ALREADY_BOUND_OR_NO_BALANCE');
        const buttons=d.wins.find(w=>String(w.winno)==='585')?.func_btns.filter(b=>b.caption==='保存')||[];
        if(buttons.length!==1||buttons[0].check_func||buttons[0].pre_compute||buttons[0].wf_enabled||buttons[0].customFunc||buttons[0].jscontent||
           JSON.stringify(buttons[0].procs)!==JSON.stringify(['`$eec573bd273e86a1f3c7946333d8e84a:#793-MultiRows#;#793-MultiCount#;#19-t.wt_amt#;#61-serialno#;#0-userinfo.userid#'])||
           !/^[A-Za-z0-9_-]+$/.test(transaction['t.seq_no']))throw Error('PAYMENTS_API_CARD_SELECTION_CONTRACT');
        cardSelection=buttons[0];cardBindings.push({person_id:card.person_id,transaction,selection_required:true,selected_amount:card.amount});continue;
      }
      const bound=matches[0];
      if(bound.GWK_BANKSNAME!==card.name||bound.GWK_BANKACNT!==transaction['t.account']||!bound.GWK_DATE.startsWith(card.transaction.date)||moneyCents(bound.GWK_PAY_AMT)!==cents||moneyCents(bound.AMOUNT||'0')>cents)throw Error('PAYMENTS_API_CARD_BOUND_IDENTITY');
      if(target['285'].some(r=>r!==bound&&moneyCents(r.AMOUNT||'0')!==0n))throw Error('PAYMENTS_API_UNPLANNED_CARD');
      const native=definition.funcs.find(f=>String(f.funcno)==='285');
      for(const key of ['t.amount',...(Object.hasOwn(card,'remark')?['t.gwk_remark']:[])]){
        const field=native.formFields.find(f=>f.fieldname===key);
        if(!field||field.readonly||field.hidden)throw Error('PAYMENTS_API_CARD_FIELD_NOT_EDITABLE');
      }
      bound.AMOUNT=card.amount;if(Object.hasOwn(card,'remark'))bound.GWK_REMARK=card.remark;
      cardBindings.push({person_id:card.person_id,transaction});
    }
  }
  if(corporate){
    const rows=target['24'],used=new Set();
    for(const p of corporatePayments){
      const matches=rows.map((row,index)=>({row,index})).filter(({row})=>row.BANKSNAME===p.name&&
        /^[0-9 ]+$/.test(row.BANKACNT)&&row.BANKACNT.replaceAll(' ','')===p.bank_account.replaceAll(' ','')&&
        (row.BANKNAME===p.bank_name||/^\d{12}\//.test(row.BANKNAME)&&row.BANKNAME.slice(13)===p.bank_name));
      if(matches.length!==1){
        if(matches.length)throw Error('PAYMENTS_API_EXISTING_CORPORATE_IDENTITY_REQUIRED');
        // A draft may prefill an invoice issuer's historical account with zero
        // payable amount. Update that unique, unpaid row for this bill only;
        // retain its taxpayer identity and never rewrite a company master record.
        const sameName=rows.map((row,index)=>({row,index})).filter(({row})=>row.BANKSNAME===p.name);
        if(sameName.length){
          if(sameName.length!==1||used.has(sameName[0].index)||moneyCents(sameName[0].row.AMOUNT||'0')!==0n||
             !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(sameName[0].row.TAXPAYER_ORD||'')||sameName[0].row.TAXPAYER_ORD==='0'||
             rows.some((r,i)=>i!==sameName[0].index&&r.BANKACNT?.replaceAll(' ','')===p.bank_account.replaceAll(' ','')))
            throw Error('PAYMENTS_API_CONFLICTING_CORPORATE_IDENTITY');
          const {row,index}=sameName[0],bank=await reimbursementCorporateBank(c,p);requests.push(...bank.requests);
          const native=definition.funcs.find(f=>String(f.funcno)==='24');
          for(const key of ['t.bankacnt','t.bankname','t.amount',...(Object.hasOwn(p,'remark')?['t.remark']:[])]){
            const f=native.formFields.find(f=>f.fieldname===key);if(!f||f.readonly||f.hidden)throw Error('PAYMENTS_API_CORPORATE_FIELD_NOT_EDITABLE');
          }
          const previous={account:row.BANKACNT,bank_name:row.BANKNAME,taxpayer_ord:row.TAXPAYER_ORD,amount:row.AMOUNT};
          Object.assign(row,{BANKACNT:p.bank_account.replaceAll(' ',''),BANKNAME:bank.branchcode+'/'+bank.branchname,AMOUNT:p.amount});
          if(Object.hasOwn(p,'remark'))row.REMARK=p.remark;
          used.add(index);corporateBindings.push({row_index:index,name:p.name,account:row.BANKACNT,bank_name:row.BANKNAME,
            resolve_required:false,unpaid_prefill_replaced:previous,taxpayer_ord:row.TAXPAYER_ORD});continue;
        }
        // Otherwise reuse only a completely blank row; preserve other identities.
        let index=rows.findIndex((r,i)=>!used.has(i)&&Object.entries(r).every(([k,v])=>
          k==='AMOUNT'?moneyCents(v||'0')===0n:k==='IS_ENTRUST_BJ_LABORATORY'?['','N','F'].includes(v):!v));
        if(index<0)index=rows.length;
        if(rows.some(r=>r.BANKSNAME===p.name||r.BANKACNT?.replaceAll(' ','')===p.bank_account.replaceAll(' ','')))throw Error('PAYMENTS_API_CONFLICTING_CORPORATE_IDENTITY');
        const bank=await reimbursementCorporateBank(c,p);requests.push(...bank.requests);
        const native=definition.funcs.find(f=>String(f.funcno)==='24'),row=rows[index]||Object.fromEntries(native.formFields.map(f=>[f.fieldname.slice(2).toUpperCase(),'']));
        for(const key of ['t.banksname','t.bankacnt','t.bankname','t.amount']){const f=native.formFields.find(f=>f.fieldname===key);if(!f||f.readonly||f.hidden)throw Error('PAYMENTS_API_NEW_CORPORATE_FIELD_NOT_EDITABLE');}
        Object.assign(row,{BANKSNAME:p.name,BANKACNT:p.bank_account.replaceAll(' ',''),BANKNAME:bank.branchcode+'/'+bank.branchname,AMOUNT:p.amount});
        if(Object.hasOwn(p,'remark'))row.REMARK=p.remark;
        rows[index]=row;used.add(index);corporateBindings.push({row_index:index,name:p.name,account:row.BANKACNT,bank_name:row.BANKNAME,resolve_required:true});continue;
      }
      if(used.has(matches[0].index))throw Error('PAYMENTS_API_EXISTING_CORPORATE_IDENTITY_REQUIRED');
      const {row,index}=matches[0];used.add(index);
      if(moneyCents(row.AMOUNT||'0')!==0n&&moneyCents(row.AMOUNT)!==moneyCents(p.amount))throw Error('PAYMENTS_API_EXISTING_CORPORATE_AMOUNT');
      const native=definition.funcs.find(f=>String(f.funcno)==='24');
      for(const key of ['t.amount','t.bankacnt',...(Object.hasOwn(p,'remark')?['t.remark']:[])]){
        const field=native.formFields.find(f=>f.fieldname===key);
        if(!field||field.readonly||field.hidden)throw Error('PAYMENTS_API_CORPORATE_FIELD_NOT_EDITABLE');
      }
      row.AMOUNT=p.amount;row.BANKACNT=p.bank_account.replaceAll(' ','');
      if(Object.hasOwn(p,'remark'))row.REMARK=p.remark;
    }
    if(rows.some((row,index)=>!used.has(index)&&moneyCents(row.AMOUNT||'0')!==0n))throw Error('PAYMENTS_API_UNPLANNED_CORPORATE_AMOUNT');
  }
  {
  if(current.some(row=>moneyCents(row.AMOUNT||'0')>0n&&!bankPayments.some(p=>p.person_id===row.SNO && p.name===row.SNAME))) throw Error('PAYMENTS_API_EXISTING_RECIPIENT_SCOPE');
  for(const p of bankPayments) {
    const raw=await http.post('commonQuery_autoComplete_f.action?funcno=22&fieldname=t.sno&id=22-t.sno&sid=df52e671fa8782a8f989c8f7c934542e&&projid=WF_YBX',
      {q:p.person_id,limit:100,timestamp:Date.now(),'#22-t.sno#':p.person_id,'#0-userinfo.userid#':c.expected_userid,'#61-serialno#':c.serial},'text');
    const matches=[],candidate_identities=[];
    for(const entry of raw.trim()?raw.split('$|$'):[]) {
      const pair=entry.split('|$|');if(pair.length!==2) throw Error('PAYMENTS_API_CANDIDATE_PROTOCOL');
      const fields=decode(pair[1]).split('>');
      if(fields.length!==6) throw Error('PAYMENTS_API_CANDIDATE_FIELDS');
      candidate_identities.push({name:fields[0],id:fields[1],empty_fields:fields.map((v,i)=>v?null:i).filter(i=>i!==null)});
      if(fields[0]===p.name && fields[1].split(':')[0]===p.person_id) matches.push(fields);
    }
    if(matches.length!==1 || [0,1,2,4,5].some(i=>!matches[0][i]||matches[0][i].includes('$|'))) throw Error('PAYMENTS_API_BANK_NOT_UNIQUE:'+JSON.stringify({requested_id:p.person_id,requested_name:p.name,candidates:candidate_identities}));
    const [name,,account,,bank,key]=matches[0];bindings.push({person_id:p.person_id,name,account,bank,key});
  }
  if(bankPayments.length)target['22']=bankPayments.map((p,i)=>{
    const old=current.filter(row=>row.SNO===p.person_id);
    if(old.length>1) throw Error('PAYMENTS_API_DUPLICATE_EXISTING_RECIPIENT');
    const row=Object.fromEntries(form.formFields.map(f=>[f.fieldname.slice(2).toUpperCase(),'']));
    Object.assign(row,old[0]||{}, {SNO:p.person_id,SNAME:p.name,CNT1:bindings[i].account,
      BANKACNT:bindings[i].account,BANKNAME:bindings[i].bank,KEY:bindings[i].key,AMOUNT:p.amount});
    if(Object.hasOwn(p,'remark')) row.REMARK=p.remark;
    return row;
  });
  }
  target['19'][0].YT_AMT=c.expected_amount;target['19'][0].WT_AMT='0.00';
  const {data:context,set,get}=http.contextCopy();
  const flag=get('0-CARDCHECK_BXDLR');
  if(typeof flag!=='string') throw Error('PAYMENTS_API_BANK_CHECK_FLAG_MISSING');
  const changes={};
  const normalized=(no,rows)=>rows.map(row=>Object.fromEntries(Object.entries(row).map(([key,value])=>
    [key,(key==='AMOUNT' || no==='19' && key.endsWith('_AMT')) && value!==''?String(moneyCents(value)):['DEPART','REMARK'].includes(key)&&typeof value==='string'&&!value.trim()?'':value])));
  for(const [no,rows] of Object.entries(target)) if(JSON.stringify(normalized(no,rows))!==JSON.stringify(normalized(no,original[no])))changes[no]=rows;
  const report={ok:true,complete:true,action:'payments-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    business_transport:'native_http',system_write:false,save_dispatched:false,page_context_modified:false,bill:read.bill,
    original,changes,bank_bindings:bindings,card_bindings:cardBindings,corporate_bindings:corporateBindings,bank_check_flag:flag,definition_sha256:hash,
    requests:[...read.requests,...requests],observed_at:new Date().toISOString()};
  return {report,http,requests,context,set,get,definition,win,save,target,cardSelection,read};
}

async function reimbursementPaymentsPrepare(c){return (await reimbursementPaymentsBuild(c)).report;}

async function reimbursementPaymentsSave(c){
  let dispatched=false,prepared=null,cardSelectionProof=null;const corporateResolution=[],traceGroups=[];
  const traces=()=>traceGroups.flatMap(g=>[...g.initial,...g.own.slice(g.length)]);
  const traceBuild=b=>traceGroups.push({initial:b.report.requests,own:b.requests,length:b.requests.length});
  try{
    if(c.execute!==true || !c.expected_plan) throw Error('PAYMENTS_API_WRITE_INTENT');
    let built=await reimbursementPaymentsBuild(c);prepared=built.report;traceBuild(built);
    for(const key of ['original','changes','bank_bindings','card_bindings','corporate_bindings','bank_check_flag','definition_sha256'])
      if(JSON.stringify(prepared[key])!==JSON.stringify(c.expected_plan[key])) throw Error('PAYMENTS_API_PLAN_DRIFT:'+key);
    const pendingCard=prepared.card_bindings.filter(b=>b.selection_required);
    if(pendingCard.length){
      if(pendingCard.length!==1||!built.cardSelection)throw Error('PAYMENTS_API_CARD_SELECTION_SCOPE');
      const binding=pendingCard[0],before=prepared;
      // 官方getMultiRowsToContext按multifields顺序，以&分列、;分行；只允许一笔精确交易。
      built.set('793-MultiRows',binding.selected_amount+'&'+binding.transaction['t.seq_no']);built.set('793-MultiCount','1');
      built.set('19-t.wt_amt',prepared.original['19'][0].WT_AMT);built.set('61-serialno',c.serial);
      dispatched=true;cardSelectionProof={transaction:binding.transaction,selected_amount:binding.selected_amount,state:'dispatched'};
      const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',built.cardSelection.procs,built.context,{}),
        projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
      const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(keys.some(k=>!/^61-(?!serialno$)[A-Za-z0-9_.]+$/i.test(k))||!ReimbursementNativeProtocol.response(raw,keys).accepted)throw Error('PAYMENTS_API_CARD_SELECTION_REJECTED');
      cardSelectionProof.state='accepted';
      built=await reimbursementPaymentsBuild(c);prepared=built.report;traceBuild(built);
      for(const [no,rows]of Object.entries(before.original))if(!['19','285'].includes(no)&&JSON.stringify(rows)!==JSON.stringify(prepared.original[no]))throw Error('PAYMENTS_API_CARD_SELECTION_CHANGED_OTHER_SECTION');
      const head=before.original['19'][0],freshHead=prepared.original['19'][0];
      for(const key of Object.keys(head))if(!['YT_AMT','WT_AMT'].includes(key)&&head[key]!==freshHead[key])throw Error('PAYMENTS_API_CARD_SELECTION_CHANGED_SUMMARY');
      const fresh=prepared.original['285'].filter(r=>r.GWK_SEQ_NO||r.GWK_BANKACNT);
      if(fresh.length!==1||fresh[0].GWK_SEQ_NO!==binding.transaction['t.seq_no']||fresh[0].GWK_BANKACNT!==binding.transaction['t.account']||
         ReimbursementHttpValues.moneyCents(fresh[0].AMOUNT)!==ReimbursementHttpValues.moneyCents(binding.selected_amount)||prepared.card_bindings.some(b=>b.selection_required))throw Error('PAYMENTS_API_CARD_SELECTION_READBACK');
      cardSelectionProof.post_binding_plan=prepared;
    }
    for(const binding of prepared.corporate_bindings.filter(b=>b.resolve_required)){
      const proof={state:'not_dispatched',...binding};corporateResolution.push(proof);
      Object.assign(proof,await reimbursementCorporateResolve(c,built,binding,()=>{dispatched=true;proof.state='dispatched'}));
    }
    if(!Object.keys(prepared.changes).length) return {...prepared,action:'payments-save',state:dispatched?'saved_independent_readback_required':'already_matches_readback_required',
      system_write:dispatched,save_dispatched:dispatched,card_selection:cardSelectionProof,corporate_resolution:corporateResolution,requests:traces(),requires_readback:true};
    const {context,set}=built;
    for(const native of built.definition.funcs) {
      const no=String(native.funcno);
      for(const key of Object.keys(context)) if(key.startsWith('WF_YBX$'+no+'-')) delete context[key];
      if(!built.target[no]) continue;
      const rows=built.target[no];
      for(const field of native.formFields) {
        const key=field.fieldname.replace(/^t\./,'').toUpperCase();
        set(no+'-'+field.fieldname,rows.map(r=>r[key]).join(native.multiRec?'$|':''));
      }
      if(native.multiRec) set(no+'-MULTICOUNT',String(rows.length));
    }
    set('61-serialno',c.serial);set('61-bankcheck_id','');
    if(prepared.bank_check_flag!=='F'&&prepared.bank_bindings.length) {
      const username=built.get('0-userinfo.username');if(typeof username!=='string' || !username) throw Error('PAYMENTS_API_OPERATOR_NAME');
      const data=prepared.bank_bindings.map(b=>({check_name:b.name,check_idcard:'*',check_opt_unino:c.expected_userid,
        check_opt_name:username,check_bankno:b.account,check_branchcode:b.bank.split('/')[0],check_unino:b.person_id,
        check_biz_id:'',check_personid_md5:'',check_bankno_md5:b.key}));
      dispatched=true;
      const token=await built.http.post('bankCheck_BankCheckDtl.action',{checkinfo:JSON.stringify(data)},'text');
      if(!/^\d+$/.test(token)) throw Error('PAYMENTS_API_BANK_CHECK_REJECTED');
      set('61-bankcheck_id',token);
    }
    const command=ReimbursementNativeProtocol.command(built.save.check_func,built.save.procs,context,{});
    built.http.guard();dispatched=true;
    const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:command,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    if(!ReimbursementNativeProtocol.response(raw,[]).accepted) throw Error('PAYMENTS_API_SAVE_REJECTED');
    return {...prepared,action:'payments-save',state:'saved_independent_readback_required',system_write:true,save_dispatched:true,
      requires_readback:true,card_selection:cardSelectionProof,corporate_resolution:corporateResolution,requests:traces()};
  }catch(error){
    return {ok:false,complete:false,action:'payments-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
      business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,prepared,card_selection:cardSelectionProof,corporate_resolution:corporateResolution,requests:traces(),requires_readback:true,
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',error:String(error.message)};
  }
}
