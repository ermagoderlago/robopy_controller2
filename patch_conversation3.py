import os

file_path = "robopy_controller/robot_ai/orchestration/conversation.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

old_except = """            except asyncio.TimeoutError:
                self.metrics.record_llm_error("timeout")
                self._logger.error("LLM timeout both Live and Standard.")
                await self.tts.speak("Non riesco a connettermi al cervello.")
                return False
            except Exception as e:
                self.metrics.record_llm_error("unexpected")
                self._logger.error(f"LLM call failed: {e}", exc_info=True)
                return False"""

new_except = """            except asyncio.TimeoutError:
                self.metrics.record_llm_error("timeout")
                self._logger.error("LLM timeout both Live and Standard.")
                response = None
                response_text = "Non riesco a connettermi al cervello. Timeout rete."
                response_actions = []
            except Exception as e:
                self.metrics.record_llm_error("unexpected")
                self._logger.error(f"LLM call failed: {e}", exc_info=True)
                response = None
                response_text = f"Errore interno del cervello: {e}"
                response_actions = []"""

if old_except in code:
    code = code.replace(old_except, new_except)

old_speak = """                already_spoken = bool(skill_speak_texts)
                if not already_spoken:
                    try:
                        await self.tts.speak(response_text)
                    except Exception as e:
                        self._logger.error(f"TTS execution error, falling back to text only: {e}")"""

new_speak = """                already_spoken = bool(skill_speak_texts)
                if not already_spoken:
                    try:
                        await self.tts.speak(response_text)
                    except Exception as e:
                        self._logger.error(f"TTS offline o non disponibile: {e}")"""

if old_speak in code:
    code = code.replace(old_speak, new_speak)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)
print("Sostituzioni stringhe completate v3.")
