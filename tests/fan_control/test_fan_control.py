import tempfile
import unittest
from pathlib import Path

from fan_control_service import DryRunFanHardware, FanConfig, FanController, KernelPwmOutput


class FanControlTest(unittest.TestCase):
    def test_temperature_policy_enters_fail_safe(self) -> None:
        config = FanConfig.from_mapping({"enabled": True})
        controller = FanController(config, DryRunFanHardware())
        duty, reason = controller.calculate_duty(90.0, 1_000.0)
        self.assertEqual(duty, 1.0)
        self.assertEqual(reason, "OVER_TEMPERATURE")

    def test_single_fan_rpm_is_reported(self) -> None:
        config = FanConfig.from_mapping({"enabled": True})
        controller = FanController(config, DryRunFanHardware())
        event = controller.publish(temperature=50.0, rpm=1_000.0, duty=0.0, reason="COOL", status="RUNNING")
        self.assertEqual(event["fan_rpm"], 1_000.0)
        self.assertNotIn("fan2_rpm", event)
        self.assertEqual(event["pwm_frequency_hz"], 25_000)
        self.assertEqual(event["pwm_backend"], "kernel")
        self.assertEqual(event["pwm_duty"], 0.0)
        self.assertNotIn("common_pwm_duty", event)

    def test_config_rejects_same_gpio(self) -> None:
        with self.assertRaises(ValueError):
            FanConfig.from_mapping({"pwm_gpio": 20, "tach_gpio": 20})

    def test_standard_four_wire_frequency_defaults_to_25khz(self) -> None:
        config = FanConfig.from_mapping({})
        self.assertEqual(config.pwm_frequency_hz, 25_000)
        self.assertEqual(config.pwm_backend, "kernel")

    def test_frequency_outside_four_wire_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FanConfig.from_mapping({"pwm_frequency_hz": 10_000})
        with self.assertRaises(ValueError):
            FanConfig.from_mapping({"pwm_frequency_hz": 30_000})

    def test_software_lgpio_pwm_backend_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FanConfig.from_mapping({"pwm_backend": "lgpio"})

    def test_kernel_pwm_writes_period_and_duty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chip = Path(temp_dir) / "pwmchip0"
            channel = chip / "pwm0"
            channel.mkdir(parents=True)
            for name in ("enable", "period", "duty_cycle"):
                (channel / name).write_text("0", encoding="ascii")
            config = FanConfig.from_mapping({"pwm_chip_path": str(chip)})
            output = KernelPwmOutput(config)
            output.apply_duty(0.5)
            self.assertEqual((channel / "period").read_text(encoding="ascii"), "40000")
            self.assertEqual((channel / "duty_cycle").read_text(encoding="ascii"), "20000")
            self.assertEqual((channel / "enable").read_text(encoding="ascii"), "1")
            output.close()
            self.assertEqual((channel / "enable").read_text(encoding="ascii"), "0")
