"""材料操作的按需字段合同；给简明代理入口和表单维护者复用。"""


def value_schemas():
    def obj(properties, required=None):
        return {
            "type": "object",
            "properties": properties,
            "required": required or list(properties),
            "additionalProperties": False,
        }

    def text(title):
        return {"type": "string", "title": title}

    def rows(item):
        return {"type": "array", "items": item}

    source = obj(
        {
            "key": text("inspect.materials中的key，原样使用"),
            "page": {"type": "integer", "minimum": 1},
        }
    )
    invoice = obj(
        {
            "source": source,
            "number": {
                "type": "string",
                "pattern": "^[0-9]{20}$",
                "title": "原件完整票号",
            },
            "face_amount": text("已核票面金额，小数文本"),
        }
    )
    evidence = obj(
        {
            "source": source,
            "kind": {"type": "string", "enum": ["request", "list"]},
            "amount": text("同一费用范围的申请或清单金额"),
        }
    )
    payee = obj(
        {
            "source": source,
            "payee_id": text("实际收款学工号或本案唯一单位编号"),
            "name": text("实际收款人或单位"),
            "mode": {
                "type": "string",
                "enum": ["bank_transfer", "corporate_transfer", "corporate_card"],
            },
            "amount": text("该收款方应收金额"),
        }
    )
    contact = obj(
        {
            "name": text("明白纸此栏姓名"),
            "blank": {
                "type": "boolean",
                "description": "只有原件明确空白才填true，不能同时填姓名",
            },
            **{
                key: text(title)
                for key, title in {
                    "student_id": "学号",
                    "employee_id": "工号",
                    "phone": "电话",
                    "wechat": "微信",
                    "qq": "QQ",
                    "email": "邮箱",
                }.items()
            },
        },
        ["name"],
    )
    # 两个互斥分支：已核联系人或明确空栏；未知时省略整个字段。
    contact = {"anyOf": [contact, obj({"blank": {"const": True}})]}
    expense = obj(
        {
            "expense_id": text("本活动稳定费用编号，只用英文数字下划线，不因重试更换"),
            "description": text("费用说明"),
            "invoices": rows(invoice),
            "amount_evidence": rows(evidence),
            "payees": rows(payee),
            "payment_materials": rows(source),
            "itinerary_materials": rows(source),
        },
        ["expense_id", "description", "invoices"],
    )
    return {
        "materialize": obj({"plan_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}),
        "save_dispositions": rows(obj({"source": source,
            "disposition": {"type": "string", "enum": ["electronic", "paper", "exclude"]},
            "reason": text("本页去向依据"), "paper_location": text("纸质原件安排或待定位说明")},
            ["source", "disposition", "reason"])),
        "save_document": obj(
            {
                "folder_name": text("原活动文件夹名称"),
                "business_date": {
                    "type": "string",
                    "format": "date",
                    "title": "明白纸业务日期",
                },
                "source": source,
                "first_sequence": {"type": "integer", "minimum": 1},
                "responsible_person": contact,
                "form_filler": contact,
            },
            ["folder_name", "source"],
        ),
        "save_expenses": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": expense,
        },
        "save_issue": obj(
            {
                "issue_id": text("本活动稳定问题编号"),
                "title": text("问题"),
                "request": text("需要什么答复或补件"),
                "owner": text("请谁处理"),
                "materials": rows(source),
            },
            ["issue_id", "title", "request", "owner"],
        ),
        "remove_expense": obj(
            {"expense_id": text("要移出规划的原费用编号；不删除原件")}
        ),
    }
