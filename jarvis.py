import json
import os
import requests
import threading
import speech_recognition as sr
from datetime import datetime
from dotenv import load_dotenv
import sounddevice as sd
import soundfile as sf
import numpy as np

# MUST BE FIRST
load_dotenv()

from groq import Groq

MEMORY_FILE = "jarvis_memory.json"
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
conversation_history = []

# Initialize recognizer once — reused every listen call
recognizer = sr.Recognizer()


# ─── MEMORY FUNCTIONS ─────────────────────────────────────────────────────────

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

# ─── KNOWLEDGE FUNCTIONS (Day 4) ──────────────────────────────────────────────

def save_knowledge(fact_key, fact_value):
    memory = load_memory()
    if "knowledge" not in memory:
        memory["knowledge"] = {}
    memory["knowledge"][fact_key] = fact_value
    save_memory(memory)
    return f"Got it. I'll remember that your {fact_key} is '{fact_value}'."

def load_knowledge():
    memory = load_memory()
    return memory.get("knowledge", {})

def format_knowledge_for_prompt(knowledge):
    if not knowledge:
        return "None yet."
    return "\n".join(f"- {k}: {v}" for k, v in knowledge.items())

def parse_remember_command(user_input):
    text = user_input.strip()
    if text.lower().startswith("remember my "):
        text = text[len("remember my "):]
    elif text.lower().startswith("remember "):
        text = text[len("remember "):]
    else:
        return None, None
    if " is " in text:
        parts = text.split(" is ", 1)
        key = parts[0].strip().lower().replace(" ", "_")
        value = parts[1].strip()
        return key, value
    return None, None

def auto_extract_knowledge(user_message, assistant_reply):
    extraction_prompt = f"""
You are a fact extractor. Given a user message and assistant reply, extract any personal facts about the user worth saving long-term.

User said: "{user_message}"
Assistant replied: "{assistant_reply}"

Extract facts like: name, city, job, age, goal, hobby, preference, friend names, etc.
Only extract facts explicitly stated by the USER (not assumptions).
If no clear facts are stated, return empty JSON.

Respond ONLY with a valid JSON object. No explanation. No markdown.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=200
        )
        raw = response.choices[0].message.content.strip()
        facts = json.loads(raw)
        return facts if isinstance(facts, dict) else {}
    except Exception:
        return {}

# ─── VOICE INPUT (Day 5) ──────────────────────────────────────────────────────



SAMPLE_RATE = 16000   # Whisper expects 16kHz audio
CHANNELS = 1          # Mono — one microphone
TEMP_AUDIO_FILE = "temp_audio.wav"

import keyboard

def listen():
    """
    Push-to-talk: hold SPACEBAR to record, release to send to Whisper.
    No silence detection needed — you control start and stop.
    """
    print("JARVIS: Hold SPACEBAR to speak, release when done...")

    # Wait until spacebar is pressed
    keyboard.wait("space", suppress=True)
    print("JARVIS: Recording... (release SPACEBAR to stop)")

    recorded_chunks = []

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
        while keyboard.is_pressed("space"):
            chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))  # 100ms chunks
            recorded_chunks.append(chunk)

    if not recorded_chunks:
        print("JARVIS: Nothing recorded.")
        return None

    print("JARVIS: Processing...")

    # Combine and save
    audio_data = np.concatenate(recorded_chunks, axis=0)
    sf.write(TEMP_AUDIO_FILE, audio_data, SAMPLE_RATE)

    # Send to Groq Whisper
    try:
        with open(TEMP_AUDIO_FILE, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                response_format="text"
            )
        os.remove(TEMP_AUDIO_FILE)
        text = transcription.strip()
        if text:
            print(f"You (voice): {text}")
            return text
        else:
            print("JARVIS: Caught silence. Try again.")
            return None
    except Exception as e:
        print(f"JARVIS: Voice error: {e}")
        return None
# ─── WEATHER FUNCTION (Day 2) ─────────────────────────────────────────────────

def get_weather(city="Chennai"):
    url = f"https://wttr.in/{city}?format=j1"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return "Couldn't reach weather service."
        data = response.json()
        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        description = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        return (
            f"Weather in {city}: {description}, "
            f"{temp_c}C (feels like {feels_like}C), "
            f"Humidity: {humidity}%"
        )
    except requests.exceptions.ConnectionError:
        return "No internet connection."
    except requests.exceptions.Timeout:
        return "Weather request timed out."
    except Exception as e:
        return f"Weather error: {e}"

# ─── GROQ BRAIN ───────────────────────────────────────────────────────────────

def ask_groq(user_message, user_name="there"):
    conversation_history.append({"role": "user", "content": user_message})

    knowledge = load_knowledge()
    knowledge_text = format_knowledge_for_prompt(knowledge)

    messages = [
        {
            "role": "system",
            "content": (
                f"You are JARVIS, a sharp and efficient AI assistant. "
                f"You are talking to {user_name}. "
                f"Today's date is {datetime.now().strftime('%B %d, %Y')}. "
                f"Be concise, helpful, and direct. No fluff.\n\n"
                f"Here is what you know about {user_name} from previous sessions:\n"
                f"{knowledge_text}"
            )
        }
    ] + conversation_history

    try:
        # stream=True — Groq sends tokens as they're generated
        # instead of waiting for the full reply to be ready
        stream = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024,
            stream=True
        )

        # Print "JARVIS: " once, then stream words as they arrive
        print("JARVIS: ", end="", flush=True)
        full_reply = ""

        for chunk in stream:
            # Each chunk has a delta — the next piece of text
            delta = chunk.choices[0].delta.content
            if delta:
                print(delta, end="", flush=True)
                full_reply += delta

        print("\n")  # newline after reply finishes

        conversation_history.append({"role": "assistant", "content": full_reply})

        # Background fact extraction — doesn't block the response
        def extract_and_save():
            facts = auto_extract_knowledge(user_message, full_reply)
            for key, value in facts.items():
                save_knowledge(key, value)

        threading.Thread(target=extract_and_save, daemon=True).start()

        return full_reply

    except Exception as e:
        print()
        return f"Brain error: {e}"

# ─── BOOT SEQUENCE ────────────────────────────────────────────────────────────

def boot_jarvis():
    memory = load_memory()
    if "user" not in memory:
        name = input("JARVIS: I don't know you yet. What's your name? -> ")
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

    knowledge = load_knowledge()
    if knowledge:
        print(f"JARVIS: I remember {len(knowledge)} things about you.")

# ─── COMMAND LOOP ─────────────────────────────────────────────────────────────

def run_jarvis():
    boot_jarvis()
    user_name = load_memory().get("user", "there")

    mode = input("JARVIS: Input mode — type 'v' for voice, anything else for text: ").strip().lower()
    use_voice = mode == "v"

    # Calibrate mic once at boot — not on every listen call
   

    print("JARVIS: Online. ('quit' to exit, 'remember my X is Y' to save facts, 'what do you know' to review)\n")

    while True:
        if use_voice:
            user_input = listen()
            if user_input is None:
                continue
        else:
            user_input = input("You: ").strip()

        if not user_input:
            continue

        # Strip trailing punctuation before any comparison — speech recognition adds it
        cleaned = user_input.lower().strip().rstrip(".!?,")

        if cleaned == "quit":
            print("JARVIS: Shutting down.")
            break

        elif cleaned.startswith("remember"):
            key, value = parse_remember_command(user_input)
            if key and value:
                result = save_knowledge(key, value)
                print(f"JARVIS: {result}\n")
            else:
                print("JARVIS: Try: 'remember my [thing] is [value]'\n")

        elif "what do you know" in cleaned:
            knowledge = load_knowledge()
            if not knowledge:
                print("JARVIS: Nothing saved yet.\n")
            else:
                print("JARVIS: Here's what I know about you:")
                for k, v in knowledge.items():
                    print(f"  - {k}: {v}")
                print()

        elif "weather" in cleaned:
            words = cleaned.split()
            city = words[words.index("in") + 1].capitalize() if "in" in words else "Chennai"
            weather_data = get_weather(city)
            enriched = f"{user_input}\n\n[Live weather data: {weather_data}]"
            response = ask_groq(enriched, user_name)


        else:
            response = ask_groq(user_input, user_name)


# ─── RUN ──────────────────────────────────────────────────────────────────────

run_jarvis()