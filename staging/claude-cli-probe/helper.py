"""Opt-in TEST harness for an operator-supplied native Claude image.

No download, login, real key, database, forwarding proxy or production entry.
Use only a disposable host with external egress denied. The test endpoint is
HTTP on numeric loopback: the sole deliberate override of the production HTTPS
profile. This does not test TLS, outside-root writes, or transient/deleted files.
Observations are metadata only, never an activation authorization.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import stat
from threading import Thread
from urllib.parse import urlsplit

from polymarket_alpha_lab import research_process as process
from polymarket_alpha_lab.research_claude_exec import CLAUDE_VERSION, ClaudeExecInput, decode_claude_result
from polymarket_alpha_lab.research_claude_profile import ClaudeExecProfile, MODEL_ID
from polymarket_alpha_lab.team_research_agent_types import strict_json

MODES = ('success', 'rate_limit', 'server_error', 'invalid_action', 'tool_use', 'truncated')
VERSION_OUTPUT = CLAUDE_VERSION + ' (Claude Code)\n'
TEST_KEY = 'SYNTHETIC-PAL-PROBE-NOT-A-CREDENTIAL'
APPROVED = 'SYNTHETIC-PAL-APPROVED-PROMPT'
UNRELATED = 'SYNTHETIC-PAL-UNRELATED-CONTEXT'
RESPONSE = 'SYNTHETIC-PAL-MOCK-RESPONSE'
MAX_FILE_BYTES, MAX_STATE_BYTES, MAX_ENTRIES = 65536, 4194304, 256
MAX_HTTP_BYTES, MAX_REQUESTS = 1048576, 8
_ENV = ('POLYMARKET_ALPHA_LAB_CLAUDE_PROBE', 'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_IMAGE', 'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_SHA256',
        'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_BYTES', 'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST')


def _image_values(image, digest, size):
    if (type(image) is not str or not Path(image).is_absolute() or '\x00' in image
            or type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None
            or type(size) is not int or not 1 <= size <= 536870912):
        raise ValueError('probe_image_invalid')
    image.encode('utf-8')
    return image, digest, size


def configured_image(environment):
    """No implicit image discovery. Partial opt-in fails rather than skips."""
    values = {key: environment[key] for key in _ENV if key in environment}
    if not values:
        return None
    try:
        if (set(values) != set(_ENV) or values[_ENV[0]] != '1' or values[_ENV[4]] != '1'
                or re.fullmatch('[1-9][0-9]{0,8}', values[_ENV[3]]) is None):
            raise ValueError
        return _image_values(values[_ENV[1]], values[_ENV[2]], int(values[_ENV[3]]))
    except Exception:
        raise ValueError('probe_configuration_invalid') from None


def test_input():
    return ClaudeExecInput(MODEL_ID, json.dumps([{'role': 'user', 'content': APPROVED}]), 1024)


def response_text(mode):
    if mode == 'invalid_action':
        return '{invalid'
    return json.dumps({'calls': [{'name': 'search_evidence',
                                 'arguments_json': json.dumps({'query': RESPONSE})}]})


def _stream(mode):
    text = response_text(mode)
    usage = dict(input_tokens=3, output_tokens=0,
                 cache_creation_input_tokens=7, cache_read_input_tokens=11)
    start = dict(id='msg_pal_synthetic', type='message', role='assistant', model=MODEL_ID,
                 content=[], stop_reason=None, stop_sequence=None, usage=usage)
    block = ({'type': 'tool_use', 'id': 'toolu_pal_synthetic', 'name': 'Read',
              'input': {}} if mode == 'tool_use' else {'type': 'text', 'text': ''})
    delta = ({'type': 'input_json_delta', 'partial_json': '{"file_path":"CLAUDE.md"}'}
             if mode == 'tool_use' else {'type': 'text_delta', 'text': text})
    events = [dict(type='message_start', message=start),
        dict(type='content_block_start', index=0, content_block=block),
        dict(type='content_block_delta', index=0, delta=delta),
        dict(type='content_block_stop', index=0),
        dict(type='message_delta', delta={'stop_reason': 'tool_use' if mode == 'tool_use' else 'end_turn',
                                         'stop_sequence': None}, usage={'output_tokens': 5}),
        dict(type='message_stop')]
    if mode == 'truncated':
        events = events[:3]
    wire = ''.join('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n' for event in events)
    return (wire + ('event: message_delta\n' if mode == 'truncated' else '')).encode()


def _strings(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str:
            yield item
        elif type(item) is dict:
            pending.extend(item.keys()); pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)


class _Server(HTTPServer):
    allow_reuse_address = False

    def __init__(self, mode):
        self.mode, self.count, self.unexpected = mode, 0, 0
        self.matches, self.faults, self.responses = True, 0, 0
        super().__init__(('127.0.0.1', 0), _Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(2)
        return sock, address

    def handle_error(self, *_):
        self.faults += 1  # Never print peer headers/body or exception text.


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.server.faults += 1
        super().send_error(code, 'Synthetic probe refusal', 'Request not admitted')

    def _respond(self, code, payload=b'', content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def do_POST(self):
        s = self.server
        s.count = min(MAX_REQUESTS+1, s.count+1)
        if s.count > MAX_REQUESTS:
            s.matches = False
            self._respond(429)
            return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) != 1 or re.fullmatch('[1-9][0-9]{0,6}', lengths[0]) is None
                    or self.headers.get('Transfer-Encoding') is not None):
                raise ValueError
            length = int(lengths[0])
            if not 1 <= length <= MAX_HTTP_BYTES:
                raise ValueError
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            body = strict_json(raw.decode('utf-8'))
            texts = tuple(_strings(body))
            route = urlsplit(self.path)
            good = (route.path == '/v1/messages' and not route.scheme and not route.netloc
                and route.query in ('', 'beta=true') and not route.fragment
                and type(body) is dict and body.get('model') == MODEL_ID
                and type(body.get('max_tokens')) is int and body['max_tokens'] == 1024
                and body.get('stream') is True and body.get('tools') in (None, [])
                and any(test_input().prompt_json in text for text in texts)
                and all(UNRELATED not in text and TEST_KEY not in text for text in texts)
                and self.headers.get_all('x-api-key', []) == [TEST_KEY]
                and self.headers.get('Authorization') is None)
            if not good:
                s.matches = False
                self._respond(400)
                return
            if s.mode in ('rate_limit', 'server_error'):
                payload = json.dumps({'type': 'error', 'error': {
                    'type': 'rate_limit_error' if s.mode == 'rate_limit' else 'api_error',
                    'message': 'Synthetic probe response'}}).encode()
                self._respond(429 if s.mode == 'rate_limit' else 500, payload)
            else:
                self._respond(200, _stream(s.mode), 'text/event-stream')
            s.responses += 1
        except Exception:
            s.faults += 1
            self.close_connection = True

    def _unexpected(self):
        self.server.unexpected = min(MAX_REQUESTS+1, self.server.unexpected+1)
        self.server.matches = False
        self._respond(404)

    do_GET = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_CONNECT = _unexpected


@contextmanager
 def loopback_server(mode):
    server = _Server(mode)
    def serve():
        try:
            server.serve_forever(poll_interval=.05)
        except BaseException:
            server.faults += 1
    worker = Thread(target=serve, name='pal-claude-probe-http')
    failure = None
    try:
        worker.start()
        yield server
    except BaseException as error:
        failure = error
    finally:
        actions = ([server.shutdown] if worker.ident is not None else []) + [server.server_close]
        if worker.ident is not None:
            actions.append(lambda: worker.join(timeout=3))
        for action in actions:
            try:
                action()
            except BaseException as error:
                failure = process._prefer_failure(failure, error)
        if worker.is_alive():
            failure = process._prefer_failure(failure, RuntimeError('probe_server_cleanup_failed'))
    if failure is not None:
        raise failure


def state_snapshot(root, *, expected_root=None):
    """Bounded surviving-state snapshot, not an OS write trace or sandbox.

    Never follows links/reparse points or reads multiply-linked/nonregular files.
    Runtime concurrent replacement is outside the guarantee; any observed
    inconsistency is incomplete, not evidence of no writes.
    """
    root = Path(root)
    files, pending, total, entries = {}, [], 0, 0
    result = dict(complete=True, unsafe_entries=0, limit_reached=False, sentinel_files=0, files=files, root_identity=None)
    needles = tuple(s.encode(encoding) for s in (APPROVED, RESPONSE, TEST_KEY)
                    for encoding in ('utf-8', 'utf-16-le', 'utf-16-be'))
    try:
        info = root.lstat()
        root_id = (info.st_dev, info.st_ino)
        result['root_identity'] = root_id
        if expected_root is not None and root_id != expected_root:
            raise ValueError
        pending.append((root, root_id))
        while pending:
            parent, identity = pending.pop()
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity
                    or getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                result['unsafe_entries'] += 1; result['complete'] = False
                continue
            with os.scandir(parent) as stream:
                for entry in stream:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        result.update(complete=False, limit_reached=True)
                        return result
                    info = entry.stat(follow_symlinks=False)
                    name = str(Path(entry.path).relative_to(root))
                    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0)
                            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                        result['unsafe_entries'] += 1; result['complete'] = False
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        files[name] = ('directory',)
                        pending.append((Path(entry.path), (info.st_dev, info.st_ino)))
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        result['unsafe_entries'] += 1; result['complete'] = False
                        continue
                    if info.st_size > MAX_FILE_BYTES or total+info.st_size > MAX_STATE_BYTES:
                        result.update(complete=False, limit_reached=True)
                        return result
                    fd = os.open(entry.path, os.O_RDONLY | getattr(os, 'O_BINARY', 0)
                                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                    failure, content = None, bytearray()
                    try:
                        opened = os.fstat(fd)
                        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                            raise ValueError
                        while len(content) <= MAX_FILE_BYTES:
                            block = os.read(fd, min(8192, MAX_FILE_BYTES+1-len(content)))
                            if not block:
                                break
                            content.extend(block)
                        if len(content) != info.st_size or os.fstat(fd).st_mtime_ns != info.st_mtime_ns:
                            raise ValueError
                    except BaseException as error:
                        failure = error
                    finally:
                        try:
                            os.close(fd)
                        except BaseException as error:
                            failure = process._prefer_failure(failure, error)
                    if failure is not None:
                        raise failure
                    total += len(content)
                    files[name] = ('file', sha256(content).hexdigest())
                    result['sentinel_files'] += int(any(n in content for n in needles))
    except Exception:
        result['complete'] = False
    return result


def state_delta(before, after):
    names = before['files'].keys() | after['files'].keys()
    return dict(complete=before['complete'] and after['complete'],
        changed_entries=sum(before['files'].get(n) != after['files'].get(n) for n in names),
        unsafe_entries=before['unsafe_entries']+after['unsafe_entries'],
        limit_reached=before['limit_reached'] or after['limit_reached'],
        sentinel_files=after['sentinel_files'])


def run_probe(image, digest, size, *, root, mode, allow_probe=False, process_runner=None):
    """Two explicit operations: --version, then ONE synthetic prompt scenario.

    A hash/version string does not establish vendor provenance. Only one fixed
    endpoint variable is changed for the local HTTP test, never runtime profiles.
    Temp files are retained for local inspection; never upload the entire root.
    """
    if allow_probe is not True:
        raise ValueError('probe_opt_in_required')
    image, digest, size = _image_values(image, digest, size)
    if type(mode) is not str or mode not in MODES:
        raise ValueError('probe_mode_invalid')
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('probe_root_unavailable')
    try:
        root.mkdir(mode=0o700)
    except OSError:
        raise ValueError('probe_root_unavailable') from None
    for name in ('home', 'config', 'tmp', 'work'):
        (root/name).mkdir(mode=0o700)
    (root/'CLAUDE.md').write_text(UNRELATED, encoding='utf-8')
    (root/'work/CLAUDE.md').write_text(UNRELATED, encoding='utf-8')
    (root/'config/settings.json').write_text(json.dumps({'env': {'PAL_UNRELATED': UNRELATED}}), encoding='utf-8')
    env = {'HOME': str(root/'home'), 'USERPROFILE': str(root/'home'), 'CLAUDE_CONFIG_DIR': str(root/'config'),
           'TMPDIR': str(root/'tmp'), 'TMP': str(root/'tmp'), 'TEMP': str(root/'tmp')}
    if os.name == 'nt':
        system = os.environ.get('SystemRoot')
        if not system or not Path(system).is_absolute():
            raise ValueError('probe_system_root_unavailable')
        env.update(SYSTEMROOT=system, WINDIR=system)
    spec = process.ResearchProcessSpec((image,), str(root/'work'), tuple(sorted(env.items())),
        digest, 20000, max_executable_bytes=size)
    # Validate the selected image before any test callback, not just trust a label.
    try:
        info = Path(image).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError
        process._verify_executable(spec)
    except Exception:
        raise ValueError('probe_image_unavailable') from None
    runner = process.run_research_process if process_runner is None else process_runner
    result = dict(schema_version='claude-cli-probe-v1', mode=mode, image_sha256=digest,
        image_bytes=size, cli_version=CLAUDE_VERSION, version_matches=False,
        version_status='not_started', process_status='not_started', decoder_status='not_attempted',
        reported_tokens=None, response_matches=False, test_endpoint_override='numeric-loopback-http',
        activation_authorized=False, external_egress_verified=False, outside_root_verified=False,
        transient_writes_verified=False, vendor_provenance_verified=False)
    before = state_snapshot(root)
    if not before['complete']:
        raise ValueError('probe_initial_snapshot_failed')
    with loopback_server(mode) as server:
        profile = ClaudeExecProfile(spec, 'https://127.0.0.1:'+str(server.server_port))
        request = test_input()
        public = profile.prepare(request, api_key=TEST_KEY)
        modified = dict(public.environment)
        modified['ANTHROPIC_BASE_URL'] = 'http://127.0.0.1:'+str(server.server_port)
        selected = replace(public, environment=tuple(sorted(modified.items())))
        result['declared_profile_sha256'] = profile.contract_sha256
        try:
            version = runner(spec=replace(selected, argv=(image, '--version')), stdin=b'', allow_process_start=True)
            result['version_status'] = 'returned'
            result['version_matches'] = (type(version) is process.ResearchProcessResult
                and version.stderr_bytes == 0 and version.stdout in (VERSION_OUTPUT.encode(), VERSION_OUTPUT.replace('\n', '\r\n').encode()))
        except process.ResearchProcessError:
            result['version_status'] = 'failed'
        if result['version_matches'] and server.count == server.unexpected == server.faults == 0:
            try:
                raw = runner(spec=selected, stdin=request.prompt_json.encode(), allow_process_start=True)
                result['process_status'] = 'returned'
                try:
                    reply = decode_claude_result(raw, request=request, call_number=1)
                    result['decoder_status'] = 'accepted'
                    result['reported_tokens'] = reply.total_tokens
                    result['response_matches'] = (len(reply.calls) == 1
                        and reply.calls[0].name == 'search_evidence'
                        and strict_json(reply.calls[0].arguments_json) == {'query': RESPONSE})
                except ValueError:
                    result['decoder_status'] = 'rejected'
            except process.ResearchProcessError:
                result['process_status'] = 'failed'
    result.update(request_count=server.count, unexpected_requests=server.unexpected,
                  request_contract_matches=server.matches and server.count == 1,
                  server_faults=server.faults, responses_sent=server.responses)
    result['state'] = state_delta(before, state_snapshot(root, expected_root=before['root_identity']))
    return result


def observation_passed(value):
    """Engineering subset only. Success can NEVER authorize real activation."""
    state = value['state']
    common = (value['version_matches'] is True and value['request_count'] == 1
        and value['unexpected_requests'] == value['server_faults'] == 0
        and value['request_contract_matches'] is True and value['responses_sent'] == 1
        and state['complete'] is True and state['changed_entries'] == state['unsafe_entries'] == 0
        and state['sentinel_files'] == 0 and state['limit_reached'] is False)
    if value['mode'] == 'success':
        return (common and value['decoder_status'] == 'accepted' and value['reported_tokens'] == 26
                and value['response_matches'] is True)
    return common and value['mode'] in MODES and (value['process_status'] == 'failed'
        or value['process_status'] == 'returned' and value['decoder_status'] == 'rejected')
