from time import perf_counter_ns

from app.codec import digest
from app.errors import DomainError
from app.pipeline import evaluate
from app.records import token
from .manifest import create
from .revision import diff, lines, merge, parse


def execute(ledger, source):
    start = perf_counter_ns()
    result = evaluate(source)
    manifest = create(source, result, (perf_counter_ns() - start) // 1_000_000)
    snapshot = {"id": manifest["id"], "manifest": manifest, "result": result, "delta": None}
    saved, reused = ledger.save(manifest["id"], source, snapshot)
    if saved["manifest"]["digest"] != manifest["digest"]:
        raise DomainError("REPLAY_MISMATCH", "重算摘要不一致，请复核规则版本", 409)
    return {**saved, "reused": reused}


def supplement(ledger, ident, body):
    base_source, base = ledger.read(ident)
    addition = parse(body)
    start = perf_counter_ns()
    merged = merge(base_source, addition)
    result = evaluate(merged)
    supplement_lines = lines(base_source, merged)
    manifest = create(merged, result, (perf_counter_ns() - start) // 1_000_000,
                      parent=ident, revision=base["manifest"].get("revision", 1) + 1)
    manifest["supplement"] = {**supplement_lines, "at": merged["at"]}
    snapshot = {"id": manifest["id"], "manifest": manifest, "result": result,
                "delta": diff(base["result"], result, ident, supplement_lines)}
    saved, reused = ledger.save(manifest["id"], merged, snapshot)
    if saved["manifest"]["digest"] != manifest["digest"]:
        raise DomainError("REPLAY_MISMATCH", "重算摘要不一致，请复核规则版本", 409)
    return {**saved, "reused": reused}


def rerun(ledger, ident):
    source, previous = ledger.read(ident)
    result = evaluate(source)
    if digest(result) != previous["manifest"]["digest"]:
        raise DomainError("REPLAY_MISMATCH", "重跑结果与归档不一致", 409)
    return {**previous, "reused": True}


def sign(ledger, ident, body):
    _, previous = ledger.read(ident)
    if not token(body.get("reviewer")):
        raise DomainError("REVIEWER_REQUIRED", "请输入签收人")
    if previous["manifest"]["digest"] != body.get("digest"):
        raise DomainError("STALE_DIGEST", "摘要已变化，请重新打开凭证", 409)
    if previous["result"]["reconcile"]["status"] != "MATCHED":
        raise DomainError("UNBALANCED_RUN", "归类未对平，当前版本不可签署", 409)
    receipt = {"reviewer": body["reviewer"].strip(), "digest": body["digest"],
               "id": ident, "balance": previous["result"]["reconcile"]["status"]}
    return ledger.sign(ident, receipt)
