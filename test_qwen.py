import os
import sys
import base64
import time
from io import BytesIO
from PIL import Image
from openai import OpenAI

api_key = os.getenv("DASHSCOPE_API_KEY", "")
if not api_key:
    print("Error: DASHSCOPE_API_KEY not found.")
    sys.exit(1)

client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# Create a small 100x100 red image
img = Image.new("RGB", (100, 100), color="red")
buffered = BytesIO()
img.save(buffered, format="JPEG")
img_base64 = base64.b64encode(buffered.getvalue()).decode("ascii")
image_url = f"data:image/jpeg;base64,{img_base64}"

try:
    print("Testing qwen3.6-plus with base64 image...")
    t0 = time.time()
    response = client.chat.completions.create(
        model="qwen3.6-plus",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the main color of this image in one word."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        timeout=30,
    )
    t1 = time.time()
    print(f"Success in {t1 - t0:.2f} seconds!")
    print("Response:", response.choices[0].message.content)
except Exception as e:
    print("Failed:", e)
