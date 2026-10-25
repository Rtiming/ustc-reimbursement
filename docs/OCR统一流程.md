# OCR统一流程

[中文](OCR统一流程.md) | [English](en/materials-and-ocr.md)

OCR接在正式收件之后：已完成的material_intake回执→缺文字候选→预览→明确执行→原任务回收→文字证据和票面候选。原件、原生文字和OCR输出分别保存，不把识别值当成已核定金额、真实身份或财务批准。

## 首先检查提供方

调用`capabilities(intent="ocr")`或`ocr_status()`。这两个入口不探测浏览器，不识别或上传材料。就绪度只说明本机配置与依赖可用于发起请求；远程连接和环境在实际任务中再次核验。

默认不启用本机OCR，不自动下载模型。已有远程提供方沿共享rtime compute运行，项目仅编译输入与读取结果；源码/输入冻结、传输、排队、资源守卫、等待和回收不另造一套。

没有服务器的新用户可以明确选择本地CPU识别：

```sh
sh install.sh --with-ocr
```

Windows用`install.cmd --with-ocr`。这会安装requirements-ocr.lock.txt中锁定的可选依赖和RapidOCR包内模型，配置仅在本工作区生效，并使用一张合成图片做实际OCR验收。普通安装及普通--check不会自动识别。已配置远程提供方时拒绝自动切换，本机模式每个请求最多20页、CPU线程限定为2，不能把大批材料静默移到本机。

## 使用同一套接口

MCP工具加reimbursement_前缀；JSON方法名、参数和结果含义与正式接口目录一致。先new_case建立本机案件，并完成material_intake；随后：

```json
{
  "method": "material_ocr",
  "params": {
    "case_id": "example_case",
    "request": {"intake_job_id": "原已完成收件job_id"},
    "request_id": "ocr-preview-01",
    "execute": false
  }
}
```

默认选择该收件回执中全部缺文字候选；request可加candidate_ids明确子集，以及rotation指定顺时针0/90/180/270度。候选必须来自同案已完成收件，不接任意源路径、URL、脚本或服务地址。原件工作副本SHA、页号和原始候选回执都会重新绑定。超出配置页数上限时先分批；长PDF每8页形成一个计算项，同一文件字节仍由输入CAS去重。

预览后使用新的执行请求号并设置execute=true。返回job_id后调用result；同一执行请求号再次调用只返回原job，不重复识别。所有步骤均为材料处理，不使用财务ack-write。

无缺文字页返回no_ocr_needed，不调用模型。提供方未配置时，预览仍列候选但can_execute=false；实际执行拒绝。不要将未配置解释成无材料问题。

## 结果和续办

| 结果 | 下一步 |
|---|---|
| ocr_preview | 核对候选、页数、方向和提供方，再明确执行 |
| waiting_original_remote | 任务程序已返回，但远程计算尚未完整结束，使用原OCR job续办 |
| failed_original_remote / original_ocr_needs_inspection | 读原日志与原run，不自动重新提交 |
| completed | 读取本job中的文字证据、回接清单和票面候选；仍需人工核对关键事实 |
| no_ocr_needed | 沿原收件清单继续整理，无需OCR |

续办调用`material_ocr_resume(case_id, ocr_job_id, request_id)`，ocr_job_id始终指原始执行任务。它沿同一个远程run等待/回收，已完成结果逐文件核SHA后复用；不会换身份重新计算。源材料或冻结配置改变时停止。计算目录保存在本机`.local/ocr-workflows/<case>/`，业务回执仍从统一result/artifact_id读取。

完成产物包括：

- text-evidence.json：既有ustc-invoice-page-text-v1格式，绑定原件SHA、页码、原OCR结果SHA、方向和置信度。
- material_inventory_ocr.json：派生清单，保留native_text，在缺文字页追加ocr_text与证据。
- invoice-review.json：直接调用原invoice_review分析器生成票面候选和问题，不复制一套金额/身份算法。
- 原识别JSON、TXT、实际识别图与计算回执：留在同一工作流中，保留模型/环境、源文件和源码身份。

文字证据可继续传给invoice_review的text-evidence入口。总账仍可通过ocr_result_paths复用原识别报告。空页、低置信度和OCR错字保留为问题；签名、章印、金额及相似数字不凭机器识别自动确认。PDF文字层缺少Poppler/pypdf时复用包内PyMuPDF读取，不因此启动OCR。

## 配置远程提供方

机器配置为`.local/ocr-provider.json`，不随包分发、不从材料请求读取。配置须来自接收者自己的已核验环境，例如：

```json
{
  "schema": "ustc-ocr-provider-v1",
  "backend": "rtime_compute",
  "target": "master",
  "server_python": "/远端个人目录/venvs/reimbursement-ocr/bin/python",
  "probe_sha256": "实际环境probe完整stdout的64位SHA",
  "compute_cli": "/本机已安装的/rtime",
  "dpi": 250,
  "concurrency": 2,
  "max_pages": 100,
  "wait_seconds": 180
}
```

当前远程适配只接受已经核验的master类型目标；未配置共享计算工具的接收者可选显式本机模式，不能复制作者账号或服务器配置。环境probe与worker由remote_material_ocr.py持有；规则沿原远程计算合同，未知run先由共享工具核对。

依赖探针只读取包版本及模型SHA，不为检查就绪度初始化OpenCV或识别引擎。模型不符时自检拒绝；正常安装只从锁定的RapidOCR包恢复模型，不能重算未知模型的SHA来放行。

## 维护与验证

material_ocr_service负责同案回执、源范围与任务层接线；ocr_workflow负责确定性的编排及结果回接；ocr_runtime负责提供方配置。识别内核仍在remote_material_ocr.py，远程和显式本机模式共用，不新增第二套识别算法。财务任务与材料任务的锁及结果语义分开，OCR不会切换财务页面。

RapidOCR的ONNX实现及包内中文模型来自[官方项目](https://github.com/RapidAI/RapidOCR)和[固定包1.4.4](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/)。版本、模型SHA和实际运行结果分别记录；软件安装成功不能代替真实识别测试，也不保证任意扫描件的准确率。
