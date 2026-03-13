import json
import os
import requests
from datetime import datetime

MEMORY_FILE = "jarvis_memory.json"

# ─── MEMORY FUNCTIONS (from Day 1) ────────────────────────────────────────────

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

# ─── WEATHER FUNCTION (Day 2 - NEW) ───────────────────────────────────────────

def get_weather(city="Chennai"):
    """
    Hits the wttr.in API and returns current weather for a city.
    wttr.in is a free weather service - no API key needed.
    We add ?format=j1 to get JSON instead of the human-readable page.
    """
    url = f"https://wttr.in/{city}?format=j1"
    
    try:
        # requests.get() sends a GET request to the URL
        # timeout=5 means "give up after 5 seconds" - always set this!
        response = requests.get(url, timeout=5)
        
        # response.status_code tells you if it worked
        # 200 = success, 404 = not found, 500 = server error
        if response.status_code != 200:
            return f"Couldn't reach weather service. Status: {response.status_code}"
        
        # .json() parses the response body from JSON string → Python dict
        data = response.json()
        
        # Navigate the nested dict to get what we want
        # This structure comes from wttr.in's API - you learn it by reading their docs
        current = data["current_condition"][0]
        
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        description = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        
        return (
            f"Weather in {city}:\n"
            f"  Condition : {description}\n"
            f"  Temp      : {temp_c}°C (feels like {feels_like}°C)\n"
            f"  Humidity  : {humidity}%"
        )
    
    except requests.exceptions.ConnectionError:
        return "No internet connection. Can't fetch weather."
    except requests.exceptions.Timeout:
        return "Weather request timed out. Try again."
    except Exception as e:
        return f"Something went wrong: {e}"

# ─── BOOT SEQUENCE (from Day 1) ───────────────────────────────────────────────

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

# ─── COMMAND LOOP (Day 2 - NEW) ───────────────────────────────────────────────

def run_jarvis():
    boot_jarvis()
    print("JARVIS: Type a command. ('weather', 'quit')\n")
    
    while True:
        # The loop: listen → understand → act → repeat
        # This is the core pattern of every AI assistant ever built
        command = input("You: ").strip().lower()
        
        if command == "quit":
            print("JARVIS: Shutting down. See you next time.")
            break
        
        elif command == "weather":
            print("JARVIS: Fetching weather...")
            result = get_weather("Chennai")
            print(f"JARVIS: {result}\n")
        
        elif command.startswith("weather "):
            # Lets user say "weather Mumbai" to get another city
            city = command.split(" ", 1)[1]  # splits "weather Mumbai" → ["weather", "Mumbai"]
            print(f"JARVIS: Fetching weather for {city}...")
            result = get_weather(city)
            print(f"JARVIS: {result}\n")
        
        else:
            print(f"JARVIS: I don't know how to '{command}' yet. Coming soon.\n")
run_jarvis()