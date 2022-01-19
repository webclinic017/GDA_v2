from datetime import datetime
from helpers import retry, get_pos_info, get_f_balance, get_price, log, record_trade_data, get_all_positions
from time import sleep
from config import params_portfolio_ema_cross as strat_params
from ccxt.base.errors import ExchangeError

from subprocess import Popen
import sys


if strat_params['DEBUG'] in ('yes', 'true', 't', 'y', '1'):
    DEBUG = True
elif strat_params['DEBUG'] in ('no', 'false', 'f', 'n', '0'):
    DEBUG = False


@retry
def make_order(exchange_obj, symbol, direction, order_type, price, trade_size,
               order_params={'reduceOnly': 'false', 'newClientOrderId': 'open_cross'}, tg_user=strat_params['dev_user']):
    """

    :param exchange_obj: obj - ccxt exchange object
    :param symbol: str - market symbol e.g. BTC/USDT
    :param direction: str - either 'buy' or 'sell'
    :param order_type: str - e.g. 'market' or 'limit'
    :param price: float - price at which order should be executed, not used with market orders
    :param trade_size: float - size to execute
    :param order_params: dict - additional params to send to exchange e.g. if the order should be reduceOnly and id of trade
    :param tg_user: list or str - users from the tg_messenger.py that should  get notifications
    :return: dict - api response from an exchange
    """

    pos_size = abs(float(trade_size))
    order = None
    id = order_params['newClientOrderId']
    if order_type == 'limit':
        order = exchange_obj.create_order(symbol=symbol, type=order_type, side=direction,
                                          amount=pos_size, price=price, params=order_params)

    elif order_type == 'market':
        order = exchange_obj.create_order(symbol=symbol, type=order_type, side=direction,
                                          amount=pos_size, params=order_params)

    msg = f'created a {order_type} order ({id}) to {direction} %23{symbol} {pos_size} @ {price}'
    if order_params['reduceOnly'] == 'true':
        msg = 'CLOSE ORDER | ' + msg

    log(txt=msg, config_file=strat_params, receivers=tg_user)

    sleep(0.5)

    return order


def strategy_signal_trade(exchange_obj, trading_datas, indicators_datas, current_poses,
                          markets_and_rules, tg_user, debug=True):
    """

    :param exchange_obj: obj - ccxt exchange object
    :param trading_datas: dict - {'data name': pd.DF} data on which we make trades
    :param indicators_datas: dict - {'data name': pd.DF} data which holds indicators
    :param current_poses: dict - with recorded trades
    :param markets_and_rules: dict - dict with markets as keys and trading rules as values
    :param tg_user: list or str - users from the tg_messenger.py that should  get notifications
    :param debug: bool - switch debug mode without trades or not
    :return: dict - api responses from the exchange about the trades
    """

    markets = list(trading_datas.keys())
    mult = strat_params['balance_mult']

    cancel_open_orders = retry(exchange_obj.cancelAllOrders)

    responses = {}
    for smbl in markets:
        responses[smbl] = {'close_trade': None, 'open_trade': None}

        all_open_poses = get_all_positions(exchange_obj)
        last_trade_time = int([a['updateTime'] for a in all_open_poses if a['symbol'] == [smbl]][0]) / 1000
        entry_time = datetime.utcfromtimestamp(last_trade_time).strftime('%Y-%m-%d %H:%M:%S')

        if trading_datas[smbl].iloc[-1]['ema_1_cross'] == 1:

            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size != 0:  # if there is an open position

                current_size = float(p_size['positionAmt'])
                if current_size < 0:  # if the position is short, close position

                    balance = get_f_balance(exchange_obj)
                    pnl = float(p_size['unRealizedProfit'])
                    price = get_price(exchange_obj, smbl)
                    portf_pnl = round((float(pnl) / float(balance)) * 100, 3)

                    msg = f'long %23{smbl} @ {price} | current size: {current_size} | close short | ' \
                          f'balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_close_cross'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='buy', order_type='market',
                                          price=price, trade_size=current_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    if smbl in current_poses['trading_data']:
                        del current_poses['trading_data'][smbl]  # delete symbol from recorded poses

                    # cancel open take profits
                    resp = cancel_open_orders(symbol=smbl)
                    c_msg = f'open limit orders for {smbl} were canceled\n {resp}'
                    log(txt=c_msg, config_file=strat_params, receivers=tg_user)

                    responses[smbl]['close_trade'] = response

                elif current_size > 0:  # if the position is long check position size is correct - SHOULD NEVER BE TRUE

                    last_price = get_price(exchange_obj, smbl)
                    pnl = float(p_size['unRealizedProfit'])
                    balance = get_f_balance(exchange_obj)
                    portf_pnl = round((float(pnl) / float(balance)) * 100, 3)

                    pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                    pos_precision = markets_and_rules[smbl]['precision']['amount']
                    price_precision = markets_and_rules[smbl]['precision']['price']

                    price = round(last_price, price_precision)

                    if trading_datas[smbl].iloc[-1]['long_emas_trend'] == 1:
                        required_size = round((balance * mult * pct_size) / price, pos_precision)
                    else:
                        required_size = round(((balance * mult * pct_size) / price) * 0.5, pos_precision)

                    msg = f'long %23{smbl} @ {price} | current size: {current_size} | we already long | ' \
                          f'balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    if abs(current_size) < abs(required_size):  # if the position is less than we need

                        msg = f'long %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                              f'add to pos | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                        log(txt=msg, config_file=strat_params, receivers=tg_user)

                        go_long = 'buy'
                        trade_size = abs(required_size) - abs(current_size)

                        orderId = f'{smbl}_open_cross_increasing'
                        params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction=go_long, order_type='market',
                                              price=price, trade_size=trade_size,
                                              order_params=params,
                                              tg_user=tg_user
                                              )
                        responses[smbl]['open_trade'] = response

                        sleep(1)
                        p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                        current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                                stop_price=float(p_info['entryPrice']),
                                                                                entry_time=entry_time,
                                                                                second_size=trade_size,
                                                                                second_entry_time=datetime.utcnow().strftime(
                                                                                    "%Y-%m-%d %H:%M:%S"),
                                                                                direction=go_long,
                                                                                size=float(p_info['positionAmt']),
                                                                                balance_at_open=float(balance))

                        continue

                    elif abs(current_size) > abs(required_size):
                        # if our current size is bigger than we need - go to the next symbol
                        msg = f'long: %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                              f'nothing to do | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                        log(txt=msg, config_file=strat_params, receivers=tg_user)
                        continue

            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size == 0:

                # if we just closed a short trade - open a new long trade
                last_price = get_price(exchange_obj, smbl)
                balance = get_f_balance(exchange_obj)

                pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                pos_precision = markets_and_rules[smbl]['precision']['amount']
                price_precision = markets_and_rules[smbl]['precision']['price']

                price = round(last_price, price_precision)

                open_size = round((balance * mult * pct_size) / price, pos_precision)

                if trading_datas[smbl].iloc[-1]['long_emas_trend'] == 1:
                #   IF WE ARE IN A LONG TERM UPTREND, OPEN UP 100% POSITION
                    msg = f'long %23{smbl} @ {price} | no open position | Full position required of size: ' \
                          f'{open_size} | balance: {balance}'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    go_long = 'buy'

                    if not DEBUG:
                        # pid1 = Popen([sys.executable, "./scale_in.py", "-symbol", smbl,
                        #               "-size", str(open_size), "-price", str(price),
                        #               "-direction", go_long])

                        ###################################################################################################
                        orderId = f'{smbl}_open_cross'
                        params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction=go_long, order_type='market',
                                              price=price, trade_size=open_size,
                                              order_params=params,
                                              tg_user=tg_user
                                              )
                        responses[smbl]['open_trade'] = response

                        sleep(1)
                        p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                        current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                                stop_price=float(p_info['entryPrice']),
                                                                                entry_time=datetime.utcnow().strftime(
                                                                                    "%Y-%m-%d %H:%M:%S"),
                                                                                direction=go_long,
                                                                                size=float(p_info['positionAmt']),
                                                                                balance_at_open=balance)

                elif trading_datas[smbl].iloc[-1]['long_emas_trend'] == -1:
                # IF LONG TERM EMAS ARE IN A DOWNTREND, OPEN UP 50% LONG POSITION SIZE

                    new_size = round(open_size * 0.5, pos_precision)
                    msg = f'long %23{smbl} @ {price} | no open position | 50% position required of size: {new_size} | balance: {balance}'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    go_long = 'buy'

                    if not DEBUG:
                        # pid1 = Popen([sys.executable, "./scale_in.py", "-symbol", smbl,
                        #               "-size", str(open_size), "-price", str(price),
                        #               "-direction", go_long])

                        ###################################################################################################
                        orderId = f'{smbl}_open_cross'
                        params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction=go_long, order_type='market',
                                              price=price, trade_size=new_size,
                                              order_params=params,
                                              tg_user=tg_user
                                              )
                        responses[smbl]['open_trade'] = response

                        sleep(1)
                        p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                        current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                                stop_price=float(p_info['entryPrice']),
                                                                                entry_time=datetime.utcnow().strftime(
                                                                                    "%Y-%m-%d %H:%M:%S"),
                                                                                direction=go_long,
                                                                                size=float(p_info['positionAmt']),
                                                                                balance_at_open=balance)


        elif trading_datas[smbl].iloc[-1]['ema_2_cross'] == 1:
            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size != 0:  # if there is an open position

                current_size = float(p_size['positionAmt'])
                last_price = get_price(exchange_obj, smbl)
                pnl = float(p_size['unRealizedProfit'])
                balance = get_f_balance(exchange_obj)
                portf_pnl = round((float(pnl) / float(balance)) * 100, 3)

                pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                pos_precision = markets_and_rules[smbl]['precision']['amount']
                price_precision = markets_and_rules[smbl]['precision']['price']

                price = round(last_price, price_precision)

                if current_size > 0:  # if the position is long, add additional 50% position to current long

                    required_size = round((balance * mult * pct_size) / price, pos_precision)
                    size_test = round(required_size * 0.9, pos_precision)

                    if abs(current_size) < abs(size_test):

                        msg = f'long %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                              f'adding to long second entry | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                        log(txt=msg, config_file=strat_params, receivers=tg_user)

                        go_long = 'buy'
                        trade_size = round(abs(required_size) - abs(current_size), pos_precision)

                        orderId = f'{smbl}_open_cross_increasing'
                        params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction=go_long, order_type='market',
                                              price=price, trade_size=trade_size,
                                              order_params=params,
                                              tg_user=tg_user
                                              )
                        responses[smbl]['open_trade'] = response


                        sleep(1)
                        p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                        current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                                stop_price=float(p_info['entryPrice']),
                                                                                entry_time=entry_time,
                                                                                second_size=trade_size,
                                                                                second_entry_time=datetime.utcnow().strftime(
                                                                                    "%Y-%m-%d %H:%M:%S"),
                                                                                direction=go_long,
                                                                                size=float(p_info['positionAmt']),
                                                                                balance_at_open=float(balance))

                        continue

                    elif abs(current_size) > abs(size_test):
                        # if our current size is bigger than we need - go to the next symbol
                        msg = f'long: %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                              f'nothing to do | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                        log(txt=msg, config_file=strat_params, receivers=tg_user)
                        continue

                if current_size < 0:  # if the position is short, reduce short position by 50%

                    required_size = round(current_size * 0.5, pos_precision)

                    msg = f'long %23{smbl} @ {price} | current size: {current_size} | reducing short by 50% | ' \
                          f'required size: {required_size} | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    trade_size = abs(required_size, pos_precision)

                    orderId = f'{smbl}_open_cross_decreasing'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='buy', order_type='market',
                                          price=price, trade_size=trade_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )
                    responses[smbl]['open_trade'] = response

                    sleep(1)
                    p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                    current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                            stop_price=float(p_info['entryPrice']),
                                                                            entry_time=entry_time,
                                                                            second_size=trade_size,
                                                                            second_entry_time=datetime.utcnow().strftime(
                                                                                "%Y-%m-%d %H:%M:%S"),
                                                                            direction="sell",
                                                                            size=float(p_info['positionAmt']),
                                                                            balance_at_open=float(balance))

                    continue


        elif trading_datas[smbl].iloc[-1]['ema_1_cross'] == -1:  # _______________________________________________________

            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size != 0:  # if there is an open position

                current_size = float(p_size['positionAmt'])
                if current_size > 0:  # if the position is long THEN CLOSE

                    balance = get_f_balance(exchange_obj)
                    pnl = float(p_size['unRealizedProfit'])
                    price = get_price(exchange_obj, smbl)
                    portf_pnl = round((float(pnl) / float(balance)) * 100, 3)

                    msg = f'short %23{smbl} @ {price} | current size: {current_size} | close long | ' \
                          f'balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_close_cross'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='sell', order_type='market',
                                          price=price, trade_size=current_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    # # cancel open take profits
                    # resp = cancel_open_orders(symbol=smbl)
                    # c_msg = f'open limit orders for {smbl} were canceled\n {resp}'
                    # log(txt=c_msg, config_file=strat_params, receivers=tg_user)

                    if smbl in current_poses['trading_data']:
                        del current_poses['trading_data'][smbl]  # delete symbol from recorded poses

                    responses[smbl]['close_trade'] = response

                    continue

                # elif current_size < 0:  # if the position is short______________________________________________________
                #     # IF EITHER 100% SHORT OR 50% SHORT (AFTER LONG EMAS TURNED LONG SIGNAL WHILE SHORT EMAS REMAINED SHORT)
                #     last_price = get_price(exchange_obj, smbl)
                #     pnl = float(p_size['unRealizedProfit'])
                #     balance = get_f_balance(exchange_obj)
                #     portf_pnl = round((float(pnl) / float(balance)) * 100, 3)
                #
                #     pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                #     pos_precision = markets_and_rules[smbl]['precision']['amount']
                #     price_precision = markets_and_rules[smbl]['precision']['price']
                #
                #     price = round(last_price, price_precision)
                #
                #     if trading_datas[smbl].iloc[-1]['long_emas_trend'] == -1:
                #         # IF LONG TERM EMAS SHORT INCREASE SHORT BY 50% TO FULL POSITION
                #         required_size = round((balance * mult * pct_size) / price, pos_precision)
                #         trade_size = abs(required_size) - abs(current_size)
                #
                #         msg = f'short %23{smbl} @ {price} | current size: {current_size} | increasing short 50% adding {trade_size} | ' \
                #               f'balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                #         log(txt=msg, config_file=strat_params, receivers=tg_user)
                #
                #         go_short = 'sell'
                #         orderId = f'{smbl}_increase_short'
                #         params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                #         response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                #                               direction=go_short, order_type='market',
                #                               price=price, trade_size=trade_size,
                #                               order_params=params,
                #                               tg_user=tg_user
                #                               )
                #
                #         responses[smbl]['open_trade'] = response
                #
                #         sleep(1)
                #         p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                #         current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                #                                                                 stop_price=float(p_info['entryPrice']),
                #                                                                 entry_time=datetime.utcnow().strftime(
                #                                                                     "%Y-%m-%d %H:%M:%S"),
                #                                                                 direction=go_short,
                #                                                                 size=float(p_info['positionAmt']),
                #                                                                 balance_at_open=balance)
                #

                    # elif abs(current_size) < abs(required_size):  # if the position is less than we need__________________
                    #
                    #     msg = f'short %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                    #           f'add to pos | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    #     log(txt=msg, config_file=strat_params, receivers=tg_user)
                    #
                    #     go_short = 'sell'
                    #     trade_size = abs(required_size) - abs(current_size)
                    #
                    #     orderId = f'{smbl}_open_cross_increasing'
                    #     params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                    #     response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                    #                           direction=go_short, order_type='market',
                    #                           price=price, trade_size=trade_size,
                    #                           order_params=params,
                    #                           tg_user=tg_user
                    #                           )
                    #     responses[smbl]['open_trade'] = response
                    #
                    #     sleep(1)
                    #     p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                    #     current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                    #                                                             stop_price=float(p_info['entryPrice']),
                    #                                                             entry_time=datetime.utcnow().strftime(
                    #                                                                 "%Y-%m-%d %H:%M:%S"),
                    #                                                             direction=go_short,
                    #                                                             size=float(p_info['positionAmt']),
                    #                                                             balance_at_open=float(balance))

                    # elif abs(current_size) > abs(required_size):
                    #     # if our current size is bigger than we need - go to the next symbol
                    #     msg = f'short: %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                    #           f'nothing to do | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    #     log(txt=msg, config_file=strat_params, receivers=tg_user)
                    #     continue

        elif trading_datas[smbl].iloc[-1]['ema_2_cross'] == -1:
            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size != 0:  # if there is an open position

                current_size = float(p_size['positionAmt'])
                last_price = get_price(exchange_obj, smbl)
                pnl = float(p_size['unRealizedProfit'])
                balance = get_f_balance(exchange_obj)

                pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                pos_precision = markets_and_rules[smbl]['precision']['amount']
                price_precision = markets_and_rules[smbl]['precision']['price']

                price = round(last_price, price_precision)

                if current_size < 0:  # if the position is short, add additional 50% position to current short
                # ONLY HAPPENS WHEN SHORT POSITION WAS RECENTLY REDUCED FROM 100% TO 50%, THIS WILL GET IT BACK TO 100% SHORT
                    required_size = round((balance * mult * pct_size) / price, pos_precision)
                    size_test = round(required_size * 0.9, pos_precision)

                    if abs(current_size) < abs(size_test):

                        msg = f'short %23{smbl} @ {price} | current size: {current_size} and required: {required_size} | ' \
                              f'adding to short second entry | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                        log(txt=msg, config_file=strat_params, receivers=tg_user)

                        go_short = 'sell'
                        trade_size = abs(required_size) - abs(current_size)

                        orderId = f'{smbl}_open_cross_increasing'
                        params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction=go_short, order_type='market',
                                              price=price, trade_size=trade_size,
                                              order_params=params,
                                              tg_user=tg_user
                                              )
                        responses[smbl]['open_trade'] = response

                        sleep(1)
                        p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                        current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                                stop_price=float(p_info['entryPrice']),
                                                                                entry_time=entry_time,
                                                                                second_size=trade_size,
                                                                                second_entry_time=datetime.utcnow().strftime(
                                                                                    "%Y-%m-%d %H:%M:%S"),
                                                                                direction=go_short,
                                                                                size=float(p_info['positionAmt']),
                                                                                balance_at_open=float(balance))

                        continue

                if current_size > 0:  # if the position is LONG, REDUCE LONG BY 50% SIZE

                    required_size = round(current_size * 0.5, pos_precision)

                    msg = f'short %23{smbl} @ {price} | current size: {current_size} | reducing long by 50% | ' \
                          f'required: {required_size} | balance: {balance} | u-pnl {pnl} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    go_short = 'sell'
                    trade_size = abs(required_size)

                    orderId = f'{smbl}_open_cross_increasing'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction=go_short, order_type='market',
                                          price=price, trade_size=trade_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )
                    responses[smbl]['open_trade'] = response

                    sleep(1)
                    p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                    current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                            stop_price=float(p_info['entryPrice']),
                                                                            entry_time=entry_time,
                                                                            second_size=trade_size,
                                                                            second_entry_time=datetime.utcnow().strftime(
                                                                                "%Y-%m-%d %H:%M:%S"),
                                                                            direction='buy',
                                                                            size=float(p_info['positionAmt']),
                                                                            balance_at_open=float(balance))

            p_size = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
            if p_size == 0:  # _________________________________________________________________________________________

                # IF NO POSITION OPEN 100% SHORT
                last_price = get_price(exchange_obj, smbl)
                balance = get_f_balance(exchange_obj)

                pct_size = indicators_datas[smbl]['p_size'].iloc[-2]  # get last recorded p_size
                pos_precision = markets_and_rules[smbl]['precision']['amount']
                price_precision = markets_and_rules[smbl]['precision']['price']

                price = round(last_price, price_precision)

                open_size = round((balance * mult * pct_size) / price, pos_precision)

                msg = f'short %23{smbl} @ {price} | no open position | Open Full Position | required size: {open_size} | balance: {balance}'
                log(txt=msg, config_file=strat_params, receivers=tg_user)

                go_short = 'sell'

                if not DEBUG:
                    # pid1 = Popen([sys.executable, "./scale_in.py", "-symbol", smbl,
                    #               "-size", str(open_size), "-price", str(price),
                    #               "-direction", go_short])

                    ###################################################################################################
                    orderId = f'{smbl}_open_cross'
                    params = {'reduceOnly': 'false', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction=go_short, order_type='market',
                                          price=price, trade_size=open_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    responses[smbl]['open_trade'] = response

                    sleep(1)
                    p_info = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
                    current_poses['trading_data'][smbl] = record_trade_data(entry_price=float(p_info['entryPrice']),
                                                                            stop_price=float(p_info['entryPrice']),
                                                                            entry_time=datetime.utcnow().strftime(
                                                                                "%Y-%m-%d %H:%M:%S"),
                                                                            direction=go_short,
                                                                            size=float(p_info['positionAmt']),
                                                                            balance_at_open=balance)

    return responses, current_poses


def check_variable_atr_stops(exchange_obj, trading_datas, indicator_datas,
                             current_positions, n_of_atrs, atr_l, tg_user):
    """

    :param exchange_obj: obj - ccxt exchange object
    :param trading_datas: dict - {'data name': pd.DF} data on which we make trades
    :param indicators_datas: dict - {'data name': pd.DF} data which holds indicators
    :param current_positions: dict - with recorded trades
    :param n_of_atrs: int or float - multiplier for ATRs
    :param atr_l: int - length of the atr to search for
    :param tg_user: list or str - users from the tg_messenger.py that should  get notifications
    :return: dict - api responses from the exchange about the trades
    """
    markets = list(trading_datas.keys())

    responses = {}
    for smbl in markets:
        pos_data = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)
        if pos_data != 0:

            pos_size = float(pos_data['positionAmt'])

            previous_close_price = float(trading_datas[smbl].iloc[-2]['close'])
            current_close_price = float(trading_datas[smbl].iloc[-1]['close'])
            atr = float(indicator_datas[smbl].iloc[-2][f'atr_{atr_l[0]}'])

            short_atr_stop_price = previous_close_price + (atr * n_of_atrs)
            long_atr_stop_price = previous_close_price - (atr * n_of_atrs)

            print(f'{smbl} | our pos size is {pos_size}, last_price: {previous_close_price} '
                  f'and current: {current_close_price}')
            print(f'our stop if we are long: {long_atr_stop_price} and if short: {short_atr_stop_price}')

            if pos_size > 0:  # we are long
                if current_close_price < long_atr_stop_price:

                    price = float(pos_data['markPrice'])
                    pnl = float(pos_data['unRealizedProfit'])
                    balance = get_f_balance(exchange_obj)
                    portf_pnl = round( (float(pnl) / float(balance)) * 100, 3)

                    msg = f'ATR close position on %23{smbl} @ {price} | size: {pos_size} | ' \
                          f'ATR stop price: {long_atr_stop_price} | ' \
                          f'u-pnl {pnl} | portfolio pnl {portf_pnl} % | balance: {balance}'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_stop_loss'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='sell', order_type='market',
                                          price=price, trade_size=pos_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    responses[smbl] = response

            elif pos_size < 0:  # we are short
                if current_close_price > short_atr_stop_price:

                    price = float(pos_data['markPrice'])
                    pnl = float(pos_data['unRealizedProfit'])
                    balance = get_f_balance(exchange_obj)
                    portf_pnl = round( (float(pnl) / float(balance)) * 100, 3)

                    msg = f'ATR close position on %23{smbl} @ {price} | size: {pos_size} | ' \
                          f'ATR stop price: {long_atr_stop_price} | ' \
                          f'u-pnl {pnl} | portfolio pnl {portf_pnl} % | balance: {balance}'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_stop_loss'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='buy', order_type='market',
                                          price=price, trade_size=pos_size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    responses[smbl] = response
        sleep(0.2)

    return responses


def update_trailing_atr_stops(indicator_datas, current_poses, atr_l, n_of_atrs, tg_user):
    """
    updated STOP Price from which the ATR stop loss price should be calculated
    :param indicator_datas: dict with trading datas
    :param current_poses: dict with current positions which are stored in the trading_data key
    :param atr_l: length of ATR indicator
    :param n_of_atrs: number of ATRs
    :param tg_user: ids of telegram users
    :return: updated current positions
    """
    markets = list(indicator_datas.keys())

    for smbl in markets:
        if smbl in current_poses['trading_data']:

            # find the date of the entry to search for this day in indicators_data
            entry_price = current_poses['trading_data'][smbl]['entry_price']
            entry_time = current_poses['trading_data'][smbl]['entry_time']
            entry_day = entry_time[:entry_time.index(" ")]
            # get the latest stop price
            stop_price = current_poses['trading_data'][smbl]['stop_price']

            atr = float(indicator_datas[smbl].iloc[-2][f'atr_{atr_l[0]}'])

            if current_poses['trading_data'][smbl]['direction'] == 'buy':

                # get the highest close price since trade entry date
                long_mask = (indicator_datas[smbl].close > stop_price) & (indicator_datas[smbl].index > entry_day)
                highest_close = indicator_datas[smbl].loc[long_mask]['close'].max()

                if highest_close > stop_price:

                    past_stop_loss = stop_price - (atr * n_of_atrs)
                    new_stop_loss = highest_close - (atr * n_of_atrs)
                    current_poses['trading_data'][smbl]['stop_price'] = highest_close
                    current_poses['trading_data'][smbl]['stop_loss'] = new_stop_loss

                    # tps = ''
                    # if current_poses['trading_data'][smbl]['tp1_executed']:
                    #     tps = f'| %23TP1{smbl}'
                    # if current_poses['trading_data'][smbl]['tp2_executed']:
                    #     tps = f'| %23TP2{smbl}'

                    msg = f'long %23{smbl} reset ATR stop price from {stop_price} to {highest_close}\n' \
                          f'STOP LOSS was updated from {past_stop_loss:.6f}$ to {new_stop_loss:.6f}$\n ' \
                          f'entry price: {entry_price}$ '
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

            elif current_poses['trading_data'][smbl]['direction'] == 'sell':

                # get the lowest close price since trade entry date
                short_mask = (indicator_datas[smbl].close < stop_price) & (indicator_datas[smbl].index > entry_day)
                lowest_close = indicator_datas[smbl].loc[short_mask]['close'].min()

                if lowest_close < stop_price:

                    past_stop_loss = stop_price + (atr * n_of_atrs)
                    new_stop_loss = lowest_close + (atr * n_of_atrs)
                    current_poses['trading_data'][smbl]['stop_price'] = lowest_close
                    current_poses['trading_data'][smbl]['stop_loss'] = new_stop_loss

                    # tps = ''
                    # if current_poses['trading_data'][smbl]['tp1_executed']:
                    #     tps = f'| %23TP1{smbl}'
                    # if current_poses['trading_data'][smbl]['tp2_executed']:
                    #     tps = f'| %23TP2{smbl}'

                    msg = f'short %23{smbl} reset ATR stop price from {stop_price} to {lowest_close}\n' \
                          f'STOP LOSS was updated from {past_stop_loss:.6f}$ to {new_stop_loss:.6f}$\n ' \
                          f'entry price: {entry_price}$ '
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

    return current_poses


def execute_atr_trailing_stops(exchange_obj, indicator_datas, current_poses, atr_l, n_of_atrs, tg_user):
    """
    execute ATR trailing stop per asset if conditions satisfied
    :param exchange_obj: ccxt exchange object
    :param indicator_datas: dict with trading datas
    :param current_poses: dict with current positions (stored in the trading_data key)
    :param atr_l: length of ATR indicator
    :param n_of_atrs: number of ATR values to use
    :param tg_user: ids of telegram users
    :return: updated current positions
    """
    markets = list(indicator_datas.keys())

    balance = get_f_balance(exchange_obj=exchange_obj)

    for smbl in markets:
        if smbl in current_poses['trading_data']:

            stop_price = current_poses['trading_data'][smbl]['stop_price']
            atr = float(indicator_datas[smbl].iloc[-2][f'atr_{atr_l[0]}'])
            long_stop_loss = stop_price - (atr * n_of_atrs)
            short_stop_loss = stop_price + (atr * n_of_atrs)

            pos_data = get_pos_info(exchange_obj=exchange_obj, smbl=smbl)

            pnl = float(pos_data['unRealizedProfit'])
            price = float(pos_data['markPrice'])
            size = float(pos_data['positionAmt'])
            portf_pnl = round((float(pnl) / float(balance)) * 100, 3)

            if current_poses['trading_data'][smbl]['direction'] == 'buy':

                if price <= long_stop_loss:
                    msg = f'%23{smbl} - current market price: ({price}) <= stop price ({long_stop_loss}) |\n' \
                          f'ATR trailing stop loss triggered | closing long {size} @ {price}' \
                          f'\nbalance: {balance:.3f} | u-PnL {pnl:.5f} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_stop_loss'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='sell', order_type='market',
                                          price=price, trade_size=size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    del current_poses['trading_data'][smbl]  # delete symbol from recorded poses

            elif current_poses['trading_data'][smbl]['direction'] == 'sell':

                if price >= short_stop_loss:
                    msg = f'%23{smbl} - current market price: ({price}) >= stop price ({short_stop_loss}) |\n' \
                          f'ATR trailing stop loss triggered | closing short {size} @ {price}' \
                          f'\nbalance: {balance:.3f} | u-PnL {pnl:.5f} | portfolio pnl {portf_pnl} %'
                    log(txt=msg, config_file=strat_params, receivers=tg_user)

                    orderId = f'{smbl}_stop_loss'
                    params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                    response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                          direction='buy', order_type='market',
                                          price=price, trade_size=size,
                                          order_params=params,
                                          tg_user=tg_user
                                          )

                    del current_poses['trading_data'][smbl]  # delete symbol from recorded poses

    return current_poses


def execute_market_take_profits(exchange_obj, trading_datas, current_poses, strat_params, markets_and_rules, tg_users):
    """
    if price crossed certain level - execute market take profit
    :param exchange_obj: ccxt exchange object
    :param trading_datas: dict with keys as data names and values as price dfs
    :param current_poses: dict with current positions
    :param strat_params: strategy parameters (params.json) imported from config.py
    :param markets_and_rules: trading rules for different assets from the exchage
    :param tg_users: ids of telegram users
    :return: updated dict with current positions
    """
    markets = list(trading_datas.keys())

    for smbl in markets:
        if smbl in current_poses['trading_data']:

            pos_precision = markets_and_rules[smbl]['precision']['amount']
            price_precision = markets_and_rules[smbl]['precision']['price']

            pos_info = current_poses['trading_data'][smbl]

            entry_price = pos_info['entry_price']
            last_close = trading_datas[smbl].iloc[-1]['close']

            price_diff = round((last_close - entry_price) / entry_price * 100, 3)

            if pos_info['direction'] == 'buy':
                if price_diff > strat_params['long_price_pct_change_tp1'] and not pos_info['tp1_executed']:

                    tp_pct = strat_params['long_pct_pos_reduction_tp1']
                    size_reduction_pct = tp_pct / 100
                    close_size = round(abs(pos_info['max_size']) * size_reduction_pct, pos_precision)

                    current_poses['trading_data'][smbl]['tp1_executed'] = True
                    current_poses['trading_data'][smbl]['position_size'] -= close_size
                    remaining = current_poses['trading_data'][smbl]['position_size']

                    try:
                        orderId = f'{smbl}_tp_1'
                        params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction='sell', order_type='market',
                                              price=last_close, trade_size=close_size,
                                              order_params=params,
                                              tg_user=tg_users
                                              )
                    except Exception as e:
                        msg = f'ERROR: %23{smbl} long TP1 reduce size: {close_size} @ {last_close}\n{e}'
                        log(txt=msg, config_file=strat_params, receivers=tg_users)
                        continue

                    msg = f'%23{smbl} hit long TP1 @ {last_close} | ' \
                          f'reducing position size by {close_size} ({tp_pct} pct) | remaining size {remaining}, ' \
                          f'%23TP1{smbl}'
                    log(txt=msg, config_file=strat_params, receivers=tg_users)

                if price_diff > strat_params['long_price_pct_change_tp2'] and not pos_info['tp2_executed']:

                    tp_pct = strat_params['long_pct_pos_reduction_tp2']
                    size_reduction_pct = tp_pct / 100
                    close_size = round(abs(pos_info['max_size']) * size_reduction_pct, pos_precision)

                    current_poses['trading_data'][smbl]['tp2_executed'] = True
                    current_poses['trading_data'][smbl]['position_size'] -= close_size
                    remaining = current_poses['trading_data'][smbl]['position_size']

                    try:
                        orderId = f'{smbl}_tp_2'
                        params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction='sell', order_type='market',
                                              price=last_close, trade_size=close_size,
                                              order_params=params,
                                              tg_user=tg_users
                                              )
                    except Exception as e:
                        msg = f'ERROR: %23{smbl} long TP2 reduce size: {close_size} @ {last_close}\n{e}'
                        log(txt=msg, config_file=strat_params, receivers=tg_users)
                        continue

                    msg = f'%23{smbl} hit long TP2 @ {last_close} | ' \
                          f'reducing position size by {close_size} ({tp_pct} pct) | remaining size {remaining}, ' \
                          f'%23TP2{smbl}'
                    log(txt=msg, config_file=strat_params, receivers=tg_users)

            elif pos_info['direction'] == 'sell':
                if price_diff < -strat_params['short_price_pct_change_tp1'] and not pos_info['tp1_executed']:

                    tp_pct = strat_params['short_pct_pos_reduction_tp1']
                    size_reduction_pct = tp_pct / 100
                    close_size = round(abs(pos_info['max_size']) * size_reduction_pct, pos_precision)

                    current_poses['trading_data'][smbl]['tp1_executed'] = True
                    current_poses['trading_data'][smbl]['position_size'] += close_size
                    remaining = current_poses['trading_data'][smbl]['position_size']

                    try:
                        orderId = f'{smbl}_tp_1'
                        params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction='buy', order_type='market',
                                              price=last_close, trade_size=close_size,
                                              order_params=params,
                                              tg_user=tg_users
                                              )
                    except Exception as e:
                        msg = f'ERROR: %23{smbl} short TP1 reduce size: {close_size} @ {last_close}\n{e}'
                        log(txt=msg, config_file=strat_params, receivers=tg_users)
                        continue

                    msg = f'%23{smbl} hit short TP1 @ {last_close} | ' \
                          f'reducing position size by {close_size} ({tp_pct} pct) | remaining size {remaining}, ' \
                          f'%23TP1{smbl}'
                    log(txt=msg, config_file=strat_params, receivers=tg_users)

                if price_diff < -strat_params['short_price_pct_change_tp2'] and not pos_info['tp2_executed']:

                    tp_pct = strat_params['short_pct_pos_reduction_tp2']
                    size_reduction_pct = tp_pct / 100
                    close_size = round(abs(pos_info['max_size']) * size_reduction_pct, pos_precision)

                    current_poses['trading_data'][smbl]['tp2_executed'] = True
                    current_poses['trading_data'][smbl]['position_size'] += close_size
                    remaining = current_poses['trading_data'][smbl]['position_size']

                    try:
                        orderId = f'{smbl}_tp_2'
                        params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
                        response = make_order(exchange_obj=exchange_obj, symbol=smbl,
                                              direction='buy', order_type='market',
                                              price=last_close, trade_size=close_size,
                                              order_params=params,
                                              tg_user=tg_users
                                              )
                    except Exception as e:
                        msg = f'ERROR: %23{smbl} short TP2 reduce size: {close_size} @ {last_close}\n{e}'
                        log(txt=msg, config_file=strat_params, receivers=tg_users)
                        continue

                    msg = f'%23{smbl} hit short TP2 @ {last_close} | ' \
                          f'reducing position size by {close_size} ({tp_pct} pct) | remaining size {remaining}, ' \
                          f'%23TP2{smbl}'
                    log(txt=msg, config_file=strat_params, receivers=tg_users)

    return current_poses


def create_limit_tp(pos_info, exchange_obj, symbol, direction, strat_params,
                    n_tp, tp_price, pos_precision, tg_users):
    """
    creates Limit Take Profit and returns information about position with data about TP
    :param pos_info: position information for specific asset taken from trading_data key of current_positions
    :param exchange_obj: ccxt exchange object
    :param symbol: asset symbol e.g. BTC/USDT
    :param direction: either Long or Short
    :param strat_params: strategy config (params.json) imported from the config.py
    :param n_tp: Take Profit number
    :param tp_price: price for the take profit
    :param pos_precision: number of decimal points for position size
    :param tg_users: ids of TG users
    :return: info about created take profit and api response
    """
    if direction == 'buy':
        side = 'long'
        reduce_action = 'sell'
    elif direction == 'sell':
        side = 'short'
        reduce_action = 'buy'

    if direction == 'buy':

        if tp_price < pos_info['stop_price']:
            pos_info[f'tp{n_tp}_executed'] = True
            return pos_info, None

    elif direction == 'sell':

        if tp_price > pos_info['stop_price']:
            pos_info[f'tp{n_tp}_executed'] = True
            return pos_info, None

    tp_size = round(pos_info['max_size'] * (strat_params[f'{side}_pct_pos_reduction_tp{n_tp}'] / 100),
                    pos_precision)

    try:
        orderId = f'{symbol}_tp_{n_tp}'
        params = {'reduceOnly': 'true', 'newClientOrderId': orderId}
        tp_resp = make_order(exchange_obj=exchange_obj, symbol=symbol,
                             direction=reduce_action, price=tp_price,
                             order_type='limit', trade_size=tp_size,
                             order_params=params)

    except ExchangeError as e:
        msg = f'error while creating limit {reduce_action} TP{n_tp} for {symbol}| p: {tp_price}, size: {tp_size} \n{e}'
        log(txt=msg, config_file=strat_params)
        return pos_info, None

    pos_info[f'tp{n_tp}_price'] = tp_price
    pos_info[f'tp{n_tp}_size'] = tp_size
    pos_info[f'tp{n_tp}_id'] = tp_resp['id']

    msg = f'%23TP{n_tp}{symbol} - created a limit order to reduce {direction} pos by {tp_size} @ {tp_price}'
    log(txt=msg, config_file=strat_params, receivers=tg_users)

    return pos_info, tp_resp


def create_check_limit_take_profits(exchange_futures, exchange_spot, strat_params,
                                    current_poses, markets_and_rules, tg_users):
    """
    checks if take profits should be created and creates them if conditions are satisfied
    :param exchange_futures: ccxt exchange object
    :param exchange_spot: ccxt exchange object
    :param strat_params: strategy config (params.json) imported from config.py
    :param current_poses: dictionary with current positions
    :param markets_and_rules: dictionary with trading rules for assets
    :param tg_users: ids of telegram users
    :return: updated current positions 
    """
    get_futures_open_orders = retry(exchange_futures.fapiPrivate_get_openorders)

    all_futures_open_orders = get_futures_open_orders()  # returns a list of open orders

    cancel_futures_open_orders_on = retry(exchange_futures.cancelAllOrders)

    for smbl in current_poses['trading_data']:
        pos_info = current_poses['trading_data'][smbl]

        pos_precision = markets_and_rules[smbl]['precision']['amount']
        price_precision = markets_and_rules[smbl]['precision']['price']

        multi_down = float([i.get('multiplierDown') for i in markets_and_rules[smbl]['info']['filters'] if
                            'multiplierDown' in i.keys()][0])

        multi_up = float([i.get('multiplierUp') for i in markets_and_rules[smbl]['info']['filters'] if
                          'multiplierUp' in i.keys()][0])

        smbl_open_orders = [i for i in all_futures_open_orders if i['symbol'] == smbl.replace('/', '')]

        if len(smbl_open_orders) > 2:
            cancel_futures_open_orders_on(symbol=smbl)
            msg = f'{smbl} had more than 2 limit orders, so they were deleted and will be created again'
            log(txt=msg, config_file=strat_params, receivers=tg_users)
            pos_info['tp1_price'] = None
            pos_info['tp1_size'] = None
            pos_info['tp1_id'] = None
            pos_info['tp2_price'] = None
            pos_info['tp2_size'] = None
            pos_info['tp2_id'] = None

        if pos_info['direction'] == 'buy':
            stop_p = float(pos_info['stop_price'])

            if not pos_info['tp1_executed'] and pos_info['tp1_id'] is None:

                target1_change = pos_info['entry_price'] * (strat_params[f'long_price_pct_change_tp1'] / 100)
                tp1_price = round(pos_info['entry_price'] + target1_change, price_precision)

                if stop_p * float(multi_down) < tp1_price < stop_p * float(multi_up):

                    pos_info, tp1_resp = create_limit_tp(pos_info=pos_info, exchange_obj=exchange_futures,
                                                         symbol=smbl, direction='buy', n_tp=1, tp_price=tp1_price,
                                                         tg_users=tg_users, strat_params=strat_params,
                                                         pos_precision=pos_precision)

            if not pos_info['tp2_executed'] and pos_info['tp2_id'] is None:

                target2_change = pos_info['entry_price'] * (strat_params[f'long_price_pct_change_tp2'] / 100)
                tp2_price = round(pos_info['entry_price'] + target2_change, price_precision)

                if stop_p * float(multi_down) < tp2_price < stop_p * float(multi_up):

                    pos_info, tp2_resp = create_limit_tp(pos_info=pos_info, exchange_obj=exchange_futures,
                                                         symbol=smbl, direction='buy', n_tp=2, tp_price=tp2_price,
                                                         tg_users=tg_users, strat_params=strat_params,
                                                         pos_precision=pos_precision)

        if pos_info['direction'] == 'sell':

            stop_p = float(pos_info['stop_price'])

            if not pos_info['tp1_executed'] and pos_info['tp1_id'] is None:

                target1_change = pos_info['entry_price'] * (strat_params[f'short_price_pct_change_tp1'] / 100)
                tp1_price = round(pos_info['entry_price'] - target1_change, price_precision)

                if stop_p * float(multi_down) < tp1_price < stop_p * float(multi_up):

                    pos_info, tp1_resp = create_limit_tp(pos_info=pos_info, exchange_obj=exchange_futures,
                                                         symbol=smbl, direction='sell', n_tp=1, tp_price=tp1_price,
                                                         tg_users=tg_users, strat_params=strat_params,
                                                         pos_precision=pos_precision)

            if not pos_info['tp2_executed'] and pos_info['tp2_id'] is None:

                target2_change = pos_info['entry_price'] * (strat_params[f'short_price_pct_change_tp2'] / 100)
                tp2_price = round(pos_info['entry_price'] - target2_change, price_precision)

                if stop_p * float(multi_down) < tp2_price < stop_p * float(multi_up):

                    pos_info, tp2_resp = create_limit_tp(pos_info=pos_info, exchange_obj=exchange_futures,
                                                         symbol=smbl, direction='sell', n_tp=2, tp_price=tp2_price,
                                                         tg_users=tg_users, strat_params=strat_params,
                                                         pos_precision=pos_precision)

    return current_poses
