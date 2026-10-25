# 报销工具代理入口

1. 先读`docs/用户指南.md`及当前用户指定案件的README。未安装先运行根目录install.sh/install.cmd；已安装用--check自检。资料根和当前专项从本机`.local/installation.json`读取，不猜作者路径。不要读取或借用其他案件的身份、金额、经费和授权。
2. 用`capabilities(intent)`选任务组，按`next_action`和`needed_inputs`继续。MCP方法加`reimbursement_`前缀；JSON方法不加前缀。准确参数从MCP schema或`mcp/tool_catalog.json`读取。
3. 首次使用`standards_init`预览并创建新私人规范库；已有库追加专项用standards_add_profile预览，再携原registry SHA保存。`student_activity/research_purchase/travel`是材料场景，`profile_id`是用户的组织/项目专项。`extends`只组合显式登记规则；有冲突先保留来源，不按层级猜覆盖。
4. 财务办理先`cases → overview`选定案件，再核对本机账号、页面、单号、金额及用户授权。只用本工具固定动作；不使用通用浏览器、坐标、任意JS或猜测接口替代。
5. 预览与执行分开。写操作用稳定request_id；取得job_id后查result。未知结果只恢复原任务，不能换号重发、清pending或借新页面冒充原现场。
6. 保存、本人批准、领导审批、正式打印、原件投递和付款分别报告。本人验证码交用户；不读取、代填或索要到聊天中。
7. 查看明细与原生列表导出分开；建立映射后归档原文件，只做下载完成、文件缺失/冲突及恢复检查。未经真实整批测速，不能报告5分钟批准达标。
8. 资料及设备配置保留在本机私有目录。升级换新工具目录，保留旧版本与回执；不覆盖正在运行的现场，不复制登录数据库。

OCR先检查ocr_status，再用本案收件job调用material_ocr；不直接拿路径执行脚本，不默认识别已有文字页。远程任务用material_ocr_resume接续原run。无服务器的新用户可明确--with-ocr安装本地模型，默认安装不下载模型。OCR结果只作候选，不自动确认票号、金额、身份或审批。

本包不含个人凭据库、远程总账运行服务、手机采集通道或表格创作运行时。缺少外部能力应返回具体缺项，不能伪造已执行或私自改用其他上传服务。
