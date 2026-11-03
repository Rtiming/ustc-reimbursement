const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../tools/finance_applicant_verification.js'),'utf8');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
async function run(mode='ok'){
  const opened=[],posts=[],values={},elements={},pending={};
  const element=()=>({getClientRects:()=>[{}],closest:()=>null});
  elements['gridWF_YBX_101']=element();
  const code=element();Object.defineProperty(code,'value',{get(){throw Error('CODE_MUST_NOT_BE_READ');}});
  const bind=k=>values[k.replaceAll('#','').toUpperCase()];
  function setData(k,v){values[k.replace('WF_YBX$','').toUpperCase()]=v;}
  function openExternal(project,win){opened.push(win);elements[win==='76'?'formWF_YBX_109_t-remark':'formWF_YBX_1362_t-code']=win==='76'?element():code;}
  function handler(){}
  const definition={wins:[],funcs:[],funcMap:{},actMap:{}};
  const contract={verification_guard_sha256:sha(String(handler)),agreement_guard_sha256:sha(String(handler)),verification_callback_sha256:sha(String(handler)),page_open_external_sha256:sha(String(openExternal)),context_set_data_sha256:sha(String(setData)),applicant_definition_sha256:sha(JSON.stringify(definition)),verification_definition_sha256:sha(JSON.stringify(definition))};
  const bill={'t.serial_no':'123','t.total_amt':'10.00','t.node_id':'508','t.wf_name':'YBX_LC','t.abst':'Purpose','t.uni_key':'789'};
  const row={'t.node_id':'508','t.node_name':'预约流程-申请人','t.appinfo':'TEST001-Test','t.operator':''};
  if(mode==='amount')bill['t.total_amt']='11.00';if(mode==='other_node')bill['t.node_id']='509';if(mode==='other_owner')row['t.appinfo']='OTHER-Test';
  if(mode==='drift')contract.applicant_definition_sha256='changed';
  if(mode==='callback_drift')contract.verification_callback_sha256='changed';
  if(mode.startsWith('existing_sms')){elements['formWF_YBX_1362_t-code']=code;values['61-SERIALNO']=mode==='existing_sms'?'123':'other';values['61-NODE_ID']='508';}
  const same=mode.startsWith('applicant_');
  if(same){values['61-SERIALNO']='123';values['61-NODE_ID']='508';elements['formWF_YBX_109_t-remark']=element();}
  const $={active:0,UC:{bindData:bind,setData},page:{openExternal,currPage:same?'76':mode==='clean_home'?'6':mode==='other_editor_page'?'198':'70'}};
  const controls=same?[{...element(),id:'formWF_YBX_109_t-remark',type:'text',value:mode==='applicant_edited'?'自定义未保存意见':'同意'}]:mode==='editor'?[{...element(),id:'unsaved',type:'text'}]:mode==='pager'?[{...element(),id:'',type:'text',closest:s=>s==='form'?{id:''}:s==='.ui-pg-table'?{}:null}]:[];
  const context={crypto:crypto.webcrypto,TextEncoder,Date,setTimeout,window:{jQuery:$,before_sp_yzm:handler,before_lc_agree:handler,yzm_jy_1362:handler},document:{getElementById:id=>elements[id],querySelectorAll:selector=>selector==='input,select,textarea'?controls:[]},
    createReimbursementHttp:()=>({guard(){},async post(url){posts.push(url);return url.startsWith('loadDefinition')?definition:{status:mode==='verified'?'yyz':mode==='unknown'?'unknown':'wyz'};}}),
    ReimbursementNativeForm:{canonical:v=>v},ReimbursementHttpValues:{moneyCents:v=>Math.round(Number(v)*100)},
    reimbursementBusinessApi:async()=>({complete:mode!=='incomplete',bill,sections:[{funcno:'1816',complete:true,rows:[row]}]}),
    c:{authorized:mode!=='unauthorized',approval_opinion:'同意',contract,case_id:'fixture',serial:'123',expected_amount:'10.00',summary:'Purpose',expected_userid:'TEST001'}};
  vm.createContext(context);
  try {return{result:await vm.runInContext(source+'\nreimbursementApplicantVerification(c)',context),opened,posts};}
  catch(e){assert.equal(opened.length,0,'rejected inputs must not open any interface');throw e;}
}
(async()=>{
  const a=await run();assert.equal(a.result.state,'verification_required');assert.deepEqual(a.opened,['76','1160']);assert.equal(a.result.code_read,false);assert.equal(a.result.code_requested,false);assert.equal(a.result.approval_dispatched,false);
  for(const mode of ['verified','existing_sms'])assert.equal((await run(mode)).opened.length,0);
  assert.deepEqual((await run('pager')).opened,['76','1160']);
  assert.deepEqual((await run('clean_home')).opened,['76','1160']);
  assert.deepEqual((await run('applicant_same')).opened,['1160']);
  for(const mode of ['amount','other_node','other_owner','drift','callback_drift','existing_sms_other','editor','applicant_edited','other_editor_page','unknown','unauthorized','incomplete'])await assert.rejects(()=>run(mode));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|activation_(?:updateUser|huoQuCode)|code\.value/.test(source));
  console.log('Applicant SMS handoff: original scope, native drift, existing work preservation and no code/send/approval passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
