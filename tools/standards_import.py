"""导入明确选择的私人规范包；保留来源、旧空模板和事务，不携带账号或财务案件。"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from assistant_contract import APIError
from reimbursement_standards import (
    encoded,
    validate_profile,
    _library_entry,
    _library_transaction,
    safe_path,
)
from workspace_config import standards_library

SCHEMA = "ustc-private-standards-bundle-v1"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".standards-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def validate(bundle):
    if (
        not isinstance(bundle, dict)
        or bundle.get("schema") != SCHEMA
        or set(bundle) - {"schema", "profiles", "material_rules", "provenance"}
    ):
        raise APIError("standards_bundle", "请选择本工具的私人规范包")
    if len(encoded(bundle)) > 2 * 1024 * 1024:
        raise APIError("standards_bundle", "私人规范包超过2MiB")
    profiles = bundle.get("profiles")
    ids = []
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 32:
        raise APIError("standards_bundle", "一次导入1至32个明确专项")
    for profile in profiles:
        check = validate_profile(profile)
        pid = profile.get("profile_id")
        if (
            check["issues"]
            or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", str(pid))
            or pid in ids
        ):
            raise APIError(
                "standards_bundle",
                "专项格式或范围无效："
                + json.dumps(check["issues"], ensure_ascii=False),
            )
        ids.append(pid)
    rules = bundle.get("material_rules")
    if rules is not None:
        if (
            not isinstance(rules, dict)
            or rules.get("configuration_status") == "needs_confirmation"
            or not isinstance(rules.get("rules"), list)
            or not rules["rules"]
        ):
            raise APIError(
                "standards_material_rules",
                "材料检查须有已核来源的非空规则，不能把空模板当作材料齐全",
            )
        seen = set()
        for row in rules["rules"]:
            if (
                not isinstance(row, dict)
                or not all(
                    isinstance(row.get(k), str) and row[k].strip()
                    for k in ["id", "role", "level", "instruction", "source", "basis"]
                )
                or row["id"] in seen
                or row["level"] not in {"required", "suggested"}
                or not isinstance(row.get("profiles"), list)
                or not row["profiles"]
                or not isinstance(row.get("when_all", {}), dict)
            ):
                raise APIError(
                    "standards_material_rules", "材料检查规则缺少固定结构或来源"
                )
            seen.add(row["id"])
    if not isinstance(bundle.get("provenance"), dict) or not bundle["provenance"].get(
        "source"
    ):
        raise APIError("standards_bundle_source", "私人规范包须说明来源")
    return profiles, rules


def plan(service, bundle):
    from assistant_api import read_json

    profiles, rules = validate(bundle)
    library = str(standards_library(service.root))
    root, entries, _, _, _ = _library_entry(service, library)
    registry = read_json(root / "registry.json")
    after = copy.deepcopy(registry)
    targets = {}
    summary = []
    ids = {r["profile_id"] for r in entries} | {p["profile_id"] for p in profiles}
    for profile in profiles:
        pid = profile["profile_id"]
        if set(profile.get("extends", [])) - ids:
            raise APIError(
                "standards_parent_missing",
                "规范包缺少被继承的专项，请同时导入明确父专项",
            )
        entry = next((r for r in entries if r["profile_id"] == pid), None)
        relative = entry["path"] if entry else "profiles/" + pid + ".json"
        path = safe_path(root, relative)
        before = path.read_bytes() if path.exists() else None
        payload = encoded(profile)
        if before is not None and before != payload:
            old = read_json(path)
            if old == profile:
                payload = before
            elif old.get("rules") or old.get("sources"):
                raise APIError(
                    "standards_import_existing",
                    "已有非空专项保持原样，请通过规则修订维护：" + pid,
                )
        if entry is None:
            after["profiles"].append(
                {"profile_id": pid, "title": profile["title"], "path": relative}
            )
        targets[relative] = {"before": before, "after": payload}
        summary.append(
            {
                "profile_id": pid,
                "title": profile["title"],
                "revision": profile["revision"],
                "rule_count": len(profile["rules"]),
                "change": "相同内容，保持原样"
                if before == payload
                else "替换已核空模板"
                if before is not None
                else "新增专项",
                "scope": profile.get("scope", {}),
            }
        )
    # 引用闭环必须在写入前拒绝；不能等首次选用时才留下坏库。
    graph = {p["profile_id"]: p.get("extends", []) for p in profiles}

    def visit(pid, trail=()):
        if pid in trail or len(trail) > 16:
            raise APIError("standards_cycle", "专项继承循环或超过16层")
        parents = graph.get(pid)
        if parents is None:
            parents = service.standards_read(library, pid)["profile"].get("extends", [])
        for parent in parents:
            visit(parent, (*trail, pid))

    for pid in graph:
        visit(pid)
    if rules is not None:
        path = root / "material_rules.json"
        before = path.read_bytes() if path.exists() else None
        payload = encoded(rules)
        if before is not None and before != payload:
            if read_json(path) == rules:
                payload = before
            else:
                raise APIError(
                    "standards_material_existing",
                    "已有私人材料规则与导入不同，保留原规则；先核对差异再修订",
                )
        targets["material_rules.json"] = {"before": before, "after": payload}
    targets["registry.json"] = {
        "before": (root / "registry.json").read_bytes(),
        "after": encoded(after)
        if after != registry
        else (root / "registry.json").read_bytes(),
    }
    scope = {
        "bundle_sha256": sha(encoded(bundle)),
        "targets": {
            k: {
                "before_sha256": sha(v["before"]) if v["before"] is not None else None,
                "after_sha256": sha(v["after"]),
            }
            for k, v in targets.items()
        },
    }
    return (
        root,
        targets,
        {
            "ok": True,
            "schema": "ustc-standards-import-preview-v1",
            "profiles": summary,
            "material_rule_count": len(rules["rules"]) if rules else 0,
            "source": bundle["provenance"],
            "scope": scope,
            "expected_snapshot": sha(encoded(scope)),
            "executed": False,
            "system_write": False,
            "finance_write_authorized": False,
        },
    )


def run(service, bundle, execute=False, expected_snapshot=None):
    from assistant_api import atomic_json

    if type(execute) is not bool:
        raise APIError("standards_import_execute", "execute须为布尔值")
    root, targets, preview = plan(service, bundle)
    if not execute:
        return preview
    if expected_snapshot != preview["expected_snapshot"]:
        raise APIError(
            "standards_import_changed", "规范库或导入内容已改变，请重新核对预览"
        )
    with _library_transaction(
        root, {"operation": "import_private_bundle", "scope": preview["scope"]}
    ) as audit:
        _, fresh, verified = plan(service, bundle)
        if verified["expected_snapshot"] != expected_snapshot:
            raise APIError("standards_import_changed", "等待期间规范库发生变化，未覆盖")
        atomic_json(audit / "bundle.json", bundle)
        atomic_json(audit / "intent.json", preview)
        written = []
        try:
            for i, (relative, change) in enumerate(fresh.items()):
                if change["before"] == change["after"]:
                    continue
                path = safe_path(root, relative)
                if (path.read_bytes() if path.exists() else None) != change["before"]:
                    raise APIError("standards_import_changed", "写入前原资料已改变")
                if change["before"] is not None:
                    (audit / (str(i) + "-before.json")).write_bytes(change["before"])
                replace_bytes(path, change["after"])
                written.append((path, change))
                if sha(path.read_bytes()) != sha(change["after"]):
                    raise APIError("standards_import_readback", "导入后回读不一致")
            atomic_json(
                audit / "committed.json",
                {"scope": preview["scope"], "expected_snapshot": expected_snapshot},
            )
        except Exception:
            restored = []
            for path, change in reversed(written):
                if path.read_bytes() != change["after"]:
                    continue
                if change["before"] is None:
                    path.unlink()
                else:
                    replace_bytes(path, change["before"])
                restored.append(str(path.relative_to(root)))
            atomic_json(
                audit / "rollback.json",
                {
                    "restored": restored,
                    "manual_review_required": len(restored) != len(written),
                },
            )
            raise
    return {
        **preview,
        "executed": True,
        "audit_path": str(audit),
        "next_action": {
            "tool": "reimbursement_standards_read",
            "arguments": {},
            "label": "查看导入后的专项与来源",
        },
    }


def main():
    import argparse
    from assistant_api import AssistantService
    from workspace_config import resolve_material_roots

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.bundle.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("私人规范包超过2MiB")
    bundle = json.loads(args.bundle.read_text(encoding="utf-8-sig"))
    service = AssistantService(root, resolve_material_roots(root))
    preview = run(service, bundle)
    result = (
        run(service, bundle, True, preview["expected_snapshot"])
        if args.execute
        else preview
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
