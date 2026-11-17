import concurrent.futures, json, os, subprocess, sys, time
from pathlib import Path
root = Path('/home/mr2/mr2-stack')
sys.path.insert(0, str(root/'rover/ros2_ws/src/mr2_latency_diagnostics'))
from mr2_latency_diagnostics.video_timing import VideoTimingCollector
worker = str(root/'rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/video_sender')
report = {'capture_checks': {}, 'rounds': []}
def control(dev, value=None):
    cmd = ['v4l2-ctl', '-d', '/dev/video'+dev]
    cmd += ['--set-ctrl=exposure_dynamic_framerate='+str(value)] if value is not None else ['--get-ctrl=auto_exposure,exposure_dynamic_framerate']
    return subprocess.check_output(cmd, text=True).strip()
def check(dev):
    control(dev, 1)
    args = [worker, dev+'/capture-check', 'v4l2src', 'device=/dev/video'+dev, 'num-buffers=1', '!', 'image/jpeg,width=640,height=360,framerate=30/1', '!', 'fakesink', 'sync=false', 'async=false']
    p = subprocess.run(args, capture_output=True, text=True, timeout=12)
    after = control(dev)
    assert p.returncode == 0, p.stderr
    assert 'exposure_dynamic_framerate: 0' in after, after
    assert 'auto_exposure: 3' in after, after
    return {'returncode': p.returncode, 'controls': after, 'stderr': p.stderr}
for dev in ['FR', 'RIGHT']:
    report['capture_checks'][dev] = check(dev)
for round_id in range(2):
    for dev in ['FR', 'RIGHT']: control(dev, 1)
    env = dict(os.environ, MR2_VIDEO_TIMING_SOCKET='mr2-fixed-fps-'+str(os.getpid()))
    def run(dev):
        args = [worker, dev, 'v4l2src', 'name=src', 'device=/dev/video'+dev, 'do-timestamp=true', 'num-buffers=300', '!', 'image/jpeg,width=640,height=360,framerate=30/1', '!', 'nvv4l2decoder', 'mjpeg=true', '!', 'nvvidconv', '!', 'video/x-raw(memory:NVMM),format=NV12', '!', 'nvv4l2h264enc', 'name=latency_encoder', 'copy-timestamp=true', 'bitrate=1530000', 'maxperf-enable=true', 'idrinterval=30', 'iframeinterval=30', 'insert-sps-pps=true', '!', 'h264parse', '!', 'rtph264pay', 'name=latency_pay', 'pt=96', '!', 'fakesink', 'sync=false', 'async=false']
        start = time.monotonic()
        p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=25)
        Path('/tmp/mr2-fixed-fps/'+dev+'-'+str(round_id)+'.log').write_text(p.stdout+p.stderr)
        assert p.returncode == 0, p.stderr
        return {'returncode': p.returncode, 'elapsed_s': time.monotonic()-start, 'controls': control(dev)}
    with VideoTimingCollector(['FR','RIGHT'], address='\0'+env['MR2_VIDEO_TIMING_SOCKET']) as collector:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result = dict(zip(['FR','RIGHT'], pool.map(run, ['FR','RIGHT'])))
        time.sleep(.2)
    for dev in result:
        samples = sorted([s['ingest_epoch_us'] for s in collector.samples if s['stream_id']==dev])
        result[dev]['matched_frames'] = len(samples)
        samples = [s for s in samples if s > samples[0]+2_000_000]
        fps = (len(samples)-1)*1e6/(samples[-1]-samples[0])
        result[dev]['actual_input_fps_after_2s'] = fps
        assert len(samples) > 200 and 27 < fps < 33, result[dev]
        assert 'exposure_dynamic_framerate: 0' in result[dev]['controls'], result[dev]
        assert 'auto_exposure: 3' in result[dev]['controls'], result[dev]
    report['rounds'].append(result)
    print(json.dumps(result), flush=True)
Path('/tmp/mr2-fixed-fps/result.json').write_text(json.dumps(report, indent=2))
print('PASS: source-open policy, capture checks, repeated camera opens and real H264 encoding')
