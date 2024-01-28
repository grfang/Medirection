# backend/app.py
from flask import Flask, jsonify
import json
import psycopg2
import random
import string
from datetime import datetime
import os
from flask_socketio import SocketIO

from dotenv import load_dotenv
from deepgram import DeepgramClient, PrerecordedOptions
from google.cloud import translate, texttospeech
from openai import OpenAI

from firebase_admin import credentials, initialize_app, storage
cred = credentials.Certificate("./service-account.json")
initialize_app(cred, {'storageBucket': 'vitalvoice-8acf9.appspot.com'})

load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app)

PROJECT_ID = "medirection"
PARENT = f"projects/{PROJECT_ID}"
TRANSLATION_CLIENT = translate.TranslationServiceClient()
TTS_CLIENT = texttospeech.TextToSpeechClient()
GPT_CLIENT = OpenAI()
DEEPGRAM_CLIENT = DeepgramClient()

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

@socketio.on('connect')
def test_connect(auth):
    emit('my response', {'data': 'Connected'})

@socketio.on('disconnect')
def test_disconnect():
    print('Client disconnected')

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
    cursor.execute("SELECT ogaudiourl, transcription, translation, senderid, timestamp, transaudiourl FROM messages WHERE channelid = '%s' ORDER BY timestamp;" % (channelid))
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
        options = PrerecordedOptions(
            model="nova",
            smart_format=True,
            summarize="v2",
            language=lang
        )
        url_response = DEEPGRAM_CLIENT.listen.prerecorded.v("1").transcribe_url(
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

def summarize(convo, doctor_lang_name, patient_lang_name):
    chat_completion = GPT_CLIENT.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a helper at a hospital whose job is to provide concise summaries of conversations during appointments and rephrase them in simple terms that someone in middle school could understand. Please read the following chat logs between a doctor and a patient. The messages from the doctor will be in " + doctor_lang_name + " and the messages patient will be in " + patient_lang_name + ", switching back and forth, separated by new lines. Provide a short 3 line summary of the conversation in english."},
            {
                "role": "user",
                "content": "Here is the chat log: Avez-vous eu des réactions au nouveau médicament?\n Nada demasiado severo, solo un par de erupciones.\n Cela a-t-il aidé à améliorer votre qualité de sommeil?\n Sí, definitivamente puedo ver una diferencia.\n"
                # "content": "Here is the chat log: Have you had any reactions to the new medication?\n Nothing too severe, just a couple of rashes.\n Has it helped improve your sleep quality?\n Yes, I can definitely see a difference.\n"
            },
            {
                "role": "assistant",
                "content": "Rashes as effect of new sleep medication."
            },
            {
                "role": "user",
                "content": "Here is the chat log: " + convo
            }
        ],
        model="gpt-3.5-turbo",
    )
    
    return chat_completion.choices[0].message.content

def generate_todos(convo, doctor_lang_name, patient_lang_name):
    chat_completion = GPT_CLIENT.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a helper at a hospital whose job is to provide concise  lists of action items after conversations during appointments and rephrase them in simple terms that someone in middle school could understand. Please read the following chat logs between a doctor and a patient. The messages from the doctor will be in " + doctor_lang_name + " and the messages patient will be in " + patient_lang_name + ", switching back and forth, separated by new lines. Identify up to 5 short phrases of action items in english. Format your response as comma separated list of action items."},
            {
                "role": "user",
                "content": "Here is the chat log: Avez-vous eu des réactions au nouveau médicament?\n Nada demasiado severo, solo un par de erupciones.\n Cela a-t-il aidé à améliorer votre qualité de sommeil?\n Sí, definitivamente puedo ver una diferencia.\n"
                # "content": "Here is the chat log: Have you had any reactions to the new medication?\n Nothing too severe, just a couple of rashes.\n Has it helped improve your sleep quality?\n Yes, I can definitely see a difference.\n"
            },
            {
                "role": "assistant",
                "content": "Monitor rash, Monitor sleep condition, Take medication"
            },
            {
                "role": "user",
                "content": "Here is the chat log: Tengo un dolor muy agudo en la oreja. Hay una descarga de mal olor proveniente de ella.\n Cest un cas classique dotite. Je vais prescrire des antibiotiques. Prenez-les deux fois par jour pendant sept jours et votre infection devrait s'améliorer.\n"
                # "content": "Here is the chat log: I have a very sharp pain in my ear. There is bad smelling discharge coming from it.\n This is classic case of an ear infection. I will prescribe some antibiotics. Take them twice a day for seven days and your infection should be better.\n"
            },
            {
                "role": "assistant",
                "content": "Take antibiotics twice a day for seven days"
            },
            {
                "role": "user",
                "content": "Here is the chat log: " + convo
            }
        ],
        model="gpt-3.5-turbo",
    )
    return chat_completion.choices[0].message.content.split(',')

@app.route('/close', methods=['POST'])
def close(channel_id):
    query = "UPDATE channels SET status = 'closed' WHERE channelid = %s;"
    cursor.execute(query, (channel_id))

    query = "SELECT transcription FROM messages WHERE channelid = %s ORDER BY timestamp ASC;"
    cursor.execute(query, (channel_id))
    message_list = cursor.fetchall()
    
    convo = ""
    for idx in range(len(message_list)):
        convo += message_list[idx][0] + "\n"
    
    query = "SELECT doctorid, patientid FROM channels WHERE channelid = %s;"
    cursor.execute(query, (channel_id))
    chatters = cursor.fetchone()
    
    with open('codes_to_lang.json', 'r') as f:
        codes_to_lang = json.loads(f.read())

    query = "SELECT language FROM users WHERE id = %s;"

    cursor.execute(query, (chatters[0]))
    doctor_language_code = cursor.fetchone()
    doctor_lang_name = codes_to_lang[doctor_language_code[0]]

    cursor.execute(query, (chatters[1]))
    patient_language_code = cursor.fetchone()
    patient_lang_name= codes_to_lang[patient_language_code[0]]

    summary = summarize(convo, doctor_lang_name, patient_lang_name) # string
    todoList = generate_todos(convo, doctor_lang_name, patient_lang_name) # list of strings
    
    query = "UPDATE channels SET summary = %s WHERE channelid = %s;"
    cursor.execute(query, (summary, channel_id))
    
    query = "INSERT INTO todos(channelid, userid, doctorid, actions) VALUES(%s, %s, %s, %s)"
    cursor.execute(query, (channel_id, [1], chatters[0], todoList))
    conn.commit()
    
    return jsonify({'exit_code': 0})

@app.route('/create', methods=['GET'])
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
    
    return jsonify({'exit_code': 0})
    
@app.route('/actionplan', methods=['GET'])
def get_action_plans(user_id):
    query = "SELECT doctorid, actions FROM todos WHERE userid = %s;"
    cursor.execute(query, (user_id))
    todo_info = cursor.fetchall()
    for idx in range(len(todo_info)):
        query = "SELECT firstname, lastname FROM users WHERE id = %s;"
        cursor.execute(query, (todo_info[idx][0]))
        doctorTuple = cursor.fetchone()
        doctorName = doctorTuple[0] + " " + doctorTuple[1]
        todo_info[idx] = (doctorName, todo_info[idx][1])
    
    if todo_info:
        return jsonify({'todos': [{'doctor_name': todo_info[idx][0], 'todo_list': todo_info[idx][1]}] for idx in range(len(todo_info))})
    else:
        return jsonify({'todos': None})

if __name__ == '__main__':
    socketio.run(app)