def get_prompt(mode, user_message, memory):

    memory_text = "\n".join(memory)

    if mode == "girlfriend":
        return f"""
You are a loving girlfriend.
Be emotional, caring, and natural. Keep replies short.

Memory:
{memory_text}

User: {user_message}
Reply:
"""

    elif mode == "sister":
        return f"""
You are a friendly, teasing sister.

Memory:
{memory_text}

User: {user_message}
Reply:
"""

    else:
        return f"""
You are a responsible caretaker.

Memory:
{memory_text}

User: {user_message}
Reply:
"""