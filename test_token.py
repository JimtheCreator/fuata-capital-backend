# test_token.py
import firebase_admin
from firebase_admin import credentials, auth

firebase_admin.initialize_app(credentials.Certificate(
    "firebase_service_account.json"
))

# Replace with the actual UID of your Google Sign-In user
# Find it in Firebase Console → Authentication → Users
uid = "Jj5CmIELeBO6qzQFSt5nRuuJFft1"

custom_token = auth.create_custom_token(uid)
print(custom_token.decode())