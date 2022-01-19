import requests
from requests.exceptions import HTTPError

# tg_bot_token = '1353063241:AAG062I8j8egOO3di7z9nFqyORqdB1D5Q_E'  # strategy_execution
tg_bot_token = '5021702910:AAEnDzAwG1Vyy4ppucISd7xQ6eP9SysBsq0' # TEST Token
chat_id = {'adam': '441225648'}


def telegram_bot_sendtext(bot_message, user='adam', tg_token=tg_bot_token):
    """
    send telegram message to a user or a list of traders in my_account_monitor_bot
    :param tg_token: telegram bot token
    :type tg_token: str
    :param bot_message: message to be sent to user or list of traders
    :type bot_message: str
    :param user: string name of the user from the chat_id dictionary, could be a list of user names
    :type user: str or list
    :return: json response with success or not
    """
    bot_token = tg_token
    #todo if needed '/' could be replaced here with nothing to have BTCUSDT instead of BTC/USDT - potential hashtag issue?
    # ie BTC/USDT and BTC/ETH will both just be #BTC

    # special characters can't be used on telegram - to use telegram requires the \ to be used before special characters (see below)
    # or we delete the characters altogether
    bot_msg_formatted = bot_message.replace("_", "\_").replace("*", "\*").replace("`", "\`").replace("[", "\[")

    # send messages to every user in a list
    if isinstance(user, list):
        for person in user:
            bot_chat_id = chat_id.get(person)
            send_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + \
                        bot_chat_id + '&parse_mode=Markdown&text=' + bot_msg_formatted
            try:
                response = requests.get(send_text)
                # If the response was successful, no Exception will be raised
                response.raise_for_status()
            except HTTPError as http_err:
                print(f'HTTP error occurred: {http_err}')
            except Exception as err:
                print(f'Other error occurred: {err}')
            else:
                print('Success - message was sent')

    else:
        # send messages to a single user
        if isinstance(user, str):
            bot_chat_id = chat_id.get(user)
            send_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + \
                        bot_chat_id + '&parse_mode=Markdown&text=' + bot_msg_formatted
        # if wrong user format, notify
        else:
            message = f'unsupported type past to user: {type(user)}'
            print(message)
            send_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + \
                        '297764520' + '&parse_mode=Markdown&text=' + message

        try:
            response = requests.get(send_text)
            # If the response was successful, no Exception will be raised
            response.raise_for_status()
        except HTTPError as http_err:
            print(f'HTTP error occurred: {http_err}')
        except Exception as err:
            print(f'Other error occurred: {err}')
        else:
            print('Success - message was sent')


def notify_missing_markets(missing_markets, users):
    """
    send notifications about missing markets
    :param missing_markets: list of strings with symbols: ['BTC/USDT', 'ETH/USDT']
    :param users: telegram users that should receive notifications
    :return:
    """
    if len(missing_markets) == 0:
        return
    msg = f'Portfolio GDA V2  Strategy\n' \
          f'There are {len(missing_markets)} missing markets: {missing_markets}.\n' \
          f'These markets should be substituted.'

    telegram_bot_sendtext(msg, user=users)
