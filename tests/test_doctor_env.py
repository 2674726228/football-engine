"""doctor 环境检查：没有 crontab 时不能给出"已验证无残留"的假结论（2026-10-28）。

原实现 `_run(["crontab", "-l"])` 在 Windows 抛 FileNotFoundError，被外层
`except Exception` 吞成 info「crontab 读取失败（无 cron?）」——页面看起来
结论正确，实际检查一次都没执行。修复后必须显式区分：
  - 没有 crontab 命令 → info + 明说"未执行，不等同于已验证无残留"
  - 有 crontab 且无残留 → ok
"""
from __future__ import annotations

from engine import doctor


def _env_findings(monkeypatch, cron_bin, crontab_out=""):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: cron_bin)
    monkeypatch.setattr(doctor, "_run", lambda args, timeout=300: (0, crontab_out))
    # 代理探测不参与本测试，直接视为端口通
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **kw: True)
    report = doctor.DoctorReport()
    doctor.check_env(report, heal=False)
    return [f for f in report.findings if f.id == "env/cron"]


def test_no_crontab_is_reported_as_not_checked(monkeypatch):
    found = _env_findings(monkeypatch, cron_bin=None)
    assert len(found) == 1
    f = found[0]
    assert f.severity == "info"
    assert "未执行" in f.detail and "不等同" in f.detail, f.detail


def test_crontab_present_without_forbidden_jobs_is_ok(monkeypatch):
    found = _env_findings(monkeypatch, cron_bin="/usr/bin/crontab",
                          crontab_out="MAILTO=me\n")
    assert found and found[0].severity == "ok", found[0].detail


def test_forbidden_cron_is_warned(monkeypatch):
    found = _env_findings(monkeypatch, cron_bin="/usr/bin/crontab",
                          crontab_out="5 * * * * auto_run.sh\n")
    assert found and found[0].severity == "warn", found[0].detail
    assert "auto_run.sh" in found[0].detail
