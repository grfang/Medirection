# backend/app.py
from flask import Flask, jsonify
import psycopg2
import random
import string

app = Flask(__name__)

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


# cursor.close()
# conn.close()

# phone_number = '+812347983246'
# firstname = 'miffy'
# lastname = 'bunny'
# role = 'patient'


@app.route('/signup', methods=['POST'])
def signup(phone_number, firstname, lastname, role):
    user_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    cursor.execute("INSERT INTO users(id, phonenumber, firstName, lastName, role) VALUES('%s', '%s', '%s', '%s', '%s') RETURNING id;" % (user_id, phone_number, firstname, lastname, role))
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
    
# if __name__ == '__main__':
#     app.run()