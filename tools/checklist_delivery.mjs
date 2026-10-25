// Shared renderer for reviewed checklist snapshots. No finance/browser operations.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';

const dateText = value => /^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$/.test(value);

export function literal(value) {
  if (value === null || value === undefined || value === '') return null;
  let text = String(value);
  // Keep numeric identifiers explicitly literal in both the renderer and Excel.
  if (/^'\d{16,}$/.test(text)) return text;
  if (/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) return "'" + text;
  // SDK预览会将ISO时间转成日期序号；文本列仍须显示采集原文。
  if (dateText(text)) return "'" + text;
  return /^\s*[=+@]/.test(text) ? "'" + text : text;
}

export function cellValue(value, type) {
  if (value === null || value === undefined || value === '') return null;
  const raw = String(value).replace(/^'(?=-?\d)/, '');
  if (type !== 'text' && /^-?\d+(?:\.\d+)?$/.test(raw)) {
    if (type === 'money' && !/^-?\d+(?:\.\d{1,2})?$/.test(raw)) throw new Error('金额超过两位小数');
    const num = Number(raw);
    if (!Number.isFinite(num) || Math.abs(num) > 1e12) throw new Error('金额或数量超出支持范围');
    return num;
  }
  return literal(value);
}

export function exportedCellValue(value, type) {
  // The SDK preview formats numeric-looking text as a number. Its temporary
  // display escape must not become an extra character in the saved identifier.
  if (type === 'text' && value !== null && value !== undefined && value !== '') {
    const text=String(value);
    if (/^'\d{16,}$/.test(text)) return text.slice(1);
    if (/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) return text;
    if (dateText(text)) return text;
  }
  return cellValue(value,type);
}

function colName(index) {
  let out = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) out = String.fromCharCode(65 + (n - 1) % 26) + out;
  return out;
}

export async function renderChecklist(options) {
  const input = path.resolve(options.input), output = path.resolve(options.output);
  const plan = JSON.parse(await fs.readFile(input, 'utf8'));
  if (plan.schema !== 'ustc-checklist-delivery-v1' || !plan.case_id || plan.system_write !== false || !Array.isArray(plan.sheets) || !plan.sheets.length) throw new Error('清单交付计划不合法');
  if (path.extname(output) !== '.xlsx') throw new Error('输出须为.xlsx');
  await fs.access(output).then(() => { throw new Error('输出已存在，不覆盖'); }, e => { if (e.code !== 'ENOENT') throw e; });
  const names = new Set();
  for (const s of plan.sheets) {
    if (!s.name || s.name.length > 31 || /[\\/:*?\[\]]/.test(s.name) || names.has(s.name.toLowerCase())) throw new Error('工作表名称非法或重复');
    names.add(s.name.toLowerCase());
    const keys = s.columns.map(c => c.key);
    if (!keys.length || new Set(keys).size !== keys.length) throw new Error('表格列为空或重复');
    for (const c of s.columns) if (!['text','money','number'].includes(c.type)) throw new Error('字段类型不合法');
    for (const r of s.rows) if (Object.keys(r).length !== keys.length || keys.some(k => !(k in r))) throw new Error('行字段与表头不符');
  }
  if (!plan.sources?.length) throw new Error('缺来源SHA');
  for (const s of plan.sources) {
    if (!path.isAbsolute(s.path)) throw new Error('来源须为绝对路径');
    const bytes = await fs.readFile(s.path);
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== s.sha256) throw new Error('来源SHA变化，须先重建交付计划');
  }
  // Resolve the documented public package entry from an explicitly configured dependency root.
  const resolver = createRequire(path.join(options.moduleRoot ? path.dirname(path.resolve(options.moduleRoot)) : process.cwd(), '__checklist_runtime__.cjs'));
  const {Workbook, SpreadsheetFile} = await import(pathToFileURL(resolver.resolve('@oai/artifact-tool')).href);
  const workbook = Workbook.create();
  const detail = [];
  for (const [index, spec] of plan.sheets.entries()) {
    const sheet = workbook.worksheets.add(spec.name);
    const width = spec.columns.length, end = colName(width - 1), headerRow = 5;
    sheet.showGridLines = false;
    const note = spec.note || plan.note || '';
    sheet.getRange('A2').values = [[literal(spec.title || spec.name)]];
    sheet.getRange('A2').format.font = {name:'Arial', size:15, bold:true, color:'#203B55'};
    // Context is useful to the reviewer; avoid tall merged notes across the entire table.
    sheet.getRange('A3').values = [[literal(note)]];
    sheet.getRange('A3').format.font = {name:'Arial', size:10, color:'#596777'};
    sheet.getRange('A3').format.wrapText = false;
    if (spec.name === '问题总结') {
      sheet.mergeCells(`A3:${end}3`);
      const context=sheet.getRange(`A3:${end}3`);
      context.format.wrapText=true;
      context.format.rowHeight=72;
      context.format.verticalAlignment='center';
      context.format.fill='#EDF3F8';
    }
    const values = spec.rows.map(row => spec.columns.map(c => cellValue(row[c.key], c.type)));
    sheet.getRange(`A${headerRow}:${end}${headerRow}`).values = [spec.columns.map(c => c.label || c.key)];
    if (values.length) sheet.getRange(`A${headerRow+1}:${end}${headerRow+values.length}`).values = values;
    const used = sheet.getRange(`A${headerRow}:${end}${headerRow + Math.max(1,values.length)}`);
    used.format.font = {name:'Arial', size:11, color:'#202E3A'};
    used.format.wrapText = true;
    used.format.verticalAlignment = 'top';
    sheet.getRange(`A${headerRow}:${end}${headerRow}`).format = {fill:'#294A68', font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'}, wrapText:true, horizontalAlignment:'center', verticalAlignment:'center', rowHeight:48};
    for (let i=0;i<width;i++) {
      const c=spec.columns[i], letter=colName(i);
      sheet.getRange(`${letter}${headerRow}:${letter}${headerRow+Math.max(1,values.length)}`).format.columnWidth = Math.min(65,Math.max(18,c.width || 26));
      const data=sheet.getRange(`${letter}${headerRow+1}:${letter}${headerRow+Math.max(1,values.length)}`);
      data.setNumberFormat(c.type==='money' ? '#,##0.00' : c.type==='number' ? '#,##0' : '@');
      data.format.horizontalAlignment = c.type==='text' ? 'left' : 'right';
    }
    if (values.length) {
      const body=sheet.getRange(`A${headerRow+1}:${end}${headerRow+values.length}`);
      body.format.rowHeight=56;
      body.format.autofitRows();
      if (spec.name === '问题总结') {
        for (const [i,row] of spec.rows.entries()) {
          const lines=Math.max(...spec.columns.map(c=>{
            const units=[...String(row[c.key] ?? '')].reduce((n,ch)=>n+(ch.charCodeAt(0)>255?2:1),0);
            return Math.ceil(units/Math.max(14,(c.width || 26)*0.9));
          }));
          sheet.getRange(`A${headerRow+1+i}:${end}${headerRow+1+i}`).format.rowHeight=Math.max(72,lines*16+14);
        }
      }
      const table=sheet.tables.add(`A${headerRow}:${end}${headerRow+values.length}`, true, `Checklist${index+1}`);
      table.showFilterButton = true;
      body.conditionalFormats.add('containsText', {text:'暂缓',format:{fill:'#FCE5E5',font:{color:'#9C2B2B',bold:true}}});
      body.conditionalFormats.add('containsText', {text:'未定位',format:{fill:'#FFF0D6',font:{color:'#805316'}}});
      body.conditionalFormats.add('containsText', {text:'待核',format:{fill:'#FFF0D6',font:{color:'#805316'}}});
    } else {
      sheet.getRange(`A${headerRow+1}`).values = [['此表当前没有已登记明细；按上方定位说明继续核对。']];
    }
    sheet.freezePanes.freezeRows(headerRow);
    if (width>5) sheet.freezePanes.freezeColumns(1);
    detail.push({name:spec.name,rows:values.length,columns:width});
  }
  workbook.recalculate();
  const inspection = await workbook.inspect({kind:'sheet',include:'id,name',maxChars:3000});
  let qaDir;
  if (options.qaDir) {
    qaDir=path.resolve(options.qaDir);
    await fs.mkdir(qaDir,{recursive:false});
    for (const [index,s] of plan.sheets.entries()) {
      const end=colName(Math.min(s.columns.length,7)-1), last=Math.min(5+s.rows.length,10);
      const preview=await workbook.render({sheetName:s.name,range:`A1:${end}${Math.max(6,last)}`,scale:1.3,format:'png'});
      await fs.writeFile(path.join(qaDir,`${String(index+1).padStart(2,'0')}_${s.name}.png`),new Uint8Array(await preview.arrayBuffer()));
    }
    await fs.writeFile(path.join(qaDir,'表格检查.ndjson'),inspection.ndjson);
  }
  // Save the exact text identifiers, not the preview-only CSV/text escape.
  for (const spec of plan.sheets) {
    if (!spec.rows.length) continue;
    const sheet=workbook.worksheets.getItem(spec.name), end=colName(spec.columns.length-1);
    sheet.getRange(`A6:${end}${5+spec.rows.length}`).values=spec.rows.map(row=>spec.columns.map(c=>exportedCellValue(row[c.key],c.type)));
  }
  workbook.recalculate();
  const file=await SpreadsheetFile.exportXlsx(workbook);
  // Reserve the filename with exclusive creation before the SDK writes it.
  const lock=await fs.open(output,'wx');await lock.close();
  try {
    await file.save(output);
    if (plan.sheets.some(s=>s.rows.some(r=>s.columns.some(c=>c.type==='text' && dateText(String(r[c.key] ?? '')))))) {
      const helper=path.join(path.dirname(fileURLToPath(import.meta.url)),'checklist_text_dates.py');
      const restored=spawnSync(options.python || 'python3',[helper,'--workbook',output,'--plan',input],{encoding:'utf8'});
      if (restored.status!==0) throw new Error('文本日期保存核对失败：'+(restored.stderr || restored.error?.message || '无回执'));
    }
  } catch (error) { await fs.unlink(output);throw error; }
  const bytes=await fs.readFile(output);
  const receipt={ok:true,case_id:plan.case_id,output,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),sheets:detail,system_write:false,source_manifest:input,qa_dir:qaDir || null,preview_numeric_text_escaped_only:true};
  await fs.writeFile(output+'.receipt.json',JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  return receipt;
}

async function main() {
  const args=process.argv.slice(2),options={};
  const flags={'--input':'input','--output':'output','--module-root':'moduleRoot','--qa-dir':'qaDir','--python':'python'};
  for (let i=0;i<args.length;i+=2) {
    if (!flags[args[i]] || !args[i+1]) throw new Error('用法: --input 交付计划.json --output 新清单.xlsx [--module-root node_modules] [--qa-dir 新核验目录]');
    options[flags[args[i]]]=args[i+1];
  }
  if (!options.input || !options.output) throw new Error('须给input和output');
  console.log(JSON.stringify(await renderChecklist(options)));
}

if (process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message);process.exitCode=1; });
}
