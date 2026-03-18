import os
import io
import json
import time
import glob
import struct
import threading
import subprocess
import winreg
import numpy as np
import soundfile as sf
import sounddevice as sd
import pygame
import pvporcupine
import pyaudio
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq
from elevenlabs.client import ElevenLabs

load_dotenv()

# ── CLIENTS ───────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
eleven_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
porcupine = pvporcupine.create(
    access_key=os.getenv("PORCUPINE_ACCESS_KEY"),
    keywords=["jarvis"]
)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
MEMORY_FILE = "jarvis_memory.json"
KNOWLEDGE_FILE = "jarvis_knowledge.json"
TEMP_AUDIO = "temp_audio.wav"
SAMPLE_RATE = 16000
CHANNELS = 1
VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
USERNAME = os.getenv("USERNAME") or "YourUsername"

# Silence detection — calibrate() will overwrite SILENCE_THRESHOLD at boot
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 2.5   # seconds of silence before stopping recording
MAX_DURATION = 15        # hard cap on recording length
END_COMMANDS = ["do it", "do it now", "that's all", "execute", "over"]

# ── SESSION STATE ─────────────────────────────────────────────────────────────
conversation_history = []
_APP_CACHE: dict[str, str] = {}

# ── MEMORY ────────────────────────────────────────────────────────────────────
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
    """Background thread — extracts real facts from user's own words."""
    knowledge = load_knowledge()
    prompt = f"""Extract specific, verifiable facts about the user from what THEY said.

Rules:
- Only extract things the user explicitly stated about themselves
- Do NOT infer, assume, or guess anything
- Do NOT extract questions, commands, or things the user asked about
- If there are no clear facts about the user, return {{}}

Examples of valid extractions:
- "I love playing chess" → {{"hobby": "chess"}}
- "I'm from Chennai" → {{"city": "Chennai"}}
- "My favourite cricketer is Dhoni" → {{"favourite_cricketer": "MS Dhoni"}}

Examples of invalid extractions (do NOT save these):
- "What's the weather?" → {{}} (a question, not a fact)
- "Open Chrome" → {{}} (a command, not a fact)
- "Tell me something you remember" → {{}} (a question)

User said: "{text}"

Reply ONLY with a JSON object. Nothing else."""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if model wraps in ```json
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        new_facts = json.loads(raw.strip())
        if new_facts:  # only save if there's actually something real
            knowledge.update(new_facts)
            save_knowledge(knowledge)
    except Exception:
        pass
# ── APP DISCOVERY ─────────────────────────────────────────────────────────────
def get_start_menu_apps() -> dict[str, str]:
    apps = {}
    start_menu_paths = [
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
    ]
    for base_path in start_menu_paths:
        pattern = os.path.join(base_path, "**", "*.lnk")
        for shortcut_path in glob.glob(pattern, recursive=True):
            app_name = os.path.splitext(os.path.basename(shortcut_path))[0].lower()
            apps[app_name] = shortcut_path
    return apps

def get_registry_apps() -> dict[str, str]:
    apps = {}
    reg_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for reg_path in reg_paths:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0].lower()
                        location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                        if location and os.path.isdir(location):
                            apps[name] = location
                    except FileNotFoundError:
                        pass
                except Exception:
                    continue
        except Exception:
            continue
    return apps

def build_app_cache():
    global _APP_CACHE
    print("JARVIS: Scanning installed apps...")
    _APP_CACHE = {**get_registry_apps(), **get_start_menu_apps()}
    print(f"JARVIS: Found {len(_APP_CACHE)} apps.")

# ── TOOLS ─────────────────────────────────────────────────────────────────────
def find_best_match(query: str) -> tuple[str, str] | None:
    query = query.strip().lower()
    if query in _APP_CACHE:
        return query, _APP_CACHE[query]
    matches = [(name, path) for name, path in _APP_CACHE.items() if query in name]
    if not matches:
        return None
    return min(matches, key=lambda x: len(x[0]))

def open_app(app_name: str) -> str:
    match = find_best_match(app_name)
    if not match:
        return f"I couldn't find '{app_name}' in your installed apps."
    matched_name, path = match
    try:
        if path.endswith(".lnk"):
            os.startfile(path)
        else:
            subprocess.Popen(path, shell=False)
        return f"Opening {matched_name}."
    except FileNotFoundError:
        return f"Couldn't locate {matched_name}. It may have been moved or uninstalled."
    except Exception as e:
        return f"Something went wrong opening '{matched_name}': {e}"

def get_weather(city="Chennai") -> str:
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

# ── WEB SEARCH ────────────────────────────────────────────────────────────────
def web_search(query: str, original_command: str = None) -> str:
    """
    Fetches DuckDuckGo results and routes them through ask_groq so JARVIS
    delivers the answer in its own voice — not as a generic search summary.
    """
    from ddgs import DDGS

    # Step 1 — fetch top 4 results
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=4))
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "Nothing came up for that."

    # Step 2 — format into a context block
    context = ""
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body  = r.get("body", "")
        context += f"[{i}] {title}\n{body}\n\n"

    # Step 3 — pass to ask_groq so JARVIS answers in character
    user_question = original_command or query
    return ask_groq(user_question, tool_context=f"Web search results for '{query}':\n{context}")

# ── INTENT DETECTION ──────────────────────────────────────────────────────────
def detect_intent(command: str) -> tuple[str, str]:
    prompt = f"""Classify this command into one intent. Reply ONLY with JSON, no extra text.

Command: "{command}"

Intents:
- open_app: user wants to open an application. Extract the app name.
- get_weather: user wants weather info. Extract the city (default: Chennai).
- web_search: user wants to search the web, asks about current events, news, facts, prices, scores, or anything that requires up-to-date information. Extract a clean search query.
- general: conversational, personal, or anything that doesn't need a web search. Parameter is empty string.

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

# ── GROQ BRAIN ────────────────────────────────────────────────────────────────
def ask_groq(user_input: str, tool_context: str = None) -> str:
    """
    Core brain. Always speaks as JARVIS.
    tool_context: optional raw data from a tool (weather, search, app result).
    When provided, JARVIS uses it to answer but still sounds like itself.
    """
    global conversation_history
    knowledge = load_knowledge()
    memory = load_memory()
    knowledge_str = json.dumps(knowledge, indent=2) if knowledge else "None yet."
    memory_str = f"User's name: {memory.get('user', 'Unknown')}. Sessions: {memory.get('sessions', 1)}."

    # Inject tool data into system prompt only when a tool was used
    tool_section = ""
    if tool_context:
        tool_section = f"""
TOOL DATA — use this to answer the user. Do NOT say "according to my data" or "the results show".
Just answer naturally, like a person who already knows this information:
{tool_context}
"""

    system_prompt = f"""You are JARVIS, a personal AI assistant — intelligent, sharp, and concise.
You speak in short, direct sentences. No fluff. Sound like a person, not a bot.
Never announce what you're doing. Never narrate your process.
Just answer — the way a sharp assistant would in conversation.

User profile: {memory_str}
Long-term knowledge about the user: {knowledge_str}
{tool_section}
STRICT RULES:
- You ONLY know what is explicitly stated in the user profile, knowledge, and tool data above.
- You have ZERO access to the user's computer unless they tell you something directly.
- If asked what you remember, only repeat facts from the knowledge above. If nothing relevant, say "I don't have anything stored about that yet."
- NEVER invent, assume, or fabricate context. Ever."""

    conversation_history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": system_prompt}] + conversation_history

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

    conversation_history.append({"role": "assistant", "content": full_reply})

    # Cap history at 20 messages (10 exchanges)
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    threading.Thread(target=extract_and_save, args=(user_input,), daemon=True).start()
    return full_reply

# ── VOICE OUTPUT ──────────────────────────────────────────────────────────────
def speak(text: str):
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

# ── MIC CALIBRATION ───────────────────────────────────────────────────────────
def get_rms(chunk: np.ndarray) -> float:
    """Measures loudness of an audio chunk using Root Mean Square."""
    return float(np.sqrt(np.mean(chunk ** 2)))

def calibrate():
    """
    Records 2 seconds of ambient silence at boot.
    Sets SILENCE_THRESHOLD to 2x ambient noise — anything above this is speech.
    """
    global SILENCE_THRESHOLD
    print("JARVIS: Calibrating mic... stay silent for 2 seconds.")
    chunks = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32") as stream:
        for _ in range(20):  # 20 x 100ms = 2 seconds
            chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))
            chunks.append(get_rms(chunk))
    ambient = float(np.mean(chunks))
    SILENCE_THRESHOLD = ambient * 2
    print(f"JARVIS: Calibrated. Ambient = {ambient:.4f}, threshold = {SILENCE_THRESHOLD:.4f}")

# ── VOICE INPUT ───────────────────────────────────────────────────────────────
def listen() -> str | None:
    """
    Listens after wake word detection.
    Starts recording, waits for speech, stops after SILENCE_DURATION seconds of silence.
    Transcribes via Groq Whisper.
    """
    print("JARVIS: Listening...")

    recorded_chunks = []
    silence_start = None
    speech_detected = False  # don't start silence timer until speech is detected
    recording_start = time.time()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32") as stream:
        time.sleep(0.8)  # grace period — gives you time to start speaking

        while True:
            chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))
            recorded_chunks.append(chunk)

            rms = get_rms(chunk)
            elapsed = time.time() - recording_start

            if elapsed > MAX_DURATION:
                print("JARVIS: Max duration reached, processing...")
                break

            if rms >= SILENCE_THRESHOLD:
                # Speech detected — reset silence timer
                speech_detected = True
                silence_start = None
            else:
                # Silence — only count down AFTER speech has been detected
                if speech_detected:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= SILENCE_DURATION:
                        print("JARVIS: Got it, processing...")
                        break

    if not recorded_chunks:
        return None

    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO, audio_data, SAMPLE_RATE)

    with open(TEMP_AUDIO, "rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            response_format="text",
            prompt="JARVIS. Anish. Chennai. India.",  # biases Whisper vocabulary toward Indian names
        )
    os.remove(TEMP_AUDIO)

    text = transcription.strip()
    if not text:
        return None

    # Strip end commands if present — "open chrome do it now" → "open chrome"
    text_lower = text.lower()
    for end_cmd in END_COMMANDS:
        if text_lower.endswith(end_cmd):
            text = text[:text_lower.rfind(end_cmd)].strip()
            break

    return text if text else None

# ── WAKE WORD ─────────────────────────────────────────────────────────────────
def wait_for_wake_word():
    """Listens continuously via PyAudio until 'JARVIS' is detected."""
    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )
    print("JARVIS: Waiting for wake word...")
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
    """Scans apps, calibrates mic, greets user."""
    build_app_cache()
    calibrate()

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
def run_jarvis():
    boot_jarvis()

    voice_input = input("JARVIS: Voice input mode? (y/n) → ").strip().lower() == "y"
    voice_output = input("JARVIS: Enable voice output? (y/n) → ").strip().lower() == "y"
    wake_word = input("JARVIS: Enable wake word? (y/n) → ").strip().lower() == "y"

    print("\nJARVIS: Online. Ready.\n")

    running = True

    while running:
        try:
            if wake_word:
                wait_for_wake_word()
                speak("Yes?")

            if voice_input:
                command = listen()
                if not command:
                    print("JARVIS: Didn't catch that.")
                    continue
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
                running = False
                break

            intent, parameter = detect_intent(command)

            if intent == "open_app":
                # Get the raw result, then let JARVIS respond naturally
                tool_data = open_app(parameter)
                reply = ask_groq(command, tool_context=f"App launch result: {tool_data}")
                if voice_output:
                    speak(reply)

            elif intent == "get_weather":
                # Fetch weather data, pass it to JARVIS to deliver conversationally
                tool_data = get_weather(parameter or "Chennai")
                reply = ask_groq(command, tool_context=f"Current weather data: {tool_data}")
                if voice_output:
                    speak(reply)

            elif intent == "web_search":
                # Pass both the search query and original command so JARVIS answers naturally
                reply = web_search(parameter or command, original_command=command)
                if voice_output:
                    speak(reply)

            else:
                reply = ask_groq(command)
                if voice_output:
                    speak(reply)

        except KeyboardInterrupt:
            print("\nJARVIS: Interrupted. Shutting down.")
            running = False
            break

    porcupine.delete()
    print("JARVIS: Terminated.")

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_jarvis()