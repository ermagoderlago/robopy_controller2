import asyncio
import os
from google import genai
from google.genai import types

async def main():
    try:
        client = genai.Client()
        model = "gemini-2.5-flash-native-audio-latest"
        api_key = os.environ.get("GEMINI_API_KEY")
        
        print("Test 2: Image input to Native Audio")
        async with client.aio.live.connect(
            model=model,
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
            )
        ) as session:
            # Create a dummy image
            from PIL import Image
            import io
            img = Image.new('RGB', (100, 100), color = 'red')
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG')
            img_bytes = img_byte_arr.getvalue()

            await session.send(
                input=types.LiveClientContent(
                    turns=[types.Content(
                        role="user", 
                        parts=[
                            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                            types.Part.from_text(text="What is the color of this image?")
                        ]
                    )],
                    turn_complete=True
                )
            )
            print("Sent image and text successfully.")
            audio_received = False
            async for msg in session.receive():
                if getattr(msg, 'server_content', None):
                    model_turn = msg.server_content.model_turn
                    if model_turn:
                        for part in model_turn.parts:
                            if part.inline_data:
                                audio_received = True
                if msg.server_content and msg.server_content.turn_complete:
                    break
            print(f"Success! Audio received: {audio_received}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
