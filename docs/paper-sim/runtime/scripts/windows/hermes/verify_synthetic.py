"""Run fixed startup cases using public Linux dependencies in an offline namespace."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch


CASES = {
    "list": 0, "send": 0, "upstream-policy-denial": 1, "plugin-disabled": 78,
    "external-weixin": 78, "external-provider": 78, "wrong-note": 78, "extra-argument": 78,
    "profile-safe-mode": 78, "wrong-recipient": 78,
}


def exercise(case):
    profile = Path("/fixture")
    root = Path("/payload")
    source = root / "source"
    target = "wxid_" + os.environ.pop("FX49_TEST_NAME")
    body = "Offline fixed native message."
    nonce = secrets.token_hex(16)
    note = profile / "PolymarketAlphaLab" / "WeixinDelivery" / "jobs" / nonce / "note.txt"
    note.parent.mkdir(parents=True)
    note.write_text(body, encoding="utf-8")
    configuration = {"plugins": {"enabled": ["platforms/weixin"]}}
    if case == "plugin-disabled":
        configuration["plugins"] = {"disabled": ["platforms/weixin"]}
    if case == "profile-safe-mode":
        (profile / ".env").write_text("HERMES_SAFE_MODE=1\n")
    if case == "external-provider":
        provider = profile / "plugins" / "model-providers" / "blank"
        provider.mkdir(parents=True)
        (provider / "__init__.py").write_bytes(b"")
    if case == "external-weixin":
        plugin = profile / "plugins" / "platforms" / "weixin"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_bytes((source / "plugins/platforms/weixin/plugin.yaml").read_bytes())
        (plugin / "__init__.py").write_bytes(b"")
    (profile / "config.yaml").write_text(json.dumps(configuration))
    (profile / "channel_directory.json").write_text(json.dumps({"platforms":{"weixin":[{"type":"dm","id":target}]}}))
    os.environ.update(WEIXIN_TOKEN="dummy", WEIXIN_ACCOUNT_ID="dummy")

    def observe(event, args):
        if event in ("socket.getaddrinfo", "socket.connect"):
            os.write(2, b"FX49_NETWORK\n")
            os._exit(90)

    sys.addaudithook(observe)
    spec = importlib.util.spec_from_file_location("fixed_entry", root / "fixed_entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    sys.argv = [str(root / "fixed_entry.py"), "send", "--list", "weixin", "--json"]
    if case in ("send", "upstream-policy-denial", "wrong-note", "wrong-recipient"):
        sys.argv = [sys.argv[0],"send","--to","weixin:" + target + ("x" if case=="wrong-recipient" else ""), "--file",
                    str(note) if case != "wrong-note" else "/fixture/other.txt", "--json"]
    elif case == "extra-argument":
        sys.argv.append("--quiet")

    # Install the transport substitute only after real discovery and verification have
    # completed. The low-level network coroutine is the only change on the success route.
    require_native = entry.require_native_weixin

    def install_substitute(src):
        require_native(src)
        from gateway.platform_registry import platform_registry
        native = sys.modules[platform_registry.get("weixin").standalone_sender_fn.__module__]
        async def transport(_session, *, base_url, token, to, text, context_token, client_id):
            if to != target or text != body or not client_id:
                os._exit(91)
            os.write(2, b"FX49_WIRE\n")
            return {"ret": 0, "errcode": 0}
        native._send_message = transport
        if case == "upstream-policy-denial":
            from gateway.outbound import send
            def deny(*args, **kwargs):
                os.write(2, b"FX49_POLICY\n")
                return "synthetic policy refusal"
            send._authorize_relay_target = deny

    out = io.StringIO()
    with patch.object(entry, "require_native_weixin", install_substitute), redirect_stdout(out):
        try:
            entry.main()
        except SystemExit as stop:
            rc = stop.code
        else:
            rc = 0
    result = json.loads(out.getvalue())
    if case == "list":
        expected = [{"type": "dm", "id": target}]
        if result.get("platforms", {}).get("weixin") != expected:
            return 92
    elif case == "send":
        if not (result.get("success") is True and result.get("platform") == "weixin"
                and result.get("chat_id") == target and result.get("message_id")):
            return 93
    elif case == "upstream-policy-denial":
        if result.get("success") or not result.get("error"):
            return 94
    return rc


def run(args):
    here = Path(__file__).resolve().parent
    def run_case(case, binding_file, test_name):
        cmd = ["/usr/bin/sudo","-n","/usr/bin/bwrap","--unshare-net","--unshare-pid","--unshare-ipc",
               "--unshare-uts","--die-with-parent","--new-session","--clearenv",
               "--ro-bind","/usr","/usr","--symlink","usr/lib","/lib","--symlink","usr/lib64","/lib64",
               "--symlink","usr/bin","/bin","--dev","/dev","--proc","/proc",
               "--dir","/etc","--ro-bind","/etc/ld.so.cache","/etc/ld.so.cache",
               "--perms","1777","--tmpfs","/tmp","--perms","1777","--tmpfs","/fixture",
               "--dir","/payload","--ro-bind",str(binding_file),"/weixin-binding.json",
               "--ro-bind",str(args.source.resolve()),"/payload/source",
               "--ro-bind",str(args.site.resolve()),"/payload/site-packages",
               "--ro-bind",str(args.runtime.resolve()),"/payload/runtime",
               "--ro-bind",str(here/"fixed_entry.py"),"/payload/fixed_entry.py",
               "--ro-bind",str(__file__),"/test.py","--chdir","/payload"]
        for key,val in {"HOME":"/fixture","HERMES_HOME":"/fixture","LOCALAPPDATA":"/fixture","FX49_TEST_NAME":test_name,
                        "HERMES_BUNDLED_PLUGINS":"/payload/source/plugins","TMP":"/tmp",
                        "TMPDIR":"/tmp","TEMP":"/tmp","LANG":"C.UTF-8","PATH":"/usr/bin:/bin"}.items():
            cmd.extend(("--setenv",key,val))
        cmd.extend(("/usr/bin/setpriv","--reuid",str(os.getuid()),"--regid",str(os.getgid()),
                    "--clear-groups","--no-new-privs","--","/payload/runtime/bin/python3","-I","-S",
                    "-B","/test.py","--inside",case))
        result = subprocess.run(cmd,capture_output=True,timeout=120)
        wire = result.stderr.count(b"FX49_WIRE")
        policy = result.stderr.count(b"FX49_POLICY")
        network = result.stderr.count(b"FX49_NETWORK")
        return {"case":case,"exit":result.returncode,"wire_substitutions":wire,"policy_substitutions":policy,
                "network_attempts":network,"passed":result.returncode==CASES[case] and not network
                and wire==int(case=="send") and policy==int(case=="upstream-policy-denial")}
    def check(case):
        with tempfile.TemporaryDirectory(prefix="hermes-offline-binding-") as tmp:
            test_name = secrets.token_hex(12)
            digest = hashlib.sha256(("weixin:wxid_" + test_name).encode()).hexdigest()
            binding = Path(tmp) / "weixin-binding.json"
            binding.write_text(json.dumps({"hermes_path":"/payload/hermes.exe","target_digest":digest}))
            return run_case(case, binding, test_name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(check,CASES))
    print(json.dumps({"platform":"Linux synthetic profiles with isolated instrumented transport",
                      "real_messages":0,"cases":results},indent=2))
    return int(not all(r["passed"] for r in results))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inside"]:
        sys.exit(exercise(sys.argv[2]))
    parser=argparse.ArgumentParser()
    for opt in ("source", "runtime", "site"):
        parser.add_argument("--"+opt,type=Path,required=True)
    sys.exit(run(parser.parse_args()))
