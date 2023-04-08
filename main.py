import os
from logging import getLogger, config

from solana.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from anchorpy import Provider, Wallet
from driftpy.clearing_house import ClearingHouse
from driftpy.accounts import *
from driftpy.math.positions import is_available
from driftpy.constants.config import configs
from driftpy.constants.banks import mainnet_banks
from driftpy.constants.numeric_constants import *
from driftpy.clearing_house_user import ClearingHouseUser
import requests

from constants import *
from func import *


def send_message(title: str, text: str, color: str = 'good') -> None:
    swu = os.getenv('SLACK_WEBHOOK_URL')
    if swu:
        requests.post(swu, data=json.dumps({
            "attachments": [
                {
                    "title": title,
                    "text": text,
                    "color": color,
                }
            ]
        }))


async def main(subaccount, env: str = 'devnet'):
    """
    Parameters
    ----------
    env : str
        environment name such as devnet, mainnet.
    """

    # read key
    authority = PublicKey(os.getenv('PUBKEY'))
    secret_key = os.getenv('PRVKEY').encode()
    keypair = Keypair.from_secret_key(secret_key)

    config: driftpy.constants.config.Config = configs[env]
    provider = Provider(
            connection=AsyncClient(config.default_http),
            wallet=Wallet(keypair))
    ch: ClearingHouse = ClearingHouse.from_config(config, provider)
    chu: ClearingHouseUser = ClearingHouseUser(
            clearing_house=ch,
            authority=authority,
            subaccount_id=subaccount,
            use_cache=True)
    await chu.set_cache()

    while True:

        #
        # Get market information
        #

        # get perp market info
        perp_market = await chu.get_perp_market(MARKET_SOLPERP)

        # get oracle data

        o_data = perp_market.amm.historical_oracle_data
        logger.info(f"Oracle data: market=SOL-PERP, price={(o_data.last_oracle_price / PRICE_PRECISION):.4f}, price={(o_data.last_oracle_price_twap/ PRICE_PRECISION):.4f}")

        # calculate next funding rate prediction
        fr = calc_funding_rate(perp_market.amm.last_mark_price_twap, o_data.last_oracle_price_twap)
        fr_apr = fr*24*365
        fr_payer = 'LONGER' if fr > 0 else 'SHORTER'
        logger.info(f'Next funding rate: {fr:.4%}(APR {fr_apr:.3%}) *{fr_payer} will pay')

        # borrow rate
        borrow_rate = await get_borrow_rate(chu, MARKET_SOL_SPOT)
        logger.info(f"SOL borrow rate: {borrow_rate:.4%}.")

        #
        # Get user statement
        #

        # get user info
        user = await chu.get_user()

        # get perp positions
        sol_positions = [p for p in user.perp_positions if p.market_index == MARKET_SOLPERP and not is_available(p)]
        for p in sol_positions:
            logger.debug(f'Position: {p}')

        long_position = [p for p in sol_positions if p.base_asset_amount > 0]
        short_position = [p for p in sol_positions if p.base_asset_amount < 0]

        # check current FR
        #

        text = f"""SOL-PERP
Oracle Price: ${(o_data.last_oracle_price / PRICE_PRECISION):.4f}
NR: {fr:.4%}(APR {fr_apr:.3%})
BR: {borrow_rate:.4%}
LONG: {long_position[0].base_asset_amount / BASE_PRECISION if long_position else 0} SOL
SHORT: {short_position[0].base_asset_amount / BASE_PRECISION if short_position else 0} SOL
"""
        send_message(title='Current State', text=text)

        if fr > 0: # LONG pays FR to SHORT

            if long_position:

                # close LONG position to avoid paying funding rate.
                result = await close_perp_positions(long_position[0])

            if short_position:

                # get unrealized pnl
                pnl = await chu.get_unrealized_pnl(with_funding=True, market_index=MARKET_SOLPERP)
                logger.info(f'Unrealized PnL: ${pnl/QUOTE_PRECISION:.4f}')

                if (pnl/QUOTE_PRECISION) > 2.0:
                    await ch.settle_pnl(authority, market_index=MARKET_SOLPERP)
                    logger.info(f'Settled unrealized PnL including Funding Rate. ${pnl/QUOTE_PRECISION:.4f}')

            else:

                # prepare SOL and create SHORT position
                await swap_usdc_to_sol(chu, ch)
                await create_short_position(chu, ch)

                # TODO Borrow SOL and create additional SHORT position

        else: # SHORT pays FR to LONG

            if short_position:

                # close SHORT position to avoid paying funding rate.
                await close_perp_positions(short_position[0])

            # TODO If SHORT pays funding rate to LON
            if fr > borrow_rate:
                # borrow SOL
                # deposit SOL
                # sell Spot SOL
                # create LONG position of SOL-PERP
                pass

            else:

                # If borrow rate is higher than funding rate, you shouldn't have any positions.
                if long_position:
                    close_perp_position(long_position[0])

        # sleep 10 min
        time.sleep(60 * 10)


if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--subacc', type=int, required=False, default=0)
    args = parser.parse_args()

    config.fileConfig('logging.conf', disable_existing_loggers=False)
    logger = getLogger('drift')

    import asyncio
    asyncio.run(main(args.subacc, env='mainnet'))
