import json
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

# THIS MUST BE FIRST — loads .env before any API client initializes
load_dotenv()

from google import genai
from google.genai import types

MEMORY_FILE = "jarvis_memory.json"

# Initialize Gemini client — reads GEMINI_API_KEY from .env
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Conversation history — full back-and-forth stored here
# Sent with every message so JARVIS remembers context
conversation_history = []

# ─── MEMORY FUNCTIONS (Day 1) ─────────────────────────────────────────────────

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

# ─── WEATHER FUNCTION (Day 2) ─────────────────────────────────────────────────

def get_weather(city="Chennai"):
    url = f"https://wttr.in/{city}?format=j1"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return f"Couldn't reach weather service."
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

# ─── GEMINI BRAIN (Day 3) ─────────────────────────────────────────────────────

from groq import Groq

# Groq client — reads GROQ_API_KEY from .env
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Full conversation history — same pattern as before
conversation_history = []

def ask_groq(user_message, user_name="there"):
    # Add user message to history
    conversation_history.append({
        "role": "user",
        "content": user_message
    })

    # System message + full history sent every time
    messages = [
        {
            "role": "system",
            "content": (
                f"You are JARVIS, a sharp and efficient AI assistant. "
                f"You are talking to {user_name}. "
                f"Today's date is {datetime.now().strftime('%B %d, %Y')}. "
                f"Be concise, helpful, and direct. No fluff. No filler phrases."
            )
        }
    ] + conversation_history
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        conversation_history.append({
            "role": "assistant",
            "content": reply
        })
        return reply
    except Exception as e:
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

# ─── COMMAND LOOP ─────────────────────────────────────────────────────────────

def run_jarvis():
    boot_jarvis()
    user_name = load_memory().get("user", "there")
    print("JARVIS: Online. Ask me anything. ('quit' to exit)\n")

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("JARVIS: Shutting down.")
            break

        # Weather: fetch live data, pass to Gemini to reason about it
        elif "weather" in user_input.lower():
            words = user_input.lower().split()
            city = words[words.index("in") + 1].capitalize() if "in" in words else "Chennai"
            weather_data = get_weather(city)
            enriched = f"{user_input}\n\n[Live weather data: {weather_data}]"
            response = ask_groq(enriched, user_name)
            print(f"JARVIS: {response}\n")

        # Everything else — straight to Gemini
        else:
            response = ask_groq(user_input, user_name)
            print(f"JARVIS: {response}\n")

# ─── RUN ──────────────────────────────────────────────────────────────────────

run_jarvis()