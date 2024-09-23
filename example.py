from edream_sdk.client import create_edream_client

edream_client = create_edream_client(
    backend_url="http://localhost:8080/api/v1", api_key="your_api_key"
)

user = edream_client.get_logged_user()
print(user)
