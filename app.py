# backend/app.py
from flask import Flask, jsonify
import psycopg2

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

#Define a route to fetch users from the database
@app.route('/dashboard', methods=['GET'])
def get_users(phone_number):
    cursor.execute("SELECT id FROM users WHERE phonenumber = '%s';" % (phone_number))
    user = cursor.fetchone() # user corresponding to phone number
    cursor.execute("SELECT channelid, doctorid, summary FROM channels WHERE patientid = '%s';" % (user[0]))
    chatpage_info = cursor.fetchall() # channel[0] = channelid and channel[1] = doctorid and channel[2] = summary
    for idx in range(len(chatpage_info)):
        cursor.execute("SELECT firstname, lastname FROM users WHERE id = '%s';" % (chatpage_info[idx][1]))
        doctorTuple = cursor.fetchone()
        doctorName = doctorTuple[0] + " " + doctorTuple[1]
        chatpage_info[idx] = (chatpage_info[idx][2], doctorName)
    
    if chatpage_info:
        blurb_list = {'chats': [{'summary': chatpage_info[idx][0], 'doctor': chatpage_info[idx][1]} for idx in range(len(chatpage_info))]}
        return jsonify({'chatpage_info': blurb_list})
    else:
        return jsonify({'chatpage_info': None})
    
if __name__ == '__main__':
    app.run()