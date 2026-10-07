import json
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
from unittest import TestCase
from unittest.mock import patch

from parking_watch import runtime


class InstallationTests(TestCase):
    def test_launch_agent_imports_project_at_new_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "New disk" / "parking-watch"
            project.mkdir(parents=True)
            agent = home / "Library/LaunchAgents" / (runtime.MONITOR_LABEL + ".plist")
            with patch.object(runtime, "ROOT", project), patch.object(runtime, "AUTOSTART", agent), \
                 patch.object(Path, "home", return_value=home), \
                 patch("parking_watch.runtime.subprocess.run", side_effect=[
                     SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=0)]):
                runtime.start_job(runtime.MONITOR_LABEL, "parking_watch.service")
            settings = plistlib.loads(agent.read_bytes())
            self.assertEqual(settings["WorkingDirectory"], str(project))
            self.assertTrue(settings["RunAtLoad"])
            self.assertTrue(settings["KeepAlive"])
            self.assertNotEqual(settings["ProgramArguments"][0], "/usr/bin/caffeinate")
            self.assertEqual(settings["ProgramArguments"][1], str(project / "scripts/run.py"))

    def test_uninstall_removes_both_agents_but_keeps_project_data(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "parking-watch"
            data = project / "data"
            data.mkdir(parents=True)
            (data / "photo.jpg").write_bytes(b"camera-image")
            (project / "config.local.json").write_text('{}')
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            labels = [runtime.MONITOR_LABEL, runtime.BOT_LABEL]
            for label in labels:
                (agents / (label + ".plist")).write_bytes(plistlib.dumps({
                    "ProgramArguments": [str(project / ".venv/bin/python"), "-m", "parking_watch.service"]}))
            loaded = set(labels)
            def launch(command, **kwargs):
                if command[1] == "list":
                    label = command[2]
                    return SimpleNamespace(returncode=0 if label in loaded else 1,
                                           stdout=str(project / ".venv/bin/python") + ' parking_watch.service')
                loaded.remove(command[2].rsplit('/', 1)[-1])
                return SimpleNamespace(returncode=0)
            state = home / "state.json"
            with patch.object(runtime, "ROOT", project), \
                 patch.object(runtime, "AUTOSTART", agents / (runtime.MONITOR_LABEL + ".plist")), \
                 patch.object(runtime, "INSTALL_STATE", state), patch.object(Path, "home", return_value=home), \
                 patch("parking_watch.runtime.subprocess.run", side_effect=launch):
                runtime.uninstall()
            self.assertEqual(loaded, set())
            self.assertEqual(list(agents.glob('*.plist')), [])
            self.assertEqual((data / "photo.jpg").read_bytes(), b"camera-image")
            self.assertTrue((project / "config.local.json").exists())
            self.assertFalse(json.loads(state.read_text())["installed"])

    def test_foreign_agent_is_checked_before_removing_any_own_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "parking-watch"
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            for label, root in [(runtime.MONITOR_LABEL, project), (runtime.BOT_LABEL, home / "parking-watch-other")]:
                (agents / (label + ".plist")).write_bytes(plistlib.dumps({
                    "ProgramArguments": [str(root / ".venv/bin/python")]}))
            with patch.object(runtime, "ROOT", project), \
                 patch.object(runtime, "AUTOSTART", agents / (runtime.MONITOR_LABEL + ".plist")), \
                 patch.object(Path, "home", return_value=home), \
                 patch("parking_watch.runtime.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="")) as run:
                with self.assertRaises(ValueError):
                    runtime.uninstall()
            self.assertEqual(len(list(agents.glob('*.plist'))), 2)
            self.assertFalse(any(call.args[0][1] == "bootout" for call in run.call_args_list))

    def test_local_python_agent_ownership_uses_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "Synced project"
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            for label in [runtime.MONITOR_LABEL, runtime.BOT_LABEL]:
                (agents / (label + ".plist")).write_bytes(plistlib.dumps({
                    "WorkingDirectory": str(project),
                    "ProgramArguments": [str(home / "Library/local-python/bin/python"),
                                         "-m", "parking_watch.service"]}))
            with patch.object(runtime, "ROOT", project), \
                 patch.object(runtime, "AUTOSTART", agents / (runtime.MONITOR_LABEL + ".plist")), \
                 patch.object(runtime, "INSTALL_STATE", home / "state.json"), \
                 patch.object(Path, "home", return_value=home), \
                 patch("parking_watch.runtime.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="")):
                runtime.uninstall()
            self.assertEqual(list(agents.glob("*.plist")), [])

    def test_loaded_ownership_accepts_exact_working_directory_not_prefix(self):
        with patch.object(runtime, "ROOT", Path("/a/project")):
            self.assertTrue(runtime._loaded_from_root('"WorkingDirectory" = "/a/project";'))
            self.assertTrue(runtime._loaded_from_root('"/a/project/.venv/bin/python"'))
            self.assertFalse(runtime._loaded_from_root('"WorkingDirectory" = "/a/project-other";'))
