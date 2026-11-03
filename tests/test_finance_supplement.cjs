const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),{webcrypto,createHash}=require('crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_supplement_api.js'].map(n=>fs.readFileSync('tools/'+n,'utf8')).join('\n');
async function fixture(){
 const bytes=new TextEncoder().encode('synthetic receipt'),sha=createHash('sha256').update(bytes).digest('hex'),guid='a'.repeat(32),filecode='b'.repeat(32),calls=[];
 const entry={id:'R1',evidence_kind:'other_receipt',amount_basis_confirmed:true,description:'Synthetic receipt',amount:'12.34',source_files:[{name:'receipt.pdf',size:bytes.length,sha256:sha,path:'/example/receipt.pdf'}]};
 const user={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic'},before=JSON.stringify(user);
 const $={getProjID:()=> 'WF_YBX',UC:{userData:user,bindData:()=> 'TEST001'},ajax:o=>{calls.push(o);assert(o.data.cmd.includes('12.34'));return{done(fn){fn('pass');return this;},fail(){return this;}}}};
 const native={form:{funcno:'752',typeMap:{},formFields:[{fieldname:'t.mattachfile',needTransPic:'true',includeMD5Code:'true',includebase64code:'false'}]},save:{check_func:'',procs:['`$'+'c'.repeat(32)+':#752-t.mxtype#;#752-t.mtkitemprice#;#752-t.mtkitemnum#;#752-t.mattachfile#;#0-tk_type#;#0-tk_mod#;#0-tk_guid#;#0-userinfo.username#;#0-userinfo.userid#']}};
 const context={performance,crypto:webcrypto,TextEncoder,Uint8Array,atob,FormData,File,URL,URLSearchParams,AbortSignal,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp',href:'https://cwzh.ustc.edu.cn/YBX/main.jsp'},
  ReimbursementNativeForm:{definition:async()=>native},fetch:async url=>({ok:true,text:async()=>filecode,arrayBuffer:async()=>bytes.buffer}),
  reimbursementBusinessApi:async()=>({ok:true,complete:true,rows:[{'t.tk_guid':guid,'t.serial_no':'','t.tk_type':'MULTITK','t.tk_total_amt':'12.34'}],requests:[]})};
 vm.createContext(context);vm.runInContext(source,context);
 const c={case_id:'fixture',expected_userid:'TEST001',entry,token:'d'.repeat(64),contracts:{definitions:{'551':'hash'}},execute:true,pending_registered:true};
 const prepared={case_id:c.case_id,entry,guid,defaults:{tk_type:'MULTITK',tk_mod:'MULTITKITEM',tk_guid:guid}};
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,pending_registered:false,step:'start',prepared}));
 await context.reimbursementNonvatWrite({...c,step:'start',prepared});
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,step:'start',prepared}));
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,step:'save'}));
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,step:'append',index:0,offset:0,base64:Buffer.from(bytes).toString('base64'),chunk_sha256:'e'.repeat(64)}));
 await context.reimbursementNonvatWrite({...c,step:'append',index:0,offset:0,base64:Buffer.from(bytes).toString('base64'),chunk_sha256:sha});
 await context.reimbursementNonvatWrite({...c,step:'upload',index:0});
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,step:'upload',index:0}));
 const saved=await context.reimbursementNonvatWrite({...c,step:'save'});assert.equal(saved.ok,true);assert.equal(calls.length,1);
 await assert.rejects(()=>context.reimbursementNonvatWrite({...c,step:'save'}));
 const read=await context.reimbursementNonvatReadback({...c,original_save:saved});assert.equal(read.source_readbacks[0].verified,true);
 await assert.rejects(()=>context.reimbursementNonvatReadback({...c,entry:{...entry,amount:'12.35'},original_save:saved}));
 assert.equal(JSON.stringify(user),before);
}
fixture().then(()=>console.log('nonvat: source SHA, native category, single upload/save, independent bytes/GUID readback, no global UC mutation passed')).catch(e=>{console.error(e);process.exitCode=1;});
