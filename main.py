import datetime
import random

import text  # 직접 정의한 모듈입니다. text.py에 따로 데이터들을 모아놓아두었습니다.
import firebase_admin
import requests as res
from bs4 import BeautifulSoup
from firebase_admin import credentials
from firebase_admin import firestore
from flask import Flask, request, make_response, jsonify

# Flask를 초기화합니다.
app = Flask(__name__)
log = app.logger

# Firebase Database에 ProjectID를 통해 연결합니다.
cred = credentials.ApplicationDefault()
firebase_admin.initialize_app(cred, {
    'projectId': 'sunflower-diary',
})
db = firestore.client()


@app.route('/', methods=['POST'])
def webhook():
    '''
    Facebook, Dialogflow에서 온 요청을 받아서
    일기장 앱 기능에 따라 답변을 수정해서 반환
    '''
    req = request.get_json(silent=True, force=True)

    try:
        intent = req.get('queryResult')
        user_text = intent.get('queryText')  # 유저가 입력한 질문/명령 텍스트
        sender_id = intent.get('outputContexts')[-1].get('parameters').get('facebook_sender_id')
        intent_name = intent.get('intent').get('displayName')

        # 한국 시간 기준으로 일기 저장
        korean_date = datetime.datetime.now() + datetime.timedelta(hours=9)
        date = str(korean_date.date())
        time = str(korean_date.time()).replace('.', ':')

        response = intent.get('fulfillmentText')  # 원래 Dialogflow가 반환할 대답

        # 1: 쓰기 모드, 0: 읽기 모드
        if get_mode(sender_id) == '1':
            if intent_name == 'diary_end':
                set_mode(sender_id, '0')
            else:
                save_chat(sender_id, date, time, user_text)
                response = text.write_mode_messages[random.randrange(0, len(text.write_mode_messages))]
        else:
            # 키워드로 일기를 검색할 때는  키워드를 받는 순간을 구별하여 그땐 인텐트 무시
            if get_keyword_ready(sender_id) == '1':
                response = load_chat_keyword(sender_id, user_text)
                set_keyword_ready(sender_id, '0')
            elif intent_name == 'app_description':
                response = text.app_description
            elif intent_name == 'diary_start':
                set_mode(sender_id, '1')
            elif intent_name == 'diary_search_date_answer':
                date_text = intent.get('outputContexts')[-1].get('parameters').get('date-time')
                response = load_chat_date(sender_id, date_text)
            elif intent_name == 'diary_search_keyword':
                set_keyword_ready(sender_id, '1')
            elif intent_name == 'tomorrow_weather':
                parameter = intent.get('outputContexts')[-1].get('parameters')
                response = tomorrow_weather(
                    parameter.get('date-time.original'), parameter.get('korean_geo.original'))

        return make_response(jsonify({'fulfillmentText': response}))
    except AttributeError:
        return make_response(jsonify({'fulfillmentText': '서버 오류입니다. ekrmdhs95@gamil.com 으로 제보해주세요.'}))


def tomorrow_weather(when, where):
    '''
    :param when, where: 네이버에 날씨를 검색할 때 언제/어디서를 포함하여 검색해야 합니다.
    :return: 동적으로 웹크롤링을 하여 날씨값을 네이버 검색 결과에서 받아 반환합니다.
             유저에게 반환할 대답 텍스트를 반환합니다.
    '''
    url = 'https://search.naver.com/search.naver?where=nexearch&query={}+{}+날씨&ie=utf8&sm=tab_she&qdt=0'.format(when, where)
    source_code = res.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    html = source_code.text
    soup = BeautifulSoup(html, 'html.parser')
    tomorrow_box = soup.find("div", {"class": "tomorrow_area"})
    weather_txt = tomorrow_box.find_all("p", {"class": "cast_txt"})

    return "{} {} 날씨 알려줄게!\n오전에는 '{}'이고, 오후에는 '{}'이야!".format(
        when, where, weather_txt[0].contents[0], weather_txt[1].contents[0])


def set_keyword_ready(sender_id, status):
    # :param status: '1': 키워드 듣기 상태 / '0' : 키워드 듣기 상태가 아님
    doc_ref = db.collection(sender_id).document('status')
    status = doc_ref.get().to_dict()
    status['keyword_ready'] = status
    doc_ref.set(status)


def get_keyword_ready(sender_id):
    # keyword를 듣기 직전 상태인지 반환합니다.
    doc_ref = db.collection(sender_id).document('status')
    status = doc_ref.get().to_dict()

    if status.get('keyword_ready') is None:
        status['keyword_ready'] = '0'
        doc_ref.set(status)
        return '0'

    return status.get('keyword_ready')


def set_mode(sender_id, mode):
    # 현재 쓰기/읽기 모드를 (각각 1/0) 설정합니다.
    doc_ref = db.collection(sender_id).document('status')
    status = doc_ref.get().to_dict()
    status['write_mode'] = mode
    doc_ref.set(status)


def get_mode(sender_id):
    # 현재 쓰기/읽기 모드를 1/0으로 다르게 반환합니다.
    doc_ref = db.collection(sender_id).document('status')
    status = doc_ref.get().to_dict()

    if status.get('write_mode') is None:
        status['write_mode'] = '0'
        doc_ref.set(status)
        return '0'

    return status.get('write_mode')


def save_chat(sender_id, date, time, text):
    # :param (sender_id)-> (data:time)-> text 구조로 DB에 저장합니다.
    city_ref = db.collection(sender_id).document(date)
    if city_ref.get().exists:
        city_ref.update({time: text})
    else:
        city_ref.set({time: text})


def load_chat_date(sender_id, date):
    '''
    :param sender_id: facebook id로 사용자별 DB를 구분하므로 필요합니다.
    :param date: 이 날짜는 시작일, 끝일을 가지고 있는 Dictionay 데이터입니다.
    :return: 이 날짜(기간) 내의 일기를 찾아 반환합니다.
    '''
    start_date = date.get('startDate')[:10]
    end_date = date.get('endDate')[:10]

    start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d')

    result = ''

    while start_date <= end_date:
        current = start_date.strftime('%Y-%m-%d')
        docs = db.collection(sender_id).document(current).get().to_dict()
        if docs is not None:
            result += "'" + start_date.strftime('%Y년 %m월 %d일') + "'"

            for _, value in sorted(docs.items()):
                result += '\n' + value

            result += '\n\n'

        start_date += datetime.timedelta(days=1)

    if result == '':
        return '그날 내게 해준 이야기가 없는 걸...'
    else:
        return result[:-2]


def load_chat_keyword(sender_id, keyword):
    '''
    :param sender_id: facebook id로 사용자별 DB를 구분하므로 필요합니다.
    :param keyword: 해당 키워드가 들어있는 일기를 찾습니다.
    :return: DB에서 특정 키워드가 들어있는 날의 일기들(여러 날이면 각 날짜별 구분 포함)을 찾아 반환합니다.
    '''
    messages = ''

    for doc_ref in db.collection(sender_id).get():
        if doc_ref.id == 'status':
            continue
        message = "'" + doc_ref.id + "'"
        for _, value in sorted(doc_ref.to_dict().items()):
            message += '\n' + value
        if keyword in message:
            messages += message + '\n\n'

    if messages == '':
        return '이야기를 찾지 못했어...'
    else:
        return messages[:-2]


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
