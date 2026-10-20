"""White-box tests (W5, W6, W12): media embedding, HTML export, full-text search."""
import base64

import pytest

from backend import export, utils
from backend.export import (
    convert_wikilinks_to_html,
    embed_images_as_base64,
    find_media_in_attachments,
    generate_export_html,
    get_image_as_base64,
    get_media_as_base64,
    load_markdown_preview_css,
    process_media_for_export,
)

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\nfake"
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "vault"
    (r / "sub" / "deep").mkdir(parents=True)
    return r


def put(path, data=PNG):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


#  W5: find_media_in_attachments
def test_find_same_folder(root):
    p = put(root / "sub" / "i.png")
    assert find_media_in_attachments("i.png", root / "sub", root) == p.resolve()


def test_find_lookup_order_prefers_note_folder(root):
    near = put(root / "sub" / "i.png")
    put(root / "sub" / "_attachments" / "i.png")
    put(root / "_attachments" / "i.png")
    assert find_media_in_attachments("i.png", root / "sub", root) == near.resolve()


def test_find_note_attachments_then_root_attachments(root):
    a = put(root / "sub" / "_attachments" / "a.png")
    b = put(root / "_attachments" / "b.png")
    assert find_media_in_attachments("a.png", root / "sub", root) == a.resolve()
    assert find_media_in_attachments("b.png", root / "sub", root) == b.resolve()


def test_find_parent_folder_attachments(root):
    p = put(root / "sub" / "_attachments" / "p.png")
    assert find_media_in_attachments("p.png", root / "sub" / "deep", root) == p.resolve()


def test_find_fallback_other_folders_attachments(root):
    p = put(root / "elsewhere" / "_attachments" / "o.png")
    assert find_media_in_attachments("o.png", root / "sub", root) == p.resolve()


def test_find_missing_returns_none(root):
    assert find_media_in_attachments("nope.png", root / "sub", root) is None


def test_find_refuses_file_outside_vault(root):
    put(root.parent / "secret.png")
    assert find_media_in_attachments("../secret.png", root, root) is None
    assert find_media_in_attachments("../../vault/../secret.png", root / "sub", root) is None


def test_find_directory_is_not_media(root):
    (root / "sub" / "dir.png").mkdir()
    assert find_media_in_attachments("dir.png", root / "sub", root) is None


# - W5: base64 helpers
def test_get_media_as_base64_image(root):
    p = put(root / "i.png")
    assert get_media_as_base64(p) == (PNG_URL, "image")
    assert get_image_as_base64(p) == PNG_URL


def test_get_media_as_base64_non_image_is_not_returned_as_image(root):
    p = put(root / "s.mp3", b"id3")
    url, kind = get_media_as_base64(p)
    assert kind == "audio" and url.startswith("data:audio")
    assert get_image_as_base64(p) is None


def test_get_media_as_base64_drawing_counts_as_image(root):
    p = put(root / "drawing-1.png")
    assert get_media_as_base64(p)[1] == "drawing"
    assert get_image_as_base64(p) == PNG_URL


@pytest.mark.parametrize("name", ["missing.png", "file.unknownext", "noext"])
def test_get_media_as_base64_none(root, name):
    p = root / name
    if name != "missing.png":
        put(p)
    assert get_media_as_base64(p) is None


def test_get_media_as_base64_directory(root):
    (root / "d.png").mkdir()
    assert get_media_as_base64(root / "d.png") is None


#  W5: process_media_for_export
def run(md, root, folder=None):
    return process_media_for_export(md, folder or root, root)


def test_markdown_image_embedded(root):
    put(root / "i.png")
    assert run("![cap](i.png)", root) == f"![cap]({PNG_URL})"


def test_markdown_image_empty_alt_uses_stem(root):
    put(root / "pic.png")
    assert run("![](pic.png)", root) == f"![pic]({PNG_URL})"


def test_markdown_image_relative_to_note_folder(root):
    put(root / "sub" / "i.png")
    assert PNG_URL in run("![a](i.png)", root, root / "sub")


def test_markdown_image_api_media_path(root):
    put(root / "sub" / "i.png")
    assert PNG_URL in run("![a](/api/media/sub/i.png)", root)
    assert PNG_URL in run("![a](/api/images/sub/i.png)", root)


def test_markdown_image_found_via_attachments(root):
    put(root / "_attachments" / "i.png")
    assert PNG_URL in run("![a](gone/i.png)", root, root / "sub")


@pytest.mark.parametrize("md", [
    "![a](http://x.com/i.png)",
    "![a](https://x.com/i.png)",
    "![a](data:image/png;base64,AAAA)",
    "![a](missing.png)",
    "![a]()" ,
])
def test_markdown_image_left_alone(root, md):
    assert run(md, root) == md


def test_markdown_image_outside_vault_not_embedded(root):
    put(root.parent / "secret.png")
    for md in ("![a](../secret.png)", "![a](/api/media/../secret.png)"):
        assert run(md, root) == md


def test_markdown_audio_video_pdf_become_placeholders(root):
    for name, label in (("a.mp3", "Audio"), ("v.mp4", "Video"), ("d.pdf", "PDF")):
        out = run(f"![x]({name})", root)
        assert label in out and "<div" in out


def test_markdown_external_pdf_becomes_link(root):
    out = run('![My "Doc"](https://x.com/f.pdf)', root)
    assert 'href="https://x.com/f.pdf"' in out and "&quot;Doc&quot;" in out and 'rel="noopener' in out


def test_wikilink_image_embedded(root):
    put(root / "i.png")
    assert run("![[i.png]]", root) == f"![i]({PNG_URL})"
    assert run("![[i.png|my alt]]", root) == f"![my alt]({PNG_URL})"


def test_wikilink_image_size_emits_img_tag(root):
    put(root / "i.png")
    out = run("![[i.png|100x50]]", root)
    assert out.startswith("<img ") and 'width="100"' in out and 'height="50"' in out
    assert "width:100px;height:50px" in out
    out = run("![[i.png|0x50]]", root)
    assert "width=" not in out and 'height="50"' in out


def test_wikilink_image_size_with_alt_is_escaped(root):
    put(root / "i.png")
    out = run("![[i.png|<b>&|80]]", root)
    assert 'alt="&lt;b&gt;&amp;"' in out and "<b>" not in out


def test_wikilink_missing_image_placeholder(root):
    out = run("![[ghost.png]]", root)
    assert "Image not found" in out and "ghost" in out


def test_wikilink_non_image_media_placeholder_ignores_size(root):
    out = run("![[song.mp3|120]]", root)
    assert "Audio file" in out and "120" not in out


def test_wikilink_traversal_not_embedded(root):
    put(root.parent / "secret.png")
    assert PNG_URL not in run("![[../secret.png]]", root)


def test_mixed_content_and_non_images_untouched(root):
    put(root / "i.png")
    md = "text [[link]] and [l](n.md)\n![[i.png]]\n![a](i.png)\n"
    out = run(md, root)
    assert out.count(PNG_URL) == 2 and "[[link]]" in out and "[l](n.md)" in out


def test_embed_images_alias(root):
    put(root / "i.png")
    assert embed_images_as_base64("![a](i.png)", root, root) == run("![a](i.png)", root)


# - W5: wikilinks to html
def test_wikilink_html_escapes_and_alias():
    out = convert_wikilinks_to_html('[[a"><script>|<i>x</i>]]')
    assert "<script>" not in out and "&lt;i&gt;" in out and "&quot;" in out


def test_wikilink_html_skips_image_embeds_and_code():
    s = "![[img.png]] `[[c]]` ```\n[[b]]\n``` [[ok]]"
    out = convert_wikilinks_to_html(s)
    assert "![[img.png]]" in out and "`[[c]]`" in out and "[[b]]" in out
    assert out.count('class="wikilink"') == 1


def test_wikilink_html_restores_multiple_code_spans_in_order():
    s = "`one` [[x]] `two` [[y]]"
    out = convert_wikilinks_to_html(s)
    assert out.index("`one`") < out.index("two") and out.count('class="wikilink"') == 2


# ------------------ W6
def test_export_html_escapes_title_and_embeds_theme():
    html = generate_export_html('</title><script>x</script>', "# hi", ":root{--zz:1}")
    assert "<script>x</script>" not in html and "&lt;/title&gt;" in html
    assert ":root{--zz:1}" in html


def test_export_html_escapes_content_for_js_template():
    html = generate_export_html("t", "a`b ${x} \\ </script>", "")
    assert "a\\`b \\${x} \\\\ <\\/script>" in html


def test_export_html_dark_vs_light():
    assert "github-dark" in generate_export_html("t", "", "", is_dark=True)
    light = generate_export_html("t", "", "")
    assert "github-dark" not in light and "theme: 'default'" in light


def test_export_html_assets_source():
    assert "/static/vendor/marked" in generate_export_html("t", "", "", local_assets=True)
    cdn = generate_export_html("t", "", "")
    assert "cdn.jsdelivr.net" in cdn and "/static/vendor/" not in cdn


def test_export_html_print_toolbar_toggle():
    assert "window.print()" in generate_export_html("t", "", "", show_print_button=True)
    assert "window.print()" not in generate_export_html("t", "", "")


def test_preview_css_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(export, "_MARKDOWN_PREVIEW_CSS_PATH", tmp_path / "none.css")
    assert load_markdown_preview_css() == ""
    assert "<html" in generate_export_html("t", "x", "")  # export still works


def test_preview_css_is_inlined(monkeypatch, tmp_path):
    css = tmp_path / "p.css"
    css.write_text(".marker-xyz{}")
    monkeypatch.setattr(export, "_MARKDOWN_PREVIEW_CSS_PATH", css)
    assert ".marker-xyz{}" in generate_export_html("t", "", "")


# ------------ W12: search_notes
@pytest.fixture
def search(vault, make_note):
    def _s(q):
        return utils.search_notes(str(vault), q)
    return _s


def paths(results):
    return [r["path"] for r in results]


def test_search_finds_case_insensitive_with_snippet(search, make_note):
    make_note("a.md", "line one\nsome Hello World here\n")
    r = search("hello world")
    assert paths(r) == ["a.md"]
    m = r[0]["matches"][0]
    assert m["line_number"] == 2 and '<mark class="search-highlight">Hello World</mark>' in m["context"]
    assert r[0]["name"] == "a" and r[0]["folder"] == ""


def test_search_no_match(search, make_note):
    make_note("a.md", "nothing here")
    assert search("zebra") == []


def test_search_empty_vault(search):
    assert search("anything") == []


def test_search_substring_inside_word(search, make_note):
    make_note("a.md", "wonderful features")
    assert paths(search("feat")) == ["a.md"]


def test_search_multi_term_requires_exact_phrase(search, make_note):
    make_note("a.md", "alpha beta")
    make_note("b.md", "beta ... alpha")
    assert paths(search("alpha beta")) == ["a.md"]


def test_search_short_and_symbol_queries_fall_back_to_full_scan(search, make_note):
    make_note("a.md", "what??? is z")
    make_note("b.md", "plain")
    assert paths(search("???")) == ["a.md"]
    assert paths(search("z")) == ["a.md"]


def test_search_nested_folder_and_ordering_newest_first(search, make_note):
    import os
    old = make_note("old.md", "term")
    new = make_note("f/new.md", "term")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    utils._scan_cache_invalidate()
    from backend import note_index
    note_index.get_index().invalidate()
    r = search("term")
    assert paths(r) == ["f/new.md", "old.md"] and r[0]["folder"] == "f"


def test_search_snippet_html_escaped_and_truncated(search, make_note):
    make_note("a.md", "<script>alert(1)</script> " + "x" * 50 + " needle " + "y" * 50)
    ctx = search("needle")[0]["matches"][0]["context"]
    assert ctx.startswith("...") and ctx.endswith("...") and "<script>" not in ctx
    ctx2 = search("alert")[0]["matches"][0]["context"]
    assert "&lt;script&gt;" in ctx2


def test_search_caps_matches_per_note_at_three(search, make_note):
    make_note("a.md", "hit\n" * 10)
    assert len(search("hit")[0]["matches"]) == 3


def test_search_regex_chars_in_query_are_literal(search, make_note):
    make_note("a.md", "cost (a+b)* done")
    assert paths(search("(a+b)*")) == ["a.md"]


def test_search_ignores_media(search, make_note, vault):
    make_note("a.md", "x")
    (vault / "pic.png").write_bytes(b"needle")
    assert search("needle") == []


def test_search_sees_new_note_after_index_built(search, make_note, vault):
    make_note("a.md", "first")
    assert search("first")
    utils.save_note(str(vault), "b.md", "second thing")
    assert paths(search("second")) == ["b.md"]
    utils.delete_note(str(vault), "b.md")
    assert search("second") == []


def test_search_unreadable_note_is_skipped(search, make_note):
    p = make_note("bad.md", "ok")
    make_note("good.md", "ok")
    search("ok")  # build the index
    p.write_bytes(b"\xff\xfe ok \x80")  # no longer valid utf-8
    assert paths(search("ok")) == ["good.md"]
