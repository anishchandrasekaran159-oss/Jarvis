import json
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

# MUST BE FIRST — loads .env before any API client initializes
load_dotenv()

from groq import Groq

MEMORY_FILE = "jarvis_memory.json"
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
conversation_history = []

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
    """Save a single fact into the 'knowledge' section of memory."""
    memory = load_memory()
    # If 'knowledge' dict doesn't exist yet, create it
    if "knowledge" not in memory:
        memory["knowledge"] = {}
    memory["knowledge"][fact_key] = fact_value
    save_memory(memory)
    return f"Got it. I'll remember that your {fact_key} is '{fact_value}'."

def load_knowledge():
    """Return the knowledge dict, or empty dict if nothing saved yet."""
    memory = load_memory()
    return memory.get("knowledge", {})

def format_knowledge_for_prompt(knowledge):
    """
    Convert the knowledge dict into a readable string for the system prompt.
    Example: {"city": "Chennai"} → "- city: Chennai"
    """
    if not knowledge:
        return "None yet."
    return "\n".join(f"- {k}: {v}" for k, v in knowledge.items())

def parse_remember_command(user_input):
    """
    Parse 'remember X is Y' or 'remember my X is Y' style commands.
    Returns (key, value) tuple or (None, None) if pattern not matched.
    
    Examples:
      'remember my city is Chennai'     → ('city', 'Chennai')
      'remember my goal is build JARVIS' → ('goal', 'build JARVIS')
      'remember hates is mornings'      → ('hates', 'mornings')
    """
    text = user_input.strip()
    
    # Remove the 'remember' prefix (case-insensitive)
    if text.lower().startswith("remember my "):
        text = text[len("remember my "):]
    elif text.lower().startswith("remember "):
        text = text[len("remember "):]
    else:
        return None, None

    # Now text should be like "city is Chennai" or "goal is build JARVIS"
    if " is " in text:
        parts = text.split(" is ", 1)  # split on first 'is' only
        key = parts[0].strip().lower().replace(" ", "_")
        value = parts[1].strip()
        return key, value
    
    return None, None

def auto_extract_knowledge(user_message, assistant_reply):
    """
    Ask Groq to check if the conversation revealed any facts worth remembering.
    Returns a dict of {key: value} facts, or empty dict if nothing found.
    
    This is the 'smart' path — JARVIS notices facts even if you don't use 'remember'.
    """
    extraction_prompt = f"""
You are a fact extractor. Given a user message and assistant reply, extract any personal facts about the user worth saving long-term.

User said: "{user_message}"
Assistant replied: "{assistant_reply}"

Extract facts like: name, city, job, age, goal, hobby, preference, friend names, etc.
Only extract facts explicitly stated by the USER (not assumptions).
If no clear facts are stated, return empty JSON.

Respond ONLY with a valid JSON object. No explanation. No markdown. Examples:
{{"city": "Chennai", "hobby": "coding"}}
{{}}
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
        # If extraction fails or JSON is invalid, silently skip — don't crash JARVIS
        return {}

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

# ─── GROQ BRAIN (Day 3 + upgraded) ───────────────────────────────────────────

def ask_groq(user_message, user_name="there"):
    conversation_history.append({"role": "user", "content": user_message})

    # Load what JARVIS knows about the user and inject into system prompt
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
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024
        )
        reply = response.choices[0].message.content
        conversation_history.append({"role": "assistant", "content": reply})

        # Auto-extract any facts from this exchange and save them silently
        facts = auto_extract_knowledge(user_message, reply)
        for key, value in facts.items():
            save_knowledge(key, value)

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

    # Show JARVIS what it already knows
    knowledge = load_knowledge()
    if knowledge:
        print(f"JARVIS: I remember {len(knowledge)} things about you.")

# ─── COMMAND LOOP ─────────────────────────────────────────────────────────────

def run_jarvis():
    boot_jarvis()
    user_name = load_memory().get("user", "there")
    print("JARVIS: Online. ('quit' to exit, 'remember X is Y' to save facts, 'what do you know' to review)\n")

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("JARVIS: Shutting down.")
            break

        # Explicit remember command
        elif user_input.lower().startswith("remember"):
            key, value = parse_remember_command(user_input)
            if key and value:
                result = save_knowledge(key, value)
                print(f"JARVIS: {result}\n")
            else:
                print("JARVIS: Try: 'remember my [thing] is [value]'\n")

        # Review what JARVIS knows
        elif "what do you know" in user_input.lower():
            knowledge = load_knowledge()
            if not knowledge:
                print("JARVIS: Nothing saved yet. Tell me things using 'remember my X is Y'.\n")
            else:
                print("JARVIS: Here's what I know about you:")
                for k, v in knowledge.items():
                    print(f"  - {k}: {v}")
                print()

        # Weather command
        elif "weather" in user_input.lower():
            words = user_input.lower().split()
            city = words[words.index("in") + 1].capitalize() if "in" in words else "Chennai"
            weather_data = get_weather(city)
            enriched = f"{user_input}\n\n[Live weather data: {weather_data}]"
            response = ask_groq(enriched, user_name)
            print(f"JARVIS: {response}\n")

        # Everything else — AI brain
        else:
            response = ask_groq(user_input, user_name)
            print(f"JARVIS: {response}\n")

# ─── RUN ──────────────────────────────────────────────────────────────────────

run_jarvis()