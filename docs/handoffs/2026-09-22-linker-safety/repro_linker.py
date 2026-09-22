"""Pinned b061 linker contract counterexamples; no actual process/CLI launch.

Run only on an independent checkout with a fresh scratch directory. The loaded
module must match its exact historical SHA256. All external operations are inert
stubs; all writes use synthetic fixtures below scratch. Exit 3 means the expected
historical defects were reproduced, not that software acceptance passed.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

SOURCE = 'b061b7d7ad51cd21d7a64c84eba0941a1fd08085'
FILE = 'tests/support/soak_linker.py'
HASH = '1e1edfd4a90d5f57ab0e4845859e18ed3a30b9f6dd14a4ed3d2d084fe6709965'
BEFORE = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)
INSIDE = dt.datetime(2026, 9, 24, 3, tzinfo=dt.timezone.utc)
AFTER = dt.datetime(2026, 9, 24, 12, 36, 42, tzinfo=dt.timezone.utc)

def digest(path):
    with open(path, 'rb') as h:
        return hashlib.file_digest(h, 'sha256').hexdigest()

def module(path, count):
    spec = importlib.util.spec_from_file_location(f'linker_repro_{count}', path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m

def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')

class Fixture:
    def __init__(self, root, source, count):
        self.root = root
        root.mkdir()
        self.m = module(source, count)
        self.control = root / 'control'; self.control.mkdir()
        self.original = root / 'original'; self.original.mkdir()
        pkg = root / 'pkg'; pkg.mkdir()
        pins = {}
        for name in self.m.PINNED_FILE_FIELDS:
            p = source if name == 'linker' else pkg / (name + '.txt')
            if name != 'linker': p.write_text('SYNTHETIC '+name+'\n', encoding='utf-8')
            pins[name] = {'path': str(p), 'sha256': digest(p)}
        self.report = self.control / 'preflight.json'
        self.new = root / 'new' / 'campaign'
        self.b = dict(schema=self.m.SCHEMA_BINDING, task_id='SYNTHETIC_LINKER_REVIEW',
            **pins, runtime={'python_exe': sys.executable, 'sha256': digest(sys.executable)},
            frozen={'commit': SOURCE, 'tree': '79ec2d7945f9dd2b492e243521e0c166f8fbebd0'},
            original_campaign=str(self.original), new_campaign_root=str(self.new.parent),
            new_campaign_dir=str(self.new), claim_path=str(self.control/'claim.json'),
            state_path=str(self.control/'state.json'),
            started_receipt_path=str(self.control/'started.json'),
            linker_stop_path=str(self.control/'stop'), preflight_report_path=str(self.report),
            preflight_argv=[sys.executable,'-I','-S','-B',pins['preflight']['path'],
                            '--spec',pins['launch_spec']['path'],'--json-out',str(self.report)],
            launch_argv=[str(pkg/'powershell.exe'),'-NoProfile','-File',
                         pins['launcher']['path'],'-SpecPath',pins['launch_spec']['path']],
            launch_window_utc={'earliest':'2026-09-24T02:14:50Z','latest':'2026-09-24T12:36:41Z'},
            deadline_utc='2026-09-28T00:36:41Z',poll_interval_seconds=300,
            evidence_stability_seconds=900)
        self.binding = self.control/'binding.json'; write(self.binding,self.b)
        self.events = []; self.preflight_hook = None; self.launch_rc = 0
        self.make_campaign = True
        # No real OS-process liveness or subprocess execution in this fixture.
        self.m._process_start_utc = lambda pid: BEFORE
        self.m.classify_original = lambda *_: ('OK','synthetic_exited_clean',{})
        self.m.subprocess = SimpleNamespace(run=self.run,
            SubprocessError=subprocess.SubprocessError)
        self.l = self.m.Linker(self.binding, BEFORE)
        assert self.l.cmd_arm() == 0
        self.l.synthetic_clock = INSIDE
    def run(self, argv, **kwargs):
        if argv == self.b['preflight_argv']:
            self.events.append('preflight')
            write(self.report, {'schema':'pal-rv05-preflight-v1','overall':'GO',
                  'checks':[],'unmet_ids':[]})
            if self.preflight_hook: self.preflight_hook()
            return SimpleNamespace(returncode=0)
        if argv == self.b['launch_argv']:
            self.events.append('launch_stub')
            if self.make_campaign:
                self.new.mkdir(parents=True)
                write(self.new/'campaign.json',{'schema':'pal-soak-campaign-v1','synthetic':True})
            return SimpleNamespace(returncode=self.launch_rc)
        raise AssertionError('unexpected subprocess argv (no real command was run)')
    def state(self):
        return json.loads(Path(self.b['state_path']).read_text(encoding='utf-8'))['state']
    def cycle(self): return self.l._cycle('never')

def case_cancel_during_preflight(f):
    def cancel():
        other=f.m.Linker(f.binding,INSIDE)
        assert other.cmd_cancel('synthetic owner cancellation')==0
        assert f.state()=='CANCELLED'
    f.preflight_hook=cancel
    f.cycle()
    actual={'state':f.state(),'launch_stub_calls':f.events.count('launch_stub'),
            'stop_marker_present':f.l.stop_path.exists()}
    return actual['launch_stub_calls']==1 and actual['stop_marker_present'],actual

def case_expires_during_preflight(f):
    f.preflight_hook=lambda:setattr(f.l,'synthetic_clock',AFTER)
    f.cycle()
    actual={'state':f.state(),'launch_stub_calls':f.events.count('launch_stub'),
            'after_latest':f.l.now()>dt.datetime(2026,9,24,12,36,41,tzinfo=dt.timezone.utc)}
    return actual['launch_stub_calls']==1 and actual['after_latest'],actual

def case_failed_receipt_heals_to_started(f):
    f.launch_rc=7; f.make_campaign=False
    first=f.cycle(); first_state=f.state(); second=f.cycle()
    actual={'first_exit':first[0],'first_state':first_state,'second_exit':second[0],
            'second_state':f.state(),'launch_stub_calls':f.events.count('launch_stub')}
    return first_state=='NO_GO' and f.state()=='STARTED',actual

def case_zero_exit_without_campaign_is_started(f):
    f.make_campaign=False; f.cycle()
    receipt=json.loads(f.l.receipt_path.read_text())
    actual={'state':f.state(),'campaign_exists':f.new.exists(),
            'campaign_json_written':receipt['campaign_json_written']}
    return f.state()=='STARTED' and not f.new.exists(),actual

def case_unpinned_command_accepted(f):
    b=dict(f.b)
    b['launch_argv']=[sys.executable,'-I','-S','-B','-c','pass',
                      '--unused-pinned-name',b['launcher']['path']]
    write(f.binding,b)
    accepted=f.m.load_binding(f.binding)
    actual={'accepted':True,'pinned_path_only_unused_argument':
            accepted['launch_argv'][4]=='-c','actual_launches':0}
    return actual['pinned_path_only_unused_argument'],actual

def case_bypass_argv_accepted(f):
    b=dict(f.b)
    b['launch_argv']=[str(f.root/'pkg'/'powershell.exe'),'-NoProfile',
                     '-ExecutionPolicy','Bypass','-File',b['launcher']['path'],
                     '-SpecPath',b['launch_spec']['path']]
    write(f.binding,b);accepted=f.m.load_binding(f.binding)
    return 'Bypass' in accepted['launch_argv'],{'accepted':True,'policy_bypass_accepted':True,
                                             'actual_launches':0}

def case_oversize_valid_prefix_accepted(f):
    p=f.control/'oversize.json';cap=f.m.RECORD_MAX_BYTES
    raw=b'{"state":"STARTED"}';p.write_bytes(raw+b' '*(cap-len(raw))+b'NOT_JSON_TRAILER')
    value,reason=f.m._read_json_record(p)
    return reason=='' and value=={'state':'STARTED'},{'reason':reason,'bytes':p.stat().st_size,
                                                   'read_cap':cap,'accepted_prefix':value}

def windows_probe(f, failed_wait):
    m=f.m; old=ctypes.__dict__.get('WinDLL'); missing=old is None
    api=SimpleNamespace(OpenProcess=lambda *_:17 if failed_wait else 0,
        WaitForSingleObject=lambda *_:0xffffffff,CloseHandle=lambda *_:True)
    # Fake kernel32 only; no Windows call, process probe, or credential access.
    ctypes.WinDLL=lambda *_args,**_kwargs:api
    m.os=SimpleNamespace(name='nt')
    try: actual=m._pid_alive(1234567)
    finally:
        if missing: delattr(ctypes,'WinDLL')
        else: ctypes.WinDLL=old
    return actual is False,{'probe_return':actual,'expected':'UNKNOWN/None',
                            'api_shape':'WAIT_FAILED' if failed_wait else 'OpenProcess NULL',
                            'windows_api_executed':False}

def control_normal(f):
    first=f.cycle();second=f.cycle()
    return first==(0,False) and second==(0,False) and f.events.count('launch_stub')==1,{
        'state':f.state(),'launch_stub_calls':f.events.count('launch_stub')}

def control_cancel_before_cycle(f):
    f.l.cmd_cancel('before');f.cycle()
    return f.state()=='CANCELLED' and not f.events,{'state':f.state(),'calls':f.events}

def control_before_window(f):
    f.l.synthetic_clock=BEFORE;result=f.cycle()
    return result==(0,True) and not f.events,{'state':f.state(),'calls':f.events}

def control_claim_unknown(f):
    f.l.write_claim();f.cycle()
    return f.state()=='UNKNOWN' and not f.events,{'state':f.state(),'calls':f.events}

def control_pin_drift(f):
    Path(f.b['generator']['path']).write_text('CHANGED SYNTHETIC\n')
    f.cycle()
    return f.state()=='NO_GO' and not f.events,{'state':f.state(),'calls':f.events}

CASES=[
 ('L1_cancel_during_preflight',case_cancel_during_preflight),
 ('L2_expired_during_preflight',case_expires_during_preflight),
 ('L3_failed_receipt_heals_to_started',case_failed_receipt_heals_to_started),
 ('L4_zero_exit_missing_campaign_started',case_zero_exit_without_campaign_is_started),
 ('L5_unpinned_command_membership',case_unpinned_command_accepted),
 ('L6_execution_policy_bypass_accepted',case_bypass_argv_accepted),
 ('L7_oversize_prefix_accepted',case_oversize_valid_prefix_accepted),
 ('L8_openprocess_null_reported_dead',lambda f:windows_probe(f,False)),
 ('L9_wait_failed_reported_dead',lambda f:windows_probe(f,True))]
CONTROLS=[('valid_once',control_normal),('cancel_before',control_cancel_before_cycle),
          ('before_window',control_before_window),('lost_ack',control_claim_unknown),
          ('pin_drift',control_pin_drift)]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path)
    p.add_argument('--scratch',required=True,type=Path)
    args=p.parse_args();source=args.source.resolve(); path=source/FILE
    if not path.is_file() or path.is_symlink() or digest(path)!=HASH:
        p.error('exact historical linker source hash required; do not run against a live checkout')
    scratch=args.scratch.absolute()
    for part in (scratch,*scratch.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            p.error('scratch ancestors must not be links/reparse points')
    if source==scratch or source in scratch.parents or scratch in source.parents:
        p.error('scratch and source must not overlap')
    if scratch.exists():p.error('scratch must be absent; preserve prior attempts')
    scratch.mkdir(parents=False)
    results=[];controls=[]
    for i,(name,fn) in enumerate(CASES+CONTROLS):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                f=Fixture(scratch/f'case-{i:02}',path,i)
                ok,actual=fn(f)
            entry={'case':name,'matched_expected_historical_behavior':ok,'actual':actual}
        except Exception as error:
            entry={'case':name,'matched_expected_historical_behavior':False,
                   'setup_error_type':type(error).__name__}
        (results if i<len(CASES) else controls).append(entry)
    report={'schema':'pal-b061-linker-repro-v1','source_commit':SOURCE,'linker_sha256':HASH,
            'counterexamples':results,'controls':controls,
            'reproduced':sum(x['matched_expected_historical_behavior'] for x in results),
            'controls_passed':sum(x['matched_expected_historical_behavior'] for x in controls),
            'real_process_launches':0,'official_cases_run':0,'activation_authorized':False,
            'scope':'Original module, fake preflight/launch/Win32, synthetic local records only'}
    print(json.dumps(report,ensure_ascii=True,sort_keys=True,indent=2))
    return 3 if report['reproduced']==9 and report['controls_passed']==5 else 5

if __name__=='__main__':raise SystemExit(main())
