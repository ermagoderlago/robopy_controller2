import asyncio
import os
from google import genai
from google.genai import types

async def main():
    try:
        client = genai.Client()
        model = "gemini-2.5-flash-native-audio-latest"
        api_key = os.environ.get("GEMINI_API_KEY")
        
        print("Test 1: ['TEXT', 'AUDIO'] modalities")
        async with client.aio.live.connect(
            model=model,
            config=types.LiveConnectConfig(
                response_modalities=["TEXT", "AUDIO"],
            )
        ) as session:
            await session.send(input="Ciao, rispondi con un brevissimo saluto.", end_of_turn=True)
            text_received = False
            audio_received = False
            async for msg in session.receive():
                if getattr(msg, 'server_content', None):
                    model_turn = msg.server_content.model_turn
                    if model_turn:
                        for part in model_turn.parts:
                            if part.text:
                                text_received = True
                            if part.inline_data:
                                audio_received = True
                if msg.server_content and msg.server_content.turn_complete:
                    break
            print(f"Success! Text received: {text_received}, Audio received: {audio_received}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
