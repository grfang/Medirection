# backend/app.py
from flask import Flask, jsonify
import json
import psycopg2
import random
import string
from datetime import datetime
import os

from dotenv import load_dotenv
from deepgram import DeepgramClient, PrerecordedOptions
from google.cloud import translate, texttospeech

from firebase_admin import credentials, initialize_app, storage
cred = credentials.Certificate("./service-account.json")
initialize_app(cred, {'storageBucket': 'vitalvoice-8acf9.appspot.com'})

load_dotenv()

app = Flask(__name__)

PROJECT_ID = "medirection"
PARENT = f"projects/{PROJECT_ID}"
TRANSLATION_CLIENT = translate.TranslationServiceClient()
TTS_CLIENT = texttospeech.TextToSpeechClient()

# Replace these variables with your PostgreSQL connection details
DB_USER = 'postgres'
DB_PASSWORD = 'postgres'
DB_HOST = 'localhost'
DB_PORT = '5432'
DB_NAME = 'medirection'

# Establish a connection to the PostgreSQL database
conn = psycopg2.connect(
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME
)

# Create a cursor to interact with the database
cursor = conn.cursor()

@app.route('/signup', methods=['POST'])
def signup(phone_number, firstname, lastname, role, language):
    with open('lang_codes.json', 'r') as f:
        lang_codes = json.loads(f.read())

    user_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    cursor.execute("INSERT INTO users(id, phonenumber, firstName, lastName, role, language) VALUES('%s', '%s', '%s', '%s', '%s', '%s') RETURNING id;" % (user_id, phone_number, firstname, lastname, role, lang_codes[language]))
    conn.commit()
    user_id = cursor.fetchone()
    if user_id:
        return jsonify({'user_id': user_id})
    else:
        return jsonify({'user_id': None})
    
@app.route('/login', methods=['GET'])
def login(phone_number):
    cursor.execute("SELECT id FROM users WHERE phonenumber = '%s';" % (phone_number))
    user = cursor.fetchone() # user corresponding to phone number
    
    if user:
        return jsonify({'user_id': user[0]})
    else:
        return jsonify({'user_id': None})

@app.route('/dashboard', methods=['GET'])
def get_dashboard(user_id):
    cursor.execute("SELECT channelid, doctorid, summary FROM channels WHERE user_id = '%s';" % (user_id))
    chatpage_info = cursor.fetchall() # chatpage_info[0] = channelid, chatpage_info[1] = doctorid, chatpage_info[2] = summary
    for idx in range(len(chatpage_info)):
        cursor.execute("SELECT firstname, lastname FROM users WHERE id = '%s';" % (chatpage_info[idx][1]))
        doctorTuple = cursor.fetchone()
        doctorName = doctorTuple[0] + " " + doctorTuple[1]
        chatpage_info[idx] = (doctorName, chatpage_info[idx][1], chatpage_info[idx][0], chatpage_info[idx][2])
    
    if chatpage_info:
        blurb_list = [{'doctorname': chatpage_info[idx][0], 'doctorid': chatpage_info[idx][1], 'chatpageid': chatpage_info[idx][2], 'summary': chatpage_info[idx][3]} for idx in range(len(chatpage_info))]
        return jsonify({'chatpage_info': blurb_list})
    else:
        return jsonify({'chatpage_info': None})

@app.route('/chatroom', methods=['GET'])
def get_messages(channelid):
    cursor.execute("SELECT ogaudiourl, transcription, translation, senderid, timestamp, transaudiourl FROM messages WHERE channelid = '%s' SORT BY timestamp;" % (channelid))
    chatroom_messages = cursor.fetchall()
    cursor.execute("SELECT status FROM channels WHERE channelid = '%s';" % (channelid))
    channel_status = cursor.fetchone()
    
    if chatroom_messages:
        return jsonify({'messages': [{'ogaudiourl': chatroom_messages[idx][0], 'transcription': chatroom_messages[idx][1], 'translation': chatroom_messages[idx][2], 'senderid': chatroom_messages[idx][3], 'timestamp': chatroom_messages[idx][4], 'transaudiourl': chatroom_messages[idx][5]} for idx in range(len(chatroom_messages))], 'channel_status': channel_status[0]})
    else:
        return jsonify({'messages': None})

def get_transcription(audio, sender_id):
    # get lang from sender_id
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (sender_id))
    lang = cursor.fetchone()[0]
    
    try:
        # call deepgram to get transscription
        deepgram = DeepgramClient()
        options = PrerecordedOptions(
            model="nova",
            smart_format=True,
            summarize="v2",
            language=lang
        )
        url_response = deepgram.listen.prerecorded.v("1").transcribe_url(
            {"url": audio}, options
        )
        return url_response.results.channels[0].alternatives[0].transcript
    except Exception as e:
        print(f"Exception: {e}")
    return None

def translate_text(text: str, target_language_code: str, source_language_code: str) -> translate.Translation:
    response = TRANSLATION_CLIENT.translate_text(
        parent=PARENT,
        contents=[text],
        target_language_code=target_language_code,
        source_language_code=source_language_code
    )
    return response.translations[0]

def get_translation(transcription, doctor_id, sender_id):
    # get lang from doctor_id
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (doctor_id))
    target_lang = cursor.fetchone()[0]
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (sender_id))
    src_lang = cursor.fetchone()[0]
    
    if target_lang == src_lang:
        return transcription

    translation = translate_text(transcription, target_lang, src_lang)
    return translation.translated_text

@app.route('/send', methods=['POST'])
def send_message(audio_url, channel_id, doctor_id, sender_id):
    transcription = get_transcription(audio_url, sender_id).replace("'", r"\'")
    translation = get_translation(transcription, doctor_id, sender_id).replace("'", r"\'")
    print(transcription)
    print(translation)
    message_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    timestamp = str(int(datetime.now().timestamp()))
    query = "INSERT INTO messages(messageid, channelid, timestamp, senderid, transcription, translation, ogaudiourl) VALUES(%s, %s, %s, %s, %s, %s, %s, %s)"
    cursor.execute(query, (message_id, channel_id, timestamp, sender_id, transcription, translation, audio_url, ""))
    conn.commit()
    return jsonify({'transcription': transcription, 'translation': translation, 'message_id': message_id})

@app.route('/receive', methods=['POST'])
def receive_message(translation, receiver_id, message_id):
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (receiver_id))
    lang = cursor.fetchone()[0]
    
    synthesis_input = texttospeech.SynthesisInput(text=translation)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang
        )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    
    response = TTS_CLIENT.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    
    with open(f"{str(int(datetime.now().timestamp()))}.mp3", "wb") as out:
        out.write(response.audio_content)
        
    bucket = storage.bucket()
    blob = bucket.blob(out.name)
    blob.upload_from_filename("./" + out.name)
    blob.make_public()
    os.remove(out.name)
    
    query = "UPDATE messages SET transaudiourl = %s WHERE messageid = %s;"
    cursor.execute(query, (blob.public_url, message_id))
    conn.commit()

    return jsonify({'url': blob.public_url})

@app.route('/receive/norm', methods=['GET'])
def change_speed_norm(translation, receiver_id):
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (receiver_id))
    lang = cursor.fetchone()[0]
    
    synthesis_input = texttospeech.SynthesisInput(text=translation)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang
        )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=1.0)
    
    response = TTS_CLIENT.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    
    with open(f"{str(int(datetime.now().timestamp()))}.mp3", "wb") as out:
        out.write(response.audio_content)
        
    bucket = storage.bucket()
    blob = bucket.blob(out.name)
    blob.upload_from_filename("./" + out.name)
    blob.make_public()
    os.remove(out.name)

    return jsonify({'url': blob.public_url})
    
@app.route('/receive/half', methods=['GET'])
def change_speed_half(translation, receiver_id):
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (receiver_id))
    lang = cursor.fetchone()[0]
    
    synthesis_input = texttospeech.SynthesisInput(text=translation)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang
        )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=0.5)
    
    response = TTS_CLIENT.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    
    with open(f"{str(int(datetime.now().timestamp()))}.mp3", "wb") as out:
        out.write(response.audio_content)
        
    bucket = storage.bucket()
    blob = bucket.blob(out.name)
    blob.upload_from_filename("./" + out.name)
    blob.make_public()
    os.remove(out.name)

    return jsonify({'url': blob.public_url})

@app.route('/receive/double', methods=['GET'])
def change_speed_double(translation, receiver_id):
    cursor.execute("SELECT language FROM users WHERE id = '%s';" % (receiver_id))
    lang = cursor.fetchone()[0]
    
    synthesis_input = texttospeech.SynthesisInput(text=translation)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang
        )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=2.0)
    
    response = TTS_CLIENT.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    
    with open(f"{str(int(datetime.now().timestamp()))}.mp3", "wb") as out:
        out.write(response.audio_content)
        
    bucket = storage.bucket()
    blob = bucket.blob(out.name)
    blob.upload_from_filename("./" + out.name)
    blob.make_public()
    os.remove(out.name)

    return jsonify({'url': blob.public_url})

@app.route('/close', methods=['POST'])
def close(channel_id):
    query = "UPDATE channels SET status = 'closed' WHERE channelid = %s;"
    cursor.execute(query, (channel_id))

@app.route('/create', methods=['GET', 'POST'])
def create(doctor_id, phone_number):
    query = "SELECT id, firstname, lastname FROM users WHERE phonenumber = %s;"
    cursor.execute(query, (phone_number))
    user_info = cursor.fetchone()
    
    if not user_info:
        return jsonify({'user': None})
    
    channel_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    query = "INSERT INTO channels(channelid, doctorid, patientid, summary, status) VALUES(%s, %s, %s, %s, %s);"
    cursor.execute(query, (channel_id, doctor_id, user_info[0], "", "open"))
    conn.commit()
    
    return jsonify({'user': {'user_id': user_info[0], 'name': user_info[1] + " " + user_info[2]}})

@app.route('/settings', methods=['POST'])
def change_language(user_id, language):
    with open('lang_codes.json', 'r') as f:
        lang_codes = json.loads(f.read())
        
    query = "UPDATE users SET language = %s WHERE id = %s;"
    cursor.execute(query, (lang_codes[language], user_id))
    conn.commit()

if __name__ == '__main__':
    app.run()