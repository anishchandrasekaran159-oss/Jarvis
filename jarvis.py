import os
import io
import json
import threading
import subprocess
import numpy as np
import soundfile as sf
import sounddevice as sd
import keyboard
import pygame
import pvporcupine
import struct
import pyaudio
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq
from elevenlabs.client import ElevenLabs

load_dotenv()

# ── CLIENTS ──────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
eleven_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
porcupine = pvporcupine.create(
    access_key=os.getenv("PORCUPINE_ACCESS_KEY"),
    keywords=["jarvis"]
)

# ── CONSTANTS ────────────────────────────────────────────────────────────────
MEMORY_FILE = "jarvis_memory.json"
KNOWLEDGE_FILE = "jarvis_knowledge.json"
TEMP_AUDIO = "temp_audio.wav"
SAMPLE_RATE = 16000
CHANNELS = 1
VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam

# Replace with your actual Windows username
USERNAME = os.getenv("USERNAME") or "YourUsername"

# ── APP REGISTRY ─────────────────────────────────────────────────────────────
APP_REGISTRY = {
    # Browsers
    "chrome":       r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox":      r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "edge":         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",

    # Dev tools
    "vscode":       rf"C:\Users\{USERNAME}\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "vs code":      rf"C:\Users\{USERNAME}\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "code":         rf"C:\Users\{USERNAME}\AppData\Local\Programs\Microsoft VS Code\Code.exe",

    # Media / productivity
    "spotify":      rf"C:\Users\{USERNAME}\AppData\Roaming\Spotify\Spotify.exe",
    "notepad":      "notepad.exe",
    "calculator":   "calc.exe",
    "explorer":     "explorer.exe",
    "task manager": "taskmgr.exe",
}

# ── SESSION STATE ────────────────────────────────────────────────────────────
# This holds the conversation history for the current session.
# It grows with every message so Groq has context across the whole conversation.
conversation_history = []

# ── MEMORY ───────────────────────────────────────────────────────────────────
def save_memory(data):
    with open(MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def update_memory(key, value):
    memory = load_memory()
    memory[key] = value
    save_memory(memory)

# ── LONG-TERM KNOWLEDGE ───────────────────────────────────────────────────────
def load_knowledge():
    if not os.path.exists(KNOWLEDGE_FILE):
        return {}
    with open(KNOWLEDGE_FILE, "r") as f:
        return json.load(f)

def save_knowledge(data):
    with open(KNOWLEDGE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def extract_and_save(text):
    """Runs in background thread — extracts facts from JARVIS's reply and stores them."""
    knowledge = load_knowledge()
    prompt = f"""Extract any specific facts, preferences, or information about the user from this text.
Return ONLY a JSON object with key-value pairs. If nothing useful, return {{}}.
Text: {text}"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        new_facts = json.loads(raw)
        knowledge.update(new_facts)
        save_knowledge(knowledge)
    except Exception:
        pass  # Silent fail — background task, don't interrupt user

# ── TOOLS ────────────────────────────────────────────────────────────────────
import os
import subprocess
import glob
import winreg  # built-in on Windows, no install needed

# ── AUTO APP DISCOVERY ────────────────────────────────────────────────────────

def get_start_menu_apps() -> dict[str, str]:
    """
    Scans Windows Start Menu folders for .lnk shortcut files.
    Returns a dict of {app_name_lowercase: full_shortcut_path}
    
    Why Start Menu? Because every properly installed app creates a shortcut here.
    It's the same source Windows Search uses when you press the Windows key.
    """
    apps = {}

    # Two Start Menu locations: system-wide and current user
    start_menu_paths = [
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
    ]

    for base_path in start_menu_paths:
        # ** means recursive — finds .lnk files in subfolders too
        pattern = os.path.join(base_path, "**", "*.lnk")
        for shortcut_path in glob.glob(pattern, recursive=True):
            # Get just the filename without extension = the app name
            app_name = os.path.splitext(os.path.basename(shortcut_path))[0].lower()
            apps[app_name] = shortcut_path

    return apps


def get_registry_apps() -> dict[str, str]:
    """
    Reads the Windows Registry uninstall keys to find installed apps and their paths.
    Returns a dict of {app_name_lowercase: exe_path}
    
    Why Registry? It's the authoritative source — every MSI/installer writes here.
    """
    apps = {}

    # These two registry paths contain all installed apps
    reg_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",  # 32-bit apps on 64-bit Windows
    ]

    for reg_path in reg_paths:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)

                    # Every app entry has a DisplayName and optionally InstallLocation
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0].lower()
                        location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                        if location and os.path.isdir(location):
                            apps[name] = location
                    except FileNotFoundError:
                        pass  # Key doesn't have these fields, skip it
                except Exception:
                    continue
        except Exception:
            continue

    return apps


# Build the app cache once at boot — scanning every time would be slow
_APP_CACHE: dict[str, str] = {}

def build_app_cache():
    """
    Called once at JARVIS boot. Builds a combined registry of all installed apps.
    Merges Start Menu shortcuts + Registry entries into one lookup dict.
    """
    global _APP_CACHE
    print("JARVIS: Scanning installed apps...")
    _APP_CACHE = {**get_registry_apps(), **get_start_menu_apps()}
    print(f"JARVIS: Found {len(_APP_CACHE)} apps.")


# ── SMART OPEN APP ────────────────────────────────────────────────────────────

def find_best_match(query: str) -> tuple[str, str] | None:
    """
    Fuzzy-matches the user's query against the app cache.
    Returns (matched_name, path) or None if no match found.
    
    Strategy:
    1. Exact match first
    2. If no exact match, find all apps whose name CONTAINS the query
    3. Pick the shortest match (most specific) — "spotify" over "spotify installer"
    """
    query = query.strip().lower()

    # 1. Exact match
    if query in _APP_CACHE:
        return query, _APP_CACHE[query]

    # 2. Substring match — find everything that contains the query
    matches = [(name, path) for name, path in _APP_CACHE.items() if query in name]

    if not matches:
        return None

    # 3. Pick shortest name = most relevant (avoids "uninstall spotify" over "spotify")
    best = min(matches, key=lambda x: len(x[0]))
    return best


def open_app(app_name: str) -> str:
    """
    Opens an app by name using auto-discovered paths.
    Falls back to manual registry if auto-discovery fails.
    """
    match = find_best_match(app_name)

    if not match:
        return f"I couldn't find '{app_name}' in your installed apps. Try a different name."

    matched_name, path = match

    try:
        # .lnk shortcut files need shell=True to resolve properly
        # Direct .exe files work with shell=False
        if path.endswith(".lnk"):
            os.startfile(path)  # os.startfile handles .lnk resolution natively
        else:
            subprocess.Popen(path, shell=False)

        return f"Opening {matched_name}."

    except FileNotFoundError:
        return f"Found '{matched_name}' in the registry but couldn't locate the file. It may have been moved."
    except Exception as e:
        return f"Something went wrong opening '{matched_name}': {e}"

def get_weather(city="Chennai") -> str:
    """Fetches weather for a city using wttr.in."""
    import requests
    url = f"https://wttr.in/{city}?format=j1"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return f"Couldn't reach weather service. Status: {response.status_code}"
        data = response.json()
        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        description = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        return (
            f"Weather in {city}: {description}, "
            f"{temp_c}°C (feels like {feels_like}°C), "
            f"humidity {humidity}%."
        )
    except Exception as e:
        return f"Couldn't get weather: {e}"

# ── INTENT DETECTION ─────────────────────────────────────────────────────────
def detect_intent(command: str) -> tuple[str, str]:
    """
    Uses Groq to classify what the user wants and extract the key parameter.
    Returns (intent, parameter).
    
    Intents: open_app | get_weather | general
    """
    prompt = f"""Classify this command into one intent. Reply ONLY with JSON, no extra text.

Command: "{command}"

Intents:
- open_app: user wants to open an application. Extract the app name.
- get_weather: user wants weather info. Extract the city (default: Chennai).
- general: anything else. Parameter is empty string.

Reply format: {{"intent": "...", "parameter": "..."}}"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        return parsed.get("intent", "general"), parsed.get("parameter", "")
    except Exception:
        return "general", ""

# ── GROQ BRAIN ───────────────────────────────────────────────────────────────
def ask_groq(user_input: str) -> str:
    """
    Sends the user's message to Groq with full conversation history.
    Streams the response token by token.
    Saves facts in the background.
    Returns the full reply.
    """
    global conversation_history

    # Build system prompt with long-term knowledge injected
    knowledge = load_knowledge()
    memory = load_memory()
    knowledge_str = json.dumps(knowledge, indent=2) if knowledge else "None yet."
    memory_str = f"User's name: {memory.get('user', 'Unknown')}. Sessions: {memory.get('sessions', 1)}."

    system_prompt = f"""You are JARVIS, a personal AI assistant — intelligent, sharp, and concise.
You speak in short, direct sentences. No fluff.

User profile: {memory_str}
Long-term knowledge about the user: {knowledge_str}

Use this knowledge naturally in responses when relevant."""

    # Add user message to history
    conversation_history.append({"role": "user", "content": user_input})

    messages = [{"role": "system", "content": system_prompt}] + conversation_history

    # Stream response
    stream = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
        stream=True,
    )

    print("JARVIS: ", end="", flush=True)
    full_reply = ""
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
            full_reply += delta
    print("\n")

    # Add reply to history (rolling context)
    conversation_history.append({"role": "assistant", "content": full_reply})

    # Keep history from growing too large (last 20 messages = 10 exchanges)
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    # Extract and save facts in background — user never waits for this
    threading.Thread(target=extract_and_save, args=(full_reply,), daemon=True).start()

    return full_reply

# ── VOICE OUTPUT ─────────────────────────────────────────────────────────────
def speak(text: str):
    """Converts text to speech using ElevenLabs and plays it via pygame."""
    try:
        audio_generator = eleven_client.text_to_speech.convert(
            voice_id=VOICE_ID,
            output_format="mp3_44100_128",
            text=text,
            model_id="eleven_turbo_v2_5",
        )
        audio_bytes = b"".join(audio_generator)
        pygame.mixer.init(frequency=44100)
        pygame.mixer.music.load(io.BytesIO(audio_bytes))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
    except Exception as e:
        print(f"[Voice output error: {e}]")

# ── VOICE INPUT ───────────────────────────────────────────────────────────────
def listen() -> str:
    """Push-to-talk: hold SPACEBAR to record, release to transcribe via Groq Whisper."""
    print("JARVIS: Hold SPACEBAR to speak...")
    keyboard.wait("space", suppress=True)
    print("JARVIS: Recording... (release SPACEBAR to stop)")

    recorded_chunks = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32") as stream:
        while keyboard.is_pressed("space"):
            chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))
            recorded_chunks.append(chunk)

    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO, audio_data, SAMPLE_RATE)

    with open(TEMP_AUDIO, "rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            response_format="text",
        )
    os.remove(TEMP_AUDIO)
    return transcription.strip()

# ── WAKE WORD ─────────────────────────────────────────────────────────────────
def wait_for_wake_word():
    """Listens continuously via PyAudio until 'Hey JARVIS' is detected."""
    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )
    print("JARVIS: Listening for wake word... (say 'Hey JARVIS')")
    try:
        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
            result = porcupine.process(pcm)
            if result >= 0:
                print("JARVIS: Wake word detected!")
                break
    finally:
        audio_stream.stop_stream()
        audio_stream.close()
        pa.terminate()

# ── BOOT ──────────────────────────────────────────────────────────────────────
def boot_jarvis():
    build_app_cache()
    """Handles first-time setup and returning user greeting."""
    memory = load_memory()
    if "user" not in memory:
        name = input("JARVIS: I don't know you yet. What's your name? → ")
        update_memory("user", name)
        update_memory("sessions", 1)
        update_memory("last_seen", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print(f"JARVIS: Got it. Nice to meet you, {name}.")
    else:
        sessions = memory.get("sessions", 1) + 1
        update_memory("sessions", sessions)
        update_memory("last_seen", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        memory = load_memory()
        print(f"JARVIS: Welcome back, {memory['user']}. Session #{sessions}.")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
# Replace your main loop with this:

def run_jarvis():
    boot_jarvis()
    build_app_cache()

    voice_input = input("JARVIS: Voice input mode? (y/n) → ").strip().lower() == "y"
    voice_output = input("JARVIS: Enable voice output? (y/n) → ").strip().lower() == "y"
    wake_word = input("JARVIS: Enable wake word? (y/n) → ").strip().lower() == "y"

    print("\nJARVIS: Online. Ready.\n")

    running = True  # ← shutdown flag

    while running:
        try:
            if wake_word:
                wait_for_wake_word()

            if voice_input:
                command = listen()
                print(f"You: {command}")
            else:
                command = input("You: ").strip()

            if not command:
                continue

            if command.lower() in ["quit", "exit", "goodbye", "bye"]:
                farewell = "Shutting down. See you next time."
                print(f"JARVIS: {farewell}")
                if voice_output:
                    speak(farewell)
                running = False  # ← flip the flag, loop exits cleanly
                break

            intent, parameter = detect_intent(command)

            if intent == "open_app":
                result = open_app(parameter)
                print(f"JARVIS: {result}")
                if voice_output:
                    speak(result)

            elif intent == "get_weather":
                result = get_weather(parameter or "Chennai")
                print(f"JARVIS: {result}")
                if voice_output:
                    speak(result)

            else:
                reply = ask_groq(command)
                if voice_output:
                    speak(reply)

        except KeyboardInterrupt:
            print("\nJARVIS: Interrupted. Shutting down.")
            running = False
            break

    porcupine.delete()  # cleanup porcupine resources
    print("JARVIS: Terminated.")
# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_jarvis()