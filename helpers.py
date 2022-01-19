from functools import wraps
from datetime import datetime
from time import sleep
from ccxt.base.errors import NetworkError, OrderNotFound, ExchangeError
from pathlib import Path
from tg_messenger import telegram_bot_sendtext
from config import params_portfolio_ema_cross as strat_params
import json
import traceback


def log(txt, config_file, send_telegram=True, write_to_log=True, receivers=strat_params['dev_user']):
    """
    write info to the log file and send telegram notifications
    :param txt: str to write to file and send as alert
    :param config_file: config dict which stores strat name and log file name
    :param send_telegram: bool - True to send telegram alerts with txt
    :param write_to_log: bool - True to write data to the log file
    :param receivers: str or list of str - ids of telegram users
    :return:
    """
    date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    strat_name = config_file['strategy_name']
    msg = f'{date} utc | {strat_name} | \n{txt}'
    print(msg)

    # write log to a file
    if write_to_log:
        with open(config_file["log_file"], "a") as text_file:
            print(msg.replace('\n', ' '), file=text_file)

    if send_telegram:
        telegram_bot_sendtext(msg, receivers)


def retry(func):
    @wraps(func)
    def retry_func(*args, **kwargs):
        retries = 5
        for i in range(retries):
            print('{} - {} - Attempt {}'.format(datetime.now(), func.__name__, i))
            try:
                return func(*args, **kwargs)
            except (NetworkError, ExchangeError) as e:
                log(f'network/exchange error {e}', config_file=strat_params, send_telegram=False)
                sleep(2)
                if i == retries - 1:
                    raise

    return retry_func


@retry
def get_pos_info(exchange_obj, smbl):
    """returns size as a float of the position on binance futures for a symbol specified as a str"""
    my_pos = exchange_obj.fapiPrivate_get_positionrisk()
    p = [pos for pos in my_pos if float(pos['positionAmt']) != 0.0 and pos['symbol'] == smbl.replace('/', '')]
    try:
        p_size = p[0]
    except IndexError:
        p_size = 0
    return p_size


@retry
def get_all_positions(exchange_obj):
    """

    example pos structure:
    [{'symbol': 'EOS/USDT', 'positionAmt': '1780.6', 'entryPrice': '2.7010', 'markPrice': '3.05863762',
    'unRealizedProfit': '636.80954617', 'liquidationPrice': '0', 'leverage': '20', 'maxNotionalValue': '250000',
    'marginType': 'cross', 'isolatedMargin': '0.00000000', 'isAutoAddMargin': 'false', 'positionSide': 'BOTH'}]

    :param exchange_obj: ccxt exchange object
    :return: list of dicts with positions
    """
    my_pos = exchange_obj.fapiPrivateV2_get_positionrisk()
    poses = [pos for pos in my_pos if float(pos['positionAmt']) != 0.0] #only get assets with a pos size != 0

    if len(poses) == 0:
        return []

    for p in poses:
        symb = p['symbol'].replace('USDT', '/USDT')
        p['symbol'] = symb

    return poses


@retry
def get_f_balance(exchange_obj):
    """returns futures balance of the account as a float - takes ccxt exchange object"""
    balance = exchange_obj.fetch_balance()
    # gets total equity for position sizing...
    #total_b = balance['USDT']['total']
    # OR, use below to get wallet balance (not equity) for position sizing:
    total_b = float(balance['info']['totalWalletBalance'])
    return total_b


def get_s_balance(exchange_obj):
    """
    takes ccxt exchange object returns a dict with available usdt, total usdt and
    dict with other assets total balances
    """
    get_spot_balance = retry(exchange_obj.fetch_balance)
    get_prices = retry(exchange_obj.v3_get_ticker_price)

    bal_spot = get_spot_balance()
    prices = get_prices()

    balances = {}
    bal_info = {}
    total_usdt = 0
    free_usdt = 0

    for i in bal_spot['total']:
        if bal_spot['total'][i] > 0:
            balances[i] = (bal_spot[i])
            print(f'{i}: {bal_spot["total"][i]}')

    if len(balances) > 0:
        for symbol in list(balances.keys()):
            if symbol == 'USDT':
                free_usdt = float(balances[symbol]['total'])
                total_usdt += free_usdt
                print(f'total usdt: {symbol} = {total_usdt}')

            else:
                size = float(balances[symbol]['total'])
                price = float([i for i in prices if i['symbol'] == symbol + 'USDT'][0]['price'])
                total_usdt += size * price

    bal_info['free_usdt'] = free_usdt
    bal_info['total_usdt'] = total_usdt
    bal_info['balances'] = balances

    return bal_info


@retry
def get_price(exchange_obj, symbol):
    """
    returns last price of an asset
    :param exchange_obj: ccxt exchange object
    :param symbol: str - symbol e.g. 'BTC/USDT'
    """
    price = exchange_obj.fetch_ticker(symbol)['last']
    return price


@retry
def update_acc_leverage(exchange_obj, leverage, list_of_markets, errors_to=strat_params['dev_user']):
    """
    set leverage for the account
    :param exchange_obj: ccxt exchange object
    :param leverage: int - which leverage should be set
    :param list_of_markets: list - markets on which to update leverage
    :param errors_to: list or str - user who receives notification about errors
    :return: None
    """
    for smbl in list_of_markets:
        try:
            exchange_obj.load_markets()
            market = exchange_obj.markets[smbl]
            exchange_obj.fapiPrivate_post_leverage({
                # convert a unified CCXT symbol to an exchange-specific market id
                "symbol": market['id'],
                "leverage": leverage,
            })
            sleep(0.3)
        except Exception as e:
            msg = traceback.format_exc()
            message = f'PORTFOLIO CROSS BOT\nerror in update acc leverage\n{str(msg)}\n{e}'
            log(txt=message, config_file=strat_params, receivers=errors_to)


def make_transfer(exchange_obj, asset, amount, type):
    """

    make transfers between account wallets, e.g. spot to futures, etc.

    :param exchange_obj: ccxt exchange object (spot object required)
    :param asset: str - the asset being transferred, e.g. 'USDT'
    :param amount: float - the amount to be transferred
    :param type: int - 1: transfer from spot account to USDT-M futures account.
                       2: transfer from USDT-M futures account to spot account.
                       3: transfer from spot account to COIN-M futures account.
                       4: transfer from COIN-M futures account to spot account.
    :return: dict - transfer id if success or error msg

    note: potential error - network/exchange error binance {"code":-5013,
                                                            "msg":"Asset transfer failed: insufficient balance"}
    """
    transfer = retry(exchange_obj.sapi_post_futures_transfer)
    resp = transfer(params={'asset': asset,
                            'amount': amount,
                            'type': type})
    return resp


def read_json_file(file_name):
    # check if we can get portfolio info from a file, else create it
    file = Path(file_name)

    # if file exists :
    if file.is_file():
        with open(file, 'r') as f:
            data = json.load(f)
        portfolio_dict = data
    # if file doesn't exist - create it
    else:
        print('no file')
        return {}

    return portfolio_dict


def write_json_file(file_name, data_dict):
    """
    save dict to a json file
    :param file_name: str - json file name
    :param data_dict: dict that should be saved to json file
    :return:
    """
    file = Path(file_name)
    utc_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    data_dict['last_update_time'] = utc_time

    with open(file, 'w+') as outfile:
        # default str to convert any to convert any type json don't like (i.e. datetime) to str
        json.dump(data_dict, outfile, indent=4, default=str)

    print(f"data recorded in {file}\n")


def record_trade_data(entry_price=None, entry_time=None, second_entry_price=None, second_size=None, exit_price=None, exit_time=None,
                      second_entry_time=None, stop_price=None, stop_loss=None, direction=None, open_reason=None, close_reason=None,
                      size=None, max_size=None, tp1=False, tp2=False, balance_at_open=None,
                      balance_at_close=None, trade_pnl=None):
    """ record trade information for a symbol - returns a dict"""

    if trade_pnl is None or balance_at_open is None:
        portf_pnl = 0
    else:
        portf_pnl = round((float(trade_pnl) / float(balance_at_open)) * 100, 3)

    if max_size is None:
        max_size = size

    trade_data = {
        "entry_price": entry_price,
        "second_entry_price": second_entry_price,
        "close_price": exit_price,
        "second_trade_size": second_size,
        "position_size": size,
        "max_size": max_size,
        "tp1_executed": tp1,
        "tp2_executed": tp2,
        "stop_price": stop_price,
        "stop_loss_price": stop_loss,
        "direction": direction,
        "entry_time": entry_time,
        "second_entry_time": second_entry_time,
        "close_time": exit_time,
        "open_reason": open_reason,
        "close_reason": close_reason,
        "balance_at_open": balance_at_open,
        "balance_at_close": balance_at_close,
        "trade_pnl": trade_pnl,
        "portfolio_pnl%": portf_pnl,
        "tp1_price": None,
        "tp1_size": None,
        "tp1_id": None,
        "tp2_price": None,
        "tp2_size": None,
        "tp2_id": None
    }

    return trade_data


def organise_open_positions(exchange_obj, open_positions):
    """
    format open positions
    :param exchange_obj: ccxt exchange object
    :param open_positions: dict with information about open positions returned from an exchange
    :return: dict with organised open positions
    """
    new_open = {}

    balance = get_f_balance(exchange_obj)

    for item in open_positions:

        if float(item['positionAmt']) > 0:
            direction = 'buy'
        elif float(item['positionAmt']) < 0:
            direction = 'sell'

        new_open[item['symbol']] = record_trade_data(entry_price=float(item['entryPrice']),
                                                     stop_price=float(item['entryPrice']),
                                                     entry_time=datetime.utcnow().strftime(
                                                         "%Y-%m-%d %H:%M:%S"),
                                                     direction=direction,
                                                     size=float(item['positionAmt']),
                                                     balance_at_open=float(balance))

    return new_open


def check_for_unrecorded_symbols(exchange_obj, open_poses, rec_pos, strategy_params, tg_users):
    """
    compare currently open positions and recorded positions to identify unrecorded poses
    :param exchange_obj: ccxt exchange object
    :param open_poses: dict with currently open positions
    :param rec_pos: dict with recorded positions
    :param strategy_params: strategy config
    :param tg_users: str or list of str - ids of TG users
    :return: dict with updated recorded positions
    """

    unrecorded_symbols = [symb for symb in list(open_poses.keys()) if symb not in list(rec_pos['trading_data'].keys())]

    cancel_futures_open_orders_on = retry(exchange_obj.cancelAllOrders)

    if len(unrecorded_symbols) > 0:
        msg = f'no record was found for positions in these symbols - they will be recorded:\n{unrecorded_symbols}'
        log(txt=msg, config_file=strategy_params, receivers=tg_users)

        for symbol in unrecorded_symbols:
            rec_pos['trading_data'][symbol] = open_poses[symbol]
            cancel_futures_open_orders_on(symbol=symbol)
            msg = f'limit orders on {symbol} were deleted'
            log(txt=msg, config_file=strategy_params)

    return rec_pos


def check_for_undeleted_symbols(exchange_obj, open_poses, rec_pos, strategy_params, tg_users):
    """
    compare currently open positions and recorded positions to identify undeleted poses
    :param exchange_obj: ccxt exchange object
    :param open_poses: dict with currently open positions
    :param rec_pos: dict with recorded positions
    :param strategy_params: strategy config
    :param tg_users: str or list of str - ids of TG users
    :return: dict with updated recorded positions
    """

    undeleted_poses = [symb for symb in list(rec_pos['trading_data'].keys()) if symb not in list(open_poses.keys())]

    cancel_futures_open_orders_on = retry(exchange_obj.cancelAllOrders)

    if len(undeleted_poses) > 0:
        msg = f'no open positions were found for the following symbols - they will be deleted from the records:' \
              f'\n{undeleted_poses}'
        log(txt=msg, config_file=strategy_params, receivers=tg_users)

        for symbol in undeleted_poses:
            del rec_pos['trading_data'][symbol]
            cancel_futures_open_orders_on(symbol=symbol)
            msg = f'limit orders on {symbol} were deleted'
            log(txt=msg, config_file=strategy_params)

    return rec_pos


def check_for_pos_size_changes(open_poses, rec_pos, strategy_params, tg_users):
    """
    identify position records with incorrect position sizes
    account for positions that changed direction
    :param open_poses: dict with currently open positions
    :param rec_pos: dict with recorded positions
    :param strategy_params: strategy config
    :param tg_users: str or list of str - ids of TG users
    :return: dict with updated recorded positions
    """
    for symbol in list(open_poses.keys()):

        resp_size = open_poses[symbol]['position_size']
        rec_size = rec_pos['trading_data'][symbol]['position_size']
        rec_max_size = rec_pos['trading_data'][symbol]['max_size']

        if resp_size != rec_size:

            if float(resp_size) * float(rec_size) > 0:  # if position is in the same direction

                max_size = rec_max_size

                if abs(resp_size) > abs(rec_size) and abs(resp_size) > abs(rec_max_size):
                    max_size = resp_size
                elif abs(rec_max_size) > abs(resp_size):
                    max_size = rec_max_size

                tp1 = rec_pos['trading_data'][symbol]['tp1_executed']
                tp2 = rec_pos['trading_data'][symbol]['tp2_executed']
                stop_price = rec_pos['trading_data'][symbol]['stop_price']
                entry_time = rec_pos['trading_data'][symbol]['entry_time']
                open_bal = rec_pos['trading_data'][symbol]['balance_at_open']
                tp1_price = rec_pos['trading_data'][symbol]['tp1_price']
                tp1_size = rec_pos['trading_data'][symbol]['tp1_size']
                tp1_id = rec_pos['trading_data'][symbol]['tp1_id']
                tp2_price = rec_pos['trading_data'][symbol]['tp2_price']
                tp2_size = rec_pos['trading_data'][symbol]['tp2_size']
                tp2_id = rec_pos['trading_data'][symbol]['tp2_id']

                rec_pos['trading_data'][symbol] = open_poses[symbol]

                rec_pos['trading_data'][symbol]['max_size'] = max_size
                rec_pos['trading_data'][symbol]['tp1_executed'] = tp1
                rec_pos['trading_data'][symbol]['tp2_executed'] = tp2
                rec_pos['trading_data'][symbol]['stop_price'] = stop_price
                rec_pos['trading_data'][symbol]['entry_time'] = entry_time
                rec_pos['trading_data'][symbol]['balance_at_open'] = open_bal
                rec_pos['trading_data'][symbol]['tp1_price'] = tp1_price
                rec_pos['trading_data'][symbol]['tp1_size'] = tp1_size
                rec_pos['trading_data'][symbol]['tp1_id'] = tp1_id
                rec_pos['trading_data'][symbol]['tp2_price'] = tp2_price
                rec_pos['trading_data'][symbol]['tp2_size'] = tp2_size
                rec_pos['trading_data'][symbol]['tp2_id'] = tp2_id

            else:
                rec_pos['trading_data'][symbol] = open_poses[symbol]

            msg = f'%23{symbol} - current position size is different to the recorded size - record was updated\n' \
                  f'size now: {resp_size} and was: {rec_size}'
            log(txt=msg, config_file=strategy_params, receivers=tg_users)

    return rec_pos


def verify_position_records(exchange_obj, rec_pos, strategy_params, tg_users):
    """

    :param exchange_obj: ccxt exchange object
    :param rec_pos: currently recorded positions
    :param strategy_params: strategy config
    :param tg_users: str or list of str - ids of TG users
    :return: dict with updated recorded positions
    """

    open_poses = get_all_positions(exchange_obj)  # return list of dicts{'str': 'str'} with poses

    new_open = organise_open_positions(exchange_obj, open_poses)

    rec_pos1 = check_for_unrecorded_symbols(exchange_obj=exchange_obj, open_poses=new_open, rec_pos=rec_pos,
                                            strategy_params=strategy_params, tg_users=tg_users)

    rec_pos2 = check_for_undeleted_symbols(exchange_obj=exchange_obj, open_poses=new_open, rec_pos=rec_pos1,
                                           strategy_params=strategy_params, tg_users=tg_users)

    rec_pos3 = check_for_pos_size_changes(open_poses=new_open, rec_pos=rec_pos2,
                                          strategy_params=strategy_params, tg_users=tg_users)

    return rec_pos3


def check_limit_tps(exch_future, exch_spot, recorded_positions, strategy_params, tg_users):
    """
    check if limit take profit was executed
    note: limit TP info is returned as requested TP id + Limit TPs created after the one requested
    :param exch_future: ccxt exchange object (futures exchange)
    :param exch_spot: ccxt exchange object (spot exchange) unused for now
    :param recorded_positions: currently recorded positions
    :param strategy_params: strategy config
    :param tg_users: str or list of str - ids of TG users
    :return: updated position records
    """

    get_futures_order = retry(exch_future.fapiPrivate_get_allorders)
    all_open_poses = get_all_positions(exch_future)

    number_of_tps = strategy_params['number_of_take_profits']

    for asset in recorded_positions['trading_data']:
        pos_info = recorded_positions['trading_data'][asset]

        for n_tp in range(1, number_of_tps+1):
            if not pos_info[f'tp{n_tp}_executed']:  # if there is no record that the TP was hit
                if pos_info[f'tp{n_tp}_id']:  # if the limit tp was created
                    # check if it was filled
                    print(f'getting n_tp-{n_tp} order info for {asset}')
                    order_info = get_futures_order(params={'symbol': asset.replace('/', ''),
                                                           'orderId': pos_info[f'tp{n_tp}_id']})

                    # find required order
                    required_ord = [i for i in order_info if str(i['orderId']) == pos_info[f'tp{n_tp}_id']]

                    if len(required_ord) > 0:
                        # if limit order was fully executed
                        print(f'checking if order was fully or partially filled')
                        print(required_ord[-1])
                        if str(required_ord[-1]['orderId']) == pos_info[f'tp{n_tp}_id'] and (
                                required_ord[-1]['status'] == 'FILLED' or float(
                                required_ord[-1]['executedQty']) == float(required_ord[-1]['origQty'])):

                            required_pos_l = [i for i in all_open_poses if str(i['symbol']) == asset]

                            if len(required_pos_l) > 0:
                                required_pos = required_pos_l[-1]
                                print(required_pos)
                            else:
                                continue

                            executed_size = float(required_ord[-1]['executedQty'])
                            price = float(required_ord[-1]['avgPrice'])
                            entry_price = float(pos_info['entry_price'])

                            pos_info['position_size'] = float(required_pos['positionAmt'])

                            if required_ord[-1]['side'] == "SELL":
                                side = 'LONG was reduced by'
                                pnl = round((price - entry_price) * executed_size, 2)
                            elif required_ord[-1]['side'] == "BUY":
                                side = 'SHORT was reduced by'
                                pnl = round((entry_price - price) * abs(executed_size), 2)

                            pos_info[f'tp{n_tp}_executed'] = True

                            remaining = pos_info['position_size']

                            msg = f'%23TP{n_tp}{asset} fully filled \n{side} {executed_size} @ {price}$\n' \
                                  f'remaining size: {remaining} | entry @ {entry_price}$ \nRealized PNL: {pnl}$'
                            log(txt=msg, config_file=strategy_params, receivers=tg_users)

                        # if limit order was partially executed
                        elif str(required_ord[-1]['orderId']) == pos_info[f'tp{n_tp}_id'] \
                                and 0 < float(required_ord[-1]['executedQty']) < float(required_ord[-1]['origQty']):

                            required_pos_l = [i for i in all_open_poses if str(i['symbol']) == asset]

                            if len(required_pos_l) > 0:
                                required_pos = required_pos_l[-1]
                            else:
                                continue

                            executed_size = float(required_ord[-1]['executedQty'])
                            price = float(required_ord[-1]['avgPrice'])
                            entry_price = float(pos_info['entry_price'])

                            pos_info['position_size'] = float(required_pos['positionAmt'])

                            if required_ord[-1]['side'] == "SELL":
                                side = 'LONG was reduced by'
                                pnl = round((price - entry_price) * executed_size, 2)
                            elif required_ord[-1]['side'] == "BUY":
                                side = 'SHORT was reduced by'
                                pnl = round((entry_price - price) * abs(executed_size), 2)

                            remaining = pos_info['position_size']

                            msg = f'%23TP{n_tp}{asset} partially filled \n{side} {executed_size} @ {price}$\n' \
                                  f'remaining size: {remaining} | entry @ {entry_price}$ \nRealized PNL: {pnl}$'
                            log(txt=msg, config_file=strategy_params, receivers=tg_users)

    return recorded_positions

def position_update(exchange_class, strategy_params, tg_users):
    pos = exchange_class.fapiPrivateV2_get_positionrisk()
    open_pos = [a['symbol'] for a in pos if a['notional'] != '0']
    total_positions = len(open_pos)
    shorts = [a['symbol'] for a in pos if int(float(a['notional'])) < 0.0]
    total_short = len(shorts)
    longs = [a['symbol'] for a in pos if int(float(a['notional'])) > 0.0]
    total_long = len(longs)
    # total possible positions
    total_markets = len(strategy_params['included_symbols'])
    pct_open = int(round((total_positions/total_markets)*100,0))
    if not shorts:
        shorts = None
    if not longs:
        longs = None

    # diff between NAV and balance
    bal = exchange_class.fetch_balance()
    total_bal = bal['info']['totalWalletBalance']
    total_nav = bal['info']['totalMarginBalance']
    bal_diff = round(((float(total_nav)-float(total_bal))/float(total_bal))*100, 2)

    # Count TPs
    with open("gda_v2_pos.json", "r") as read_file:
        data = json.load(read_file)

    # tp1 = [key for key, val in data["trading_data"].items() if val["tp1_executed"] == True]
    # tp1_num = len(tp1)
    #
    # tp2 = [key for key, val in data["trading_data"].items() if val["tp2_executed"] == True]
    # tp2_num = len(tp2)

    msg =   f'                          MARKETS \n\n' \
            f'{total_positions} ({pct_open}%) markets currently open out of {total_markets} \n\n' \
            f'Total Short positions:  {total_short}, currently: \n\n' \
            f'{shorts} \n\n' \
            f'Total Long positions:  {total_long}, currently: \n\n' \
            f'{longs} \n\n\n' \
            f'                          NAV \n\n' \
            f'Balance:  {round(float(total_bal),2)} \n NAV: {round(float(total_nav),2)} \n\n' \
            f'Difference between NAV and Balance is:  {bal_diff}% \n\n\n' \
            # f'                          TAKE PROFITS \n\n' \
            # f'Total TP1s:   {tp1_num}, currently: \n\n' \
            # f'{tp1} \n\n' \
            # f'Total TP2s:   {tp2_num}, currently: \n\n' \
            # f'{tp2}'
    log(txt=msg, config_file=strategy_params, receivers=tg_users)
    return open_pos
