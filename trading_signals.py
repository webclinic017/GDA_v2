from numpy import where
from config import params_portfolio_ema_cross as strat_params
from helpers import log


def add_crossovers_to(dataset, tg_user):
    """
    add EMA crossovers to the pd.DFs in the dict with datasets - requires pd.DFs that were given to have
    columns named as 'ema_s_1', 'ema_l_1'.
    :param dataset: dict - {'data name': pd.DF}
    :return: dict - same sa given to the func but with crossovers
    """

    markets = list(dataset.keys())

    for smbl in markets:
        dataset[smbl]['ema_1_cross'] = (
                    where((dataset[smbl]['ema_s_1'] <= dataset[smbl]['ema_l_1']) &
                          (dataset[smbl]['ema_s_1'].shift(1) >= dataset[smbl]['ema_l_1'].shift(1)), -1, 0)
                    | where((dataset[smbl]['ema_s_1'] >= dataset[smbl]['ema_l_1']) &
                            (dataset[smbl]['ema_s_1'].shift(1) <= dataset[smbl]['ema_l_1'].shift(1)), 1, 0)
        )
        dataset[smbl]['ema_2_cross'] = (
                where((dataset[smbl]['ema_s_2'] <= dataset[smbl]['ema_l_2']) &
                      (dataset[smbl]['ema_s_2'].shift(1) >= dataset[smbl]['ema_l_2'].shift(1)), -1, 0)
                | where((dataset[smbl]['ema_s_2'] >= dataset[smbl]['ema_l_2']) &
                        (dataset[smbl]['ema_s_2'].shift(1) <= dataset[smbl]['ema_l_2'].shift(1)), 1, 0)
        )
        dataset[smbl]['long_emas_trend'] = (
                where((dataset[smbl]['ema_s_2'] < dataset[smbl]['ema_l_2']), -1, 0)
                | where((dataset[smbl]['ema_s_2'] >= dataset[smbl]['ema_l_2']), 1, 0)
        )

    a = [1, -1]

    for smbl in markets:
        if dataset[smbl].iloc[-1]['ema_1_cross'] in a or dataset[smbl].iloc[-2]['ema_1_cross'] in a:

            if dataset[smbl].iloc[-1]['ema_1_cross'] == 1 or dataset[smbl].iloc[-2]['ema_1_cross'] == 1:
                direction = 'LONG'
            elif dataset[smbl].iloc[-1]['ema_1_cross'] == -1 or dataset[smbl].iloc[-2]['ema_1_cross'] == -1:
                direction = 'SHORT'

            msg = f'%23{smbl} {direction} shorter emas crossover signal was generated in the last 2 trading candles'
            log(txt=msg, config_file=strat_params, receivers=tg_user)

        if dataset[smbl].iloc[-1]['ema_2_cross'] in a or dataset[smbl].iloc[-2]['ema_2_cross'] in a:

            if dataset[smbl].iloc[-1]['ema_2_cross'] == 1 or dataset[smbl].iloc[-2]['ema_2_cross'] == 1:
                direction = 'LONG'
            elif dataset[smbl].iloc[-1]['ema_2_cross'] == -1 or dataset[smbl].iloc[-2]['ema_2_cross'] == -1:
                direction = 'SHORT'

            msg = f'%23{smbl} {direction} longer emas crossover signal was generated in the last 2 trading candles'
            log(txt=msg, config_file=strat_params, receivers=tg_user)

    return dataset
