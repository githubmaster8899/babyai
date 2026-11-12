"""The overlay's little sounds (babyai/ui/sounds.py). No display, no speakers: the player is given a fake player program."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import wave
from pathlib import Path

from babyai.config.loader import DEFAULT_CONFIG
from babyai.ui import sounds
from babyai.ui.sounds import SOUNDS, SoundPlayer, clamp_volume, make_wav


class FakeProcess:
    def __init__(self, done: bool = False) -> None:
        self.done = done

    def poll(self) -> int | None:
        return 0 if self.done else None


class Rig:
    """A player with a clock moved by hand, a program finder, and a "run" that only records what it was asked to start."""

    def __init__(self, **options: object) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = 100.0
        self.started: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.available = {"paplay": "/usr/bin/paplay"}
        self.fail_to_start = False
        settings = {"enabled": True, "directory": Path(self._tmp.name) / "sounds", **options}
        self.player = SoundPlayer(find=self.available.get, run=self._run, clock=lambda: self.now, **settings)  # type: ignore[arg-type]

    def _run(self, command: list[str], **kwargs: object) -> FakeProcess:
        if self.fail_to_start:
            raise OSError("no sound card")
        self.started.append(command)
        process = FakeProcess()
        self.processes.append(process)
        return process

    def close(self) -> None:
        self._tmp.cleanup()


class SoundTests(unittest.TestCase):
    def rig(self, **options: object) -> Rig:
        rig = Rig(**options)
        self.addCleanup(rig.close)
        return rig

    def test_they_are_off_by_default_in_the_config(self) -> None:
        self.assertIs(DEFAULT_CONFIG["ui"]["sounds"], False)
        self.assertFalse(SoundPlayer.from_config(DEFAULT_CONFIG["ui"]).enabled)
        self.assertFalse(SoundPlayer.from_config(None).enabled)

    def test_only_a_real_true_turns_them_on(self) -> None:
        for value in ("true", "yes", 1, "on", None):
            with self.subTest(value=value):
                self.assertFalse(SoundPlayer.from_config({"sounds": value}).enabled)
        self.assertTrue(SoundPlayer.from_config({"sounds": True}).enabled)

    def test_nothing_is_played_while_they_are_off(self) -> None:
        rig = self.rig(enabled=False)
        self.assertFalse(rig.player.play("wake"))
        self.assertEqual(rig.started, [])
        self.assertFalse((Path(rig._tmp.name) / "sounds").exists(), "not even a file is made")

    def test_a_sound_is_played_with_the_first_player_found(self) -> None:
        rig = self.rig()
        rig.available.update({"aplay": "/usr/bin/aplay"})
        self.assertTrue(rig.player.play("wake"))
        self.assertEqual(rig.started[0][0], "/usr/bin/paplay")
        self.assertTrue(rig.started[0][-1].endswith("wake-40.wav"))
        self.assertTrue(os.path.exists(rig.started[0][-1]))

    def test_the_players_are_tried_in_order_and_aplay_is_told_to_be_quiet(self) -> None:
        rig = self.rig()
        rig.available.clear()
        rig.available["aplay"] = "/usr/bin/aplay"
        rig.player.play("reply")
        self.assertEqual(rig.started[0][:2], ["/usr/bin/aplay", "-q"])
        rig.available["pw-play"] = "/usr/bin/pw-play"
        rig.now += 1
        rig.player.play("reply")
        self.assertEqual(rig.started[1][0], "/usr/bin/pw-play")

    def test_without_any_player_it_is_silent_and_does_not_fail(self) -> None:
        rig = self.rig()
        rig.available.clear()
        self.assertFalse(rig.player.play("wake"))
        self.assertEqual(rig.started, [])

    def test_a_player_that_will_not_start_silences_it_for_good(self) -> None:
        rig = self.rig()
        rig.fail_to_start = True
        self.assertFalse(rig.player.play("wake"))
        rig.fail_to_start = False
        rig.now += 5
        self.assertFalse(rig.player.play("wake"), "no retrying a broken sound system on every click")
        self.assertEqual(rig.started, [])

    def test_a_cache_folder_that_cannot_be_written_silences_it_too(self) -> None:
        rig = self.rig(directory=Path("/proc/no/such/place"))
        self.assertFalse(rig.player.play("wake"))
        self.assertTrue(rig.player._broken)

    def test_an_unknown_name_is_ignored(self) -> None:
        rig = self.rig()
        self.assertFalse(rig.player.play("explode"))
        self.assertEqual(rig.started, [])

    def test_the_same_sound_is_not_rattled_off(self) -> None:
        rig = self.rig()
        self.assertTrue(rig.player.play("pet"))
        rig.now += sounds.MIN_GAP_S / 2
        self.assertFalse(rig.player.play("pet"))
        rig.now += sounds.MIN_GAP_S
        self.assertTrue(rig.player.play("pet"))
        self.assertEqual(len(rig.started), 2)

    def test_a_different_sound_is_not_held_back_by_that(self) -> None:
        rig = self.rig()
        self.assertTrue(rig.player.play("pet"))
        self.assertTrue(rig.player.play("reply"))

    def test_not_too_many_at_once_and_room_again_when_they_finish(self) -> None:
        rig = self.rig()
        for name in ("wake", "reply", "ask"):
            self.assertTrue(rig.player.play(name))
        rig.now += 5
        self.assertFalse(rig.player.play("pet"), f"{sounds.MAX_AT_ONCE} are already going")
        rig.processes[0].done = True
        self.assertTrue(rig.player.play("pet"))

    def test_the_volume_is_in_the_file_name_and_in_the_sound(self) -> None:
        loud, quiet = self.rig(volume=1.0), self.rig(volume=0.2)
        loud.player.play("wake")
        quiet.player.play("wake")
        self.assertTrue(loud.started[0][-1].endswith("wake-100.wav"))
        self.assertTrue(quiet.started[0][-1].endswith("wake-20.wav"))
        self.assertNotEqual(Path(loud.started[0][-1]).read_bytes(), Path(quiet.started[0][-1]).read_bytes())

    def test_silent_at_zero_volume(self) -> None:
        rig = self.rig(volume=0)
        self.assertFalse(rig.player.play("wake"))
        self.assertEqual(rig.started, [])

    def test_a_damaged_file_is_made_again(self) -> None:
        rig = self.rig()
        rig.player.play("wake")
        path = Path(rig.started[0][-1])
        good = path.read_bytes()
        path.write_bytes(b"junk")
        rig.now += 5
        rig.player.play("wake")
        self.assertEqual(path.read_bytes(), good)

    def test_the_cache_folder_follows_xdg(self) -> None:
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = "/somewhere/cache"
        self.addCleanup(lambda: os.environ.pop("XDG_CACHE_HOME") if old is None else os.environ.update(XDG_CACHE_HOME=old))
        self.assertEqual(sounds.cache_dir(), Path("/somewhere/cache/babyai/sounds"))


class WavTests(unittest.TestCase):
    def test_every_sound_is_a_short_valid_wav(self) -> None:
        for name in SOUNDS:
            with self.subTest(name=name), wave.open(io.BytesIO(make_wav(name))) as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, sounds.RATE))
                seconds = wav.getnframes() / wav.getframerate()
                self.assertGreater(seconds, 0.1)
                self.assertLess(seconds, 1.0)

    def test_the_same_bytes_every_time(self) -> None:
        self.assertEqual(make_wav("ask", 0.4), make_wav("ask", 0.4))

    def test_louder_is_louder_and_zero_is_silence(self) -> None:
        def peak(volume: float) -> int:
            with wave.open(io.BytesIO(make_wav("wake", volume))) as wav:
                raw = wav.readframes(wav.getnframes())
            return max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True)) for i in range(0, len(raw), 2))

        self.assertGreater(peak(1.0), peak(0.5))
        self.assertGreater(peak(0.5), peak(0.1))
        self.assertEqual(peak(0.0), 0)

    def test_the_notes_fade_so_there_is_no_click(self) -> None:
        with wave.open(io.BytesIO(make_wav("wake", 1.0))) as wav:
            raw = wav.readframes(wav.getnframes())
        first = int.from_bytes(raw[:2], "little", signed=True)
        last = int.from_bytes(raw[-2:], "little", signed=True)
        self.assertEqual((first, last), (0, 0))

    def test_a_rest_is_silent(self) -> None:
        with wave.open(io.BytesIO(make_wav("ask", 1.0))) as wav:
            raw = wav.readframes(wav.getnframes())
        start = int(sounds.RATE * 0.12) * 2
        gap = raw[start + 2: start + int(sounds.RATE * 0.04) * 2 - 2]
        self.assertTrue(gap and not any(gap))

    def test_volume_values_that_make_no_sense_are_replaced(self) -> None:
        self.assertEqual(clamp_volume(5), 1.0)
        self.assertEqual(clamp_volume(-1), 0.0)
        for bad in ("loud", None, True, float("nan"), [0.5]):
            with self.subTest(bad=bad):
                self.assertEqual(clamp_volume(bad), sounds.DEFAULT_VOLUME)


if __name__ == "__main__":
    unittest.main()
