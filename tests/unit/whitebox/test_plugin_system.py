"""White-box tests (W23-W26): PluginManager loading, persistence and dispatch, plus the
internals of the note_stats and search_open_tasks plugins."""
import json
import logging
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend import plugins as plugin_module
from backend.plugins import HOOK_SPECS, Plugin as BasePlugin, PluginContext, PluginManager
from plugins import note_stats
from plugins.contrib import search_open_tasks
from plugins.contrib.search_open_tasks import Plugin as OpenTasksPlugin
from plugins.note_stats import Plugin as NoteStatsPlugin

pytestmark = pytest.mark.unit


def plugin_source(body="", enabled=True, name="Fake"):
    """Source of a plugin file. `body` is the indented rest of the class."""
    return (
        "class Plugin:\n"
        "    def __init__(self):\n"
        f"        self.name = {name!r}\n"
        "        self.version = '1.2.3'\n"
        f"        self.enabled = {enabled}\n"
        f"{body}\n"
    )


@pytest.fixture
def log(monkeypatch):
    """Replaces the module logger: caplog depends on what uvicorn did to propagation."""
    mock = Mock()
    monkeypatch.setattr(plugin_module, "logger", mock)
    return mock


def logged(mock_method) -> str:
    return " ".join(str(call.args) for call in mock_method.call_args_list)


# W23: load_plugins
def test_load_missing_dir_creates_it_with_example_plugin(tmp_path):
    target = tmp_path / "new" / "plugins"
    manager = PluginManager(str(target))
    assert (target / "example_plugin.py").exists()
    assert manager.plugins == {}  # the example is written, then picked up on the next start
    assert not (target / "plugin_config.json").exists()


def test_created_example_plugin_loads_on_next_start(tmp_path):
    target = tmp_path / "plugins"
    PluginManager(str(target))
    manager = PluginManager(str(target))
    assert manager.list_plugins() == [
        {"id": "example_plugin", "name": "Example Plugin", "version": "1.0.0", "enabled": True},
    ]


def test_load_empty_dir_writes_no_config(plugins_dir):
    assert PluginManager(str(plugins_dir)).plugins == {}
    assert not (plugins_dir / "plugin_config.json").exists()


def test_load_registers_plugin(plugins_dir, make_plugin):
    make_plugin("good", plugin_source())
    assert list(PluginManager(str(plugins_dir)).plugins) == ["good"]


def test_load_skips_underscore_files(plugins_dir, make_plugin):
    make_plugin("_private", plugin_source())
    assert PluginManager(str(plugins_dir)).plugins == {}


def test_load_ignores_module_without_plugin_class(plugins_dir, make_plugin):
    make_plugin("helper", "VALUE = 1\n")
    assert PluginManager(str(plugins_dir)).plugins == {}


def test_load_ignores_non_python_files(plugins_dir):
    (plugins_dir / "notes.txt").write_text(plugin_source())
    assert PluginManager(str(plugins_dir)).plugins == {}


@pytest.mark.parametrize("source", [
    "def broken(:\n",  # syntax error
    "raise RuntimeError('at import')\n",
    "class Plugin:\n    def __init__(self):\n        raise RuntimeError('in init')\n",
])
def test_load_broken_plugin_skipped_others_still_load(plugins_dir, make_plugin, log, source):
    make_plugin("a_broken", source)
    make_plugin("b_good", plugin_source())
    assert list(PluginManager(str(plugins_dir)).plugins) == ["b_good"]
    assert "a_broken" in logged(log.error)


def test_load_order_is_sorted_by_file_name(plugins_dir, make_plugin):
    for name in ("c", "a", "b"):
        make_plugin(name, plugin_source())
    assert list(PluginManager(str(plugins_dir)).plugins) == ["a", "b", "c"]


def test_load_skips_file_without_import_spec(plugins_dir, make_plugin, monkeypatch):
    make_plugin("p", plugin_source())
    monkeypatch.setattr(plugin_module.importlib.util, "spec_from_file_location", lambda *_: None)
    assert PluginManager(str(plugins_dir)).plugins == {}


# W23: base Plugin class
def test_base_plugin_defaults_are_harmless():
    plugin = BasePlugin()
    assert (plugin.name, plugin.version, plugin.enabled) == ("Base Plugin", "1.0.0", False)
    assert plugin.setup(Mock()) is None
    assert plugin.get_routes() is None
    assert plugin.on_note_save("n.md", "c") is None
    assert plugin.on_note_delete("n.md") is None
    assert plugin.on_search("q", []) is None
    assert plugin.on_note_create("n.md", "initial") == "initial"
    assert plugin.on_note_load("n.md", "c") is None
    assert plugin.on_app_startup() is None


# W23: _warn_on_unknown_hooks
def test_unknown_hook_warns(plugins_dir, make_plugin, log):
    make_plugin("typo", plugin_source("    def on_note_saved(self, **kw):\n        pass\n"))
    PluginManager(str(plugins_dir))
    assert "on_note_saved" in logged(log.warning)


def test_known_hooks_and_non_callable_attributes_do_not_warn(plugins_dir, make_plugin, log):
    make_plugin("ok", plugin_source(
        "    on_label = 'not a method'\n"
        "    def on_note_save(self, note_path, content):\n"
        "        return None\n"
    ))
    PluginManager(str(plugins_dir))
    log.warning.assert_not_called()


def test_unknown_hook_check_survives_broken_dir(plugins_dir, log):
    class Hostile:
        def __dir__(self):
            raise RuntimeError("no listing")

    PluginManager(str(plugins_dir))._warn_on_unknown_hooks("hostile", Hostile())
    log.warning.assert_not_called()


# W23: _setup_plugin
def test_setup_receives_context(tmp_path, plugins_dir):
    config = {"k": "v"}
    manager = PluginManager(str(plugins_dir), notes_dir=str(tmp_path), config=config)
    plugin = Mock()

    manager._setup_plugin("my_id", plugin)

    ctx = plugin.setup.call_args.args[0]
    assert isinstance(ctx, PluginContext)
    assert (ctx.notes_dir, ctx.plugins_dir, ctx.config) == (tmp_path, plugins_dir, config)
    assert ctx.logger.name == "uvicorn.error.plugins.my_id"


def test_setup_without_notes_dir_uses_current_dir(plugins_dir):
    plugin = Mock()
    PluginManager(str(plugins_dir))._setup_plugin("p", plugin)
    assert str(plugin.setup.call_args.args[0].notes_dir) == "."


def test_setup_called_during_load(tmp_path, plugins_dir, make_plugin):
    make_plugin("p", plugin_source(
        "    def setup(self, ctx):\n"
        "        self.seen = ctx.notes_dir\n"
    ))
    manager = PluginManager(str(plugins_dir), notes_dir=str(tmp_path))
    assert manager.plugins["p"].seen == tmp_path


def test_plugin_without_setup_still_loads(plugins_dir, make_plugin):
    make_plugin("p", plugin_source())
    assert "p" in PluginManager(str(plugins_dir)).plugins


def test_setup_raising_is_logged_and_plugin_stays_loaded(plugins_dir, make_plugin, log):
    make_plugin("p", plugin_source("    def setup(self, ctx):\n        raise RuntimeError('boom')\n"))
    assert "p" in PluginManager(str(plugins_dir)).plugins
    assert "boom" in logged(log.error)


# W23: list_plugins, enable_plugin, disable_plugin, config persistence
def test_list_plugins(plugins_dir, make_plugin):
    make_plugin("p", plugin_source(name="Nice Name", enabled=False))
    assert PluginManager(str(plugins_dir)).list_plugins() == [
        {"id": "p", "name": "Nice Name", "version": "1.2.3", "enabled": False},
    ]


def test_config_written_on_start(plugins_dir, make_plugin):
    make_plugin("p", plugin_source())
    PluginManager(str(plugins_dir))
    assert json.loads((plugins_dir / "plugin_config.json").read_text()) == {"p": True}


def test_disable_enable_persisted_across_restart(plugins_dir, make_plugin):
    make_plugin("p", plugin_source(enabled=True))
    manager = PluginManager(str(plugins_dir))

    manager.disable_plugin("p")
    assert manager.plugins["p"].enabled is False
    assert PluginManager(str(plugins_dir)).plugins["p"].enabled is False

    manager.enable_plugin("p")
    assert manager.plugins["p"].enabled is True
    assert PluginManager(str(plugins_dir)).plugins["p"].enabled is True


def test_toggle_unknown_plugin_is_noop(plugins_dir, make_plugin):
    make_plugin("p", plugin_source())
    manager = PluginManager(str(plugins_dir))
    manager.enable_plugin("ghost")
    manager.disable_plugin("ghost")
    assert json.loads(manager.config_file.read_text()) == {"p": True}


def test_saved_state_for_removed_plugin_ignored_and_dropped(plugins_dir, make_plugin):
    make_plugin("p", plugin_source(enabled=True))
    (plugins_dir / "plugin_config.json").write_text(json.dumps({"gone": True, "p": False}))
    manager = PluginManager(str(plugins_dir))
    assert manager.plugins["p"].enabled is False
    assert json.loads(manager.config_file.read_text()) == {"p": False}


def test_load_config_missing_file(plugins_dir):
    assert PluginManager(str(plugins_dir))._load_config() == {}


def test_load_config_corrupt_file_falls_back_to_defaults(plugins_dir, make_plugin, log):
    make_plugin("p", plugin_source(enabled=True))
    (plugins_dir / "plugin_config.json").write_text("{not json")
    manager = PluginManager(str(plugins_dir))
    assert manager.plugins["p"].enabled is True
    assert "Failed to load plugin config" in logged(log.error)


def test_save_config_failure_is_logged_not_raised(plugins_dir, make_plugin, log):
    make_plugin("p", plugin_source())
    manager = PluginManager(str(plugins_dir))
    manager.config_file = plugins_dir / "no" / "such" / "dir" / "config.json"
    manager._save_config()
    assert "Failed to save plugin config" in logged(log.error)


# W23: build_router
ROUTED_PLUGIN = plugin_source(
    "    def get_routes(self):\n"
    "        from fastapi import APIRouter\n"
    "        router = APIRouter()\n"
    "        @router.get('/ping')\n"
    "        def ping():\n"
    "            return {'pong': True}\n"
    "        return router\n"
)


def client_for(manager):
    app = FastAPI()
    app.include_router(manager.build_router(), prefix="/api")
    return TestClient(app)


def test_router_mounts_plugin_routes_under_plugin_id(plugins_dir, make_plugin):
    make_plugin("routed", ROUTED_PLUGIN)
    response = client_for(PluginManager(str(plugins_dir))).get("/api/plugins/routed/ping")
    assert response.status_code == 200 and response.json() == {"pong": True}


def test_router_mounted_even_for_disabled_plugin(plugins_dir, make_plugin):
    make_plugin("routed", ROUTED_PLUGIN)
    manager = PluginManager(str(plugins_dir))
    manager.disable_plugin("routed")
    assert client_for(manager).get("/api/plugins/routed/ping").status_code == 200


def test_router_empty_without_plugins(plugins_dir):
    assert PluginManager(str(plugins_dir)).build_router().routes == []


@pytest.mark.parametrize("body,message", [
    ("", None),  # no get_routes at all
    ("    def get_routes(self):\n        return None\n", None),
    ("    def get_routes(self):\n        raise RuntimeError('boom')\n", "failed to provide routes"),
    ("    def get_routes(self):\n        return 'not a router'\n", "expected APIRouter"),
])
def test_router_skips_plugins_without_usable_routes(plugins_dir, make_plugin, log, body, message):
    make_plugin("bad", plugin_source(body))
    make_plugin("routed", ROUTED_PLUGIN)
    manager = PluginManager(str(plugins_dir))

    client = client_for(manager)

    assert client.get("/api/plugins/routed/ping").status_code == 200
    assert client.get("/api/plugins/bad/ping").status_code == 404
    if message:
        assert message in str(log.error.call_args_list)
    else:
        log.error.assert_not_called()


# W24: dispatch
def hook(body):
    return f"    def on_note_save(self, note_path, content):\n        {body}\n"


def manager_with(plugins_dir, make_plugin, *bodies, method=hook):
    """One plugin per body, named p0, p1, ... so load order follows the arguments."""
    for i, body in enumerate(bodies):
        make_plugin(f"p{i}", plugin_source(method(body), name=f"P{i}"))
    return PluginManager(str(plugins_dir))


def test_dispatch_unknown_hook(plugins_dir, make_plugin, log):
    manager = manager_with(plugins_dir, make_plugin, "return 'changed'")
    assert manager.dispatch("on_nothing", content="x") is None
    assert "on_nothing" in logged(log.error)


def test_dispatch_without_plugins_returns_subject(plugins_dir):
    assert PluginManager(str(plugins_dir)).dispatch("on_note_save", note_path="n", content="x") == "x"


def test_dispatch_chains_subject_in_load_order(plugins_dir, make_plugin):
    manager = manager_with(plugins_dir, make_plugin, "return content + '1'", "return content + '2'")
    assert manager.dispatch("on_note_save", note_path="n", content="x") == "x12"


def test_dispatch_none_keeps_value(plugins_dir, make_plugin):
    manager = manager_with(plugins_dir, make_plugin, "return None", "return content + '2'", "return None")
    assert manager.dispatch("on_note_save", note_path="n", content="x") == "x2"


def test_dispatch_wrong_return_type_ignored(plugins_dir, make_plugin, log):
    manager = manager_with(plugins_dir, make_plugin, "return 123", "return content + '2'")
    assert manager.dispatch("on_note_save", note_path="n", content="x") == "x2"
    assert "expected" in logged(log.error) and "'int'" in logged(log.error)


def test_dispatch_list_subject_checks_list_type(plugins_dir, make_plugin):
    def search_hook(body):
        return f"    def on_search(self, query, results):\n        {body}\n"

    manager = manager_with(plugins_dir, make_plugin, "return 'nope'", "return results + [query]", method=search_hook)
    assert manager.dispatch("on_search", query="q", results=["a"]) == ["a", "q"]


def test_dispatch_raising_plugin_skipped_keeps_last_good_value(plugins_dir, make_plugin, log):
    manager = manager_with(
        plugins_dir, make_plugin,
        "return content + '1'",
        "raise RuntimeError('boom')",
        "return content + '3'",
    )
    assert manager.dispatch("on_note_save", note_path="n", content="x") == "x13"
    assert "boom" in logged(log.error)


def test_dispatch_disabled_plugin_skipped(plugins_dir, make_plugin):
    manager = manager_with(plugins_dir, make_plugin, "return content + '1'", "return content + '2'")
    manager.disable_plugin("p0")
    assert manager.dispatch("on_note_save", note_path="n", content="x") == "x2"


def test_dispatch_plugin_without_that_hook_skipped(plugins_dir, make_plugin):
    make_plugin("nohook", plugin_source())
    assert PluginManager(str(plugins_dir)).dispatch("on_note_save", note_path="n", content="x") == "x"


def test_dispatch_non_callable_hook_attribute_skipped(plugins_dir, make_plugin):
    make_plugin("attr", plugin_source("    on_note_save = 'not callable'\n"))
    assert PluginManager(str(plugins_dir)).dispatch("on_note_save", note_path="n", content="x") == "x"


def test_dispatch_observe_only_hook_runs_and_returns_none(plugins_dir, make_plugin):
    assert HOOK_SPECS["on_note_delete"].subject is None
    manager = manager_with(
        plugins_dir, make_plugin,
        "self.deleted = note_path; return 'ignored'",
        method=lambda b: f"    def on_note_delete(self, note_path):\n        {b}\n",
    )
    assert manager.dispatch("on_note_delete", note_path="gone.md") is None
    assert manager.plugins["p0"].deleted == "gone.md"


def test_dispatch_observe_only_hook_survives_raising_plugin(plugins_dir, make_plugin, log):
    manager = manager_with(
        plugins_dir, make_plugin,
        "raise RuntimeError('boom')",
        "self.deleted = note_path",
        method=lambda b: f"    def on_note_delete(self, note_path):\n        {b}\n",
    )
    assert manager.dispatch("on_note_delete", note_path="gone.md") is None
    assert manager.plugins["p1"].deleted == "gone.md"
    assert "boom" in logged(log.error)


def test_dispatch_startup_hook_takes_no_arguments(plugins_dir, make_plugin):
    manager = manager_with(
        plugins_dir, make_plugin,
        "self.started = True",
        method=lambda b: f"    def on_app_startup(self):\n        {b}\n",
    )
    assert manager.dispatch("on_app_startup") is None
    assert manager.plugins["p0"].started is True


def test_dispatch_on_note_create_subject_is_initial_content(plugins_dir, make_plugin):
    manager = manager_with(
        plugins_dir, make_plugin,
        "return initial_content + '!'",
        method=lambda b: f"    def on_note_create(self, note_path, initial_content):\n        {b}\n",
    )
    assert manager.dispatch("on_note_create", note_path="n", initial_content="x") == "x!"


# W24: deprecated wrappers
def test_run_hook_returns_value_only_when_content_passed(plugins_dir, make_plugin):
    manager = manager_with(plugins_dir, make_plugin, "return content + '!'")
    assert manager.run_hook("on_note_save", note_path="n", content="x") == "x!"
    # on_search has a subject, but run_hook only returns for 'content'.
    assert manager.run_hook("on_search", query="q", results=["a"]) is None


def test_run_hook_with_return_delegates_to_dispatch(plugins_dir, make_plugin):
    manager = manager_with(plugins_dir, make_plugin, "return content + '!'")
    assert manager.run_hook_with_return("on_note_save", note_path="n", content="x") == "x!"
    assert manager.run_hook_with_return("on_search", query="q", results=["a"]) == ["a"]


# W25: search_open_tasks._blank_non_task_regions
@pytest.fixture
def tasks():
    return OpenTasksPlugin()


def test_blank_keeps_plain_content(tasks):
    assert tasks._blank_non_task_regions("- [ ] a\ntext") == "- [ ] a\ntext"


def test_blank_frontmatter(tasks):
    assert tasks._blank_non_task_regions("---\n- [ ] meta\n---\n- [ ] real") == "\n\n\n- [ ] real"


def test_blank_unterminated_frontmatter_stays_readable(tasks):
    assert tasks._blank_non_task_regions("---\n- [ ] body") == "---\n- [ ] body"


def test_blank_frontmatter_must_start_on_first_line(tasks):
    content = "\n---\n- [ ] body\n---"
    assert tasks._blank_non_task_regions(content) == content


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "   ```"])
def test_blank_fenced_block_and_keeps_line_count(tasks, fence):
    content = f"before\n{fence}\n- [ ] in fence\n{fence}\nafter"
    assert tasks._blank_non_task_regions(content) == "before\n\n\n\nafter"


def test_blank_unclosed_fence_runs_to_end(tasks):
    assert tasks._blank_non_task_regions("```\n- [ ] a\n- [ ] b") == "\n\n"


@pytest.mark.parametrize("closer", [
    "~~~",  # other character
    "``",  # not a fence at all
    "```python",  # text after the closer
])
def test_blank_invalid_closer_keeps_fence_open(tasks, closer):
    assert tasks._blank_non_task_regions(f"```\n{closer}\n- [ ] still inside") == "\n\n"


def test_blank_closer_shorter_than_opener_keeps_fence_open(tasks):
    assert tasks._blank_non_task_regions("````\n```\n- [ ] inside\n````\n- [ ] out") == "\n\n\n\n- [ ] out"


def test_blank_closer_longer_than_opener_closes(tasks):
    assert tasks._blank_non_task_regions("```\n`````\n- [ ] out") == "\n\n- [ ] out"


def test_blank_fence_indented_four_spaces_is_not_a_fence(tasks):
    content = "    ```\n- [ ] real"
    assert tasks._blank_non_task_regions(content) == content


# W25: _open_tasks
def test_open_tasks_skips_fence_and_frontmatter_keeps_line_numbers(tasks):
    content = "---\ntags: x\n---\n```\n- [ ] no\n```\n- [ ] yes"
    matches = tasks._open_tasks(content)
    assert [m["line_number"] for m in matches] == [7]
    assert "yes" in matches[0]["context"]


def test_open_tasks_long_text_truncated(tasks):
    text = "a" * 250
    context = tasks._open_tasks(f"- [ ] {text}")[0]["context"]
    assert "a" * 200 + "…" in context and "a" * 201 not in context


def test_open_tasks_text_at_limit_not_truncated(tasks):
    assert "…" not in tasks._open_tasks("- [ ] " + "a" * 200)[0]["context"]


def test_open_tasks_html_escaped(tasks):
    context = tasks._open_tasks("- [ ] <b>bold</b>")[0]["context"]
    assert context == '☐ <mark class="search-highlight">&lt;b&gt;bold&lt;/b&gt;</mark>'


# W25: setup, _scan_vault
@pytest.fixture
def vault_tasks(tmp_path, monkeypatch):
    # setup() replaces the module-global logger; monkeypatch restores it after the test.
    monkeypatch.setattr(search_open_tasks, "logger", search_open_tasks.logger)
    plugin = OpenTasksPlugin()
    plugin.setup(Mock(notes_dir=tmp_path, logger=Mock()))
    return plugin


def write(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_setup_takes_vault_and_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(search_open_tasks, "logger", search_open_tasks.logger)
    custom = Mock()
    plugin = OpenTasksPlugin()
    plugin.setup(Mock(notes_dir=tmp_path, logger=custom))
    assert plugin.notes_dir == tmp_path
    assert search_open_tasks.logger is custom


def test_scan_vault_result_shape(vault_tasks, tmp_path):
    write(tmp_path, "root.md", "- [ ] a")
    write(tmp_path, "sub/deep/n.md", "- [ ] b\n- [ ] c")
    notes = {n["path"]: n for n in vault_tasks._scan_vault()}
    assert set(notes) == {"root.md", "sub/deep/n.md"}
    assert (notes["root.md"]["name"], notes["root.md"]["folder"]) == ("root", "")
    assert (notes["sub/deep/n.md"]["name"], notes["sub/deep/n.md"]["folder"]) == ("n", "sub/deep")
    assert len(notes["sub/deep/n.md"]["matches"]) == 2


def test_scan_vault_skips_what_core_scan_skips(vault_tasks, tmp_path):
    write(tmp_path, ".obsidian/hidden.md", "- [ ] a")
    write(tmp_path, ".dotfile.md", "- [ ] a")
    write(tmp_path, "other.txt", "- [ ] a")
    write(tmp_path, "done.md", "- [x] finished")
    assert vault_tasks._scan_vault() == []


def test_scan_vault_unreadable_note_skips_itself(vault_tasks, tmp_path):
    (tmp_path / "binary.md").write_bytes(b"\xff\xfe\xff")
    write(tmp_path, "ok.md", "- [ ] a")
    assert [n["path"] for n in vault_tasks._scan_vault()] == ["ok.md"]


# W25: on_search
@pytest.mark.parametrize("query", ["hello", "", "@taskss", "task", "@ tasks"])
def test_on_search_other_query_returns_none(vault_tasks, tmp_path, query):
    write(tmp_path, "n.md", "- [ ] a")
    assert vault_tasks.on_search(query, [{"keep": True}]) is None


@pytest.mark.parametrize("query", ["@task", "@tasks", "@TASKS", "  @Tasks  "])
def test_on_search_trigger_replaces_results(vault_tasks, tmp_path, query):
    write(tmp_path, "n.md", "- [ ] a")
    assert [n["path"] for n in vault_tasks.on_search(query, [{"old": True}])] == ["n.md"]


def test_on_search_sorted_by_lowercase_path(vault_tasks, tmp_path):
    for name in ("Zeta.md", "alpha.md", "Mid.md"):
        write(tmp_path, name, "- [ ] a")
    assert [n["path"] for n in vault_tasks.on_search("@tasks", [])] == ["alpha.md", "Mid.md", "Zeta.md"]


def test_on_search_no_tasks_returns_empty_list_not_none(vault_tasks, tmp_path):
    write(tmp_path, "n.md", "nothing")
    assert vault_tasks.on_search("@tasks", [{"old": True}]) == []


# W26: note_stats._count_tasks
@pytest.fixture
def stats():
    return NoteStatsPlugin()


@pytest.mark.parametrize("content,expected", [
    ("", (0, 0)),
    ("plain text", (0, 0)),
    ("- [ ] a\n- [x] b\n- [X] c", (3, 2)),
    ("* [ ] a\n+ [x] b\n1. [ ] c\n2) [x] d", (4, 2)),
    ("  - [ ] nested\n> - [x] quoted\n- - [ ] double", (3, 1)),
    ("- [] no space\n- [ ]no gap\n[ ] no bullet", (0, 0)),
    ("```\n- [ ] in code\n```\n- [ ] real", (1, 0)),
    ("~~~\n- [x] in code\n~~~\n- [x] real", (1, 1)),
    ("  ```\n- [ ] in indented fence\n  ```", (0, 0)),
    ("```\n- [ ] unclosed fence", (0, 0)),
    ("```\n~~~\n- [ ] other char keeps it open\n```", (0, 0)),
    ("````\n```\n- [ ] short closer keeps it open\n````\n- [ ] real", (1, 0)),
    ("---\n- [ ] meta\n---\n- [x] real", (1, 1)),
    ("---\n- [ ] unterminated counts as body", (1, 0)),
    ("\n---\n- [ ] not frontmatter\n---", (1, 0)),
])
def test_count_tasks(stats, content, expected):
    assert stats._count_tasks(content) == expected


# W26: on_note_save, setup
@pytest.fixture
def stats_log(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(note_stats, "logger", mock)
    return mock


def summary(stats_log):
    return stats_log.info.call_args.args[2]


def test_on_note_save_returns_none(stats, stats_log):
    assert stats.on_note_save("n.md", "content") is None


def test_on_note_save_minimal_summary(stats, stats_log):
    stats.on_note_save("n.md", "one two.")
    assert stats_log.info.call_args.args[1] == "n.md"
    assert summary(stats_log) == "2 words | 1 sentences | ~1m read | 1 lines"


def test_on_note_save_optional_parts(stats, stats_log):
    content = (
        "- item\n"
        "- [x] done\n"
        "- [ ] open\n"
        "[[wiki]] [ext](https://e.com)\n"
        "| a |\n"
        "| --- |\n"
    )
    stats.on_note_save("n.md", content)
    text = summary(stats_log)
    assert "1 lists" in text
    assert "1 tables" in text
    assert "2 links (1 internal)" in text
    assert "1/2 tasks" in text


def test_on_note_save_large_numbers_grouped(stats, stats_log):
    stats.on_note_save("n.md", "word " * 1000)
    assert summary(stats_log).startswith("1,000 words")


def test_observe_only_plugin_leaves_content_through_dispatch(plugins_dir, stats_log):
    manager = PluginManager(str(plugins_dir))
    manager.plugins["note_stats"] = NoteStatsPlugin()
    assert manager.dispatch("on_note_save", note_path="n.md", content="x") == "x"
    stats_log.info.assert_called_once()


def test_disabled_stats_plugin_not_dispatched(plugins_dir, stats_log):
    manager = PluginManager(str(plugins_dir))
    plugin = NoteStatsPlugin()
    plugin.enabled = False
    manager.plugins["note_stats"] = plugin
    manager.dispatch("on_note_save", note_path="n.md", content="x")
    stats_log.info.assert_not_called()


def test_stats_setup_swaps_logger(stats, monkeypatch):
    monkeypatch.setattr(note_stats, "logger", note_stats.logger)
    custom = logging.getLogger("test.stats")
    stats.setup(Mock(logger=custom))
    assert note_stats.logger is custom


# W26: get_routes
@pytest.fixture
def stats_client(stats):
    router = stats.get_routes()
    assert isinstance(router, APIRouter)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_route_enabled_returns_stats(stats_client):
    body = stats_client.get("/calculate", params={"content": "one two"}).json()
    assert body["enabled"] is True
    assert body["stats"]["words"] == 2


def test_route_disabled_returns_no_stats(stats, stats_client):
    stats.enabled = False
    assert stats_client.get("/calculate", params={"content": "one two"}).json() == {"enabled": False, "stats": None}


def test_route_content_required(stats_client):
    assert stats_client.get("/calculate").status_code == 422
