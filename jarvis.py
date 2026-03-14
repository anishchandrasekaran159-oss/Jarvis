import json
import os
import io
import requests
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
import keyboard
import pygame
import pvporcupine
import struct
from datetime import datetime
from dotenv import load_dotenv

# ─── MUST BE FIRST ────────────────────────────────────────────────────────────
load_dotenv()

from groq import Groq
from elevenlabs.client import ElevenLabs

# ─── CLIENTS ──────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
eleven_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# ─── CONFIG ───────────────────────────────────────────────────────────────────
MEMORY_FILE = "jarvis_memory.json"
TEMP_AUDIO_FILE = "temp_audio.wav"
SAMPLE_RATE = 16000
CHANNELS = 1
VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam

conversation_history = []

# ─── MEMORY ───────────────────────────────────────────────────────────────────
def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {"user_name": "there", "session_count": 0, "last_seen": None, "knowledge": {}}

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def save_knowledge(fact_key, fact_value):
    memory = load_memory()
    if "knowledge" not in memory:
        memory["knowledge"] = {}
    memory["knowledge"][fact_key] = fact_value
    save_memory(memory)
    return f"Got it. I'll remember that your {fact_key} is '{fact_value}'."

def format_knowledge_for_prompt(knowledge):
    if not knowledge:
        return "None yet."
    return "\n".join(f"- {k}: {v}" for k, v in knowledge.items())

def parse_remember_command(user_input):
    text = user_input.lower().strip()
    if text.startswith("remember my "):
        text = text[len("remember my "):]
    elif text.startswith("remember "):
        text = text[len("remember "):]
    else:
        return None
    if " is " in text:
        parts = text.split(" is ", 1)
        key = parts[0].strip().replace(" ", "_")
        value = parts[1].strip()
        return (key, value)
    return None

def auto_extract_knowledge(user_message, assistant_reply):
    extraction_prompt = f"""You are a fact extractor. Extract any personal facts about the user worth saving long-term.
User said: "{user_message}"
Assistant replied: "{assistant_reply}"
Respond ONLY with a valid JSON object like {{"name": "Anish"}}. Empty object {{}} if nothing found. No explanation. No markdown."""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=200
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        facts = json.loads(raw)
        if isinstance(facts, dict) and facts:
            for k, v in facts.items():
                save_knowledge(k, str(v))
    except Exception:
        pass

def extract_and_save(user_message, assistant_reply):
    threading.Thread(
        target=auto_extract_knowledge,
        args=(user_message, assistant_reply),
        daemon=True
    ).start()

# ─── INTERNET TOOLS ───────────────────────────────────────────────────────────
def get_weather(city="Chennai"):
    try:
        response = requests.get(f"https://wttr.in/{city}?format=3", timeout=5)
        return response.text.strip()
    except Exception as e:
        return f"Weather unavailable: {e}"

# ─── AI BRAIN ─────────────────────────────────────────────────────────────────
def ask_groq(user_message, user_name, knowledge):
    conversation_history.append({"role": "user", "content": user_message})
    knowledge_text = format_knowledge_for_prompt(knowledge)
    system_prompt = (
        f"You are JARVIS, a sharp and efficient AI assistant. "
        f"You are talking to {user_name}. "
        f"Today's date is {datetime.now().strftime('%B %d, %Y')}. "
        f"Be concise, helpful, and direct. No fluff.\n\n"
        f"What you know about {user_name}:\n{knowledge_text}"
    )
    messages = [{"role": "system", "content": system_prompt}] + conversation_history
    try:
        stream = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024,
            stream=True
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
        return full_reply
    except Exception as e:
        error_msg = f"Brain error: {e}"
        print(f"JARVIS: {error_msg}")
        return error_msg

# ─── VOICE INPUT (push-to-talk, used after wake word) ─────────────────────────
def listen():
    """Record audio until SPACEBAR is released, transcribe with Whisper."""
    recorded_chunks = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
            while keyboard.is_pressed("space"):
                chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))
                recorded_chunks.append(chunk)
    except Exception as e:
        print(f"JARVIS: Mic error: {e}")
        return None

    if not recorded_chunks:
        return None

    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO_FILE, audio_data, SAMPLE_RATE)

    try:
        print("JARVIS: Processing...")
        with open(TEMP_AUDIO_FILE, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                response_format="text"
            )
        os.remove(TEMP_AUDIO_FILE)
        text = transcription.strip()
        print(f"You (voice): {text}")
        return text
    except Exception as e:
        print(f"JARVIS: Transcription error: {e}")
        return None

# ─── VOICE OUTPUT ─────────────────────────────────────────────────────────────
def speak(text):
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

# ─── WAKE WORD ────────────────────────────────────────────────────────────────
def wait_for_wake_word():
    """
    Continuously listen for 'jarvis' wake word using Porcupine.
    Blocks until wake word is detected, then returns.
    
    How it works:
    - Porcupine reads audio in fixed-size frames (512 samples at 16kHz)
    - Each frame is checked against the wake word model locally (no API call)
    - Returns index >= 0 when wake word detected, -1 otherwise
    - Uses struct.unpack to convert raw bytes → 16-bit PCM integers Porcupine expects
    """
    porcupine = pvporcupine.create(
        access_key=os.getenv("PICOVOICE_ACCESS_KEY"),
        keywords=["jarvis"]
    )

    print("JARVIS: Listening for wake word... (say 'Jarvis')")

    # Open mic stream matching Porcupine's exact requirements
    with sd.RawInputStream(
        samplerate=porcupine.sample_rate,   # 16000 Hz
        channels=1,
        dtype='int16',                       # Porcupine needs 16-bit PCM
        blocksize=porcupine.frame_length     # 512 samples per frame
    ) as stream:
        while True:
            raw_audio, _ = stream.read(porcupine.frame_length)
            # Convert raw bytes to list of 16-bit ints Porcupine can process
            pcm = struct.unpack_from("h" * porcupine.frame_length, raw_audio)
            result = porcupine.process(pcm)
            if result >= 0:  # Wake word detected!
                porcupine.delete()  # Clean up Porcupine instance
                return
# ─── VOICE INPUT (auto-record, press ENTER to stop) ───────────────────────────
def listen_after_wake():
    """
    Auto-starts recording after wake word.
    Records continuously until user presses ENTER.
    No spacebar needed — just talk naturally, hit ENTER when done.
    """
    print("JARVIS: Recording... (press ENTER when done speaking)")
    
    recorded_chunks = []
    stop_flag = threading.Event()

    def record():
        """Runs in background thread — keeps capturing mic audio until stop_flag is set."""
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
            while not stop_flag.is_set():
                chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))  # 100ms chunks
                recorded_chunks.append(chunk)

    # Start recording in background thread
    record_thread = threading.Thread(target=record, daemon=True)
    record_thread.start()

    # Main thread just waits for ENTER
    input()  # Blocks until user presses ENTER

    # Signal the recording thread to stop
    stop_flag.set()
    record_thread.join()  # Wait for thread to finish cleanly

    if not recorded_chunks:
        print("JARVIS: No audio captured.")
        return None

    # Save and transcribe
    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO_FILE, audio_data, SAMPLE_RATE)

    try:
        print("JARVIS: Processing...")
        with open(TEMP_AUDIO_FILE, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                response_format="text"
            )
        os.remove(TEMP_AUDIO_FILE)
        text = transcription.strip()
        print(f"You (voice): {text}")
        return text
    except Exception as e:
        print(f"JARVIS: Transcription error: {e}")
        return None
# ─── BOOT ─────────────────────────────────────────────────────────────────────
def boot():
    memory = load_memory()
    memory["session_count"] = memory.get("session_count", 0) + 1
    memory["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_memory(memory)
    return memory

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  JARVIS v0.1 — Day 7: Wake Word Active")
    print("=" * 50)

    memory = boot()
    user_name = memory.get("user_name", "there")
    knowledge = memory.get("knowledge", {})

    print(f"JARVIS: Welcome back, {user_name}. Session #{memory['session_count']}.")
    print()

    # Input mode
    mode = input("JARVIS: Input mode? [w]ake word / [v]oice push-to-talk / [t]ext: ").strip().lower()
    vo = input("JARVIS: Enable voice output? [y/n]: ").strip().lower()
    voice_output = (vo == "y")

    while True:
        # ── GET INPUT ──────────────────────────────────────────────────────
        if mode == "w":
            # Step 1: Wait passively for "Jarvis"
            wait_for_wake_word()
            
            # Step 2: Auto-start recording, wait for ENTER
            print("JARVIS: I'm listening...")
            if voice_output:
                speak("I'm listening")
            user_input = listen_after_wake()
            if user_input is None:
                continue

        elif mode == "v":
            print("JARVIS: Hold SPACEBAR to speak...")
            keyboard.wait("space", suppress=True)
            print("JARVIS: Recording... (release SPACEBAR to stop)")
            user_input = listen()
            if user_input is None:
                continue

        else:
            user_input = input("You: ").strip()

        if not user_input:
            continue

        # ── QUIT ───────────────────────────────────────────────────────────
        if user_input.lower().rstrip(".!?,") in ["quit", "exit", "goodbye", "bye"]:
            farewell = "Shutting down. Goodbye."
            print(f"JARVIS: {farewell}")
            if voice_output:
                speak(farewell)
            break

        # ── SPECIAL COMMANDS ───────────────────────────────────────────────
        result = parse_remember_command(user_input)
        if result:
            key, value = result
            reply = save_knowledge(key, value)
            knowledge = load_memory().get("knowledge", {})
            print(f"JARVIS: {reply}")
            if voice_output:
                speak(reply)
            continue

        if "what do you know" in user_input.lower():
            memory = load_memory()
            knowledge = memory.get("knowledge", {})
            if knowledge:
                lines = "\n".join(f"  - {k}: {v}" for k, v in knowledge.items())
                reply = f"Here's what I know about you:\n{lines}"
            else:
                reply = "I don't have any saved facts about you yet."
            print(f"JARVIS: {reply}")
            if voice_output:
                speak(reply)
            continue

        if any(word in user_input.lower() for word in ["weather", "temperature", "forecast"]):
            weather_data = get_weather("Chennai")
            user_input = f"{user_input}\n[Current weather data: {weather_data}]"

        # ── AI RESPONSE ────────────────────────────────────────────────────
        reply = ask_groq(user_input, user_name, knowledge)

        if voice_output:
            speak(reply)

        extract_and_save(user_input, reply)


if __name__ == "__main__":
    main()