import os
import pandas as pd
import ccxt
from config import params_portfolio_ema_cross as strat_params
import collect_preprocess_data
import helpers
from tg_messenger import notify_missing_markets

project_path = r'%s' % os.getcwd().replace('\\', '/')
data_folder_path = 'data/'

files = os.listdir(project_path + data_folder_path)

pd.options.display.max_columns = 100
pd.options.display.max_rows = 100
pd.set_option('display.expand_frame_repr', False)

exchange_id = strat_params['exchange']
exchange_class = getattr(ccxt, exchange_id)

exchange_data = exchange_class({
    'apiKey': strat_params['apikey'],
    'secret': strat_params['secret'],
    'timeout': 30000,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
})

exchange_trading = exchange_class({
    'apiKey': strat_params['apikey'],
    'secret': strat_params['secret'],
    'timeout': 30000,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # 'spot'
    }
})

telegram_users = strat_params['dev_user']  # 'all_users'
dev_user = strat_params['dev_user']  # 'all_users'


def run():
    try:
        markets_and_rules, \
        list_of_symbols = collect_preprocess_data.load_exchange_markets(exchange_object=exchange_trading,
                                                                        excluded_markets=strat_params['excluded_symbols'],
                                                                        included_markets=strat_params['included_symbols'],
                                                                        use_included_markets=True)

        missing_markets = collect_preprocess_data.check_if_all_markets_present(included_markets=strat_params['included_symbols'],
                                                                               markets_and_rules=markets_and_rules)

        notify_missing_markets(missing_markets, users=telegram_users)

        trading_data = collect_preprocess_data.read_saved_data(list_of_symbols=list_of_symbols,
                                                               folder_files=files,
                                                               path=project_path,
                                                               data_path=data_folder_path,
                                                               data_tf=strat_params['trading_tf'])

        daily_indicators_data = collect_preprocess_data.load_price_data(exchange_obj=exchange_data,
                                                                        list_of_markets=list_of_symbols,
                                                                        exchange_tf=strat_params['indicator_tf'],
                                                                        candles_back=strat_params['candles_limit'],
                                                                        to_csv=True)

        new_trading_data = collect_preprocess_data.load_price_data(exchange_obj=exchange_data,
                                                                   list_of_markets=list_of_symbols,
                                                                   exchange_tf=strat_params['trading_tf'],
                                                                   candles_back=strat_params['candles_limit'],
                                                                   to_csv=False)

        # attach new_trading_data to trading data
        trading_data = collect_preprocess_data.attach_new_and_old_dfs(trading_data, new_trading_data)

        trading_data = collect_preprocess_data.add_emas_to(datasets=trading_data,
                                                           emas=strat_params['emas'])

        daily_indicators_data = collect_preprocess_data.add_atrs_to(daily_indicators_data,
                                                                    atrs=strat_params['atrs'])

        daily_indicators_data, df = collect_preprocess_data.add_pct_pos_size_to(daily_indicators_data,
                                                                                atr_ls=strat_params['atrs'])

        current_time = helpers.datetime.now().isoformat(timespec='minutes')

        balance = helpers.get_f_balance(exchange_trading)
        mult =strat_params['balance_mult']

        file = open('current_pos_sizes.csv', 'w+')
        print(f"asset,balance_$,mult,pct_size,price_$,units,value_$")
        file.write(f"asset,balance_$,mult,pct_size,price_$,units,value_$\n")
        for asset in daily_indicators_data.keys():

            pct_size = daily_indicators_data[asset]['p_size'].iloc[-2]
            price = trading_data[asset]['close'].iloc[-1]
            pos_precision = markets_and_rules[asset]['precision']['amount']

            units = round((balance * mult * pct_size) / price, pos_precision)

            value = round(price * units, 4)

            print(f"{asset},{balance},{mult},{pct_size},{price},{units},{value}")
            file.write(f"{asset},{balance},{mult},{pct_size},{price},{units},{value}\n")

        print(current_time)
        file.write(f'\nLast Update Time:,{current_time} \n')
        file.close()

    except Exception as e:
        helpers.log(f'error in *run* func\n{e}', config_file=strat_params, receivers=dev_user)


if __name__ == "__main__":
    run()
