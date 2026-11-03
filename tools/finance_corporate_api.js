/* 对公新增身份：开户行取固定官方目录，纳税人关联沿原字段change过程；不执行客户端回调。 */
async function reimbursementCorporateBank(c,p){
  const contract=c.corporate_contract,requests=[];
  if(!contract||contract.window!=='56')throw Error('CORPORATE_API_CONTRACT_REQUIRED');
  if([p.name,p.bank_name,p.bank_account].some(v=>typeof v!=='string'||v.includes('$|')||/[\x00-\x1f\x7f]/.test(v)))throw Error('CORPORATE_API_FIELD_SEPARATOR');
  const http=createReimbursementHttp(c.expected_userid,{prefix:'CORPORATE_API',requests,timeout:15000,allowed:['definition','gridDefinition','query']});
  const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
  const hash=async v=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(v)))))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const d=await http.post('loadDefinition.action?type=W',{winno:'56',projid:'WF_YBX'});
  if(await hash(Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]])))!==contract.definition_sha256)throw Error('CORPORATE_API_DEFINITION_CHANGED');
  const raw=await http.post('loadDefinition.action?type=Q',{funcNo:'80',projid:'WF_YBX'});
  if(!Array.isArray(raw)||raw.length!==1||await hash(raw[0])!==contract.query_sha256||d.funcs.find(f=>String(f.funcno)==='80')?.tablenames!==contract.bank_query.tablenames)throw Error('CORPORATE_API_QUERY_CHANGED');
  const columns=raw[0].colModel.map(f=>f.name);
  if(JSON.stringify(columns)!==JSON.stringify(['t.branchcode','t.branchname'])||raw[0].needcount!==true)throw Error('CORPORATE_API_BANK_COLUMNS');
  const given=p.bank_name.match(/^(\d{12})\/(.+)$/),name=(given?given[2]:p.bank_name.replace(/^\//,'')).trim();
  if(!name)throw Error('CORPORATE_API_BRANCH_NAME_REQUIRED');
  const {data:context,set,get}=http.contextCopy();
  // init_branchinfo displays branchname1 only for the native "up" variant.
  const nameField=get('0-show_branchname')==='up'?'82-t.branchname1':'82-t.branchname';
  for(const key of Object.keys(context))if(key.startsWith('WF_YBX$82-')||key.startsWith('WF_YBX$80-'))delete context[key];
  for(const field of ['province','city','bankname','branchname','branchname1'])set('82-t.'+field,'');
  set('82-t.branchcode',given?given[1]:'');set(nameField,name);set('80-COND','');set('80-CONDOBJ',[]);set('80-CURRPAGE',1);
  const variants=[...new Set([name,name.replace('股份有限公司',''),...(name.endsWith('支行')?[name.slice(-5).replace(/^[市县区]/,'')]:[])])];
  const rows=[];let count=null,pages=1,queryVariant=0;
  const nextName=()=>{if(given||++queryVariant>=variants.length)return false;set(nameField,variants[queryVariant]);return true;};
  for(let page=1;page<=pages;page++){
    const r=await http.post('commonQuery_doQuery.action?funcno=80&projid=WF_YBX&needcount=true',{_search:false,nd:Date.now(),page,rows:'100',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
    if(Array.isArray(r)&&r.length===0&&page===1&&count===null){
      if(nextName()){page=0;continue;}
      count=0;pages=0;break;
    }
    const n=Number(r?.records),total=Number(r?.total);
    if(!Number.isSafeInteger(n)||n<0||n>5000||!Number.isSafeInteger(total)||total!==Math.ceil(n/100)||
       !(Number(r.page)===page||!n&&Number(r.page)===0)||!Array.isArray(r.rows)||r.rows.length!==Math.min(100,Math.max(0,n-(page-1)*100))||count!==null&&n!==count)throw Error('CORPORATE_API_INCOMPLETE_BANK_QUERY:'+JSON.stringify({type:Array.isArray(r)?'array':typeof r,keys:r&&typeof r==='object'?Object.keys(r):[],records:r?.records,total:r?.total,page:r?.page,rows:Array.isArray(r?.rows)?r.rows.length:null}));
    if(n===0&&page===1&&count===null&&nextName()){page=0;continue;}
    count=n;pages=total;
    for(const row of r.rows){if(!Array.isArray(row.cell)||row.cell.length!==2)throw Error('CORPORATE_API_BANK_ROW');rows.push(Object.fromEntries(columns.map((k,i)=>[k,ReimbursementHttpValues.decode(row.cell[i])])));}
  }
  if(new Set(rows.map(r=>r['t.branchcode'])).size!==rows.length)throw Error('CORPORATE_API_DUPLICATE_BANK');
  const canonicalName=value=>value.trim().replace('股份有限公司','');
  const matched=rows.filter(r=>canonicalName(r['t.branchname'])===canonicalName(name)&&(!given||r['t.branchcode']===given[1]));
  if(matched.length!==1||!/^\d{12}$/.test(matched[0]['t.branchcode']))throw Error('CORPORATE_API_BRANCH_NOT_UNIQUE:'+JSON.stringify({requested:name,candidates:rows}));
  return {branchcode:matched[0]['t.branchcode'],branchname:matched[0]['t.branchname'],requests};
}

async function reimbursementCorporateResolve(c,built,binding,beforeDispatch){
  const contract=c.corporate_contract,field=built.definition.funcs.find(f=>String(f.funcno)==='24')?.formFields.find(f=>f.fieldname==='t.banksname');
  const events=field?.Events?.filter(e=>e.event==='change')||[];
  if(events.length!==1||events[0].proc!==contract.taxpayer_event||events[0].callBack!=='after_transfer_taxord(idx);'||
     events[0].proc.split('|')[0]!==contract.taxpayer_proc)throw Error('CORPORATE_API_TAXPAYER_EVENT_CHANGED');
  const form=built.definition.funcs.find(f=>String(f.funcno)==='24'),rows=built.target['24'];
  for(const f of form.formFields){const key=f.fieldname.slice(2).toUpperCase();built.set('24-'+f.fieldname,rows.map(r=>r[key]).join('$|'));}
  built.set('24-MULTICOUNT',String(rows.length));built.set('0-multi_form_index',String(binding.row_index));built.set('0-taxpayer_ord','');built.set('61-serialno',c.serial);
  // 官方callProc对$过程直接传procName、独立userData与从0开始的表单行下标。
  beforeDispatch();
  const raw=await built.http.post('common_callProc.action',{procName:contract.taxpayer_proc,userData:JSON.stringify(built.context),projid:'WF_YBX',idx:String(binding.row_index)},'text');
  const parsed=ReimbursementNativeProtocol.applyResponse(raw,built.context,['0-taxpayer_ord']);
  const ordinal=parsed.updates['0-TAXPAYER_ORD'];
  if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(ordinal||'')||ordinal==='0')throw Error('CORPORATE_API_NATIVE_TAXPAYER_UNAVAILABLE');
  rows[binding.row_index].TAXPAYER_ORD=ordinal;built.set('0-taxpayer_ord','');
  return {state:'accepted',row_index:binding.row_index,taxpayer_ord:ordinal,name:binding.name,account:binding.account,bank_name:binding.bank_name};
}
