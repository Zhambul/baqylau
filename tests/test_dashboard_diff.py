from dashboard.render.diff import source_html, unified_diff_html


def test_unified_diff_view_has_numbered_colored_code_without_patch_headers():
    rendered = unified_diff_html(
        """--- /work/example.py
+++ /work/example.py
@@ -4,3 +4,3 @@
 def answer():
-    return old_value
+    return new_value
 trailing_call()
""",
        "/work/example.py",
    )

    assert "--- /work/example.py" not in rendered
    assert "+++ /work/example.py" not in rendered
    assert "@@ -4,3 +4,3 @@" not in rendered
    assert '<div class="dl context"><span class="ln">4</span>' in rendered
    assert '<div class="dl removed"><span class="ln">5</span>' in rendered
    assert '<div class="dl added"><span class="ln">5</span>' in rendered
    assert '<div class="dl context"><span class="ln">6</span>' in rendered
    assert rendered.count('<mark class="changed">') == 2
    assert "old" in rendered
    assert "new" in rendered
    assert "color:rgb(" in rendered


def test_unified_diff_view_separates_hunks_without_showing_hunk_coordinates():
    rendered = unified_diff_html(
        """--- a/example.js
+++ b/example.js
@@ -1 +1 @@
-const first = false;
+const first = true;
@@ -20 +20 @@
-const second = false;
+const second = true;
""",
        "example.js",
    )

    assert rendered.count('class="dl sep"') == 1
    assert "@@" not in rendered
    assert "⋮" in rendered


def test_captured_source_view_is_numbered_and_syntax_highlighted():
    rendered = source_html("def answer():\n    return 42\n", "example.py")

    assert '<span class="ln">1</span>' in rendered
    assert '<span class="ln">2</span>' in rendered
    assert "color:rgb(198,120,221)" in rendered
