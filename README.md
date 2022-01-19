# Portfolio trading bot for Binance Futures

Project was built with Python 3.8

#### To start the bot:
1. install requirements with `pip3 install -r requirements.txt`

1. in the `params.json` file fill in the config with necessary API keys and settings like the crossover lengths, TP info, names of the telegram users who should receive notifications, etc.

1. in the `config.py` choose the config from the `params.json` that you've created to apply these settings to the bot

1. start the bot from the `main.py` file, e.g. `python3 main.py`

#### Relevant Files
* `main.py` combines all bot logic together and has one run function that is scheduled to run every 1st minute of every hour.

* `trading_signals.py` is the file where trading signals are generated. For example, crossover signals based on 2 EMAs are generated with add_crossovers_to() function.

* `collect_preprocess_data.py` contains the code to download, organise, clean and save pricing data. Also, the code to create technical indicators.

* `tg_messenger.py` has the code related to the telegram bot notifications and Tg IDs and names of the users, and the Tg bot token to send notifications to the right chat.

* `make_trades.py` is the file where all trading functions are stored. Functions to make trades based on the signals generated by the functions from the trading_signals.py, functions to update and execute ATR stop losses, creation of limit take profits, etc.

* `helpers.py` stores code related to logging, func retries and all of the supporting functions, such as to get wallet balance, open positions and functions to verify position records and confirm that records are aligned with open positions on Binance.

* Files portfolio_cross_pos.json, markets_and_rules.json and *_log.txt are auto generated files and their names should be specified in the relevant fields of the params.json file.

* `_log` file stores all actions of the bot and errors;

* `markets and rules` is downloaded at the beginning of every cycle and is used to identify price and size precision of the traded markets.

* `_pos.json` file stores current open trades and info such as current stop price, wallet balance at entry, TP info (price targets, size reduction values and IDs (orderId) of each TP). The record is added when the position is opened and deleted when the trade is fully closed.


#### Manual Trading
In the settings for the bot, you need to specify the markets that the bot should trade in a list. You can place trades manually and in case your trade is placed in one of the specified markets, then the bot will treat it as a newly opened trade and will create all the necessary records for that asset and no other manual intervention is required. 

To see current position size values to manually open a trade execute the `get_pct_pos_sizes.py`.

If the trade was placed in the market that was not specified in the list of markets that should be traded, then the bot will ignore that trade completely.

The bot records could be updated manually, for example, if you don't want TPs for some asset to be created or if you want to update the max original position size in order
to change the size of the take profits in the future, etc.

To update the records, you need to update the `portfolio_cross_pos.json` file on the server. For example, to avoid the creation of TPs for some asset, you will need to add `true` next to the `tp1_executed` or `tp2_executed` field. So that this field looks like `"tp1_executed": true`. You can also update fields like `max_size` which is used to deduce the size of the Take Profits, or the `entry_price` which is used to deduce at which price level to place the take profit. Finally, if you created TPs yourself, you can add information about that TP to the records, specifying `id`, `price` and `size` of the TP. For example, `"tp2_price": 0.033944, "tp2_size": 11931.0, "tp2_id": "9657413875"`.

The `portfolio_cross_pos.json` file could be modified via command-line editor or it could be downloaded using `FileZilla`, modified and uploaded back to the 
server with replacement.

#### Notes:
The bot has two versions at the moment which should be merged. Normal Binance Futures version of the bot is in the main branch on GitHub and the version with the Hedging Mode on is in the hedging mode branch.

When making trades the bot attaches its own IDs to orders (newClientOrderId). They will have the following format: portfolioStrat_ + action_tag

For example:

portfolioStrat_OpenLong or portfolioStrat_AtrCloseShort etc.

Current action_tags are:

* Open/Close Long/Short

* AddTo Long/Short

* Close Long/Short

* AtrClose Long/Short

* L Short/LongTpx e.g. LShortTp2 - limit short tp 2

* M Short/LongTpx e.g. MLongTp1 - market long tp 1
