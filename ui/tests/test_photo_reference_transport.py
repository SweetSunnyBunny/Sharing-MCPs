"""Contract with the installed Photos MCP: no network or paid image calls."""
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

SERVICE = Path('C:/Apps/mcp/services/photos/server.py')


@unittest.skipUnless(SERVICE.is_file(), 'Installed Photos MCP integration test')
class PhotoReferenceTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('photo_service_reference_test', SERVICE)
        cls.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.service)

    def test_reference_bytes_use_edit_endpoint_and_old_calls_still_generate(self):
        service = self.service
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.png'
            Image.new('RGB', (16, 16), 'green').save(path)
            original = path.read_bytes()
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({
                'data': [{'b64_json': base64.b64encode(original).decode()}]}).encode()
            with patch.object(service, '_openai_key', return_value='fake'), \
                    patch.object(service, 'CHAT_DIR', Path(folder)), \
                    patch.object(service.urllib.request, 'urlopen', return_value=response) as send:
                result = service.photo_generate('School group', subject='new', reference_paths=[str(path)])
                self.assertTrue(result.startswith('Saved ('))
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, service.EDIT_API_URL)
                payload = json.loads(request.data)
                encoded = payload['images'][0]['image_url']
                self.assertTrue(encoded.startswith('data:image/png;base64,'))
                self.assertEqual(base64.b64decode(encoded.split(',', 1)[1]), original)
                if service.MODEL.startswith('gpt-image-1'):
                    self.assertEqual(payload['input_fidelity'], 'high')
                else:
                    self.assertNotIn('input_fidelity', payload)
                self.assertEqual(path.read_bytes(), original)
                result = service.photo_generate('Shoes', subject='plain')
                self.assertTrue(result.startswith('Saved ('))
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, service.API_URL)
                self.assertNotIn('images', json.loads(request.data))

    def test_invalid_references_fail_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.png'
            path.write_text('not image bytes')
            with patch.object(self.service, '_openai_key', return_value='fake'), \
                    patch.object(self.service.urllib.request, 'urlopen') as send:
                for paths in ([str(path)], [str(path)] * 5, ['https://example.com/photo.png']):
                    self.assertTrue(self.service.photo_generate('Photo', reference_paths=paths).startswith('Refused:'))
                send.assert_not_called()
