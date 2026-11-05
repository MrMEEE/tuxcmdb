"""Remote installer validation, isolation and per-host progress."""

import io
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django
django.setup()

from django.conf import settings
from django.test import Client, override_settings
import paramiko
from webui import agent_install
from webui.forms import AgentInstallForm


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.override = override_settings(
            AGENT_INSTALL_DATA_DIR=self.root / "install-data", AGENTS_DIR=self.root,
            AGENT_RELEASES_DIR="", AGENT_INSTALL_SERVER_URL="",
            SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.options = {
            "hosts": ["192.0.2.1", "2001:db8::1"], "username": "operator",
            "password": "sensitive-password", "server_url": "https://api.example.test",
            "use_sudo": True, "ignore_ssl": False,
        }
        self.client = Client()

    def login(self, readonly=False):
        session = self.client.session
        session.update({"api_username": "writer", "api_readonly": readonly})
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    def test_form_validates_ips_deduplicates_and_normalizes_url(self):
        data = {**self.options, "hosts": "192.0.2.1,2001:db8::1\n192.0.2.1",
                "server_url": "api.example.test:8443"}
        form = AgentInstallForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["hosts"], ["192.0.2.1", "2001:db8::1"])
        self.assertEqual(form.cleaned_data["server_url"], "https://api.example.test:8443")
        for field, value in (
            ("hosts", "example.test"), ("hosts", "192.0.2.1;id"), ("hosts", "0.0.0.0"),
            ("hosts", "ff02::1"), ("hosts", "fe80::1%eth0"),
            ("hosts", " ".join(f"192.0.2.{number}" for number in range(1, 52))),
            ("username", "-oProxyCommand=id"), ("password", "pass\nid"),
            ("server_url", "https://user:pass@example.test"),
            ("server_url", "https://api.example.test/path"),
            ("server_url", "https://api.example.test:invalid"), ("server_url", "file:///etc/passwd"),
        ):
            with self.subTest(field=field, value=value):
                invalid = AgentInstallForm({**data, field: value})
                self.assertFalse(invalid.is_valid())

    def test_detects_supported_distributions_without_executing_os_release(self):
        for text, expected in (
            ('ID=rocky\nID_LIKE="rhel centos"\nVERSION_ID="8.10"', "el8"),
            ('ID=almalinux\nVERSION_ID="9.5"', "el9"),
            ('ID=rhel\nVERSION_ID="10.0"', "el10"),
            ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"', "debian"),
            ('ID=debian\nVERSION_ID="12"', "debian"),
        ):
            self.assertEqual(agent_install.select_target(text), expected)
        for text in ('ID=fedora\nVERSION_ID="43"', 'ID=rhel\nVERSION_ID="7"', 'ID="a$(id)"'):
            with self.assertRaises(agent_install.InstallError):
                agent_install.select_target(text)

    @patch("webui.agent_install._command")
    @patch("webui.agent_install.paramiko.SSHClient")
    def test_install_selects_package_runs_as_service_user_and_reports_every_step(self, ssh, command):
        package = self.root / "tuxcmdb-agent-0.2.26.6-1.el9.noarch.rpm"
        package.write_bytes(b"test package")
        self.root.joinpath("install-data").mkdir()
        known_hosts = self.root / "install-data/known_hosts"
        known_hosts.touch()
        client = ssh.return_value
        sftp = client.open_sftp.return_value
        destination = io.BytesIO()
        destination.close = Mock()
        sftp.open.side_effect = [
            io.BytesIO(b'ID=rocky\nVERSION_ID="9"'), destination,
        ]
        updates = []
        agent_install._install_host({"host": "192.0.2.1"}, self.options, {"el9": package},
                                    lambda *values: updates.append(values))
        for step, _ in agent_install.STEPS:
            self.assertIn((step, "success"), [(item[0], item[1]) for item in updates])
        self.assertEqual(destination.getvalue(), b"test package")
        commands = [call.args[1] for call in command.call_args_list]
        self.assertTrue(any("dnf install" in value for value in commands))
        registration = next(value for value in commands if "runuser" in value)
        self.assertIn("runuser -u tuxcmdb-agent", registration)
        self.assertIn("ensure_config", registration)
        self.assertIn("https://api.example.test", registration)
        self.assertNotIn(self.options["password"], " ".join(commands))
        self.assertTrue(any("enable --now tuxcmdb-agent.timer" in value for value in commands))
        client.connect.assert_called_once_with(
            "192.0.2.1", username="operator", password=self.options["password"],
            look_for_keys=False, allow_agent=False, timeout=15, auth_timeout=15, banner_timeout=15,
        )
        client.load_host_keys.assert_called_once_with(str(known_hosts))
        self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.AutoAddPolicy)
        client.close.assert_called_once()
        sftp.remove.assert_called_once()
        sftp.rmdir.assert_called_once()

    @patch("webui.agent_install._command")
    @patch("webui.agent_install.paramiko.SSHClient")
    def test_debian_install_and_missing_package_failures(self, ssh, command):
        self.root.joinpath("install-data").mkdir()
        self.root.joinpath("install-data/known_hosts").touch()
        package = self.root / "tuxcmdb-agent_0.2.26.6-1.debian_all.deb"
        package.write_bytes(b"deb")
        sftp = ssh.return_value.open_sftp.return_value
        destination = io.BytesIO()
        destination.close = Mock()
        sftp.open.side_effect = [io.BytesIO(b"ID=ubuntu\nID_LIKE=debian"), destination]
        updates = []
        agent_install._install_host({"host": "192.0.2.1"}, self.options, {"debian": package},
                                    lambda *values: updates.append(values))
        self.assertTrue(any("apt-get install" in call.args[1] for call in command.call_args_list))
        sftp.open.side_effect = [io.BytesIO(b"ID=ubuntu\nID_LIKE=debian")]
        updates.clear()
        agent_install._install_host({"host": "192.0.2.1"}, self.options, {},
                                    lambda *values: updates.append(values))
        self.assertTrue(any(item[0] == "os" and item[1] == "failed"
                            and "No published debian" in item[2] for item in updates))

    @patch("webui.agent_install.paramiko.SSHClient")
    def test_auth_failure_does_not_expose_password_and_stops_before_install(self, ssh):
        ssh.return_value.connect.side_effect = paramiko.AuthenticationException(self.options["password"])
        updates = []
        with self.assertLogs("webui.agent_install", level="WARNING") as logs:
            agent_install._install_host({"host": "192.0.2.1"}, self.options, {},
                                        lambda *values: updates.append(values))
        self.assertIn(("connect", "failed", "SSH authentication failed."), updates)
        self.assertNotIn(self.options["password"], str(updates) + str(logs.output))
        ssh.return_value.open_sftp.assert_not_called()

    def test_sudo_password_uses_stdin_never_command_and_command_errors_are_explicit(self):
        client = Mock()
        stdin, stdout, stderr = Mock(), Mock(), Mock()
        client.exec_command.return_value = (stdin, stdout, stderr)
        channel = stdout.channel
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        agent_install._command(client, "id -u", self.options)
        self.assertTrue(client.exec_command.call_args.args[0].startswith("sudo -S -p ''"))
        self.assertNotIn(self.options["password"], client.exec_command.call_args.args[0])
        stdin.write.assert_called_once_with(self.options["password"] + "\n")
        channel.recv_exit_status.return_value = 1
        with self.assertRaises(agent_install.InstallError):
            agent_install._command(client, "false", {**self.options, "use_sudo": False})
        self.assertNotIn("sudo", client.exec_command.call_args.args[0])

    def test_registration_command_is_safely_quoted_and_checks_existing_server(self):
        args = shlex.split(agent_install._register_command("https://api.example.test", True))
        self.assertEqual(args[-2:], ["https://api.example.test", "true"])
        self.assertIn('old.get("server_url"', args[args.index("-c") + 1])
        self.assertIn('args.append("--insecure")', args[args.index("-c") + 1])

    def test_registration_preserves_matching_config_and_rejects_mismatched_policy(self):
        args = shlex.split(agent_install._register_command("https://api.example.test", True))
        code = compile(args[args.index("-c") + 1], "<registration-check>", "exec")
        agent = {"ensure_config": Mock(), "build_arg_parser": Mock()}
        with patch("runpy.run_path", return_value=agent), patch.object(
            sys, "argv", ["-c", "https://api.example.test", "true"]
        ), patch("os.path.exists", return_value=True), patch("builtins.open", return_value=io.StringIO(
            '{"server_url":"https://api.example.test","verify_ssl":false}'
        )):
            exec(code, {})
        agent["ensure_config"].assert_called_once()
        agent["build_arg_parser"].return_value.parse_args.assert_called_once_with(
            ["--server-url", "https://api.example.test", "--insecure"]
        )
        with patch("runpy.run_path") as load, patch.object(
            sys, "argv", ["-c", "https://api.example.test", "false"]
        ), patch("os.path.exists", return_value=True), patch("builtins.open", return_value=io.StringIO(
            '{"server_url":"https://other.example.test","verify_ssl":true}'
        )), self.assertRaises(SystemExit):
            exec(code, {})
        load.assert_not_called()

    def test_real_ssh_channel_exec_drains_output_and_sends_sudo_password(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        self.addCleanup(listener.close)
        key = paramiko.RSAKey.generate(2048)
        received = []
        ready = threading.Event()

        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL if password == "ssh-secret" else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "password"

            def check_channel_request(self, kind, channel_id):
                return paramiko.OPEN_SUCCEEDED

            def check_channel_exec_request(self, channel, command):
                received.append(command.decode())
                ready.set()
                return True

        def serve():
            connection, _ = listener.accept()
            with paramiko.Transport(connection) as transport:
                transport.add_server_key(key)
                transport.start_server(server=Server())
                channel = transport.accept(5)
                if channel is None or not ready.wait(5):
                    return
                received.append(channel.recv(1024).decode())
                channel.send(b"ordinary output")
                channel.send_stderr(b"not exposed")
                channel.send_exit_status(0)
                channel.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        with paramiko.SSHClient() as client:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect("127.0.0.1", port=listener.getsockname()[1],
                           username="test", password="ssh-secret", look_for_keys=False,
                           allow_agent=False, timeout=5)
            agent_install._command(client, "id -u", {**self.options, "password": "sudo-secret"}, timeout=5)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(received[0].startswith("sudo -S"))
        self.assertNotIn("sudo-secret", received[0])
        self.assertEqual(received[1], "sudo-secret\n")

    @patch("webui.agent_install._install_host")
    def test_background_worker_continues_after_host_failure_and_never_persists_credentials(self, install):
        first_started = threading.Event()
        proceed = threading.Event()

        def fake(host, options, packages, update):
            first_started.set()
            proceed.wait(5)
            update("connect", "failed" if host["host"] == "192.0.2.1" else "success", "test")

        install.side_effect = fake
        threads = []
        thread_class = threading.Thread

        def capture_thread(*args, **kwargs):
            thread = thread_class(*args, **kwargs)
            threads.append(thread)
            return thread

        with patch("webui.agent_install.threading.Thread", side_effect=capture_thread):
            job = agent_install.start("writer", dict(self.options), {})
            self.assertTrue(first_started.wait(3))
            with self.assertRaises(agent_install.InstallError):
                agent_install.start("writer", dict(self.options), {})
            self.assertIsNone(agent_install.status(job, "someone-else"))
            self.assertEqual(agent_install.status(job, "writer")["state"], "running")
            proceed.set()
        threads[0].join(timeout=3)
        self.assertFalse(threads[0].is_alive())
        data = agent_install.status(job, "writer")
        self.assertEqual(data["state"], "finished")
        self.assertEqual([host["state"] for host in data["hosts"]], ["failed", "success"])
        serialized = (settings.AGENT_INSTALL_DATA_DIR / f"{job}.json").read_text()
        self.assertNotIn("password", serialized)
        self.assertNotIn("operator", serialized)

    def test_interrupted_job_is_reported_after_worker_restart(self):
        directory = settings.AGENT_INSTALL_DATA_DIR
        directory.mkdir()
        job = "a" * 32
        data = {"owner": "writer", "state": "running", "hosts": [
            {"state": "running", "steps": [{"state": "running"}]},
        ]}
        (directory / f"{job}.json").write_text(json.dumps(data))
        result = agent_install.status(job, "writer")
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["hosts"][0]["steps"][0]["state"], "interrupted")
        self.assertIsNone(agent_install.status("../settings", "writer"))

    @patch("webui.agent_install.start", return_value="b" * 32)
    def test_install_endpoints_require_write_access_and_select_current_packages(self, start):
        data = {**self.options, "hosts": "192.0.2.1"}
        self.assertEqual(self.client.post("/agents/install/", data).status_code, 302)
        self.login(readonly=True)
        self.assertEqual(self.client.post("/agents/install/", data).status_code, 403)
        self.assertNotIn("Install agents</button>", self.client.get("/agents/").content.decode())
        self.login()
        self.assertEqual(self.client.get("/agents/install/").status_code, 405)
        self.assertEqual(self.client.post("/agents/install/", data).status_code, 400)
        package = self.root / "tuxcmdb-agent-0.2.26.6-1.el9.noarch.rpm"
        package.write_bytes(b"rpm")
        response = self.client.post("/agents/install/", data)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(start.call_args.args[0], "writer")
        self.assertEqual(start.call_args.args[2], {"el9": package})
        self.assertNotIn(self.options["password"], response.content.decode())
        content = self.client.get("/agents/").content.decode()
        self.assertLess(content.index("Install agent</h5>"), content.index("Agent Downloads"))
        self.assertIn('value="http://testserver"', content)
        with override_settings(AGENT_INSTALL_SERVER_URL="https://api.example.test"):
            self.assertIn('value="https://api.example.test"', self.client.get("/agents/").content.decode())

    @patch("webui.agent_install.status")
    def test_progress_endpoint_is_owner_scoped_and_uncacheable(self, status):
        self.login()
        job = "a" * 32
        status.return_value = {"id": job, "state": "running", "hosts": []}
        response = self.client.get(f"/agents/install/{job}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        status.assert_called_once_with(job, "writer")
        status.return_value = None
        self.assertEqual(self.client.get(f"/agents/install/{job}/").status_code, 404)
        self.login(readonly=True)
        self.assertEqual(self.client.get(f"/agents/install/{job}/").status_code, 403)

    def test_installer_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["api_username"] = "writer"
        session["api_readonly"] = False
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        self.assertEqual(client.post("/agents/install/", {
            **self.options, "hosts": "192.0.2.1",
        }).status_code, 403)


if __name__ == "__main__":
    unittest.main()
