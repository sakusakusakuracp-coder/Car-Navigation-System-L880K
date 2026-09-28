"""実JPEGと実動画を用いて、確定・復旧・容量不足・古い画像の排除を検証する。"""

import base64
from dataclasses import replace
from io import BytesIO
import json
import socket
import threading
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from camera.catalog import RecordingCatalog, inspect_video
from camera.client import CameraClient
from camera.config import CameraConfig
from camera.frames import Frame, FrameDistributor, validate_jpeg
from camera.health import HealthMonitor
from camera.inputs import StreamReceiver
from camera.segment_writer import SegmentWriter
from camera.service import CameraService
from subcontroller_gateway_service import SubcontrollerGateway


def jpeg(color="red"):
    """再生検査に使う既知の画素を持つJPEGを生成する。"""
    buffer = BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, "JPEG")
    return buffer.getvalue()


class CameraTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = CameraConfig(self.root, self.root / "catalog.sqlite3", local_cameras={}, remote_cameras=("front",),
                                   width=64, height=48, fps=10, low_space_bytes=1024, resume_space_bytes=2048,
                                   segment_max_bytes=1024 * 1024, segment_duration_s=1)
        self.catalog = RecordingCatalog(self.config)
        self.health = HealthMonitor(self.config)
        self.writer = SegmentWriter("front", self.config, self.catalog, self.health)
        self.now = time.monotonic()

    def tearDown(self):
        if self.writer.container:
            self.writer.abort("TEST_END")
        self.temp.cleanup()

    def frame(self, seq=1, elapsed=0, session="first"):
        return Frame("front", session, seq, self.now + elapsed, jpeg(), 64, 48)

    def rows(self):
        with self.catalog.shared.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM recordings ORDER BY ready_at")]

    def test_real_video_rotation_and_upload_catalog(self):
        self.writer.write_packet(self.frame(1, 0))
        self.writer.write_packet(self.frame(2, .2))
        self.assertEqual(self.rows(), [])
        self.writer.write_packet(self.frame(3, 1.1))
        self.writer.stop_recording()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([inspect_video(self.root / r["relative_path"]) for r in rows], [2, 1])
        self.assertTrue(all(r["state"] == "READY" and r["checksum"] for r in rows))
        self.assertEqual(list(self.root.glob("*.part")), [])
        self.assertIsNotNone(self.catalog.shared.next_item(False))

    def test_new_stream_must_start_new_video(self):
        self.writer.write_packet(self.frame())
        self.writer.write_packet(self.frame(1, .2, "reconnected"))
        self.writer.stop_recording()
        self.assertEqual(len(self.rows()), 2)

    def test_storage_reservations_are_shared(self):
        space = type("Space", (), {"free": self.config.segment_max_bytes + 4096})()
        with patch("camera.health.shutil.disk_usage", return_value=space):
            self.assertTrue(self.health.reserve_storage("first"))
            self.assertFalse(self.health.reserve_storage("second"))
            self.health.release_storage("first")
            self.assertTrue(self.health.reserve_storage("second"))

    def test_invalid_video_is_never_ready(self):
        self.writer.write_packet(self.frame())
        with patch("camera.segment_writer.inspect_video", side_effect=ValueError("corrupt")):
            with self.assertRaises(ValueError):
                self.writer.stop_recording()
        self.assertEqual(self.rows(), [])
        with self.catalog.shared.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM capture_segments").fetchone()[0], "INCOMPLETE")

    def test_ready_registration_failure_is_recoverable(self):
        self.writer.write_packet(self.frame())
        with patch.object(self.catalog, "mark_ready", side_effect=RuntimeError("busy")):
            with self.assertRaises(RuntimeError):
                self.writer.stop_recording()
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.catalog.recover_catalog()), 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.catalog.recover_catalog(), [])

    def test_recovery_does_not_promote_unvalidated_partial(self):
        partial, _ = self.catalog.mark_writing("interrupted", "front")
        partial.write_bytes(b"not a video")
        self.catalog.recover_catalog()
        self.assertEqual(self.rows(), [])
        self.assertTrue(partial.exists())

    def test_recovery_rejects_tampered_final(self):
        self.writer.write_packet(self.frame())
        with patch.object(self.catalog, "mark_ready", side_effect=RuntimeError("busy")):
            with self.assertRaises(RuntimeError):
                self.writer.stop_recording()
        next(self.root.glob("*.mkv")).write_bytes(b"replaced")
        self.assertEqual(self.catalog.recover_catalog(), [])
        self.assertEqual(self.rows(), [])

    def test_slow_display_does_not_grow_queue_or_stop_recording(self):
        distributor = FrameDistributor(1000)
        key = distributor.subscribe_frames("front", 42, "ui")
        for i in range(1, 50):
            distributor.publish_frame(self.frame(i))
        event = distributor.get_frame(key)
        self.assertEqual(event["sequence"], 49)
        self.assertEqual(event["display_generation"], 42)
        distributor.unsubscribe_frames(key)
        self.writer.write_packet(self.frame())
        self.writer.stop_recording()
        self.assertEqual(len(self.rows()), 1)

    def test_stale_frame_not_displayed(self):
        distributor = FrameDistributor(100)
        key = distributor.subscribe_frames("front", 1, "ui")
        distributor.publish_frame(replace(self.frame(), received_mono=time.monotonic() - 1))
        self.assertNotIn("jpeg_base64", distributor.get_frame(key))

    def test_invalid_jpeg_and_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            validate_jpeg(jpeg(), 400000, 100, 100)
        with self.assertRaises(ValueError):
            validate_jpeg(jpeg(), 10, 64, 48)

    def test_record_queue_bounded_display_still_updates(self):
        service = CameraService(replace(self.config, queue_frames=1))
        service.accept_frame(self.frame(1))
        service.accept_frame(self.frame(2))
        self.assertEqual(service.queues["front"].qsize(), 1)
        self.assertEqual(service.status()["cameras"]["front"]["drop_count"], 1)
        key = service.distributor.subscribe_frames("front", 1, "ui")
        self.assertEqual(service.distributor.get_frame(key)["sequence"], 2)

    def test_disabled_recording_still_displays(self):
        service = CameraService(replace(self.config, recording_enabled=False))
        service.accept_frame(self.frame())
        self.assertTrue(service.queues["front"].empty())
        self.assertFalse(service.status()["recording"])

    def test_invalid_config_rejected(self):
        for config in (replace(self.config, local_cameras={"left": 0, "right": 0}),
                       replace(self.config, recording_enabled="false"), replace(self.config, fps=0)):
            with self.assertRaises(ValueError):
                config.validate()

    def test_receiving_times_preserve_gaps_in_recorded_video(self):
        import av

        for seq, elapsed in enumerate((0, .2, .8), 1):
            self.writer.write_packet(self.frame(seq, elapsed))
        self.writer.stop_recording()
        with av.open(str(self.root / self.rows()[0]["relative_path"])) as video:
            times = [float(f.pts * f.time_base) for f in video.decode(video=0)]
        self.assertEqual(times, [0, .2, .8])

    def test_storage_failure_stops_reservations_for_all_directions(self):
        self.health.storage_error()
        self.assertFalse(self.health.reserve_storage("another-camera"))
        self.assertEqual(self.health.check_health()["storage_state"], "ERROR")

    def test_stop_does_not_start_another_segment(self):
        self.writer.write_packet(self.frame())
        self.writer.allow_new_segments = False
        self.writer.write_packet(self.frame(2, 2))
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNone(self.writer.container)

    def test_real_gateway_to_recording_service(self):
        gateway = SubcontrollerGateway("127.0.0.1", 0)
        thread = threading.Thread(target=gateway.start, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while gateway._listener is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(gateway._listener)
        port = gateway._listener.getsockname()[1]
        service = CameraService(replace(self.config, gateway_port=port))
        try:
            service.start()
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sender:
                for seq in range(1, 6):
                    sender.sendall((json.dumps(RemoteTest().message(seq)) + "\n").encode())
                    time.sleep(.02)
            deadline = time.monotonic() + 4
            while service.status()["cameras"]["front"]["input_state"] != "STREAMING" and time.monotonic() < deadline:
                time.sleep(.02)
            request = service.request({"operation": "subscribe_video", "camera_id": "front", "display_generation": 4, "consumer_boot_id": "test"})
            event = service.request({"operation": "get_frame", "subscription_id": request["subscription_id"]})
            self.assertEqual(event["validity"], "VALID")
            time.sleep(.2)
        finally:
            closed = service.close()
            gateway.stop()
            thread.join(2)
        self.assertTrue(closed)
        self.assertEqual(sum(inspect_video(self.root / r["relative_path"]) for r in self.rows()), 5)


class RemoteTest(unittest.TestCase):
    def test_ui_client_reports_unsupported_socket(self):
        events = []
        client = CameraClient("unused", events.append)
        with patch("camera.client.socket", spec=[]):
            client._run()
        self.assertEqual(events, [{"camera_disconnected": True, "reason": "UNIX_SOCKET_UNSUPPORTED"}])

    def test_frames_do_not_replace_vehicle_status(self):
        gateway = SubcontrollerGateway("127.0.0.1", 0)
        status = {**self.message(), "event": "sub2.status", "signals": {"left": True}}
        gateway.accept(status)
        gateway.accept(self.message(2))
        self.assertEqual(gateway.snapshot()["sources"][status["source"]], status)
        rejected = gateway.accept({**self.message(100), "camera_id": "rear"})
        self.assertFalse(rejected["accepted"])
        self.assertTrue(gateway.accept(self.message(3))["accepted"])
        gateway.accept(self.message(1, "boot-b"))
        self.assertNotIn(status["source"], gateway.snapshot()["sources"])

    def message(self, seq=1, boot="boot-a"):
        return {"schema_version": "1.0", "source": "16 サブコントローラ2前方系", "camera_id": "front",
                "boot_id": boot, "sequence": seq, "frame_sequence": seq, "stream_session_id": "camera",
                "event": "camera.frame", "width": 64, "height": 48, "jpeg_base64": base64.b64encode(jpeg()).decode()}

    def test_gateway_retains_frames_separately_and_bounds_queue(self):
        gateway = SubcontrollerGateway("127.0.0.1", 0)
        for seq in range(1, 21):
            gateway.accept(self.message(seq))
        gateway.accept({**self.message(21), "event": "sub2.status"})
        batch = gateway.take_frames()
        self.assertEqual(len(batch["frames"]), 16)
        self.assertEqual(batch["dropped"]["front"], 4)
        self.assertEqual(gateway.take_frames()["frames"], [])

    def test_remote_clock_is_unsynchronized_and_old_boot_is_rejected(self):
        config = CameraConfig(Path("."), Path("catalog"))
        receiver = StreamReceiver(config)
        message = {**self.message(), "gateway_age_ms": 5}
        frame = receiver.accept_frame(message)
        self.assertEqual(frame.clock_quality, "UNSYNCHRONIZED")
        with self.assertRaises(ValueError):
            receiver.accept_frame(message)
        receiver.accept_frame({**message, "boot_id": "boot-b"})
        with self.assertRaises(ValueError):
            receiver.accept_frame({**message, "frame_sequence": 100})
        with self.assertRaises(ValueError):
            receiver.accept_frame({**message, "boot_id": "boot-c", "gateway_age_ms": 10000})
