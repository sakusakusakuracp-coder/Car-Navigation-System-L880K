import unittest

from obd2.decoder import decode_response
from obd2.models import RequestDefinition, ResponseFrame, SerialIdentity, VehicleProfile


class DecoderTest(unittest.TestCase):
    def test_decodes_scaled_value(self) -> None:
        request = RequestDefinition(
            request_id="engine_rpm",
            key="engine_rpm",
            request=b"\x01",
            decode={"offset": 0, "length": 2, "scale": 0.25, "unit": "rpm"},
        )
        profile = VehicleProfile(
            profile_id="test",
            version="1",
            status="confirmed",
            confirmed=True,
            port="/dev/null",
            serial_identity=SerialIdentity(port="/dev/null"),
            host_baudrate=38400,
            kline_baudrate=10400,
            init_steps=(),
            requests=(request,),
        )
        frame = ResponseFrame(b"\x03\xe8", "engine_rpm", 1, 1, 1.0, 1.1)
        sample = decode_response(frame, request, profile)
        self.assertEqual(sample.value, 250)
        self.assertEqual(sample.unit, "rpm")

