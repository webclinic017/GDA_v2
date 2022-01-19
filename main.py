import schedule
import traceback
import ccxt
import os
import pandas as pd
from config import params_portfolio_ema_cross as strat_params
import collect_preprocess_data
import make_trades
from trading_signals import add_crossovers_to
from tg_messenger import notify_missing_markets
import helpers

# path to data folder
project_path = r'%s' % os.getcwd().replace('\\', '/')
data_folder_path = '/data/'

files = os.listdir(project_path + data_folder_path)

# not used in strategy, useful if want to create tables e.g calculate pos sizes
pd.options.display.max_columns = 100
pd.options.display.max_rows = 100
pd.set_option('display.expand_frame_repr', False)

exchange_id = strat_params['exchange']
markets = strat_params['included_symbols']

exchange_class = getattr(ccxt, exchange_id)

#binance spot
exchange_data = exchange_class({
    'apiKey': strat_params['apikey'],
    'secret': strat_params['secret'],
    'timeout': 30000,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
})

#binance futures - using Alpha 4 as test
exchange_trading = exchange_class({
    'apiKey': strat_params['apikey'],
    'secret': strat_params['secret'],
    'timeout': 30000,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # 'spot'
    }
})

telegram_users = strat_params['all_users']  # 'all_users'
dev_user = strat_params['all_users']  # 'all_users'

# todo update log function calls everywhere to make sure that users are passed into the receivers param


def run():
    # main loop - combines all of the components of the trading bot
    # TODO UPDATE DEBUG CONDITIONS IN TRADING FUNCS
    # TODO add notification that the bot started and which params it uses
    try:

        current_positions = helpers.read_json_file(strat_params['positions_file_name'])

        # current_positions = helpers.check_limit_tps(exch_future=exchange_trading,
        #                                             exch_spot=exchange_data,
        #                                             recorded_positions=current_positions,
        #                                             strategy_params=strat_params,
        #                                             tg_users=telegram_users)

        current_positions = helpers.verify_position_records(exchange_obj=exchange_trading,
                                                            rec_pos=current_positions,
                                                            strategy_params=strat_params,
                                                            tg_users=telegram_users)

        markets_and_rules, \
        list_of_symbols = collect_preprocess_data.load_exchange_markets(exchange_object=exchange_trading,
                                                                        excluded_markets=strat_params['excluded_symbols'],
                                                                        included_markets=strat_params['included_symbols'],
                                                                        use_included_markets=True)

        missing_markets = collect_preprocess_data.check_if_all_markets_present(included_markets=strat_params['included_symbols'],
                                                                               markets_and_rules=markets_and_rules)
        # todo make sure notification about missing markets is sent 1-2 times and not every iteration
        notify_missing_markets(missing_markets, users=telegram_users)

        # update_acc_leverage(exchange_obj=exchange_trading,
        #                     leverage=strat_params['account_leverage'],
        #                     list_of_markets=list_of_symbols,
        #                     errors_to=dev_user)

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
                                                           ema_1=strat_params['emas_1'],
                                                           ema_2=strat_params['emas_2'],)

        daily_indicators_data = collect_preprocess_data.add_atrs_to(daily_indicators_data,
                                                                    atrs=strat_params['atrs'])

        daily_indicators_data, df = collect_preprocess_data.add_pct_pos_size_to(daily_indicators_data,
                                                                                atr_ls=strat_params['atrs'])

        trading_data = add_crossovers_to(trading_data, tg_user=telegram_users)

        trading_responses, current_positions = make_trades.strategy_signal_trade(exchange_obj=exchange_trading,
                                                                                 trading_datas=trading_data,
                                                                                 indicators_datas=daily_indicators_data,
                                                                                 current_poses=current_positions,
                                                                                 markets_and_rules=markets_and_rules,
                                                                                 tg_user=telegram_users)

        current_positions = make_trades.update_trailing_atr_stops(indicator_datas=daily_indicators_data,
                                                                  current_poses=current_positions,
                                                                  atr_l=strat_params['atrs'],
                                                                  n_of_atrs=strat_params['n_atr_stops'],
                                                                  tg_user=telegram_users)

        current_positions = make_trades.execute_atr_trailing_stops(exchange_obj=exchange_trading,
                                                                   indicator_datas=daily_indicators_data,
                                                                   current_poses=current_positions,
                                                                   atr_l=strat_params['atrs'],
                                                                   n_of_atrs=strat_params['n_atr_stops'],
                                                                   tg_user=telegram_users)

        # current_positions = execute_market_take_profits(exchange_obj=exchange_trading,
        #                                                 trading_datas=trading_data,
        #                                                 current_poses=current_positions,
        #                                                 strat_params=strat_params,
        #                                                 markets_and_rules=markets_and_rules,
        #                                                 tg_users=telegram_users)

        # current_positions = make_trades.create_check_limit_take_profits(exchange_futures=exchange_trading,
        #                                                                 exchange_spot=exchange_data,
        #                                                                 strat_params=strat_params,
        #                                                                 current_poses=current_positions,
        #                                                                 markets_and_rules=markets_and_rules,
        #                                                                 tg_users=telegram_users)

        helpers.write_json_file(strat_params['positions_file_name'], current_positions)

        # write trading_data_with new data to a file
        collect_preprocess_data.write_dfs_to_folder(datas=trading_data,
                                                    datas_tf=strat_params['trading_tf'],
                                                    datas_path=data_folder_path.replace('/', ''),
                                                    datas_exchange=strat_params['exchange'])

        # record updated exchange trading rules for our traded assets
        helpers.write_json_file(strat_params['exchange_trading_rules'], markets_and_rules)

    except Exception as e:
        msg = traceback.format_exc()
        helpers.log(f'error in *run* func\n{e}\n{str(msg)}', config_file=strat_params, receivers=dev_user)

def run_pos():

    try:

        helpers.position_update(exchange_trading, strat_params, telegram_users)

    except Exception as e:
        msg = traceback.format_exc()
        helpers.log(f'error in *run_pos* func\n{e}\n{str(msg)}', config_file=strat_params, receivers=dev_user)

if __name__ == "__main__":
    helpers.log(txt=f'{strat_params["strategy_name"]} strategy has started',
                config_file=strat_params, receivers=telegram_users)

    # schedule.every(5).minutes.at(':30').do(run)
    schedule.every().hour.at(":01").do(run)
    schedule.every().day.at("09:00").do(run_pos)

    while True:
        try:
            schedule.run_pending()
            helpers.sleep(10)
        except (KeyboardInterrupt, SystemExit):
            helpers.log(txt=f'The bot was shut down by user', config_file=strat_params, receivers=telegram_users)
            break
        except Exception as e:
            msg = traceback.format_exc()
            helpers.log(txt=f'error in the main loop\n{e}\n{str(msg)}', config_file=strat_params, receivers=dev_user)
            pass
