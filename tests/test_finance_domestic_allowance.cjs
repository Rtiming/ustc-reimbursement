const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../tools/finance_domestic_allowance_api.js'),'utf8');
async function fixture(){
 let writes=0,fail=false;
 const box={window:{},ReimbursementNativeForm:{canonical:x=>x},ReimbursementNativeProtocol:{command:()=>'{fixed}'},ReimbursementHttpValues:{moneyCents:x=>BigInt(String(x).replace('.',''))}};
 vm.createContext(box);vm.runInContext(code,box);
 const prepared={ok:true,bill:{serial:'1'},before_allowances:[],used_rows:[],person:{SNO:'S1'},trips:[{STARTDATE:'2026-07-03'}],originalTrip:{},originalPerson:{},definition_sha256:'sha'};
 box.built={report:prepared,native:{form:{typeMap:{}},value:{funcs:[{funcno:'493',typeMap:{}}]},save:{check_func:'',procs:['fixed']}},context:{},http:{post:async()=>{writes++;if(fail)throw Error('lost');return'pass';}}};
 vm.runInContext('reimbursementDomesticTripBuild=async()=>built',box);
 const c={case_id:'case',serial:'1',expected_userid:'U1',token:'intent',execute:true,pending_registered:true,person:{id:'S1'},prepared};
 await assert.rejects(()=>box.reimbursementDomesticTripSave({...c,execute:false}),/PENDING/);assert.equal(writes,0);
 await assert.rejects(()=>box.reimbursementDomesticTripSave({...c,prepared:{...prepared,trips:[]}}),/CHANGED/);assert.equal(writes,0);
 let r=await box.reimbursementDomesticTripSave(c);assert.equal(r.ok,true);assert.equal(writes,1);
 await assert.rejects(()=>box.reimbursementDomesticTripSave(c),/ORIGINAL/);assert.equal(writes,1);
 fail=true;r=await box.reimbursementDomesticTripSave({...c,token:'unknown'});assert.equal(r.ok,false);assert.equal(r.save_dispatched,true);assert.equal(r.state,'save_result_unknown');
 await assert.rejects(()=>box.reimbursementDomesticTripSave({...c,token:'unknown'}),/ORIGINAL/);assert.equal(writes,2);
 const rows=[{'t.sno':'S1','t.sname':'甲','t.startdate':'2026-07-03 00:00:00','t.staydays':'9'}],entries=[{person_id:'S1',name:'甲',date:'2026-07-03'},{person_id:'S1',name:'甲',date:'2026-07-11'}];
 assert.equal(box.reimbursementDomesticAllowanceAssignments(rows,entries)[0].length,2);
 assert.throws(()=>box.reimbursementDomesticAllowanceAssignments([...rows,...rows],entries),/NOT_UNIQUE/);
 assert.throws(()=>box.reimbursementDomesticAllowanceAssignments(rows,[{...entries[0],date:'2026-07-12'}]),/NOT_UNIQUE/);
 const edit={...prepared,rows:rows,row_index:0,original:{HSBT_AMT:'900'},target:{HSBT_AMT:'200'},entries,expected_after:'13226.57',init_raw:'pass'};
 box.built.report=edit;vm.runInContext('reimbursementDomesticAllowanceEditBuild=async()=>built',box);fail=false;
 const ec={...c,token:'edit',prepared:edit,row_index:0};
 await assert.rejects(()=>box.reimbursementDomesticAllowanceEditSave({...ec,prepared:{...edit,target:{HSBT_AMT:'900'}}}),/CHANGED/);assert.equal(writes,2);
 r=await box.reimbursementDomesticAllowanceEditSave(ec);assert.equal(r.ok,true);assert.equal(writes,3);
 await assert.rejects(()=>box.reimbursementDomesticAllowanceEditSave(ec),/ORIGINAL/);assert.equal(writes,3);
}
fixture().then(()=>console.log('domestic allowance: source drift, explicit pending, original-only and unknown-result guards passed'));
