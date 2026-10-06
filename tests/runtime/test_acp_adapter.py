"""ACP adapter outcome classification."""

from cpipe.runtime.adapters.acp import AcpAdapter
from cpipe.runtime.config import parse_config
from cpipe.runtime.result import Status


def adapter(error, stderr):
    lane = parse_config({"lanes": {"c": {"assignee": "cp:c", "adapter": "acp",
                                         "agent": "codex"}}}).lanes["cp:c"]
    a = AcpAdapter(lane)
    a._error, a._stderr_tail = error, list(stderr)
    return a


def test_error_classification_ignores_earlier_stderr_noise():
    # Real codex-acp run: MCP servers wanting OAuth and a model catalog mentioning
    # "unauthorized" precede the actual (non-auth) failure.
    noise = ['ERROR rmcp: AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer" })',
             'models: {"guardian": "authorize a previously unauthorized action"}',
             'ERROR codex_acp: {"status":400,"message":"model is not supported"}']
    r = adapter(RuntimeError("Internal error"), noise).collect_result()
    assert r.status is Status.FAILED
    assert "model is not supported" in r.summary


def test_error_classification_uses_final_stderr_line():
    r = adapter(RuntimeError("Internal error"), ["boot", "Error: not logged in", ""]).collect_result()
    assert r.status is Status.UNAVAILABLE
    r = adapter(RuntimeError("Internal error"), ["boot", "429 Too Many Requests"]).collect_result()
    assert r.status is Status.RATE_LIMITED
