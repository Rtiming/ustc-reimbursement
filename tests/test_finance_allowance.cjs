const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
const code=['finance_http.js','finance_native_protocol.js','finance_business_api.js','finance_allowance_api.js'].map(n=>fs.readFileSync('tools/'+n,'utf8')).join('\n');
async function main(){
 const user={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic','0-WPCL_SHOW_MEMBERS':'N','0-WPCLTSGZ':'N'},before=JSON.stringify(user),calls=[];
 const values={members:'',ylry:'0',elry:'0',slry:'1',cllx:'1',jtgj:'其他',cfxc_s:'合肥市',cfxc_e:'上海市',clbz:'',is_wcsy:'N',sdate:'2026-07-03',edate:'2026-07-03',jtfy:'0',hsbt:'100',jtbt:'80',feeamount:'180.00',btts:'1'};
 const native={hash:'hash',form:{funcno:'1076',formFields:Object.keys(values).map(n=>({fieldname:'t.'+n}))},save:{check_func:'`$'+'a'.repeat(32)+':#1076-t.slry#',procs:['`$'+'b'.repeat(32)+':#1076-t.hsbt#;#1076-t.jtbt#;#61-BX_JBXX_BX_TYPE#;#0-userinfo.userid#']}};
 let rows=[{YLRY:'0',ELRY:'0',SLRY:'1',CLLX:'1',CFXC_S:'合肥市   ',CFXC_E:'上海市   ',CLBZ:'',SDATE:'2026-07-03',EDATE:'2026-07-03',HSBT:'100',JTBT:'80',JTFY:'0',FEEAMOUNT:'180',IS_WCSY:'N',JTGJ:'其他',BTTS:'0'}];
 const $={getProjID:()=> 'WF_YBX',UC:{userData:user,bindData:()=> 'TEST001'},ajax:o=>{calls.push(o);return{done(fn){fn(o.url.includes('common_bindSQLDataBackend')?JSON.stringify(rows):'passlocal(0-g0Serialno:12345)');return this;},fail(){return this;}}}};
 const ctx={performance,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},ReimbursementNativeForm:{definition:async()=>native}};vm.createContext(ctx);vm.runInContext(code,ctx);
 const prepared={complete:true,case_id:'fixture',expected_userid:'TEST001',policy:{'0-WPCL_SHOW_MEMBERS':'N','0-WPCLTSGZ':'N'},groups:[{values}]};
 const c={case_id:'fixture',expected_userid:'TEST001',expected_amount:'180.00',contracts:{definitions:{867:'hash'}},prepared,execute:true,pending_registered:true,token:'c'.repeat(64),creation_prepare:{ok:true,groups:prepared.groups,definition_sha256:'hash',before_serials:['999']}};
 await assert.rejects(()=>ctx.reimbursementAllowanceCreate({...c,pending_registered:false}));
 await assert.rejects(()=>ctx.reimbursementAllowanceCreate({...c,expected_amount:'360.00'}));assert.equal(calls.length,0);
 const r=await ctx.reimbursementAllowanceCreate(c);assert.equal(r.ok,true);assert.equal(r.serial,'12345');assert.equal(calls.length,1);
 await assert.rejects(()=>ctx.reimbursementAllowanceCreate(c));assert.equal(calls.length,1);assert.equal(JSON.stringify(user),before);
 ctx.reimbursementBusinessApi=async()=>({complete:true,bill:{'t.status':'-99','t.lc_finish':'F'},requests:[]});
 const read=await ctx.reimbursementAllowanceReadback({...c,serial:'12345'});assert.equal(read.verified_groups,1);assert.equal(read.rows[0].BTTS,'0');
 rows=[{...rows[0],JTBT:'81'}];await assert.rejects(()=>ctx.reimbursementAllowanceReadback({...c,serial:'12345'}));
 user['0-WPCL_SHOW_MEMBERS']='Y';await assert.rejects(()=>ctx.reimbursementAllowanceBuild(c));
 console.log('allowance: source totals, native counts policy, single create, duplicate refusal, no global context mutation passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
