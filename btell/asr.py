"""Independent PTT/meeting models; never invent a transcript on failure."""
import asyncio
import io
import shutil
import subprocess
import tempfile
import wave


class ASRError(Exception):
    pass


class WhisperASR:
    def __init__(self, model="large-v3", ptt_model="small"):
        self.model_names = {"ptt": ptt_model, "meeting": model}
        self.models = {}
        self.locks = {"ptt": asyncio.Lock(), "meeting": asyncio.Lock()}

    def ready(self):
        try:
            import faster_whisper  # noqa: F401
            return bool(shutil.which("ffmpeg"))
        except ImportError:
            return False

    def _transcribe(self, audio, kind):
        try:
            from faster_whisper import WhisperModel
            if kind not in self.models:
                self.models[kind] = WhisperModel(self.model_names[kind], device="cpu", compute_type="int8")
            with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
                wav.write(audio)
                wav.flush()
                segments, _ = self.models[kind].transcribe(wav.name, language="zh", vad_filter=True)
                return "".join(s.text for s in segments).strip()
        except Exception as exc:
            raise ASRError("ASR processing failed") from exc

    async def pcm(self, pcm):
        if not pcm or len(pcm) % 2:
            raise ASRError("invalid PCM s16le")
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        async with self.locks["ptt"]:
            return await asyncio.to_thread(self._transcribe, audio.getvalue(), "ptt")

    async def aac(self, data):
        if not shutil.which("ffmpeg"):
            raise ASRError("ffmpeg unavailable")
        try:
            result = await asyncio.to_thread(subprocess.run,
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ASRError("audio conversion failed") from exc
        if result.returncode or not result.stdout:
            raise ASRError("audio conversion failed")
        async with self.locks["meeting"]:
            return await asyncio.to_thread(self._transcribe, result.stdout, "meeting")
