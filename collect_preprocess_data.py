from ccxt.base.errors import BadSymbol
import pandas as pd
from helpers import retry, log
from config import params_portfolio_ema_cross as strat_params
from time import sleep
from datetime import datetime, timedelta


@retry
def load_exchange_markets(exchange_object, excluded_markets, included_markets, use_included_markets=True):
    """

    :param exchange_object: obj - ccxt exchange object
    :param excluded_markets: list - use all of the futures markets but the ones in this list
    :param included_markets: list - use only markets within this list
    :param use_included_markets: bool whether to included_markets (True) or excluded (False)
    :return: dict and list - dict{'asset name': {rules}} with downloaded markets and rules and list of those symbols
    """
    markets_and_rules = exchange_object.load_markets()

    if use_included_markets:
        for market in list(markets_and_rules.keys()):
            if market not in included_markets:
                del markets_and_rules[market]
    else:
        for market in excluded_markets:
            if market in markets_and_rules:
                del markets_and_rules[market]

    list_of_symbols = list(markets_and_rules.keys())

    return markets_and_rules, list_of_symbols


def check_if_all_markets_present(included_markets, markets_and_rules):
    """

    :param included_markets: list - list of markets that should be present in the markets and rules dict
    :param markets_and_rules: dict - downloaded assets from the exchange
    :return: list - missing markets
    """
    missing_markets = []

    for market in included_markets:
        if market not in list(markets_and_rules.keys()):
            missing_markets.append(market)

    return missing_markets


@retry
def load_price_data(exchange_obj, list_of_markets, exchange_tf, candles_back, to_csv=False):
    """

    :param exchange_obj: obj - ccxt exchange object
    :param list_of_markets: list - list of markets for which we should download the price data
    :param exchange_tf: str - time frame of the candles to download, e.g. 1h, 1d - available TF differs per exchange
    :param candles_back: int - how many candles we want to download in one go - number differs per exchange
    :param to_csv: bool - do we want to save this data to the CSV file or not
    :return: dict - {'data name': pd.DataFrame}
    """
    min_candles = strat_params['minimum_days_traded']
    datas = {}
    data_cols = ['time', 'open', 'high', 'low', 'close', 'volume']

    # TODO REFORMAT THIS FUNC
    # FIX download of prices, make since properly updatable, make sure any TF could be passed and not 1h / 1d

    time_now = datetime.utcnow()
    start_datetime = time_now - timedelta(124)
    start = round(start_datetime.timestamp() * 1000)

    for market in list_of_markets:

        try:
            l_of_l = exchange_obj.fetch_ohlcv(symbol=market, timeframe=exchange_tf, limit=candles_back)

        except BadSymbol:
            msg = f'BadSymbol error: %23{market} is not available on {exchange_obj.id}'
            log(txt=msg, config_file=strat_params)
            pass

        df = pd.DataFrame(l_of_l, columns=data_cols)
        df.set_index('time', inplace=True)
        df.index = pd.to_datetime(df.index, unit='ms')

        # check for dupliactes and drop them
        df = df[~df.index.duplicated(keep='first')]
        print(f'dropped duplicated rows, df shape: {df.shape}')

        if exchange_tf[-1] in ['h', 'd', 'w']:
            asfreq_tf = exchange_tf.upper()
        elif exchange_tf[-1] == 'm':
            asfreq_tf = exchange_tf + 'in'
        else:
            asfreq_tf = exchange_tf

        df = df.asfreq(freq=f'{asfreq_tf}')
        missing_periods = len(df[df.isnull().any(axis=1)])
        print(f'{market} had {missing_periods} missing periods out of {len(df)} observations on {exchange_tf} timeframe')

        df.interpolate(method='linear', limit_direction='forward', axis=0, inplace=True)

        if len(df) > min_candles:
            # we add df to our datasets only if there are more than 90 days of data
            if exchange_tf == '1h':
                df.drop(df.tail(1).index, inplace=True)  # drop the last row on hourly TF
            datas[market] = df

        else:
            msg = f'%23{market} has less than {min_candles} {exchange_tf} candles of data - {len(df)}'
            log(txt=msg, config_file=strat_params)

        sleep(0.3)

        if to_csv:
            df.to_csv(path_or_buf=f"../data/{exchange_obj.id}_{market.replace('/', '')}_{exchange_tf}.csv")

    return datas


def add_emas_to(datasets, emas_1={'s': 5, 'l': 15}, emas_2={'s': 10, 'l': 20}):
    """

    :param datasets: dict - {'data name': pd.DataFrame with data}
    :param ema_1: dict - short EMAs that should be added
    :param ema_2: dict - long EMAs that should be added
    :return: dict - same dict that was given to the func but with EMAs added to pd.DFs
    """

    markets = list(datasets.keys())

    for smbl in markets:
        datasets[smbl]['ema_s_1'] = datasets[smbl]['close'].ewm(span=emas_1['s'],
                                                              adjust=False,
                                                              min_periods=emas_1['s']
                                                              ).mean()

        datasets[smbl]['ema_l_1'] = datasets[smbl]['close'].ewm(span=emas_1['l'],
                                                              adjust=False,
                                                              min_periods=emas_1['l']
                                                              ).mean()
        datasets[smbl]['ema_s_2'] = datasets[smbl]['close'].ewm(span=emas_2['s'],
                                                                adjust=False,
                                                                min_periods=emas_2['s']
                                                                ).mean()

        datasets[smbl]['ema_l_2'] = datasets[smbl]['close'].ewm(span=emas_2['l'],
                                                                adjust=False,
                                                                min_periods=emas_2['l']
                                                                ).mean()


    return datasets


def add_atrs_to(datasets, atrs=[30]):
    """

    :param datasets: dict - {'data name': pd.DataFrame with data}
    :param atrs: list - atrs that should be added
    :return: dict - same dict that was given to the func but with ATRs added to pd.DFs
    """
    markets = list(datasets.keys())

    for smbl in markets:
        df = datasets[smbl].copy()
        df['atr1'] = abs(df['high'] - df['low'])
        df['atr2'] = abs(df['high'] - df['close'].shift())
        df['atr3'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['atr1', 'atr2', 'atr3']].max(axis=1)

        for atr_l in atrs:
            datasets[smbl][f'atr_{atr_l}'] = df['tr'].ewm(span=atr_l, adjust=False, min_periods=atr_l).mean()
            datasets[smbl][f'atr_pct_{atr_l}'] = datasets[smbl][f'atr_{atr_l}'] / datasets[smbl][f'close']

    return datasets


def add_pct_pos_size_to(datasets, atr_ls=[30], debug_to_csv=True):
    """

    :param datasets: dict - {'data name': pd.DataFrame with data}
    :param atr_ls: list - atr lengths that the func should search for to use in pos size calculations
    :return: dict and pd.DF - same dict given to the func but with pos sizes and pd.DF with calculations for debug
    """
    # todo assumption is that we will always trade BTC/USDT as part of all assets, make this updatable
    markets = list(datasets.keys())

    df = pd.DataFrame()
    df['atr_pct_sum'] = datasets['BTC/USDT'][f'atr_pct_{atr_ls[0]}'].copy()

    for smbl in markets:
        if smbl == 'BTC/USDT':
            continue
        df['atr_pct_sum'] = df['atr_pct_sum'].add(datasets[smbl][f'atr_pct_{atr_ls[0]}'], fill_value=0)

    for smbl in markets:
        datasets[smbl]['atr_from_total'] = datasets[smbl][f'atr_pct_{atr_ls[0]}'] / df['atr_pct_sum']
        datasets[smbl]['atr_from_one'] = 1 / datasets[smbl]['atr_from_total']

    df['sum_atr_from_one'] = datasets['BTC/USDT']['atr_from_one'].copy()
    for smbl in markets:
        if smbl == 'BTC/USDT':
            continue
        df['sum_atr_from_one'] = df['sum_atr_from_one'].add(datasets[smbl][f'atr_from_one'], fill_value=0)

    for smbl in markets:
        datasets[smbl]['p_size'] = datasets[smbl]['atr_from_one'] / df['sum_atr_from_one']

    df['pos_size_sum'] = datasets['BTC/USDT']['p_size'].copy()
    for smbl in markets:
        if smbl == 'BTC/USDT':
            continue
        df['pos_size_sum'] = df['pos_size_sum'].add(datasets[smbl][f'p_size'], fill_value=0)

    for smbl in markets:
        datasets[smbl].drop(['atr_from_total', 'atr_from_one'], axis=1, inplace=True)

    if debug_to_csv:
        df.to_csv(path_or_buf=f"data/DEBUG_atr_pos_size_calc.csv")

    return datasets, df


def read_saved_data(list_of_symbols, folder_files, path, data_path, data_tf):
    """

    :param list_of_symbols: list of str - e.g. ['BTC/USDT', 'ETH/USDT'] that should be found in dir
    :param folder_files: list of files within the folder e.g. ['binance_AAVEUSDT_1d.csv']
    :param path: full path to project, e.g. 'C:/Users/master/PycharmProjects/GDA_v2'
    :param data_path: path to folder with data e.g. `/data/'
    :param data_tf: which time frame to search for in files e.g. '1d' or '1h'
    :return: dict e.g. {'BTC/USDT': btc_prices_df}
    """

    trading_data = {}
    ttf = data_tf

    relevant_files = [f for f in folder_files if any(data.replace('/', '') + f'_{ttf}' in f for data in list_of_symbols)]

    for f in relevant_files:

        try:
            df = pd.read_csv(path + data_path + f, index_col='time')
            df.index = pd.to_datetime(df.index)

            if ttf[-1] in ['h', 'd', 'w']:
                asfreq_tf = ttf.upper()
            elif ttf[-1] == 'm':
                asfreq_tf = ttf + 'in'
            else:
                asfreq_tf = ttf

            df = df.asfreq(freq=f'{asfreq_tf}')

            missing_vals = len(df[df.isnull().any(axis=1)])
            print(f'{f} had {missing_vals} missing periods out of {len(df)} observations on {ttf} timeframe')

            trading_data[f.replace(f'_{ttf}.csv', '').replace('binance_', '').replace('USDT', '/USDT')] = df

        except ValueError as e:
            print(f'Value Error when reading: {f} \n{e}')
            pass

    return trading_data


def attach_new_and_old_dfs(old_data, new_data):
    """
    concatenate old and new data
    :param old_data: dict with saved data {'BTC/USDT': btc_prices_df}
    :param new_data: dict with recently downloaded data
    :return: dict with combined datas
    """

    final_datas = {}

    for market in new_data:

        try:
            df1 = old_data[market].copy()
        except KeyError as e:
            print(f'KeyError - {e}\n{market} is not in recorded data')
            df1 = pd.DataFrame()

        try:
            df2 = new_data[market].copy()
            final_df = pd.concat([df1, df2])
            final_df = final_df[~final_df.index.duplicated(keep='first')]

            final_datas[market] = final_df
        except KeyError as e:
            print(f'KeyError - {e}\n{market} is not in recently downloaded data')

    return final_datas


def write_dfs_to_folder(datas, datas_tf, datas_path, datas_exchange):
    """
    write dataframes to csv files in the specified folder
    :param datas: dictionary of assets as keys and price dfs as values
    :param datas_tf: trading tf to put in the name
    :param datas_path: full path to the folder with data
    :param datas_exchange: exchange from where the data was taken
    :return:
    """

    for market in datas:
        datas[market].to_csv(path_or_buf=f"{datas_path}/{datas_exchange}_{market.replace('/', '')}_{datas_tf}.csv")

