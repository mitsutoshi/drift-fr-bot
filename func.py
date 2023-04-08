import time
from logging import getLogger

from driftpy.accounts import SpotBalanceType, PositionDirection, OrderType, MarketType, PerpPosition
from driftpy.constants.numeric_constants import *
from driftpy.clearing_house import ClearingHouse
from driftpy.clearing_house_user import ClearingHouseUser

from constants import *

logger = getLogger('drift')


def calc_funding_rate(mark_price_twap, oracle_price_twap) -> float:
    '''
    '''
    return 1 / 24 * (mark_price_twap - oracle_price_twap) / oracle_price_twap


async def swap_usdc_to_sol(chu, ch):
    logger.info(f"Start to swap USDC to SOL.")

    # Get USDC balance
    usdc_position = await chu.get_user_spot_position(MARKET_USDC_SPOT)
    usdc_balance = usdc_position.scaled_balance / SPOT_BALANCE_PRECISION

    # Get SOL balance
    sol_position = await chu.get_user_spot_position(MARKET_SOL_SPOT)
    sol_balance = sol_position.scaled_balance / SPOT_BALANCE_PRECISION
    logger.info(f"Balance: {usdc_balance} USDC, {sol_balance} SOL")

    # if you have USDC enough, buy SOL.
    if usdc_position.scaled_balance > 0 and SpotBalanceType.DEPOSIT():

        # get current spot market info
        spot_market = await chu.get_spot_market(MARKET_SOL_SPOT)
        solusdc_price = spot_market.historical_index_data.last_index_bid_price / PRICE_PRECISION
        logger.info(f"SOL/USDC price is ${solusdc_price:.4f}.")

        # if USDC balance is enough, buy SOL.
        if usdc_balance > solusdc_price * 0.1: # TODO should get min_size from oracle

            # adjust amount
            amount_str = str(usdc_balance / solusdc_price)
            amount = float(amount_str[:amount_str.find('.') + 2])
            order_amount = int(amount * BASE_PRECISION)

            if order_amount < 0.1:
                logger.info('USDC balance is not enough to buy SOL.')
                return

            # create new order to buy SOL/USDC
            params = ch.default_order_params(
                    OrderType.LIMIT(), MARKET_SOL_SPOT, order_amount, PositionDirection.LONG())
            params.market_type = MarketType.SPOT()
            params.post_only = True
            params.oracle_price_offset = -1

            order_ix = [await ch.get_place_spot_order_ix(params)]
            result = await ch.send_ixs(order_ix)
            logger.info(f'Create BUY {amount} SOL order.')

            # loop and check order status
            while True:

                # TODO need to change limit price
                sleep_sec = 10
                logger.info(f"Order hasn't filled yet. Wait {sleep_sec} seconds...")
                time.sleep(sleep_sec)

                await chu.set_cache()
                sp = await chu.get_user_spot_position(market_index=MARKET_SOL_SPOT)
                logger.debug(sp)

                updated_sol_balance = sp.scaled_balance / SPOT_BALANCE_PRECISION
                if updated_sol_balance > sol_balance:
                    logger.info(f'Swapping USDC to SOL finished. SOL balance has been updated. {sol_balance} => {updated_sol_balance}')
                    break


async def get_borrow_rate(chu, spot_market: int) -> float:
    '''get_borrow_rate returns current the borrow interest rate.

    If you see mechanism of the borrow interest rate, see below document.
    https://docs.drift.trade/borrow-interest-rate
    '''
    from driftpy.constants.numeric_constants import SPOT_UTILIZATION_PRECISION as P

    m = await chu.get_spot_market(spot_market)

    # calc utilisation%
    utilisation = m.borrow_balance / m.deposit_balance
    logger.debug(f"optimal_utilization:, {m.optimal_utilization}, \
deposit_balance: {m.deposit_balance / P} SOL, \
borrow_balance: {m.borrow_balance / P} SOL, \
utilisation of spot market {spot_market}: {utilisation:%}")

    # calc utilisation%
    u = m.borrow_balance / m.deposit_balance

    if utilisation > m.optimal_borrow_rate:
        surplus_utilization = u - m.optimal_utilization
        borrow_rate_slope = (m.max_borrow_rate - m.optimal_borrow_rate) * P / (P - m.optimal_utilization)
        borrow_rate = (m.optimal_borrow_rate + surplus_utilization) * borrow_rate_slope / P
    else:
        borrow_rate_slope = m.optimal_borrow_rate * P / m.optimal_utilization
        borrow_rate = u * borrow_rate_slope / P

    logger.debug(f'utilisation: {u:%}, borrow_rate_slope: {borrow_rate_slope}, borrow_rate: {borrow_rate:.2%}', )
    return borrow_rate


async def create_short_position(chu, ch):

    # Get SOL balance
    sol_spot_pos = await chu.get_user_spot_position(MARKET_SOL_SPOT)
    sol_balance = sol_spot_pos.scaled_balance / SPOT_BALANCE_PRECISION
    logger.info(f"Spot balance: {sol_balance} SOL")

    sol_perp_pos: PerpPosition = await chu.get_user_position(market_index=MARKET_SOLPERP)
    logger.info(f"SOL-PERP position: {sol_perp_pos}.")

    order_amount = sol_spot_pos.scaled_balance - abs(sol_perp_pos.base_asset_amount)
    min_order_amount = 0.1 * SPOT_BALANCE_PRECISION

    if sol_perp_pos.base_asset_amount < 0 and order_amount > min_order_amount:

        # market price of SOL($)
        perp_market = await chu.get_perp_market(MARKET_SOLPERP)
        sol_price = perp_market.amm.last_mark_price_twap / PRICE_PRECISION
        logger.info(f'SOL-PERF price: ${sol_price}')

        # create order
        params = ch.default_order_params(
                OrderType.LIMIT(), MARKET_SOLPERP, order_amount, PositionDirection.SHORT())
        params.market_type = MarketType.PERP()
        params.post_only = True
        params.oracle_price_offset = 1

        order_ix = [await ch.get_place_perp_order_ix(params)]
        logger.debug('Instructions:', order_ix)
        result = await ch.send_ixs(order_ix)
        logger.info(f"Sent transaction to create new SHORT position. [\
market=SOL-PERP, direction=SHORT, amount={order_amount}, \
price_offset={params.oracle_price_offset}, post_only={params.post_only}, signature={result}]")

        # loop and check order status
        while True:

            sleep_sec = 10
            logger.info(f"Order hasn't filled yet. Wait {sleep_sec} seconds...")
            time.sleep(sleep_sec)

            await chu.set_cache()

            # get current SOL-PERP position
            pos: PerpPosition = await chu.get_user_position(market_index=MARKET_SOLPERP)
            logger.info(f"SOL-PERP position: {sol_perp_position}.")
            if pos:
                if pos.open_orders == 0 and pos.open_asks == 0 and pos.base_asset_amount < pos.base_asset_amount:
                    print('Order has been filled.')
                    break

    else:
        logger.info('SOL balance is not enough to create position.')


async def close_perp_position(ch: ClearingHouse, chu: ClearingHouseUser, position: PerpPosition) -> None:
    '''Close perpetual positions.
    '''

    # decide close direction
    if position.base_asset_amount > 0:
        direction = PositionDirection.SHORT()
        price_offset = 1
    else:
        direction = PositionDirection.LONG()
        price_offset = -1

    # create order parameters
    ch.place_spot_order()
    params = ch.default_order_params(
            OrderType.LIMIT(),
            MARKET_SOLPERP,
            abs(position.base_asset_amount),
            direction)
    params.reduce_only = True
    params.post_only = True
    params.oracle_price_offset = price_offset

    ixs = [await ch.get_place_perp_order_ix(params)]
    logger.debug('Created instructions:', ixs)

    result = await ch.send_ixs(ixs)
    logger.info(f"Sent transaction to close position. [\
market=SOL-PERP, direction={direction}, amount={abs(position.base_asset_amount)}, \
price_offset={params.oracle_price_offset}, post_only={params.post_only}, signature={result}]")

    # loop and check order status
    while True:

        # TODO need to change limit price
        sleep_sec = 10
        logger.info(f"Order hasn't filled yet. Wait {sleep_sec} seconds...")
        time.sleep(sleep_sec)

        await chu.set_cache()
        p = await chu.get_user_position(market_index=MARKET_SOLPERP)
        logger.debug(p)

        if p.open_orders == 0 and p.base_asset_amount == 0:
            logger.info(f'Position has closed.')
            break


async def borrow(ch, key):
    print('key:', key)
    #params = ch.default_order_params(
    #        OrderType.MARKET(), 1, int(0.1 * BASE_PRECISION), PositionDirection.SHORT())
    #params.market_type = MarketType.SPOT()
    #params.reduce_only = False
    #params.oracle_price_offset = -1
    #ixs = [await ch.get_place_spot_order_ix(params)]
    #logger.debug('Created instructions:', ixs)
    #result = await ch.send_ixs(ixs)
    #logger.debug('Sent order transaction:', result)

    result = await ch.withdraw(
            amount=int(0.1 * SPOT_RATE_PRECISION),
            spot_market_index=1,
            user_token_account=key,
            reduce_only=True,
            user_id=0)
    print(result)


async def borrow_usdc(ch, usdc_amount):
    print('borrow usdc')
    pubkey = os.getenv('ATA_PUBKEY_USDC')
    print('pubkey:', pubkey)
    result = await ch.withdraw(
            amount=int(usdc_amount * SPOT_RATE_PRECISION),
            spot_market_index=0,
            user_token_account=PublicKey(pubkey),
            reduce_only=False,
            user_id=0)
    print(result)


