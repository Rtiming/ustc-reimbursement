# 财务网页与两类导出

本适配器面向中国科大当前网页结构，需要本人账号、学校网络和本机Edge。安装及离线合成自检不证明网页接线、登录或真实财务写入已完成。Linux不提供财务网页操作，Windows整批测试版已接同一接口，但真实财务流程尚未实测；按docs/整批流程与登记.md完成当前设备与明确授权业务的验收前，不应据此认定生产可用。

## 本机接线

macOS先用统一CLI运行`bridge status`，按返回报告及`bridge --help`准备本用户Native Messaging桥。Windows运行`python tools/windows_bridge_setup.py status`，按该入口的`--help`执行prepare/install。浏览器扩展代码在extension/；扩展加载、账号登录及系统允许的本机权限由使用者完成。不要复制他人浏览器数据库、Cookie或密码库。

所有浏览器操作经本工具固定适配器执行。先`cases → overview`选同案，再使用tabs/access等既有入口核对当前账号、单号及页面。禁止以通用点击、任意JS、隐藏按钮或猜URL替代未实现能力。默认静默，不能因失败自动转成前台操控。

## 办理和恢复

调用合同在mcp/tool_catalog.json，任务通过明确operation入队。先预览，核当前案件授权，执行后沿原job取得结果和独立回读。未知结果只恢复原任务，不换request_id重发，不删除pending重试。

已登记批次的workflow阶段有fill、ready、approve、export-system、export-list、finish。ready只是准备到本人待审；approve需要当前案件的明确授权，验证码由本人在系统填写。批准、领导审核、正式打印与付款是不同状态。finish需要先验证整个批次的输入和授权，不是绕过分阶段检查的快捷键。

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
