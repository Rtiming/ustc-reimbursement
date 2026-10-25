/* 可在Edge/Chrome MAIN world执行的控件工具。无坐标、无认证票据搬运。 */
(function(root){
 'use strict';
 const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 function cents(value){
   const s=String(value).replaceAll(',','').trim();
   if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw new Error('金额格式不合法');
   const [a,b='']=s.split('.'),n=Number(a)*100+Number(b.padEnd(2,'0'));
   if(!Number.isSafeInteger(n))throw new Error('金额超出精确整数范围');
   return n;
 }
 async function waitFor(test,message,timeout=5000){
   const end=Date.now()+timeout;
   while(!test()){if(Date.now()>end)throw new Error(message);await delay(50);}
 }
 function one(elements,message){if(elements.length!==1)throw new Error(message);return elements[0];}
 function button(scope,caption){
   if(['同意','撤销报销单','撤回','删除报销单'].includes(caption))throw new Error('该动作需要专用状态和授权校验');
   const nodes=[...scope.querySelectorAll('button')].filter(e=>e.getClientRects().length&&(e.getAttribute('btnname')===caption||e.textContent.trim()===caption));
   return one(nodes,'按钮不唯一：'+caption);
 }
 function field(funcno,label,index){
   const form=root.$.F.list[String(funcno)];if(!form)throw new Error('表单未加载');
   const def=one((form.formFields||[]).filter(f=>f.label===label),'字段标签不唯一：'+label);
   const id=root.$.F.idFunc.getFormID(funcno)+'_'+def.id+(index===undefined?'':'_'+index);
   const element=root.document.getElementById(id);if(!element)throw new Error('字段不存在：'+id);
   return {id,element,definition:def};
 }
 async function selectXm(id,employee){
   const $=root.$,control=$('#'+id).data('xmSelect');
   if(!control)throw new Error('不是已初始化的xmSelect');
   const candidate=one((control.options.data||[]).filter(x=>x.value===employee),'人员候选不唯一');
   $.F.setInputData(id,'xmselect',employee,'');
   await waitFor(()=>control.getValue().map(x=>x.value).join(',')===employee && $.F.getInputData(id,'xmselect')===employee,'显示或sdata未回写');
   $('#'+id).find('xm-select').trigger('change');
   return {value:employee,label:candidate.name};
 }
 async function selectAutocomplete(input,candidateText){
   const $=root.$;
   const li=one([...root.document.querySelectorAll('.ac_results li')].filter(e=>e.getClientRects().length&&e.textContent.trim()===candidateText),'原生候选不唯一或已消失');
   $(li).trigger('mouseover').trigger('mousedown');
   // 此站点的blur钩子依赖.ac_over；必须先blur再click。
   input.blur();$(li).trigger('mouseup').trigger('click');
   await delay(50);
   return {typedValue:input.value,candidateText};
 }
 function requireCase(serial,amount,expectedNode){
   const $=root.$;
   if($.getProjID()!=='WF_YBX'||$.UC.bindData('#61-serialno#')!==serial)throw new Error('单据身份变化');
   if(expectedNode!==undefined && $.UC.bindData('#61-node_id#')!==expectedNode)throw new Error('流程状态变化');
   return {serial,expectedCents:cents(amount)};
 }
 const api={cents,waitFor,one,button,field,selectXm,selectAutocomplete,requireCase};
 root.ReimbursementDOM=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
