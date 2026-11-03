"""当前账号的审批批次计划与任务入口。计划、本人验证及原写入结果分别保留。"""

from pathlib import Path
import hashlib
import json
import re

from assistant_contract import APIError

SCHEMA = "ustc-approver-batch-v1"
PHASES = {"run", "resume", "recover", "reconcile", "verification", "finalize"}


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def folder(service, plan_id):
    if not isinstance(plan_id, str) or not re.fullmatch("[a-f0-9]{32}", plan_id):
        raise APIError("approver_plan", "请使用本次审批计划的plan_id")
    return service.root / ".local/approver-batches" / plan_id


def load(service, plan_id):
    from assistant_api import read_json
    from material_intake import reject_links

    root = folder(service, plan_id)
    reject_links(root)
    plan = read_json(root / "plan.json")
    body = {k: v for k, v in plan.items() if k not in {"plan_id", "fingerprint"}}
    if (
        plan.get("schema") != SCHEMA
        or plan.get("fingerprint") != fingerprint(body)
        or plan_id != plan["fingerprint"][:32]
    ):
        raise APIError("approver_plan_changed", "审批计划发生变化，保留原任务，不执行")
    return root, plan


def source(service, connection_id, source_job_id):
    from finance_connection import bound

    connection, runtime = bound(service, connection_id)
    case = connection["case_id"]
    result = service.result(case, source_job_id)
    outputs = [a for a in result.get("artifacts", []) if a["name"] == "output.json"]
    if (
        result.get("status") != "completed"
        or result.get("exit_code") != 0
        or len(outputs) != 1
    ):
        raise APIError("approver_source", "先从本人财务收件箱取得完整待审批清单")
    path, envelope, request = service._typed_case_artifact(
        case, source_job_id, outputs[0]["artifact_id"], "business", ["approvals-list"]
    )
    body = envelope.get("result", {})
    who = connection["expected_userid"]
    rows = body.get("rows")
    if (
        request.get("execute") is not False
        or request["parameters"].get("expected_userid") != who
        or envelope.get("phase") != "script_completed"
        or body.get("ok") is not True
        or body.get("complete") is not True
        or body.get("case_id") != case
        or body.get("expected_userid") != who
        or body.get("account_scope") != "current_approver"
        or body.get("query_scope") != "native_approval_list_empty_filters"
        or body.get("system_write") is not False
        or body.get("business_transport") != "native_http"
        or body.get("action") != "approvals-list"
        or not isinstance(rows, list)
        or type(body.get("total_records")) is not int
        or body["total_records"] != len(rows)
    ):
        raise APIError(
            "approver_source",
            "来源不是同账号完整待审批清单，不能使用历史记录或其他账号的清单",
        )
    proof = {
        "case_id": case,
        "job_id": source_job_id,
        "artifact_id": outputs[0]["artifact_id"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    items = []
    for row in rows:
        identity = {
            k: row.get(k)
            for k in ["t.serial_no", "t.uni_key", "t.node_id", "t.collab_ord"]
        }
        item_id = fingerprint(identity)[:24]
        eligible = (
            row.get("t.wfname") == "YBX_LC"
            and row.get("t.wf_name") == "YBX_LC"
            and row.get("t.collab_ord") == "none"
            and row.get("t.isNeedCollab") == "F"
            and row.get("t.nodecnt") == "1"
            and row.get("t.signtype") in {"", "no"}
            and str(row.get("t.curr_mans", "")).startswith(who + " - ")
        )
        items.append(
            {
                "item_id": item_id,
                "serial": row.get("t.serial_no"),
                "applicant": row.get("t.req_name"),
                "summary": row.get("t.remark"),
                "amount": row.get("t.amount"),
                "role": row.get("t.node_name1"),
                "supported": eligible,
                "reason": None
                if eligible
                else "此项需要另核协同、签章、分发或角色范围，暂不批量派发",
                "native_row": row,
            }
        )
    if len({r["item_id"] for r in items}) != len(items):
        raise APIError("approver_source", "审批节点身份重复，先核对原清单")
    return connection, runtime, proof, body, items


def view(service, plan_id):
    from assistant_api import read_json

    root, plan = load(service, plan_id)
    progress = (
        read_json(root / "progress.json")
        if (root / "progress.json").exists()
        else {"state": "planned", "items": []}
    )
    value = {
        "ok": True,
        "schema": SCHEMA,
        "plan_id": plan_id,
        "case_id": plan["case_id"],
        "expected_userid": plan["expected_userid"],
        "decision": plan["decision"],
        "opinion": plan["opinion"],
        "source": plan["source"],
        "state": progress["state"],
        "items": progress.get("items")
        or [
            {
                "item_id": r["item_id"],
                "serial": r["serial"],
                "summary": r["summary"],
                "amount": r["amount"],
                "role": r["role"],
                "state": "not_started",
            }
            for r in plan["items"]
        ],
        "last_job_id": progress.get("last_job_id"),
        "error": progress.get("error"),
        "pause_requested": (
            read_json(root / "control.json").get("pause_requested", False)
            if (root / "control.json").exists()
            else False
        ),
        "system_write": False,
        "note": "只显示原批次记录；审批步骤完成不代表整单审批或付款完成。",
    }
    from approver_view import project

    # 完成由逐项闭合导出；损坏或不完整的汇总字段不能盖过子项的实际停点。
    if value['state'] == 'completed':
        expected = {(i['item_id'], i['serial']) for i in plan['items']}
        actual = {(i.get('item_id'), i.get('serial')) for i in value['items']}
        if (expected != actual or len(value['items']) != len(plan['items'])
                or any(i.get('state') != 'verified' or i.get('finalized') is not True for i in value['items'])):
            value.update(state='result_needs_review', error='批次汇总与逐项回读不一致；保留原记录，核对每项后再接续。')
    value["next_action"] = project(value)["next_action"]
    return value


def assert_can_dispatch(service, request):
    """本人验证和未知审批期间保留原现场；材料整理和只读核对可继续。"""
    if request.get("execute") is not True or request["operation"] in {
        "material_intake",
        "material_ocr",
        "activity",
        "render",
        "pdf_export",
        "print_batch",
        "record_result",
    }:
        return
    from assistant_api import read_json

    for path in (service.root / ".local/approver-batches").glob("*/progress.json"):
        state = read_json(path)
        if state.get("state") not in {
            "verification_required",
            "unknown_result",
            "result_needs_review",
        }:
            continue
        if (
            request["operation"] == "approver_batch"
            and request["parameters"].get("plan_id") == path.parent.name
        ):
            continue
        raise APIError(
            "approver_original_pending",
            "有一批本人验证或审批结果尚待核对，请先回“本人确认与审批”接续原批次；材料整理仍可继续。",
        )


def run(
    service,
    action="inspect",
    connection_id=None,
    source_job_id=None,
    selected_ids=None,
    decision="agree",
    opinion=None,
    plan_id=None,
    authorization_note=None,
    request_id=None,
    execute=False,
):
    from assistant_api import AssistantService, atomic_json, read_json

    if (
        action not in {"list", "inspect", "plan", "status", "pause"} | PHASES
        or type(execute) is not bool
    ):
        raise APIError("approver_action", "请选择原批次的明确操作")
    if action == "list":
        if execute:
            raise APIError("approver_action", "查找原批次不接受执行标志")
        plans = sorted(
            (service.root / ".local/approver-batches").glob("*/plan.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:100]
        return {
            "ok": True,
            "plans": [view(service, p.parent.name) for p in plans],
            "system_write": False,
        }
    if action == "status":
        if execute:
            raise APIError("approver_action", "查看状态不接受执行标志")
        return view(service, plan_id)
    if action in {"inspect", "plan"}:
        if execute:
            raise APIError(
                "approver_action", "读取或冻结计划不批准单据，不接受财务执行标志"
            )
        connection, runtime, proof, observed, items = source(
            service, connection_id, source_job_id
        )
        if action == "inspect":
            return {
                "ok": True,
                "schema": "ustc-approver-selection-v1",
                "case_id": connection["case_id"],
                "connection_id": connection_id,
                "source_job_id": source_job_id,
                "observed_at": observed.get("observed_at"),
                "items": [
                    {k: v for k, v in r.items() if k != "native_row"} for r in items
                ],
                "system_write": False,
                "needed_inputs": [
                    "明确选择的审批项",
                    "同意或拒绝",
                    "逐字确认的审批意见",
                ],
            }
        if (
            decision not in {"agree", "reject"}
            or not isinstance(opinion, str)
            or not opinion.strip()
            or len(opinion) > 2000
        ):
            raise APIError(
                "approver_opinion",
                "请选择同意或拒绝，并填写本次实际审批意见，最多2000字",
            )
        if (
            not isinstance(selected_ids, list)
            or not 1 <= len(selected_ids) <= 100
            or any(not isinstance(v, str) for v in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
        ):
            raise APIError("approver_selection", "明确选择1至100个不同的原审批项")
        lookup = {r["item_id"]: r for r in items}
        if set(selected_ids) - set(lookup):
            raise APIError("approver_selection", "所选项不在本次完整清单中")
        selected = [lookup[key] for key in selected_ids]
        if any(not row["supported"] for row in selected):
            raise APIError(
                "approver_special_role",
                "所选项含尚需原生签章、协同或分发的角色，不能混入普通批次",
            )
        body = {
            "schema": SCHEMA,
            "case_id": connection["case_id"],
            "expected_userid": connection["expected_userid"],
            "connection": {
                k: connection[k]
                for k in [
                    "connection_id",
                    "expected_userid",
                    "tab_id",
                    "window_id",
                    "namespace",
                ]
            },
            "source": proof,
            "items": selected,
            "decision": decision,
            "opinion": opinion,
            "preserve_sms_verification": True,
            "create_new_claims": False,
        }
        digest = fingerprint(body)
        plan_id = digest[:32]
        root = folder(service, plan_id)
        root.mkdir(parents=True, exist_ok=True)
        from material_review_launch import launch_lock

        with launch_lock(root / "plan.json"):
            planned = {**body, "plan_id": plan_id, "fingerprint": digest}
            if (root / "plan.json").exists() and read_json(
                root / "plan.json"
            ) != planned:
                raise APIError("approver_plan_conflict", "同一计划标识已有不同内容")
            if not (root / "plan.json").exists():
                atomic_json(root / "plan.json", planned)
        return {
            **view(service, plan_id),
            "authorization_required": True,
            "next_action": {
                "tool": "reimbursement_approver_batch",
                "arguments": {
                    "action": "run",
                    "plan_id": plan_id,
                    "connection_id": connection_id,
                    "execute": True,
                },
                "needed_inputs": [
                    "用户对所列单号、角色、金额、意见的明确授权说明authorization_note",
                    "本次request_id",
                ],
            },
        }
    root, plan = load(service, plan_id)
    if action == "pause":
        if execute:
            raise APIError("approver_action", "暂停只登记本地意图，不接受财务执行标志")
        atomic_json(root / "control.json", {"pause_requested": True})
        return {
            **view(service, plan_id),
            "note": "已请求暂停后续项目；正在派发的本项仍须完成回读，原验证码界面和未知结果不会被清除。",
        }
    if not execute and action in {"run", "resume", "verification", "finalize"}:
        return {
            **view(service, plan_id),
            "preview": True,
            "note": "尚未派发审批；本人验证码及结果回读保留。",
        }
    if execute != (action in {"run", "resume", "verification", "finalize"}):
        raise APIError(
            "approver_execute", "恢复和独立核对只读；实际办理与本人验证界面须明确执行"
        )
    from finance_connection import bound

    connection, runtime = bound(
        service, connection_id or plan["connection"]["connection_id"]
    )
    if connection["expected_userid"] != plan["expected_userid"]:
        raise APIError("approver_identity", "不能把原审批计划交给另一个账号执行")
    if action != "reconcile" and any(
        connection[k] != plan["connection"][k]
        for k in ["tab_id", "window_id", "namespace"]
    ):
        raise APIError(
            "approver_transport_changed",
            "原页面身份已改变。先独立核对原结果，不重发审批。",
        )
    auth = root / "authority.json"
    if execute:
        if not auth.exists():
            if (
                not isinstance(authorization_note, str)
                or not authorization_note.strip()
            ):
                raise APIError(
                    "approver_authority", "请记录用户对本批具体范围和意见的明确授权"
                )
            atomic_json(
                auth,
                {
                    "schema": "ustc-approver-authority-v1",
                    "plan_id": plan_id,
                    "plan_sha256": plan["fingerprint"],
                    "expected_userid": plan["expected_userid"],
                    "user_text": authorization_note.strip(),
                    "decision": plan["decision"],
                    "opinion": plan["opinion"],
                    "preserve_sms_verification": True,
                    "create_new_claims": False,
                },
            )
    if not auth.is_file():
        raise APIError(
            "approver_original_authority", "没有本批已确认的原执行授权，只能先查看计划"
        )
    if action == "resume":
        atomic_json(root / "control.json", {"pause_requested": False})
    scoped = AssistantService(
        service.root, service.material_roots, service.python, runtime
    )
    return {
        **scoped.enqueue(
            plan["case_id"],
            "approver_batch",
            {
                "action": action,
                "plan_id": plan_id,
                "connection_id": connection["connection_id"],
            },
            request_id,
            execute,
        ),
        "case_id": plan["case_id"],
        "plan_id": plan_id,
    }


def command(service, request, job_folder):
    from assistant_api import atomic_json, read_json, PROJECT
    from finance_native_create import ref, frozen
    from finance_http import source as adapter_source

    args = request["parameters"]
    root, plan = load(service, args["plan_id"])
    if (
        request["case_id"] != plan["case_id"]
        or args["action"] not in PHASES
        or request["execute"]
        != (args["action"] in {"run", "resume", "verification", "finalize"})
    ):
        raise APIError("approver_job_scope", "任务与原审批计划不一致")
    authority = read_json(root / "authority.json")
    if (
        authority.get("schema") != "ustc-approver-authority-v1"
        or authority.get("plan_sha256") != plan["fingerprint"]
        or authority.get("expected_userid") != plan["expected_userid"]
        or authority.get("decision") != plan["decision"]
        or authority.get("opinion") != plan["opinion"]
        or not authority.get("user_text")
        or authority.get("preserve_sms_verification") is not True
    ):
        raise APIError("approver_authority_changed", "原授权与当前计划不同，未派发")
    original = service.artifact(
        plan["case_id"], plan["source"]["job_id"], plan["source"]["artifact_id"]
    )
    if hashlib.sha256(original.read_bytes()).hexdigest() != plan["source"]["sha256"]:
        raise APIError("approver_source_changed", "原待办清单证据已改变")
    code = adapter_source("approver")
    adapter = job_folder / "approver-adapter.js"
    if adapter.exists() and adapter.read_text(encoding="utf-8") != code:
        raise APIError("approver_adapter_changed", "原任务源码变化，保留原意图")
    if not adapter.exists():
        adapter.write_text(code, encoding="utf-8", newline="\n")
    contracts = read_json(PROJECT / "data/finance_business_contracts.json")["domains"]
    config = {
        "schema": SCHEMA,
        "workspace": str(service.root),
        "material_roots": list(map(str, service.material_roots)),
        "case_id": plan["case_id"],
        "plan_id": plan["plan_id"],
        "plan": ref(root / "plan.json"),
        "authority": ref(root / "authority.json"),
        "source_snapshot": ref(original),
        "adapter": ref(adapter),
        "action": args["action"],
        "request_id": request["request_id"],
        "job_id": job_folder.name,
        "connection_id": args["connection_id"],
        "contract": read_json(PROJECT / "data/finance_approver_contract.json"),
        "queue_contracts": contracts["approval"],
        "history_contracts": contracts["approval_history"],
        "log_contracts": [c for c in contracts["submission"] if c["funcno"] == "1816"],
        "context_contracts": [
            c for c in contracts["submission"] if c["funcno"] in {"109", "1816"}
        ],
        "submission_contracts": contracts["submission"],
    }
    if args["action"] == "verification":
        verification_code = (
            code
            + "\n"
            + (PROJECT / "tools/finance_approver_verification.js").read_text(
                encoding="utf-8"
            )
        )
        verification_adapter = job_folder / "verification-adapter.js"
        if (
            verification_adapter.exists()
            and verification_adapter.read_text(encoding="utf-8") != verification_code
        ):
            raise APIError("approver_adapter_changed", "原本人验证源码变化")
        if not verification_adapter.exists():
            verification_adapter.write_text(
                verification_code, encoding="utf-8", newline="\n"
            )
        config["verification_adapter"] = ref(verification_adapter)
    path = job_folder / "approver-input.json"
    frozen(path, config)
    return [
        service.python,
        str(PROJECT / "tools/finance_approver_batch.py"),
        "--input",
        str(path),
        "--output",
        str(job_folder / "output.json"),
    ]
