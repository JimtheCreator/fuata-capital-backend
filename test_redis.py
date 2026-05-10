import redis
from config import get_settings

def test_connection():
    settings = get_settings()
    url = settings.redis_url
    
    print(f"--- Attempting to connect to Upstash ---")
    print(f"URL: {url.split('@')[-1]}") # Prints only the endpoint for safety
    
    try:
        # We use ssl_cert_reqs=None to bypass local CA issues common on Windows
        client = redis.from_url(
            url, 
            decode_responses=True, 
            ssl_cert_reqs=None,
            socket_connect_timeout=5
        )
        
        # The Moment of Truth
        response = client.ping()
        
        if response:
            print("✅ SUCCESS: Redis responded to PING!")
        else:
            print("❌ FAILED: Redis connected but returned an empty response.")
            
    except redis.exceptions.AuthenticationError:
        print("❌ ERROR: Authentication failed. Check your password in .env")
    except redis.exceptions.ConnectionError as e:
        print(f"❌ ERROR: Could not connect to server. \nDetails: {e}")
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    test_connection()