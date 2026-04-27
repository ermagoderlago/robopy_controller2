import os

file_path = "robopy_controller/robot_ai/orchestration/conversation.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

old_logic = """            # Post LLM validation -> prevent emergency action
            response_actions = getattr(response, 'actions', [])
            # Controlliamo che l'LLM non abbia tradimentato la regex emergency stop se aveva solo lo scopo della chat
            # Gestiamo dictionary o obj pattern
            if hasattr(response, "get"):
                 # It's a dict likely
                 response_text = response.get("response_text", "")
                 response_actions = response.get("actions", [])
            else:
                 response_text = getattr(response, "response_text", "")
                 response_actions = getattr(response, "actions", [])"""

new_logic = """            # Post LLM validation -> prevent emergency action
            if response is not None:
                response_actions = getattr(response, 'actions', [])
                # Controlliamo che l'LLM non abbia tradimentato la regex emergency stop se aveva solo lo scopo della chat
                # Gestiamo dictionary o obj pattern
                if hasattr(response, "get"):
                     # It's a dict likely
                     response_text = response.get("response_text", "")
                     response_actions = response.get("actions", [])
                else:
                     response_text = getattr(response, "response_text", "")
                     response_actions = getattr(response, "actions", [])"""

if old_logic in code:
    code = code.replace(old_logic, new_logic)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)
print("Sostituzioni stringhe completate v4.")
