// Run from a private runtime directory containing the bundled node_modules link.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const require=createRequire(path.join(process.cwd(),'package.json'));
const {Workbook,SpreadsheetFile,FileBlob}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const [input,planFile,out]=process.argv.slice(2);
if(!input||!planFile||!out)throw Error('Usage: input.json plan.json NEW_OUTPUT_DIR');
const data=JSON.parse(await fs.readFile(input,'utf8'));
const plan=JSON.parse(await fs.readFile(planFile,'utf8'));
if(!process.env.ARTIFACT_PYTHON)throw Error('Set ARTIFACT_PYTHON to the loaded workspace Python');
await fs.mkdir(out,{recursive:false,mode:0o700});
const wb=Workbook.create(),summary=wb.worksheets.add('问题与汇总');
const services=plan.account_list?wb.worksheets.add('本批服务清单'):null;
const groups=[...new Set(data.matches.filter(x=>!x.already_reimbursed).map(x=>x.group))];
const sheets=new Map(groups.map(g=>[g,wb.worksheets.add(g)]));
const refs={},counts={},ranges={},expected=[];
const money=n=>(n/100).toFixed(2);
const clock=s=>{
  if(!s)return null;
  const a=s.split(/[- :T]/).map(Number);
  return Date.UTC(a[0],a[1]-1,a[2],a[3]||0,a[4]||0,a[5]||0)/86400000+25569;
};
function format(s,range){
  s.showGridLines=false;
  s.getRange(range).format={font:{name:'Arial',size:11,color:'#1F2937'},rowHeight:25,verticalAlignment:'center'};
  s.getRange('A2').format.font={name:'Arial',size:15,bold:true};
}
function header(s,range){s.getRange(range).format={fill:'#334155',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:30,horizontalAlignment:'center'};}
for(const group of groups){
  const s=sheets.get(group),rows=data.matches.filter(x=>x.group===group&&!x.already_reimbursed).sort((a,b)=>(a.payment?.paid_at||a.source_time).localeCompare(b.payment?.paid_at||b.source_time));
  const end=rows.length+5,total=end+1;
  const sourceTitle=rows[0].time_kind==='platform_created_at'?'平台创建时间（时区未知）':rows[0].time_kind==='vendor_status_at'?'网站已支付状态时间':'订单付款时间';
  format(s,`A1:M${total+2}`);s.getRange('A2').values=[[group]];
  const accountList=Boolean(plan.account_list),hasName=rows.some(x=>x.item_name),nameColumn=accountList?'A':'M',hasExtra=accountList||hasName;
  const headers=[accountList?'费用名称':'完整订单号','金额（元）',sourceTitle,'支付宝支付时间','支付宝交易号','付款凭证状态','付款方式','付款人','发票及分组','截图位置','日期核对说明','报销归属'];
  if(hasExtra)headers.push(accountList?'完整订单号':'费用名称');
  const last=hasExtra?'M':'L';s.getRange(`A5:${last}5`).values=[headers];
  header(s,`A5:${last}5`);
  const values=rows.map(x=>{const r=[accountList?(x.item_name||x.group):x.order_ref,x.amount_cents/100,clock(x.source_time),clock(x.payment?.paid_at),x.payment?.transaction_ref||'',x.status,x.payment?.method||'付款渠道待凭证核实',x.payer||'姓名待核',x.invoice_status||'',x.payment?path.basename(path.dirname(x.payment.image)):'待补充',x.date_note,x.allocation_label||''];if(hasExtra)r.push(accountList?x.order_ref:(x.item_name||''));return r;});
  s.getRange(`A6:${last}${end}`).values=values;
  const table=s.tables.add(`A5:${last}${end}`,true,'Payments'+groups.indexOf(group));
  table.style='TableStyleLight1';
  header(s,`A5:${last}5`);
  s.getRange(`A6:A${end}`).setNumberFormat('@');s.getRange(`E6:E${end}`).setNumberFormat('@');
  s.getRange(`B6:B${total}`).setNumberFormat('#,##0.00');s.getRange(`C6:D${end}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
  s.getRange(`A${total}`).values=[[rows.some(x=>x.include_in_current===false)?'登记合计（含下批）':'合计']];s.getRange(`B${total}`).formulas=[[`=ROUND(SUM(B6:B${end}),2)`]];
  s.getRange(`A${total}:K${total}`).format.borders={top:{style:'thin',color:'#94A3B8'}};
  s.getRange(`A${total}:B${total}`).format.font.bold=true;
  s.getRange(`F6:F${end}`).conditionalFormats.add('containsText',{text:'待补',format:{fill:'#FEF3C7',font:{color:'#92400E'}}});
  if(hasName)s.getRange(`${nameColumn}6:${nameColumn}${end}`).conditionalFormats.add('containsText',{text:'待确认',format:{fill:'#FEF3C7',font:{color:'#92400E'}}});
  for(const [column,width] of Object.entries({A:42,B:14,C:31,D:25,E:42,F:16,G:32,H:19,I:50,J:37,K:84,L:37}))s.getRange(`${column}5:${column}${total}`).format.columnWidth=width;
  s.freezePanes.freezeRows(5);s.freezePanes.freezeColumns(1);
  if(hasExtra)s.getRange(`M5:M${total}`).format.columnWidth=42;
  const invoice=plan.cost_basis?.[group];
  if(invoice?.kind==='invoice'){
    s.getRange('M3').values=[['本次发票金额（元）']];s.getRange('M4').values=[[invoice.amount_cents/100]];s.getRange('M4').setNumberFormat('#,##0.00');
    s.getRange('M6').values=[['充值合计不重复加到报销金额']];s.getRange('M3:M6').format.columnWidth=36;
    refs[group]=`'${group}'!M4`;
  }else refs[group]=`'${group}'!B${total}`;
  counts[group]=`COUNTIFS('${group}'!F6:F${end},"已匹配")`;
  ranges[group]={amount:`'${group}'!B6:B${end}`,status:`'${group}'!F6:F${end}`,allocation:`'${group}'!L6:L${end}`,names:`'${group}'!${nameColumn}6:${nameColumn}${end}`};
}
if(services){
  const current=data.matches.filter(x=>!x.already_reimbursed&&x.include_in_current!==false);
  const names=[...new Set(current.map(x=>x.item_name||x.group))];
  format(services,`A1:D${names.length+9}`);services.getRange('A2').values=[[plan.title]];
  services.getRange('A3').values=[['仅列本批；每笔按1项登记，服务期限未推定。']];
  services.getRange('A5:D5').values=[['费用名称','数量（笔）','金额（元）','名称依据']];header(services,'A5:D5');
  for(const [i,name] of names.entries()){
    const subset=current.filter(x=>(x.item_name||x.group)===name),parts=[];const counts=[];
    for(const group of [...new Set(subset.map(x=>x.group))])for(const label of [...new Set(subset.filter(x=>x.group===group).map(x=>x.allocation_label||''))]){
      const quote=x=>'"'+x.replaceAll('"','""')+'"';
      parts.push(`SUMIFS(${ranges[group].amount},${ranges[group].names},${quote(name)},${ranges[group].allocation},${quote(label)})`);
      counts.push(`COUNTIFS(${ranges[group].names},${quote(name)},${ranges[group].allocation},${quote(label)})`);
    }
    const row=i+6;services.getRange(`A${row}:D${row}`).values=[[name,null,null,subset.some(x=>x.item_name_confirmed===false)?'名称待确认，金额据付款记录保留':'用户指定名称']];
    services.getRange(`B${row}:C${row}`).formulas=[['='+counts.join('+'),'=ROUND('+parts.join('+')+',2)']];
  }
  const total=names.length+6;services.getRange(`A${total}`).values=[['本批合计']];services.getRange(`C${total}`).formulas=[[`=ROUND(SUM(C6:C${total-1}),2)`]];
  services.getRange(`C6:C${total}`).setNumberFormat('#,##0.00');services.getRange(`B6:B${total}`).setNumberFormat('0');
  for(const [c,w] of Object.entries({A:42,B:18,C:20,D:55}))services.getRange(`${c}5:${c}${total}`).format.columnWidth=w;
  services.getRange(`A6:A${total-1}`).conditionalFormats.add('containsText',{text:'待确认',format:{fill:'#FEF3C7',font:{color:'#92400E'}}});
  services.getRange(`A${total}:D${total}`).format.borders={top:{style:'thin',color:'#94A3B8'}};
}
const n=plan.summary.length,end=n+5,total=end+1;
format(summary,'A1:F23');summary.getRange('A2').values=[[plan.title]];
summary.getRange('A3').values=[['材料金额为拟报销范围，正式申请和提交后续办理。']];
summary.getRange('A5:E5').values=[['办理分组','拟报销材料金额（元）','已存付款图','核对笔数','待补及后续安排']];header(summary,'A5:E5');
const selectedRows=row=>data.matches.filter(x=>!x.already_reimbursed&&(row.key?x.allocation_key===row.key:row.groups.includes(x.group)));
plan.summary.forEach((row,i)=>{
  const r=i+6;const groupRows=selectedRows(row),rowGroups=[...new Set(groupRows.map(x=>x.group))];
  const quoted='"'+row.label.replaceAll('"','""')+'"';
  const amountRefs=rowGroups.map(g=>row.key&&plan.cost_basis?.[g]?.kind!=='invoice'?`SUMIFS(${ranges[g].amount},${ranges[g].allocation},${quoted})`:refs[g]);
  const countRefs=rowGroups.map(g=>row.key?`COUNTIFS(${ranges[g].status},"已匹配",${ranges[g].allocation},${quoted})`:counts[g]);
  summary.getRange(`A${r}:E${r}`).values=[[row.label,null,null,groupRows.length,row.note]];
  summary.getRange(`B${r}`).formulas=[['=ROUND('+amountRefs.join('+')+',2)']];
  summary.getRange(`C${r}`).formulas=[['='+countRefs.join('+')]];
  const amount=rowGroups.reduce((sum,g)=>sum+(plan.cost_basis?.[g]?.amount_cents??groupRows.filter(x=>x.group===g).reduce((s,x)=>s+x.amount_cents,0)),0);
  if(row.expected_amount_cents!==undefined&&amount!==row.expected_amount_cents)throw Error('分组金额与已确认清单不一致');
  expected.push(amount/100);
});
const currentRows=plan.summary.map((r,i)=>r.include_in_current===false?null:i+6).filter(x=>x!==null);
const currentAmount=expected.reduce((sum,value,i)=>sum+(plan.summary[i].include_in_current===false?0:Math.round(value*100)),0)/100;
const sumCurrent=column=>currentRows.map(r=>`${column}${r}`).join('+')||'0';
summary.getRange(`A${total}`).values=[[plan.summary_total_label||'本次拟报合计']];summary.getRange(`B${total}:D${total}`).formulas=[[`=ROUND(${sumCurrent('B')},2)`,`=${sumCurrent('C')}`,`=${sumCurrent('D')}`]];
summary.getRange(`B6:B${total}`).setNumberFormat('#,##0.00');summary.getRange(`C6:D${total}`).setNumberFormat('0');
summary.getRange(`A${total}:E${total}`).format.borders={top:{style:'thin',color:'#94A3B8'}};
const issueStart=Math.max(11,total+2),exportStart=Math.max(17,issueStart+plan.issues.length+3),previewEnd=exportStart+plan.export_notes.length;
summary.getRange(`A${issueStart}`).values=[['待补事项']];plan.issues.forEach((t,i)=>summary.getRange(`A${issueStart+1+i}`).values=[[t]]);
summary.getRange(`A${exportStart}`).values=[['说明']];plan.export_notes.forEach((t,i)=>summary.getRange(`A${exportStart+1+i}`).values=[[t]]);
for(const [c,w] of Object.entries({A:35,B:27,C:16,D:14,E:87}))summary.getRange(`${c}5:${c}${total}`).format.columnWidth=w;
summary.getRange('A2:E2').format.rowHeight=29;summary.getRange(`A${issueStart}:E${previewEnd}`).format.rowHeight=27;
wb.recalculate();
if(services){const last=services.getUsedRange().values.length+1;const expectedService=data.matches.filter(x=>!x.already_reimbursed&&x.include_in_current!==false).reduce((sum,x)=>sum+x.amount_cents,0);const actualService=services.getRange('C'+(6+[...new Set(data.matches.filter(x=>!x.already_reimbursed&&x.include_in_current!==false).map(x=>x.item_name||x.group))].length)).values[0][0];if(Math.round(Number(actualService)*100)!==expectedService)throw Error('本批服务清单与逐笔金额不一致');}
const actual=summary.getRange(`B6:B${end}`).values.flat();
if(actual.some((x,i)=>Math.round(Number(x)*100)!==Math.round(expected[i]*100)))throw Error('费用分组计算不一致');
await fs.writeFile(path.join(out,'workbook-validation.json'),JSON.stringify({expected,actual,current_amount:currentAmount,summary:summary.getRange(`A5:E${total}`).values},null,2));
await fs.writeFile(path.join(out,'formula-scan.ndjson'),(await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:50},maxChars:3000})).ndjson);
const filename=plan.filename||'AI报销汇总与付款对应.xlsx';
await fs.mkdir(path.join(out,'qa'));
const raw=path.join(out,'qa','artifact-export.xlsx'),finalPath=path.join(out,filename);
await (await SpreadsheetFile.exportXlsx(wb)).save(raw);
execFileSync(process.env.ARTIFACT_PYTHON,[path.join(path.dirname(fileURLToPath(import.meta.url)),'normalize_identifier_text.py'),'--input',raw,'--output',finalPath,'--receipt',path.join(out,'qa','identifier-text.json')],{stdio:'pipe'});
const delivered=await SpreadsheetFile.importXlsx(await FileBlob.load(finalPath));
delivered.recalculate();
for(const [name,s] of [['问题与汇总',summary],...(services?[['本批服务清单',services]]:[]),...sheets.entries()]){
  const preview=await delivered.render({sheetName:name,range:name==='问题与汇总'?`A1:E${previewEnd}`:name==='本批服务清单'?`A1:D${s.getUsedRange().values.length+1}`:`A1:F${Math.min(s.getUsedRange().values.length,12)}`,scale:1,format:'png'});
  await fs.writeFile(path.join(out,name+'-preview.png'),new Uint8Array(await preview.arrayBuffer()));
}
const escape=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const href=p=>encodeURI(path.relative(out,p).split(path.sep).join('/'));
let html='<!doctype html><meta charset="utf-8"><title>'+escape(plan.title)+'</title><style>body{font:16px system-ui;margin:32px;line-height:1.6;max-width:1600px}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}th{background:#eef2f6}h1{font-size:25px}small{color:#555}</style><h1>'+escape(plan.title)+'</h1><table><tr><th>分组</th><th>材料金额</th><th>付款图</th><th>后续安排</th></tr>';
plan.summary.forEach((r,i)=>{const rows=selectedRows(r);html+=`<tr><td>${escape(r.label)}</td><td>${expected[i].toFixed(2)}元</td><td>${rows.filter(x=>x.status==='已匹配').length}/${rows.length}</td><td>${escape(r.note)}</td></tr>`;});
html+='</table><p>本次拟报合计'+currentAmount.toFixed(2)+'元。下一批单列、不计入本次。尚未提交财务。</p><h2>待补事项</h2><ul>'+plan.issues.map(x=>'<li>'+escape(x)+'</li>').join('')+'</ul><h2>说明</h2><ul>'+plan.export_notes.map(x=>'<li>'+escape(x)+'</li>').join('')+'</ul>';
for(const group of groups){
  html+='<h2>'+escape(group)+'</h2><table><tr><th>费用名称</th><th>订单号</th><th>金额</th><th>原来源时间</th><th>支付宝支付时间</th><th>付款截图</th><th>报销归属</th><th>备注</th></tr>';
  for(const r of data.matches.filter(x=>x.group===group&&!x.already_reimbursed).sort((a,b)=>a.source_time.localeCompare(b.source_time)))html+='<tr><td>'+escape(r.item_name||r.group)+'</td><td>'+escape(r.order_ref)+'</td><td>'+money(r.amount_cents)+'</td><td>'+escape(r.source_time)+'</td><td>'+escape(r.payment?.paid_at||'待补充')+'</td><td>'+(r.payment?'<a href="'+href(r.payment.image)+'">查看付款详情</a>':'聂支付，待补充')+'</td><td>'+escape(r.allocation_label||'')+'</td><td>'+escape(r.date_note)+'</td></tr>';
  html+='</table>';
}
html+='<p><small>来源：本次原始订单截图及手机付款详情。平台创建时间时区未知；网站已支付状态时间不覆盖实际支付时间。已报销的9月14日1299元排除，充值额不重复计为服务报销额。</small></p>';
await fs.writeFile(path.join(out,'汇总与付款截图.html'),html);
const brief='# '+plan.title+'\n\n'+plan.summary.map((r,i)=>'- '+r.label+'：'+expected[i].toFixed(2)+'元。'+r.note).join('\n')+'\n\n本次拟报合计'+currentAmount.toFixed(2)+'元，下一批不计入本次，尚未提交财务。\n\n'+plan.issues.map(x=>'- '+x).join('\n')+'\n\n'+plan.export_notes.map(x=>'- '+x).join('\n')+'\n';
await fs.writeFile(path.join(out,'问题总结.md'),brief);
await fs.writeFile(path.join(out,'问题总结.html'),'<!doctype html><meta charset="utf-8"><pre>'+escape(brief)+'</pre>');
console.log(JSON.stringify({output:out,amount:currentAmount,captured:data.matches.filter(x=>x.status==='已匹配').length}));
