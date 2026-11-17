import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('field_setup', Path(__file__).with_name('field_setup.py'))
field = importlib.util.module_from_spec(spec)
spec.loader.exec_module(field)


class FieldSetupTest(unittest.TestCase):
    def track(self, **kwargs):
        return {'Reference ID': 'C0A80165 (192.168.1.101)', 'Leap status': 'Normal',
                'System time': '0.00001 seconds slow', 'Root delay': '0.0001 seconds',
                'Root dispersion': '0.0001 seconds', **kwargs}

    def test_offline_base_ready(self):
        self.assertTrue(field.idle_base_ready({'locked': False}, self.track(),
                        '^,*,192.168.1.101,8,2,377,1,0.00001,0.00001,0.0001'))

    def test_reject_wrong_peer_stale_large_uncertain_samples(self):
        good = '^,*,192.168.1.101,8,2,377,1,0.00001,0.00001,0.0001'
        for track, sample in [
            (self.track(**{'Reference ID': 'C0A8003E'}), good),
            (self.track(**{'System time': '10 seconds slow'}), good),
            (self.track(**{'Root dispersion': '0.1 seconds'}), good),
            (self.track(**{'System time': 'nan seconds'}), good),
            (self.track(), good.replace(',377,1,', ',377,30,')),
            (self.track(), good.replace(',0.00001,0.0001', ',0.01,0.1')),
        ]:
            with self.subTest(track=track, sample=sample):
                self.assertFalse(field.idle_base_ready({'locked': False}, track, sample))

    def test_cannot_prepare_after_ros_launch(self):
        with self.assertRaisesRegex(RuntimeError, 'clock lock'):
            field.idle_base_ready({'locked': True}, self.track(), '')

    def test_nginx_changes_only_rover_upstream_and_is_idempotent(self):
        source = ('upstream mr2_gateway { server 127.0.0.1:8081; }\n'
                  'upstream mr2_rover_rosbridge {\n server 192.168.0.59:9090;\n}\n'
                  'server { location /latency/uplink/ { proxy_pass http://mr2_gateway; } }')
        result = field.render_field_nginx(source)
        self.assertIn('server 192.168.1.102:9090;', result)
        self.assertIn('server 127.0.0.1:8081;', result)
        self.assertEqual(field.render_field_nginx(result), result)

    def test_nginx_refuses_unexpected_configuration(self):
        for source in ('', 'upstream mr2_rover_rosbridge { server 1:9090; }'):
            with self.assertRaises(RuntimeError):
                field.render_field_nginx(source)


if __name__ == '__main__':
    unittest.main()
