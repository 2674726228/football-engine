"""赛前情报展示（2026-10-23）：外链标题必须转义，来源与免责声明可见。"""
import json

import engine.build_site as bs


def _seed(tmp_path, monkeypatch, items):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    d = tmp_path / "data" / "daily" / "2026-10-22"
    d.mkdir(parents=True)
    counts = {k: sum(1 for it in items if it.get("kind") == k)
              for k in ("injury", "transfer", "other")}
    (d / "news.json").write_text(json.dumps({
        "source": "直播吧足球频道",
        "generated_at": "2026-10-23 01:00:00",
        "counts": counts,
        "items": items,
    }, ensure_ascii=False), encoding="utf-8")


def test_escapes_external_title_and_keeps_disclaimer(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{
        "title": "<script>alert(1)</script> 曼联伤情",
        "url": "https://news.zhibo8.com/zuqiu/2026-10-22/x.htm",
        "teams": ["曼彻斯特联"],
    }])
    html = bs._news_section("2026-10-22")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "未经核实" in html and "直播吧" in html
    assert 'rel="noopener noreferrer"' in html


def test_rejects_non_source_urls(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{
        "title": "正常标题 曼联", "url": "https://evil.example/x", "teams": ["曼彻斯特联"],
    }])
    assert bs._news_section("2026-10-22") == ""


def test_injury_first_and_kind_badge(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"title": "转会传闻 尤文", "url": "https://news.zhibo8.com/zuqiu/2026-10-22/a.htm",
         "teams": ["尤文图斯"], "kind": "transfer"},
        {"title": "尤文伤情：主力缺阵", "url": "https://news.zhibo8.com/zuqiu/2026-10-22/b.htm",
         "teams": ["尤文图斯"], "kind": "injury"},
    ])
    html = bs._news_section("2026-10-22")
    assert "伤病/首发" in html and "转会" in html
    assert html.index("伤病/首发") < html.index("伤情"), "伤病条目必须排在前面"
    assert html.index("伤情") < html.index("转会传闻")
    assert "injury 1" in html and "transfer 1" in html


def test_missing_file_renders_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    assert bs._news_section("2026-10-22") == ""
