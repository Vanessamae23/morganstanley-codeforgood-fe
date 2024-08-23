import base64
import os
import logging
from flask import Flask, request, jsonify

import firebase_admin
from firebase_admin import credentials, auth, db
import datetime
from dotenv import load_dotenv
import requests  

import telebot
from telebot import types
# Initialize Flask server
server = Flask(__name__)

TELEGRAM_API_TOKEN="7364529459:AAGcLtCPPO-m70xDMtZ7WYuYk1Ssokj1iLw"#os.getenv("TELEGRAM_API_TOKEN")
bot = telebot.TeleBot(TELEGRAM_API_TOKEN, threaded=False)

# Load the .env file
load_dotenv()

# Fetch the service account key JSON file contents
cred = credentials.Certificate('settings.json')

# Initialize the app with a service account, granting admin privileges
firebase_admin.initialize_app(cred, {
    'databaseURL': os.getenv("DATABASE_URL")
})

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@server.route('/add-food', methods=['POST'])
def add_food_posting():
    try:
        data = request.get_json()
        required_fields = ['name', 'numOfMeals', 'preparedAt', 'consumeBy', 'recurring', 'selectedDays']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Store the food posting in the database
        prepared_at_date = datetime.datetime.strptime(data['preparedAt'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y-%m-%d')
        ref = db.reference(f'food_postings/{prepared_at_date}')
        new_post_ref = ref.push(data)
        post_id = new_post_ref.key

        logging.info(f'Food posting added successfully with ID: {post_id}')

        # Fetch all chat IDs associated with emails
        email_ref = db.reference('emails')
        emails_data = email_ref.get()

        if emails_data:
            # Send notification to each chat ID
            for encoded_email, chat_id in emails_data.items():
                try:
                    # Decode email for logging (optional)
                    decoded_email = base64.b64decode(encoded_email).decode()
                    logging.info(f'Sending notification to {decoded_email} (chat_id: {chat_id})')
                    
                    # Send notification to the chat_id
                    bot.send_message(chat_id, f"New food posting added: {data['name']}. Check it out!")
                except Exception as e:
                    logging.error(f"Failed to send message to chat_id {chat_id}: {e}")
        
        return jsonify({"message": "Food posting added successfully", "id": post_id}), 201

    except Exception as e:
        logging.error(f"Failed to add food posting: {e}")
        return jsonify({"error": str(e)}), 500

@server.route('/get-food', methods=['GET'])
def get_food_postings():
    try:
        ref = db.reference('food_postings')
        food_postings = ref.get()
        if not food_postings:
            food_postings = {}
        logging.info('Food postings retrieved successfully.')
        return jsonify(food_postings), 200
    except Exception as e:
        logging.error(f"Failed to retrieve food postings: {e}")
        return jsonify({"error": str(e)}), 500

@server.route('/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        roles = data.get('roles')  # An array of roles (e.g., ['Donor', 'Volunteer'])

        if not email or not password or not roles or not isinstance(roles, list):
            return jsonify({"error": "Missing email, password, or roles (roles must be an array)"}), 400

        # Create user with Firebase Admin SDK
        user = auth.create_user(
            email=email,
            password=password,
        )

        # Store user roles in Firebase Realtime Database
        user_ref = db.reference(f'users/{user.uid}')
        user_ref.set({
            'email': email,
            'roles': roles,
            'createdAt': datetime.datetime.now().isoformat()
        })

        logging.info('User registered successfully with roles.')
        return jsonify({"message": "User registered successfully", "userId": user.uid}), 201
    except Exception as e:
        logging.error(f"Failed to register user: {e}")
        return jsonify({"error": str(e)}), 500

FIREBASE_WEB_API_KEY = os.getenv('FIREBASE_WEB_API_KEY')

@server.route('/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({"error": "Missing email or password"}), 400

        # Firebase REST API URL for email/password sign-in
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"

        # Data for the request
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }

        # Make a POST request to the Firebase Auth REST API
        response = requests.post(url, json=payload)
        response_data = response.json()

        # Check for errors in the response
        if 'error' in response_data:
            return jsonify({"error": response_data['error']['message']}), 400

        # Return user information and ID token
        return jsonify({
            "message": "Login successful",
            "idToken": response_data['idToken'],
            "refreshToken": response_data['refreshToken'],
            "expiresIn": response_data['expiresIn'],
            "userId": response_data['localId']
        }), 200
    except Exception as e:
        logging.error(f"Failed to log in user: {e}")
        return jsonify({"error": str(e)}), 500
    
# BOT STUFF
@bot.message_handler(commands=['attach'])
def register_user(message):
    chat_id = str(message.chat.id)
    msg = bot.reply_to(message, "Please enter your email address:")
    bot.register_next_step_handler(msg, process_email_step)

def process_email_step(message):
    email = message.text.lower()
    ref = db.reference()
    encoded_email = base64.b64encode(email.encode()).decode()
    chat_id = str(message.chat.id)
    email_ref = ref.child('emails').child(encoded_email)
    email_ref.set(chat_id)
    bot.send_message(message.chat.id, f"Thank you, it will be registered")
    
@server.route("/")
def webhook():
    bot.remove_webhook()
    server_uri = os.getenv("DEV_SERVER") + TELEGRAM_API_TOKEN
    logger.info(str(server_uri))
    bot.set_webhook(url=server_uri)
    return "!", 200

@server.route('/' + TELEGRAM_API_TOKEN, methods=['POST'])
def getMessage():
    try:
        # Read the raw data from Telegram
        raw_data = request.stream.read().decode("utf-8")
        logger.info(f"Raw incoming request data: {raw_data}")
        
        # Process the update
        update = telebot.types.Update.de_json(raw_data)
        
        # Check if 'update_id' exists before processing
        if hasattr(update, 'update_id'):
            bot.process_new_updates([update])
            return "OK", 200  # Correct tuple response with body and status
        else:
            logger.warning(f"Missing 'update_id' in update: {raw_data}")
            return jsonify({"error": "Missing 'update_id'"}), 400  # Return error as a valid JSON response with status code
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return jsonify({"error": "Failed to process update", "details": str(e)}), 500  # Handle exceptions with proper response
    
    return "OK", 200  # Ensure valid response is always returned
    
# Start the server
if __name__ == "__main__":
    server.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))


