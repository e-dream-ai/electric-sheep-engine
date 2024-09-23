import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)


user = edream_client.get_logged_user()
print(user)
