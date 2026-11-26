"""Start the pinned Hermes Weixin sender in the process monitored by the fixed job script."""

import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sys
import unicodedata


def refuse():
    # Upstream optional-plugin loaders catch exceptions. Terminate before any access to the
    # outbound adapter if a loader is about to execute a program outside the installed payload.
    os.write(2, b"Fixed Hermes entry refused.\n")
    os._exit(78)


def native_arguments(argv, binding_file):
    if argv == ["send", "--list", "weixin", "--json"]:
        return argparse.Namespace(list_targets=True, message="weixin", json=True)
    if len(argv) != 6 or argv[:2] != ["send", "--to"] or argv[3] != "--file" or argv[5] != "--json":
        refuse()
    target, note = argv[2], Path(argv[4])
    platform, _, recipient = target.partition(":")
    if (platform != "weixin" or not recipient or ":" in recipient
            or any(c.isspace() or unicodedata.category(c) == "Cc" for c in recipient)):
        refuse()
    # Reuse the same protected, non-secret recipient digest as Invoke-PaperWeixin.ps1.
    try:
        expected_digest = json.loads(binding_file.read_text(encoding="utf-8-sig"))["target_digest"]
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        refuse()
    actual_digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    if (not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            or not hmac.compare_digest(actual_digest, expected_digest)):
        refuse()
    local = os.environ.get("LOCALAPPDATA")
    if not local or not re.fullmatch(r"[0-9a-f]{32}", note.parent.name) or not note.is_absolute():
        refuse()
    jobs = Path(local) / "PolymarketAlphaLab" / "WeixinDelivery" / "jobs"
    expected = jobs / note.parent.name / "note.txt"
    if expected != note or not expected.is_file() or expected.resolve() != expected:
        refuse()
    return argparse.Namespace(list_targets=False, to=target, file=str(note), json=True)


def install_code_policy(source, runtime, dependencies):
    permitted = (source, runtime, dependencies)
    # The C entry obtains this from Windows itself before CPython starts.
    system_dlls = Path(os.environ["PAL_HERMES_SYSTEM32"]).resolve() if os.name == "nt" else None
    sys.path.extend((str(source), str(dependencies)))

    def in_payload(filename):
        path = Path(filename).resolve()
        return any(path.is_relative_to(root) for root in permitted)

    def audit(event, args):
        if event == "exec":
            filename = args[0].co_filename
            if not (filename.startswith("<") and filename.endswith(">")) and not in_payload(filename):
                refuse()
        elif event == "import" and len(args) > 1 and isinstance(args[1], str):
            if Path(args[1]).is_absolute() and not in_payload(args[1]):
                refuse()
        elif event in ("os.system", "os.exec", "os.posix_spawn", "subprocess.Popen"):
            refuse()
        elif event == "ctypes.dlopen" and isinstance(args[0], (str, bytes)):
            # Bare names are resolved under the native launcher's restricted loader policy.
            path = Path(os.fsdecode(args[0]))
            system_library = system_dlls is not None and path.is_absolute() and path.resolve().parent == system_dlls
            if path.parent != Path(".") and not system_library and not in_payload(path):
                refuse()

    sys.addaudithook(audit)

    import providers
    from hermes_cli.plugins import PluginManager

    approved_dir = source / "plugins" / "platforms" / "weixin"

    def gate_loader(original):
        def load(manager, manifest):
            if (manifest.source != "bundled" or manifest.key != "platforms/weixin"
                    or Path(manifest.path).resolve() != approved_dir):
                refuse()
            return original(manager, manifest)
        return load

    # Keep the normal discovery, enable/disable and authorization decisions. Guard the
    # actual program admission points after stock discovery decides to load a plugin.
    PluginManager._load_plugin = gate_loader(PluginManager._load_plugin)
    PluginManager._register_deferred_platform = gate_loader(PluginManager._register_deferred_platform)

    def deny_provider(*args, **kwargs):
        refuse()

    # Provider discovery has its own importer, separate from PluginManager. This point
    # precedes the per-user snapshot copy, in-process execution and hosted execution.
    providers._import_user_plugin_dir = deny_provider
    # The official installation includes no bundled model-provider plugins. A configured
    # override of the bundled plugin directory cannot introduce another implementation.
    providers._exec_plugin_module = deny_provider


def require_native_weixin(source):
    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    entry = platform_registry.get("weixin")
    if entry is None:
        refuse()
    sender = entry.standalone_sender_fn
    code = getattr(sender, "__code__", None)
    if (getattr(sender, "__name__", None) != "_standalone_send" or code is None
            or Path(code.co_filename).resolve() != source / "plugins" / "platforms" / "weixin" / "adapter.py"):
        refuse()


def main():
    root = Path(__file__).resolve().parent
    source = root / "source"
    if not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        refuse()
    arguments = native_arguments(sys.argv[1:], root.parent / "weixin-binding.json")
    logging.disable(logging.CRITICAL)
    # Keep native request and reply diagnostics on the fixed wrapper's pipe only.
    install_code_policy(source, root / "runtime", root / "site-packages")
    from hermes_cli import send_cmd
    load_profile = send_cmd._load_hermes_env

    def load_and_check():
        # cmd_send first hydrates the existing profile. Honor its native ordering and check
        # the final adapter once that profile has selected its delivery policy.
        load_profile()
        require_native_weixin(source)

    send_cmd._load_hermes_env = load_and_check
    send_cmd.cmd_send(arguments)


if __name__ == "__main__":
    main()
