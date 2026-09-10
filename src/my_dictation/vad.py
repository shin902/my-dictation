from __future__ import annotations

import math
import os
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_SUPPORTED_WEBRTC_RATES = {8000, 16000, 32000, 48000}
_SUPPORTED_FRAME_MS = {10, 20, 30}


@dataclass(frozen=True)
class VadResult:
    """The audio-boundary decision made before ASR.

    ``start_ms`` and ``end_ms`` describe the half-open range retained from the
    original recording.  A result with ``applied=False`` means that the
    original bytes were left in place, either because no safe speech boundary
    was found or because the input format is not supported by this lightweight
    implementation.
    """

    enabled: bool
    requested_backend: str
    backend: str
    applied: bool
    padding_ms: int
    original_duration_ms: int | None = None
    trimmed_duration_ms: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    reason: str | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "requested_backend": self.requested_backend,
            "backend": self.backend,
            "applied": self.applied,
            "padding_ms": self.padding_ms,
        }
        for name in (
            "original_duration_ms",
            "trimmed_duration_ms",
            "start_ms",
            "end_ms",
            "reason",
            "fallback_reason",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


class _UnsupportedWave(ValueError):
    pass


class VoiceActivityTrimmer:
    """Trim only the outer non-speech portions of a PCM WAV recording.

    WebRTC VAD is used when the optional ``webrtcvad`` module is available and
    ``backend`` is ``webrtc`` (the default).  A dependency-free RMS energy VAD
    is used as a safe fallback.  The fallback is intentionally conservative:
    if it cannot find enough speech, the original file is retained rather than
    producing an empty or aggressively shortened training sample.

    The operation replaces ``path`` atomically after a successful decision.
    Internal pauses are never removed.  Keeping ``padding_ms`` around the
    first and last detected speech frame protects word boundaries and useful
    breathing/context for later fine-tuning.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        backend: str = "webrtc",
        padding_ms: int = 300,
        frame_ms: int = 30,
        min_speech_ms: int = 90,
        aggressiveness: int = 2,
    ):
        normalized_backend = str(backend).lower()
        if normalized_backend == "builtin":
            normalized_backend = "energy"
        if normalized_backend not in {"webrtc", "energy", "none"}:
            raise ValueError(f"unknown VAD backend: {backend}")
        if padding_ms < 0:
            raise ValueError("VAD padding_ms must be non-negative")
        if frame_ms not in _SUPPORTED_FRAME_MS:
            raise ValueError(f"VAD frame_ms must be one of {sorted(_SUPPORTED_FRAME_MS)}")
        if min_speech_ms < 0:
            raise ValueError("VAD min_speech_ms must be non-negative")
        if aggressiveness not in range(4):
            raise ValueError("VAD aggressiveness must be between 0 and 3")
        self.enabled = bool(enabled)
        self.backend = normalized_backend
        self.padding_ms = int(padding_ms)
        self.frame_ms = int(frame_ms)
        self.min_speech_ms = int(min_speech_ms)
        self.aggressiveness = int(aggressiveness)

    def trim(self, path: Path) -> VadResult:
        """Atomically trim ``path`` in place and return the decision metadata."""
        path = Path(path)
        if not self.enabled or self.backend == "none":
            return VadResult(
                enabled=False,
                requested_backend=self.backend,
                backend="none",
                applied=False,
                padding_ms=self.padding_ms,
                reason="disabled",
            )

        try:
            params = _read_pcm_wav(path)
        except _UnsupportedWave as exc:
            return VadResult(
                enabled=True,
                requested_backend=self.backend,
                backend="none",
                applied=False,
                padding_ms=self.padding_ms,
                reason="unsupported_format",
                fallback_reason=str(exc),
            )

        channels, sample_width, sample_rate, frame_count = params
        if frame_count == 0 or sample_rate <= 0:
            return VadResult(
                enabled=True,
                requested_backend=self.backend,
                backend="none",
                applied=False,
                padding_ms=self.padding_ms,
                original_duration_ms=0,
                trimmed_duration_ms=0,
                reason="empty_audio",
            )

        try:
            flags, actual_backend, fallback_reason = self._speech_flags(path, params)
        except _UnsupportedWave as exc:
            return VadResult(
                enabled=True,
                requested_backend=self.backend,
                backend="none",
                applied=False,
                padding_ms=self.padding_ms,
                original_duration_ms=_duration_ms(frame_count, sample_rate),
                trimmed_duration_ms=_duration_ms(frame_count, sample_rate),
                start_ms=0,
                end_ms=_duration_ms(frame_count, sample_rate),
                reason="unsupported_format",
                fallback_reason=str(exc),
            )
        bounds = _speech_bounds(flags, self.frame_ms, self.min_speech_ms)
        original_duration_ms = _duration_ms(frame_count, sample_rate)
        if bounds is None:
            return VadResult(
                enabled=True,
                requested_backend=self.backend,
                backend=actual_backend,
                applied=False,
                padding_ms=self.padding_ms,
                original_duration_ms=original_duration_ms,
                trimmed_duration_ms=original_duration_ms,
                start_ms=0,
                end_ms=original_duration_ms,
                reason="no_speech_detected",
                fallback_reason=fallback_reason,
            )

        first_frame, last_frame = bounds
        frame_samples = max(1, round(sample_rate * self.frame_ms / 1000))
        start_frame = max(0, first_frame * frame_samples - round(sample_rate * self.padding_ms / 1000))
        end_frame = min(
            frame_count,
            (last_frame + 1) * frame_samples + round(sample_rate * self.padding_ms / 1000),
        )
        # A frame detector can report a padded range that already covers the
        # whole recording.  Avoid rewriting bytes in that common case.
        applied = start_frame > 0 or end_frame < frame_count
        reason = "trimmed" if applied else "speech_spans_recording"
        if applied:
            _replace_wav_frames(path, params, start_frame, end_frame)

        start_ms = _duration_ms(start_frame, sample_rate)
        end_ms = _duration_ms(end_frame, sample_rate)
        return VadResult(
            enabled=True,
            requested_backend=self.backend,
            backend=actual_backend,
            applied=applied,
            padding_ms=self.padding_ms,
            original_duration_ms=original_duration_ms,
            trimmed_duration_ms=_duration_ms(end_frame - start_frame, sample_rate),
            start_ms=start_ms,
            end_ms=end_ms,
            reason=reason,
            fallback_reason=fallback_reason,
        )

    def _speech_flags(
        self,
        path: Path,
        params: tuple[int, int, int, int],
    ) -> tuple[list[bool], str, str | None]:
        channels, sample_width, sample_rate, _ = params
        frames = _iter_mono_frames(path, channels, sample_width, sample_rate, self.frame_ms)
        if self.backend == "energy":
            return _energy_flags(frames), "energy", None

        if sample_rate not in _SUPPORTED_WEBRTC_RATES:
            return (
                _energy_flags(frames),
                "energy",
                f"sample rate {sample_rate} is not supported by WebRTC VAD",
            )
        try:
            import webrtcvad  # type: ignore[import-not-found]

            detector = webrtcvad.Vad(self.aggressiveness)
            flags: list[bool] = []
            frame_samples = max(1, round(sample_rate * self.frame_ms / 1000))
            for frame in frames:
                if len(frame) < frame_samples:
                    frame = frame + [0] * (frame_samples - len(frame))
                pcm = struct.pack(f"<{len(frame)}h", *frame)
                flags.append(bool(detector.is_speech(pcm, sample_rate)))
            return flags, "webrtc", None
        except Exception as exc:
            return _energy_flags(
                _iter_mono_frames(path, channels, sample_width, sample_rate, self.frame_ms)
            ), "energy", f"WebRTC VAD unavailable: {exc}"


# A short alias keeps the public name convenient for callers that use the
# acronym in class names.
VadTrimmer = VoiceActivityTrimmer


def _read_pcm_wav(path: Path) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE":
                raise _UnsupportedWave("compressed WAV is not supported")
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            if channels <= 0 or sample_rate <= 0:
                raise _UnsupportedWave("WAV has invalid channel or sample-rate metadata")
            if sample_width not in {1, 2, 3, 4}:
                raise _UnsupportedWave(f"{sample_width * 8}-bit PCM is not supported")
    except (OSError, EOFError, wave.Error) as exc:
        raise _UnsupportedWave(f"not a readable PCM WAV: {exc}") from exc
    return channels, sample_width, sample_rate, frame_count


def _iter_mono_frames(
    path: Path,
    channels: int,
    sample_width: int,
    sample_rate: int,
    frame_ms: int,
) -> Iterable[list[int]]:
    frame_samples = max(1, round(sample_rate * frame_ms / 1000))
    bytes_per_frame = channels * sample_width
    try:
        with wave.open(str(path), "rb") as audio:
            remaining = audio.getnframes()
            while remaining > 0:
                raw = audio.readframes(min(frame_samples, remaining))
                if not raw:
                    return
                available_frames = len(raw) // bytes_per_frame
                if available_frames <= 0:
                    return
                yield _to_mono_samples(raw[: available_frames * bytes_per_frame], channels, sample_width)
                remaining -= available_frames
    except (OSError, EOFError, wave.Error) as exc:
        raise _UnsupportedWave(f"could not read PCM WAV frames: {exc}") from exc


def _to_mono_samples(raw_frames: bytes, channels: int, sample_width: int) -> list[int]:
    """Convert little-endian PCM of any supported width to signed mono int16."""
    bytes_per_frame = channels * sample_width
    frame_count = len(raw_frames) // bytes_per_frame
    samples: list[int] = []
    for frame_index in range(frame_count):
        offset = frame_index * bytes_per_frame
        total = 0
        for channel in range(channels):
            sample_offset = offset + channel * sample_width
            chunk = raw_frames[sample_offset : sample_offset + sample_width]
            if sample_width == 1:
                value = (chunk[0] - 128) << 8
            elif sample_width == 2:
                value = int.from_bytes(chunk, "little", signed=True)
            elif sample_width == 3:
                value = int.from_bytes(chunk + (b"\xff" if chunk[2] & 0x80 else b"\0"), "little", signed=True) >> 8
            else:
                value = int.from_bytes(chunk, "little", signed=True) >> 16
            total += max(-32768, min(32767, value))
        samples.append(max(-32768, min(32767, round(total / channels))))
    return samples


def _energy_flags(frames: Iterable[list[int]]) -> list[bool]:
    levels: list[float] = []
    for frame in frames:
        if frame:
            levels.append(math.sqrt(sum(sample * sample for sample in frame) / len(frame)) / 32768.0)
    if not levels:
        return []

    # The low percentile estimates the room/device floor.  The absolute floor
    # prevents random single-sample noise from becoming a speech segment, while
    # the relative floor still handles quiet recordings better than a fixed dBFS
    # threshold alone.
    sorted_levels = sorted(levels)
    floor = sorted_levels[(len(sorted_levels) - 1) // 5]
    threshold = max(0.0005, floor * 2.5)
    if max(levels) < threshold:
        return [False] * len(levels)
    return [level >= threshold for level in levels]


def _speech_bounds(flags: list[bool], frame_ms: int, min_speech_ms: int) -> tuple[int, int] | None:
    active = [index for index, is_speech in enumerate(flags) if is_speech]
    if not active:
        return None
    if len(active) * frame_ms < min_speech_ms:
        return None
    return active[0], active[-1]


def _duration_ms(frames: int, sample_rate: int) -> int:
    return round(frames * 1000 / sample_rate)


def _replace_wav_frames(
    path: Path,
    params: tuple[int, int, int, int],
    start_frame: int,
    end_frame: int,
) -> None:
    channels, sample_width, sample_rate, _ = params
    bytes_per_frame = channels * sample_width
    mode = path.stat().st_mode
    fd, temporary = tempfile.mkstemp(prefix=".vad-", suffix=path.suffix or ".wav", dir=path.parent)
    os.close(fd)
    try:
        with wave.open(str(path), "rb") as source, wave.open(temporary, "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            source.setpos(start_frame)
            remaining = max(0, end_frame - start_frame)
            while remaining > 0:
                chunk = source.readframes(min(4096, remaining))
                if not chunk:
                    break
                output.writeframes(chunk)
                frames_written = len(chunk) // bytes_per_frame
                if frames_written <= 0:
                    break
                remaining -= frames_written
        with open(temporary, "rb") as output:
            os.fsync(output.fileno())
        os.chmod(temporary, mode & 0o777)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
