import json
import os


path = r'%s' % os.getcwd().replace('\\', '/')

# import this config to every file that needs it
with open(path + '/params.json', 'r') as f:
    params_portfolio_ema_cross = json.load(f)["bin_portfolio_ema_cross_v2"]  # update config name here !
