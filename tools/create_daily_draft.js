(m=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务系统不符');
 if(m.serial||m.draft_creation_requested)throw Error('案件已有单号或创建请求，先回读，不能重复创建');
 const cents=s=>{s=String(s).replaceAll(',','');if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw Error('金额格式错误');const[a,b='']=s.split('.');return Number(a)*100+Number(b.padEnd(2,'0'));};
 const expected=m.selected_invoices;
 if(!expected?.length||expected.length!==m.expected_count||new Set(expected.map(x=>x.guid)).size!==expected.length)throw Error('已核对的票据选择参数不完整');
 if(expected.reduce((n,x)=>n+cents(x.amount),0)!==cents(m.expected_amount))throw Error('选择合计不符');
 const grid=$('#gridWF_YBX_53');if(!grid[0]?.grid||!grid[0].getClientRects().length)throw Error('日常发票池未打开');
 const rows=grid.jqGrid('getDataIDs').map(id=>({id,r:grid.jqGrid('getRowData',id)}));
 const targets=expected.map(e=>{
  const matches=rows.filter(x=>x.r['t.tk_guid']===e.guid);if(matches.length!==1)throw Error('票据GUID不唯一');const x=matches[0];
  const numbers=x.r['t.tk_code_abst'].match(/(?<!\d)(?:\d{20}|\d{8})(?!\d)/g)||[];
  const medical=e.type==='MEDICAL'&&/^\d{8}$/.test(e.invoice_code||'')&&/^\d{10}$/.test(e.number||'')&&x.r['t.tk_code']===e.invoice_code&&x.r['t.tk_code_abst']==='医/'+e.invoice_code+'/'+e.number;
  const identity=e.type==='MEDICAL'?medical:e.number_exposed!==false?numbers.includes(e.number):e.binding_verified===true&&x.r['t.tk_code_abst']===e.native_code_abst&&x.r['t.tk_display_abst']===e.native_display_abst&&x.r['t.tk_buysno']===e.native_person_id;
  if(x.r['t.serial_no']||x.r['t.tk_mod_flag']!=='NORMAL'||x.r['t.tk_type']!==e.type||!identity||cents(x.r['t.tk_total_amt'])!==cents(e.amount))throw Error('票号/类别/金额/认证/占用状态变化');return x;
 });
 grid.jqGrid('resetSelection');targets.forEach(x=>grid.jqGrid('setSelection',x.id,true));
 if(grid.jqGrid('getGridParam','selarrrow').length!==expected.length)throw Error('原生选择未完成');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_39 button[btnname="前往报销"]')].filter(e=>e.getClientRects().length);
 if(buttons.length!==1)throw Error('前往报销入口不唯一');buttons[0].click();
 return{ok:true,action:'create_daily_draft_requested',case_id:m.case_id,count:expected.length,total:m.expected_amount,requiresIndependentReadback:true,finalApproval:false};
})
