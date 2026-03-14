# jarvis.py — Day 6: Voice Output (ElevenLabs + pygame)
# Stack: Groq LLaMA (brain) + Groq Whisper (voice in) + ElevenLabs (voice out)
# Memory: jarvis_memory.json (session + long-term knowledge)

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
VOICE_ID = "pNInz6obpgDQGcFmaJgB"
conversation_history = []

# ─── MEMORY ───────────────────────────────────────────────────────────────────
def load_memory():
    """Load memory from JSON file. Returns default structure if file doesn't exist."""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {
        "user_name": "there",
        "session_count": 0,
        "last_seen": None,
        "knowledge": {}
    }

def save_memory(memory):
    """Save memory dict to JSON file."""
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def save_knowledge(fact_key, fact_value):
    """Save a specific fact to long-term memory."""
    memory = load_memory()
    if "knowledge" not in memory:
        memory["knowledge"] = {}
    memory["knowledge"][fact_key] = fact_value
    save_memory(memory)
    return f"Got it. I'll remember that your {fact_key} is '{fact_value}'."

def format_knowledge_for_prompt(knowledge):
    """Convert knowledge dict to bullet points for system prompt injection."""
    if not knowledge:
        return "None yet."
    return "\n".join(f"- {k}: {v}" for k, v in knowledge.items())

def parse_remember_command(user_input):
    """
    Parse 'remember my X is Y' or 'remember X is Y' commands.
    Returns (key, value) tuple or None if not a remember command.
    """
    text = user_input.lower().strip()
    if text.startswith("remember my "):
        text = text[len("remember my "):]
    elif text.startswith("remember "):
        text = text[len("remember "):]
    else:
        return None

    if " is " in text:
        parts = text.split(" is ", 1)  # Split on first ' is ' only
        key = parts[0].strip().replace(" ", "_")
        value = parts[1].strip()
        return (key, value)
    return None

def auto_extract_knowledge(user_message, assistant_reply):
    """
    Silently ask Groq to extract any personal facts from the conversation.
    Runs in background thread — never blocks the user.
    """
    extraction_prompt = f"""You are a fact extractor. Given a user message and assistant reply, extract any personal facts about the user worth saving long-term.
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
        # Strip markdown fences if model adds them
        raw = raw.replace("```json", "").replace("```", "").strip()
        facts = json.loads(raw)
        if isinstance(facts, dict) and facts:
            for k, v in facts.items():
                save_knowledge(k, str(v))
    except Exception:
        pass  # Silent failure — never crash JARVIS for this

def extract_and_save(user_message, assistant_reply):
    """Wrapper to run auto_extract_knowledge in a background thread."""
    thread = threading.Thread(
        target=auto_extract_knowledge,
        args=(user_message, assistant_reply),
        daemon=True  # Dies cleanly when main program exits
    )
    thread.start()

# ─── INTERNET TOOLS ───────────────────────────────────────────────────────────
def get_weather(city="Chennai"):
    """Fetch current weather for a city using wttr.in (no API key needed)."""
    try:
        response = requests.get(f"https://wttr.in/{city}?format=3", timeout=5)
        return response.text.strip()
    except Exception as e:
        return f"Weather unavailable: {e}"

# ─── AI BRAIN ─────────────────────────────────────────────────────────────────
def ask_groq(user_message, user_name, knowledge):
    """
    Send message to Groq LLaMA. Streams response token by token.
    Returns the full reply string after streaming completes.
    """
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
        print("\n")  # Newline after response finishes

        conversation_history.append({"role": "assistant", "content": full_reply})
        return full_reply

    except Exception as e:
        error_msg = f"Brain error: {e}"
        print(f"JARVIS: {error_msg}")
        return error_msg

# ─── VOICE INPUT ──────────────────────────────────────────────────────────────
def listen():
    """
    Push-to-talk voice input.
    Hold SPACEBAR to record, release to stop.
    Returns transcribed text string, or None on failure.
    """
    print("JARVIS: Hold SPACEBAR to speak, release when done...")
    keyboard.wait("space", suppress=True)
    print("JARVIS: Recording... (release SPACEBAR to stop)")

    recorded_chunks = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
            while keyboard.is_pressed("space"):
                chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))  # Read 100ms chunks
                recorded_chunks.append(chunk)
    except Exception as e:
        print(f"JARVIS: Mic error: {e}")
        return None

    if not recorded_chunks:
        print("JARVIS: No audio captured.")
        return None

    # Save recorded audio to temp WAV file
    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO_FILE, audio_data, SAMPLE_RATE)

    # Send to Groq Whisper for transcription
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
    """
    Convert text to speech using ElevenLabs and play it via pygame.
    Falls back silently if anything fails — JARVIS already printed the text.
    """
    try:
        # Request audio from ElevenLabs
        audio_generator = eleven_client.text_to_speech.convert(
            voice_id=VOICE_ID,
            output_format="mp3_44100_128",
            text=text,
            model_id="eleven_turbo_v2_5",  # Fastest model, lowest latency
        )

        # Join generator chunks into a single bytes object
        audio_bytes = b"".join(audio_generator)

        # Play audio using pygame (in-memory, no temp file needed)
        pygame.mixer.init(frequency=44100)
        pygame.mixer.music.load(io.BytesIO(audio_bytes))
        pygame.mixer.music.play()

        # Wait for playback to finish before returning
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

    except Exception as e:
        print(f"[Voice output error: {e}]")  # Silent fail — text was already printed

# ─── BOOT SEQUENCE ────────────────────────────────────────────────────────────
def boot():
    """Load memory, update session count, greet user."""
    memory = load_memory()
    memory["session_count"] = memory.get("session_count", 0) + 1
    memory["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_memory(memory)
    return memory

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  JARVIS v0.1 — Day 6: Voice I/O")
    print("=" * 50)

    memory = boot()
    user_name = memory.get("user_name", "there")
    knowledge = memory.get("knowledge", {})

    print(f"JARVIS: Welcome back, {user_name}. Session #{memory['session_count']}.")
    print(f"JARVIS: Last seen: {memory.get('last_seen', 'first time')}")
    print()

    # Input mode selection
    mode = input("JARVIS: Input mode? [v]oice / [t]ext: ").strip().lower()
    voice_mode = (mode == "v")
    voice_output = False

    if voice_mode:
        # Ask if they also want voice output
        vo = input("JARVIS: Enable voice output? [y/n]: ").strip().lower()
        voice_output = (vo == "y")
        if voice_output:
            print("JARVIS: Voice output enabled. ElevenLabs active.")
        print("JARVIS: Voice mode active. Hold SPACEBAR to speak.")
    else:
        print("JARVIS: Text mode active. Type your message.")

    print("JARVIS: Type/say 'quit' to exit.\n")

    while True:
        # ── GET INPUT ──────────────────────────────────────────────────────
        if voice_mode:
            user_input = listen()
            if user_input is None:
                continue  # Failed transcription — just loop again
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
        # Remember command
        result = parse_remember_command(user_input)
        if result:
            key, value = result
            reply = save_knowledge(key, value)
            knowledge = load_memory().get("knowledge", {})  # Refresh knowledge
            print(f"JARVIS: {reply}")
            if voice_output:
                speak(reply)
            continue

        # What do you know
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

        # Weather command
        if any(word in user_input.lower() for word in ["weather", "temperature", "forecast"]):
            weather_data = get_weather("Chennai")
            user_input = f"{user_input}\n[Current weather data: {weather_data}]"

        # ── AI RESPONSE ────────────────────────────────────────────────────
        reply = ask_groq(user_input, user_name, knowledge)

        # Voice output (if enabled)
        if voice_output:
            speak(reply)

        # Background fact extraction — doesn't block anything
        extract_and_save(user_input, reply)


if __name__ == "__main__":
    main()