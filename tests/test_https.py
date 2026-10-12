import ssl
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse
from unittest.mock import patch

from cryptography import x509

import server
from https_support import ensure_certificates


class HttpsTests(unittest.TestCase):
    def test_root_survives_leaf_renewal_for_a_new_lan_address(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = ensure_certificates(Path(directory), "192.168.1.10")
            root = paths["ca"].read_bytes()
            original = x509.load_pem_x509_certificate(paths["cert"].read_bytes())
            reused = ensure_certificates(Path(directory), "192.168.1.10")
            self.assertEqual(original.serial_number, x509.load_pem_x509_certificate(reused["cert"].read_bytes()).serial_number)
            renewed = ensure_certificates(Path(directory), "192.168.1.11")
            leaf = x509.load_pem_x509_certificate(renewed["cert"].read_bytes())
            self.assertEqual(root, renewed["ca"].read_bytes())
            self.assertNotEqual(original.serial_number, leaf.serial_number)
            leaf.verify_directly_issued_by(x509.load_pem_x509_certificate(root))

    def test_https_is_validated_by_the_generated_ca_and_keys_are_not_served(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = ensure_certificates(Path(directory), "127.0.0.1")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(paths["cert"], paths["key"])
            httpd = server.CastDeckServer(("127.0.0.1", 0), server.Handler)
            httpd.media_port = 4173
            httpd.ca_certificate = paths["ca"]
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            base = f"https://127.0.0.1:{httpd.server_port}"
            trusted = ssl.create_default_context(cafile=str(paths["ca"]))
            try:
                with urlopen(base + "/", context=trusted) as response:
                    self.assertIn(b"Join current cast", response.read())
                with urlopen(base + "/setup/castdeck-ca.crt", context=trusted) as response:
                    self.assertEqual(paths["ca"].read_bytes(), response.read())
                # The Chromecast must receive an HTTP media URL even though the
                # phone fetched its library over trusted HTTPS.
                (Path(directory) / "sample.mp4").write_bytes(b"demo media")
                with patch.object(server, "MEDIA_DIRS", (Path(directory),)):
                    with urlopen(base + "/api/media", context=trusted) as response:
                        media = json.load(response)
                self.assertEqual(1, len(media))
                self.assertEqual("http", urlparse(media[0]["castUrl"]).scheme)
                self.assertEqual(4173, urlparse(media[0]["castUrl"]).port)
                from urllib.error import HTTPError, URLError
                with self.assertRaises(HTTPError) as denied:
                    urlopen(base + "/setup/server-key.pem", context=trusted)
                self.assertEqual(404, denied.exception.code)
                denied.exception.close()
                with self.assertRaises(URLError):
                    urlopen(base + "/", context=ssl.create_default_context())
            finally:
                httpd.shutdown()
                httpd.server_close()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
