import ollama
import os
import sys
import psutil
from core.ai.memory import MemoryManager
import win32gui
import win32process
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
import json
import random
import shutil

def _get_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "AIPetDesktop"
    return Path(__file__).parent.parent.parent

def _get_bundled_config() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "config.json"
    return Path(__file__).parent.parent.parent / "config.json"

CONFIG_DIR  = _get_config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "pet_name":    "Xander",
    "pet_type":    "otter",
    "personality": "playful",
    "model":       "llama3.2",
    "memory_limit": 10,
    "user_name":   "",
    "language":    "AUTO",
    "pet_size":    200
}

PERSONALITY_TRAITS = {
    "playful": "You are energetic, cheerful, love using exclamation marks, and often use cute expressions.",
    "calm":    "You are calm, wise, and speak in a relaxed and thoughtful manner.",
    "sassy":   "You are witty, a little sarcastic, and like to tease — but still loveable.",
    "shy":     "You are shy and soft-spoken. You give short answers and sometimes hesitate."
}

WINDOW_CONTEXT_MAP = {
    # coding
    "visual studio code": "coding on VSCode",
    "code":               "coding on VSCode",
    "pycharm":            "coding on PyCharm",
    "unity":              "making a game on Unity",
    "unreal":             "making a game on Unreal Engine",
    "postman":            "testing APIs on Postman",
    # browser
    "chrome":             "browsing the web",
    "firefox":            "browsing the web",
    "edge":               "browsing the web",
    "youtube":            "watching YouTube",
    # music & media
    "spotify":            "listening to music on Spotify",
    "vlc":                "watching a video on VLC",
    # communication
    "discord":            "chatting on Discord",
    "telegram":           "chatting on Telegram",
    "whatsapp":           "chatting on WhatsApp",
    "slack":              "working on Slack",
    "zoom":               "in a meeting on Zoom",
    # design
    "figma":              "designing on Figma",
    "photoshop":          "editing on Photoshop",
    "illustrator":        "designing on Illustrator",
    "blender":            "working on 3D in Blender",
    # office
    "word":               "writing a document",
    "excel":              "working on a spreadsheet",
    "powerpoint":         "making a presentation",
    "notion":             "taking notes on Notion",
    "obsidian":           "writing on Obsidian",
    "notepad":            "writing notes",
    # file manager
    "file explorer":      "browsing files on File Explorer",
    "windows explorer":   "browsing files on File Explorer",
    # games
    "steam":              "browsing games on Steam",
    "valorant":           "playing Valorant",
    "minecraft":          "playing Minecraft",
    # terminal
    "terminal":           "using the terminal",
    "cmd":                "using the terminal",
    "powershell":         "using PowerShell",
    "windows terminal":   "using the terminal",
}

# process-based detection 
PROCESS_CONTEXT_MAP = {
    "spotify.exe":        "listening to music on Spotify",
    "discord.exe":        "chatting on Discord",
    "slack.exe":          "working on Slack",
    "zoom.exe":           "in a meeting on Zoom",
    "telegram.exe":       "chatting on Telegram",
    "steam.exe":          "browsing games on Steam",
    "obs64.exe":          "streaming or recording on OBS",
    "obs32.exe":          "streaming or recording on OBS",
    "unity.exe":          "making a game on Unity",
    "unrealengine.exe":   "making a game on Unreal Engine",
    "explorer.exe":       "browsing files on File Explorer",
}

FALLBACK_REPLIES = ["Hmm?", "...", "Hehe~", "Yes?"]

# streaming worker

class StreamWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished       = pyqtSignal(str)
    error          = pyqtSignal(str)

    def __init__(self, brain: "AIBrain", user_text: str):
        super().__init__()
        self.brain     = brain
        self.user_text = user_text

    def run(self):
        system_prompt = self.brain.build_system_prompt()
        self.brain.history.append({"role": "user", "content": self.user_text})

        limit = self.brain.config["memory_limit"]
        if len(self.brain.history) > limit * 2:
            self.brain.history = self.brain.history[-(limit * 2):]

        messages = [{"role": "system", "content": system_prompt}] + self.brain.history

        full_reply = ""
        try:
            stream = ollama.chat(
                model=self.brain.config["model"],
                messages=messages,
                stream=True
            )
            for chunk in stream:
                piece = chunk["message"]["content"]
                full_reply += piece
                self.chunk_received.emit(piece)
        except ollama.ResponseError as e:
            # model not found
            model = self.brain.config["model"]
            self.error.emit(f"Model not ready! Run: ollama pull {model}")
            return
        except Exception:
            self.error.emit(self.brain._get_friendly_error())
            return

        self.brain.history.append({"role": "assistant", "content": full_reply})
        self.brain._session_msg_count += 1
        self.finished.emit(full_reply)

# AI Brain

class AIBrain:

    def __init__(self):
        self.config           = self._load_config()
        self.history          = []
        self.ollama_ok        = self._check_ollama()
        self._cached_activity = None
        self.memory           = MemoryManager()
        self._session_msg_count = 0

    def _check_ollama(self) -> bool:
        try:
            ollama.list()
            print("[AIBrain] Ollama connected.")
        except Exception:
            print("[AIBrain] ERROR: Ollama is not running!")
            print("[AIBrain] Run: ollama serve — then restart the app.")
            return False

        # check downloaded model
        try:
            models = ollama.list()
            model_names = [m["name"].split(":")[0] for m in models.get("models", [])]
            target = self.config["model"].split(":")[0]
            if target not in model_names:
                print(f"[AIBrain] Model '{target}' is not downloaded!")
                print(f"[AIBrain] Run: ollama pull {target}")
                self._model_missing = True
            else:
                self._model_missing = False
        except Exception:
            self._model_missing = False

        return True

    def _get_friendly_error(self) -> str:
        if not self.ollama_ok:
            return "Ollama is not running! Run: ollama serve"
        if getattr(self, "_model_missing", False):
            model = self.config["model"]
            return f"Model not ready! Run: ollama pull {model}"
        return self._fallback_reply()

    def ensure_ollama(self) -> bool:
        self.ollama_ok = self._check_ollama()
        return self.ollama_ok

    def _load_config(self) -> dict:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if not CONFIG_PATH.exists():
            bundled = _get_bundled_config()
            if bundled.exists():
                shutil.copy2(str(bundled), str(CONFIG_PATH))

        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return {**DEFAULT_CONFIG, **json.load(f)}
            except Exception:
                pass
        return DEFAULT_CONFIG.copy()

    def update_config(self, **kwargs):
        self.config.update(kwargs)
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass

    def refresh_activity(self):
        try:
            hwnd  = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd).lower()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc_name = psutil.Process(pid).name().lower()
            except Exception:
                proc_name = ""

            for proc_key, context in PROCESS_CONTEXT_MAP.items():
                if proc_key in proc_name:
                    self._cached_activity = context
                    return

            for keyword, context in WINDOW_CONTEXT_MAP.items():
                if keyword in title:
                    self._cached_activity = context
                    return

            self._cached_activity = None
        except Exception:
            self._cached_activity = None

    def _get_activity_context(self) -> str | None:
        return self._cached_activity

    def _get_time_context(self) -> str:
        hour = datetime.now().hour
        if 5  <= hour < 12: return "morning"
        if 12 <= hour < 17: return "afternoon"
        if 17 <= hour < 21: return "evening"
        return "late night"

    def _fallback_reply(self) -> str:
        return random.choice(FALLBACK_REPLIES)

    def build_system_prompt(self) -> str:
        name     = self.config["pet_name"]
        pet_type = self.config["pet_type"]
        traits   = PERSONALITY_TRAITS.get(self.config["personality"], PERSONALITY_TRAITS["playful"])
        activity = self._get_activity_context()

        user_name = self.config.get("user_name", "").strip()
        language  = self.config.get("language", "AUTO")

        prompt = (
            f"You are {name}, a cute pixel-art {pet_type} desktop pet and personal assistant.\n"
            f"{traits}\n"
        )
        if user_name:
            prompt += f"The user's name is {user_name}. Call them by name occasionally.\n"

        if language == "INDONESIA":
            prompt += "Always reply in Bahasa Indonesia only.\n"
        elif language == "ENGLISH":
            prompt += "Always reply in English only.\n"
        else:
            prompt += "Detect the language the user writes in and reply in the same language.\n"

        memory_ctx = self.memory.build_memory_context()
        if memory_ctx:
            prompt += f"\n{memory_ctx}"

        if activity:
            prompt += f"The user is currently {activity}.\n"

        prompt += (
            
            "Keep replies short and in-character. Maximum 1-2 sentences, strictly under 20 words total. If you need more, summarize. "
            "Never break character. Never say you are an AI.\n"
            "Important: never refer to your own gender. Avoid words like he, she, him, her, "
            "his, hers, boy, girl, male, female, cantik, tampan, manis, imut, lucu, "
            "sweet, cute, adorable, pretty, handsome, or any appearance/gender-specific terms "
            "when describing yourself. Never use adjectives to describe your own appearance. "
            "Stay completely gender-neutral and appearance-neutral at all times."
        )
        return prompt

    def create_stream_worker(self, user_text: str) -> StreamWorker:
        if not self.ollama_ok:
            return _FallbackWorker("Ollama is not running! Run: ollama serve")
        if getattr(self, "_model_missing", False):
            model = self.config["model"]
            return _FallbackWorker(f"Model not ready! Run: ollama pull {model}")
        return StreamWorker(self, user_text)

    def _simple_reply(self, prompt: str) -> str:
        if not self.ollama_ok:
            return "Ollama is not running! Run: ollama serve"
        if getattr(self, "_model_missing", False):
            model = self.config["model"]
            return f"Model not ready! Run: ollama pull {model}"
        system = self.build_system_prompt()
        try:
            res = ollama.chat(
                model=self.config["model"],
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt}
                ]
            )
            return res["message"]["content"].strip()
        except ollama.ResponseError:
            model = self.config["model"]
            self._model_missing = True
            return f"Model not ready! Run: ollama pull {model}"
        except Exception:
            return self._fallback_reply()

    def greeting(self) -> str:
        if self.memory.is_first_time():
            prompt = (
                "This is the very first time you meet the user. "
                "Introduce yourself briefly and greet them warmly in one short sentence. "
                "Be excited to meet them for the first time!"
            )
        else:
            count = self.memory.get_session_count()
            prompt = (
                f"You have chatted with the user {count} time(s) before. "
                "Welcome them back warmly in one short sentence, "
                "as if greeting a friend you already know."
            )
        return self._simple_reply(prompt)

    def random_thought(self) -> str:
        activity = self._get_activity_context()
        if activity:
            prompt = f"The user is {activity}. Say one short spontaneous comment about it, in character."
        else:
            prompt = "You are alone. Say one short random thought or feeling."
        return self._simple_reply(prompt)

    def extract_emotion(self, reply: str) -> str:
        if not self.ollama_ok:
            return "neutral"
        try:
            res = ollama.chat(
                model=self.config["model"],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Tag the emotion of this text in ONE word only. "
                        f"Choose from: happy, sad, excited, sleepy, angry, neutral.\n"
                        f"Text: {reply}\n"
                        f"Reply with ONE word only, nothing else."
                    )
                }]
            )
            emotion = res["message"]["content"].strip().lower().split()[0]
            valid = {"happy", "sad", "excited", "sleepy", "angry", "neutral"}
            return emotion if emotion in valid else "neutral"
        except Exception:
            return "neutral"

    def save_session_summary(self) -> None:
        if self._session_msg_count == 0:
            return  # no conversation this session, skip
        if not self.ollama_ok or not self.history:
            return
        # ask LLM to summarize this session history
        history_text = "\n".join(
            f"{m['role'].title()}: {m['content']}"
            for m in self.history[-20:]  # max 20 last messages
        )
        try:
            res = ollama.chat(
                model=self.config["model"],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Summarize this conversation in 1-2 sentences from the pet's perspective. "
                        f"Focus on what was discussed and how the user felt.\n\n"
                        f"{history_text}"
                    )
                }]
            )
            summary = res["message"]["content"].strip()
            self.memory.save_summary(summary, self._session_msg_count)
        except Exception:
            pass

class _FallbackWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished       = pyqtSignal(str)
    error          = pyqtSignal(str)

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def run(self):
        self.chunk_received.emit(self._text)
        self.finished.emit(self._text)