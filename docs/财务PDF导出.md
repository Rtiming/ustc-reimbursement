# 财务接口与文件导出

审批后正式打印已提供`workflow --case-id 本批 --phase print --output-root /授权资料/正式打印`，默认预览。`--merge-pdf/--no-merge-pdf`和`--finance-only/--no-finance-only`独立，缺省false/true；`--execute`才开始。MCP为`reimbursement_print_batch`，返回job_id后查result。正式打印直接调用固定学校HTTP接口，不模拟点击；其他历史页面适配器不表示已经迁移。登记工作页共用Mac/Windows原生桥；入口和验收边界见[Windows适配与功能验收](Windows适配与功能验收.md)。

当前基本信息、经费、预算、支付、说明、预审、普通建单、原件上传、异常清理、医疗自费和单段行程维护已有固定HTTP适配及独立回读合同，具体限制由能力目录返回。新增对公身份及单笔公务卡选择有合成验证，真实有变更保存仍待对应样本验收；其他写业务继续由迁移策略阻止旧点击路径。`access check-backend`只读验证固定后端或首页YBX入口，登记会话开始前自动使用。接口不可用时不自动换报账项目、不重试写入。详细参数和恢复见包内`docs/底层业务接口.md`，能力目录以同一实现返回的状态为准。


本适配器面向中国科大当前网页结构，需要本人账号、学校网络和本机Edge。安装及离线合成自检不证明网页接线、登录或真实财务写入已完成。Linux不提供财务网页操作，Windows已接同一接口，旧版已有现场办理记录；本版精确包与每台设备仍须分别验收，按docs/整批流程与登记.md完成当前设备与明确授权业务的验收前，不应据此认定生产可用。

## 本机接线

macOS先用统一CLI运行`bridge status`，按返回报告及`bridge --help`准备本用户Native Messaging桥。Windows运行`python tools/windows_bridge_setup.py status`，按该入口的`--help`执行prepare/install。浏览器扩展代码在extension/；扩展加载、账号登录及系统允许的本机权限由使用者完成。不要复制他人浏览器数据库、Cookie或密码库。

所有浏览器操作经本工具固定适配器执行。先`cases → overview`选同案，再使用tabs/access等既有入口核对当前账号、单号及页面。禁止以通用点击、任意JS、隐藏按钮或猜URL替代未实现能力。默认静默，不能因失败自动转成前台操控。

## 办理和恢复

调用合同在mcp/tool_catalog.json，任务通过明确operation入队。先预览，核当前案件授权，执行后沿原job取得结果和独立回读。未知结果只恢复原任务，不换request_id重发，不删除pending重试。

已登记批次的workflow阶段有fill、ready、approve、print、export-system、export-list、finish。ready只是准备到本人待审；approve需要当前案件的明确授权，验证码由本人在系统填写。批准、领导审核、正式打印与付款是不同状态。finish需要先验证整个批次的输入和授权，不是绕过分阶段检查的快捷键。

## 明细PDF与列表导出

| 动作 | 产物 | 不能代替 |
| --- | --- | --- |
| 查看明细PDF / export-system | 绑定当前单号的原生单据明细PDF | 审批完成证明、正式打印状态或列表导出 |
| 原生列表导出 / export-list | 按确认范围导出的原列表文件 | 逐张单据PDF |
| 正式预约打印 | 学校状态允许时取得的正式打印件 | 不能从查看明细成功推定 |

明细导出先确认case_id、serial、业务日期、序号、摘要及来源，再读取当前同源PDF预览。`view_kind="details"`与`print_preview`绑定不同原生入口。统一任务层冻结tab、expected_userid和输出目录；准确参数按工具schema，不让代理指定任意下载URL。

默认delivery_mode为rename_only：既有对应关系成立后保留原文件字节并命名归档，只检查传输完成、文件缺失、重复命名和恢复。不会为了归档再次审核金额、页数或版面。文件SHA用于确认传输字节，不能证明财务状态。

列表导出经grid_export_workflow单独处理，确认完整单号范围和格式后取得原生文件。明细与列表各自有结果回执，任何一个缺失都必须明确报告。导出受阻时保留原任务，不将网页截图、HTML重排或人工合成PDF冒充系统PDF。

低层诊断`tools/finance_pdf_export.py --inspect-output 原任务绝对目录`只检查原回执；实际业务仍从统一助手入口和当前案件继续。审批速度以真实逐单独立回读及完整计时为准，复用旧回执或合成测试不能作为整批5分钟达标证据。

正式打印的`finance_only`映射学校附件选项，`merge_pdf`仅作用于本地合并，源文件始终保留。学校明确拒绝时分单登记，未知打印结果停止共享现场。已确认生成且只在本地文件类型校验失败时，`pdf_export`可用`resume_job_id`恢复原引用的下载；不重发生成、不换会话、不接受任意URL。

## 独立密码库

财务工具仅登记账号与credential_reference，不保存密码库或登录秘密。可使用经办人自己的
rtime-credentials：在本机data/finance_access.json指定credential_cli及私有credential_config
绝对路径。默认后端为rtime-credentials；服务自身的本机加密缓存、同步及离线解锁由服务维护。

其他密码库通过credential_backend=json-command及部署者固定credential_command数组接入，
首项须为绝对可执行文件。工具追加use，stdin发送bindings、固定argv和timeout；代理
将凭据字段注入子进程环境，返回{"ok":true,"result":{"returncode":0,"stdout":"业务结果","stderr":""}}。
credential_config通过CREDENTIAL_PROVIDER_CONFIG传入，且其SHA绑定原认证意图。
业务调用不能临时换代理、库或服务URL；失败停止，不回退为项目明文密码。学校CAS
与本人短信/审批继续核本人的真实身份。独立密码库部署和金融写入分别验收。
